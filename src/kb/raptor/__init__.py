"""Phase 3d — RAPTOR tree builder.

Per build_tracker §5.10 decisions:
  #1 clustering algorithm: sklearn AgglomerativeClustering(cosine, average)
  #2 branching factor: 8 (configurable via KB_RAPTOR_BRANCHING_FACTOR)
  #3 max levels: 6 (configurable via KB_RAPTOR_MAX_LEVELS)
  #4 termination: n_at_level ≤ 1 OR level > MAX_LEVELS OR n_at_level ≤ branching

Two surfaces:
  - `cluster_embeddings(vectors, branching_factor)` — pure function returning
    per-vector cluster labels. Deterministic given input. Used by both
    `build_tree_for_file` (3d, per-doc) and Phase 3e's corpus orchestrator.
  - `build_tree_for_file` — async worker-level entrypoint that orchestrates
    Summarizer + Embedder + DB writes. Exposes a `_build_in_memory` helper
    for unit tests that inject fake_summarize + fake_embed dependencies
    (no DB, no real LLM).

Phase 3e adds a sibling `cluster_embeddings_corpus(...)` switching to
UMAP+GMM since AgglomerativeClustering is O(N²) — infeasible at N=100K.
"""

from __future__ import annotations

import asyncio
import math
import os
from typing import Awaitable, Callable

import numpy as np
from sklearn.cluster import AgglomerativeClustering


DEFAULT_BRANCHING_FACTOR = 8
DEFAULT_MAX_LEVELS = 6


# ---------------------------------------------------------------------------
# Pure-function clustering (decision #1, #2)
# ---------------------------------------------------------------------------


def cluster_embeddings(
    vectors: list[list[float]],
    *,
    branching_factor: int = DEFAULT_BRANCHING_FACTOR,
) -> list[int]:
    """Cluster N vectors into ceil(N/branching_factor) clusters via
    AgglomerativeClustering(metric='cosine', linkage='average').

    Returns a per-vector cluster label (0-indexed, contiguous).

    Deterministic: same input vectors + branching → same labels every call.
    Required so RAPTOR tree rebuilds produce stable structure for Phase 4
    HNSW re-indexing logic.

    Edge case: n=1 → returns [0] (singleton cluster).
    """
    n = len(vectors)
    if n == 0:
        return []
    if n == 1:
        return [0]

    n_clusters = max(1, math.ceil(n / branching_factor))
    if n_clusters >= n:
        # Each vector its own cluster — trivial, return identity labels.
        return list(range(n))

    X = np.asarray(vectors, dtype=np.float32)
    model = AgglomerativeClustering(
        n_clusters=n_clusters,
        metric="cosine",
        linkage="average",
    )
    labels = model.fit_predict(X)
    return [int(label) for label in labels]


# ---------------------------------------------------------------------------
# Tree-build orchestrator (decision #4 termination + #15 embedder reuse)
# ---------------------------------------------------------------------------


SummarizeFn = Callable[..., Awaitable]  # async (*, texts, doc_context=None) -> Summary
EmbedFn = Callable[[list[str]], Awaitable]  # async (texts) -> list[EmbeddingResult]


