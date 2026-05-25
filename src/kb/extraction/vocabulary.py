"""WA-2 / Design 6 §"Pipeline integration" — vocabulary discovery from L2b.

The L2b cross-doc field clusterer (Phase 5b, `kb.extraction.promotion`)
groups proposed fields into FieldClusters keyed on a snake_case canonical
name. This module sits on top of that output and emits vocab candidates
when two or more clusters have *semantically similar names* — those then
become synonym entries in `domain_vocabulary`.

Per Design 6:
  - similarity threshold ≥ 0.85 (cosine on name embeddings)
  - combined n_docs_observed ≥ 5
  - source='discovered', confidence = max pairwise similarity
  - one canonical term is chosen (shortest cluster_name as tiebreaker),
    others become its synonyms
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

from kb.extraction.promotion import FieldCluster


@dataclass(frozen=True)
class VocabCandidate:
    canonical_term: str
    synonyms: tuple[str, ...]
    n_docs_observed: int
    confidence: float
    # Member cluster canonical_names that contributed (for audit).
    member_cluster_names: tuple[str, ...]


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a))
    db = math.sqrt(sum(y * y for y in b))
    if da == 0 or db == 0:
        return 0.0
    return num / (da * db)


def _pick_canonical(cluster_names: Iterable[str]) -> str:
    """Tiebreaker: shortest name wins (least-modified canonical form);
    alphabetic on ties (deterministic)."""
    return min(cluster_names, key=lambda n: (len(n), n))


def discover_vocabulary_candidates(
    *,
    clusters: list[FieldCluster],
    name_embeddings: dict[str, list[float]],
    similarity_threshold: float = 0.85,
    min_combined_docs: int = 5,
) -> list[VocabCandidate]:
    """Pairwise-compare cluster name embeddings; group into transitive
    similarity classes; emit a VocabCandidate per multi-cluster class.

    `name_embeddings[cluster.canonical_name]` must hold the vector for
    every cluster passed in. Clusters without an embedding are skipped.

    Returns the list of candidates ready to upsert via
    `kb.domain.vocabulary.upsert_vocabulary`.
    """
    if not clusters:
        return []

    names = [c.canonical_name for c in clusters if c.canonical_name in name_embeddings]
    if len(names) < 2:
        return []

    # Build adjacency: edge between names with cosine ≥ threshold.
    by_name = {c.canonical_name: c for c in clusters}
    adj: dict[str, dict[str, float]] = {n: {} for n in names}
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            sim = _cosine(name_embeddings[a], name_embeddings[b])
            if sim >= similarity_threshold:
                adj[a][b] = sim
                adj[b][a] = sim

    # Transitive connected components — union-find over the adjacency.
    parent: dict[str, str] = {n: n for n in names}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: str, y: str) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[ry] = rx

    for a, neighbors in adj.items():
        for b in neighbors:
            union(a, b)

    groups: dict[str, list[str]] = {}
    for n in names:
        groups.setdefault(find(n), []).append(n)

    out: list[VocabCandidate] = []
    for member_names in groups.values():
        if len(member_names) < 2:
            continue
        combined_docs = sum(by_name[n].n_docs_observed for n in member_names)
        if combined_docs < min_combined_docs:
            continue
        canonical = _pick_canonical(member_names)
        synonyms = tuple(sorted(n for n in member_names if n != canonical))
        # max pairwise similarity in the group as the confidence score
        max_sim = 0.0
        for i, a in enumerate(member_names):
            for b in member_names[i + 1:]:
                s = adj.get(a, {}).get(b, 0.0)
                if s > max_sim:
                    max_sim = s
        out.append(VocabCandidate(
            canonical_term=canonical,
            synonyms=synonyms,
            n_docs_observed=combined_docs,
            confidence=max_sim,
            member_cluster_names=tuple(sorted(member_names)),
        ))
    return out
