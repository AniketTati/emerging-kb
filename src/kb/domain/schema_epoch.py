"""T2 (§6.1 / A10) — per-workspace schema epoch read + bump.

A monotonically-increasing token bumped on ANY schema change:
  - T1 corpus convergence (canonical key rewrites — `workers.tasks`), and
  - T1 manual display-rename (a presentation pointer — `api.schemas`).

The live-schema cache (`kb.domain.structured_schema`) is keyed on this epoch,
so a cached `LiveSchema` / `field_display_map` is invalidated the instant the
schema moves. This closes the v1 bug where a label-only rename never bumped
`schema_version`, leaving a freshly renamed field invisible to the planner.

Both helpers are best-effort by construction: `read_schema_epoch` returns 0 on
any miss/error (a cold workspace with no row reads as epoch 0), and
`bump_schema_epoch` swallows its own failure (a bump that can't write must
never break convergence or a rename API call — the worst case is a slightly
stale cache for one TTL, not a 5xx). The table is created in migration 0054.
"""

from __future__ import annotations

from typing import Any


async def read_schema_epoch(conn: Any, *, workspace_id: str) -> int:
    """Return the current schema epoch for `workspace_id` (0 if no row yet
    or on any error — a cold workspace has never converged/renamed)."""
    if conn is None:
        return 0
    # SAVEPOINT-guarded so a failure (e.g. txn already aborted upstream) leaves
    # the shared request txn usable and just reads as epoch 0.
    try:
        await conn.execute("SAVEPOINT read_schema_epoch")
    except Exception:  # noqa: BLE001
        return 0
    try:
        cur = await conn.execute(
            "SELECT epoch FROM workspace_schema_epoch WHERE workspace_id = %s",
            (workspace_id,),
        )
        row = await cur.fetchone()
        try:
            await conn.execute("RELEASE SAVEPOINT read_schema_epoch")
        except Exception:  # noqa: BLE001
            pass
        return int(row[0]) if row and row[0] is not None else 0
    except Exception:  # noqa: BLE001
        try:
            await conn.execute("ROLLBACK TO SAVEPOINT read_schema_epoch")
            await conn.execute("RELEASE SAVEPOINT read_schema_epoch")
        except Exception:  # noqa: BLE001
            pass
        return 0


async def bump_schema_epoch(conn: Any, *, workspace_id: str) -> int | None:
    """Increment (or initialize at 1) the workspace's schema epoch. Returns
    the new epoch, or None on any failure.

    Best-effort: a bump failure must never abort the caller (convergence /
    rename). The caller runs inside its own transaction; we wrap the write in
    a SAVEPOINT so a failure here (e.g. a contended row, an aborted outer txn)
    rolls back cleanly to a usable state instead of poisoning it.
    """
    if conn is None:
        return None
    sp_open = False
    try:
        await conn.execute("SAVEPOINT bump_schema_epoch")
        sp_open = True
    except Exception:  # noqa: BLE001
        # Outer txn already aborted — nothing safe to do.
        return None
    try:
        cur = await conn.execute(
            "INSERT INTO workspace_schema_epoch (workspace_id, epoch, updated_at) "
            "VALUES (%s, 1, now()) "
            "ON CONFLICT (workspace_id) DO UPDATE SET "
            "  epoch = workspace_schema_epoch.epoch + 1, updated_at = now() "
            "RETURNING epoch",
            (workspace_id,),
        )
        row = await cur.fetchone()
        try:
            await conn.execute("RELEASE SAVEPOINT bump_schema_epoch")
        except Exception:  # noqa: BLE001
            pass
        return int(row[0]) if row else None
    except Exception:  # noqa: BLE001
        if sp_open:
            try:
                await conn.execute("ROLLBACK TO SAVEPOINT bump_schema_epoch")
                await conn.execute("RELEASE SAVEPOINT bump_schema_epoch")
            except Exception:  # noqa: BLE001
                pass
        return None
