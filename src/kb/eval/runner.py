"""B9 / WA-17 — Eval runner.

Loads the golden_questions.yaml set + drives each question through
POST /chat. Returns one `EvalResult` per question carrying the
question, answer, citations, refusal flag, mode_used, faithfulness
verdict, latency, and the per-question expectations from the YAML
(needed by the scorer).

Pure-async; concurrency is configurable. Idempotency-Key is set
per-call so repeated runs are safe to replay against the same
workspace.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml


_LOG = logging.getLogger(__name__)


# Default golden set ships in-package.
_DEFAULT_QUESTIONS_PATH = Path(__file__).parent / "golden_questions.yaml"


# Stratum labels mirror the YAML.
STRATA: tuple[str, ...] = (
    "needle", "rare_clause", "adversarial", "synthesis",
    "ambiguous", "negative", "aggregation", "chain_aware", "conflict",
)


@dataclass(frozen=True)
class GoldenQuestion:
    id: str
    stratum: str
    text: str
    # Expected outcomes — consumed by the scorer.
    keywords: tuple[str, ...] = field(default_factory=tuple)
    must_refuse: bool = False
    min_citations: int = 0
    mode_hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "stratum": self.stratum,
            "text": self.text,
            "keywords": list(self.keywords),
            "must_refuse": self.must_refuse,
            "min_citations": self.min_citations,
            "mode_hint": self.mode_hint,
        }


@dataclass(frozen=True)
class EvalResult:
    question: GoldenQuestion
    # Outcomes from /chat.
    http_status: int
    query_id: str | None
    answer: str
    refused: bool
    refusal_reason: str | None
    citations_count: int
    citation_modalities: tuple[str, ...]
    mode: str | None
    intent: str | None
    faithfulness_verdict: str | None
    faithfulness_score: float | None
    latency_ms: int
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question.id,
            "stratum": self.question.stratum,
            "text": self.question.text,
            "http_status": self.http_status,
            "query_id": self.query_id,
            "answer": self.answer,
            "refused": self.refused,
            "refusal_reason": self.refusal_reason,
            "citations_count": self.citations_count,
            "citation_modalities": list(self.citation_modalities),
            "mode": self.mode,
            "intent": self.intent,
            "faithfulness_verdict": self.faithfulness_verdict,
            "faithfulness_score": self.faithfulness_score,
            "latency_ms": self.latency_ms,
            "error": self.error,
            "must_refuse": self.question.must_refuse,
            "min_citations": self.question.min_citations,
            "keywords": list(self.question.keywords),
        }


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_golden_questions(
    path: Path | str | None = None,
) -> list[GoldenQuestion]:
    """Parse the YAML golden set into typed records. Validates that
    every question carries id + stratum + text and that the stratum
    is one of the 9 spec values.

    Raises ValueError on a malformed file."""
    src = Path(path) if path else _DEFAULT_QUESTIONS_PATH
    raw = yaml.safe_load(src.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{src} root must be a mapping")
    items = raw.get("questions") or []
    if not isinstance(items, list):
        raise ValueError(f"{src} 'questions' must be a list")

    out: list[GoldenQuestion] = []
    seen_ids: set[str] = set()
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"questions[{i}] must be a mapping")
        qid = str(item.get("id") or "").strip()
        stratum = str(item.get("stratum") or "").strip()
        text = str(item.get("text") or "").strip()
        if not qid:
            raise ValueError(f"questions[{i}] missing id")
        if qid in seen_ids:
            raise ValueError(f"duplicate question id {qid!r}")
        seen_ids.add(qid)
        if stratum not in STRATA:
            raise ValueError(
                f"question {qid!r} stratum={stratum!r} must be one of {list(STRATA)}"
            )
        if not text:
            raise ValueError(f"question {qid!r} missing text")
        expected = item.get("expected") or {}
        if not isinstance(expected, dict):
            raise ValueError(f"question {qid!r} expected must be a mapping")
        raw_keywords = expected.get("keywords") or []
        if not isinstance(raw_keywords, list):
            raise ValueError(f"question {qid!r} expected.keywords must be a list")
        keywords = tuple(str(k) for k in raw_keywords if isinstance(k, str))
        out.append(GoldenQuestion(
            id=qid,
            stratum=stratum,
            text=text,
            keywords=keywords,
            must_refuse=bool(expected.get("must_refuse", False)),
            min_citations=int(expected.get("min_citations", 0)),
            mode_hint=(
                str(expected["mode_hint"])
                if isinstance(expected.get("mode_hint"), str) else None
            ),
        ))
    return out


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def _ask_one(
    client: httpx.AsyncClient,
    question: GoldenQuestion,
    *,
    workspace_id: str,
) -> EvalResult:
    """Run one POST /chat. Never raises — failures become EvalResult.error."""
    body: dict[str, Any] = {"query": question.text}
    if question.mode_hint:
        body["mode"] = question.mode_hint
    else:
        body["mode"] = "H"
    headers = {
        "X-Test-Workspace": workspace_id,
        "Idempotency-Key": str(uuid.uuid4()),
    }
    try:
        resp = await client.post("/chat", json=body, headers=headers)
    except Exception as exc:  # noqa: BLE001
        return EvalResult(
            question=question, http_status=0,
            query_id=None, answer="",
            refused=False, refusal_reason=None,
            citations_count=0, citation_modalities=(),
            mode=None, intent=None,
            faithfulness_verdict=None, faithfulness_score=None,
            latency_ms=0, error=f"HTTP error: {exc}",
        )

    if resp.status_code != 200:
        return EvalResult(
            question=question, http_status=resp.status_code,
            query_id=None, answer="",
            refused=False, refusal_reason=None,
            citations_count=0, citation_modalities=(),
            mode=None, intent=None,
            faithfulness_verdict=None, faithfulness_score=None,
            latency_ms=0,
            error=f"HTTP {resp.status_code}: {resp.text[:200]}",
        )

    payload = resp.json()
    gen = payload.get("generation") or {}
    citations = gen.get("citations") or []
    modalities = payload.get("citation_modalities") or []

    return EvalResult(
        question=question,
        http_status=200,
        query_id=payload.get("query_id"),
        answer=str(gen.get("answer") or ""),
        refused=bool(gen.get("refused", False)),
        refusal_reason=gen.get("refusal_reason"),
        citations_count=len(citations),
        citation_modalities=tuple(
            str(m) for m in modalities if isinstance(m, str)
        ),
        mode=payload.get("mode"),
        intent=payload.get("intent"),
        faithfulness_verdict=payload.get("faithfulness_verdict"),
        faithfulness_score=payload.get("faithfulness_score"),
        latency_ms=int(payload.get("latency_ms") or 0),
        error=None,
    )


async def run_eval(
    client: httpx.AsyncClient,
    questions: list[GoldenQuestion],
    *,
    workspace_id: str,
    concurrency: int = 2,
) -> list[EvalResult]:
    """Drive each question through POST /chat. Returns one EvalResult
    per question — order matches `questions` input."""
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _bound(q: GoldenQuestion) -> EvalResult:
        async with sem:
            return await _ask_one(client, q, workspace_id=workspace_id)

    results = await asyncio.gather(*[_bound(q) for q in questions])
    return list(results)
