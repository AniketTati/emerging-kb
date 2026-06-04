"""Phase 3a (§6.9) — typed-relationship Knowledge-Graph query path.

The workspace has a clean **typed** relationship layer (`relationships`:
subject —predicate→ object, with confidence + n_evidence + per-edge provenance in
`relationship_evidence`) that the query head never queried — T-mode only ran
PageRank over proximity `graph_edges` and *boosted RAG hits*. So "who are Acme's
counterparties / subsidiaries / signatories; where is Acme located" fell back to
fuzzy retrieval even though the graph holds the exact typed edges.

This module realizes §6.9's *"filter output by requested type / edge role, not
raw proximity"* directly over those typed edges:

  resolve seed entity → traverse `relationships` (1 hop, both directions) →
  filter by the requested predicate (canonicalized, so "located in" / "is located
  in" / "is in" all match) + target entity type → surface a STRUCTURED, CITED
  answer (each edge with its evidence docs + confidence), with a §6.9 lower-
  confidence flag for single-evidence edges. Falls back (returns None) when no
  seed / no edge resolves, so the caller degrades to PPR + RAG (I2).

Provenance, not proximity: every surfaced edge is auditable (which relationship,
how many evidence sources, which files) — the KG analog of Q-mode's §6.6 audit
envelope.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from kb.domain.relationships import (
    list_relationships_for_entity,
    read_evidence_for_relationship,
)


# ---------------------------------------------------------------------------
# Predicate canonicalization (KG-2) — fold fragmented surface predicates
# ---------------------------------------------------------------------------

# Canonical predicate -> the surface phrases (substring/normalized match) that
# map to it. Extraction emits free-text predicates ("located in", "is located
# in", "is in"), so a filter must canonicalize both sides before comparing.
_PREDICATE_SYNONYMS: dict[str, tuple[str, ...]] = {
    "located_in": ("located in", "is located in", "is in", "is from",
                   "based in", "headquartered in", "registered in"),
    "works_for": ("works for", "worked at", "employed by", "employee of",
                  "works at"),
    "has_role": ("is", "serves as", "holds the position", "appointed as",
                 "designated as", "acts as"),
    "has_subsidiary": ("has subsidiary", "subsidiary of", "parent of",
                       "owns subsidiary"),
    "owns": ("is beneficial owner of", "beneficial owner", "owns", "owned by",
             "holds", "shareholder of"),
    "counterparty": ("is a counterparty to", "counterparty", "counterparty to",
                     "transacts with", "trades with"),
    "has_account_with": ("has account with", "banks with", "account with",
                         "maintains account"),
    "signed_by": ("signed by", "signed for", "has signatory", "signatory",
                  "authorised signatory", "authorized signatory"),
    "regulated_by": ("regulated by", "regulated as", "supervised by"),
    "issued_by": ("issued by", "issuer"),
    "has_branch": ("has branch", "branch of", "branch"),
    "pays": ("pays", "paid", "payment to", "remits to"),
    "cc": ("cc'd", "copied", "cc"),
    "studied_at": ("studied at", "graduated from", "alumnus of"),
}

# Leading verb/article noise stripped before normalized comparison.
_PRED_LEAD_RE = re.compile(r"^(is|are|was|were|has|have|had|a|an|the)\s+", re.I)


def canonicalize_predicate(predicate: str | None) -> str:
    """Fold a surface predicate to a canonical key. Unknown predicates collapse
    to a normalized form (lowercase, leading is/has/the stripped, spaces→_), so
    even an un-mapped predicate matches its own variants.

    EXACT synonym match wins first (so the bare role-assignment predicate "is"
    → has_role, but "is a counterparty to" → counterparty, not has_role); only
    then a MULTI-WORD phrase substring (longest wins) — single-word phrases
    like "is" never match as a substring (that bug mapped everything to is/role)."""
    if not predicate:
        return ""
    p = predicate.strip().lower()
    # 1. exact synonym match.
    for canon, phrases in _PREDICATE_SYNONYMS.items():
        if p in phrases:
            return canon
    # 2. multi-word phrase substring (longest phrase wins).
    best: tuple[int, str] | None = None
    for canon, phrases in _PREDICATE_SYNONYMS.items():
        for ph in phrases:
            if " " in ph and ph in p and (best is None or len(ph) > best[0]):
                best = (len(ph), canon)
    if best is not None:
        return best[1]
    # 3. Fallback: strip a leading verb/article, normalize separators.
    p = _PRED_LEAD_RE.sub("", p)
    return re.sub(r"[^a-z0-9]+", "_", p).strip("_")


# ---------------------------------------------------------------------------
# Relation-intent detection (KG-1) — what predicate / target type is asked for
# ---------------------------------------------------------------------------

# Query keyword -> canonical predicate it implies.
_QUERY_PREDICATE_HINTS: tuple[tuple[str, str], ...] = (
    ("counterpart", "counterparty"),
    ("subsidiar", "has_subsidiary"),
    ("parent compan", "has_subsidiary"),
    ("beneficial owner", "owns"),
    ("owner", "owns"),
    ("shareholder", "owns"),
    ("signator", "signed_by"),
    ("signed", "signed_by"),
    ("regulat", "regulated_by"),
    ("account with", "has_account_with"),
    ("bank with", "has_account_with"),
    ("located", "located_in"),
    ("location", "located_in"),
    ("based", "located_in"),
    ("headquarter", "located_in"),
    ("branch", "has_branch"),
    ("works for", "works_for"),
    ("work at", "works_for"),
    ("employ", "works_for"),
    ("issued by", "issued_by"),
    ("issuer", "issued_by"),
)

# Query keyword -> NER entity_type to keep among the neighbors. NOTE: a bare
# "who" is deliberately ABSENT — in business questions "who are the
# counterparties" usually means ORGs, so a "who"→PERSON filter would wrongly
# drop them. Only EXPLICIT type words filter.
_QUERY_TYPE_HINTS: tuple[tuple[str, str], ...] = (
    ("which people", "PERSON"),
    ("which person", "PERSON"),
    ("which individual", "PERSON"),
    ("which compan", "ORG"),
    ("which organi", "ORG"),
    ("which firm", "ORG"),
    ("what location", "LOC"),
    ("which location", "LOC"),
)


@dataclass(frozen=True)
class RelationIntent:
    predicate: str | None = None      # canonical predicate to filter on
    target_types: tuple[str, ...] = ()  # NER types to keep among neighbors


def detect_relation_intent(query: str) -> RelationIntent:
    """Light, deterministic extractor: which predicate + neighbor type the query
    asks for. Both are FILTERS — when neither resolves we still surface ALL of
    the seed's typed relations (a useful 'what do we know about X' answer)."""
    q = (query or "").lower()
    predicate = next((p for kw, p in _QUERY_PREDICATE_HINTS if kw in q), None)
    types = tuple({t for kw, t in _QUERY_TYPE_HINTS if kw in q})
    return RelationIntent(predicate=predicate, target_types=types)


