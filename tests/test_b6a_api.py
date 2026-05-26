"""B6a / WA-12 — HTTP + integration tests for conversation memory.

Covers:
  - Migration: chat_sessions + chat_turns tables, RLS forced, kb_app
    GRANT shape, indexes
  - Repo: create_session, insert_turn (auto-incrementing turn_index),
    update_session_carry_forward, build_chat_context (3-tier assembly)
  - HTTP: POST /sessions, GET /sessions, GET /sessions/{id},
    GET /sessions/{id}/turns, GET /sessions/{id}/context
  - End-to-end: /chat with session_id appends a chat_turns row and rolls
    carry-forward state when the Identity resolver detects refinement
  - Workspace isolation: another workspace can't see this workspace's sessions
  - Regression: prior /chat without session_id still works
"""

from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager

import psycopg
import pytest

from kb.api.query import reset_orchestrator
from kb.config import get_settings
from kb.domain.chat_memory import (
    build_chat_context,
    count_turns_in_session,
    create_session,
    insert_turn,
    list_recent_sessions,
    read_last_k_turns,
    read_session,
    update_session_carry_forward,
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


# ===========================================================================
# Migration shape
# ===========================================================================


async def test_chat_sessions_table_rls_forced(db_url_superuser):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname = 'chat_sessions'"
        )
        row = await cur.fetchone()
        assert row is not None
        assert row[0] is True and row[1] is True


async def test_chat_turns_table_rls_forced(db_url_superuser):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname = 'chat_turns'"
        )
        row = await cur.fetchone()
        assert row is not None
        assert row[0] is True and row[1] is True


async def test_chat_sessions_grants(db_url_superuser):
    """Sessions are mutable (carry-forward updates) AND deletable by the
    chat-history sidebar's row-trash UX (migration 0035). The old
    contract was "no DELETE for kb_app" — that left the user with no
    way to clean up their history."""
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT privilege_type FROM information_schema.role_table_grants "
            "WHERE grantee = 'kb_app' AND table_name = 'chat_sessions'"
        )
        privs = {r[0] for r in await cur.fetchall()}
        assert {"SELECT", "INSERT", "UPDATE", "DELETE"}.issubset(privs)


async def test_chat_turns_grants(db_url_superuser):
    """Turns are insert-and-read by kb_app, plus DELETE via cascade on
    session removal (migration 0035 grants the DELETE so the cascade
    fires under the kb_app role; without it the API's session-delete
    would fail with 'permission denied for table chat_turns')."""
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT privilege_type FROM information_schema.role_table_grants "
            "WHERE grantee = 'kb_app' AND table_name = 'chat_turns'"
        )
        privs = {r[0] for r in await cur.fetchall()}
        assert {"SELECT", "INSERT", "DELETE"}.issubset(privs)
        # UPDATE intentionally NOT granted — turns are immutable once
        # persisted (the audit-style append-only contract).
        assert "UPDATE" not in privs


async def test_chat_turns_unique_per_session_index(
    db_url_superuser, test_workspace,
):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        sid = await create_session(conn, workspace_id=test_workspace)
        await conn.execute(
            "INSERT INTO chat_turns (workspace_id, session_id, turn_index, "
            "user_query) VALUES (%s, %s, 0, 'first')",
            (test_workspace, sid),
        )
        with pytest.raises(Exception):
            await conn.execute(
                "INSERT INTO chat_turns (workspace_id, session_id, turn_index, "
                "user_query) VALUES (%s, %s, 0, 'duplicate')",
                (test_workspace, sid),
            )


# ===========================================================================
# Repo
# ===========================================================================


async def test_create_and_read_session(db_url_superuser, test_workspace):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        sid = await create_session(
            conn, workspace_id=test_workspace, title="Test Conv",
        )
        s = await read_session(conn, session_id=sid)
    assert s is not None
    assert s.workspace_id == test_workspace
    assert s.title == "Test Conv"
    assert s.carry_forward_entities == ()
    assert s.older_turn_summary == ""


