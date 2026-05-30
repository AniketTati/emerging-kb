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
# M1 — per-stage measurement (checklist M1 / DECISIONS D9)
#
# The end-to-end metrics above answer "was the final answer right?". They
# CANNOT tell you WHERE accuracy was lost. M1 scores the three pipeline
# stages separately, against the verified `expected_citations` from the
# rebuilt eval (demo-corpus/domains/*/queries.yaml):
#
#   retrieval recall@k  — did the verified citation's file appear in the
#                         FUSED candidate set (pre-rerank), within top-k?
#   rerank retention    — GIVEN it was in the fused set, did the reranker
#                         keep it in the top-10? (isolates the reranker)
#   citation correctness— did the GENERATED answer actually cite that file?
#   faithfulness        — is the answer grounded (reuses the verdict / HHEM)
#
# Each question is then localised to the FIRST stage that dropped its
# gold doc (lost_retrieval / lost_rerank / lost_generation / ok), so a
# later fix can be attributed to the stage it targets.
#
# These are pure functions over a plain `StageObservation` (no orchestrator
# / DB types) so this module stays import-light. The driver that produces
# observations lives in `kb.eval.stage_runner`.
# ---------------------------------------------------------------------------


# Fusion keeps top-30 (orchestrator `_POST_FUSION_TOP_K`); rerank keeps
# top-10 (`_POST_RERANK_TOP_K`). Mirrored here so the scorer's k-cutoffs
# match what the pipeline actually returns.
RECALL_KS: tuple[int, ...] = (10, 30)
RERANK_K: int = 10
FUSED_K: int = 30


@dataclass(frozen=True)
class StageObservation:
    """One question's per-stage trace, captured by `stage_runner` from a
    single `orchestrator.chat()` call + its event sink. All file refs are
    workspace-resolved file_ids (the `expected_citations` slugs are
    resolved to file_ids before construction)."""

    question_id: str
    domain: str
    stratum: str
    verified: bool
    expected_refusal: bool
    # Resolved gold file_ids (from expected_citations). Empty when the
    # question carries no citations (some negatives) or none resolved.
    expected_file_ids: tuple[str, ...]
    # Ordered file_ids of the fused candidate set (pre-rerank, ≤30).
    fused_file_ids: tuple[str, ...]
    # Ordered file_ids of the reranked top-K (≤10).
    reranked_file_ids: tuple[str, ...]
    # file_ids the generated answer actually cited.
    cited_file_ids: tuple[str, ...]
    refused: bool
    faithfulness_verdict: str | None
    # True when slug→file_id resolution found EVERY expected citation in
    # the workspace. False means the gold doc may be absent from the
    # ingested corpus — retrieval misses on such rows are not the
    # pipeline's fault, so they're excluded from recall denominators.
    citations_resolved: bool = True
    error: str | None = None

    def is_scorable_retrieval(self) -> bool:
        """A row contributes to retrieval/rerank/citation metrics only
        when it's a verified, non-refusal question whose gold citations
        were fully resolved in the workspace and it didn't error."""
        return (
            self.verified
            and not self.expected_refusal
            and self.citations_resolved
            and bool(self.expected_file_ids)
            and not self.error
        )


def _clean_set(ids: Iterable[str]) -> set[str]:
    return {i for i in ids if i}


def first_rank(expected_file_ids: Iterable[str], ranked: Iterable[str]) -> int | None:
    """1-indexed rank of the earliest gold file in `ranked` (chunk-level,
    so dups count as positions), or None if no gold file appears."""
    exp = _clean_set(expected_file_ids)
    if not exp:
        return None
    for i, fid in enumerate(ranked):
        if fid and fid in exp:
            return i + 1
    return None


def recall_at_k(
    expected_file_ids: Iterable[str], ranked: Iterable[str], k: int,
) -> float | None:
    """Fraction of gold files that appear in the first `k` ranked
    positions. None when there's nothing to measure (no gold files)."""
    exp = _clean_set(expected_file_ids)
    if not exp:
        return None
    topk = _clean_set(list(ranked)[:k])
    return len(exp & topk) / len(exp)


