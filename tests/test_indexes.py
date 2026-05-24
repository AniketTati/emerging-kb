"""Phase 4 — DDL + planner-usage tests for HNSW + BM25 indexes.

RED at G3: depends on `migrations/sql/0013_indexes.sql` (lands at G4) which
creates the 4 indexes asserted below. Without the migration, the index-exists
assertions fail (rows absent from pg_indexes) and the planner asserts fail
(planner falls back to Seq Scan).

Spec: tests/specs/phase_4.md §3 (decisions #1, #2, #3, #4, #15).
"""

from __future__ import annotations

import uuid

import psycopg
import pytest


pytestmark = pytest.mark.asyncio


# ===========================================================================
# Bucket A — DDL invariants (5 tests, decisions #1, #2, #15)
# ===========================================================================


async def test_hnsw_index_exists_on_chunk_embeddings(db_superuser):
    """Decision #1 + #2: HNSW index on chunk_embeddings.embedding using
    halfvec_cosine_ops. Built CONCURRENTLY (decision #4) — asserted only by
    the fact that the migration applied without lock errors during fixture
    setup; the index DEFINITION inspection here covers the rest."""
    row = await (
        await db_superuser.execute(
            "SELECT indexdef FROM pg_indexes "
            "WHERE schemaname='public' AND tablename='chunk_embeddings' "
            "AND indexname='chunk_embeddings_embedding_hnsw_idx'"
        )
    ).fetchone()
    assert row is not None, "expected HNSW index on chunk_embeddings.embedding"
    indexdef = row[0]
    assert "USING hnsw" in indexdef, f"index not USING hnsw: {indexdef}"
    assert "halfvec_cosine_ops" in indexdef, (
        f"expected halfvec_cosine_ops operator class; got: {indexdef}"
    )


async def test_hnsw_index_exists_on_raptor_nodes(db_superuser):
    """Decision #1 + #2: same shape for raptor_nodes.embedding. Single index
    covers BOTH scope='per_doc' and scope='corpus' rows (decision #5 —
    single shared HNSW graph, RLS filters at query time)."""
    row = await (
        await db_superuser.execute(
            "SELECT indexdef FROM pg_indexes "
            "WHERE schemaname='public' AND tablename='raptor_nodes' "
            "AND indexname='raptor_nodes_embedding_hnsw_idx'"
        )
    ).fetchone()
    assert row is not None, "expected HNSW index on raptor_nodes.embedding"
    indexdef = row[0]
    assert "USING hnsw" in indexdef, f"index not USING hnsw: {indexdef}"
    assert "halfvec_cosine_ops" in indexdef, (
        f"expected halfvec_cosine_ops; got: {indexdef}"
    )


async def test_bm25_index_exists_on_contextual_chunks(db_superuser):
    """Decision #1: pg_search (Tantivy) BM25 index on
    contextual_chunks.contextual_text."""
    row = await (
        await db_superuser.execute(
            "SELECT indexdef FROM pg_indexes "
            "WHERE schemaname='public' AND tablename='contextual_chunks' "
            "AND indexname='contextual_chunks_text_bm25_idx'"
        )
    ).fetchone()
    assert row is not None, "expected BM25 index on contextual_chunks.contextual_text"
    indexdef = row[0]
    assert "USING bm25" in indexdef, f"index not USING bm25: {indexdef}"


async def test_bm25_index_exists_on_raptor_nodes_text(db_superuser):
    """Decision #1: BM25 index on raptor_nodes.text — same shape; covers
    summary text from both scope='per_doc' and scope='corpus' rows."""
    row = await (
        await db_superuser.execute(
            "SELECT indexdef FROM pg_indexes "
            "WHERE schemaname='public' AND tablename='raptor_nodes' "
            "AND indexname='raptor_nodes_text_bm25_idx'"
        )
    ).fetchone()
    assert row is not None, "expected BM25 index on raptor_nodes.text"
    indexdef = row[0]
    assert "USING bm25" in indexdef, f"index not USING bm25: {indexdef}"


