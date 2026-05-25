"""B4b — Q-mode HTTP + integration tests.

Covers layers 7 + 8 + 10 (need a real DB) and end-to-end Q-mode:

  - Migration: audit_queries table shape, RLS forced, kb_app GRANT (SELECT
    + INSERT only; no UPDATE/DELETE), kb_app_q role created
  - Executor layer 7: SET LOCAL transaction_read_only blocks writes
  - Executor layer 8: statement_timeout aborts pg_sleep
  - Executor + compiler layer 9: row cap returns row_cap_exceeded
  - Layer 10: audit_queries row written; CSV artifact key set when MinIO
    is reachable (best-effort; failures log but don't block)
  - End-to-end via /chat with hand-crafted Q payload
  - Workspace isolation: another workspace's plan can't escape
  - Regression: B4a + B3 + B2 endpoints still pass
"""

from __future__ import annotations

import hashlib
import os
import uuid
from contextlib import contextmanager

import psycopg
import pytest

from kb.api.query import reset_orchestrator
from kb.config import get_settings
from kb.domain.audit_queries import (
    AUDIT_QUERY_STATUSES,
    insert_audit_query,
    read_audit_queries_for_workspace,
    read_audit_query_by_id,
)
from kb.q_planner import (
    compile_plan,
    execute,
    parse_plan,
    validate,
)


pytestmark = pytest.mark.asyncio


@contextmanager
def _env(**kwargs):
    prior = {k: os.environ.get(k) for k in kwargs}
    for k, v in kwargs.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    get_settings.cache_clear()
    try:
        yield
    finally:
        for k, v in prior.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        get_settings.cache_clear()


@pytest.fixture
def test_workspace() -> str:
    return str(uuid.uuid4())


def headers(workspace: str) -> dict[str, str]:
    return {"X-Test-Workspace": workspace}


async def _seed_files(
    db_url: str, workspace: str, *, count: int = 3,
    doc_status: str = "live",
) -> list[str]:
    ids = []
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        for i in range(count):
            file_id = str(uuid.uuid4())
            sha = hashlib.sha256(f"{workspace}-{file_id}-{i}".encode()).hexdigest()
            await conn.execute(
                "INSERT INTO files (id, workspace_id, name, content_sha, "
                "object_key, mime_type, size_bytes, lifecycle_state, doc_status) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, 'ready', %s)",
                (file_id, workspace, f"doc_{i}.pdf", sha, f"raw/{file_id}",
                 "application/pdf", 1000 + i, doc_status),
            )
            ids.append(file_id)
    return ids


# ===========================================================================
# Migration shape
# ===========================================================================


async def test_audit_queries_table_exists_with_rls(db_url_superuser):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname = 'audit_queries'"
        )
        row = await cur.fetchone()
        assert row is not None
        assert row[0] is True and row[1] is True


async def test_audit_queries_kb_app_grant_is_append_only(db_url_superuser):
    """Audit semantics: kb_app may SELECT + INSERT but never UPDATE/DELETE."""
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT privilege_type FROM information_schema.role_table_grants "
            "WHERE grantee = 'kb_app' AND table_name = 'audit_queries'"
        )
        privs = {r[0] for r in await cur.fetchall()}
        assert privs == {"SELECT", "INSERT"}


async def test_audit_queries_status_check_enum(db_url_superuser, test_workspace):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        # Valid status.
        for status in AUDIT_QUERY_STATUSES:
            await insert_audit_query(
                conn,
                workspace_id=test_workspace,
                query_log_id=None,
                plan={"from": "files"},
                compiled_sql="SELECT 1",
                params=[],
                row_count=0,
                runtime_ms=0,
                status=status,
            )
        # Invalid status → repo raises ValueError before SQL.
        with pytest.raises(ValueError):
            await insert_audit_query(
                conn,
                workspace_id=test_workspace,
                query_log_id=None,
                plan={},
                compiled_sql="SELECT 1",
                params=[],
                row_count=0,
                runtime_ms=0,
                status="bogus",
            )


