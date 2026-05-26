"""R1 — unit tests for kb.query.conflict_resolution (pure functions).

DB-bound functions (fetch_*, persist_*) are covered by an integration
test in test_query_chat_api.py once we extend it; these tests focus on
the translation logic (FactCandidate builders + prompt block renderer)
that doesn't need a DB.
"""

from __future__ import annotations

import pytest

from kb.query.conflict_resolution import (
    FileMetaForConflict,
    _SKIP_PARAMETER_KEYS,
    _stringify,
    build_conflict_prompt_block,
    build_fact_candidates,
)
from kb.query.conflict_detector import (
    FactCandidate,
    ResolvedConflict,
    resolve_all,
)
from kb.query.rrf import Hit


# ---------------------------------------------------------------------------
# _stringify — value normalization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("val,expected", [
    (None, None),
    ("", None),
    ("  spaces  ", "spaces"),
    (30, "30"),
    (30.0, "30"),                  # float-with-zero → int form
    (30.5, "30.5"),
    (True, "true"),
    (False, "false"),
    (["NorthWind", "Vertex"], "NorthWind,Vertex"),
    ([], None),
    ([None, ""], None),            # all-empty list → None
    ({"nested": "x"}, None),       # dicts not supported in Wave A
])
def test_stringify_normalizes_value(val, expected):
    assert _stringify(val) == expected


# ---------------------------------------------------------------------------
# build_fact_candidates — translation layer
# ---------------------------------------------------------------------------


def _meta(file_id: str, *, chain_id: str | None = "chain-1",
          authority: float = 0.5, status: str = "live",
          current_version: str | None = "f-newer") -> FileMetaForConflict:
    return FileMetaForConflict(
        file_id=file_id,
        source_authority=authority,
        doc_status=status,
        chain_id=chain_id,
        chain_current_version_doc_id=current_version,
        created_at_iso="2026-01-01T00:00:00",
    )


def test_build_fact_candidates_skips_non_atomic_unit_hits():
    hits = [
        Hit(id="h1", kind="chunk", score=0.9, snippet="text",
            metadata={"file_id": "f1"}),
        Hit(id="h2", kind="raptor_node", score=0.8, snippet="summary",
            metadata={"file_id": "f1"}),
    ]
    out = build_fact_candidates(
        hits,
        file_metas={"f1": _meta("f1")},
        atomic_unit_params={},
    )
    assert out == []


def test_build_fact_candidates_skips_hits_without_chain():
    hits = [
        Hit(id="au-1", kind="atomic_unit", score=0.9, snippet="",
            metadata={"file_id": "f-loose"}),
    ]
    params = {"au-1": {"payment_due_days": 30, "__unit_type__": "clause"}}
    out = build_fact_candidates(
        hits,
        file_metas={"f-loose": _meta("f-loose", chain_id=None)},
        atomic_unit_params=params,
    )
    assert out == [], "files outside any chain should not yield FactCandidates"


def test_build_fact_candidates_uses_clause_type_as_namespace():
    """Two MSA/Amendment clauses share clause_type=payment_terms but
    disagree on payment_due_days — should land as a conflict on
    `payment_terms.payment_due_days`, not on bare `payment_due_days`."""
    hits = [
        Hit(id="au-msa", kind="atomic_unit", score=0.9, snippet="",
            metadata={"file_id": "f-msa"}),
        Hit(id="au-amend", kind="atomic_unit", score=0.9, snippet="",
            metadata={"file_id": "f-amend"}),
    ]
    params = {
        "au-msa": {
            "clause_type": "payment_terms", "payment_due_days": 30,
            "__unit_type__": "clause", "summary": "Net-30 terms.",
        },
        "au-amend": {
            "clause_type": "payment_terms", "payment_due_days": 45,
            "__unit_type__": "clause", "summary": "Net-45 terms.",
        },
    }
    out = build_fact_candidates(
        hits,
        file_metas={
            "f-msa": _meta("f-msa", current_version="f-amend"),
            "f-amend": _meta("f-amend", current_version="f-amend"),
        },
        atomic_unit_params=params,
    )
    # 2 facts (one per file) on the SAME predicate.
    assert len(out) == 2
    assert all(c.predicate == "payment_terms.payment_due_days" for c in out)
    assert {c.value for c in out} == {"30", "45"}
    # Entity_id = chain_id so both candidates compare like-with-like.
    assert {c.entity_id for c in out} == {"chain-1"}


def test_build_fact_candidates_uses_unit_type_when_no_clause_type():
    """Generic-items output sets unit_type=action_item etc. directly —
    use it as the namespace when clause_type is absent."""
    hits = [
        Hit(id="au-1", kind="atomic_unit", score=0.9, snippet="",
            metadata={"file_id": "f-pm1"}),
    ]
    params = {
        "au-1": {
            "__unit_type__": "action_item",
            "title": "Patch IAM",
            "summary": "Update IAM",
            "actor": "alice",
            "date": "2026-03-15",
        },
    }
    out = build_fact_candidates(
        hits,
        file_metas={"f-pm1": _meta("f-pm1")},
        atomic_unit_params=params,
    )
    # title + summary skipped per _SKIP_PARAMETER_KEYS; actor + date kept.
    predicates = sorted(c.predicate for c in out)
    assert predicates == ["action_item.actor", "action_item.date"]


