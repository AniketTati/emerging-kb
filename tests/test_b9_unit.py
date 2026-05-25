"""B9 / WA-16 + WA-17 — pure-function unit tests.

Covers:
  - kb.eval.runner.load_golden_questions: parses the shipped YAML;
    validates id uniqueness, stratum enum, required fields
  - kb.eval.scorer pure-function metrics:
      lexical_overlap, refusal_correct, citation_ok,
      faithfulness_score, score_results aggregation,
      write_results_csv round-trip, render_summary text
"""

from __future__ import annotations

import csv
from io import StringIO
from pathlib import Path

import pytest
import yaml

from kb.eval.runner import (
    STRATA,
    GoldenQuestion,
    load_golden_questions,
)
from kb.eval.scorer import (
    ScoreReport,
    StratumScore,
    citation_ok,
    faithfulness_score,
    lexical_overlap,
    refusal_correct,
    render_summary,
    score_results,
    write_results_csv,
)


# ===========================================================================
# load_golden_questions
# ===========================================================================


def test_default_golden_set_has_45_questions():
    """45 = 5 questions × 9 strata."""
    qs = load_golden_questions()
    assert len(qs) == 45


def test_default_golden_set_has_all_9_strata():
    qs = load_golden_questions()
    seen = {q.stratum for q in qs}
    assert seen == set(STRATA)


def test_default_golden_set_five_questions_per_stratum():
    qs = load_golden_questions()
    counts: dict[str, int] = {}
    for q in qs:
        counts[q.stratum] = counts.get(q.stratum, 0) + 1
    for stratum in STRATA:
        assert counts[stratum] == 5, f"{stratum} has {counts[stratum]} not 5"


def test_load_rejects_duplicate_ids(tmp_path):
    fpath = tmp_path / "qs.yaml"
    fpath.write_text(yaml.safe_dump({
        "questions": [
            {"id": "dup-1", "stratum": "needle", "text": "a",
             "expected": {"keywords": []}},
            {"id": "dup-1", "stratum": "needle", "text": "b",
             "expected": {"keywords": []}},
        ],
    }))
    with pytest.raises(ValueError, match="duplicate"):
        load_golden_questions(fpath)


def test_load_rejects_unknown_stratum(tmp_path):
    fpath = tmp_path / "qs.yaml"
    fpath.write_text(yaml.safe_dump({
        "questions": [{
            "id": "x-1", "stratum": "unknown_stratum", "text": "a",
            "expected": {"keywords": []},
        }],
    }))
    with pytest.raises(ValueError, match="stratum"):
        load_golden_questions(fpath)


def test_load_rejects_missing_fields(tmp_path):
    fpath = tmp_path / "qs.yaml"
    fpath.write_text(yaml.safe_dump({
        "questions": [{"stratum": "needle", "text": "x"}],
    }))
    with pytest.raises(ValueError, match="missing id"):
        load_golden_questions(fpath)


def test_load_preserves_must_refuse_flag(tmp_path):
    fpath = tmp_path / "qs.yaml"
    fpath.write_text(yaml.safe_dump({
        "questions": [{
            "id": "neg-1", "stratum": "negative", "text": "x",
            "expected": {"keywords": [], "must_refuse": True},
        }],
    }))
    qs = load_golden_questions(fpath)
    assert qs[0].must_refuse is True


def test_load_preserves_mode_hint(tmp_path):
    fpath = tmp_path / "qs.yaml"
    fpath.write_text(yaml.safe_dump({
        "questions": [{
            "id": "agg-1", "stratum": "aggregation", "text": "how many",
            "expected": {"keywords": [], "mode_hint": "Q"},
        }],
    }))
    qs = load_golden_questions(fpath)
    assert qs[0].mode_hint == "Q"


# ===========================================================================
# lexical_overlap
# ===========================================================================


def test_lexical_overlap_full_match():
    assert lexical_overlap("the cap is twenty five million", ["twenty", "million"]) == 1.0


def test_lexical_overlap_partial():
    score = lexical_overlap("the cap is twenty five", ["twenty", "million"])
    assert score == 0.5


def test_lexical_overlap_no_match():
    assert lexical_overlap("pineapples dance", ["twenty", "million"]) == 0.0


def test_lexical_overlap_empty_keywords_is_neutral_pass():
    """No keywords = nothing to verify = neutral pass (1.0)."""
    assert lexical_overlap("anything", []) == 1.0


def test_lexical_overlap_case_insensitive():
    assert lexical_overlap("THE CAP IS 25 MILLION", ["million"]) == 1.0


def test_lexical_overlap_handles_multi_word_keywords():
    """Multi-word keywords are tokenized into their constituent words."""
    out = lexical_overlap(
        "the indemnification cap is twenty five million",
        ["indemnification cap"],
    )
    assert out == 1.0


# ===========================================================================
# refusal_correct
# ===========================================================================


@pytest.mark.parametrize("refused,must_refuse,expected", [
    (True, True, True),
    (False, False, True),
    (True, False, False),   # over-refused
    (False, True, False),   # under-refused
])
def test_refusal_correct(refused, must_refuse, expected):
    assert refusal_correct(refused, must_refuse) is expected


# ===========================================================================
# citation_ok
# ===========================================================================