def reciprocal_rank(
    expected_file_ids: Iterable[str], ranked: Iterable[str],
) -> float:
    """1 / (rank of the earliest gold file), or 0.0 if it never appears."""
    r = first_rank(expected_file_ids, ranked)
    return (1.0 / r) if r else 0.0


def rerank_outcome(
    expected_file_ids: Iterable[str],
    fused: Iterable[str],
    reranked: Iterable[str],
    *,
    k_fused: int = FUSED_K,
    k_rerank: int = RERANK_K,
) -> str:
    """Classify the reranker's effect on the gold doc:

      'retained'        — gold was in the fused set AND survived to top-K
      'dropped'         — gold was in the fused set but rerank evicted it
      'miss_retrieval'  — gold wasn't in the fused set (not rerank's fault)
      'n/a'             — nothing to measure (no gold files)
    """
    exp = _clean_set(expected_file_ids)
    if not exp:
        return "n/a"
    in_fused = exp & _clean_set(list(fused)[:k_fused])
    if not in_fused:
        return "miss_retrieval"
    in_rerank = exp & _clean_set(list(reranked)[:k_rerank])
    return "retained" if in_rerank else "dropped"


def citation_correct(
    expected_file_ids: Iterable[str], cited: Iterable[str],
) -> bool | None:
    """True when the answer cited at least one gold file. None when there's
    no gold to check against."""
    exp = _clean_set(expected_file_ids)
    if not exp:
        return None
    return bool(exp & _clean_set(cited))


def localise(obs: StageObservation) -> str:
    """Attribute the question to the FIRST stage that lost its gold doc.

    Returns one of: 'ok', 'lost_retrieval', 'lost_rerank',
    'lost_generation', 'refused_correct', 'refused_wrong', 'unscorable'.
    """
    if obs.expected_refusal:
        return "refused_correct" if obs.refused else "refused_wrong"
    if not obs.is_scorable_retrieval():
        return "unscorable"
    outcome = rerank_outcome(obs.expected_file_ids, obs.fused_file_ids,
                             obs.reranked_file_ids)
    if outcome == "miss_retrieval":
        return "lost_retrieval"
    if outcome == "dropped":
        return "lost_rerank"
    if not citation_correct(obs.expected_file_ids, obs.cited_file_ids):
        return "lost_generation"
    return "ok"


# Localisation buckets, ordered for stable reporting.
_LOCALISATION_BUCKETS: tuple[str, ...] = (
    "ok", "lost_retrieval", "lost_rerank", "lost_generation",
    "refused_correct", "refused_wrong", "unscorable",
)


@dataclass(frozen=True)
class StageScore:
    """Aggregated per-stage metrics for one group (a stratum or a domain)."""

    group: str                       # stratum name or domain name
    count: int                       # all rows in the group
    scorable: int                    # rows contributing to recall/rerank
    # Retrieval — recall@k over scorable rows, keyed by k.
    recall_at_k: dict[int, float]
    retrieval_mrr: float
    # Rerank — retention = retained / (retained + dropped).
    rerank_retention: float | None
    rerank_dropped: int
    # Generation.
    citation_accuracy: float | None
    faithfulness_pass_rate: float | None
    # Refusal subset (expected_refusal rows).
    refusal_count: int
    refusal_accuracy: float | None
    # Localisation histogram over the WHOLE group.
    localisation: dict[str, int]
    errors: int


