"""Phase 3d — RAPTOR clustering + tree-build unit tests (no DB, no LLM).

RED at G3: imports `kb.raptor.cluster_embeddings` + `kb.raptor.build_tree_for_file`
which don't exist yet — land at G4.

Spec: tests/specs/phase_3d.md §3 (decisions #1, #2, #3, #4).
"""

from __future__ import annotations

import hashlib
import math

import pytest


def _seeded_vectors(n: int, dim: int = 3072, *, seed: str = "raptor-test") -> list[list[float]]:
    """Deterministic pseudo-random unit-norm vectors. Used so unit tests don't
    depend on a numpy random seed (which can drift across versions)."""
    vectors: list[list[float]] = []
    for i in range(n):
        raw = []
        for j in range(dim):
            h = hashlib.sha256(f"{seed}:{i}:{j}".encode("utf-8")).digest()
            raw.append((h[0] / 255.0) * 2.0 - 1.0)  # in [-1, 1]
        norm = math.sqrt(sum(v * v for v in raw)) or 1.0
        vectors.append([v / norm for v in raw])
    return vectors


# ===========================================================================
# §5.10 decision #1, #2 — clustering algorithm + branching arithmetic
# ===========================================================================


def test_cluster_embeddings_returns_one_label_per_vector():
    """Output length must equal input length; every label is a valid cluster id."""
    from kb.raptor import cluster_embeddings

    vectors = _seeded_vectors(20)
    labels = cluster_embeddings(vectors, branching_factor=8)

    assert len(labels) == 20
    n_clusters = max(labels) + 1
    # Expected: ceil(20 / 8) = 3 clusters
    assert n_clusters == 3, f"expected 3 clusters for n=20 branching=8; got {n_clusters}"
    assert all(0 <= label < n_clusters for label in labels)


def test_cluster_embeddings_branching_factor_arithmetic():
    """n_clusters == ceil(n / branching_factor) for various sizes."""
    from kb.raptor import cluster_embeddings

    # n=50, branching=8 → ceil(50/8) = 7
    labels_50 = cluster_embeddings(_seeded_vectors(50), branching_factor=8)
    assert max(labels_50) + 1 == 7

    # n=100, branching=8 → ceil(100/8) = 13
    labels_100 = cluster_embeddings(_seeded_vectors(100), branching_factor=8)
    assert max(labels_100) + 1 == 13

    # n=16, branching=4 → ceil(16/4) = 4
    labels_16 = cluster_embeddings(_seeded_vectors(16), branching_factor=4)
    assert max(labels_16) + 1 == 4


def test_cluster_embeddings_is_deterministic():
    """Same input vectors → same cluster assignment. Required so tree-build
    is reproducible across runs (Phase 4 retrieval references nodes by ID;
    rebuilds must produce stable structure for re-indexing logic)."""
    from kb.raptor import cluster_embeddings

    vectors = _seeded_vectors(24)
    labels_a = cluster_embeddings(vectors, branching_factor=8)
    labels_b = cluster_embeddings(vectors, branching_factor=8)
    assert labels_a == labels_b


def test_cluster_singleton_returns_single_label():
    """Edge case: n=1 → one cluster with one member. Tree-build relies on
    this for the termination check (n_at_level <= 1 ⇒ root reached)."""
    from kb.raptor import cluster_embeddings

    labels = cluster_embeddings(_seeded_vectors(1), branching_factor=8)
    assert labels == [0]


# ===========================================================================
# §5.10 decision #4 — termination conditions (#3 MAX_LEVELS=6 covered here too)
# ===========================================================================


def test_tree_terminates_when_n_le_branching():
    """When previous level has ≤ BRANCHING_FACTOR nodes, the next-level
    cluster would collapse to N=1 — no information gain. build_tree must
    terminate without writing the trivial L→L+1 transition.

    Asserted via the pure orchestrator: feed in 5 leaf embeddings (≤ 8 =
    branching_factor); expect ONE level built (L2 = single root cluster),
    no L3 attempted.
    """
    from kb.raptor import build_tree_for_file

    # Inject test doubles so we don't need a real DB / real LLM.
    captured_levels: list[int] = []

    async def fake_summarize(*, texts, doc_context=None):
        captured_levels.append(len(texts))
        from kb.summarization import Summary
        return Summary(
            text=f"summary-of-{len(texts)}",
            model_id="test-mock",
            input_token_count=0,
            output_token_count=10,
        )

    async def fake_embed(texts):
        from kb.embeddings import EmbeddingResult
        return [EmbeddingResult(vector=[0.1] * 3072, model_id="test-mock", dim=3072) for _ in texts]

    # 5 leaves; branching=8 → ceil(5/8)=1 cluster at L2 → terminate.
    leaves = _seeded_vectors(5)
    leaf_texts = [f"leaf {i}" for i in range(5)]
    leaf_ids = [f"chunk-{i}" for i in range(5)]

    levels_built = build_tree_for_file._build_in_memory(  # type: ignore[attr-defined]
        leaf_embeddings=leaves,
        leaf_texts=leaf_texts,
        leaf_ids=leaf_ids,
        summarize_fn=fake_summarize,
        embed_fn=fake_embed,
        branching_factor=8,
        max_levels=6,
    )

    # Expected tree: L1 (5 leaves, not stored in raptor_nodes) → L2 (1 root)
    # → terminate. levels_built describes the raptor_nodes levels only (L2+).
    assert levels_built == [2], f"expected [2]; got {levels_built}"


def test_tree_terminates_when_max_levels_reached():
    """For pathological inputs, MAX_LEVELS caps the tree depth even if
    n_at_level > branching_factor still."""
    from kb.raptor import build_tree_for_file

    async def fake_summarize(*, texts, doc_context=None):
        from kb.summarization import Summary
        return Summary(
            text="x", model_id="test-mock",
            input_token_count=0, output_token_count=1,
        )

    async def fake_embed(texts):
        from kb.embeddings import EmbeddingResult
        return [EmbeddingResult(vector=[0.1] * 3072, model_id="test-mock", dim=3072) for _ in texts]

    # 1000 leaves; branching=2 → would naturally go log_2(1000) ≈ 10 levels.
    # MAX_LEVELS=3 caps it.
    n = 1000
    leaves = _seeded_vectors(n)
    leaf_texts = [f"leaf {i}" for i in range(n)]
    leaf_ids = [f"chunk-{i}" for i in range(n)]

    levels_built = build_tree_for_file._build_in_memory(  # type: ignore[attr-defined]
        leaf_embeddings=leaves,
        leaf_texts=leaf_texts,
        leaf_ids=leaf_ids,
        summarize_fn=fake_summarize,
        embed_fn=fake_embed,
        branching_factor=2,
        max_levels=3,
    )

    # MAX_LEVELS=3 means raptor_nodes levels are {2, 3} only (L1 = leaves,
    # not stored). The tree stops at L3 even though further clustering would
    # still reduce.
    assert max(levels_built) <= 3
    assert 2 in levels_built  # at minimum L2 was built
