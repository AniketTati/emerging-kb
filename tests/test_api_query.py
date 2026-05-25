"""Phase 8f — Query HTTP endpoint tests (testcontainers + real DB)."""

from __future__ import annotations

import uuid

import psycopg
import pytest


pytestmark = pytest.mark.asyncio


@pytest.fixture
def test_workspace() -> str:
    return str(uuid.uuid4())


def headers(workspace: str, *, idempotency_key: str | None = None) -> dict[str, str]:
    h = {"X-Test-Workspace": workspace}
    if idempotency_key:
        h["Idempotency-Key"] = idempotency_key
    return h


@pytest.fixture(autouse=True)
def _reset_orchestrator():
    """Each test rebuilds the orchestrator from env (so per-test KB_QUERY_LLM
    overrides apply). Without this the lru singleton from a prior test leaks."""
    from kb.api import query as query_module
    query_module.reset_orchestrator()
    yield
    query_module.reset_orchestrator()


# ===========================================================================
# Migration shape (decision #11 + GRANT immutability)
# ===========================================================================


async def test_query_log_table_exists_with_rls_forced(db_url_superuser):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname='query_log'"
        )
        row = await cur.fetchone()
        assert row is not None
        assert row[0] is True and row[1] is True


async def test_kb_app_cannot_update_or_delete_query_log(db_url_superuser):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT privilege_type FROM information_schema.role_table_grants "
            "WHERE grantee='kb_app' AND table_name='query_log'"
        )
        privs = {r[0] for r in await cur.fetchall()}
        assert "SELECT" in privs
        assert "INSERT" in privs
        assert "UPDATE" not in privs
        assert "DELETE" not in privs


# ===========================================================================
# POST /search — §7.2
# ===========================================================================


async def test_post_search_returns_200_with_envelope(client, test_workspace):
    """Even with empty corpus, /search returns 200 + envelope."""
    resp = await client.post(
        "/search",
        json={"query": "what is X?"},
        headers=headers(test_workspace),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["query"] == "what is X?"
    assert "query_id" in body
    assert "rewrites" in body
    assert "hits" in body
    assert "crag_score" in body
    assert "latency_ms" in body


async def test_post_search_400_on_empty_query(client, test_workspace):
    resp = await client.post(
        "/search", json={"query": ""}, headers=headers(test_workspace),
    )
    # Pydantic min_length=1 → 422 validation-error from FastAPI handler.
    assert resp.status_code in (400, 422)


async def test_post_search_400_on_whitespace_query(client, test_workspace):
    resp = await client.post(
        "/search", json={"query": "   "}, headers=headers(test_workspace),
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["type"].endswith("/invalid-query")


async def test_post_search_422_on_oversize_query(client, test_workspace):
    big = "x" * 4001
    resp = await client.post(
        "/search", json={"query": big}, headers=headers(test_workspace),
    )
    # Pydantic max_length=4000 enforces at validation, surfacing as 422.
    assert resp.status_code in (400, 422)


async def test_post_search_400_on_unsupported_mode(client, test_workspace):
    """Any mode outside the 12 spec values is 400. (Q is now supported
    in B4b; this test covers the catch-all for unknown modes.)"""
    resp = await client.post(
        "/search", json={"query": "q", "mode": "Z"},
        headers=headers(test_workspace),
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["type"].endswith("/invalid-query")


# ===========================================================================
# POST /chat — §7.3
# ===========================================================================


async def test_post_chat_returns_200_with_chat_envelope(client, test_workspace):
    resp = await client.post(
        "/chat",
        json={"query": "what is X?"},
        headers=headers(test_workspace),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "query_id" in body
    assert "generation" in body
    assert "hits" in body
    assert "crag_score" in body
    # Empty corpus + IdentityGenerator → refusal envelope, not 4xx.
    assert body["generation"]["refused"] is True


async def test_post_chat_refusal_envelope_is_200_not_4xx(client, test_workspace):
    """Critical invariant §7.1 #3 — refusal is 200, not 4xx."""
    resp = await client.post(
        "/chat",
        json={"query": "what was Q4 2026 revenue?"},
        headers=headers(test_workspace),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["generation"]["refused"] is True
    # Empty corpus path → either no_hits or insufficient_evidence
    assert body["generation"]["refusal_reason"] in (
        "insufficient_evidence", "no_hits"
    )


async def test_post_chat_idempotency_replay_returns_cached_envelope(
    client, test_workspace,
):
    """Decision #13: replay returns cached body, doesn't re-execute."""
    idem = str(uuid.uuid4())
    first = await client.post(
        "/chat", json={"query": "what is X?"},
        headers=headers(test_workspace, idempotency_key=idem),
    )
    assert first.status_code == 200
    first_qid = first.json()["query_id"]

    second = await client.post(
        "/chat", json={"query": "what is X?"},
        headers=headers(test_workspace, idempotency_key=idem),
    )
    assert second.status_code == 200
    # Cached replay returns same query_id (the original; not re-executed).
    assert second.json()["query_id"] == first_qid


# ===========================================================================
# Audit (query_log row written per call — §7.1 #4)
# ===========================================================================


async def test_query_log_row_written_per_search_call(
    client, test_workspace, db_url_superuser,
):
    resp = await client.post(
        "/search", json={"query": "search-audit-test"},
        headers=headers(test_workspace),
    )
    assert resp.status_code == 200
    qid = resp.json()["query_id"]

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT endpoint, query FROM query_log WHERE id = %s", (qid,)
        )
        row = await cur.fetchone()
        assert row is not None
        assert row[0] == "search"
        assert row[1] == "search-audit-test"


async def test_query_log_row_written_per_chat_call_with_refused_true(
    client, test_workspace, db_url_superuser,
):
    """Empty corpus → refused=true logged in query_log."""
    resp = await client.post(
        "/chat", json={"query": "chat-audit-test"},
        headers=headers(test_workspace),
    )
    assert resp.status_code == 200
    qid = resp.json()["query_id"]

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT endpoint, refused, refusal_reason FROM query_log WHERE id = %s",
            (qid,),
        )
        row = await cur.fetchone()
        assert row is not None
        assert row[0] == "chat"
        assert row[1] is True
        assert row[2] in ("insufficient_evidence", "no_hits")


# ===========================================================================
# Workspace isolation (§7.1 #1 + #4)
# ===========================================================================


async def test_query_log_rls_workspace_b_cannot_see_a_rows(
    client, db_url_kb_app,
):
    ws_a = str(uuid.uuid4())
    ws_b = str(uuid.uuid4())

    # Workspace A makes a chat call.
    resp_a = await client.post(
        "/chat", json={"query": "ws-A query"}, headers=headers(ws_a),
    )
    assert resp_a.status_code == 200

    # As workspace B (via kb_app role + RLS), the row for A's query is invisible.
    async with await psycopg.AsyncConnection.connect(db_url_kb_app) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (ws_b,))
        cur = await conn.execute(
            "SELECT count(*) FROM query_log WHERE workspace_id = %s", (ws_a,)
        )
        cnt = (await cur.fetchone())[0]
        assert cnt == 0


# ===========================================================================
# OpenAPI surface
# ===========================================================================


async def test_openapi_includes_search_and_chat(client):
    resp = await client.get("/openapi.json")
    assert resp.status_code == 200
    paths = resp.json()["paths"]
    assert "/search" in paths
    assert "/chat" in paths
    assert "post" in paths["/search"]
    assert "post" in paths["/chat"]
