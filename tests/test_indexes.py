"""Phase 4 — DDL + planner-usage tests for HNSW + BM25 indexes.

RED at G3: depends on `migrations/sql/0013_indexes.sql` (lands at G4) which
creates the 4 indexes asserted below. Without the migration, the index-exists
assertions fail (rows absent from pg_indexes) and the planner asserts fail
(planner falls back to Seq Scan).

Spec: tests/specs/phase_4.md §3 (decisions #1, #2, #3, #4, #15).
"""

from __future__ import annotations

import hashlib
import uuid

import psycopg
import pytest


pytestmark = pytest.mark.asyncio


def _sha64(seed: str) -> str:
    """64-char hex SHA-256 — files.content_sha CHECK requires exactly 64 chars."""
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


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
# Bucket B — Planner usage
# ===========================================================================
#
# Planner-usage tests (HNSW + BM25 chosen by the planner) live in
# scripts/verify_phase_4.sh, NOT here. Rationale: at pytest-fixture scale
# (~200 fabricated rows per test) the planner correctly prefers btree
# index-scan + in-memory sort over HNSW — HNSW only wins above ~5K rows
# per workspace AND with up-to-date pg_statistic stats from ANALYZE.
# Forcing the planner via `SET LOCAL enable_*=off` flags tests a synthetic
# scenario rather than the real choice.
#
# verify_phase_4.sh exercises this at full-stack scale: it seeds 5+ docs
# through the real ingestion pipeline (Docling → chunk → contextualize →
# embed → RAPTOR), runs ANALYZE, then asserts EXPLAIN shows HNSW + BM25
# in the plan for realistic query shapes.
#
# The 4 DDL-invariant tests above cover the contract that the indexes
# EXIST with the right operator classes. Planner choice is a G5 concern.