# ---------------------------------------------------------------------------
# Traversal + structured answer (KG-1 + KG-4 envelope)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KgEdge:
    subject: str
    predicate: str
    object: str
    neighbor_id: str
    neighbor_type: str
    direction: str               # 'out' (seed=subject) | 'in' (seed=object)
    confidence: float
    n_evidence: int
    evidence_file_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class KgAnswer:
    seed_id: str
    seed_name: str
    edges: tuple[KgEdge, ...] = ()
    intent_predicate: str | None = None
    notes: tuple[str, ...] = ()

    @property
    def n_edges(self) -> int:
        return len(self.edges)

    @property
    def n_single_evidence(self) -> int:
        return sum(1 for e in self.edges if e.n_evidence < 2)

    @property
    def file_ids(self) -> list[str]:
        out: list[str] = []
        for e in self.edges:
            for f in e.evidence_file_ids:
                if f not in out:
                    out.append(f)
        return out


async def _resolve_entities(
    conn: Any, workspace_id: str, ids: list[str],
) -> dict[str, tuple[str, str]]:
    """entity_id -> (canonical_name, entity_type) for non-merged entities."""
    if not ids:
        return {}
    try:
        cur = await conn.execute(
            "SELECT id::text, canonical_name, entity_type FROM canonical_entities "
            "WHERE workspace_id = %s AND merged_into IS NULL AND id::text = ANY(%s)",
            (workspace_id, sorted(set(ids))),
        )
        return {str(r[0]): (str(r[1]), str(r[2] or "")) for r in await cur.fetchall()}
    except Exception:  # noqa: BLE001
        return {}