async def test_kb_app_can_query_indexed_tables(db_session):
    """Decision #15: kb_app role already has SELECT on the 4 tables.
    Postgres auto-grants index USAGE when SELECT is granted on the table —
    no explicit grant changes needed. This test proves kb_app can SELECT
    through the indexes post-migration (failure would mean a misnamed grant
    or a permission regression)."""
    # Set workspace context so RLS doesn't reject the query.
    workspace = str(uuid.uuid4())
    await db_session.execute(
        "SELECT set_config('app.workspace_id', %s, true)", (workspace,)
    )
    # Plain SELECT — no rows expected (clean workspace), but the query
    # must succeed (no permission errors).
    for table in (
        "chunk_embeddings",
        "raptor_nodes",
        "contextual_chunks",
    ):
        cur = await db_session.execute(f"SELECT count(*) FROM {table}")  # noqa: S608
        row = await cur.fetchone()
        assert row[0] == 0, f"expected 0 rows in clean workspace's {table}"


# ===========================================================================
# Bucket B — Planner usage (3 tests)
# ===========================================================================


async def _seed_minimal_index_targets(db_superuser, workspace: str) -> None:
    """Insert enough rows so the planner has a non-trivial choice between
    seq scan and index scan. HNSW kicks in above ~100 rows; below that the
    planner correctly picks seq scan. Need ~200 rows to force index usage.

    Fabricated via direct SQL bypassing the full pipeline — purely for
    planner shape inspection. Cleared at session teardown (rollback)."""
    await db_superuser.execute(
        "SELECT set_config('app.workspace_id', %s, true)", (workspace,)
    )
    # Need files → chunks → contextual_chunks → chunk_embeddings + raptor_nodes
    # all linked by FK. Minimal seed: 1 file, 1 chunk, 1 contextual_chunk,
    # 200 chunk_embeddings (impossible — each has UNIQUE (contextual_chunk_id,
    # model_id)), so instead 200 contextual_chunks each with 1 embedding.
    file_id = str(uuid.uuid4())
    await db_superuser.execute(
        "INSERT INTO files (id, workspace_id, name, content_sha, object_key, "
        "mime_type, size_bytes, lifecycle_state) "
        "VALUES (%s, %s, 'planner-seed.pdf', 'sha-planner-seed', "
        "'raw_files/sha-planner-seed', 'application/pdf', 100, 'ready')",
        (file_id, workspace),
    )
    await db_superuser.execute(
        "INSERT INTO raw_pages (id, file_id, workspace_id, page_number, text, "
        "layout_json, content_sha) "
        "VALUES (%s, %s, %s, 1, 'page text', '{}'::jsonb, 'sha-planner-seed')",
        (str(uuid.uuid4()), file_id, workspace),
    )
    for i in range(200):
        chunk_id = str(uuid.uuid4())
        cc_id = str(uuid.uuid4())
        await db_superuser.execute(
            "INSERT INTO chunks (id, file_id, workspace_id, chunk_index, text, "
            "source_page_numbers, token_count, content_sha) "
            "VALUES (%s, %s, %s, %s, %s, %s, 5, %s)",
            (chunk_id, file_id, workspace, i, f"chunk text {i}", [1], f"sha-{i}"),
        )
        await db_superuser.execute(
            "INSERT INTO contextual_chunks (id, chunk_id, file_id, workspace_id, "
            "contextual_prefix, contextual_text, model_id, prefix_token_count, "
            "cache_creation_input_tokens, cache_read_input_tokens) "
            "VALUES (%s, %s, %s, %s, '', %s, 'identity', 0, 0, 0)",
            (cc_id, chunk_id, file_id, workspace, f"ctx text {i}"),
        )
        # Embedding: one-hot at index i % 3072 so we get distinct vectors.
        vec = [0.0] * 3072
        vec[i % 3072] = 1.0
        vec_literal = "[" + ",".join(repr(float(v)) for v in vec) + "]"
        await db_superuser.execute(
            "INSERT INTO chunk_embeddings (contextual_chunk_id, file_id, "
            "workspace_id, embedding, model_id) "
            "VALUES (%s, %s, %s, %s::halfvec, 'test-mock')",
            (cc_id, file_id, workspace, vec_literal),
        )