@dataclass(frozen=True)
class StageReport:
    """Top-level per-stage report: overall + by-stratum + by-domain."""

    total: int
    overall: StageScore
    by_stratum: tuple[StageScore, ...] = field(default_factory=tuple)
    by_domain: tuple[StageScore, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        def _s(s: StageScore) -> dict[str, Any]:
            return {
                "group": s.group, "count": s.count, "scorable": s.scorable,
                "recall_at_k": {str(k): v for k, v in s.recall_at_k.items()},
                "retrieval_mrr": s.retrieval_mrr,
                "rerank_retention": s.rerank_retention,
                "rerank_dropped": s.rerank_dropped,
                "citation_accuracy": s.citation_accuracy,
                "faithfulness_pass_rate": s.faithfulness_pass_rate,
                "refusal_count": s.refusal_count,
                "refusal_accuracy": s.refusal_accuracy,
                "localisation": s.localisation,
                "errors": s.errors,
            }
        return {
            "total": self.total,
            "overall": _s(self.overall),
            "by_stratum": [_s(s) for s in self.by_stratum],
            "by_domain": [_s(s) for s in self.by_domain],
        }


def _score_group(group: str, rows: list[StageObservation]) -> StageScore:
    """Aggregate one group's per-stage metrics."""
    n = len(rows)
    scorable = [r for r in rows if r.is_scorable_retrieval()]
    ns = len(scorable)

    recall: dict[int, float] = {}
    for k in RECALL_KS:
        vals = [
            recall_at_k(r.expected_file_ids, r.fused_file_ids, k)
            for r in scorable
        ]
        vals = [v for v in vals if v is not None]
        recall[k] = (sum(vals) / len(vals)) if vals else 0.0

    mrr_vals = [
        reciprocal_rank(r.expected_file_ids, r.fused_file_ids)
        for r in scorable
    ]
    mrr = (sum(mrr_vals) / len(mrr_vals)) if mrr_vals else 0.0

    # Rerank retention only over rows whose gold was actually in the fused
    # set (else the reranker had nothing to retain).
    outcomes = [
        rerank_outcome(r.expected_file_ids, r.fused_file_ids,
                       r.reranked_file_ids)
        for r in scorable
    ]
    retained = sum(1 for o in outcomes if o == "retained")
    dropped = sum(1 for o in outcomes if o == "dropped")
    denom = retained + dropped
    retention = (retained / denom) if denom else None

    cit_vals = [
        citation_correct(r.expected_file_ids, r.cited_file_ids)
        for r in scorable
    ]
    cit_vals = [1.0 if v else 0.0 for v in cit_vals if v is not None]
    cit_acc = (sum(cit_vals) / len(cit_vals)) if cit_vals else None

    # Faithfulness over non-refused scorable rows (reuse the verdict map).
    faith_rows = [r for r in scorable if not r.refused]
    faith = (
        sum(faithfulness_score(r.faithfulness_verdict) for r in faith_rows)
        / len(faith_rows)
    ) if faith_rows else None

    refusal_rows = [r for r in rows if r.expected_refusal]
    refusal_acc = (
        sum(1.0 for r in refusal_rows if r.refused) / len(refusal_rows)
    ) if refusal_rows else None

    loc: dict[str, int] = {b: 0 for b in _LOCALISATION_BUCKETS}
    for r in rows:
        loc[localise(r)] += 1

    return StageScore(
        group=group, count=n, scorable=ns,
        recall_at_k=recall, retrieval_mrr=mrr,
        rerank_retention=retention, rerank_dropped=dropped,
        citation_accuracy=cit_acc, faithfulness_pass_rate=faith,
        refusal_count=len(refusal_rows), refusal_accuracy=refusal_acc,
        localisation=loc,
        errors=sum(1 for r in rows if r.error),
    )


def score_stages(observations: list[StageObservation]) -> StageReport:
    """Aggregate per-stage observations into overall + per-stratum +
    per-domain `StageScore`s (checklist M1 done-when)."""
    if not observations:
        empty = _score_group("overall", [])
        return StageReport(total=0, overall=empty)

    overall = _score_group("overall", observations)

    by_stratum_groups: dict[str, list[StageObservation]] = defaultdict(list)
    by_domain_groups: dict[str, list[StageObservation]] = defaultdict(list)
    for o in observations:
        by_stratum_groups[o.stratum].append(o)
        by_domain_groups[o.domain].append(o)

    # Group by whatever stratum strings actually appear — the verified
    # queries.yaml uses hyphenated names (chain-aware, rare-clause,
    # conflict-resolution, long-form) that don't all match the canonical
    # underscore STRATA tuple; filtering to STRATA would silently drop
    # them from the per-stratum table. Order: canonical strata first (for
    # stable diffs), then any extras, both alphabetical within group.
    seen_strata = set(by_stratum_groups)
    ordered_strata = [s for s in STRATA if s in seen_strata] + sorted(
        seen_strata - set(STRATA)
    )
    by_stratum = tuple(
        _score_group(s, by_stratum_groups[s]) for s in ordered_strata
    )
    by_domain = tuple(
        _score_group(d, by_domain_groups[d])
        for d in sorted(by_domain_groups)
    )

    return StageReport(
        total=len(observations), overall=overall,
        by_stratum=by_stratum, by_domain=by_domain,
    )


# Per-question stage CSV columns.
_STAGE_CSV_FIELDS: tuple[str, ...] = (
    "question_id", "domain", "stratum", "verified", "expected_refusal",
    "n_expected", "citations_resolved",
    "retrieval_rank", "recall_at_10", "recall_at_30",
    "rerank_outcome", "citation_correct",
    "refused", "faithfulness_verdict", "localisation", "error",
)


def write_stage_csv(
    observations: list[StageObservation], out_path: Path | str,
) -> Path:
    """Write one stage-scored row per question for offline analysis."""
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(_STAGE_CSV_FIELDS))
        writer.writeheader()
        for o in observations:
            r10 = recall_at_k(o.expected_file_ids, o.fused_file_ids, 10)
            r30 = recall_at_k(o.expected_file_ids, o.fused_file_ids, 30)
            cc = citation_correct(o.expected_file_ids, o.cited_file_ids)
            writer.writerow({
                "question_id": o.question_id,
                "domain": o.domain,
                "stratum": o.stratum,
                "verified": "1" if o.verified else "0",
                "expected_refusal": "1" if o.expected_refusal else "0",
                "n_expected": len(o.expected_file_ids),
                "citations_resolved": "1" if o.citations_resolved else "0",
                "retrieval_rank": (
                    first_rank(o.expected_file_ids, o.fused_file_ids) or ""
                ),
                "recall_at_10": _fmt(r10),
                "recall_at_30": _fmt(r30),
                "rerank_outcome": rerank_outcome(
                    o.expected_file_ids, o.fused_file_ids, o.reranked_file_ids,
                ),
                "citation_correct": (
                    "" if cc is None else ("1" if cc else "0")
                ),
                "refused": "1" if o.refused else "0",
                "faithfulness_verdict": o.faithfulness_verdict or "",
                "localisation": localise(o),
                "error": o.error or "",
            })
    return p


