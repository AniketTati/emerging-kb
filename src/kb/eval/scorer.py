"""B9 / WA-17 — Eval scorer + CSV writer.

Pure-function metrics, computed per result + aggregated by stratum:

  - lexical_overlap   : token-overlap between answer and expected.keywords
                        (0.0–1.0; 1.0 when all keywords appear in the answer)
  - refusal_correct   : answer.refused == expected.must_refuse (0/1)
  - citation_ok       : citations >= min_citations when not refused (0/1)
  - faithfulness_pass : verdict == 'pass' (1) | 'low_confidence' (0.5) | else 0
  - latency_ms        : copied through for percentile reporting

Aggregate ScoreReport breaks down per-stratum totals so the eval CSV
matches the architecture's "per-stratum" reporting expectation.

Optional LLM-based scorers (opt-in via flags on `score_results`):

  - RAGAS (`enable_ragas=True`, requires `pip install -e .[eval]`):
      faithfulness        — answer claims grounded in contexts
      answer_relevancy    — answer semantically aligned to question
      context_relevance   — retrieved chunks relevant to question
    Skipped on the fly when `EvalResult.contexts` is empty or the
    runtime LLM/embedder cannot be constructed.

  - HHEM (`enable_hhem=True`, reuses `kb/query/faithfulness.py`):
      hhem_pass_rate      — fraction of non-refused answers whose
                            HHEM verdict is 'pass'.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from kb.eval.runner import EvalResult, STRATA


logger = logging.getLogger(__name__)


_WORD = re.compile(r"\w+")


# Per-stratum CSV column order so the output is human-readable.
_CSV_FIELDS: tuple[str, ...] = (
    "question_id", "stratum", "text", "mode", "intent",
    "answer", "refused", "refusal_reason",
    "citations_count", "citation_modalities",
    "faithfulness_verdict", "faithfulness_score",
    "lexical_overlap", "refusal_correct", "citation_ok",
    # Optional LLM-judged metrics — empty cells when scoring was disabled
    # or the per-row dependency was missing (no contexts / no LLM).
    "ragas_faithfulness", "ragas_answer_relevancy", "ragas_context_relevance",
    "hhem_pass",
    "latency_ms", "error",
)


# ---------------------------------------------------------------------------
# Pure-function scoring
# ---------------------------------------------------------------------------


def _tokens(s: str) -> set[str]:
    return {m.group(0).lower() for m in _WORD.finditer(s or "")}


def lexical_overlap(answer: str, keywords: Iterable[str]) -> float:
    """Fraction of keyword tokens that appear in the answer (case-
    insensitive, whitespace-tokenized). Returns 1.0 when keywords is
    empty (nothing to verify; lexical is "neutral pass")."""
    kw_tokens: set[str] = set()
    for k in keywords:
        kw_tokens |= _tokens(k)
    if not kw_tokens:
        return 1.0
    answer_tokens = _tokens(answer)
    hits = kw_tokens & answer_tokens
    return len(hits) / len(kw_tokens)


def refusal_correct(refused: bool, must_refuse: bool) -> bool:
    return refused == must_refuse


def citation_ok(
    citations_count: int, min_citations: int, *, refused: bool,
) -> bool:
    """When the question expected a refusal, citation count doesn't
    matter (refusals carry no citations). Otherwise enforce the min."""
    if refused:
        return True
    return citations_count >= max(0, int(min_citations))


def faithfulness_score(verdict: str | None) -> float:
    """Map verdict → numeric for aggregation."""
    if verdict == "pass":
        return 1.0
    if verdict == "low_confidence":
        return 0.5
    if verdict in (None, "skipped"):
        # skipped means generator refused upstream — neutral for the gate.
        return 1.0
    return 0.0   # 'refused'


# ---------------------------------------------------------------------------
# Aggregate report
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StratumScore:
    stratum: str
    count: int
    lexical_overlap_avg: float
    refusal_accuracy: float
    citation_accuracy: float
    faithfulness_pass_rate: float
    avg_latency_ms: float
    errors: int


@dataclass(frozen=True)
class ScoreReport:
    total: int
    overall_lexical_avg: float
    overall_refusal_accuracy: float
    overall_citation_accuracy: float
    overall_faithfulness_avg: float
    overall_avg_latency_ms: float
    total_errors: int
    by_stratum: tuple[StratumScore, ...] = field(default_factory=tuple)
    # Optional LLM-judged aggregates. `None` when the scorer was not
    # asked to compute them; the UI/CSV renders blanks in that case.
    ragas_faithfulness_avg: float | None = None
    ragas_answer_relevancy_avg: float | None = None
    ragas_context_relevance_avg: float | None = None
    hhem_pass_rate: float | None = None
    # Optional human-readable note (e.g. "ragas skipped: no LLM key").
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "overall_lexical_avg": self.overall_lexical_avg,
            "overall_refusal_accuracy": self.overall_refusal_accuracy,
            "overall_citation_accuracy": self.overall_citation_accuracy,
            "overall_faithfulness_avg": self.overall_faithfulness_avg,
            "overall_avg_latency_ms": self.overall_avg_latency_ms,
            "total_errors": self.total_errors,
            "ragas_faithfulness_avg": self.ragas_faithfulness_avg,
            "ragas_answer_relevancy_avg": self.ragas_answer_relevancy_avg,
            "ragas_context_relevance_avg": self.ragas_context_relevance_avg,
            "hhem_pass_rate": self.hhem_pass_rate,
            "notes": list(self.notes),
            "by_stratum": [
                {
                    "stratum": s.stratum,
                    "count": s.count,
                    "lexical_overlap_avg": s.lexical_overlap_avg,
                    "refusal_accuracy": s.refusal_accuracy,
                    "citation_accuracy": s.citation_accuracy,
                    "faithfulness_pass_rate": s.faithfulness_pass_rate,
                    "avg_latency_ms": s.avg_latency_ms,
                    "errors": s.errors,
                }
                for s in self.by_stratum
            ],
        }


def score_results(
    results: list[EvalResult],
    *,
    enable_ragas: bool = False,
    enable_hhem: bool = False,
    ragas_llm: Any | None = None,
    ragas_embeddings: Any | None = None,
) -> ScoreReport:
    """Aggregate per-result metrics into overall + per-stratum scores.

    `enable_ragas` / `enable_hhem` are opt-in because both pull heavy
    deps (RAGAS via `[eval]` extras, HHEM via `transformers`/`torch` +
    ~600MB model). Each scorer attempts a lazy import and degrades to a
    `None` aggregate + a `notes` entry rather than crashing the run.
    """
    if not results:
        return ScoreReport(
            total=0, overall_lexical_avg=0.0,
            overall_refusal_accuracy=0.0,
            overall_citation_accuracy=0.0,
            overall_faithfulness_avg=0.0,
            overall_avg_latency_ms=0.0,
            total_errors=0,
            by_stratum=(),
        )

    by_stratum: dict[str, list[EvalResult]] = defaultdict(list)
    for r in results:
        by_stratum[r.question.stratum].append(r)

    def _aggregate(items: list[EvalResult]) -> dict[str, Any]:
        n = len(items)
        lex = sum(
            lexical_overlap(r.answer, r.question.keywords) for r in items
        ) / n
        ref = sum(
            1.0 if refusal_correct(r.refused, r.question.must_refuse) else 0.0
            for r in items
        ) / n
        cit = sum(
            1.0 if citation_ok(
                r.citations_count, r.question.min_citations,
                refused=r.refused,
            ) else 0.0
            for r in items
        ) / n
        faith = sum(
            faithfulness_score(r.faithfulness_verdict) for r in items
        ) / n
        latency = sum(r.latency_ms for r in items) / n
        errors = sum(1 for r in items if r.error)
        return {
            "lexical": lex, "refusal": ref, "citation": cit,
            "faith": faith, "latency": latency, "errors": errors,
        }

    overall = _aggregate(results)

    stratum_scores: list[StratumScore] = []
    for stratum in STRATA:
        items = by_stratum.get(stratum) or []
        if not items:
            continue
        agg = _aggregate(items)
        stratum_scores.append(StratumScore(
            stratum=stratum, count=len(items),
            lexical_overlap_avg=agg["lexical"],
            refusal_accuracy=agg["refusal"],
            citation_accuracy=agg["citation"],
            faithfulness_pass_rate=agg["faith"],
            avg_latency_ms=agg["latency"],
            errors=agg["errors"],
        ))

    notes: list[str] = []
    ragas_aggs: dict[str, float | None] = {
        "faithfulness": None,
        "answer_relevancy": None,
        "context_relevance": None,
    }
    hhem_agg: float | None = None

    if enable_ragas:
        ragas_aggs, ragas_per_q, ragas_note = ragas_scores(
            results, llm=ragas_llm, embeddings=ragas_embeddings,
        )
        if ragas_note:
            notes.append(ragas_note)
        # Stash per-question scores on the results so the CSV writer
        # can render them inline. Using a sidecar dict keeps EvalResult
        # frozen.
        _per_question_ragas.update(ragas_per_q)

    if enable_hhem:
        hhem_agg, hhem_per_q, hhem_note = hhem_scores(results)
        if hhem_note:
            notes.append(hhem_note)
        _per_question_hhem.update(hhem_per_q)

    return ScoreReport(
        total=len(results),
        overall_lexical_avg=overall["lexical"],
        overall_refusal_accuracy=overall["refusal"],
        overall_citation_accuracy=overall["citation"],
        overall_faithfulness_avg=overall["faith"],
        overall_avg_latency_ms=overall["latency"],
        total_errors=overall["errors"],
        by_stratum=tuple(stratum_scores),
        ragas_faithfulness_avg=ragas_aggs.get("faithfulness"),
        ragas_answer_relevancy_avg=ragas_aggs.get("answer_relevancy"),
        ragas_context_relevance_avg=ragas_aggs.get("context_relevance"),
        hhem_pass_rate=hhem_agg,
        notes=tuple(notes),
    )


# ---------------------------------------------------------------------------
# RAGAS — opt-in, lazy-imported
#
# Sidecar dicts let `write_results_csv` render per-question scores after
# `score_results` ran with the corresponding `enable_*` flag. Keyed by
# `EvalResult.question.id`. Kept module-level (not in ScoreReport) so
# the dataclass stays frozen + JSON-safe.
# ---------------------------------------------------------------------------


_per_question_ragas: dict[str, dict[str, float]] = {}
_per_question_hhem: dict[str, float] = {}


def reset_sidecars() -> None:
    """Test helper — wipe the per-question RAGAS/HHEM caches."""
    _per_question_ragas.clear()
    _per_question_hhem.clear()


def ragas_scores(
    results: list[EvalResult],
    *,
    llm: Any | None = None,
    embeddings: Any | None = None,
) -> tuple[dict[str, float | None], dict[str, dict[str, float]], str | None]:
    """Run the 3-metric RAGAS judge on `results`. Returns
    `(aggregates, per_question, note)` where:

    - `aggregates`: dict with keys `faithfulness`, `answer_relevancy`,
      `context_relevance` mapped to overall mean (None when no rows
      had usable contexts).
    - `per_question`: `{question_id: {metric: score}}` for CSV inline.
    - `note`: human-readable string when scoring was degraded
      (no LLM, no contexts, ragas import failed), else None.

    Scorable rows are those that:
      - did not refuse (refused answers carry no claims to ground),
      - have at least one retrieved context snippet,
      - have a non-empty answer.

    The default `llm` / `embeddings` are constructed from
    `langchain_google_genai` against `KB_GEMINI_API_KEY`. When that
    key is absent, RAGAS scoring is skipped with a clean note rather
    than raised.
    """
    aggregates: dict[str, float | None] = {
        "faithfulness": None,
        "answer_relevancy": None,
        "context_relevance": None,
    }
    per_question: dict[str, dict[str, float]] = {}

    scorable = [
        r for r in results
        if not r.refused and r.answer and r.contexts and not r.error
    ]
    if not scorable:
        return aggregates, per_question, (
            "ragas skipped: no scorable rows "
            "(need non-refused answers with retrieved contexts)"
        )

    # Lazy imports — keep the eval module importable in CI without the
    # `[eval]` extras installed.
    try:
        from ragas import evaluate                         # type: ignore
        from ragas.metrics import (                        # type: ignore
            Faithfulness, AnswerRelevancy, ContextRelevance,
        )
        from datasets import Dataset                       # type: ignore
    except Exception as exc:  # noqa: BLE001
        return aggregates, per_question, (
            f"ragas skipped: pip install -e .[eval] failed import: {exc}"
        )

    # LLM / embedder bootstrap — caller can inject (tests) else we
    # construct a Gemini-backed pair when a key is present.
    if llm is None or embeddings is None:
        try:
            llm, embeddings, note = _bootstrap_gemini_for_ragas(llm, embeddings)
            if note:
                return aggregates, per_question, note
        except Exception as exc:  # noqa: BLE001
            return aggregates, per_question, f"ragas skipped: {exc}"

    # RAGAS expects a HuggingFace Dataset; build it from scorable rows.
    dataset = Dataset.from_dict({
        "question":     [r.question.text for r in scorable],
        "answer":       [r.answer        for r in scorable],
        "contexts":     [list(r.contexts) for r in scorable],
    })

    try:
        result = evaluate(
            dataset,
            metrics=[Faithfulness(), AnswerRelevancy(), ContextRelevance()],
            llm=llm,
            embeddings=embeddings,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("ragas evaluate failed")
        return aggregates, per_question, f"ragas failed: {exc}"

    # RAGAS 0.2 returns a `Result` object with `.scores` (list of per-row
    # dicts) and aggregate access via dict-like indexing.
    try:
        per_rows = list(result.scores)  # type: ignore[attr-defined]
    except Exception:
        per_rows = []

    for r, row in zip(scorable, per_rows):
        clean: dict[str, float] = {}
        for k in ("faithfulness", "answer_relevancy", "context_relevance"):
            v = row.get(k) if isinstance(row, dict) else None
            if isinstance(v, (int, float)) and not _isnan(float(v)):
                clean[k] = float(v)
        if clean:
            per_question[r.question.id] = clean

    # Aggregate by averaging non-None per-question scores.
    for k in ("faithfulness", "answer_relevancy", "context_relevance"):
        vals = [
            d[k] for d in per_question.values()
            if k in d
        ]
        aggregates[k] = sum(vals) / len(vals) if vals else None

    return aggregates, per_question, None


def _bootstrap_gemini_for_ragas(
    llm: Any | None, embeddings: Any | None,
) -> tuple[Any, Any, str | None]:
    """Build LangchainLLMWrapper(Gemini) + LangchainEmbeddingsWrapper
    (Gemini) for RAGAS. Returns (llm, embeddings, note). `note` is
    non-None when bootstrap failed (skip ragas with that message)."""
    import os
    api_key = os.environ.get("KB_GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return llm, embeddings, (
            "ragas skipped: KB_GEMINI_API_KEY not set "
            "(scoring needs an LLM judge)"
        )
    try:
        from langchain_google_genai import (                              # type: ignore
            ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings,
        )
        from ragas.llms import LangchainLLMWrapper                        # type: ignore
        from ragas.embeddings import LangchainEmbeddingsWrapper           # type: ignore
    except Exception as exc:  # noqa: BLE001
        return llm, embeddings, (
            f"ragas skipped: `langchain-google-genai` not installed ({exc}); "
            f"pip install -e .[eval] to enable"
        )
    if llm is None:
        llm = LangchainLLMWrapper(ChatGoogleGenerativeAI(
            model="gemini-2.5-flash", google_api_key=api_key,
        ))
    if embeddings is None:
        embeddings = LangchainEmbeddingsWrapper(GoogleGenerativeAIEmbeddings(
            model="models/embedding-001", google_api_key=api_key,
        ))
    return llm, embeddings, None


def _isnan(x: float) -> bool:
    return x != x


# ---------------------------------------------------------------------------
# HHEM — reuses kb/query/faithfulness.py (already lazy-loads transformers)
# ---------------------------------------------------------------------------


def hhem_scores(
    results: list[EvalResult],
) -> tuple[float | None, dict[str, float], str | None]:
    """Score every non-refused answer against its retrieved contexts via
    HHEM-2.1. Returns `(pass_rate, per_question_score, note)` where
    pass_rate is the fraction of rows whose HHEM verdict == 'pass'.

    A row is scorable when it has both a non-empty answer and at least
    one context snippet. Refusals skip cleanly.
    """
    scorable = [
        r for r in results
        if not r.refused and r.answer and r.contexts and not r.error
    ]
    if not scorable:
        return None, {}, (
            "hhem skipped: no scorable rows "
            "(need non-refused answers with retrieved contexts)"
        )

    try:
        from kb.query.faithfulness import HHEMFaithfulnessGate
    except Exception as exc:  # noqa: BLE001
        return None, {}, f"hhem skipped: import failed: {exc}"

    gate = HHEMFaithfulnessGate()
    import asyncio

    per_q: dict[str, float] = {}
    passes = 0
    n = 0
    for r in scorable:
        try:
            res = asyncio.run(gate.assess(r.answer, r.contexts))
        except Exception as exc:  # noqa: BLE001
            logger.warning("hhem failed on %s: %s", r.question.id, exc)
            continue
        per_q[r.question.id] = float(res.score)
        if res.verdict == "pass":
            passes += 1
        n += 1

    return (passes / n) if n else None, per_q, None


# ---------------------------------------------------------------------------
# CSV writer
# ---------------------------------------------------------------------------


def write_results_csv(
    results: list[EvalResult],
    out_path: Path | str,
) -> Path:
    """Write per-result rows to a CSV. Returns the resolved path. The
    file's column order matches `_CSV_FIELDS` for reproducible diffs
    across runs."""
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(_CSV_FIELDS))
        writer.writeheader()
        for r in results:
            rg = _per_question_ragas.get(r.question.id, {})
            hh = _per_question_hhem.get(r.question.id)
            row = {
                "question_id": r.question.id,
                "stratum": r.question.stratum,
                "text": r.question.text,
                "mode": r.mode or "",
                "intent": r.intent or "",
                "answer": (r.answer or "").replace("\n", " ").strip(),
                "refused": "1" if r.refused else "0",
                "refusal_reason": r.refusal_reason or "",
                "citations_count": r.citations_count,
                "citation_modalities": "|".join(r.citation_modalities),
                "faithfulness_verdict": r.faithfulness_verdict or "",
                "faithfulness_score": (
                    f"{r.faithfulness_score:.3f}"
                    if r.faithfulness_score is not None else ""
                ),
                "lexical_overlap": f"{lexical_overlap(r.answer, r.question.keywords):.3f}",
                "refusal_correct": (
                    "1" if refusal_correct(r.refused, r.question.must_refuse) else "0"
                ),
                "citation_ok": (
                    "1" if citation_ok(
                        r.citations_count, r.question.min_citations,
                        refused=r.refused,
                    ) else "0"
                ),
                "ragas_faithfulness": _fmt(rg.get("faithfulness")),
                "ragas_answer_relevancy": _fmt(rg.get("answer_relevancy")),
                "ragas_context_relevance": _fmt(rg.get("context_relevance")),
                "hhem_pass": _fmt(hh),
                "latency_ms": r.latency_ms,
                "error": r.error or "",
            }
            writer.writerow(row)
    return p


def _fmt(v: float | None) -> str:
    """Format an optional float for CSV — blank when None."""
    return f"{v:.3f}" if isinstance(v, (int, float)) else ""


def render_summary(report: ScoreReport) -> str:
    """Human-readable summary string (one block per stratum + overall +
    optional RAGAS / HHEM aggregates when present)."""
    lines: list[str] = []
    lines.append(
        f"=== Eval Summary ({report.total} questions, "
        f"{report.total_errors} errors) ==="
    )
    lines.append(
        f"Overall: lex={report.overall_lexical_avg:.2f} "
        f"refusal={report.overall_refusal_accuracy:.2f} "
        f"cite={report.overall_citation_accuracy:.2f} "
        f"faith={report.overall_faithfulness_avg:.2f} "
        f"avg_lat={report.overall_avg_latency_ms:.0f}ms"
    )

    optional: list[str] = []
    if report.ragas_faithfulness_avg is not None:
        optional.append(f"ragas_faith={report.ragas_faithfulness_avg:.2f}")
    if report.ragas_answer_relevancy_avg is not None:
        optional.append(f"ragas_rel={report.ragas_answer_relevancy_avg:.2f}")
    if report.ragas_context_relevance_avg is not None:
        optional.append(f"ragas_ctx={report.ragas_context_relevance_avg:.2f}")
    if report.hhem_pass_rate is not None:
        optional.append(f"hhem={report.hhem_pass_rate:.2f}")
    if optional:
        lines.append("LLM-judged: " + " ".join(optional))
    for note in report.notes:
        lines.append(f"  · {note}")

    for s in report.by_stratum:
        lines.append(
            f"  [{s.stratum:<14}] n={s.count} "
            f"lex={s.lexical_overlap_avg:.2f} "
            f"refusal={s.refusal_accuracy:.2f} "
            f"cite={s.citation_accuracy:.2f} "
            f"faith={s.faithfulness_pass_rate:.2f} "
            f"lat={s.avg_latency_ms:.0f}ms "
            f"errors={s.errors}"
        )
    return "\n".join(lines)
