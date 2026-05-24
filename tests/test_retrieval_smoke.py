"""Phase 4 — Smoke helper tests for kb.retrieval.smoke.

RED at G3: imports `kb.retrieval.smoke` which doesn't exist yet — lands at G4
alongside `migrations/sql/0013_indexes.sql`. Module is INTERNAL — not mounted
on any HTTP router, not importable from `kb.api.*`. Used only by these tests
+ `scripts/verify_phase_4.sh` to prove the indexes work end-to-end.

Spec: tests/specs/phase_4.md §3 (decisions #10, #11, #12).
"""

from __future__ import annotations

import uuid

import psycopg
import pytest


pytestmark = pytest.mark.asyncio


# Workspace UUIDs used across tests. RLS isolation test (test #4) uses both.
_WS_A = "33333333-3333-3333-3333-333333333333"
_WS_B = "44444444-4444-4444-4444-444444444444"


async def _seed_minimal_retrievable(
    db_superuser, workspace: str, *, marker: str
) -> tuple[str, str]:
    """Seed 1 file → 1 chunk → 1 contextual_chunk (with `marker` in text) →
    1 chunk_embedding (one-hot at index 0) → 1 raptor_node (same text + same
    one-hot embedding).

    Returns (cc_id, rn_id) — the IDs of the seeded contextual_chunk +
    raptor_node so tests can assert on specific hits.

    Marker is a unique token (e.g. 'zxqvbnm-marker-A') so BM25 smoke can
    use it as a query and we know exactly which row should top-rank.
    """
    await db_superuser.execute(
        "SELECT set_config('app.workspace_id', %s, true)", (workspace,)
    )
    import hashlib

    sha = hashlib.sha256(f"{workspace}-{marker}".encode()).hexdigest()
    file_id = str(uuid.uuid4())
    await db_superuser.execute(
        "INSERT INTO files (id, workspace_id, name, content_sha, object_key, "
        "mime_type, size_bytes, lifecycle_state) "
        "VALUES (%s, %s, %s, %s, %s, 'application/pdf', 100, 'ready')",
        (file_id, workspace, f"smoke-{marker}.pdf", sha, f"raw_files/{sha}"),
    )
    rp_id = str(uuid.uuid4())
    await db_superuser.execute(
        "INSERT INTO raw_pages (id, file_id, workspace_id, page_number, text, "
        "layout_json, content_sha) "
        "VALUES (%s, %s, %s, 1, %s, '{}'::jsonb, %s)",
        (rp_id, file_id, workspace, f"page with {marker}", sha),
    )
    chunk_id = str(uuid.uuid4())
    await db_superuser.execute(
        "INSERT INTO chunks (id, file_id, workspace_id, chunk_index, text, "
        "source_page_numbers, token_count, content_sha) "
        "VALUES (%s, %s, %s, 0, %s, %s, 5, %s)",
        (chunk_id, file_id, workspace, f"chunk with {marker}", [1], sha),
    )
    cc_id = str(uuid.uuid4())
    await db_superuser.execute(
        "INSERT INTO contextual_chunks (id, chunk_id, file_id, workspace_id, "
        "contextual_prefix, contextual_text, model_id, prefix_token_count, "
        "cache_creation_input_tokens, cache_read_input_tokens) "
        "VALUES (%s, %s, %s, %s, '', %s, 'identity', 0, 0, 0)",
        (cc_id, chunk_id, file_id, workspace, f"contextual text {marker}"),
    )
    vec = [0.0] * 3072
    vec[0] = 1.0
    vec_literal = "[" + ",".join(repr(float(v)) for v in vec) + "]"
    await db_superuser.execute(
        "INSERT INTO chunk_embeddings (contextual_chunk_id, file_id, "
        "workspace_id, embedding, model_id) "
        "VALUES (%s, %s, %s, %s::halfvec, 'test-mock')",
        (cc_id, file_id, workspace, vec_literal),
    )
    rn_id = str(uuid.uuid4())
    await db_superuser.execute(
        "INSERT INTO raptor_nodes (id, scope, file_id, workspace_id, "
        "level, text, embedding, cluster_id_in_level, "
        "summarizer_model_id, embedder_model_id) "
        "VALUES (%s, 'per_doc', %s, %s, 2, %s, %s::halfvec, 0, "
        "'identity', 'test-mock')",
        (rn_id, file_id, workspace, f"raptor summary {marker}", vec_literal),
    )
    return cc_id, rn_id


# ===========================================================================
# Decision #11 — smoke helper signature + return shape
# ===========================================================================


async def test_bm25_smoke_returns_ranked_results(db_superuser):
    """bm25_smoke returns list of 4-tuples (id, score, level, scope), ranked
    by BM25 score descending. The seeded `marker` token is unique so the
    seeded row top-ranks."""
    from kb.retrieval.smoke import bm25_smoke

    marker = "zxqvbnm-marker-bm25"
    cc_id, _rn_id = await _seed_minimal_retrievable(db_superuser, _WS_A, marker=marker)

    hits = await bm25_smoke(
        db_superuser, workspace_id=_WS_A, query=marker, limit=5
    )

    assert isinstance(hits, list)
    assert len(hits) >= 1, f"expected ≥1 BM25 hit for marker={marker}; got {hits}"
    # Each hit is a 4-tuple — locks the contract for decision #11.
    for hit in hits:
        assert len(hit) == 4, f"expected 4-tuple (id, score, level, scope); got {hit}"
        hit_id, score, level, scope = hit
        assert isinstance(hit_id, str)
        assert isinstance(score, (int, float))
        assert isinstance(level, int)
        assert isinstance(scope, str)
    # Ranked descending by score.
    scores = [h[1] for h in hits]
    assert scores == sorted(scores, reverse=True), f"hits not ranked: {scores}"