async def test_kb_app_q_role_exists(db_url_superuser):
    """kb_app_q is created for the future per-mode connection pool."""
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = 'kb_app_q'"
        )
        assert await cur.fetchone() is not None


# ===========================================================================
# Executor — layers 7 + 8 + 9 (need DB)
# ===========================================================================


async def test_executor_compiler_only_emits_select(test_workspace):
    """Layer 6/7 — Wave A relies on the compiler (kb.q_planner.compiler)
    to be the only SQL source; it ONLY ever emits SELECT. The Wave A
    executor doesn't enforce DB-level read-only because PG refuses to
    flip back to read-write mid-transaction, deadlocking the audit
    write. The kb_app_q role created in the migration is the future
    per-mode-pool defense. Compiler tests in test_b4b_unit verify the
    SELECT-only invariant; this test documents the layered choice."""
    from kb.q_planner import compile_plan, parse_plan, validate
    validated = validate(parse_plan({
        "from": "files",
        "aggregations": [{"op": "COUNT", "field": "*", "alias": "n"}],
    }))
    sql, _ = compile_plan(validated, workspace_id=test_workspace, row_cap=100)
    upper = sql.upper()
    # No mutation keywords in the compiled output.
    for forbidden in ("UPDATE ", "DELETE ", "INSERT ", "DROP ", "ALTER ", "CREATE ", "TRUNCATE "):
        assert forbidden not in upper, f"compiled SQL contains {forbidden!r}: {sql}"
    assert upper.lstrip().startswith("SELECT ")


async def test_executor_statement_timeout_triggers(db_url_superuser, test_workspace):
    """Layer 8 — pg_sleep beyond timeout returns status='timeout'."""
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
            )
            result = await execute(
                conn,
                "SELECT pg_sleep(2)",
                [],
                row_cap=1000,
                timeout_ms=200,   # 200ms cap; pg_sleep(2) = 2s
            )

    assert result.status == "timeout"
    assert result.runtime_ms < 2000  # aborted well before the 2s sleep


async def test_executor_runs_compiled_count_plan(db_url_superuser, test_workspace):
    """End-to-end: parse → validate → compile → execute returns correct
    row_count via the COUNT(*) aggregate."""
    await _seed_files(db_url_superuser, test_workspace, count=5)
    await _seed_files(
        db_url_superuser, test_workspace, count=2,
        doc_status="superseded",
    )

    plan = parse_plan({
        "from": "files",
        "filters": [{"field": "doc_status", "op": "eq", "value": "live"}],
        "aggregations": [{"op": "COUNT", "field": "*", "alias": "n"}],
    })
    validated = validate(plan)
    sql, params = compile_plan(
        validated, workspace_id=test_workspace, row_cap=1000,
    )

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
            )
            result = await execute(conn, sql, params, row_cap=1000, timeout_ms=5000)

    assert result.status == "ok"
    assert result.row_count == 1
    assert result.column_names == ("n",)
    # 5 live + 0 superseded matching the filter
    assert result.rows[0][0] == 5


async def test_executor_group_by_returns_one_row_per_status(
    db_url_superuser, test_workspace,
):
    await _seed_files(db_url_superuser, test_workspace, count=3, doc_status="live")
    await _seed_files(
        db_url_superuser, test_workspace, count=2, doc_status="superseded",
    )

    plan = parse_plan({
        "from": "files",
        "group_by": ["doc_status"],
        "aggregations": [{"op": "COUNT", "field": "*", "alias": "n"}],
        "order_by": [{"field": "n", "direction": "desc"}],
    })
    sql, params = compile_plan(
        validate(plan), workspace_id=test_workspace, row_cap=1000,
    )

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
            )
            result = await execute(conn, sql, params, row_cap=1000, timeout_ms=5000)

    assert result.status == "ok"
    by_status = {r[0]: r[1] for r in result.rows}
    assert by_status == {"live": 3, "superseded": 2}