async def build_kg_answer(
    conn: Any,
    *,
    workspace_id: str,
    seed_ids: list[str],
    intent: RelationIntent,
    max_edges: int = 25,
    relax: bool = True,
) -> KgAnswer | None:
    """Traverse the typed `relationships` from the best seed (1 hop, both
    directions), filter by the intent predicate + neighbor type, attach
    provenance, and return a structured `KgAnswer`. None when nothing usable
    resolves (→ caller falls back to PPR/RAG).

    `relax=True` (committed T-mode): if the specific predicate/type matches
    nothing, fall back to ALL of the seed's relations + a note. `relax=False`
    (opportunistic augmentation of a non-graph mode): require the specific match,
    so we never inject off-topic relations into an unrelated answer."""
    if conn is None or not seed_ids:
        return None

    seed_names = await _resolve_entities(conn, workspace_id, seed_ids)

    # Pick the seed with the most typed relations (the one the question is
    # likely about); ties → first.
    best: KgAnswer | None = None
    for seed_id in seed_ids:
        try:
            rels = await list_relationships_for_entity(
                conn, workspace_id=workspace_id, entity_id=seed_id,
                direction="both", limit=200,
            )
        except Exception:  # noqa: BLE001
            rels = []
        if not rels:
            continue

        # Resolve every neighbor (the non-seed end) to name + type.
        neighbor_ids = [
            (r.object_entity_id if r.subject_entity_id == seed_id
             else r.subject_entity_id)
            for r in rels
        ]
        all_named = await _resolve_entities(
            conn, workspace_id, neighbor_ids + [seed_id],
        )
        seed_name = all_named.get(seed_id, seed_names.get(seed_id, ("", "")))[0]
        if not seed_name:
            seed_name = seed_names.get(seed_id, ("", ""))[0] or "(entity)"

        # Candidate edges (the resolved-neighbor relations) — provenance is
        # fetched only for the FINAL capped set, not all 200 (cheap).
        cands: list[tuple[Any, bool, str, str, str]] = []
        for r in rels:
            out_dir = r.subject_entity_id == seed_id
            nbr_id = r.object_entity_id if out_dir else r.subject_entity_id
            nbr = all_named.get(nbr_id)
            if nbr is None:           # dangling neighbor (unresolved) → skip
                continue
            cands.append((r, out_dir, nbr_id, nbr[0], nbr[1]))
        if not cands:
            continue

        def _matches(c: tuple) -> bool:
            r, _o, _i, _n, nbr_type = c
            if intent.predicate and (
                canonicalize_predicate(r.predicate) != intent.predicate
            ):
                return False
            if intent.target_types and (
                (nbr_type or "").upper() not in intent.target_types
            ):
                return False
            return True

        # Filter by intent. On an empty match: RELAX to all relations (committed
        # T-mode — better to show what we know than fall to RAG; mirrors T2's
        # AND-salvage) OR, when relax=False (opportunistic augmentation), skip
        # the seed so we never inject off-topic relations into an unrelated answer.
        filtered = [c for c in cands if _matches(c)]
        relax_note: str | None = None
        if filtered:
            chosen = filtered
        elif relax:
            chosen = cands
            if intent.predicate or intent.target_types:
                relax_note = (
                    f"no relation matching the specific ask was found for "
                    f"{seed_name}; showing all {len(cands)} known relation(s)"
                )
        else:
            continue
        chosen.sort(key=lambda c: (c[0].n_evidence, c[0].confidence), reverse=True)
        chosen = chosen[:max_edges]

        edges: list[KgEdge] = []
        for r, out_dir, nbr_id, nbr_name, nbr_type in chosen:
            try:
                ev = await read_evidence_for_relationship(
                    conn, relationship_id=r.id, limit=10,
                )
                ev_files = tuple({e.file_id for e in ev if e.file_id})
            except Exception:  # noqa: BLE001
                ev_files = ()
            edges.append(KgEdge(
                subject=seed_name if out_dir else nbr_name,
                predicate=r.predicate,
                object=nbr_name if out_dir else seed_name,
                neighbor_id=nbr_id, neighbor_type=nbr_type,
                direction="out" if out_dir else "in",
                confidence=r.confidence, n_evidence=r.n_evidence,
                evidence_file_ids=ev_files,
            ))

        if not edges:
            continue
        notes: list[str] = []
        if relax_note:
            notes.append(relax_note)
        n_single = sum(1 for e in edges if e.n_evidence < 2)
        if n_single:
            notes.append(
                f"{n_single} of {len(edges)} relationships rest on a single "
                f"source — treat as lower-confidence (graph not yet verified)"
            )
        ans = KgAnswer(
            seed_id=seed_id, seed_name=seed_name, edges=tuple(edges),
            intent_predicate=intent.predicate, notes=tuple(notes),
        )
        if best is None or ans.n_edges > best.n_edges:
            best = ans

    return best


def format_kg_snippet(ans: KgAnswer) -> str:
    """Human-readable structured rendering for the generator — the typed edges,
    each with its support, plus the §6.9 lower-confidence note. The number/edge
    is never bare: it ships with evidence count + (via the hit) source docs."""
    lines = [
        f"Knowledge-graph relations for {ans.seed_name} "
        f"(from {ans.n_edges} typed relationship(s)):"
    ]
    for e in ans.edges:
        arrow = "→" if e.direction == "out" else "←"
        support = (
            f"[{e.n_evidence} source{'s' if e.n_evidence != 1 else ''}]"
        )
        if e.direction == "out":
            lines.append(f"  {e.subject} {arrow} {e.predicate} {arrow} {e.object} {support}")
        else:
            lines.append(f"  {e.object} → {e.predicate} → {e.subject} {support}")
    for n in ans.notes:
        lines.append(f"Note: {n}")
    return "\n".join(lines)