async def test_insert_turn_auto_increments_index(
    db_url_superuser, test_workspace,
):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        sid = await create_session(conn, workspace_id=test_workspace)
        ids = []
        indices = []
        for i in range(4):
            tid, ti = await insert_turn(
                conn,
                workspace_id=test_workspace,
                session_id=sid,
                user_query=f"q{i}",
                resolved_query=None,
                answer=f"a{i}",
                citations=[],
                context_used={},
            )
            ids.append(tid)
            indices.append(ti)
        n = await count_turns_in_session(conn, session_id=sid)
    assert indices == [0, 1, 2, 3]
    assert n == 4


async def test_update_session_carry_forward(
    db_url_superuser, test_workspace,
):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        sid = await create_session(conn, workspace_id=test_workspace)
        # We need real entity ids that exist for the uuid[] type check.
        ent_a = str(uuid.uuid4())
        ent_b = str(uuid.uuid4())
        ok = await update_session_carry_forward(
            conn,
            session_id=sid,
            carry_forward_entities=[ent_a, ent_b],
            carry_forward_filters={"date_range": {"from": "2026-01", "to": "2026-03"}},
            older_turn_summary="Older turn rolling summary text.",
        )
        assert ok is True
        s = await read_session(conn, session_id=sid)
    assert set(s.carry_forward_entities) == {ent_a, ent_b}
    assert s.carry_forward_filters["date_range"]["from"] == "2026-01"
    assert "rolling summary" in s.older_turn_summary


async def test_update_session_carry_forward_no_op_on_missing(db_url_superuser):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        ok = await update_session_carry_forward(
            conn, session_id=str(uuid.uuid4()),
            older_turn_summary="x",
        )
        assert ok is False


async def test_build_chat_context_three_tier_assembly(
    db_url_superuser, test_workspace,
):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        sid = await create_session(conn, workspace_id=test_workspace)
        # Insert 3 turns.
        for i in range(3):
            await insert_turn(
                conn,
                workspace_id=test_workspace,
                session_id=sid,
                user_query=f"q{i}",
                resolved_query=None,
                answer=f"a{i}",
                citations=[],
                context_used={},
            )
        # Roll carry-forward state.
        ent = str(uuid.uuid4())
        await update_session_carry_forward(
            conn,
            session_id=sid,
            carry_forward_entities=[ent],
            carry_forward_filters={"doc_type": ["contract"]},
            older_turn_summary="Older summary.",
        )
        ctx = await build_chat_context(conn, session_id=sid, k_hot_turns=2)

    assert ctx is not None
    # Tier 3 — carry-forward.
    assert ctx.carry_forward_entities == (ent,)
    assert ctx.carry_forward_filters == {"doc_type": ["contract"]}
    # Tier 2 — older summary.
    assert ctx.older_turn_summary == "Older summary."
    # Tier 1 — last K turns in chronological order.
    assert len(ctx.last_k_verbatim_turns) == 2
    # Last turn is the most recent (turn_index=2).
    assert ctx.last_k_verbatim_turns[-1]["turn_index"] == 2


async def test_build_chat_context_returns_none_for_missing_session(
    db_url_superuser,
):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        ctx = await build_chat_context(conn, session_id=str(uuid.uuid4()))
        assert ctx is None


# ===========================================================================
# HTTP endpoints
# ===========================================================================


async def test_post_session_returns_id(client, test_workspace):
    resp = await client.post(
        "/sessions", headers=headers(test_workspace),
        json={"title": "My Conv"},
    )
    assert resp.status_code == 200
    assert "id" in resp.json()


async def test_get_sessions_lists_recent(client, test_workspace):
    for i in range(3):
        await client.post(
            "/sessions", headers=headers(test_workspace),
            json={"title": f"Session {i}"},
        )
    resp = await client.get("/sessions", headers=headers(test_workspace))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 3
    # Newest first.
    assert body["items"][0]["title"] == "Session 2"


async def test_get_session_404_when_missing(client, test_workspace):
    resp = await client.get(
        f"/sessions/{uuid.uuid4()}", headers=headers(test_workspace),
    )
    assert resp.status_code == 404


