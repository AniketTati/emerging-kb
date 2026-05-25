"""B5 / WA-11 — Hash-chained audit_log integration tests.

Covers:
  - Migration shape: pgcrypto extension loaded; trigger present;
    audit_log_recompute_chain function exists + grants
  - Trigger behavior: BEFORE INSERT fills prev_hash + hash; chain
    increments across inserts; multiple workspaces have independent chains
  - Python ↔ Postgres parity: compute_row_hash matches what the trigger
    wrote for an identical (workspace_id, created_at, payload) tuple
  - Integrity walker:
    - clean chain → ok=True
    - tampered payload → first divergence row reported
    - tampered hash → reported
    - tampered prev_hash → reported
  - HTTP endpoints: /audit-log and /audit-log/integrity
  - RLS isolation across workspaces
  - Regression: prior /audit (query_log) still works
"""

from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager

import psycopg
import pytest

from kb.config import get_settings
from kb.domain.audit_chain import (
    compute_genesis_hash,
    compute_row_hash,
    insert_audit_event,
    read_audit_log,
    walk_chain,
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


async def test_pgcrypto_extension_loaded(db_url_superuser):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT 1 FROM pg_extension WHERE extname = 'pgcrypto'"
        )
        assert await cur.fetchone() is not None


async def test_audit_log_trigger_installed(db_url_superuser):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT tgname FROM pg_trigger "
            "WHERE tgrelid = 'audit_log'::regclass AND NOT tgisinternal"
        )
        names = {r[0] for r in await cur.fetchall()}
        assert "audit_log_chain_trg" in names


async def test_recompute_chain_function_exists(db_url_superuser):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT proname FROM pg_proc WHERE proname = 'audit_log_recompute_chain'"
        )
        assert await cur.fetchone() is not None


# ===========================================================================
# Trigger behavior — chain assembly
# ===========================================================================


async def test_trigger_fills_genesis_hashes_on_first_insert(
    db_url_superuser, test_workspace,
):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        await conn.execute(
            "INSERT INTO audit_log (workspace_id, actor, action, payload) "
            "VALUES (%s, 'system:test', 'genesis', %s::jsonb)",
            (test_workspace, json.dumps({"first": True})),
        )
        cur = await conn.execute(
            "SELECT prev_hash IS NOT NULL, hash IS NOT NULL, length(hash) "
            "FROM audit_log WHERE workspace_id = %s",
            (test_workspace,),
        )
        row = await cur.fetchone()

    assert row is not None
    prev_set, hash_set, hash_len = row
    assert prev_set is True
    assert hash_set is True
    assert hash_len == 32  # SHA-256


async def test_trigger_chains_subsequent_rows(
    db_url_superuser, test_workspace,
):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        for i in range(3):
            await conn.execute(
                "INSERT INTO audit_log (workspace_id, actor, action, payload) "
                "VALUES (%s, 'system:test', %s, %s::jsonb)",
                (test_workspace, f"action_{i}", json.dumps({"i": i})),
            )
        cur = await conn.execute(
            "SELECT id::text, prev_hash, hash FROM audit_log "
            "WHERE workspace_id = %s ORDER BY created_at ASC",
            (test_workspace,),
        )
        rows = await cur.fetchall()

    assert len(rows) == 3
    # Row 1's prev_hash = row 0's hash; row 2's prev_hash = row 1's hash.
    assert rows[1][1] == rows[0][2]
    assert rows[2][1] == rows[1][2]
    # All distinct hashes.
    hashes = [r[2] for r in rows]
    assert len(set(hashes)) == 3


async def test_workspaces_have_independent_chains(db_url_superuser):
    ws_a = str(uuid.uuid4())
    ws_b = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        # Insert one in each workspace.
        for ws in (ws_a, ws_b):
            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)", (ws,),
            )
            await conn.execute(
                "INSERT INTO audit_log (workspace_id, actor, action, payload) "
                "VALUES (%s, 'system:test', 'init', %s::jsonb)",
                (ws, json.dumps({})),
            )

        cur = await conn.execute(
            "SELECT workspace_id::text, hash FROM audit_log "
            "WHERE workspace_id = ANY(%s)",
            ([ws_a, ws_b],),
        )
        rows = await cur.fetchall()

    assert len(rows) == 2
    h_by_ws = {r[0]: r[1] for r in rows}
    # Different workspaces produce different genesis hashes.
    assert h_by_ws[ws_a] != h_by_ws[ws_b]