def test_citation_ok_when_min_met():
    assert citation_ok(3, min_citations=2, refused=False) is True


def test_citation_ok_when_min_not_met():
    assert citation_ok(1, min_citations=2, refused=False) is False


def test_citation_ok_refused_question_passes_regardless():
    """Refusals correctly carry no citations."""
    assert citation_ok(0, min_citations=3, refused=True) is True


def test_citation_ok_zero_min_always_passes():
    assert citation_ok(0, min_citations=0, refused=False) is True


# ===========================================================================
# faithfulness_score
# ===========================================================================


@pytest.mark.parametrize("verdict,expected", [
    ("pass", 1.0),
    ("low_confidence", 0.5),
    ("refused", 0.0),
    ("skipped", 1.0),    # generator refused upstream → neutral
    (None, 1.0),         # no gate ran → neutral
])
def test_faithfulness_score_mapping(verdict, expected):
    assert faithfulness_score(verdict) == expected


# ===========================================================================
# score_results aggregation
# ===========================================================================


def _make_result(
    stratum: str = "needle",
    *,
    answer: str = "the cap is twenty five",
    keywords: list | None = None,
    refused: bool = False,
    must_refuse: bool = False,
    citations_count: int = 1,
    min_citations: int = 1,
    verdict: str | None = "pass",
    latency_ms: int = 100,
    error: str | None = None,
    mode_hint: str | None = None,
):
    from kb.eval.runner import EvalResult, GoldenQuestion
    q = GoldenQuestion(
        id=f"{stratum}-test",
        stratum=stratum,
        text="?",
        keywords=tuple(keywords or []),
        must_refuse=must_refuse,
        min_citations=min_citations,
        mode_hint=mode_hint,
    )
    return EvalResult(
        question=q,
        http_status=200,
        query_id="q-1",
        answer=answer,
        refused=refused,
        refusal_reason=None,
        citations_count=citations_count,
        citation_modalities=("pdf_span",),
        mode="H",
        intent="factoid",
        faithfulness_verdict=verdict,
        faithfulness_score=1.0 if verdict == "pass" else 0.5,
        latency_ms=latency_ms,
        error=error,
    )


def test_score_results_empty():
    report = score_results([])
    assert report.total == 0
    assert report.by_stratum == ()


def test_score_results_overall_metrics():
    results = [
        _make_result(
            "needle", answer="cap twenty",
            keywords=["twenty"], citations_count=2,
        ),
        _make_result(
            "needle", answer="cap five",
            keywords=["million"], citations_count=2,  # miss
        ),
    ]
    report = score_results(results)
    assert report.total == 2
    # First answer fully matched 'twenty' (1.0); second missed 'million' (0.0).
    assert report.overall_lexical_avg == 0.5
    assert report.overall_refusal_accuracy == 1.0
    assert report.overall_citation_accuracy == 1.0


def test_score_results_per_stratum_breakdown():
    results = [
        _make_result("needle"),
        _make_result("needle"),
        _make_result("adversarial", refused=True, must_refuse=True),
    ]
    report = score_results(results)
    assert len(report.by_stratum) == 2
    by_name = {s.stratum: s for s in report.by_stratum}
    assert by_name["needle"].count == 2
    assert by_name["adversarial"].count == 1


def test_score_results_counts_errors():
    results = [
        _make_result(error="HTTP 500"),
        _make_result(),
    ]
    report = score_results(results)
    assert report.total_errors == 1


def test_score_results_refusal_accuracy():
    """Over-refused / under-refused questions drop the rate."""
    results = [
        _make_result(refused=True, must_refuse=True),
        _make_result(refused=False, must_refuse=True),  # under-refused
    ]
    report = score_results(results)
    assert report.overall_refusal_accuracy == 0.5


# ===========================================================================
# write_results_csv
# ===========================================================================


def test_write_results_csv_round_trip(tmp_path: Path):
    results = [_make_result("needle", answer="hello world")]
    out = write_results_csv(results, tmp_path / "out.csv")
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    rows = list(csv.DictReader(StringIO(text)))
    assert len(rows) == 1
    assert rows[0]["question_id"] == "needle-test"
    assert rows[0]["stratum"] == "needle"
    assert rows[0]["answer"] == "hello world"


def test_write_results_csv_creates_parent_dir(tmp_path: Path):
    target = tmp_path / "nested" / "deeper" / "out.csv"
    write_results_csv([_make_result()], target)
    assert target.exists()


def test_write_results_csv_normalizes_newlines_in_answer(tmp_path: Path):
    results = [_make_result("needle", answer="line one\nline two")]
    out = write_results_csv(results, tmp_path / "out.csv")
    rows = list(csv.DictReader(StringIO(out.read_text())))
    assert "\n" not in rows[0]["answer"]


# ===========================================================================
# render_summary
# ===========================================================================


def test_render_summary_includes_overall_block():
    report = score_results([_make_result("needle")])
    text = render_summary(report)
    assert "Eval Summary" in text
    assert "Overall" in text


def test_render_summary_lists_per_stratum_rows():
    results = [
        _make_result("needle"),
        _make_result("adversarial", refused=True, must_refuse=True),
    ]
    report = score_results(results)
    text = render_summary(report)
    assert "needle" in text
    assert "adversarial" in text
