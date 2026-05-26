"""Unit tests for the opt-in RAGAS + HHEM hooks in `kb.eval.scorer`.

These tests pass even when neither `[eval]` extras nor `transformers`
are installed — both scorers degrade with a clean note rather than
raising. When the deps ARE present they short-circuit at the LLM /
model-load boundary via monkeypatched helpers, so the test suite
never reaches a real API call or 600 MB model download.
"""

from __future__ import annotations

import pytest

from kb.eval.runner import EvalResult, GoldenQuestion
from kb.eval.scorer import (
    ScoreReport, hhem_scores, ragas_scores, render_summary, reset_sidecars,
    score_results, write_results_csv,
)


def _q(qid: str = "q1", *, stratum: str = "needle") -> GoldenQuestion:
    return GoldenQuestion(
        id=qid, stratum=stratum, text=f"Why {qid}?",
        keywords=("why",), must_refuse=False, min_citations=1,
    )


def _r(
    *,
    qid: str = "q1",
    answer: str = "Because the contexts say so.",
    contexts: tuple[str, ...] = ("Because the contexts say so.",),
    refused: bool = False,
    error: str | None = None,
    stratum: str = "needle",
) -> EvalResult:
    return EvalResult(
        question=_q(qid, stratum=stratum),
        http_status=200,
        query_id=qid,
        answer=answer,
        refused=refused,
        refusal_reason="no_hits" if refused else None,
        citations_count=1,
        citation_modalities=("chunk",),
        mode="H",
        intent="explain",
        faithfulness_verdict="pass",
        faithfulness_score=1.0,
        latency_ms=42,
        error=error,
        contexts=contexts,
    )


# ---------------------------------------------------------------------------
# Skip-cleanly cases — no scorable rows
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_sidecars():
    reset_sidecars()
    yield
    reset_sidecars()


def test_ragas_skips_when_all_rows_refused():
    rows = [_r(refused=True), _r(qid="q2", refused=True)]
    aggs, per_q, note = ragas_scores(rows)
    assert aggs == {"faithfulness": None, "answer_relevancy": None, "context_relevance": None}
    assert per_q == {}
    assert note and "no scorable rows" in note


def test_ragas_skips_when_no_contexts():
    rows = [_r(contexts=())]
    aggs, per_q, note = ragas_scores(rows)
    assert aggs["faithfulness"] is None
    assert "no scorable rows" in (note or "")


def test_hhem_skips_when_all_rows_refused():
    rows = [_r(refused=True)]
    pass_rate, per_q, note = hhem_scores(rows)
    assert pass_rate is None
    assert per_q == {}
    assert note and "no scorable rows" in note


# ---------------------------------------------------------------------------
# score_results respects opt-in flags; no scorable → aggregates stay None,
# note explains why. Proves the wiring + ScoreReport surface.
# ---------------------------------------------------------------------------


def test_score_results_ragas_flag_records_note_when_no_scorable():
    rows = [_r(refused=True)]
    report = score_results(rows, enable_ragas=True)
    assert report.ragas_faithfulness_avg is None
    assert any("ragas skipped" in n for n in report.notes)


def test_score_results_hhem_flag_records_note_when_no_scorable():
    rows = [_r(refused=True)]
    report = score_results(rows, enable_hhem=True)
    assert report.hhem_pass_rate is None
    assert any("hhem skipped" in n for n in report.notes)


def test_score_results_no_flags_no_optional_aggregates():
    rows = [_r()]
    report = score_results(rows)
    # Defaults — opt-in stayed off; no notes either.
    assert report.ragas_faithfulness_avg is None
    assert report.ragas_answer_relevancy_avg is None
    assert report.ragas_context_relevance_avg is None
    assert report.hhem_pass_rate is None
    assert report.notes == ()


# ---------------------------------------------------------------------------
# CSV writer renders the new columns + leaves them blank when not scored.
# ---------------------------------------------------------------------------


def test_csv_includes_optional_columns_blank_when_not_scored(tmp_path):
    rows = [_r()]
    out = write_results_csv(rows, tmp_path / "eval.csv")
    text = out.read_text(encoding="utf-8")
    header = text.splitlines()[0]
    assert "ragas_faithfulness" in header
    assert "ragas_answer_relevancy" in header
    assert "ragas_context_relevance" in header
    assert "hhem_pass" in header
    # Body row should have empty cells for the optional columns.
    body = text.splitlines()[1].split(",")
    cols = header.split(",")
    for col in ("ragas_faithfulness", "ragas_answer_relevancy",
                "ragas_context_relevance", "hhem_pass"):
        idx = cols.index(col)
        assert body[idx] == "", f"expected {col} to be blank, got {body[idx]!r}"


def test_csv_renders_optional_scores_when_sidecars_populated(tmp_path):
    from kb.eval.scorer import _per_question_hhem, _per_question_ragas
    _per_question_ragas["q1"] = {
        "faithfulness": 0.91, "answer_relevancy": 0.87, "context_relevance": 0.75,
    }
    _per_question_hhem["q1"] = 0.93
    out = write_results_csv([_r()], tmp_path / "eval.csv")
    text = out.read_text(encoding="utf-8")
    header, body = text.splitlines()[:2]
    cols = header.split(",")
    cells = body.split(",")
    assert cells[cols.index("ragas_faithfulness")] == "0.910"
    assert cells[cols.index("ragas_answer_relevancy")] == "0.870"
    assert cells[cols.index("ragas_context_relevance")] == "0.750"
    assert cells[cols.index("hhem_pass")] == "0.930"


# ---------------------------------------------------------------------------
# render_summary surfaces optional aggregates + notes when present.
# ---------------------------------------------------------------------------


def test_render_summary_includes_optional_aggregates():
    report = ScoreReport(
        total=1, overall_lexical_avg=1.0,
        overall_refusal_accuracy=1.0,
        overall_citation_accuracy=1.0,
        overall_faithfulness_avg=1.0,
        overall_avg_latency_ms=42.0,
        total_errors=0,
        by_stratum=(),
        ragas_faithfulness_avg=0.88,
        hhem_pass_rate=0.95,
        notes=("ragas skipped: no LLM key",),
    )
    out = render_summary(report)
    assert "ragas_faith=0.88" in out
    assert "hhem=0.95" in out
    assert "ragas skipped" in out


# ---------------------------------------------------------------------------
# Bootstrap helper guards: no Gemini key → clean note, never raises.
# ---------------------------------------------------------------------------


def test_ragas_skips_without_gemini_key(monkeypatch):
    # Strip any ambient credentials so the bootstrap path returns a note.
    monkeypatch.delenv("KB_GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    rows = [_r()]  # has contexts + non-refused → would be scorable
    aggs, per_q, note = ragas_scores(rows)
    # Without ragas installed (CI) the lazy import note fires first;
    # either skip-shape is acceptable as long as it doesn't raise.
    assert aggs["faithfulness"] is None
    assert note and ("ragas skipped" in note or "ragas" in note.lower())
