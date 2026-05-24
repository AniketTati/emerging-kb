"""Phase 4 — index smoke helpers (INTERNAL, no HTTP surface).

Two functions:
  - `bm25_smoke(conn, *, workspace_id, query, limit)` — BM25 search over
    `contextual_chunks.contextual_text` UNION `raptor_nodes.text`.
  - `dense_smoke(conn, *, workspace_id, query_vec, limit)` — HNSW search over
    `chunk_embeddings.embedding` UNION `raptor_nodes.embedding`.

Both return `list[tuple[id, score, level, scope]]`:
  - `id`: uuid of the hit (contextual_chunks.id or raptor_nodes.id).
  - `score`: BM25 score (higher = better) or cosine distance (lower = better).
  - `level`: 1 for contextual_chunks (leaves), 2..6 for raptor_nodes.
  - `scope`: 'leaf' for contextual_chunks; 'per_doc'/'corpus' for raptor_nodes.

The 4-tuple shape is locked at G1 decision #11. Phase 8 will wrap these with
the real planner + channel orchestration.

Workspace isolation: callers must SET `app.workspace_id` on the connection
before calling these helpers (or pass a `db_superuser` connection that
explicitly filters by workspace_id — both helpers accept a `workspace_id`
kwarg and apply it as a WHERE filter). The kb_app role's RLS policy also
enforces isolation regardless of the helper's WHERE clause.

Used by:
  - tests/test_retrieval_smoke.py (Phase 4 G3 spec)
  - scripts/verify_phase_4.sh (Phase 4 G5 verify)

NOT used by `kb.api.*` — that's Phase 8.
"""

from __future__ import annotations

from typing import Any


SmokeHit = tuple[str, float, int, str]


async def bm25_smoke(
    conn: Any,
    *,
    workspace_id: str,
    query: str,
    limit: int = 10,
) -> list[SmokeHit]:
    """BM25 search across contextual_chunks + raptor_nodes.

    Uses pg_search's `@@@` operator + `paradedb.score()` for ranking.
    UNION result is re-ranked by score descending and truncated to `limit`.
    """
    if not query.strip():
        return []

    # Two SELECTs, UNION-ALL combined, sorted in Python so we don't have to
    # rely on pg_search's UNION semantics for score preservation. Each leg
    # filters by workspace_id (RLS holds redundantly).
    leaf_sql = """
        SELECT id::text,
               paradedb.score(id) AS score,
               1 AS level,
               'leaf' AS scope
        FROM contextual_chunks
        WHERE workspace_id = %s
          AND contextual_text @@@ %s
        ORDER BY score DESC
        LIMIT %s
    """
    node_sql = """
        SELECT id::text,
               paradedb.score(id) AS score,
               level,
               scope
        FROM raptor_nodes
        WHERE workspace_id = %s
          AND text @@@ %s
        ORDER BY score DESC
        LIMIT %s
    """

    leaf_cur = await conn.execute(leaf_sql, (workspace_id, query, limit))
    leaf_rows = await leaf_cur.fetchall()

    node_cur = await conn.execute(node_sql, (workspace_id, query, limit))
    node_rows = await node_cur.fetchall()

    combined: list[SmokeHit] = [
        (str(r[0]), float(r[1]), int(r[2]), str(r[3]))
        for r in (*leaf_rows, *node_rows)
    ]
    combined.sort(key=lambda h: h[1], reverse=True)
    return combined[:limit]


async def dense_smoke(
    conn: Any,
    *,
    workspace_id: str,
    query_vec: list[float],
    limit: int = 10,
) -> list[SmokeHit]:
    """Dense (HNSW) search across chunk_embeddings + raptor_nodes.

    Uses pgvector's `<=>` cosine-distance operator. Distance is converted to
    a similarity score (`1 - distance`) so callers can rank higher-is-better
    uniformly across BM25 + dense smoke results.

    `query_vec` must be a 3072-d list of floats (matches the halfvec(3072)
    columns). Returned 4-tuple `score` field is the similarity in [-1, 1]
    (1 = identical, 0 = orthogonal, -1 = opposite).
    """
    if not query_vec:
        return []
    vec_literal = "[" + ",".join(repr(float(v)) for v in query_vec) + "]"

    chunk_sql = """
        SELECT contextual_chunk_id::text,
               1.0 - (embedding <=> %s::halfvec)::float AS score,
               1 AS level,
               'leaf' AS scope
        FROM chunk_embeddings
        WHERE workspace_id = %s
        ORDER BY embedding <=> %s::halfvec
        LIMIT %s
    """
    node_sql = """
        SELECT id::text,
               1.0 - (embedding <=> %s::halfvec)::float AS score,
               level,
               scope
        FROM raptor_nodes
        WHERE workspace_id = %s
        ORDER BY embedding <=> %s::halfvec
        LIMIT %s
    """

    chunk_cur = await conn.execute(
        chunk_sql, (vec_literal, workspace_id, vec_literal, limit)
    )
    chunk_rows = await chunk_cur.fetchall()

    node_cur = await conn.execute(
        node_sql, (vec_literal, workspace_id, vec_literal, limit)
    )
    node_rows = await node_cur.fetchall()

    combined: list[SmokeHit] = [
        (str(r[0]), float(r[1]), int(r[2]), str(r[3]))
        for r in (*chunk_rows, *node_rows)
    ]
    combined.sort(key=lambda h: h[1], reverse=True)
    return combined[:limit]
