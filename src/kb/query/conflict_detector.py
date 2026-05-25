"""B2 / WA-6 — Design 2 conflict detection + resolution cascade.

Pure-function. Consumed by the generator (kb/query/generate.py) BEFORE
composing the answer. Wired into WA-10's planner output path.

Contract:

    detect_conflicts(candidates) -> list[ConflictGroup]
      Group candidates by (entity_id, predicate). Groups with 2+ distinct
      `value`s are conflicts. Single-value groups are returned with
      kind='consensus' (no conflict, but the grouping is still useful for
      the generator's authority + status annotations).

    resolve_conflict(group) -> ResolvedConflict
      Apply Design 2 §"Resolution rules" in order:
        1. doc_chain check (supersession)
        2. doc_status filter (drop superseded/draft/archived/retracted)
        3. authority dominates (gap >= 0.3 → higher wins)
        4. recency tiebreaker (within 0.3 gap → most recent wins)
        5. unresolvable (surface both)

      Returns a `ResolvedConflict` with the picked value + which rule
      fired ('chain'|'status'|'authority'|'recency'|'unresolved') and
      contextual citations (the losing candidates that the answer still
      surfaces for transparency).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable


# Authority gap that triggers rule 3 (Design 2 §"Resolution rules").
DEFAULT_AUTHORITY_DOMINANCE_GAP = 0.30


@dataclass(frozen=True)
class FactCandidate:
    """One asserted fact from one source doc — what detect_conflicts
    consumes. Built by the generator from retrieved hits + their docs'
    source_authority / doc_status / date metadata."""
    doc_id: str
    entity_id: str
    predicate: str        # e.g. "indemnification_cap"
    value: str            # the asserted value as a string ("$25M")
    authority: float      # files.source_authority (0.0-1.0)
    doc_status: str       # files.doc_status ('live'/'superseded'/...)
    # ISO 8601 date or None when unknown — used by rule 4 recency tiebreak.
    doc_date_iso: str | None = None
    # Optional doc-chain context — if set + chain has current_version_id,
    # rule 1 fires.
    chain_id: str | None = None
    chain_current_version_doc_id: str | None = None
    # The retrieval-side reference back to the hit (for citation).
    hit_id: str | None = None


@dataclass(frozen=True)
class ConflictGroup:
    """A group of candidates sharing (entity_id, predicate). When
    `kind='conflict'` there are 2+ distinct value strings; when
    `kind='consensus'` all candidates agree."""
    entity_id: str
    predicate: str
    kind: str    # 'conflict' | 'consensus'
    candidates: tuple[FactCandidate, ...]

    @property
    def distinct_values(self) -> list[str]:
        # Order-preserving distinct list of value strings.
        seen: set[str] = set()
        out: list[str] = []
        for c in self.candidates:
            if c.value not in seen:
                seen.add(c.value)
                out.append(c.value)
        return out


@dataclass(frozen=True)
class ResolvedConflict:
    """Output of resolve_conflict: picked value + which rule fired +
    losing candidates that the answer should surface for transparency."""
    entity_id: str
    predicate: str
    # 'chain' | 'status' | 'authority' | 'recency' | 'unresolved' | 'consensus'
    resolution: str
    picked_value: str | None
    picked_candidate: FactCandidate | None
    losers: tuple[FactCandidate, ...] = field(default_factory=tuple)
    notes: str | None = None


# ===========================================================================
# Detection: group by (entity, predicate) → identify conflicts
# ===========================================================================


def detect_conflicts(
    candidates: Iterable[FactCandidate],
) -> list[ConflictGroup]:
    """Group candidates by (entity_id, predicate). Groups where the set
    of distinct values has ≥ 2 elements are kind='conflict'; single-value
    groups are kind='consensus'.

    Returns a list of ConflictGroup, sorted deterministically:
    conflicts first (so the caller can short-circuit on the most
    important ones), then by (entity_id, predicate)."""
    by_key: dict[tuple[str, str], list[FactCandidate]] = defaultdict(list)
    for c in candidates:
        if not c.predicate or not c.entity_id:
            continue
        by_key[(c.entity_id, c.predicate)].append(c)

    groups: list[ConflictGroup] = []
    for (eid, pred), cands in by_key.items():
        distinct_vals = {c.value for c in cands}
        kind = "conflict" if len(distinct_vals) >= 2 else "consensus"
        groups.append(ConflictGroup(
            entity_id=eid,
            predicate=pred,
            kind=kind,
            candidates=tuple(cands),
        ))

    # Conflicts first, then deterministic ordering.
    groups.sort(key=lambda g: (
        0 if g.kind == "conflict" else 1, g.entity_id, g.predicate,
    ))
    return groups


# ===========================================================================
# Resolution: apply Design 2 §"Resolution rules" in order
# ===========================================================================


# doc_status priority for rule 2 — 'live' is preferred; the others are
# downranked / dropped from primary citation.
_SUPERSEDED_STATUSES = {"superseded", "archived", "retracted", "draft"}


def _parse_iso(d: str | None) -> datetime | None:
    if not d:
        return None
    try:
        # Accept 'YYYY-MM-DD' and full ISO 8601 with tz.
        return datetime.fromisoformat(d.replace("Z", "+00:00"))
    except ValueError:
        return None


def resolve_conflict(
    group: ConflictGroup,
    *,
    authority_dominance_gap: float = DEFAULT_AUTHORITY_DOMINANCE_GAP,
) -> ResolvedConflict:
    """Apply the 5-rule cascade to a ConflictGroup. Pure-function.

    A 'consensus' group passes through with resolution='consensus' and
    the (single-value) picked = the first candidate.
    """
    if group.kind == "consensus":
        first = group.candidates[0]
        return ResolvedConflict(
            entity_id=group.entity_id,
            predicate=group.predicate,
            resolution="consensus",
            picked_value=first.value,
            picked_candidate=first,
            losers=group.candidates[1:],
            notes=None,
        )

    candidates = list(group.candidates)

    # --- Rule 1: Doc-chain check ----------------------------------------
    # When ≥2 candidates share a chain_id, the chain's current_version_id
    # wins. (Not a conflict — supersession.)
    chains: dict[str, list[FactCandidate]] = defaultdict(list)
    for c in candidates:
        if c.chain_id:
            chains[c.chain_id].append(c)
    for chain_id, in_chain in chains.items():
        if len(in_chain) < 2:
            continue
        # Find the candidate whose doc_id == chain_current_version_doc_id.
        # If no candidate in the chain has the chain's current_version
        # field set, skip the rule.
        current_doc_id = next(
            (c.chain_current_version_doc_id for c in in_chain
             if c.chain_current_version_doc_id),
            None,
        )
        if not current_doc_id:
            continue
        picked = next(
            (c for c in in_chain if c.doc_id == current_doc_id), None,
        )
        if picked is None:
            continue
        losers = tuple(c for c in candidates if c is not picked)
        return ResolvedConflict(
            entity_id=group.entity_id,
            predicate=group.predicate,
            resolution="chain",
            picked_value=picked.value,
            picked_candidate=picked,
            losers=losers,
            notes=f"chain_id={chain_id}: current version supersedes prior",
        )

    # --- Rule 2: Status filter ------------------------------------------
    # Drop superseded/draft/archived/retracted from primary citation.
    # If exactly one 'live' candidate remains AND its value is unique,
    # it wins; otherwise we fall through to rule 3 with the filtered set.
    live = [c for c in candidates if c.doc_status == "live"]
    if live and len(live) < len(candidates):
        live_values = {c.value for c in live}
        if len(live_values) == 1:
            picked = live[0]
            losers = tuple(c for c in candidates if c is not picked)
            return ResolvedConflict(
                entity_id=group.entity_id,
                predicate=group.predicate,
                resolution="status",
                picked_value=picked.value,
                picked_candidate=picked,
                losers=losers,
                notes="non-live candidates filtered out of primary citation",
            )
        # Multiple live values disagree → continue to rule 3 over `live`.
        candidates = live

    # --- Rule 3: Authority dominates -----------------------------------
    # Sort by authority desc. If gap to next >= gap threshold, top wins.
    sorted_by_auth = sorted(candidates, key=lambda c: c.authority, reverse=True)
    top = sorted_by_auth[0]
    if len(sorted_by_auth) >= 2:
        gap = top.authority - sorted_by_auth[1].authority
        if gap >= authority_dominance_gap:
            losers = tuple(c for c in group.candidates if c is not top)
            return ResolvedConflict(
                entity_id=group.entity_id,
                predicate=group.predicate,
                resolution="authority",
                picked_value=top.value,
                picked_candidate=top,
                losers=losers,
                notes=(
                    f"authority gap {gap:.2f} >= {authority_dominance_gap:.2f}; "
                    f"higher-authority source wins"
                ),
            )

    # --- Rule 4: Recency tiebreaker -------------------------------------
    # Among candidates within the authority gap, pick the most recent.
    dated = [
        (c, _parse_iso(c.doc_date_iso)) for c in candidates
    ]
    dated_known = [(c, d) for c, d in dated if d is not None]
    if dated_known:
        # Most recent first.
        dated_known.sort(key=lambda cd: cd[1], reverse=True)
        recency_winner, winner_date = dated_known[0]
        # If multiple candidates share the same date (no tiebreaker possible),
        # treat as unresolvable. Otherwise the most-recent wins.
        same_date_count = sum(
            1 for c, d in dated_known if d == winner_date
            and c.value != recency_winner.value
        )
        if same_date_count == 0:
            losers = tuple(c for c in group.candidates if c is not recency_winner)
            return ResolvedConflict(
                entity_id=group.entity_id,
                predicate=group.predicate,
                resolution="recency",
                picked_value=recency_winner.value,
                picked_candidate=recency_winner,
                losers=losers,
                notes=(
                    f"authority within gap; most-recent doc "
                    f"({recency_winner.doc_date_iso}) wins"
                ),
            )

    # --- Rule 5: Unresolvable -------------------------------------------
    # Surface both. picked = None means generator must render the
    # "two contradictory values" template (Design 2 §"Generation behavior").
    return ResolvedConflict(
        entity_id=group.entity_id,
        predicate=group.predicate,
        resolution="unresolved",
        picked_value=None,
        picked_candidate=None,
        losers=tuple(group.candidates),
        notes="authority + recency both ambiguous; surface side-by-side",
    )


def resolve_all(
    candidates: Iterable[FactCandidate],
    *,
    authority_dominance_gap: float = DEFAULT_AUTHORITY_DOMINANCE_GAP,
) -> list[ResolvedConflict]:
    """End-to-end: detect groups + resolve each. The generator calls this
    once per query then writes any 'unresolved' results to fact_conflicts."""
    groups = detect_conflicts(candidates)
    return [
        resolve_conflict(g, authority_dominance_gap=authority_dominance_gap)
        for g in groups
    ]
