"""B2 / WA-6 — pure-function unit tests for the Design 2 conflict cascade.

Covers `kb.query.conflict_detector`:

  - detect_conflicts: grouping by (entity_id, predicate); consensus vs. conflict
  - resolve_conflict: each of the 5 cascade rules in isolation, plus
    consensus passthrough
  - resolve_all: end-to-end (detect + resolve) over a mixed input

Pure-function only — no DB. Repo + endpoint coverage lives in test_b2_api.py.
"""

from __future__ import annotations

import pytest

from kb.query.conflict_detector import (
    DEFAULT_AUTHORITY_DOMINANCE_GAP,
    ConflictGroup,
    FactCandidate,
    ResolvedConflict,
    _parse_iso,
    detect_conflicts,
    resolve_all,
    resolve_conflict,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cand(
    *,
    doc_id: str = "doc1",
    entity_id: str = "ent1",
    predicate: str = "indemnification_cap",
    value: str = "$25M",
    authority: float = 0.5,
    doc_status: str = "live",
    doc_date_iso: str | None = None,
    chain_id: str | None = None,
    chain_current_version_doc_id: str | None = None,
) -> FactCandidate:
    return FactCandidate(
        doc_id=doc_id,
        entity_id=entity_id,
        predicate=predicate,
        value=value,
        authority=authority,
        doc_status=doc_status,
        doc_date_iso=doc_date_iso,
        chain_id=chain_id,
        chain_current_version_doc_id=chain_current_version_doc_id,
    )


# ===========================================================================
# detect_conflicts — grouping
# ===========================================================================


def test_detect_groups_consensus_when_all_agree():
    cands = [
        _cand(doc_id="a", value="$25M"),
        _cand(doc_id="b", value="$25M"),
    ]
    groups = detect_conflicts(cands)
    assert len(groups) == 1
    assert groups[0].kind == "consensus"
    assert groups[0].distinct_values == ["$25M"]


def test_detect_flags_conflict_when_values_disagree():
    cands = [
        _cand(doc_id="a", value="$25M"),
        _cand(doc_id="b", value="$50M"),
    ]
    groups = detect_conflicts(cands)
    assert len(groups) == 1
    assert groups[0].kind == "conflict"
    assert set(groups[0].distinct_values) == {"$25M", "$50M"}


def test_detect_separates_distinct_keys():
    cands = [
        _cand(entity_id="A", predicate="x", value="1"),
        _cand(entity_id="A", predicate="y", value="2"),
        _cand(entity_id="B", predicate="x", value="3"),
    ]
    groups = detect_conflicts(cands)
    assert len(groups) == 3
    keys = {(g.entity_id, g.predicate) for g in groups}
    assert keys == {("A", "x"), ("A", "y"), ("B", "x")}


def test_detect_skips_empty_entity_or_predicate():
    cands = [
        _cand(entity_id="", predicate="x", value="1"),
        _cand(entity_id="A", predicate="", value="2"),
        _cand(entity_id="A", predicate="x", value="3"),
    ]
    groups = detect_conflicts(cands)
    assert len(groups) == 1
    assert groups[0].entity_id == "A"


def test_detect_sorts_conflicts_first_then_deterministic():
    cands = [
        _cand(entity_id="B", predicate="z", value="1"),
        _cand(entity_id="A", predicate="y", value="1"),
        _cand(entity_id="A", predicate="y", value="2"),  # conflict
    ]
    groups = detect_conflicts(cands)
    # Conflicts first.
    assert groups[0].kind == "conflict"
    assert (groups[0].entity_id, groups[0].predicate) == ("A", "y")
    assert groups[1].kind == "consensus"


# ===========================================================================
# resolve_conflict — Rule 0: consensus passthrough
# ===========================================================================


def test_resolve_consensus_passthrough():
    cands = [
        _cand(doc_id="a", value="$25M"),
        _cand(doc_id="b", value="$25M"),
    ]
    group = detect_conflicts(cands)[0]
    res = resolve_conflict(group)
    assert res.resolution == "consensus"
    assert res.picked_value == "$25M"
    assert res.picked_candidate is cands[0]
    assert len(res.losers) == 1


# ===========================================================================
# resolve_conflict — Rule 1: doc-chain check (supersession)
# ===========================================================================


def test_rule1_chain_current_version_wins():
    """Two amendments in the same chain. The chain's current_version doc wins."""
    cands = [
        _cand(
            doc_id="amend_v1", value="$25M", authority=0.9,
            chain_id="ch-1", chain_current_version_doc_id="amend_v2",
        ),
        _cand(
            doc_id="amend_v2", value="$50M", authority=0.9,
            chain_id="ch-1", chain_current_version_doc_id="amend_v2",
        ),
    ]
    res = resolve_conflict(detect_conflicts(cands)[0])
    assert res.resolution == "chain"
    assert res.picked_value == "$50M"
    assert res.picked_candidate.doc_id == "amend_v2"
    assert res.notes is not None and "chain_id=ch-1" in res.notes


def test_rule1_chain_skipped_when_only_one_candidate_in_chain():
    """Only one candidate in the chain → chain rule does not fire; falls through."""
    cands = [
        _cand(
            doc_id="a", value="$25M", authority=0.9,
            chain_id="ch-1", chain_current_version_doc_id="a",
        ),
        # Different doc, no chain.
        _cand(doc_id="b", value="$50M", authority=0.4),
    ]
    res = resolve_conflict(detect_conflicts(cands)[0])
    # Single-chain → falls through; authority gap 0.5 >= 0.3 → authority wins.
    assert res.resolution == "authority"


def test_rule1_chain_skipped_when_no_current_version_marker():
    """In-chain candidates exist but none carry chain_current_version_doc_id."""
    cands = [
        _cand(doc_id="a", value="$25M", authority=0.5, chain_id="ch-1"),
        _cand(doc_id="b", value="$50M", authority=0.5, chain_id="ch-1"),
    ]
    res = resolve_conflict(detect_conflicts(cands)[0])
    assert res.resolution != "chain"


# ===========================================================================
# resolve_conflict — Rule 2: status filter
# ===========================================================================


def test_rule2_status_filter_unique_live_wins():
    """One live + one superseded. Live wins by status."""
    cands = [
        _cand(doc_id="a", value="$25M", doc_status="live", authority=0.5),
        _cand(doc_id="b", value="$50M", doc_status="superseded", authority=0.5),
    ]
    res = resolve_conflict(detect_conflicts(cands)[0])
    assert res.resolution == "status"
    assert res.picked_value == "$25M"
    assert "non-live" in res.notes


def test_rule2_status_filter_with_multiple_live_falls_through():
    """Two live disagree + one archived → archived dropped, but live values
    still disagree → falls through to authority/recency over the live-only set."""
    cands = [
        _cand(doc_id="a", value="$25M", doc_status="live", authority=0.9),
        _cand(doc_id="b", value="$50M", doc_status="live", authority=0.5),
        _cand(doc_id="c", value="$99M", doc_status="archived", authority=0.9),
    ]
    res = resolve_conflict(detect_conflicts(cands)[0])
    # Falls into rule 3 with live-only — gap 0.9-0.5 = 0.4 >= 0.3 → authority.
    assert res.resolution == "authority"
    assert res.picked_value == "$25M"


def test_rule2_no_live_candidates_skips_rule():
    """If nothing is 'live', rule 2 cannot fire; falls through."""
    cands = [
        _cand(doc_id="a", value="$25M", doc_status="draft", authority=0.9),
        _cand(doc_id="b", value="$50M", doc_status="superseded", authority=0.4),
    ]
    res = resolve_conflict(detect_conflicts(cands)[0])
    assert res.resolution == "authority"  # gap 0.5 → authority wins
    assert res.picked_value == "$25M"


# ===========================================================================
# resolve_conflict — Rule 3: authority dominates
# ===========================================================================


def test_rule3_authority_dominates_on_gap():
    cands = [
        _cand(doc_id="a", value="$25M", authority=0.95),
        _cand(doc_id="b", value="$50M", authority=0.50),
    ]
    res = resolve_conflict(detect_conflicts(cands)[0])
    assert res.resolution == "authority"
    assert res.picked_value == "$25M"
    assert res.notes is not None and "authority gap" in res.notes


def test_rule3_authority_gap_below_threshold_falls_through():
    """Gap 0.1 < 0.3 → no authority dominance; falls to recency or unresolved."""
    cands = [
        _cand(doc_id="a", value="$25M", authority=0.6, doc_date_iso="2025-01-01"),
        _cand(doc_id="b", value="$50M", authority=0.5, doc_date_iso="2026-01-01"),
    ]
    res = resolve_conflict(detect_conflicts(cands)[0])
    # Should fall to recency: doc b is newer.
    assert res.resolution == "recency"
    assert res.picked_value == "$50M"


def test_rule3_authority_threshold_configurable():
    """Custom threshold = 0.05 → gap 0.1 now triggers authority."""
    cands = [
        _cand(doc_id="a", value="$25M", authority=0.6),
        _cand(doc_id="b", value="$50M", authority=0.5),
    ]
    res = resolve_conflict(
        detect_conflicts(cands)[0],
        authority_dominance_gap=0.05,
    )
    assert res.resolution == "authority"


# ===========================================================================
# resolve_conflict — Rule 4: recency tiebreaker
# ===========================================================================


def test_rule4_recency_wins_when_authority_close():
    cands = [
        _cand(doc_id="a", value="$25M", authority=0.5, doc_date_iso="2024-06-01"),
        _cand(doc_id="b", value="$50M", authority=0.5, doc_date_iso="2026-04-15"),
    ]
    res = resolve_conflict(detect_conflicts(cands)[0])
    assert res.resolution == "recency"
    assert res.picked_value == "$50M"
    assert res.picked_candidate.doc_id == "b"


def test_rule4_recency_accepts_iso8601_with_tz():
    """Full ISO 8601 timestamps with 'Z' offset are parsed (not just dates)."""
    cands = [
        _cand(doc_id="a", value="$25M", authority=0.5,
              doc_date_iso="2026-01-15T10:00:00Z"),
        _cand(doc_id="b", value="$50M", authority=0.5,
              doc_date_iso="2026-05-01T10:00:00Z"),
    ]
    res = resolve_conflict(detect_conflicts(cands)[0])
    assert res.resolution == "recency"
    assert res.picked_value == "$50M"


def test_rule4_skipped_when_no_dates_known():
    """No candidate has a date → falls to rule 5 unresolved."""
    cands = [
        _cand(doc_id="a", value="$25M", authority=0.5),
        _cand(doc_id="b", value="$50M", authority=0.5),
    ]
    res = resolve_conflict(detect_conflicts(cands)[0])
    assert res.resolution == "unresolved"
    assert res.picked_value is None


def test_rule4_same_date_different_values_falls_to_unresolved():
    """Two docs with identical date and disagreeing values → unresolvable."""
    cands = [
        _cand(doc_id="a", value="$25M", authority=0.5, doc_date_iso="2026-01-15"),
        _cand(doc_id="b", value="$50M", authority=0.5, doc_date_iso="2026-01-15"),
    ]
    res = resolve_conflict(detect_conflicts(cands)[0])
    assert res.resolution == "unresolved"


# ===========================================================================
# resolve_conflict — Rule 5: unresolvable
# ===========================================================================


def test_rule5_unresolvable_surfaces_all_candidates_as_losers():
    cands = [
        _cand(doc_id="a", value="$25M", authority=0.5),
        _cand(doc_id="b", value="$50M", authority=0.5),
    ]
    res = resolve_conflict(detect_conflicts(cands)[0])
    assert res.resolution == "unresolved"
    assert res.picked_value is None
    assert res.picked_candidate is None
    assert len(res.losers) == 2


# ===========================================================================
# Cascade ordering — earlier rules take precedence
# ===========================================================================


def test_chain_beats_authority_beats_recency():
    """A chain win should fire even when authority + recency would point
    elsewhere. (Rule 1 short-circuits the cascade.)"""
    cands = [
        _cand(
            doc_id="orig", value="$25M",
            authority=0.99,                # would dominate by authority
            doc_date_iso="2024-01-01",     # but is older
            chain_id="ch-1", chain_current_version_doc_id="amend",
        ),
        _cand(
            doc_id="amend", value="$50M",
            authority=0.40,                # lower authority
            doc_date_iso="2026-05-01",     # newer
            chain_id="ch-1", chain_current_version_doc_id="amend",
        ),
    ]
    res = resolve_conflict(detect_conflicts(cands)[0])
    assert res.resolution == "chain"
    assert res.picked_value == "$50M"


def test_status_beats_authority():
    """Unique live candidate wins on status even when authority gap would
    pick the non-live one."""
    cands = [
        _cand(doc_id="a", value="$25M", doc_status="superseded", authority=0.99),
        _cand(doc_id="b", value="$50M", doc_status="live", authority=0.50),
    ]
    res = resolve_conflict(detect_conflicts(cands)[0])
    assert res.resolution == "status"
    assert res.picked_value == "$50M"


# ===========================================================================
# resolve_all — end-to-end
# ===========================================================================


def test_resolve_all_mixed_consensus_and_conflict():
    cands = [
        # consensus pair
        _cand(entity_id="E1", predicate="p", value="X"),
        _cand(entity_id="E1", predicate="p", value="X"),
        # conflict pair
        _cand(entity_id="E2", predicate="p", value="A", authority=0.9),
        _cand(entity_id="E2", predicate="p", value="B", authority=0.4),
    ]
    results = resolve_all(cands)
    by_entity = {r.entity_id: r for r in results}
    assert by_entity["E1"].resolution == "consensus"
    assert by_entity["E2"].resolution == "authority"
    assert by_entity["E2"].picked_value == "A"


def test_resolve_all_empty_input_returns_empty():
    assert resolve_all([]) == []


# ===========================================================================
# _parse_iso helper
# ===========================================================================


@pytest.mark.parametrize("s,expected_truthy", [
    ("2026-05-01", True),
    ("2026-05-01T10:00:00Z", True),
    ("2026-05-01T10:00:00+00:00", True),
    ("garbage", False),
    (None, False),
    ("", False),
])
def test_parse_iso_handles_inputs(s, expected_truthy):
    d = _parse_iso(s)
    assert (d is not None) == expected_truthy


# ===========================================================================
# Module constants
# ===========================================================================


def test_default_authority_gap_is_design_value():
    """Design 2 specifies 0.3 as the authority-dominance threshold."""
    assert DEFAULT_AUTHORITY_DOMINANCE_GAP == 0.30