async def _build_in_memory(
    *,
    leaf_embeddings: list[list[float]],
    leaf_texts: list[str],
    leaf_ids: list[str],
    summarize_fn: SummarizeFn,
    embed_fn: EmbedFn,
    branching_factor: int = DEFAULT_BRANCHING_FACTOR,
    max_levels: int = DEFAULT_MAX_LEVELS,
    concurrency: int = 4,
) -> list[int]:
    """Pure-orchestrator helper exposed for unit testing.

    Builds a RAPTOR tree from leaf embeddings in memory, calling
    `summarize_fn` + `embed_fn` for each cluster at each level. Returns the
    list of levels actually built in raptor_nodes (2..max_levels — L1 leaves
    are NOT in raptor_nodes per decision #9).

    Used by `build_tree_for_file` (which adds the DB-write layer on top)
    and directly by `test_raptor_unit.py` (which injects fakes).
    """
    if not leaf_embeddings:
        return []
    assert len(leaf_embeddings) == len(leaf_texts) == len(leaf_ids), (
        "leaf inputs must align"
    )

    levels_built: list[int] = []
    semaphore = asyncio.Semaphore(concurrency)

    # State at each level: (texts, embeddings, ids). At L=2, ids point at
    # contextual_chunks; at L>=3, ids point at raptor_nodes from the prior
    # level (for edge-building when this orchestrator is wrapped by the DB
    # layer; _build_in_memory itself just tracks ids opaquely).
    prev_texts = list(leaf_texts)
    prev_embeddings = list(leaf_embeddings)
    prev_ids = list(leaf_ids)

    for level in range(2, max_levels + 1):
        n = len(prev_embeddings)

        # Termination checks (decision #4):
        if n <= 1:
            # Root reached — nothing to cluster.
            break
        if n <= branching_factor:
            # One more cluster step would collapse to N=1 with no information
            # gain. Build ONE final level (the root) by clustering everything
            # into a single cluster, then terminate.
            n_clusters = 1
        else:
            n_clusters = max(1, math.ceil(n / branching_factor))

        labels = cluster_embeddings(prev_embeddings, branching_factor=branching_factor) \
            if n_clusters > 1 else [0] * n

        # Group child-indexes per cluster.
        clusters: dict[int, list[int]] = {}
        for idx, label in enumerate(labels):
            clusters.setdefault(label, []).append(idx)

        # Summarize each cluster (parallel under semaphore).
        async def _summarize_one(cluster_idx: int, member_indexes: list[int]):
            async with semaphore:
                cluster_texts = [prev_texts[i] for i in member_indexes]
                summary = await summarize_fn(texts=cluster_texts)
                return cluster_idx, member_indexes, summary

        summaries = await asyncio.gather(*(
            _summarize_one(ci, mi) for ci, mi in clusters.items()
        ))
        # Sort by cluster_id for deterministic level output.
        summaries.sort(key=lambda t: t[0])

        # Embed all summaries in one batch call.
        summary_texts = [s.text for _, _, s in summaries]
        summary_embeddings = await embed_fn(summary_texts)

        # State for next level: each cluster becomes one "node" carrying
        # (summary_text, summary_embedding, opaque_node_id_placeholder). The
        # in-memory orchestrator uses synthetic ids (level/cluster). The
        # DB-bound caller swaps in real raptor_nodes.id values.
        next_texts = [s.text for _, _, s in summaries]
        next_embeddings = [list(e.vector) for e in summary_embeddings]
        next_ids = [f"L{level}-c{ci}" for ci, _, _ in summaries]

        levels_built.append(level)

        prev_texts = next_texts
        prev_embeddings = next_embeddings
        prev_ids = next_ids

        # If we just produced a single root-cluster summary, we're done.
        if len(next_texts) <= 1:
            break

    return levels_built


# Public façade used by the worker — DB-backed orchestration lives in
# the worker module since it needs DB connection + transaction management.
# The function name `build_tree_for_file` is referenced by tests via
# `build_tree_for_file._build_in_memory` — expose the helper as an attribute.

class _BuildTreeFacade:
    """Namespace exposing `_build_in_memory` to unit tests + a placeholder
    docstring for the DB-backed entry point (which lives in the worker)."""

    _build_in_memory = staticmethod(_build_in_memory)

    def __call__(self, *args, **kwargs):
        raise NotImplementedError(
            "build_tree_for_file is invoked from kb.workers.tasks.raptor_build_file_impl, "
            "which provides the DB connection + transaction. Tests use "
            "build_tree_for_file._build_in_memory directly."
        )


build_tree_for_file = _BuildTreeFacade()