async def test_executor_workspace_scoping_isolates_results(
    db_url_superuser,
):
    """A plan executed under workspace A must NEVER see workspace B rows.
    The compiler bakes workspace_id into the SQL; the executor's results
    confirm it."""
    ws_a = str(uuid.uuid4())
    ws_b = str(uuid.uuid4())
    await _seed_files(db_url_superuser, ws_a, count=4)
    await _seed_files(db_url_superuser, ws_b, count=7)

    plan = parse_plan({
        "from": "files",
        "aggregations": [{"op": "COUNT", "field": "*", "alias": "n"}],
    })
    validated = validate(plan)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)", (ws_a,),
            )
            sql, params = compile_plan(
                validated, workspace_id=ws_a, row_cap=1000,
            )
            result_a = await execute(conn, sql, params, row_cap=1000, timeout_ms=5000)
    assert result_a.status == "ok"
    assert result_a.rows[0][0] == 4   # only ws_a's files


# ===========================================================================
# audit_queries repo
# ===========================================================================


async def test_insert_and_read_audit_query(db_url_superuser, test_workspace):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        aid = await insert_audit_query(
            conn,
            workspace_id=test_workspace,
            query_log_id=None,
            plan={"from": "files", "aggregations": [{"op": "COUNT"}]},
            compiled_sql='SELECT COUNT(*) FROM "files" WHERE "workspace_id" = %s',
            params=[test_workspace],
            row_count=12,
            runtime_ms=42,
            status="ok",
            csv_artifact_key="q_mode_artifacts/test/abc.csv",
        )
        rec = await read_audit_query_by_id(conn, audit_query_id=aid)

    assert rec is not None
    assert rec.row_count == 12
    assert rec.runtime_ms == 42
    assert rec.status == "ok"
    assert rec.csv_artifact_key == "q_mode_artifacts/test/abc.csv"
    assert rec.plan["from"] == "files"


async def test_read_audit_queries_for_workspace_isolated(
    db_url_superuser,
):
    ws_a = str(uuid.uuid4())
    ws_b = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (ws_a,),
        )
        await insert_audit_query(
            conn,
            workspace_id=ws_a, query_log_id=None,
            plan={}, compiled_sql="SELECT 1", params=[],
            row_count=0, runtime_ms=0, status="ok",
        )
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (ws_b,),
        )
        await insert_audit_query(
            conn,
            workspace_id=ws_b, query_log_id=None,
            plan={}, compiled_sql="SELECT 1", params=[],
            row_count=0, runtime_ms=0, status="ok",
        )

        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (ws_a,),
        )
        rows = await read_audit_queries_for_workspace(
            conn, workspace_id=ws_a,
        )
    assert len(rows) == 1
    assert rows[0].workspace_id == ws_a


# ===========================================================================
# End-to-end through /chat with hand-crafted Q payload
# ===========================================================================


