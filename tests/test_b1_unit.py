"""B1 / WA-4 + WA-5 — pure-function unit tests.

Covers:
  - triples parser (kb.extraction.triples._parse_triples + factory)
  - resolver (kb.extraction.relationships_resolver.resolve_triples)
  - graph builder (kb.extraction.graph_builder.build_edges_for_file)
  - PPR (kb.query.ppr.personalized_pagerank)
"""

from __future__ import annotations

import os
from contextlib import contextmanager

import pytest

from kb.domain.relationships import RelationshipRecord
from kb.domain.triples import TripleRecord
from kb.extraction.graph_builder import (
    EdgeUpsert,
    LineagePair,
    MentionInUnit,
    build_edges_for_file,
)
from kb.extraction.relationships_resolver import (
    ResolvedRelationship,
    resolve_triples,
)
from kb.extraction.triples import (
    IdentityTripleExtractor,
    TripleCandidate,
    TripleExtractionResult,
    _parse_triples,
    _strip_code_fence,
    make_triple_extractor,
)
from kb.query.ppr import (
    PPRResult,
    build_adjacency_from_edges,
    personalized_pagerank,
)


pytestmark = pytest.mark.asyncio


@contextmanager
def _env(**kwargs):
    prior = {k: os.environ.get(k) for k in kwargs}
    for k, v in kwargs.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in prior.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ===========================================================================
# Triples parser + factory
# ===========================================================================