async def test_planner_uses_hnsw_for_chunk_embeddings_knn(db_superuser):
    """EXPLAIN on an ORDER BY embedding <=> :vec LIMIT k query must show
    Index Scan using chunk_embeddings_embedding_hnsw_idx (NOT Seq Scan).

    The pgvector planner picks HNSW only when row count is above its
    seq-scan threshold + ORDER BY uses a vector distance operator."""
    workspace = str(uuid.uuid4())
    await _seed_minimal_index_targets(db_superuser, workspace)

    vec = [0.0] * 3072
    vec[0] = 1.0
    vec_literal = "[" + ",".join(repr(float(v)) for v in vec) + "]"
    cur = await db_superuser.execute(
        "EXPLAIN (FORMAT JSON) "
        "SELECT id, embedding <=> %s::halfvec AS dist FROM chunk_embeddings "
        "WHERE workspace_id = %s "
        "ORDER BY embedding <=> %s::halfvec LIMIT 5",
        (vec_literal, workspace, vec_literal),
    )
    plan_json = (await cur.fetchone())[0]
    plan_text = str(plan_json)
    assert "hnsw" in plan_text.lower(), (
        f"expected HNSW index in plan; got:\n{plan_text}"
    )
    assert "Seq Scan" not in plan_text or "Index" in plan_text, (
        f"planner fell back to seq scan only:\n{plan_text}"
    )


async def test_planner_uses_hnsw_for_raptor_nodes_knn(db_superuser):
    """Same shape for raptor_nodes. Seeds N=200 raptor_nodes (one per
    fabricated 'cluster') so HNSW kicks in."""
    workspace = str(uuid.uuid4())
    await db_superuser.execute(
        "SELECT set_config('app.workspace_id', %s, true)", (workspace,)
    )
    # raptor_nodes can have file_id NULL (scope='corpus'); seed both scopes
    # to exercise the index across the full table.
    file_id = str(uuid.uuid4())
    await db_superuser.execute(
        "INSERT INTO files (id, workspace_id, name, content_sha, object_key, "
        "mime_type, size_bytes, lifecycle_state) "
        "VALUES (%s, %s, 'rn-seed.pdf', 'sha-rn-seed', 'raw_files/sha-rn-seed', "
        "'application/pdf', 100, 'ready')",
        (file_id, workspace),
    )
    for i in range(200):
        vec = [0.0] * 3072
        vec[i % 3072] = 1.0
        vec_literal = "[" + ",".join(repr(float(v)) for v in vec) + "]"
        scope = "per_doc" if i % 2 == 0 else "corpus"
        fid = file_id if scope == "per_doc" else None
        await db_superuser.execute(
            "INSERT INTO raptor_nodes (id, scope, file_id, workspace_id, "
            "level, text, embedding, cluster_id_in_level, "
            "summarizer_model_id, embedder_model_id) "
            "VALUES (%s, %s, %s, %s, 2, %s, %s::halfvec, %s, "
            "'identity', 'test-mock')",
            (str(uuid.uuid4()), scope, fid, workspace, f"summary {i}", vec_literal, i),
        )

    vec = [0.0] * 3072
    vec[0] = 1.0
    vec_literal = "[" + ",".join(repr(float(v)) for v in vec) + "]"
    cur = await db_superuser.execute(
        "EXPLAIN (FORMAT JSON) "
        "SELECT id, embedding <=> %s::halfvec AS dist FROM raptor_nodes "
        "WHERE workspace_id = %s "
        "ORDER BY embedding <=> %s::halfvec LIMIT 5",
        (vec_literal, workspace, vec_literal),
    )
    plan_json = (await cur.fetchone())[0]
    plan_text = str(plan_json)
    assert "hnsw" in plan_text.lower(), (
        f"expected HNSW index in plan; got:\n{plan_text}"
    )


async def test_planner_uses_bm25_for_text_search(db_superuser):
    """EXPLAIN on a BM25 query (`@@@` operator) shows the BM25 index in
    the plan. pg_search's BM25 operator is `@@@` which routes to the
    bm25 access method when an index exists."""
    workspace = str(uuid.uuid4())
    await _seed_minimal_index_targets(db_superuser, workspace)

    cur = await db_superuser.execute(
        "EXPLAIN (FORMAT JSON) "
        "SELECT id FROM contextual_chunks "
        "WHERE workspace_id = %s AND contextual_text @@@ 'chunk' LIMIT 5",
        (workspace,),
    )
    plan_json = (await cur.fetchone())[0]
    plan_text = str(plan_json)
    assert "bm25" in plan_text.lower(), (
        f"expected BM25 index in plan; got:\n{plan_text}"
    )
