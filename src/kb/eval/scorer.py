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

LLM-based metrics (RAGAS context precision/recall, FactScore for
long-form, HalluGraph alignment) are NOT computed in Wave A — they
require either real corpora or scored ground-truth and a separate LLM
budget. The hook points are documented for Wave B / a follow-up.
"""

from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from kb.eval.runner import EvalResult, STRATA


_WORD = re.compile(r"\w+")


# Per-stratum CSV column order so the output is human-readable.
_CSV_FIELDS: tuple[str, ...] = (
    "question_id", "stratum", "text", "mode", "intent",
    "answer", "refused", "refusal_reason",
    "citations_count", "citation_modalities",
    "faithfulness_verdict", "faithfulness_score",
    "lexical_overlap", "refusal_correct", "citation_ok",
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "overall_lexical_avg": self.overall_lexical_avg,
            "overall_refusal_accuracy": self.overall_refusal_accuracy,
            "overall_citation_accuracy": self.overall_citation_accuracy,
            "overall_faithfulness_avg": self.overall_faithfulness_avg,
            "overall_avg_latency_ms": self.overall_avg_latency_ms,
            "total_errors": self.total_errors,
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


def score_results(results: list[EvalResult]) -> ScoreReport:
    """Aggregate per-result metrics into overall + per-stratum scores."""
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

    return ScoreReport(
        total=len(results),
        overall_lexical_avg=overall["lexical"],
        overall_refusal_accuracy=overall["refusal"],
        overall_citation_accuracy=overall["citation"],
        overall_faithfulness_avg=overall["faith"],
        overall_avg_latency_ms=overall["latency"],
        total_errors=overall["errors"],
        by_stratum=tuple(stratum_scores),
    )


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
                "latency_ms": r.latency_ms,
                "error": r.error or "",
            }
            writer.writerow(row)
    return p


def render_summary(report: ScoreReport) -> str:
    """Human-readable summary string (one block per stratum + overall)."""
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
