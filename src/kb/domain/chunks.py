"""Chunks domain — repo for the immutable chunks table.

Phase 3a. INSERTs go through `insert_chunk` (called by the worker after
`kb.chunking.chunk_pages()` returns). UPDATEs are not exposed because the
table is immutable at the DB layer (REVOKE UPDATE, DELETE from kb_app).
"""

from __future__ import annotations

from pydantic import BaseModel

from kb.db.pool import Connection


class ChunkSummary(BaseModel):
    """Doc-detail row shape — enough for the chunks-panel tree without
    streaming every byte of `text`. The text preview is truncated to
    keep the response small on docs with hundreds of chunks; full text
    stays accessible via the chat retrieval surfaces.
    """

    id: str
    chunk_index: int
    node_level: int            # 0=leaf, 1=mid, 2=root (per migration 0040)
    parent_chunk_id: str | None
    token_count: int
    source_page_numbers: list[int]
    text_preview: str          # first ~200 chars
    text_length: int           # full length so the UI can show "... +N chars"


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
    node_level: int = 0,
    parent_chunk_id: str | None = None,
) -> str | None:
    """INSERT one row. Idempotent on `(file_id, chunk_index)` UNIQUE via
    ON CONFLICT DO NOTHING — a replayed worker won't duplicate.

    Hierarchical-chunking extension (migration 0040):
      * `node_level` — 0=leaf, 1=mid, 2=root. Default 0 = backwards-
        compatible flat behavior.
      * `parent_chunk_id` — FK to another chunks row. Required for
        non-root rows; None for roots.

    Returns the inserted row's id as a string, or None when the INSERT
    was a no-op (already-existing row picked up by ON CONFLICT). The
    new id is needed by the chunker worker to resolve children's
    `parent_chunk_id` linkage.
    """
    cur = await conn.execute(
        "INSERT INTO chunks "
        "(file_id, workspace_id, chunk_index, text, source_page_numbers, "
        " token_count, content_sha, node_level, parent_chunk_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (file_id, chunk_index) DO NOTHING "
        "RETURNING id::text",
        (
            file_id,
            workspace_id,
            chunk_index,
            text,
            source_page_numbers,
            token_count,
            content_sha,
            node_level,
            parent_chunk_id,
        ),
    )
    row = await cur.fetchone()
    return row[0] if row else None


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


async def list_chunks_for_file(
    conn: Connection, *, file_id: str, preview_chars: int = 200,
) -> list[ChunkSummary]:
    """Return all chunks for a file as `ChunkSummary` rows, ordered so a
    UI tree can render in one pass: by `node_level DESC` (roots first,
    leaves last) then `chunk_index ASC` within each level.

    No pagination — even our largest demo doc has < 200 chunks across
    all 3 levels. If we ever ingest a 5000-page contract, swap to
    cursor-paginated traversal keyed on `(parent_chunk_id, chunk_index)`.

    Text is truncated to `preview_chars` (default 200) to keep the
    response light; `text_length` carries the full length so the UI
    can render a "... +1234 chars" affordance.
    """
    cur = await conn.execute(
        "SELECT id::text, chunk_index, node_level, parent_chunk_id::text, "
        "       token_count, source_page_numbers, text "
        "FROM chunks "
        "WHERE file_id = %s "
        "ORDER BY node_level DESC, chunk_index ASC",
        (file_id,),
    )
    out: list[ChunkSummary] = []
    for row in await cur.fetchall():
        text = str(row[6] or "")
        preview = text[:preview_chars]
        out.append(
            ChunkSummary(
                id=str(row[0]),
                chunk_index=int(row[1]),
                node_level=int(row[2] or 0),
                parent_chunk_id=row[3],
                token_count=int(row[4] or 0),
                source_page_numbers=list(row[5] or []),
                text_preview=preview,
                text_length=len(text),
            )
        )
    return out
