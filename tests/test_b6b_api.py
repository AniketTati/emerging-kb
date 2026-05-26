"""B6b / WA-13 — Feedback loop tests.

Covers:
  - Migration: 4 tables (corrections, entity_overrides, schema_field_overrides,
    regression_set) with RLS forced and CHECK enums
  - Repo: CRUD + route_correction's scope-conditional side effects
  - HTTP: POST/GET/PATCH /corrections, GET overrides, GET regression-set
  - Workspace isolation
  - Regression: prior endpoints (B6a sessions, B5 audit-log) still work
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from contextlib import contextmanager

import psycopg
import pytest

from kb.config import get_settings
from kb.domain.corrections import (
    CORRECTION_SCOPES,
    CORRECTION_SEVERITIES,
    CORRECTION_STATUSES,
    insert_correction,
    insert_entity_override,
    insert_regression_entry,
    insert_schema_field_override,
    list_active_entity_overrides,
    list_active_regressions,
    list_active_schema_field_overrides,
    list_corrections,
    read_correction,
    route_correction,
    update_correction_status,
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


async def _seed_file(db_url: str, workspace: str) -> str:
    file_id = str(uuid.uuid4())
    sha = hashlib.sha256(f"{workspace}-{file_id}".encode()).hexdigest()
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        await conn.execute(
            "INSERT INTO files (id, workspace_id, name, content_sha, object_key, "
            "mime_type, size_bytes, lifecycle_state) "
            "VALUES (%s, %s, 'test.pdf', %s, %s, 'application/pdf', 100, 'ready')",
            (file_id, workspace, sha, f"raw/{file_id}"),
        )
    return file_id


# ===========================================================================
# Constants
# ===========================================================================


def test_correction_scopes_complete():
    assert "entity_merge" in CORRECTION_SCOPES
    assert "entity_split" in CORRECTION_SCOPES
    assert "schema_field" in CORRECTION_SCOPES
    assert "source_authority" in CORRECTION_SCOPES
    assert len(CORRECTION_SCOPES) == 9


def test_correction_severities_complete():
    assert set(CORRECTION_SEVERITIES) == {
        "blocker", "important", "minor", "enhancement",
    }


def test_correction_statuses_complete():
    assert set(CORRECTION_STATUSES) == {
        "open", "triaged", "fixing", "verified", "closed", "rejected",
    }


# ===========================================================================
# Migration shape
# ===========================================================================


async def test_b6b_tables_rls_forced(db_url_superuser):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        for tbl in (
            "corrections", "entity_overrides",
            "schema_field_overrides", "regression_set",
        ):
            cur = await conn.execute(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname = %s", (tbl,),
            )
            row = await cur.fetchone()
            assert row is not None, f"missing {tbl}"
            assert row[0] is True and row[1] is True, f"{tbl} lacks forced RLS"


async def test_correction_scope_check_constraint(db_url_superuser, test_workspace):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        with pytest.raises(Exception):
            await conn.execute(
                "INSERT INTO corrections (workspace_id, scope, target) "
                "VALUES (%s, %s, %s::jsonb)",
                (test_workspace, "bogus_scope", "{}"),
            )


async def test_correction_severity_check_constraint(db_url_superuser, test_workspace):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        with pytest.raises(Exception):
            await conn.execute(
                "INSERT INTO corrections (workspace_id, scope, target, severity) "
                "VALUES (%s, 'answer', %s::jsonb, 'apocalyptic')",
                (test_workspace, "{}"),
            )


# ===========================================================================
# Repo: insert + read + update
# ===========================================================================


async def test_insert_and_read_correction(db_url_superuser, test_workspace):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        cid = await insert_correction(
            conn,
            workspace_id=test_workspace,
            scope="answer",
            target={"query_id": "q-1"},
            observed_value="$25M",
            correct_value="$50M",
            reason="wrong cap",
            severity="blocker",
        )
        rec = await read_correction(conn, correction_id=cid)
    assert rec is not None
    assert rec.scope == "answer"
    assert rec.severity == "blocker"
    assert rec.status == "open"
    assert rec.target == {"query_id": "q-1"}


async def test_update_correction_status_sets_resolved_at(
    db_url_superuser, test_workspace,
):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        cid = await insert_correction(
            conn, workspace_id=test_workspace,
            scope="other", target={},
        )
        ok = await update_correction_status(
            conn, correction_id=cid, status="verified",
            resolution={"note": "fixed"},
        )
        rec = await read_correction(conn, correction_id=cid)
    assert ok is True
    assert rec.status == "verified"
    assert rec.resolved_at is not None
    assert rec.resolution == {"note": "fixed"}


async def test_list_corrections_filters(db_url_superuser, test_workspace):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        await insert_correction(
            conn, workspace_id=test_workspace, scope="answer", target={},
        )
        await insert_correction(
            conn, workspace_id=test_workspace, scope="extraction", target={},
        )
        a_only = await list_corrections(
            conn, workspace_id=test_workspace, scope="answer",
        )
        e_only = await list_corrections(
            conn, workspace_id=test_workspace, scope="extraction",
        )
    assert len(a_only) == 1 and a_only[0].scope == "answer"
    assert len(e_only) == 1 and e_only[0].scope == "extraction"


# ===========================================================================
# Routing — scope-conditional side effects
# ===========================================================================


async def test_route_entity_merge_inserts_never_merge_override(
    db_url_superuser, test_workspace,
):
    ent_a = str(uuid.uuid4())
    ent_b = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        cid = await insert_correction(
            conn, workspace_id=test_workspace, scope="entity_merge",
            target={"entity_a": ent_a, "entity_b": ent_b},
            reason="not the same person",
        )
        rec = await read_correction(conn, correction_id=cid)
        outcome = await route_correction(conn, correction=rec)
        overrides = await list_active_entity_overrides(
            conn, workspace_id=test_workspace,
        )
    assert outcome.final_status == "fixing"
    assert outcome.entity_override_id is not None
    assert len(overrides) == 1
    assert overrides[0].rule_type == "never_merge"
    assert overrides[0].entity_a == ent_a
    assert overrides[0].entity_b == ent_b


async def test_route_entity_split_inserts_split_override(
    db_url_superuser, test_workspace,
):
    ent_a = str(uuid.uuid4())
    ent_b = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        cid = await insert_correction(
            conn, workspace_id=test_workspace, scope="entity_split",
            target={"entity_a": ent_a, "entity_b": ent_b},
        )
        rec = await read_correction(conn, correction_id=cid)
        outcome = await route_correction(conn, correction=rec)
        overrides = await list_active_entity_overrides(
            conn, workspace_id=test_workspace,
        )
    assert outcome.final_status == "fixing"
    assert overrides[0].rule_type == "split"


async def test_route_schema_field_inserts_override_and_verifies(
    db_url_superuser, test_workspace,
):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        cid = await insert_correction(
            conn, workspace_id=test_workspace, scope="schema_field",
            target={
                "field_path": "Contract.cap",
                "override_kind": "undo_promotion",
            },
            reason="promoted prematurely",
        )
        rec = await read_correction(conn, correction_id=cid)
        outcome = await route_correction(conn, correction=rec)
        overrides = await list_active_schema_field_overrides(
            conn, workspace_id=test_workspace,
        )
        refreshed = await read_correction(conn, correction_id=cid)
    assert outcome.final_status == "verified"
    assert outcome.schema_field_override_id is not None
    assert len(overrides) == 1
    assert overrides[0].field_path == "Contract.cap"
    assert overrides[0].override_kind == "undo_promotion"
    assert refreshed.status == "verified"
    assert refreshed.resolved_at is not None


async def test_route_source_authority_applies_override(
    db_url_superuser, test_workspace,
):
    file_id = await _seed_file(db_url_superuser, test_workspace)
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        cid = await insert_correction(
            conn, workspace_id=test_workspace, scope="source_authority",
            target={"file_id": file_id, "authority": 0.9},
            reason="this is the original signed PDF",
        )
        rec = await read_correction(conn, correction_id=cid)
        outcome = await route_correction(conn, correction=rec)
        # File's source_authority should now be 0.9.
        cur = await conn.execute(
            "SELECT source_authority FROM files WHERE id = %s", (file_id,),
        )
        sa = (await cur.fetchone())[0]
    assert outcome.final_status == "verified"
    assert float(sa) == 0.9


async def test_route_extraction_sets_fixing(db_url_superuser, test_workspace):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        cid = await insert_correction(
            conn, workspace_id=test_workspace, scope="extraction",
            target={"doc_id": str(uuid.uuid4()), "field_name": "cap"},
        )
        rec = await read_correction(conn, correction_id=cid)
        outcome = await route_correction(conn, correction=rec)
    assert outcome.final_status == "fixing"


async def test_route_extraction_blocker_defers_re_extraction(
    db_url_superuser, test_workspace,
):
    """Wave A close-up (Design 4 §"Pipeline integration"): a blocker
    correction on an extraction with implicated_docs must defer the
    targeted re-extraction tasks. Prior to this commit the route just
    set status='fixing' with a "(Wave A: deferred to follow-up commit)"
    note — the actual procrastinate defer never ran, so corrections
    sat there and the system never learned from them.

    We monkeypatch the procrastinate task's `defer_async` so the test
    runs without a live broker, and assert that defer was called once
    per implicated doc for both extraction subtasks.
    """
    from unittest.mock import AsyncMock, patch

    doc_a = str(uuid.uuid4())
    doc_b = str(uuid.uuid4())

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        cid = await insert_correction(
            conn, workspace_id=test_workspace, scope="extraction",
            target={
                "doc_id": doc_a,
                "field_name": "cap",
                "implicated_docs": [doc_a, doc_b],
            },
            severity="blocker",
            observed_value="wrong",
            correct_value="right",
        )
        rec = await read_correction(conn, correction_id=cid)

        # Patch the defer call site so we don't need a live broker.
        with patch(
            "kb.workers.tasks.procrastinate_app.configure_task"
        ) as mock_configure:
            mock_task = AsyncMock()
            mock_configure.return_value = mock_task
            outcome = await route_correction(conn, correction=rec)

    assert outcome.final_status == "fixing"
    deferred = outcome.resolution.get("deferred_re_extraction_for") or []
    assert sorted(deferred) == sorted([doc_a, doc_b]), (
        f"expected both implicated docs to be deferred; got {deferred}"
    )
    # 2 implicated docs × 2 extraction tasks = 4 configure_task calls
    assert mock_configure.call_count == 4
    # Each configure_task() returned object had defer_async called once.
    assert mock_task.defer_async.call_count == 4


async def test_route_extraction_low_severity_does_not_defer(
    db_url_superuser, test_workspace,
):
    """Same shape but with severity='minor' — re-extraction must NOT
    fire, since low-impact feedback shouldn't churn the worker queue.
    Routing still sets status='fixing'."""
    from unittest.mock import patch

    doc_a = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        cid = await insert_correction(
            conn, workspace_id=test_workspace, scope="extraction",
            target={"doc_id": doc_a, "implicated_docs": [doc_a]},
            severity="minor",
        )
        rec = await read_correction(conn, correction_id=cid)
        with patch(
            "kb.workers.tasks.procrastinate_app.configure_task"
        ) as mock_configure:
            outcome = await route_correction(conn, correction=rec)

    assert outcome.final_status == "fixing"
    assert outcome.resolution.get("deferred_re_extraction_for") == []
    assert mock_configure.call_count == 0


async def test_route_answer_triages(db_url_superuser, test_workspace):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        cid = await insert_correction(
            conn, workspace_id=test_workspace, scope="answer", target={},
        )
        rec = await read_correction(conn, correction_id=cid)
        outcome = await route_correction(conn, correction=rec)
    assert outcome.final_status == "triaged"


async def test_route_creates_regression_entry_for_blocker_with_query_text(
    db_url_superuser, test_workspace,
):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        cid = await insert_correction(
            conn, workspace_id=test_workspace, scope="answer",
            target={"query_text": "What's the cap on contract X?"},
            observed_value="$25M",
            correct_value="$50M",
            severity="blocker",
        )
        rec = await read_correction(conn, correction_id=cid)
        outcome = await route_correction(conn, correction=rec)
        regressions = await list_active_regressions(
            conn, workspace_id=test_workspace,
        )
    assert outcome.regression_entry_id is not None
    assert len(regressions) == 1
    assert regressions[0].query_text == "What's the cap on contract X?"
    assert regressions[0].severity == "blocker"


async def test_route_skips_regression_entry_for_minor_severity(
    db_url_superuser, test_workspace,
):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        cid = await insert_correction(
            conn, workspace_id=test_workspace, scope="answer",
            target={"query_text": "x"},
            severity="minor",
        )
        rec = await read_correction(conn, correction_id=cid)
        outcome = await route_correction(conn, correction=rec)
    assert outcome.regression_entry_id is None


# ===========================================================================
# HTTP endpoints
# ===========================================================================


async def test_post_correction_returns_outcome(client, test_workspace):
    resp = await client.post(
        "/corrections", headers=headers(test_workspace),
        json={
            "scope": "entity_merge",
            "target": {
                "entity_a": str(uuid.uuid4()),
                "entity_b": str(uuid.uuid4()),
            },
            "reason": "wrong merge",
            "severity": "important",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "fixing"
    assert body["entity_override_id"] is not None


async def test_post_correction_400_on_bad_scope(client, test_workspace):
    resp = await client.post(
        "/corrections", headers=headers(test_workspace),
        json={"scope": "bogus", "target": {}},
    )
    assert resp.status_code == 400


async def test_post_correction_400_on_bad_severity(client, test_workspace):
    resp = await client.post(
        "/corrections", headers=headers(test_workspace),
        json={"scope": "answer", "target": {}, "severity": "yolo"},
    )
    assert resp.status_code == 400


async def test_get_corrections_returns_seeded(client, test_workspace, db_url_superuser):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        await insert_correction(
            conn, workspace_id=test_workspace,
            scope="answer", target={"query_id": "q-1"},
        )
    resp = await client.get(
        "/corrections", headers=headers(test_workspace),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1


async def test_get_corrections_filter_by_scope(client, test_workspace, db_url_superuser):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        await insert_correction(
            conn, workspace_id=test_workspace, scope="answer", target={},
        )
        await insert_correction(
            conn, workspace_id=test_workspace, scope="extraction", target={},
        )
    resp = await client.get(
        "/corrections?scope=extraction", headers=headers(test_workspace),
    )
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["scope"] == "extraction"


async def test_get_corrections_bad_scope_filter_400(client, test_workspace):
    resp = await client.get(
        "/corrections?scope=bogus", headers=headers(test_workspace),
    )
    assert resp.status_code == 400


async def test_get_correction_404_on_missing(client, test_workspace):
    resp = await client.get(
        f"/corrections/{uuid.uuid4()}", headers=headers(test_workspace),
    )
    assert resp.status_code == 404


async def test_patch_correction_admin_update(client, test_workspace, db_url_superuser):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        cid = await insert_correction(
            conn, workspace_id=test_workspace, scope="other", target={},
        )
    resp = await client.patch(
        f"/corrections/{cid}", headers=headers(test_workspace),
        json={"status": "rejected", "resolution": {"note": "not a real issue"}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "rejected"
    assert body["resolved_at"] is not None


async def test_get_entity_overrides_lists_active(
    client, test_workspace, db_url_superuser,
):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        await insert_entity_override(
            conn, workspace_id=test_workspace,
            rule_type="never_merge",
            entity_a=str(uuid.uuid4()),
            entity_b=str(uuid.uuid4()),
        )
    resp = await client.get(
        "/entity-overrides", headers=headers(test_workspace),
    )
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["rule_type"] == "never_merge"


async def test_get_schema_field_overrides_lists_active(
    client, test_workspace, db_url_superuser,
):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        await insert_schema_field_override(
            conn, workspace_id=test_workspace,
            field_path="Contract.cap", override_kind="blacklist",
        )
    resp = await client.get(
        "/schema-field-overrides", headers=headers(test_workspace),
    )
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["override_kind"] == "blacklist"


async def test_get_regression_set_lists_active(
    client, test_workspace, db_url_superuser,
):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        await insert_regression_entry(
            conn, workspace_id=test_workspace,
            query_text="cap on contract X",
            expected_facts={"value": "$50M"},
        )
    resp = await client.get(
        "/regression-set", headers=headers(test_workspace),
    )
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["query_text"] == "cap on contract X"


# ===========================================================================
# Workspace isolation
# ===========================================================================


async def test_workspace_isolation_on_corrections(client, db_url_superuser):
    ws_a = str(uuid.uuid4())
    ws_b = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (ws_a,),
        )
        await insert_correction(
            conn, workspace_id=ws_a, scope="answer", target={},
        )
    resp = await client.get("/corrections", headers=headers(ws_b))
    assert resp.status_code == 200
    assert resp.json()["items"] == []


# ===========================================================================
# Regression
# ===========================================================================


async def test_b6a_sessions_endpoint_still_works(client, test_workspace):
    resp = await client.get("/sessions", headers=headers(test_workspace))
    assert resp.status_code == 200


async def test_b5_audit_log_still_works(client, test_workspace):
    resp = await client.get("/audit-log", headers=headers(test_workspace))
    assert resp.status_code == 200


async def test_b2_conflicts_still_works(client, test_workspace):
    resp = await client.get("/conflicts", headers=headers(test_workspace))
    assert resp.status_code == 200