async def test_get_session_returns_shape(client, test_workspace):
    create_resp = await client.post(
        "/sessions", headers=headers(test_workspace), json={},
    )
    sid = create_resp.json()["id"]
    resp = await client.get(f"/sessions/{sid}", headers=headers(test_workspace))
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == sid
    assert body["carry_forward_entities"] == []
    assert body["older_turn_summary"] == ""


async def test_get_session_context_returns_404_when_missing(
    client, test_workspace,
):
    resp = await client.get(
        f"/sessions/{uuid.uuid4()}/context",
        headers=headers(test_workspace),
    )
    assert resp.status_code == 404


async def test_get_session_context_empty_session(client, test_workspace):
    sid = (await client.post(
        "/sessions", headers=headers(test_workspace), json={},
    )).json()["id"]
    resp = await client.get(
        f"/sessions/{sid}/context", headers=headers(test_workspace),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == sid
    assert body["last_turn_id"] is None
    assert body["last_k_verbatim_turns"] == []


# ===========================================================================
# End-to-end: /chat with session_id
# ===========================================================================


async def test_chat_with_session_id_persists_turn(
    client, test_workspace, db_url_superuser,
):
    sid = (await client.post(
        "/sessions", headers=headers(test_workspace), json={"title": "Test"},
    )).json()["id"]

    reset_orchestrator()
    with _env(
        KB_QUERY_LLM="identity",
        KB_FAITHFULNESS_GATE="identity",
        KB_INTENT_CLASSIFIER="identity",
        KB_PLANNER="identity",
        KB_CONTEXT_RESOLVER="identity",
    ):
        reset_orchestrator()
        resp = await client.post(
            "/chat",
            headers=headers(test_workspace),
            json={
                "query": "Tell me about contracts.",
                "mode": "H",
                "session_id": sid,
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == sid
    assert body["turn_index"] == 0

    # Turn was persisted.
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        n = await count_turns_in_session(conn, session_id=sid)
    assert n == 1


async def test_chat_followup_uses_prior_context(
    client, test_workspace, db_url_superuser,
):
    """Two-turn conversation. The second turn carries the first turn's
    context — the Identity resolver appends a hint when it detects
    anaphora."""
    sid = (await client.post(
        "/sessions", headers=headers(test_workspace), json={},
    )).json()["id"]

    reset_orchestrator()
    with _env(
        KB_QUERY_LLM="identity",
        KB_FAITHFULNESS_GATE="identity",
        KB_INTENT_CLASSIFIER="identity",
        KB_PLANNER="identity",
        KB_CONTEXT_RESOLVER="identity",
    ):
        reset_orchestrator()
        # Turn 1: establishes context.
        r1 = await client.post(
            "/chat", headers=headers(test_workspace),
            json={
                "query": "Tell me about ACME Corp.",
                "mode": "H", "session_id": sid,
            },
        )
        assert r1.status_code == 200
        assert r1.json()["turn_index"] == 0
        # Turn 2: follow-up with a pronoun → resolver fires.
        r2 = await client.post(
            "/chat", headers=headers(test_workspace),
            json={
                "query": "What about their contracts?",
                "mode": "H", "session_id": sid,
            },
        )
        assert r2.status_code == 200
        body2 = r2.json()
    assert body2["turn_index"] == 1
    # Resolver fired — context_resolution is non-null.
    assert body2["context_resolution"] is not None
    # resolved_query should be populated (heuristic hint appended).
    assert body2["resolved_query"] is not None

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        n = await count_turns_in_session(conn, session_id=sid)
    assert n == 2


async def test_chat_without_session_id_auto_creates_session(
    client, test_workspace,
):
    """No session_id passed → orchestrator auto-creates one + persists the
    turn. Without this the chat-history sidebar would be permanently
    empty for any client that doesn't manage sessions itself.

    Prior contract was "no session_id → skip memory entirely" but that
    left every UI-driven chat as an unrecoverable orphan. New contract:
    every chat lands in chat_turns, identified by an auto-created
    session id that the response echoes back. The title backfills from
    the first user query so the sidebar reads as a thread label.
    """
    reset_orchestrator()
    with _env(
        KB_QUERY_LLM="identity",
        KB_FAITHFULNESS_GATE="identity",
        KB_INTENT_CLASSIFIER="identity",
        KB_PLANNER="identity",
        KB_CONTEXT_RESOLVER="identity",
    ):
        reset_orchestrator()
        resp = await client.post(
            "/chat", headers=headers(test_workspace),
            json={"query": "x", "mode": "H"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] is not None
    assert body["turn_index"] == 0


async def test_chat_session_title_backfills_from_first_user_query(
    client, test_workspace, db_url_superuser,
):
    """The auto-created session's title is set to the first user query
    so the chat-history sidebar shows a readable label instead of
    "Untitled". Only fires on turn_index == 0 — subsequent turns must
    not overwrite the title.
    """
    import psycopg as _psycopg
    reset_orchestrator()
    with _env(
        KB_QUERY_LLM="identity",
        KB_FAITHFULNESS_GATE="identity",
        KB_INTENT_CLASSIFIER="identity",
        KB_PLANNER="identity",
        KB_CONTEXT_RESOLVER="identity",
    ):
        reset_orchestrator()
        resp1 = await client.post(
            "/chat", headers=headers(test_workspace),
            json={"query": "what's in this workspace", "mode": "H"},
        )
        sid = resp1.json()["session_id"]
        # Second turn in same session must NOT overwrite the title.
        await client.post(
            "/chat", headers=headers(test_workspace),
            json={"query": "follow-up question", "mode": "H",
                  "session_id": sid},
        )

    async with await _psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT title FROM chat_sessions WHERE id = %s", (sid,),
        )
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == "what's in this workspace"


async def test_chat_with_invalid_session_id_is_treated_as_standalone(
    client, test_workspace,
):
    """A session_id that doesn't exist in this workspace → orchestrator
    silently no-ops the memory path. The chat still answers normally."""
    bogus_sid = str(uuid.uuid4())
    reset_orchestrator()
    with _env(
        KB_QUERY_LLM="identity",
        KB_FAITHFULNESS_GATE="identity",
        KB_INTENT_CLASSIFIER="identity",
        KB_PLANNER="identity",
        KB_CONTEXT_RESOLVER="identity",
    ):
        reset_orchestrator()
        resp = await client.post(
            "/chat", headers=headers(test_workspace),
            json={"query": "x", "mode": "H", "session_id": bogus_sid},
        )
    assert resp.status_code == 200
    body = resp.json()
    # session_id is echoed but turn_index is None (no persistence).
    assert body["turn_index"] is None


# ===========================================================================
# Workspace isolation
# ===========================================================================


async def test_workspace_isolation_on_sessions(client, db_url_superuser):
    ws_a = str(uuid.uuid4())
    ws_b = str(uuid.uuid4())
    await client.post(
        "/sessions", headers=headers(ws_a), json={"title": "A only"},
    )
    resp = await client.get("/sessions", headers=headers(ws_b))
    assert resp.status_code == 200
    assert resp.json()["items"] == []


# ===========================================================================
# Regression
# ===========================================================================


async def test_legacy_chat_no_session_still_works(client, test_workspace):
    reset_orchestrator()
    with _env(KB_INTENT_CLASSIFIER="identity", KB_PLANNER="identity"):
        reset_orchestrator()
        resp = await client.post(
            "/chat", headers=headers(test_workspace),
            json={"query": "x", "mode": "H"},
        )
    assert resp.status_code == 200


async def test_b5_audit_log_endpoint_still_works(client, test_workspace):
    resp = await client.get("/audit-log", headers=headers(test_workspace))
    assert resp.status_code == 200


async def test_b4b_search_endpoint_still_works(client, test_workspace):
    reset_orchestrator()
    with _env(KB_INTENT_CLASSIFIER="identity", KB_PLANNER="identity"):
        reset_orchestrator()
        resp = await client.post(
            "/search", headers=headers(test_workspace),
            json={"query": "x", "mode": "H"},
        )
    assert resp.status_code == 200


# ===========================================================================
# DELETE /sessions (single + batch)
# ===========================================================================


async def test_delete_session_removes_row_and_cascades_turns(
    client, test_workspace, db_url_superuser,
):
    """DELETE /sessions/{id} hard-removes the session + its turns
    (FK ON DELETE CASCADE). Returns the number of session rows removed.
    """
    reset_orchestrator()
    with _env(
        KB_QUERY_LLM="identity", KB_FAITHFULNESS_GATE="identity",
        KB_INTENT_CLASSIFIER="identity", KB_PLANNER="identity",
        KB_CONTEXT_RESOLVER="identity",
    ):
        reset_orchestrator()
        # Land a turn so we have a real session + turn row to delete.
        chat_resp = await client.post(
            "/chat", headers=headers(test_workspace),
            json={"query": "doomed turn"},
        )
    sid = chat_resp.json()["session_id"]
    assert sid is not None

    # Delete it.
    resp = await client.delete(
        f"/sessions/{sid}", headers=headers(test_workspace),
    )
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 1

    # Confirm the row is gone + the cascade fired.
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT count(*)::int FROM chat_sessions WHERE id = %s", (sid,),
        )
        assert (await cur.fetchone())[0] == 0
        cur = await conn.execute(
            "SELECT count(*)::int FROM chat_turns WHERE session_id = %s", (sid,),
        )
        assert (await cur.fetchone())[0] == 0


async def test_delete_session_404_when_missing(client, test_workspace):
    """Deleting a non-existent session id returns 404, not 200/0."""
    import uuid as _uuid
    bogus = str(_uuid.uuid4())
    resp = await client.delete(
        f"/sessions/{bogus}", headers=headers(test_workspace),
    )
    assert resp.status_code == 404


async def test_delete_sessions_batch_drops_multiple(
    client, test_workspace, db_url_superuser,
):
    """POST /sessions/delete-batch removes several sessions in one call."""
    reset_orchestrator()
    with _env(
        KB_QUERY_LLM="identity", KB_FAITHFULNESS_GATE="identity",
        KB_INTENT_CLASSIFIER="identity", KB_PLANNER="identity",
        KB_CONTEXT_RESOLVER="identity",
    ):
        reset_orchestrator()
        ids: list[str] = []
        for i in range(3):
            r = await client.post(
                "/chat", headers=headers(test_workspace),
                json={"query": f"batch turn {i}"},
            )
            ids.append(r.json()["session_id"])

    resp = await client.post(
        "/sessions/delete-batch",
        headers=headers(test_workspace),
        json={"session_ids": ids},
    )
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 3

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT count(*)::int FROM chat_sessions WHERE id::text = ANY(%s)",
            (ids,),
        )
        assert (await cur.fetchone())[0] == 0


async def test_get_session_turns_includes_pipeline_metadata(
    client, test_workspace,
):
    """Turns LEFT JOIN query_log so mode/intent/crag/faithfulness come
    back inline. Pre-fix the chat UI's replay showed '?' for these
    fields because they lived on query_log but the /turns endpoint
    only read chat_turns columns."""
    reset_orchestrator()
    with _env(
        KB_QUERY_LLM="identity", KB_FAITHFULNESS_GATE="identity",
        KB_INTENT_CLASSIFIER="identity", KB_PLANNER="identity",
        KB_CONTEXT_RESOLVER="identity",
    ):
        reset_orchestrator()
        chat_resp = await client.post(
            "/chat", headers=headers(test_workspace),
            json={"query": "metadata-test query"},
        )
    sid = chat_resp.json()["session_id"]

    resp = await client.get(
        f"/sessions/{sid}/turns", headers=headers(test_workspace),
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    turn = items[0]
    # The Identity stack always sets a mode + intent + crag, so these
    # must come through (any non-null value passes — we're testing the
    # JOIN wiring, not the exact stack output).
    assert turn.get("mode") is not None
    assert turn.get("intent") is not None
    assert turn.get("crag_score") is not None
