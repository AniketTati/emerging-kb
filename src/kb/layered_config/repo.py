"""Layers 1-4 — runtime overrides stored in `config_overrides`.

Workspace-scoped + RLS-applicable (the table policy is set in migration
0020). Callers must hold an `app.workspace_id` GUC, set via the
WorkspaceMiddleware on the API side or `SET LOCAL` in workers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from kb.db.pool import Connection


# Migration-side check accepts these four. Mirror in code for early error.
ALLOWED_SCOPE_KINDS: tuple[str, ...] = ("user", "doc", "doc_type", "workspace")


@dataclass(frozen=True)
class OverrideRecord:
    id: str
    workspace_id: str
    scope_kind: str
    scope_id: str
    config_key: str
    config_value: Any
    reason: str | None
    set_by: str | None
    set_at: str   # iso8601
    active: bool


def _row_to_record(row: tuple) -> OverrideRecord:
    return OverrideRecord(
        id=str(row[0]),
        workspace_id=str(row[1]),
        scope_kind=str(row[2]),
        scope_id=str(row[3]),
        config_key=str(row[4]),
        config_value=row[5],
        reason=row[6],
        set_by=row[7],
        set_at=row[8].isoformat() if hasattr(row[8], "isoformat") else str(row[8]),
        active=bool(row[9]),
    )


async def read_override(
    conn: Connection,
    *,
    workspace_id: str,
    scope_kind: str,
    scope_id: str,
    config_key: str,
) -> OverrideRecord | None:
    """Single-row lookup for the resolver's hot path."""
    cur = await conn.execute(
        """
        SELECT id, workspace_id, scope_kind, scope_id, config_key,
               config_value, reason, set_by, set_at, active
          FROM config_overrides
         WHERE workspace_id = %s
           AND scope_kind   = %s
           AND scope_id     = %s
           AND config_key   = %s
           AND active       = true
         LIMIT 1
        """,
        (workspace_id, scope_kind, scope_id, config_key),
    )
    row = await cur.fetchone()
    return _row_to_record(row) if row else None


async def read_workspace_overrides(
    conn: Connection,
    *,
    workspace_id: str,
) -> list[OverrideRecord]:
    """All active overrides for a workspace. Used by the Effective Config UI."""
    cur = await conn.execute(
        """
        SELECT id, workspace_id, scope_kind, scope_id, config_key,
               config_value, reason, set_by, set_at, active
          FROM config_overrides
         WHERE workspace_id = %s
           AND active       = true
         ORDER BY scope_kind, scope_id, config_key
        """,
        (workspace_id,),
    )
    rows = await cur.fetchall()
    return [_row_to_record(r) for r in rows]


async def insert_override(
    conn: Connection,
    *,
    workspace_id: str,
    scope_kind: str,
    scope_id: str,
    config_key: str,
    config_value: Any,
    reason: str | None = None,
    set_by: str | None = None,
) -> str:
    """Insert a new override row OR re-activate an existing inactive one.

    The unique partial index `(workspace_id, scope_kind, scope_id,
    config_key) WHERE active = true` means we can have at most one active
    row per key — so before inserting we deactivate any existing active
    entry (preserves history) then insert the new one.

    Returns the id of the new row.
    """
    if scope_kind not in ALLOWED_SCOPE_KINDS:
        raise ValueError(
            f"scope_kind={scope_kind!r} not in {ALLOWED_SCOPE_KINDS}"
        )

    # 1) deactivate any current active row.
    await conn.execute(
        """
        UPDATE config_overrides
           SET active = false,
               set_at = NOW()
         WHERE workspace_id = %s
           AND scope_kind   = %s
           AND scope_id     = %s
           AND config_key   = %s
           AND active       = true
        """,
        (workspace_id, scope_kind, scope_id, config_key),
    )

    # 2) insert the new active row.
    cur = await conn.execute(
        """
        INSERT INTO config_overrides (
            workspace_id, scope_kind, scope_id, config_key,
            config_value, reason, set_by, active
        ) VALUES (
            %s, %s, %s, %s, %s::jsonb, %s, %s, true
        )
        RETURNING id::text
        """,
        (workspace_id, scope_kind, scope_id, config_key,
         json.dumps(config_value), reason, set_by),
    )
    row = await cur.fetchone()
    assert row is not None
    return str(row[0])


async def revoke_override(
    conn: Connection,
    *,
    workspace_id: str,
    scope_kind: str,
    scope_id: str,
    config_key: str,
) -> bool:
    """Soft-revoke: set active=false. Returns True if a row was deactivated."""
    cur = await conn.execute(
        """
        UPDATE config_overrides
           SET active = false,
               set_at = NOW()
         WHERE workspace_id = %s
           AND scope_kind   = %s
           AND scope_id     = %s
           AND config_key   = %s
           AND active       = true
        """,
        (workspace_id, scope_kind, scope_id, config_key),
    )
    # rowcount semantics — psycopg's AsyncCursor exposes .rowcount.
    return getattr(cur, "rowcount", 0) > 0
