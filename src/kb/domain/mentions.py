"""Phase 5a — extracted_mentions repo.

Tiny domain layer: read contextual_chunks for a file (to feed the extractor),
DELETE-then-INSERT mentions (idempotent re-extract per §5.12.1 #8).
"""

from __future__ import annotations

from typing import Any

from kb.db.pool import Connection


async def read_contextual_chunks_for_file(
    conn: Connection,
    *,
    file_id: str,
) -> list[tuple[str, str]]:
    """Return [(contextual_chunk_id, contextual_text), ...] for the file
    ordered by chunk_index (so output is deterministic across runs).

    Joins chunks → contextual_chunks; assumes RLS already applied via
    the worker's `SET LOCAL app.workspace_id`.
    """
    cur = await conn.execute(
        "SELECT cc.id::text, cc.contextual_text "
        "FROM contextual_chunks cc "
        "JOIN chunks c ON c.id = cc.chunk_id "
        "WHERE cc.file_id = %s "
        "ORDER BY c.chunk_index ASC",
        (file_id,),
    )
    rows = await cur.fetchall()
    return [(row[0], row[1]) for row in rows]


async def delete_mentions_for_file(
    conn: Connection,
    *,
    file_id: str,
) -> int:
    """DELETE all extracted_mentions for a file (idempotent re-extract).
    Returns the rowcount."""
    cur = await conn.execute(
        "DELETE FROM extracted_mentions WHERE file_id = %s",
        (file_id,),
    )
    return cur.rowcount or 0


async def insert_mention(
    conn: Connection,
    *,
    contextual_chunk_id: str,
    file_id: str,
    workspace_id: str,
    mention_text: str,
    mention_type: str,
    start_offset: int | None,
    end_offset: int | None,
    confidence: float | None,
    model_id: str,
) -> str:
    """INSERT one extracted_mentions row. Returns the new row's id."""
    cur = await conn.execute(
        "INSERT INTO extracted_mentions "
        "(contextual_chunk_id, file_id, workspace_id, mention_text, "
        "mention_type, start_offset, end_offset, confidence, model_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "RETURNING id::text",
        (
            contextual_chunk_id, file_id, workspace_id, mention_text,
            mention_type, start_offset, end_offset, confidence, model_id,
        ),
    )
    row = await cur.fetchone()
    return row[0]


async def count_mentions_for_file(conn: Connection, *, file_id: str) -> int:
    cur = await conn.execute(
        "SELECT count(*) FROM extracted_mentions WHERE file_id = %s",
        (file_id,),
    )
    row = await cur.fetchone()
    return row[0] if row else 0