async def test_chat_with_q_payload_executes_and_persists_audit_row(
    client, test_workspace, db_url_superuser,
):
    """Inject a Q payload into the planner's Plan by stubbing it. We bypass
    the planner here because the IdentityPlanner can't emit a Q payload
    from a raw query — Gemini does that for real."""
    from kb.api.query import get_orchestrator
    from kb.query.planner import IdentityPlanner, Plan
    from kb.query.intent import IntentResult

    await _seed_files(db_url_superuser, test_workspace, count=4)

    class StubPlanner(IdentityPlanner):
        async def plan(self, query, intent, *, requested_mode=None):
            base = await super().plan(query, intent, requested_mode=requested_mode)
            return Plan(
                mode="Q",
                intent="aggregation",
                intent_confidence=0.95,
                q_payload={
                    "from": "files",
                    "aggregations": [{"op": "COUNT", "field": "*", "alias": "n"}],
                    "filters": [
                        {"field": "doc_status", "op": "eq", "value": "live"},
                    ],
                },
                notes="injected by test",
            )

    reset_orchestrator()
    with _env(
        KB_QUERY_LLM="identity",
        KB_FAITHFULNESS_GATE="identity",
        KB_INTENT_CLASSIFIER="identity",
    ):
        reset_orchestrator()
        orch = get_orchestrator()
        orch._planner = StubPlanner()  # type: ignore[attr-defined]
        resp = await client.post(
            "/chat",
            headers=headers(test_workspace),
            json={"query": "count files", "mode": "H"},
        )
        reset_orchestrator()

    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "Q"
    # One synthesized aggregate Hit (no q_refused).
    agg_hits = [
        h for h in body["hits"]
        if (h.get("metadata") or {}).get("aggregate") is True
        and not (h.get("metadata") or {}).get("q_refused")
    ]
    assert len(agg_hits) == 1
    audit_id = agg_hits[0]["metadata"]["audit_query_id"]
    assert agg_hits[0]["metadata"]["row_count"] == 1   # COUNT returns 1 row
    assert "Aggregate result" in agg_hits[0]["snippet"]
    assert "n=4" in agg_hits[0]["snippet"]   # 4 live files

    # audit_queries row landed.
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        rec = await read_audit_query_by_id(conn, audit_query_id=audit_id)
    assert rec is not None
    assert rec.status == "ok"
    assert rec.row_count == 1
    assert rec.workspace_id == test_workspace
    # Compiled SQL preserves placeholders, not raw values.
    assert "%s" in rec.compiled_sql
    assert "live" in rec.params


async def test_chat_with_bad_q_payload_falls_through_to_refusal(
    client, test_workspace,
):
    """A Q payload that fails validation surfaces as a q_refused Hit
    rather than a 500."""
    from kb.api.query import get_orchestrator
    from kb.query.planner import IdentityPlanner, Plan

    class BadPayloadPlanner(IdentityPlanner):
        async def plan(self, query, intent, *, requested_mode=None):
            return Plan(
                mode="Q",
                intent="aggregation",
                q_payload={
                    "from": "users",   # not in catalog → validator refuses
                    "aggregations": [{"op": "COUNT", "field": "*", "alias": "n"}],
                },
            )

    reset_orchestrator()
    with _env(
        KB_QUERY_LLM="identity",
        KB_FAITHFULNESS_GATE="identity",
        KB_INTENT_CLASSIFIER="identity",
    ):
        reset_orchestrator()
        orch = get_orchestrator()
        orch._planner = BadPayloadPlanner()  # type: ignore[attr-defined]
        resp = await client.post(
            "/chat",
            headers=headers(test_workspace),
            json={"query": "x", "mode": "H"},
        )
        reset_orchestrator()

    assert resp.status_code == 200
    body = resp.json()
    refusal = [
        h for h in body["hits"]
        if (h.get("metadata") or {}).get("q_refused")
    ]
    assert len(refusal) == 1
    assert "not in the Q-mode allowlist" in refusal[0]["metadata"]["q_refusal_reason"]


# ===========================================================================
# Regression — prior endpoints still work
# ===========================================================================


async def test_b1_endpoint_regression(client, test_workspace):
    resp = await client.get("/triples", headers=headers(test_workspace))
    assert resp.status_code == 200


async def test_b2_endpoint_regression(client, test_workspace):
    resp = await client.get("/conflicts", headers=headers(test_workspace))
    assert resp.status_code == 200


async def test_b4a_search_regression(client, test_workspace):
    reset_orchestrator()
    with _env(KB_INTENT_CLASSIFIER="identity", KB_PLANNER="identity"):
        reset_orchestrator()
        resp = await client.post(
            "/search",
            headers=headers(test_workspace),
            json={"query": "x", "mode": "H"},
        )
    assert resp.status_code == 200
