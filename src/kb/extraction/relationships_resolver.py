"""B1 / WA-4 — triples → entity-id relationships resolver (arch §5 stage 16).

Pure-function. Takes the file's extracted_triples + a callable to
deterministically look up an entity_id by (workspace, text, type=?) and
returns a list of ResolvedRelationship rows ready to upsert.

Resolution policy (Wave A — keep it cheap):
  - Look up subject_text + object_text via DETERMINISTIC match only
    (Phase 7's find_entity_deterministic by lower(canonical_name)).
  - If both ends resolve → emit one ResolvedRelationship + carry the
    triple_id as evidence.
  - If either end doesn't resolve → skip the triple (no entity yet,
    nothing to link to).

Predicate normalization: lowercase + strip. Wave B will cluster
predicates via embeddings to dedupe synonyms ("supplies" ↔ "provides").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable

from kb.domain.triples import TripleRecord


@dataclass(frozen=True)
class ResolvedRelationship:
    subject_entity_id: str
    object_entity_id: str
    predicate: str
    confidence: float
    # Evidence triples that backed this resolution.
    triple_ids: tuple[str, ...] = field(default_factory=tuple)
    # First file_id + chunk_id we saw the triple in (for the evidence row).
    file_id: str | None = None
    chunk_id: str | None = None


# Type alias for the lookup callable the resolver depends on.
# Signature: (workspace_id, text) -> entity_id | None
EntityLookup = Callable[[str, str], Awaitable[str | None]]


def _normalize_predicate(p: str) -> str:
    return " ".join(p.lower().split())


async def resolve_triples(
    *,
    triples: list[TripleRecord],
    workspace_id: str,
    lookup: EntityLookup,
) -> list[ResolvedRelationship]:
    """Resolve each triple's subj/obj texts to entity ids via the provided
    lookup callable. Skip triples where either end can't be resolved.
    Aggregate by (subj_id, obj_id, normalized_predicate) — multiple
    triples backing the same logical relationship contribute as evidence
    and bump confidence to the max."""
    by_key: dict[tuple[str, str, str], ResolvedRelationship] = {}

    for tr in triples:
        subj_id = await lookup(workspace_id, tr.subject_text)
        if subj_id is None:
            continue
        obj_id = await lookup(workspace_id, tr.object_text)
        if obj_id is None:
            continue
        if subj_id == obj_id:
            continue  # self-loop disallowed by DB CHECK + relationships table

        pred = _normalize_predicate(tr.predicate_text)
        if not pred:
            continue

        key = (subj_id, obj_id, pred)
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = ResolvedRelationship(
                subject_entity_id=subj_id,
                object_entity_id=obj_id,
                predicate=pred,
                confidence=tr.confidence,
                triple_ids=(tr.id,),
                file_id=tr.file_id,
                chunk_id=tr.chunk_id,
            )
        else:
            by_key[key] = ResolvedRelationship(
                subject_entity_id=subj_id,
                object_entity_id=obj_id,
                predicate=pred,
                confidence=max(existing.confidence, tr.confidence),
                triple_ids=existing.triple_ids + (tr.id,),
                file_id=existing.file_id,
                chunk_id=existing.chunk_id,
            )

    return list(by_key.values())
