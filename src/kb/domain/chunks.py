"""Chunks domain — repo for the immutable chunks table.

Phase 3a. INSERTs go through `insert_chunk` (called by the worker after
`kb.chunking.chunk_pages()` returns). UPDATEs are not exposed because the
table is immutable at the DB layer (REVOKE UPDATE, DELETE from kb_app).
"""

from __future__ import annotations

from kb.db.pool import Connection


async def insert_chunk(
    conn: Connection,
    *,
    file_id: str,
    workspace_id: str,
    chunk_index: int,
    text: str,
    source_page_numbers: list[int],
    token_count: int,
    content_sha: str,
) -> None:
    """INSERT one row. Idempotent on `(file_id, chunk_index)` UNIQUE via
    ON CONFLICT DO NOTHING — a replayed worker won't duplicate."""
    await conn.execute(
        "INSERT INTO chunks "
        "(file_id, workspace_id, chunk_index, text, source_page_numbers, "
        " token_count, content_sha) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (file_id, chunk_index) DO NOTHING",
        (
            file_id,
            workspace_id,
            chunk_index,
            text,
            source_page_numbers,
            token_count,
            content_sha,
        ),
    )


async def count_chunks_for_file(conn: Connection, *, file_id: str) -> int:
    cur = await conn.execute(
        "SELECT count(*) FROM chunks WHERE file_id = %s", (file_id,)
    )
    row = await cur.fetchone()
    return int(row[0]) if row else 0


async def read_pages_for_chunking(
    conn: Connection, *, file_id: str
) -> list[tuple[int, str]]:
    """Return [(page_number, text), ...] for the file, ordered by page_number."""
    cur = await conn.execute(
        "SELECT page_number, text FROM raw_pages "
        "WHERE file_id = %s ORDER BY page_number ASC",
        (file_id,),
    )
    rows = await cur.fetchall()
    return [(int(r[0]), str(r[1])) for r in rows]