def test_strip_code_fence_removes_json_marker():
    assert _strip_code_fence('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert _strip_code_fence('```\n{"a": 1}\n```') == '{"a": 1}'
    assert _strip_code_fence('{"a": 1}') == '{"a": 1}'


def test_parse_triples_returns_empty_on_empty_input():
    assert _parse_triples("") == []
    assert _parse_triples("not json") == []


def test_parse_triples_extracts_valid_triples():
    raw = (
        '{"triples": ['
        '{"subject":"Acme","predicate":"supplies to","object":"Vertex","confidence":0.92},'
        '{"subject":"Vertex","predicate":"contracts with","object":"Aakash","confidence":0.85}'
        ']}'
    )
    out = _parse_triples(raw)
    assert len(out) == 2
    assert out[0].subject == "Acme"
    assert out[0].predicate == "supplies to"
    assert out[0].object == "Vertex"
    assert out[0].confidence == pytest.approx(0.92)


def test_parse_triples_drops_empty_components():
    raw = (
        '{"triples": ['
        '{"subject":"","predicate":"x","object":"y"},'
        '{"subject":"a","predicate":"","object":"b"},'
        '{"subject":"valid","predicate":"is","object":"good"}'
        ']}'
    )
    out = _parse_triples(raw)
    assert len(out) == 1
    assert out[0].subject == "valid"


def test_parse_triples_drops_self_loops():
    raw = '{"triples":[{"subject":"X","predicate":"is","object":"X"}]}'
    assert _parse_triples(raw) == []


def test_parse_triples_handles_code_fence_wrapping():
    raw = '```json\n{"triples":[{"subject":"A","predicate":"is","object":"B"}]}\n```'
    out = _parse_triples(raw)
    assert len(out) == 1
    assert out[0].subject == "A"


def test_parse_triples_tolerates_non_list_triples_field():
    raw = '{"triples": "not a list"}'
    assert _parse_triples(raw) == []


def test_parse_triples_skips_unparseable_entries():
    raw = (
        '{"triples": ['
        '"string instead of dict",'
        '{"subject":"a","predicate":"b","object":"c"}'
        ']}'
    )
    out = _parse_triples(raw)
    assert len(out) == 1


@pytest.mark.asyncio
async def test_identity_extractor_returns_empty():
    ex = IdentityTripleExtractor()
    out = await ex.extract(chunk_text="anything")
    assert out.triples == []
    assert ex.model_id == "identity"


def test_factory_identity_when_no_keys():
    with _env(KB_TRIPLES_EXTRACTOR=None, KB_GEMINI_API_KEY=None, KB_ANTHROPIC_API_KEY=None):
        ex = make_triple_extractor()
        assert isinstance(ex, IdentityTripleExtractor)


def test_factory_explicit_identity():
    with _env(KB_TRIPLES_EXTRACTOR="identity"):
        ex = make_triple_extractor()
        assert isinstance(ex, IdentityTripleExtractor)


def test_factory_explicit_gemini_without_key_raises():
    with _env(KB_TRIPLES_EXTRACTOR="gemini", KB_GEMINI_API_KEY=None):
        with pytest.raises(ValueError):
            make_triple_extractor()


def test_factory_unknown_value_raises():
    with _env(KB_TRIPLES_EXTRACTOR="made_up"):
        with pytest.raises(ValueError):
            make_triple_extractor()


# ===========================================================================
# Resolver — triples → relationships
# ===========================================================================


def _triple(
    triple_id: str,
    subject: str,
    predicate: str,
    object_: str,
    confidence: float = 0.5,
    file_id: str = "f1",
    chunk_id: str | None = "c1",
) -> TripleRecord:
    return TripleRecord(
        id=triple_id,
        workspace_id="ws-1",
        file_id=file_id,
        chunk_id=chunk_id,
        subject_text=subject,
        predicate_text=predicate,
        object_text=object_,
        confidence=confidence,
        model_id="test",
        created_at="2026-05-25T00:00:00Z",
    )


@pytest.mark.asyncio
async def test_resolver_empty_input_returns_empty():
    async def lookup(workspace: str, text: str) -> str | None:
        return None
    out = await resolve_triples(triples=[], workspace_id="ws-1", lookup=lookup)
    assert out == []


@pytest.mark.asyncio
async def test_resolver_links_both_ends_when_lookup_succeeds():
    triples = [
        _triple("t1", "Acme", "supplies to", "Vertex"),
    ]
    table = {"acme": "ent-acme", "vertex": "ent-vertex"}

    async def lookup(workspace: str, text: str) -> str | None:
        return table.get(text.lower())

    out = await resolve_triples(triples=triples, workspace_id="ws-1", lookup=lookup)
    assert len(out) == 1
    assert out[0].subject_entity_id == "ent-acme"
    assert out[0].object_entity_id == "ent-vertex"
    assert out[0].predicate == "supplies to"
    assert out[0].triple_ids == ("t1",)


@pytest.mark.asyncio
async def test_resolver_skips_triple_when_subject_unresolved():
    triples = [_triple("t1", "Unknown", "supplies to", "Vertex")]
    table = {"vertex": "ent-vertex"}

    async def lookup(workspace: str, text: str) -> str | None:
        return table.get(text.lower())

    out = await resolve_triples(triples=triples, workspace_id="ws-1", lookup=lookup)
    assert out == []


@pytest.mark.asyncio
async def test_resolver_skips_self_loops_post_resolution():
    """If subj_text != obj_text but both resolve to same entity (alias →
    canonical), drop the triple."""
    triples = [_triple("t1", "M. Ambani", "is", "Mukesh Ambani")]
    table = {"m. ambani": "ent-ambani", "mukesh ambani": "ent-ambani"}

    async def lookup(workspace: str, text: str) -> str | None:
        return table.get(text.lower())

    out = await resolve_triples(triples=triples, workspace_id="ws-1", lookup=lookup)
    assert out == []


@pytest.mark.asyncio
async def test_resolver_aggregates_multiple_triples_into_one_relationship():
    """Two triples backing the same (subj, obj, pred) → one resolved
    relationship with both triple_ids as evidence + MAX confidence."""
    triples = [
        _triple("t1", "Acme", "supplies to", "Vertex", confidence=0.7),
        _triple("t2", "Acme", "supplies to", "Vertex", confidence=0.9),
    ]
    table = {"acme": "ent-acme", "vertex": "ent-vertex"}

    async def lookup(workspace: str, text: str) -> str | None:
        return table.get(text.lower())

    out = await resolve_triples(triples=triples, workspace_id="ws-1", lookup=lookup)
    assert len(out) == 1
    assert set(out[0].triple_ids) == {"t1", "t2"}
    assert out[0].confidence == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_resolver_normalizes_predicate_case_and_whitespace():
    triples = [
        _triple("t1", "Acme", "  Supplies  TO  ", "Vertex"),
        _triple("t2", "Acme", "supplies to", "Vertex"),
    ]
    table = {"acme": "a", "vertex": "v"}

    async def lookup(workspace: str, text: str) -> str | None:
        return table.get(text.lower())

    out = await resolve_triples(triples=triples, workspace_id="ws-1", lookup=lookup)
    # Both triples normalize to "supplies to" → one relationship aggregating both.
    assert len(out) == 1
    assert out[0].predicate == "supplies to"
    assert set(out[0].triple_ids) == {"t1", "t2"}


# ===========================================================================
# Graph builder — relationships + co-mentions + lineage → edges
# ===========================================================================


def _rel(rid: str, subj: str, obj: str, predicate: str = "rel", n_evidence: int = 1) -> RelationshipRecord:
    return RelationshipRecord(
        id=rid, workspace_id="ws-1",
        subject_entity_id=subj, object_entity_id=obj,
        predicate=predicate, confidence=0.5, n_evidence=n_evidence,
        created_at="2026-05-25T00:00:00Z",
        updated_at="2026-05-25T00:00:00Z",
    )


def test_builder_empty_inputs_returns_empty():
    assert build_edges_for_file() == []


def test_builder_relationship_edge():
    rel = _rel("r1", "a", "b", n_evidence=3)
    edges = build_edges_for_file(relationships=[rel])
    assert len(edges) == 1
    edge = edges[0]
    assert edge.edge_kind == "relationship"
    # Canonical src/dst (a < b).
    assert edge.src_entity_id == "a"
    assert edge.dst_entity_id == "b"
    assert edge.weight_delta == 3.0


def test_builder_canonicalizes_src_dst_pair():
    """rel(b → a) should be canonicalized to (a, b) so subsequent merges
    converge on the same row (UNIQUE constraint)."""
    rel = _rel("r1", "z", "a")
    edges = build_edges_for_file(relationships=[rel])
    assert edges[0].src_entity_id == "a"
    assert edges[0].dst_entity_id == "z"


def test_builder_co_mention_edges_within_unit():
    mentions = [
        MentionInUnit(entity_id="a", unit_id="u1"),
        MentionInUnit(entity_id="b", unit_id="u1"),
        MentionInUnit(entity_id="c", unit_id="u1"),
    ]
    edges = build_edges_for_file(mentions_in_units=mentions)
    # 3 entities in same unit → C(3,2) = 3 pairs.
    assert len(edges) == 3
    pairs = {(e.src_entity_id, e.dst_entity_id, e.edge_kind) for e in edges}
    assert pairs == {
        ("a", "b", "co_mention"),
        ("a", "c", "co_mention"),
        ("b", "c", "co_mention"),
    }


def test_builder_co_mention_dedupes_within_call():
    """Same pair in same unit twice (e.g. entity mentioned twice in unit)
    should produce one edge, not two."""
    mentions = [
        MentionInUnit(entity_id="a", unit_id="u1"),
        MentionInUnit(entity_id="a", unit_id="u1"),
        MentionInUnit(entity_id="b", unit_id="u1"),
    ]
    edges = build_edges_for_file(mentions_in_units=mentions)
    assert len(edges) == 1


def test_builder_lineage_edges():
    pairs = [
        LineagePair(parent_entity_id="parent", child_entity_id="child"),
    ]
    edges = build_edges_for_file(lineage_pairs=pairs)
    assert len(edges) == 1
    assert edges[0].edge_kind == "lineage"
    assert edges[0].weight_delta == 2.0  # lineage is structurally strong


def test_builder_three_kinds_for_same_pair_keep_separate_rows():
    """Same (a, b) pair appearing in all three sources → three distinct
    rows (one per kind). The DB UNIQUE constraint is per-kind."""
    rel = _rel("r1", "a", "b")
    mentions = [MentionInUnit(entity_id="a", unit_id="u1"),
                MentionInUnit(entity_id="b", unit_id="u1")]
    lineage = [LineagePair(parent_entity_id="a", child_entity_id="b")]
    edges = build_edges_for_file(
        relationships=[rel],
        mentions_in_units=mentions,
        lineage_pairs=lineage,
    )
    kinds = sorted(e.edge_kind for e in edges)
    assert kinds == ["co_mention", "lineage", "relationship"]


def test_builder_skips_self_loops():
    rel = _rel("r1", "a", "a")  # would fail at DB CHECK, builder filters it
    mentions = [MentionInUnit(entity_id="a", unit_id="u1")]  # singleton → no pair
    lineage = [LineagePair(parent_entity_id="a", child_entity_id="a")]
    edges = build_edges_for_file(
        relationships=[rel], mentions_in_units=mentions, lineage_pairs=lineage,
    )
    assert edges == []


# ===========================================================================
# PPR
# ===========================================================================


def test_ppr_empty_inputs_return_empty():
    assert personalized_pagerank(adjacency={}, seed_entity_ids=[]) == []
    assert personalized_pagerank(adjacency={"a": []}, seed_entity_ids=[]) == []


def test_ppr_unknown_seed_returns_empty():
    adj = {"a": [("b", 1.0)], "b": [("a", 1.0)]}
    out = personalized_pagerank(adjacency=adj, seed_entity_ids=["nonexistent"])
    assert out == []


def test_ppr_seed_scores_higher_than_neighbors():
    """Standard PPR invariant: with default alpha=0.15, the seed should
    have the highest stationary probability in a small graph."""
    adj = build_adjacency_from_edges([
        ("seed", "n1", 1.0),
        ("seed", "n2", 1.0),
        ("n1", "n3", 1.0),
    ])
    out = personalized_pagerank(
        adjacency=adj, seed_entity_ids=["seed"], iterations=50,
    )
    by_id = {r.entity_id: r.score for r in out}
    assert by_id["seed"] > by_id["n1"]
    assert by_id["seed"] > by_id["n2"]
    assert by_id["seed"] > by_id["n3"]


def test_ppr_results_are_sorted_descending():
    adj = build_adjacency_from_edges([
        ("a", "b", 1.0), ("a", "c", 1.0), ("a", "d", 1.0),
        ("b", "c", 1.0), ("b", "d", 1.0),
    ])
    out = personalized_pagerank(adjacency=adj, seed_entity_ids=["a"], iterations=20)
    scores = [r.score for r in out]
    assert scores == sorted(scores, reverse=True)


def test_ppr_higher_weight_edges_get_more_mass():
    """Compare two structurally-identical graphs that differ only in
    edge weights — the heavier-weighted neighbor should rank higher."""
    light = build_adjacency_from_edges([
        ("seed", "weak", 1.0),
        ("seed", "strong", 10.0),
    ])
    out = personalized_pagerank(adjacency=light, seed_entity_ids=["seed"], iterations=50)
    by_id = {r.entity_id: r.score for r in out}
    assert by_id["strong"] > by_id["weak"]


def test_ppr_top_k_truncates_correctly():
    adj = build_adjacency_from_edges([
        ("seed", "a", 1.0), ("seed", "b", 1.0), ("seed", "c", 1.0),
        ("seed", "d", 1.0), ("seed", "e", 1.0),
    ])
    out = personalized_pagerank(
        adjacency=adj, seed_entity_ids=["seed"], iterations=20, top_k=3,
    )
    assert len(out) == 3


def test_ppr_convergence_tolerance_short_circuits():
    """A small fully-connected graph converges within a few iterations.
    Passing iterations=1000 should still terminate fast because the
    tolerance check breaks the loop early."""
    adj = build_adjacency_from_edges([("a", "b", 1.0)])
    out = personalized_pagerank(
        adjacency=adj, seed_entity_ids=["a"],
        iterations=1000, tolerance=1e-3,
    )
    assert len(out) == 2


def test_build_adjacency_from_edges_is_undirected():
    adj = build_adjacency_from_edges([("a", "b", 1.0), ("b", "c", 2.0)])
    assert ("b", 1.0) in adj["a"]
    assert ("a", 1.0) in adj["b"]
    assert ("c", 2.0) in adj["b"]
    assert ("b", 2.0) in adj["c"]