def render_stage_summary(report: StageReport) -> str:
    """Human-readable per-stage table: overall + per-stratum + per-domain,
    plus the localisation histogram (where accuracy is lost)."""

    def _line(s: StageScore) -> str:
        rec = " ".join(
            f"r@{k}={s.recall_at_k.get(k, 0.0):.2f}" for k in RECALL_KS
        )
        ret = (
            f"{s.rerank_retention:.2f}" if s.rerank_retention is not None
            else "  - "
        )
        cit = (
            f"{s.citation_accuracy:.2f}" if s.citation_accuracy is not None
            else "  - "
        )
        faith = (
            f"{s.faithfulness_pass_rate:.2f}"
            if s.faithfulness_pass_rate is not None else "  - "
        )
        ref = (
            f"{s.refusal_accuracy:.2f}" if s.refusal_accuracy is not None
            else "  - "
        )
        return (
            f"n={s.count:<3d} scor={s.scorable:<3d} {rec} "
            f"mrr={s.retrieval_mrr:.2f} rerank_ret={ret} "
            f"cite={cit} faith={faith} refuse={ref}"
        )

    def _loc(s: StageScore) -> str:
        L = s.localisation
        return (
            f"      └─ ok={L['ok']} retrieval={L['lost_retrieval']} "
            f"rerank={L['lost_rerank']} generation={L['lost_generation']} "
            f"refuse✓={L['refused_correct']} refuse✗={L['refused_wrong']} "
            f"unscorable={L['unscorable']}"
        )

    lines: list[str] = []
    lines.append(f"=== Per-stage Eval ({report.total} questions) ===")
    lines.append(f"OVERALL  {_line(report.overall)}")
    lines.append(_loc(report.overall))
    if report.by_domain:
        lines.append("--- by domain ---")
        for s in report.by_domain:
            lines.append(f"  [{s.group:<14}] {_line(s)}")
            lines.append(_loc(s))
    if report.by_stratum:
        lines.append("--- by stratum ---")
        for s in report.by_stratum:
            lines.append(f"  [{s.group:<14}] {_line(s)}")
            lines.append(_loc(s))
    return "\n".join(lines)


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
