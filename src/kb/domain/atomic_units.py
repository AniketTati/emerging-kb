"""Phase 5c — atomic_units repo."""

from __future__ import annotations

import json
from typing import Any

from kb.db.pool import Connection


async def delete_atomic_units_for_file(
    conn: Connection, *, file_id: str
) -> int:
    cur = await conn.execute(
        "DELETE FROM atomic_units WHERE file_id = %s", (file_id,),
    )
    return cur.rowcount or 0


async def insert_atomic_unit(
    conn: Connection,
    *,
    file_id: str,
    workspace_id: str,
    unit_type: str,
    parameters: dict[str, Any],
    anchor_chunk_id: str | None,
    rarity_score: float | None,
    model_id: str,
    source_chunk_id: str | None = None,
    source_char_start: int | None = None,
    source_char_end: int | None = None,
) -> str:
    cur = await conn.execute(
        "INSERT INTO atomic_units "
        "(file_id, workspace_id, unit_type, parameters, anchor_chunk_id, "
        "rarity_score, model_id, "
        "source_chunk_id, source_char_start, source_char_end) "
        "VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s) "
        "RETURNING id::text",
        (
            file_id, workspace_id, unit_type, json.dumps(parameters),
            anchor_chunk_id, rarity_score, model_id,
            source_chunk_id, source_char_start, source_char_end,
        ),
    )
    return (await cur.fetchone())[0]


async def read_existing_unit_parameters(
    conn: Connection,
    *,
    workspace_id: str,
    unit_type: str,
) -> list[dict[str, Any]]:
    """Read all `parameters` dicts for (workspace, unit_type) — used as the
    historical corpus for anomaly centroid."""
    cur = await conn.execute(
        "SELECT parameters FROM atomic_units "
        "WHERE workspace_id = %s AND unit_type = %s",
        (workspace_id, unit_type),
    )
    rows = await cur.fetchall()
    return [r[0] for r in rows]


async def update_atomic_unit_rarity(
    conn: Connection, *, unit_id: str, rarity_score: float | None,
) -> None:
    await conn.execute(
        "UPDATE atomic_units SET rarity_score = %s WHERE id = %s",
        (rarity_score, unit_id),
    )


async def count_atomic_units_for_file(
    conn: Connection, *, file_id: str,
) -> int:
    cur = await conn.execute(
        "SELECT count(*) FROM atomic_units WHERE file_id = %s",
        (file_id,),
    )
    return (await cur.fetchone())[0]


async def read_atomic_units_for_file(
    conn: Connection, *, file_id: str,
) -> list[dict[str, Any]]:
    """Read every atomic_unit row for `file_id` with the full payload
    needed by the L4 extraction step to promote each unit into an
    extracted_entity child of the doc-root.

    Returns dicts (not tuples) so call-sites can keep using stable
    field names if the column set evolves.
    """
    cur = await conn.execute(
        "SELECT id::text, unit_type, parameters, anchor_chunk_id::text, "
        "       rarity_score, model_id "
        "  FROM atomic_units "
        " WHERE file_id = %s "
        " ORDER BY created_at, id",
        (file_id,),
    )
    rows = await cur.fetchall()
    return [
        {
            "id": r[0],
            "unit_type": r[1],
            "parameters": r[2] or {},
            "anchor_chunk_id": r[3],
            "rarity_score": float(r[4]) if r[4] is not None else None,
            "model_id": r[5],
        }
        for r in rows
    ]