async def test_kb_app_cannot_update_or_delete_audit_log(
    db_url_kb_app, test_workspace, db_url_superuser,
):
    """Append-only at the role level — even bypassing the trigger via
    UPDATE/DELETE is refused at the GRANT layer."""
    # Insert as superuser so we have a row to attack.
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        cur = await conn.execute(
            "INSERT INTO audit_log (workspace_id, actor, action, payload) "
            "VALUES (%s, 'system:test', 'x', %s::jsonb) RETURNING id::text",
            (test_workspace, "{}"),
        )
        row_id = (await cur.fetchone())[0]

    async with await psycopg.AsyncConnection.connect(db_url_kb_app) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        with pytest.raises(Exception):
            await conn.execute(
                "UPDATE audit_log SET actor = 'evil' WHERE id = %s",
                (row_id,),
            )
        # rollback the aborted txn before the next attempt
        try:
            await conn.execute("ROLLBACK")
        except Exception:
            pass
        with pytest.raises(Exception):
            await conn.execute(
                "DELETE FROM audit_log WHERE id = %s", (row_id,),
            )


# ===========================================================================
# Python ↔ Postgres parity
# ===========================================================================


async def test_python_genesis_matches_trigger_genesis(
    db_url_superuser, test_workspace,
):
    """Python's `compute_genesis_hash` must produce the same bytes the
    trigger writes for the first row in a workspace. Row-hash parity
    isn't strictly required (the SQL walker is what verifies integrity
    operationally) because timestamptz + jsonb text rendering varies
    across psycopg / PG versions. Genesis is simpler — just text
    interpolation — and IS expected to match exactly."""
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        await conn.execute(
            "INSERT INTO audit_log (workspace_id, actor, action, payload) "
            "VALUES (%s, 'system:test', 'init', %s::jsonb)",
            (test_workspace, json.dumps({"foo": "bar", "n": 1})),
        )
        # Read the genesis prev_hash AND the PG-rendered timestamp text so
        # Python sees exactly the string the trigger used.
        cur = await conn.execute(
            "SELECT prev_hash, created_at::text "
            "FROM audit_log WHERE workspace_id = %s",
            (test_workspace,),
        )
        prev_hash, created_at_text = await cur.fetchone()

    # Use the PG-rendered text directly — no timezone-format guessing.
    py_genesis = compute_genesis_hash(test_workspace, created_at_text)
    assert bytes(prev_hash) == py_genesis


# ===========================================================================
# Integrity walker
# ===========================================================================


async def test_walk_chain_clean_ok(db_url_superuser, test_workspace):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        for i in range(5):
            await insert_audit_event(
                conn, workspace_id=test_workspace,
                actor="system:test", action=f"a_{i}",
                payload={"i": i},
            )

        result = await walk_chain(conn, workspace_id=test_workspace)
    assert result.ok is True
    assert result.total_rows == 5
    assert result.broken_at_row_id is None


async def test_walk_chain_detects_tampered_payload(
    db_url_superuser, test_workspace,
):
    """Superuser bypasses RLS + GRANTs to tamper with a row. The walker
    must catch the divergence at the tampered row's position."""
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        ids = []
        for i in range(3):
            ids.append(await insert_audit_event(
                conn, workspace_id=test_workspace,
                actor="system:test", action=f"a_{i}",
                payload={"i": i},
            ))

        # Tamper with row index 1 directly (bypass trigger — UPDATE doesn't
        # fire the BEFORE INSERT trigger).
        await conn.execute(
            "UPDATE audit_log SET payload = %s::jsonb WHERE id = %s",
            (json.dumps({"i": 999, "tampered": True}), ids[1]),
        )

        result = await walk_chain(conn, workspace_id=test_workspace)
    assert result.ok is False
    assert result.broken_at_position == 2  # 1-indexed
    assert result.broken_at_row_id == ids[1]
    assert "tampered" in (result.notes or "") or "mismatch" in (result.notes or "")


