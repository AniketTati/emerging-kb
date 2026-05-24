"""RAPTOR nodes + edges domain — repo for the immutable raptor_nodes /
raptor_edges tables.

Phase 3d. Writes only INSERT (tables are immutable at the DB layer via REVOKE
UPDATE, DELETE on kb_app per §5.10 decision #11).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from kb.db.pool import Connection


class RaptorNode(BaseModel):
    """One raptor_nodes row. L1 leaves are NOT stored here — they live in
    contextual_chunks per §5.10 decision #9."""

    id: str
    scope: str  # 'per_doc' | 'corpus'
    file_id: str | None  # NULL for scope='corpus'
    workspace_id: str
    level: int  # 2..6
    text: str
    embedding: list[float]
    token_count: int | None
    cluster_id_in_level: int
    summarizer_model_id: str
    embedder_model_id: str


async def insert_raptor_node(
    conn: Connection,
    *,
    scope: str,
    file_id: str | None,
    workspace_id: str,
    level: int,
    text: str,
    vector: list[float],
    cluster_id_in_level: int,
    summarizer_model_id: str,
    embedder_model_id: str,
    token_count: int | None = None,
) -> str:
    """INSERT one raptor_nodes row. Returns the new row's id.

    Idempotent on `(scope, file_id, level, cluster_id_in_level)` UNIQUE via
    ON CONFLICT DO NOTHING — a replayed worker won't duplicate. When the
    INSERT collides, returns the existing id (RETURNING is empty on
    DO NOTHING, so we re-fetch by the unique key).
    """
    vec_literal = "[" + ",".join(repr(float(v)) for v in vector) + "]"
    cur = await conn.execute(
        "INSERT INTO raptor_nodes "
        "(scope, file_id, workspace_id, level, text, embedding, "
        " cluster_id_in_level, summarizer_model_id, embedder_model_id, token_count) "
        "VALUES (%s, %s, %s, %s, %s, %s::halfvec, %s, %s, %s, %s) "
        "ON CONFLICT (scope, file_id, level, cluster_id_in_level) DO NOTHING "
        "RETURNING id::text",
        (
            scope, file_id, workspace_id, level, text, vec_literal,
            cluster_id_in_level, summarizer_model_id, embedder_model_id, token_count,
        ),
    )
    row = await cur.fetchone()
    if row is not None:
        return row[0]
    # Conflict — re-fetch the existing id.
    cur = await conn.execute(
        "SELECT id::text FROM raptor_nodes "
        "WHERE scope = %s AND level = %s AND cluster_id_in_level = %s "
        "  AND ((file_id IS NULL AND %s::uuid IS NULL) OR file_id = %s::uuid)",
        (scope, level, cluster_id_in_level, file_id, file_id),
    )
    existing = await cur.fetchone()
    if existing is None:
        raise RuntimeError(
            f"insert_raptor_node: INSERT conflicted but lookup found no row "
            f"(scope={scope}, file_id={file_id}, level={level}, "
            f"cluster_id_in_level={cluster_id_in_level})"
        )
    return existing[0]


async def insert_raptor_edge(
    conn: Connection,
    *,
    parent_node_id: str,
    workspace_id: str,
    child_node_id: str | None = None,
    child_contextual_chunk_id: str | None = None,
) -> None:
    """INSERT one raptor_edges row. EXACTLY ONE of `child_node_id` or
    `child_contextual_chunk_id` must be set (enforced by the DB row CHECK
    `raptor_edges_exactly_one_child`).

    Idempotent on the partial UNIQUE indexes per child kind via
    ON CONFLICT DO NOTHING — a replayed worker won't duplicate.
    """
    if (child_node_id is None) == (child_contextual_chunk_id is None):
        raise ValueError(
            "insert_raptor_edge: exactly one of child_node_id or "
            "child_contextual_chunk_id must be set"
        )

    # Which partial UNIQUE index we hit depends on which child column is set.
    if child_node_id is not None:
        # Use the (parent_node_id, child_node_id) partial UNIQUE index.
        await conn.execute(
            "INSERT INTO raptor_edges "
            "(parent_node_id, child_node_id, workspace_id) "
            "VALUES (%s, %s, %s) "
            "ON CONFLICT (parent_node_id, child_node_id) "
            "  WHERE child_node_id IS NOT NULL DO NOTHING",
            (parent_node_id, child_node_id, workspace_id),
        )
    else:
        await conn.execute(
            "INSERT INTO raptor_edges "
            "(parent_node_id, child_contextual_chunk_id, workspace_id) "
            "VALUES (%s, %s, %s) "
            "ON CONFLICT (parent_node_id, child_contextual_chunk_id) "
            "  WHERE child_contextual_chunk_id IS NOT NULL DO NOTHING",
            (parent_node_id, child_contextual_chunk_id, workspace_id),
        )


async def read_leaves_for_raptor_build(
    conn: Connection, *, file_id: str
) -> list[tuple[str, str, list[float], str]]:
    """Return [(contextual_chunk_id, contextual_text, embedding_vector,
    embedder_model_id), ...] for a file, ordered by contextual_chunk_id.

    Reads from contextual_chunks JOIN chunk_embeddings — the L1 leaves
    that raptor_build_file_impl will cluster + summarize into L2+ nodes.
    """
    cur = await conn.execute(
        "SELECT cc.id::text, cc.contextual_text, ce.embedding::text, ce.model_id "
        "FROM contextual_chunks cc "
        "JOIN chunk_embeddings ce ON ce.contextual_chunk_id = cc.id "
        "WHERE cc.file_id = %s "
        "ORDER BY cc.id ASC",
        (file_id,),
    )
    rows = await cur.fetchall()
    out: list[tuple[str, str, list[float], str]] = []
    for row in rows:
        cc_id, text, emb_text, model_id = row
        # halfvec text representation is "[v1,v2,...]"
        vec_str = emb_text.strip()
        if vec_str.startswith("[") and vec_str.endswith("]"):
            vec_str = vec_str[1:-1]
        vector = [float(x) for x in vec_str.split(",") if x.strip()]
        out.append((cc_id, text, vector, model_id))
    return out


async def count_raptor_nodes_for_file(
    conn: Connection, *, file_id: str
) -> int:
    cur = await conn.execute(
        "SELECT count(*) FROM raptor_nodes WHERE file_id = %s",
        (file_id,),
    )
    row = await cur.fetchone()
    return int(row[0]) if row else 0