def test_build_fact_candidates_skips_narrative_keys():
    """summary/title/parties shouldn't become predicates."""
    hits = [Hit(id="au-1", kind="atomic_unit", score=0.9, snippet="",
                metadata={"file_id": "f1"})]
    params = {"au-1": {
        "__unit_type__": "clause",
        "clause_type": "termination",
        "summary": "60-day notice period",
        "title": "Termination",
        "parties": ["A", "B"],
        "anchor_chunk_index": 3,
        "notice_days": 60,
    }}
    out = build_fact_candidates(
        hits,
        file_metas={"f1": _meta("f1")},
        atomic_unit_params=params,
    )
    keys_emitted = {c.predicate for c in out}
    assert keys_emitted == {"termination.notice_days"}
    # Sanity-check the skip set is the actual reason
    for skip in ("summary", "title", "parties", "anchor_chunk_index"):
        assert skip in _SKIP_PARAMETER_KEYS


# ---------------------------------------------------------------------------
# End-to-end: build_fact_candidates → resolve_all → meaningful result
# ---------------------------------------------------------------------------


def test_msa_amendment_payment_terms_resolves_via_chain_rule():
    """The motivating demo case — Amendment (current version) wins over
    MSA on payment_due_days via Rule 1 (doc_chain supersession)."""
    hits = [
        Hit(id="au-msa", kind="atomic_unit", score=0.9, snippet="",
            metadata={"file_id": "f-msa"}),
        Hit(id="au-amend", kind="atomic_unit", score=0.9, snippet="",
            metadata={"file_id": "f-amend"}),
    ]
    params = {
        "au-msa": {"clause_type": "payment_terms", "payment_due_days": 30,
                   "__unit_type__": "clause"},
        "au-amend": {"clause_type": "payment_terms", "payment_due_days": 45,
                     "__unit_type__": "clause"},
    }
    metas = {
        "f-msa": _meta("f-msa", current_version="f-amend"),
        "f-amend": _meta("f-amend", current_version="f-amend"),
    }
    cands = build_fact_candidates(hits, file_metas=metas, atomic_unit_params=params)
    resolutions = resolve_all(cands)
    assert len(resolutions) == 1
    r = resolutions[0]
    assert r.predicate == "payment_terms.payment_due_days"
    assert r.resolution == "chain"
    assert r.picked_value == "45"
    assert r.picked_candidate.doc_id == "f-amend"
    assert {l.doc_id for l in r.losers} == {"f-msa"}


# ---------------------------------------------------------------------------
# build_conflict_prompt_block — generator-prompt rendering
# ---------------------------------------------------------------------------


def test_prompt_block_empty_when_only_consensus():
    """A resolution list containing only consensus rows renders as ""."""
    cand = FactCandidate(doc_id="f1", entity_id="chain-1",
                         predicate="x.y", value="42",
                         authority=0.5, doc_status="live")
    resolved = resolve_all([cand])
    assert resolved[0].resolution == "consensus"
    assert build_conflict_prompt_block(resolved) == ""


def test_prompt_block_describes_chain_resolution():
    chain_resolution = ResolvedConflict(
        entity_id="chain-1",
        predicate="payment_terms.payment_due_days",
        resolution="chain",
        picked_value="45",
        picked_candidate=FactCandidate(
            doc_id="f-amend-uuid-here-1234567890abcdef",
            entity_id="chain-1", predicate="payment_terms.payment_due_days",
            value="45", authority=0.5, doc_status="live",
        ),
        losers=(FactCandidate(
            doc_id="f-msa-uuid-here-abcdef1234567890",
            entity_id="chain-1", predicate="payment_terms.payment_due_days",
            value="30", authority=0.5, doc_status="live",
        ),),
        notes="chain_id=chain-1: current version supersedes prior",
    )
    block = build_conflict_prompt_block([chain_resolution])
    assert "RESOLVED" in block
    assert "payment_terms.payment_due_days" in block
    assert "picked '45'" in block
    assert "f-amend-" in block      # winner doc id (truncated)
    assert "via chain" in block
    assert "superseded:" in block
    assert "30" in block            # loser value


def test_prompt_block_describes_unresolved_conflict():
    unresolved = ResolvedConflict(
        entity_id="chain-1",
        predicate="indemnification.cap",
        resolution="unresolved",
        picked_value=None,
        picked_candidate=None,
        losers=(
            FactCandidate(doc_id="f1-aaaa", entity_id="chain-1",
                          predicate="indemnification.cap", value="25M",
                          authority=0.5, doc_status="live"),
            FactCandidate(doc_id="f2-bbbb", entity_id="chain-1",
                          predicate="indemnification.cap", value="50M",
                          authority=0.5, doc_status="live"),
        ),
        notes="authority + recency both ambiguous",
    )
    block = build_conflict_prompt_block([unresolved])
    assert "UNRESOLVED CONFLICT" in block
    assert "indemnification.cap" in block
    assert "25M" in block
    assert "50M" in block
    assert "Surface both" in block
