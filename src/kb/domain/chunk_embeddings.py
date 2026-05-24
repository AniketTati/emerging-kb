"""Chunk embeddings domain — repo for the immutable chunk_embeddings table.

Phase 3c. INSERTs go through `insert_chunk_embedding` (worker after the
`Embedder.embed_batch()` batch returns). UPDATEs are not exposed because the
table is immutable at the DB layer (REVOKE UPDATE, DELETE).
"""

from __future__ import annotations

from kb.db.pool import Connection


async def insert_chunk_embedding(
    conn: Connection,
    *,
    contextual_chunk_id: str,
    file_id: str,
    workspace_id: str,
    vector: list[float],
    model_id: str,
) -> None:
    """INSERT one row. Idempotent on `(contextual_chunk_id, model_id)` UNIQUE
    via ON CONFLICT DO NOTHING — a replayed worker won't duplicate."""
    # pgvector accepts a bracketed-list string literal for halfvec.
    vec_literal = "[" + ",".join(repr(float(v)) for v in vector) + "]"
    await conn.execute(
        "INSERT INTO chunk_embeddings "
        "(contextual_chunk_id, file_id, workspace_id, embedding, model_id) "
        "VALUES (%s, %s, %s, %s::halfvec, %s) "
        "ON CONFLICT (contextual_chunk_id, model_id) DO NOTHING",
        (
            contextual_chunk_id,
            file_id,
            workspace_id,
            vec_literal,
            model_id,
        ),
    )


async def read_contextual_chunks_for_embedding(
    conn: Connection, *, file_id: str
) -> list[tuple[str, str]]:
    """Return [(contextual_chunk_id, contextual_text), ...] for the file."""
    cur = await conn.execute(
        "SELECT id::text, contextual_text FROM contextual_chunks "
        "WHERE file_id = %s ORDER BY id ASC",
        (file_id,),
    )
    rows = await cur.fetchall()
    return [(str(r[0]), str(r[1])) for r in rows]
