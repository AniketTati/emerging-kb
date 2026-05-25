"""B2 / WA-6 — fact_conflicts + files.source_authority + doc_status repo.

Contracts:

  - apply_source_authority_from_config(file_id, inferred_doc_type, conn)
      Reads config/doc_types/<type>.yaml via WA-1's resolve_config
      (or falls back to defaults.yaml's source_authority.defaults),
      UPDATEs files.source_authority + source_authority_reason.

  - set_doc_status(file_id, new_status, ...) — admin override.

  - set_source_authority_override(...) — admin override.

  - insert_conflict / read_conflicts_for_workspace /
    read_conflicts_for_entity / mark_conflict_resolved — fact_conflicts
    CRUD consumed by WA-14 dashboard + Doc Detail panel.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from kb.db.pool import Connection
from kb.layered_config import resolve_config
from kb.layered_config.resolver import ConfigKeyNotFoundError


DOC_STATUSES: tuple[str, ...] = (
    "live", "superseded", "draft", "archived", "retracted",
)
RESOLUTIONS: tuple[str, ...] = (
    "chain", "status", "authority", "recency", "unresolved", "user",
)


# ---------------------------------------------------------------------------
# files.source_authority + doc_status
# ---------------------------------------------------------------------------


async def apply_source_authority_from_config(
    conn: Connection,
    *,
    file_id: str,
    workspace_id: str,
    inferred_doc_type: str | None,
) -> tuple[float, str | None]:
    """Look up the file's doc-type authority from config (WA-1) and apply
    to files.source_authority + files.source_authority_reason. Returns
    the (authority, reason) tuple that was applied.

    Lookup order (per Design 2 §"How authority is assigned"):
      1. config/doc_types/<inferred_doc_type>.yaml `authority` key
      2. config/defaults.yaml source_authority.defaults.<heuristic>
      3. defaults.yaml source_authority.unknown_default (0.5)
    """
    authority: float = 0.5
    reason: str | None = None

    if inferred_doc_type:
        try:
            authority = float(await resolve_config(
                "authority",
                workspace_id=workspace_id,
                conn=conn,
                doc_type=inferred_doc_type,
            ))
            try:
                reason = await resolve_config(
                    "authority_reason",
                    workspace_id=workspace_id,
                    conn=conn,
                    doc_type=inferred_doc_type,
                )
            except ConfigKeyNotFoundError:
                reason = f"per config/doc_types/{inferred_doc_type}.yaml"
        except ConfigKeyNotFoundError:
            # Fall through to heuristic defaults table.
            try:
                authority = float(await resolve_config(
                    f"source_authority.defaults.{inferred_doc_type}",
                    workspace_id=workspace_id,
                    conn=conn,
                ))
                reason = f"heuristic default for doc_type={inferred_doc_type!r}"
            except ConfigKeyNotFoundError:
                pass

    if reason is None:
        # Final fallback — 0.5 "authority not assessed" (Design 2 failure mode).
        try:
            authority = float(await resolve_config(
                "source_authority.unknown_default",
                workspace_id=workspace_id,
                conn=conn,
                default=0.5,
            ))
        except (ConfigKeyNotFoundError, TypeError, ValueError):
            authority = 0.5
        reason = "authority not assessed (no doc-type classification)"

    await conn.execute(
        "UPDATE files SET source_authority = %s, source_authority_reason = %s "
        "WHERE id = %s",
        (authority, reason, file_id),
    )
    return authority, reason


async def set_source_authority_override(
    conn: Connection,
    *,
    file_id: str,
    authority: float,
    reason: str,
) -> bool:
    """Admin override from the Doc Detail panel."""
    if not (0.0 <= authority <= 1.0):
        raise ValueError(f"authority must be in [0,1], got {authority}")
    cur = await conn.execute(
        "UPDATE files SET source_authority = %s, source_authority_reason = %s "
        "WHERE id = %s",
        (authority, reason, file_id),
    )
    return getattr(cur, "rowcount", 0) > 0


async def set_doc_status(
    conn: Connection, *, file_id: str, new_status: str,
) -> bool:
    if new_status not in DOC_STATUSES:
        raise ValueError(f"new_status must be one of {DOC_STATUSES}, got {new_status!r}")
    cur = await conn.execute(
        "UPDATE files SET doc_status = %s WHERE id = %s",
        (new_status, file_id),
    )
    return getattr(cur, "rowcount", 0) > 0


async def read_file_authority(
    conn: Connection, *, file_id: str,
) -> tuple[float, str | None, str] | None:
    """Returns (authority, reason, doc_status) for a file or None."""
    cur = await conn.execute(
        "SELECT source_authority, source_authority_reason, doc_status "
        "FROM files WHERE id = %s",
        (file_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return (float(row[0]), row[1], str(row[2]))


# ---------------------------------------------------------------------------
# fact_conflicts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConflictRecord:
    id: str
    workspace_id: str
    entity_id: str
    predicate: str
    observed_at: str
    evidence: list[dict]
    resolution: str
    resolved_value: str | None
    resolved_doc_id: str | None
    notes: str | None
    resolved_by: str | None
    resolved_at: str | None


_CONFLICT_COLS = (
    "id, workspace_id, entity_id, predicate, observed_at, evidence, "
    "resolution, resolved_value, resolved_doc_id, notes, resolved_by, resolved_at"
)


def _conflict_row(row: tuple) -> ConflictRecord:
    return ConflictRecord(
        id=str(row[0]),
        workspace_id=str(row[1]),
        entity_id=str(row[2]),
        predicate=str(row[3]),
        observed_at=row[4].isoformat() if hasattr(row[4], "isoformat") else str(row[4]),
        evidence=list(row[5] or []),
        resolution=str(row[6]),
        resolved_value=row[7],
        resolved_doc_id=str(row[8]) if row[8] is not None else None,
        notes=row[9],
        resolved_by=row[10],
        resolved_at=(
            row[11].isoformat() if hasattr(row[11], "isoformat") else
            (str(row[11]) if row[11] is not None else None)
        ),
    )


async def insert_conflict(
    conn: Connection,
    *,
    workspace_id: str,
    entity_id: str,
    predicate: str,
    evidence: list[dict],
    resolution: str = "unresolved",
    resolved_value: str | None = None,
    resolved_doc_id: str | None = None,
    notes: str | None = None,
) -> str:
    if resolution not in RESOLUTIONS:
        raise ValueError(f"resolution must be one of {RESOLUTIONS}, got {resolution!r}")
    cur = await conn.execute(
        """
        INSERT INTO fact_conflicts (
            workspace_id, entity_id, predicate, evidence,
            resolution, resolved_value, resolved_doc_id, notes
        ) VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, %s)
        RETURNING id::text
        """,
        (
            workspace_id, entity_id, predicate, json.dumps(evidence),
            resolution, resolved_value, resolved_doc_id, notes,
        ),
    )
    row = await cur.fetchone()
    assert row is not None
    return str(row[0])


async def read_conflicts_for_workspace(
    conn: Connection,
    *,
    workspace_id: str,
    resolution: str | None = None,  # filter; None = all
    limit: int = 200,
) -> list[ConflictRecord]:
    if resolution is None:
        cur = await conn.execute(
            f"SELECT {_CONFLICT_COLS} FROM fact_conflicts "
            "WHERE workspace_id = %s ORDER BY observed_at DESC LIMIT %s",
            (workspace_id, limit),
        )
    else:
        if resolution not in RESOLUTIONS:
            raise ValueError(f"resolution filter must be one of {RESOLUTIONS}")
        cur = await conn.execute(
            f"SELECT {_CONFLICT_COLS} FROM fact_conflicts "
            "WHERE workspace_id = %s AND resolution = %s "
            "ORDER BY observed_at DESC LIMIT %s",
            (workspace_id, resolution, limit),
        )
    return [_conflict_row(r) for r in await cur.fetchall()]


async def read_conflicts_for_entity(
    conn: Connection, *, workspace_id: str, entity_id: str,
) -> list[ConflictRecord]:
    cur = await conn.execute(
        f"SELECT {_CONFLICT_COLS} FROM fact_conflicts "
        "WHERE workspace_id = %s AND entity_id = %s "
        "ORDER BY observed_at DESC",
        (workspace_id, entity_id),
    )
    return [_conflict_row(r) for r in await cur.fetchall()]


async def read_conflict_by_id(
    conn: Connection, *, conflict_id: str,
) -> ConflictRecord | None:
    cur = await conn.execute(
        f"SELECT {_CONFLICT_COLS} FROM fact_conflicts WHERE id = %s",
        (conflict_id,),
    )
    row = await cur.fetchone()
    return _conflict_row(row) if row else None


async def mark_conflict_resolved(
    conn: Connection,
    *,
    conflict_id: str,
    resolution: str,
    resolved_value: str | None,
    resolved_doc_id: str | None,
    resolved_by: str | None = None,
    notes: str | None = None,
) -> bool:
    if resolution not in RESOLUTIONS:
        raise ValueError(f"resolution must be one of {RESOLUTIONS}, got {resolution!r}")
    cur = await conn.execute(
        """
        UPDATE fact_conflicts SET
            resolution = %s,
            resolved_value = %s,
            resolved_doc_id = %s,
            resolved_by = %s,
            notes = COALESCE(%s, notes),
            resolved_at = NOW()
        WHERE id = %s
        """,
        (resolution, resolved_value, resolved_doc_id, resolved_by, notes, conflict_id),
    )
    return getattr(cur, "rowcount", 0) > 0
