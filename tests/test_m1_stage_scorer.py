"""M1 — per-stage scorer unit tests (pure functions, no DB / no LLM).

Covers the stage-localisation metrics added for checklist M1:
  - first_rank / recall_at_k / reciprocal_rank   (retrieval)
  - rerank_outcome                                (rerank retention)
  - citation_correct                              (generation cited gold)
  - localise                                      (where it was lost)
  - score_stages aggregation (by stratum + by domain, scorable filter,
    retention denominator, refusal accuracy, localisation histogram)
  - write_stage_csv round-trip + render_stage_summary text
"""

from __future__ import annotations

import csv
from pathlib import Path

from kb.eval.scorer import (
    StageObservation,
    StageReport,
    citation_correct,
    first_rank,
    localise,
    recall_at_k,
    reciprocal_rank,
    render_stage_summary,
    rerank_outcome,
    score_stages,
    write_stage_csv,
)


def _obs(**kw) -> StageObservation:
    """StageObservation with sensible scorable defaults; override per test."""
    base = dict(
        question_id="q1", domain="construction", stratum="needle",
        verified=True, expected_refusal=False,
        expected_file_ids=("F1",),
        fused_file_ids=("F1", "F2", "F3"),
        reranked_file_ids=("F1", "F2"),
        cited_file_ids=("F1",),
        refused=False, faithfulness_verdict="pass",
        citations_resolved=True, error=None,
    )
    base.update(kw)
    return StageObservation(**base)


# ---- retrieval primitives -------------------------------------------------


def test_first_rank_finds_earliest_gold():
    assert first_rank(("F2",), ("F1", "F2", "F3")) == 2
    assert first_rank(("FX",), ("F1", "F2")) is None
    # earliest of several gold files
    assert first_rank(("F3", "F1"), ("F1", "F2", "F3")) == 1
    # no gold to measure
    assert first_rank((), ("F1",)) is None


def test_recall_at_k_cutoff_and_fraction():
    # gold at rank 3 → present at k=30, absent at k=2
    assert recall_at_k(("F3",), ("F1", "F2", "F3"), 30) == 1.0
    assert recall_at_k(("F3",), ("F1", "F2", "F3"), 2) == 0.0
    # two gold, one present → 0.5
    assert recall_at_k(("F1", "FX"), ("F1", "F2"), 10) == 0.5
    # nothing to measure
    assert recall_at_k((), ("F1",), 10) is None
    # None/empty entries in the ranked list are ignored
    assert recall_at_k(("F1",), ("", None, "F1"), 10) == 1.0  # type: ignore[arg-type]


def test_reciprocal_rank():
    assert reciprocal_rank(("F1",), ("F1", "F2")) == 1.0
    assert reciprocal_rank(("F2",), ("F1", "F2")) == 0.5
    assert reciprocal_rank(("FX",), ("F1", "F2")) == 0.0


# ---- rerank outcome -------------------------------------------------------


def test_rerank_outcome_classes():
    # retained: gold in fused AND in reranked
    assert rerank_outcome(("F1",), ("F1", "F2"), ("F1",)) == "retained"
    # dropped: gold in fused but rerank evicted it
    assert rerank_outcome(("F2",), ("F1", "F2"), ("F1",)) == "dropped"
    # miss_retrieval: gold never made the fused set
    assert rerank_outcome(("FX",), ("F1", "F2"), ("F1",)) == "miss_retrieval"
    # nothing to measure
    assert rerank_outcome((), ("F1",), ("F1",)) == "n/a"


def test_rerank_outcome_respects_k_cutoffs():
    fused = tuple(f"F{i}" for i in range(1, 40))  # 39 entries
    # gold at fused position 35 → beyond FUSED_K=30 → miss_retrieval
    assert rerank_outcome(("F35",), fused, fused[:10]) == "miss_retrieval"
    # gold at fused position 20 (within 30), not in top-10 rerank → dropped
    assert rerank_outcome(("F20",), fused, fused[:10]) == "dropped"


# ---- citation correctness -------------------------------------------------


def test_citation_correct():
    assert citation_correct(("F1",), ("F1", "F9")) is True
    assert citation_correct(("F1",), ("F9",)) is False
    assert citation_correct((), ("F1",)) is None


# ---- localisation ---------------------------------------------------------


def test_localise_ok():
    assert localise(_obs()) == "ok"


def test_localise_lost_retrieval():
    o = _obs(fused_file_ids=("F2", "F3"), reranked_file_ids=("F2",))
    assert localise(o) == "lost_retrieval"


def test_localise_lost_rerank():
    o = _obs(fused_file_ids=("F1", "F2"), reranked_file_ids=("F2",))
    assert localise(o) == "lost_rerank"


def test_localise_lost_generation():
    # retrieved + reranked the gold, but cited a different doc
    o = _obs(cited_file_ids=("F9",))
    assert localise(o) == "lost_generation"


