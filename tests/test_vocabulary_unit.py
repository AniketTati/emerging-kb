"""WA-2 / Design 6 — vocabulary discovery unit tests (pure-function)."""

from __future__ import annotations

import math

import pytest

from kb.extraction.promotion import FieldCluster
from kb.extraction.vocabulary import (
    VocabCandidate,
    discover_vocabulary_candidates,
)


def _cluster(name: str, n_docs: int) -> FieldCluster:
    return FieldCluster(
        canonical_name=name,
        description="",
        value_type="text",
        n_docs_observed=n_docs,
        prevalence=0.5,
        stability=1.0,
        value_type_confidence=1.0,
    )


# Vectors picked so the pairs we want similar have cosine > 0.85 and
# unrelated pairs have cosine < 0.85.
HIGH_SIM_A = [0.95, 0.05, 0.05, 0.05]
HIGH_SIM_B = [0.93, 0.10, 0.08, 0.05]
HIGH_SIM_C = [0.91, 0.12, 0.10, 0.07]
ORTHOGONAL = [0.05, 0.95, 0.05, 0.05]


def test_no_clusters_returns_empty():
    out = discover_vocabulary_candidates(
        clusters=[],
        name_embeddings={},
    )
    assert out == []


def test_single_cluster_returns_empty():
    cluster = _cluster("non_compete", 5)
    out = discover_vocabulary_candidates(
        clusters=[cluster],
        name_embeddings={"non_compete": HIGH_SIM_A},
    )
    assert out == []


def test_two_similar_clusters_emit_one_candidate():
    clusters = [
        _cluster("non_compete", 3),
        _cluster("non_competition_clause", 3),
    ]
    out = discover_vocabulary_candidates(
        clusters=clusters,
        name_embeddings={
            "non_compete": HIGH_SIM_A,
            "non_competition_clause": HIGH_SIM_B,
        },
        similarity_threshold=0.85,
        min_combined_docs=5,
    )
    assert len(out) == 1
    candidate = out[0]
    # Shortest name wins as canonical.
    assert candidate.canonical_term == "non_compete"
    assert candidate.synonyms == ("non_competition_clause",)
    assert candidate.n_docs_observed == 6
    assert candidate.confidence > 0.85


def test_three_similar_clusters_merge_into_one_group():
    """Design 6 example: 'non_compete' + 'non_competition_clause' +
    'restrictive_covenant' across enough docs."""
    clusters = [
        _cluster("non_compete", 2),
        _cluster("non_competition_clause", 2),
        _cluster("restrictive_covenant", 2),
    ]
    out = discover_vocabulary_candidates(
        clusters=clusters,
        name_embeddings={
            "non_compete": HIGH_SIM_A,
            "non_competition_clause": HIGH_SIM_B,
            "restrictive_covenant": HIGH_SIM_C,
        },
        similarity_threshold=0.85,
        min_combined_docs=5,
    )
    assert len(out) == 1
    cand = out[0]
    assert cand.canonical_term == "non_compete"
    assert set(cand.synonyms) == {"non_competition_clause", "restrictive_covenant"}
    assert cand.n_docs_observed == 6


def test_below_doc_threshold_skipped():
    clusters = [
        _cluster("a_term", 1),
        _cluster("similar_term", 1),
    ]
    out = discover_vocabulary_candidates(
        clusters=clusters,
        name_embeddings={"a_term": HIGH_SIM_A, "similar_term": HIGH_SIM_B},
        similarity_threshold=0.85,
        min_combined_docs=5,
    )
    assert out == []  # combined 2 < 5


def test_unrelated_clusters_dont_merge():
    clusters = [
        _cluster("indemnification", 3),
        _cluster("delivery_schedule", 3),
    ]
    out = discover_vocabulary_candidates(
        clusters=clusters,
        name_embeddings={
            "indemnification": HIGH_SIM_A,
            "delivery_schedule": ORTHOGONAL,
        },
        similarity_threshold=0.85,
        min_combined_docs=5,
    )
    assert out == []  # cosine well below 0.85


def test_clusters_without_embeddings_are_skipped():
    clusters = [
        _cluster("a", 5),
        _cluster("b", 5),
    ]
    # Only one has an embedding → no pair to compare.
    out = discover_vocabulary_candidates(
        clusters=clusters,
        name_embeddings={"a": HIGH_SIM_A},
    )
    assert out == []


def test_two_disjoint_similarity_groups_emit_two_candidates():
    """Group 1: {indemnification, hold_harmless} | Group 2: {non_compete, restrictive_covenant}"""
    GROUP1_A = [0.95, 0.05, 0.0, 0.0]
    GROUP1_B = [0.93, 0.10, 0.0, 0.0]
    GROUP2_A = [0.0, 0.0, 0.95, 0.05]
    GROUP2_B = [0.0, 0.0, 0.93, 0.10]
    clusters = [
        _cluster("indemnification", 3),
        _cluster("hold_harmless", 3),
        _cluster("non_compete", 3),
        _cluster("restrictive_covenant", 3),
    ]
    out = discover_vocabulary_candidates(
        clusters=clusters,
        name_embeddings={
            "indemnification": GROUP1_A,
            "hold_harmless": GROUP1_B,
            "non_compete": GROUP2_A,
            "restrictive_covenant": GROUP2_B,
        },
        similarity_threshold=0.85,
        min_combined_docs=5,
    )
    canonical_terms = sorted(c.canonical_term for c in out)
    # Both groups emit (each has 6 docs combined ≥ 5).
    assert canonical_terms == ["hold_harmless", "non_compete"]


def test_confidence_is_max_pairwise_similarity():
    clusters = [
        _cluster("term_a", 3),
        _cluster("term_b", 3),
    ]
    out = discover_vocabulary_candidates(
        clusters=clusters,
        name_embeddings={"term_a": HIGH_SIM_A, "term_b": HIGH_SIM_B},
        similarity_threshold=0.85,
        min_combined_docs=5,
    )
    assert len(out) == 1
    # Cosine of HIGH_SIM_A . HIGH_SIM_B
    a, b = HIGH_SIM_A, HIGH_SIM_B
    expected = sum(x * y for x, y in zip(a, b)) / (
        math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    )
    assert abs(out[0].confidence - expected) < 1e-6
