"""B1 / WA-5 — graph_edges derivation (arch §5 stage 17).

Pure-function. Takes three derived sources for ONE file and emits the
graph edges that should be UPSERTed:

  1. Relationships         → edge_kind='relationship', weight per evidence
  2. Mention co-occurrence → edge_kind='co_mention', within same atomic_unit
  3. Lineage parent/child  → edge_kind='lineage'

All edges are undirected in semantics but stored directionally (the repo
makes both queries via OR clauses). The builder canonicalizes (src, dst)
so the pair is order-stable: lexicographically-smaller id is src.

The builder takes raw data (lists of dicts/dataclasses); the worker
stage is responsible for the DB calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from kb.domain.graph import EDGE_KINDS
from kb.domain.relationships import RelationshipRecord


@dataclass(frozen=True)
class EdgeUpsert:
    src_entity_id: str
    dst_entity_id: str
    edge_kind: str
    weight_delta: float
    source_ref: dict


@dataclass(frozen=True)
class MentionInUnit:
    """One entity mention inside an atomic_unit (or in any container that
    suggests co-occurrence). Builder pairs entities from the same unit_id
    into co-occurrence edges."""
    entity_id: str
    unit_id: str


@dataclass(frozen=True)
class LineagePair:
    """One parent-child pair from extracted_entities.lineage_path."""
    parent_entity_id: str
    child_entity_id: str
    relationship_kind: str = "contains"  # or 'part_of'


def _canonical_pair(a: str, b: str) -> tuple[str, str]:
    """Order-stable src/dst pair — smaller id is src. The UNIQUE constraint
    on (workspace, src, dst, kind) needs this or we'd get two rows per
    undirected edge."""
    return (a, b) if a < b else (b, a)


def build_edges_for_file(
    *,
    relationships: list[RelationshipRecord] | None = None,
    mentions_in_units: list[MentionInUnit] | None = None,
    lineage_pairs: list[LineagePair] | None = None,
) -> list[EdgeUpsert]:
    """Produce the deduplicated list of EdgeUpsert from the three sources.

    Multiple sources can produce the same logical undirected pair (e.g.
    "X relates to Y" + "X co-mentioned with Y"); each is stored as a
    SEPARATE row (different edge_kind) so the kind is preserved for
    UI filtering + PPR weighting per kind."""
    edges: list[EdgeUpsert] = []
    seen_in_call: set[tuple[str, str, str]] = set()

    # 1) Relationships → 'relationship' edges
    for rel in relationships or []:
        if rel.subject_entity_id == rel.object_entity_id:
            continue
        src, dst = _canonical_pair(rel.subject_entity_id, rel.object_entity_id)
        key = (src, dst, "relationship")
        if key in seen_in_call:
            continue
        seen_in_call.add(key)
        edges.append(EdgeUpsert(
            src_entity_id=src,
            dst_entity_id=dst,
            edge_kind="relationship",
            # Use the relationship's n_evidence so frequently-attested
            # relations contribute more to PPR.
            weight_delta=max(1.0, float(rel.n_evidence)),
            source_ref={"kind": "rel", "id": rel.id, "predicate": rel.predicate},
        ))

    # 2) Mention co-occurrence → 'co_mention' edges
    # Pair every two distinct entities mentioned in the same unit.
    if mentions_in_units:
        by_unit: dict[str, list[str]] = {}
        for m in mentions_in_units:
            by_unit.setdefault(m.unit_id, []).append(m.entity_id)
        for unit_id, entity_ids in by_unit.items():
            unique = sorted(set(entity_ids))
            for i, a in enumerate(unique):
                for b in unique[i + 1:]:
                    src, dst = _canonical_pair(a, b)
                    key = (src, dst, "co_mention")
                    if key in seen_in_call:
                        continue
                    seen_in_call.add(key)
                    edges.append(EdgeUpsert(
                        src_entity_id=src,
                        dst_entity_id=dst,
                        edge_kind="co_mention",
                        weight_delta=1.0,
                        source_ref={"kind": "mention", "unit_id": unit_id},
                    ))

    # 3) Lineage → 'lineage' edges
    for lp in lineage_pairs or []:
        if lp.parent_entity_id == lp.child_entity_id:
            continue
        src, dst = _canonical_pair(lp.parent_entity_id, lp.child_entity_id)
        key = (src, dst, "lineage")
        if key in seen_in_call:
            continue
        seen_in_call.add(key)
        edges.append(EdgeUpsert(
            src_entity_id=src,
            dst_entity_id=dst,
            edge_kind="lineage",
            weight_delta=2.0,  # lineage relations are structurally strong
            source_ref={
                "kind": "lineage",
                "parent": lp.parent_entity_id,
                "child": lp.child_entity_id,
                "rel_kind": lp.relationship_kind,
            },
        ))

    return edges