def test_localise_refusal_paths():
    assert localise(_obs(expected_refusal=True, refused=True)) == "refused_correct"
    assert localise(_obs(expected_refusal=True, refused=False)) == "refused_wrong"


def test_localise_unscorable_when_unverified_or_unresolved():
    assert localise(_obs(verified=False)) == "unscorable"
    assert localise(_obs(citations_resolved=False)) == "unscorable"
    assert localise(_obs(expected_file_ids=())) == "unscorable"
    assert localise(_obs(error="boom")) == "unscorable"


# ---- aggregation ----------------------------------------------------------


def test_score_stages_groups_and_retention_denominator():
    rows = [
        # ok (retained + cited)
        _obs(question_id="a", stratum="needle", domain="construction"),
        # dropped at rerank
        _obs(question_id="b", stratum="needle", domain="construction",
             fused_file_ids=("F1", "F2"), reranked_file_ids=("F2",),
             cited_file_ids=()),
        # retrieval miss — should NOT count in retention denominator
        _obs(question_id="c", stratum="rare_clause", domain="legal",
             expected_file_ids=("FX",),
             fused_file_ids=("F1", "F2"), reranked_file_ids=("F1",),
             cited_file_ids=()),
        # refusal row (expected to refuse, refused) — separate axis
        _obs(question_id="d", stratum="adversarial", domain="legal",
             expected_refusal=True, refused=True,
             expected_file_ids=(), cited_file_ids=()),
    ]
    report = score_stages(rows)
    assert isinstance(report, StageReport)
    assert report.total == 4

    overall = report.overall
    # scorable = a, b, c (d is a refusal row, excluded from retrieval)
    assert overall.scorable == 3
    # retention denominator = retained(a) + dropped(b) = 2; c was a
    # retrieval miss so it's excluded → retention = 1/2
    assert overall.rerank_retention == 0.5
    assert overall.rerank_dropped == 1
    # localisation histogram covers every row
    assert overall.localisation["ok"] == 1
    assert overall.localisation["lost_rerank"] == 1
    assert overall.localisation["lost_retrieval"] == 1
    assert overall.localisation["refused_correct"] == 1
    # refusal accuracy over the 1 expected-refusal row
    assert overall.refusal_accuracy == 1.0
    assert overall.refusal_count == 1

    # by_domain present and split
    domains = {s.group: s for s in report.by_domain}
    assert set(domains) == {"construction", "legal"}
    assert domains["construction"].scorable == 2
    # construction retention: a retained, b dropped → 0.5
    assert domains["construction"].rerank_retention == 0.5

    # by_stratum present
    strata = {s.group for s in report.by_stratum}
    assert {"needle", "rare_clause", "adversarial"} <= strata


def test_score_stages_recall_averages():
    rows = [
        _obs(question_id="a", expected_file_ids=("F1",),
             fused_file_ids=("F1", "F2")),                  # recall@10 = 1.0
        _obs(question_id="b", expected_file_ids=("FX",),
             fused_file_ids=("F1", "F2")),                  # recall@10 = 0.0
    ]
    report = score_stages(rows)
    assert report.overall.recall_at_k[10] == 0.5


def test_score_stages_empty():
    report = score_stages([])
    assert report.total == 0
    assert report.overall.scorable == 0


def test_score_stages_keeps_noncanonical_strata():
    # The verified queries.yaml uses hyphenated strata (chain-aware,
    # rare-clause, conflict-resolution, long-form) that aren't in the
    # canonical STRATA tuple — they must still appear in by_stratum.
    rows = [
        _obs(question_id="a", stratum="needle"),          # canonical
        _obs(question_id="b", stratum="chain-aware"),     # hyphenated
        _obs(question_id="c", stratum="long-form"),       # not in STRATA
    ]
    report = score_stages(rows)
    groups = {s.group for s in report.by_stratum}
    assert groups == {"needle", "chain-aware", "long-form"}
    # canonical 'needle' is ordered before the extras
    assert report.by_stratum[0].group == "needle"


# ---- CSV + summary --------------------------------------------------------


def test_write_stage_csv_roundtrip(tmp_path: Path):
    rows = [
        _obs(question_id="a"),
        _obs(question_id="b", expected_refusal=True, refused=True,
             expected_file_ids=(), cited_file_ids=()),
    ]
    p = write_stage_csv(rows, tmp_path / "stage.csv")
    parsed = list(csv.DictReader(p.open()))
    assert len(parsed) == 2
    assert parsed[0]["question_id"] == "a"
    assert parsed[0]["localisation"] == "ok"
    assert parsed[0]["recall_at_10"] == "1.000"
    assert parsed[1]["localisation"] == "refused_correct"


def test_render_stage_summary_text():
    report = score_stages([_obs()])
    text = render_stage_summary(report)
    assert "Per-stage Eval" in text
    assert "by domain" in text
    assert "r@10=" in text and "rerank_ret=" in text