async def test_dense_smoke_returns_ranked_results(db_superuser):
    """dense_smoke returns 4-tuples ranked by cosine. Query is the same
    one-hot vector as the seeded row — cosine = 1.0, top hit guaranteed."""
    from kb.retrieval.smoke import dense_smoke

    marker = "zxqvbnm-marker-dense"
    cc_id, rn_id = await _seed_minimal_retrievable(db_superuser, _WS_A, marker=marker)

    query_vec = [0.0] * 3072
    query_vec[0] = 1.0

    hits = await dense_smoke(
        db_superuser, workspace_id=_WS_A, query_vec=query_vec, limit=5
    )

    assert isinstance(hits, list)
    assert len(hits) >= 1, f"expected ≥1 dense hit; got {hits}"
    for hit in hits:
        assert len(hit) == 4, f"expected 4-tuple (id, score, level, scope); got {hit}"
    # Top hit's ID is one of the seeded rows.
    top_id = hits[0][0]
    assert top_id in (cc_id, rn_id), (
        f"expected top hit to be seeded row {cc_id} or {rn_id}; got {top_id}"
    )


# ===========================================================================
# Decision #11 — multi-level hits (chunks + raptor_nodes both indexed)
# ===========================================================================


async def test_smoke_returns_hits_across_levels(db_superuser):
    """When a workspace has BOTH contextual_chunks (level=1) AND
    raptor_nodes (level≥2), dense_smoke should return hits from both
    levels (proving both tables are indexed + queried).

    The seeded marker creates one row in EACH table with the same
    embedding — both should rank top."""
    from kb.retrieval.smoke import dense_smoke

    marker = "zxqvbnm-marker-levels"
    cc_id, rn_id = await _seed_minimal_retrievable(db_superuser, _WS_A, marker=marker)

    query_vec = [0.0] * 3072
    query_vec[0] = 1.0
    hits = await dense_smoke(
        db_superuser, workspace_id=_WS_A, query_vec=query_vec, limit=10
    )

    levels_hit = {hit[2] for hit in hits}
    assert 1 in levels_hit, (
        f"expected level=1 (contextual_chunks) hit; got levels={levels_hit}"
    )
    assert any(lvl >= 2 for lvl in levels_hit), (
        f"expected level≥2 (raptor_nodes) hit; got levels={levels_hit}"
    )


# ===========================================================================
# Decision #10 + RLS at index layer — workspace isolation
# ===========================================================================


async def test_smoke_respects_workspace_isolation(db_superuser, db_session):
    """RLS holds at the index layer — querying as workspace B sees zero of
    workspace A's seeded rows.

    db_superuser seeds WS_A (bypasses RLS to do so); db_session is the
    kb_app role connection which enforces RLS. Smoke helper takes a
    connection — when called with db_session set to WS_B, it must return
    empty results even though WS_A's rows exist in the table."""
    from kb.retrieval.smoke import bm25_smoke, dense_smoke

    marker = "zxqvbnm-marker-rls"
    await _seed_minimal_retrievable(db_superuser, _WS_A, marker=marker)

    # Now query as WS_B through the kb_app connection — RLS must hide
    # WS_A's rows.
    await db_session.execute(
        "SELECT set_config('app.workspace_id', %s, true)", (_WS_B,)
    )

    bm25_hits = await bm25_smoke(
        db_session, workspace_id=_WS_B, query=marker, limit=5
    )
    assert bm25_hits == [], (
        f"workspace B should see no BM25 hits for WS_A's seed; got {bm25_hits}"
    )

    query_vec = [0.0] * 3072
    query_vec[0] = 1.0
    dense_hits = await dense_smoke(
        db_session, workspace_id=_WS_B, query_vec=query_vec, limit=5
    )
    assert dense_hits == [], (
        f"workspace B should see no dense hits for WS_A's seed; got {dense_hits}"
    )


async def test_smoke_returns_empty_for_unknown_workspace(db_superuser):
    """Brand-new workspace UUID (never seeded) → empty list. Helper must
    not raise on empty results."""
    from kb.retrieval.smoke import bm25_smoke, dense_smoke

    fresh_workspace = str(uuid.uuid4())
    await db_superuser.execute(
        "SELECT set_config('app.workspace_id', %s, true)", (fresh_workspace,)
    )

    bm25_hits = await bm25_smoke(
        db_superuser, workspace_id=fresh_workspace, query="anything", limit=5
    )
    assert bm25_hits == [], f"unknown workspace should return []; got {bm25_hits}"

    query_vec = [0.0] * 3072
    query_vec[0] = 1.0
    dense_hits = await dense_smoke(
        db_superuser, workspace_id=fresh_workspace, query_vec=query_vec, limit=5
    )
    assert dense_hits == [], f"unknown workspace should return []; got {dense_hits}"