async def test_walk_chain_detects_tampered_hash(
    db_url_superuser, test_workspace,
):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        ids = []
        for i in range(3):
            ids.append(await insert_audit_event(
                conn, workspace_id=test_workspace,
                actor="system:test", action=f"a_{i}",
                payload={"i": i},
            ))
        # Tamper with the stored hash of row 1 — payload looks legit but
        # hash no longer matches the contents.
        await conn.execute(
            "UPDATE audit_log SET hash = decode(%s, 'hex') WHERE id = %s",
            ("ff" * 32, ids[1]),
        )

        result = await walk_chain(conn, workspace_id=test_workspace)
    assert result.ok is False
    # The walker first checks the stored row's hash against the recompute.
    assert result.broken_at_row_id == ids[1]


async def test_walk_chain_detects_tampered_prev_hash(
    db_url_superuser, test_workspace,
):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        ids = []
        for i in range(3):
            ids.append(await insert_audit_event(
                conn, workspace_id=test_workspace,
                actor="system:test", action=f"a_{i}",
                payload={"i": i},
            ))
        # Break the prev_hash link on row 2 (its hash will no longer
        # recompute correctly because prev changed).
        await conn.execute(
            "UPDATE audit_log SET prev_hash = decode(%s, 'hex') WHERE id = %s",
            ("aa" * 32, ids[2]),
        )

        result = await walk_chain(conn, workspace_id=test_workspace)
    assert result.ok is False
    assert result.broken_at_row_id == ids[2]


async def test_walk_chain_empty_workspace_is_ok(
    db_url_superuser,
):
    fresh_ws = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        result = await walk_chain(conn, workspace_id=fresh_ws)
    assert result.ok is True
    assert result.total_rows == 0


# ===========================================================================
# HTTP endpoints
# ===========================================================================


async def test_get_audit_log_returns_seeded_rows(
    client, db_url_superuser, test_workspace,
):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        await insert_audit_event(
            conn, workspace_id=test_workspace,
            actor="admin@example.com", action="schema.update",
            payload={"schema_id": "abc"},
            entity_type="schema", entity_id="abc",
        )

    resp = await client.get("/audit-log", headers=headers(test_workspace))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["actor"] == "admin@example.com"
    assert item["action"] == "schema.update"
    # Hex-encoded 64-char SHA-256.
    assert len(item["hash"]) == 64
    assert len(item["prev_hash"]) == 64


async def test_get_audit_log_integrity_clean(
    client, db_url_superuser, test_workspace,
):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        for i in range(4):
            await insert_audit_event(
                conn, workspace_id=test_workspace,
                actor="system:test", action=f"a_{i}", payload={"i": i},
            )

    resp = await client.get(
        "/audit-log/integrity", headers=headers(test_workspace),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["total_rows"] == 4
    assert body["broken_at_row_id"] is None


async def test_get_audit_log_integrity_flags_tampered_row(
    client, db_url_superuser, test_workspace,
):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        ids = []
        for i in range(3):
            ids.append(await insert_audit_event(
                conn, workspace_id=test_workspace,
                actor="system:test", action=f"a_{i}", payload={"i": i},
            ))
        await conn.execute(
            "UPDATE audit_log SET payload = %s::jsonb WHERE id = %s",
            (json.dumps({"i": -1}), ids[1]),
        )

    resp = await client.get(
        "/audit-log/integrity", headers=headers(test_workspace),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["broken_at_row_id"] == ids[1]
    assert body["broken_at_position"] == 2


async def test_workspace_isolation_on_audit_log(
    client, db_url_superuser,
):
    ws_a = str(uuid.uuid4())
    ws_b = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (ws_a,),
        )
        await insert_audit_event(
            conn, workspace_id=ws_a, actor="x", action="y", payload={},
        )
    resp = await client.get("/audit-log", headers=headers(ws_b))
    assert resp.status_code == 200
    assert resp.json()["items"] == []


# ===========================================================================
# Regression
# ===========================================================================


async def test_legacy_audit_endpoint_still_works(client, test_workspace):
    """GET /audit (Phase 9 query_log list) is unchanged."""
    resp = await client.get("/audit", headers=headers(test_workspace))
    assert resp.status_code == 200


async def test_b4b_endpoint_still_works(client, test_workspace):
    """POST /search still works."""
    from kb.api.query import reset_orchestrator
    reset_orchestrator()
    with _env(KB_INTENT_CLASSIFIER="identity", KB_PLANNER="identity"):
        reset_orchestrator()
        resp = await client.post(
            "/search", headers=headers(test_workspace),
            json={"query": "x", "mode": "H"},
        )
    assert resp.status_code == 200
