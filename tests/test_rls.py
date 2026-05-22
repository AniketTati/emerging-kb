"""Phase 0 — RLS isolation tests (build_tracker §5.1 decision #6, architecture §7).

RED at G3: imports point to modules that land at G4.

These tests connect as the non-superuser `kb_app` role. RLS only applies to
non-superuser roles, so testing under superuser would be a false positive.

Spec: tests/specs/phase_0.md §4.4.
"""

from __future__ import annotations

import uuid

import pytest


pytestmark = pytest.mark.asyncio


WS_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
WS_B = uuid.UUID("22222222-2222-2222-2222-222222222222")


async def test_audit_log_has_rls_enabled(db_superuser):
    """ENABLE ROW LEVEL SECURITY landed on audit_log."""
    row = await db_superuser.fetchrow(
        "SELECT relrowsecurity FROM pg_class WHERE relname = 'audit_log'"
    )
    assert row[0] is True


async def test_idempotency_keys_has_rls_enabled(db_superuser):
    """ENABLE ROW LEVEL SECURITY landed on idempotency_keys."""
    row = await db_superuser.fetchrow(
        "SELECT relrowsecurity FROM pg_class WHERE relname = 'idempotency_keys'"
    )
    assert row[0] is True


async def test_schema_migrations_has_no_rls(db_superuser):
    """schema_migrations is infrastructure; no workspace scope, no RLS."""
    row = await db_superuser.fetchrow(
        "SELECT relrowsecurity FROM pg_class WHERE relname = 'schema_migrations'"
    )
    assert row[0] is False


async def test_audit_log_isolated_across_workspaces(db_url_kb_app):
    """Insert under ws_A and ws_B in separate sessions; each sees only its own."""
    from kb.db.pool import open_connection  # G4

    # Write as workspace A.
    async with open_connection(db_url_kb_app) as conn_a:
        async with conn_a.transaction():
            await conn_a.execute("SET LOCAL app.workspace_id = %s", (str(WS_A),))
            await conn_a.execute(
                "INSERT INTO audit_log (workspace_id, actor, action, payload) "
                "VALUES (%s, 'test', 'test.action', '{}'::jsonb)",
                (str(WS_A),),
            )

    # Write as workspace B.
    async with open_connection(db_url_kb_app) as conn_b:
        async with conn_b.transaction():
            await conn_b.execute("SET LOCAL app.workspace_id = %s", (str(WS_B),))
            await conn_b.execute(
                "INSERT INTO audit_log (workspace_id, actor, action, payload) "
                "VALUES (%s, 'test', 'test.action', '{}'::jsonb)",
                (str(WS_B),),
            )

    # Read as workspace A — should see only A's row.
    async with open_connection(db_url_kb_app) as conn_a:
        await conn_a.execute("SET LOCAL app.workspace_id = %s", (str(WS_A),))
        rows = await conn_a.fetch("SELECT workspace_id FROM audit_log")
        assert all(r[0] == WS_A for r in rows), "workspace B's row leaked"


async def test_idempotency_keys_isolated_across_workspaces(db_url_kb_app):
    """Same isolation guarantee for idempotency_keys."""
    from kb.db.pool import open_connection  # G4

    async with open_connection(db_url_kb_app) as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL app.workspace_id = %s", (str(WS_A),))
            await conn.execute(
                "INSERT INTO idempotency_keys (workspace_id, key, response, status_code) "
                "VALUES (%s, 'k', '{}'::jsonb, 200)",
                (str(WS_A),),
            )

    async with open_connection(db_url_kb_app) as conn:
        await conn.execute("SET LOCAL app.workspace_id = %s", (str(WS_B),))
        rows = await conn.fetch("SELECT key FROM idempotency_keys")
        assert rows == [], "workspace A's idempotency key leaked into B's view"


async def test_no_workspace_context_means_no_rows(db_url_kb_app, db_superuser):
    """Connect as kb_app without SET LOCAL app.workspace_id → SELECT fails or returns 0."""
    from kb.db.pool import open_connection  # G4

    # Seed one row as superuser (RLS-bypassing).
    await db_superuser.execute(
        "INSERT INTO audit_log (workspace_id, actor, action, payload) "
        "VALUES (%s, 'test', 'test.action', '{}'::jsonb)",
        (str(WS_A),),
    )

    async with open_connection(db_url_kb_app) as conn:
        # No SET LOCAL — app.workspace_id resolves to NULL or errors.
        # Either is acceptable; what's NOT acceptable is "returns A's row".
        try:
            rows = await conn.fetch("SELECT 1 FROM audit_log")
        except Exception:
            # Cast error or undefined GUC error — both fine.
            return
        assert rows == [], "RLS leaked: kb_app saw rows without workspace context set"


async def test_superuser_bypasses_rls(db_superuser):
    """Superuser sees all rows regardless of app.workspace_id."""
    # Seed two workspaces' worth of data.
    await db_superuser.execute(
        "INSERT INTO audit_log (workspace_id, actor, action, payload) VALUES "
        "(%s, 'test', 'a', '{}'::jsonb), (%s, 'test', 'b', '{}'::jsonb)",
        (str(WS_A), str(WS_B)),
    )
    rows = await db_superuser.fetch("SELECT DISTINCT workspace_id FROM audit_log")
    assert {r[0] for r in rows} >= {WS_A, WS_B}


async def test_dropping_workspace_filter_does_not_leak(db_url_kb_app):
    """Query without explicit WHERE workspace_id=... still only returns own rows."""
    from kb.db.pool import open_connection  # G4

    async with open_connection(db_url_kb_app) as conn:
        await conn.execute("SET LOCAL app.workspace_id = %s", (str(WS_A),))
        # Intentionally no WHERE clause — RLS policy must filter.
        rows = await conn.fetch("SELECT workspace_id FROM audit_log")
        assert all(r[0] == WS_A for r in rows)
