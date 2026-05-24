"""Contextual chunks domain — repo for the immutable contextual_chunks table.

Phase 3b. INSERTs go through `insert_contextual_chunk` (worker after the
`Contextualizer.contextualize()` batch returns). UPDATEs are not exposed
because the table is immutable at the DB layer (REVOKE UPDATE, DELETE).
"""

from __future__ import annotations

from kb.db.pool import Connection


async def insert_contextual_chunk(
    conn: Connection,
    *,
    chunk_id: str,
    file_id: str,
    workspace_id: str,
    contextual_prefix: str,
    contextual_text: str,
    model_id: str,
    prefix_token_count: int,
    cache_creation_input_tokens: int,
    cache_read_input_tokens: int,
) -> None:
    """INSERT one row. Idempotent on `(chunk_id)` UNIQUE via ON CONFLICT
    DO NOTHING — a replayed worker won't duplicate."""
    await conn.execute(
        "INSERT INTO contextual_chunks "
        "(chunk_id, file_id, workspace_id, contextual_prefix, contextual_text, "
        " model_id, prefix_token_count, cache_creation_input_tokens, "
        " cache_read_input_tokens) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (chunk_id) DO NOTHING",
        (
            chunk_id,
            file_id,
            workspace_id,
            contextual_prefix,
            contextual_text,
            model_id,
            prefix_token_count,
            cache_creation_input_tokens,
            cache_read_input_tokens,
        ),
    )


async def read_chunks_for_contextualization(
    conn: Connection, *, file_id: str
) -> list[tuple[str, str]]:
    """Return [(chunk_id, text), ...] for the file, ordered by chunk_index."""
    cur = await conn.execute(
        "SELECT id::text, text FROM chunks "
        "WHERE file_id = %s ORDER BY chunk_index ASC",
        (file_id,),
    )
    rows = await cur.fetchall()
    return [(str(r[0]), str(r[1])) for r in rows]


async def read_doc_text(conn: Connection, *, file_id: str) -> str:
    """Concatenate all raw_pages.text for the file (ordered) — used as the
    cached doc context for every chunk's contextualization call."""
    cur = await conn.execute(
        "SELECT text FROM raw_pages WHERE file_id = %s ORDER BY page_number ASC",
        (file_id,),
    )
    rows = await cur.fetchall()
    return "\n\n".join(str(r[0]) for r in rows)
