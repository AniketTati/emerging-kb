"""B3 / WA-8 — HHEM-2.1-style faithfulness gate.

Architecture §6 step 9 (gate A): a post-generation check that scores how
well each claim in the answer is supported by its citation snippets. The
orchestrator consults the verdict + score to decide:

  - 'pass'           → return the answer
  - 'low_confidence' → return the answer with a low_confidence flag
                       attached (UI surfaces a warning badge)
  - 'refused'        → regenerate (max 2 retries) then abstain

Three impls (mirrors the CRAG factory pattern at kb/query/crag.py):

  - IdentityFaithfulnessGate — always 'pass'. Fail-safe default for CI.
  - HeuristicFaithfulnessGate — pure-Python token-overlap. Splits the
    answer into sentences, scores each by Jaccard overlap with the
    concatenated citation snippets. Cheap + deterministic.
  - HHEMFaithfulnessGate — lazy-imports `vectara/hallucination_evaluation_model`
    (Vectara HHEM-2.1, ~600MB local) when KB_FAITHFULNESS_GATE=hhem and the
    package is installed. Per-sentence inference.

Selection:
  KB_FAITHFULNESS_GATE ∈ {identity, heuristic, hhem, auto}
    auto → 'identity' (fail-safe default for the demo). Switch to 'hhem'
    explicitly when the model is available.

Verdict bands (Design 7 §"two-judge" sketch + architecture §9 Moment 3):
  score >= PASS_THRESHOLD          → 'pass'
  LOW_CONFIDENCE_THRESHOLD ≤ score < PASS_THRESHOLD → 'low_confidence'
  score < LOW_CONFIDENCE_THRESHOLD → 'refused'
  no answer / no citations         → 'skipped'
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol

from kb.query.llm_client import JsonLLMClient, LLMCallError


# ---------------------------------------------------------------------------
# Verdict thresholds + constants
# ---------------------------------------------------------------------------


FAITHFULNESS_VERDICTS: tuple[str, ...] = (
    "pass", "low_confidence", "refused", "skipped",
)

# Architecture §6 step 9: pass / regen-retry / refuse cascade.
# These are the HHEM-calibrated thresholds. The Heuristic gate uses
# looser thresholds (see `_HEURISTIC_PASS_THRESHOLD` below) because
# token-overlap Jaccard naturally scores lower than NLI entailment
# on legitimate paraphrased prose.
PASS_THRESHOLD: float = 0.80
LOW_CONFIDENCE_THRESHOLD: float = 0.50

# Heuristic-gate-specific thresholds. Construction eval (50 queries on
# 46 docs) showed the prior 0.30/0.50 calibration was too strict — it
# was refusing 10 valid answers including chain-walk summaries and
# multi-line factoids where paraphrased prose drops Jaccard overlap
# below 0.30 even though the answer's claims ARE grounded.
#
# Looser defaults (0.15 refuse / 0.40 pass) widen the low_confidence
# band so paraphrased correct answers pass through with a soft
# warning rather than being hidden entirely. Genuine hallucinations
# (Zorblax-9000 scoring 0.05-0.10) still get refused.
#
# Env-configurable so we can tune per-deployment without rebuild:
#   KB_FAITHFULNESS_HEURISTIC_PASS_THRESHOLD
#   KB_FAITHFULNESS_HEURISTIC_REFUSE_THRESHOLD
def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


_HEURISTIC_PASS_THRESHOLD: float = _env_float(
    "KB_FAITHFULNESS_HEURISTIC_PASS_THRESHOLD", 0.40
)
_HEURISTIC_REFUSE_THRESHOLD: float = _env_float(
    "KB_FAITHFULNESS_HEURISTIC_REFUSE_THRESHOLD", 0.15
)

# Architecture §6 step 9 — "max 2 retries". Orchestrator counter.
MAX_REGENERATIONS: int = 2


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FaithfulnessResult:
    """Verdict + score + per-claim breakdown. The orchestrator persists
    `verdict`, `score`, and `regenerations` to query_log."""
    verdict: str          # one of FAITHFULNESS_VERDICTS
    score: float          # 0.0 - 1.0
    per_claim_scores: tuple[float, ...] = field(default_factory=tuple)
    notes: str | None = None
    model_id: str = ""


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class FaithfulnessGate(Protocol):
    async def assess(
        self,
        answer: str,
        citation_snippets: Iterable[str],
        *,
        model_id_hint: str = "",
    ) -> FaithfulnessResult: ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"\[])")
_WORD = re.compile(r"\w+")


def split_sentences(text: str) -> list[str]:
    """Cheap sentence splitter. Good enough for English answers — avoids a
    spaCy / nltk dependency. Returns at least one sentence (the whole
    answer) when no terminal punctuation is found."""
    text = (text or "").strip()
    if not text:
        return []
    parts = [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]
    return parts or [text]


def verdict_from_score(
    score: float,
    *,
    pass_threshold: float = PASS_THRESHOLD,
    refuse_threshold: float = LOW_CONFIDENCE_THRESHOLD,
) -> str:
    """Apply the band thresholds. Pure-function. Gates can override
    via the kwargs — Heuristic uses looser thresholds since Jaccard
    naturally scores lower than NLI entailment on paraphrased prose."""
    if score >= pass_threshold:
        return "pass"
    if score >= refuse_threshold:
        return "low_confidence"
    return "refused"


def should_regenerate(verdict: str, regenerations: int) -> bool:
    """Architecture §6 step 9: 'refused' verdict + retries remaining → regen.
    'low_confidence' is surfaced as-is (the UI badges it)."""
    return verdict == "refused" and regenerations < MAX_REGENERATIONS


def _tokens(text: str) -> set[str]:
    return {m.group(0).lower() for m in _WORD.finditer(text or "")}


def _stripped_tokens(text: str) -> set[str]:
    """Lowercased word tokens minus a tiny stop-word set, for the heuristic
    overlap scorer."""
    toks = _tokens(text)
    return {t for t in toks if t not in _STOPWORDS and len(t) > 1}


_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "at",
    "for", "with", "by", "from", "as", "is", "are", "was", "were", "be",
    "been", "this", "that", "these", "those", "it", "its", "his", "her",
    "their", "our", "your", "my", "we", "they", "i", "you", "not", "no",
    "do", "does", "did", "have", "has", "had", "can", "could", "would",
    "should", "may", "might", "will", "shall",
}


# ---------------------------------------------------------------------------
# IdentityFaithfulnessGate — fail-safe pass
# ---------------------------------------------------------------------------


class IdentityFaithfulnessGate:
    """Always returns 'pass' with score=1.0. CI / demo-default path —
    consistent with the existing CRAG IdentityGate pattern."""

    MODEL_ID = "identity"

    async def assess(
        self,
        answer: str,
        citation_snippets: Iterable[str],
        *,
        model_id_hint: str = "",
    ) -> FaithfulnessResult:
        if not (answer or "").strip():
            return FaithfulnessResult(
                verdict="skipped", score=0.0,
                notes="empty answer", model_id=self.MODEL_ID,
            )
        return FaithfulnessResult(
            verdict="pass", score=1.0, model_id=self.MODEL_ID,
        )


# ---------------------------------------------------------------------------
# HeuristicFaithfulnessGate — token-overlap baseline
# ---------------------------------------------------------------------------


class HeuristicFaithfulnessGate:
    """Pure-Python Jaccard overlap per sentence vs concatenated citation
    snippets. Deterministic + fast — runs in any CI environment.

    Score per sentence = |sentence_tokens ∩ snippet_tokens| / |sentence_tokens|
    Average across sentences → overall score.

    Cheap heuristic — NOT a real hallucination detector. Useful as the
    'Heuristic' tier in the gate ladder (Identity → Heuristic → HHEM)."""

    MODEL_ID = "heuristic-jaccard-v1"

    async def assess(
        self,
        answer: str,
        citation_snippets: Iterable[str],
        *,
        model_id_hint: str = "",
    ) -> FaithfulnessResult:
        sentences = split_sentences(answer)
        if not sentences:
            return FaithfulnessResult(
                verdict="skipped", score=0.0,
                notes="empty answer", model_id=self.MODEL_ID,
            )
        snippets_text = " ".join(citation_snippets or [])
        snippet_tokens = _stripped_tokens(snippets_text)
        if not snippet_tokens:
            # No citations to ground against — must refuse per architecture.
            return FaithfulnessResult(
                verdict="refused", score=0.0,
                per_claim_scores=tuple(0.0 for _ in sentences),
                notes="no citation snippets to ground against",
                model_id=self.MODEL_ID,
            )

        per: list[float] = []
        for sent in sentences:
            sent_toks = _stripped_tokens(sent)
            if not sent_toks:
                # No content words (e.g. "Yes." or "Sure!") — neutral.
                per.append(1.0)
                continue
            overlap = sent_toks & snippet_tokens
            per.append(len(overlap) / max(1, len(sent_toks)))

        overall = sum(per) / max(1, len(per))
        return FaithfulnessResult(
            verdict=verdict_from_score(
                overall,
                pass_threshold=_HEURISTIC_PASS_THRESHOLD,
                refuse_threshold=_HEURISTIC_REFUSE_THRESHOLD,
            ),
            score=overall,
            per_claim_scores=tuple(per),
            notes=None,
            model_id=self.MODEL_ID,
        )


# ---------------------------------------------------------------------------
# HHEMFaithfulnessGate — Vectara HHEM-2.1 (lazy import)
# ---------------------------------------------------------------------------


class HHEMFaithfulnessGate:
    """Vectara HHEM-2.1 local model. ~600MB checkpoint, ~200ms per
    sentence on CPU. Lazy-imports `transformers` so this module stays
    importable in CI without the dependency."""

    MODEL_ID = "vectara/hhem-2.1"

    def __init__(self, *, model: Any | None = None) -> None:
        self._model = model

    def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        # Lazy import — only required at first call.
        try:
            from transformers import AutoModelForSequenceClassification  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "HHEMFaithfulnessGate requires `transformers`. "
                "Install with: pip install transformers torch"
            ) from exc
        self._model = AutoModelForSequenceClassification.from_pretrained(
            "vectara/hallucination_evaluation_model",
            trust_remote_code=True,
        )
        return self._model

    async def assess(
        self,
        answer: str,
        citation_snippets: Iterable[str],
        *,
        model_id_hint: str = "",
    ) -> FaithfulnessResult:
        sentences = split_sentences(answer)
        if not sentences:
            return FaithfulnessResult(
                verdict="skipped", score=0.0,
                notes="empty answer", model_id=self.MODEL_ID,
            )
        premise = " ".join(citation_snippets or [])
        if not premise.strip():
            return FaithfulnessResult(
                verdict="refused", score=0.0,
                per_claim_scores=tuple(0.0 for _ in sentences),
                notes="no citation snippets to ground against",
                model_id=self.MODEL_ID,
            )

        try:
            model = self._ensure_model()
        except RuntimeError as exc:
            # Mis-configured environment → fail-safe pass + note (mirrors
            # CRAG #7 rationale). The orchestrator will still surface the
            # answer; the verdict notes flag the degradation.
            return FaithfulnessResult(
                verdict="pass", score=1.0,
                notes=f"hhem unavailable: {exc}", model_id=self.MODEL_ID,
            )

        pairs = [(premise, s) for s in sentences]
        try:
            scores = model.predict(pairs)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            return FaithfulnessResult(
                verdict="pass", score=1.0,
                notes=f"hhem predict failed: {exc}",
                model_id=self.MODEL_ID,
            )

        per = tuple(max(0.0, min(1.0, float(s))) for s in scores)
        overall = sum(per) / max(1, len(per))
        return FaithfulnessResult(
            verdict=verdict_from_score(overall),
            score=overall,
            per_claim_scores=per,
            model_id=self.MODEL_ID,
        )


# ---------------------------------------------------------------------------
# LLMFaithfulnessGate — claim-decomposition + per-claim entailment (D6)
# ---------------------------------------------------------------------------


_LLM_FAITHFULNESS_SYSTEM_PROMPT = (
    "You are a strict faithfulness judge for a knowledge-base answer. You "
    "are given CONTEXT snippets — the ONLY evidence the answer is allowed "
    "to use — and a numbered list of CLAIMS taken from the answer. For "
    "each claim decide whether it is SUPPORTED: a reader could verify the "
    "claim from the context snippets alone. A claim that asserts a fact "
    "absent from the context is NOT supported, even if it is plausibly "
    "true. A claim whose number/date/name CONTRADICTS the context is NOT "
    "supported. Pure framing sentences with no factual content (e.g. "
    "'Here is what I found.') count as supported.\n\n"
    "Return STRICTLY a JSON object: "
    "{\"verdicts\": [{\"claim\": <int 1-based>, \"supported\": <bool>}]} "
    "— exactly one entry per claim, in order. No prose."
)


def _build_faithfulness_user_prompt(
    claims: list[str], snippets: list[str],
) -> str:
    ctx = "\n".join(f"[S{i + 1}] {s}" for i, s in enumerate(snippets))
    cl = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(claims))
    return f"CONTEXT:\n{ctx}\n\nCLAIMS:\n{cl}\n\nReturn JSON only."


def _parse_faithfulness_verdicts(raw: str, n_claims: int) -> list[float] | None:
    """Parse the judge JSON into a per-claim [1.0|0.0] list aligned to the
    `n_claims` claims. Returns None when the response is unusable so the
    caller can fail-safe pass — never raises.

    A claim the judge omitted defaults to SUPPORTED (1.0): we don't punish
    a grounded answer for a judge omission, matching the gate ladder's
    fail-safe posture (Identity/HHEM also pass on degradation)."""
    text = (raw or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    verdicts = data.get("verdicts") if isinstance(data, dict) else data
    if not isinstance(verdicts, list) or not verdicts:
        return None
    by_idx: dict[int, bool] = {}
    for j, item in enumerate(verdicts):
        if not isinstance(item, dict):
            continue
        idx = item.get("claim")
        if not isinstance(idx, int) or isinstance(idx, bool):
            idx = j + 1  # positional fallback when the judge drops the index
        by_idx[idx] = bool(item.get("supported"))
    if not by_idx:
        return None
    return [1.0 if by_idx.get(i + 1, True) else 0.0 for i in range(n_claims)]


class LLMFaithfulnessGate:
    """Claim-decomposition + per-claim entailment via a (lite) LLM — the
    D6 deliverable that replaces the Jaccard heuristic's weak token-overlap
    signal, which under-scores legitimately-grounded paraphrased/numeric
    answers (e.g. a correct '9.4%' answer scored 0.15, on the refuse cliff).

    Decomposes the answer into atomic claims (sentences) and asks the LLM —
    in ONE batched call — whether each claim is supported by the cited
    snippets. Per-claim supported→1.0 / not→0.0, averaged → overall score →
    `verdict_from_score` on the HHEM-calibrated bands (entailment scores
    like NLI, not Jaccard, so the 0.80/0.50 thresholds apply).

    Provider-neutral: takes any `JsonLLMClient`. Runs once per answered
    query post-generation, so a lite model is the intended fit. On any LLM
    transport/shape failure it fail-safe PASSES (never refuse a good answer
    because the judge hiccupped) — same rationale as the HHEM gate."""

    def __init__(self, llm: JsonLLMClient) -> None:
        self._llm = llm
        self.model_id = f"llm-faithfulness:{getattr(llm, 'model_id', '') or '?'}"

    async def assess(
        self,
        answer: str,
        citation_snippets: Iterable[str],
        *,
        model_id_hint: str = "",
    ) -> FaithfulnessResult:
        claims = split_sentences(answer)
        if not claims:
            return FaithfulnessResult(
                verdict="skipped", score=0.0,
                notes="empty answer", model_id=self.model_id,
            )
        snippets = [s for s in (citation_snippets or []) if (s or "").strip()]
        if not snippets:
            # No evidence to ground against — refuse (architecture §6 step 9).
            return FaithfulnessResult(
                verdict="refused", score=0.0,
                per_claim_scores=tuple(0.0 for _ in claims),
                notes="no citation snippets to ground against",
                model_id=self.model_id,
            )
        try:
            raw = await self._llm.generate_json(
                user=_build_faithfulness_user_prompt(claims, snippets),
                system=_LLM_FAITHFULNESS_SYSTEM_PROMPT,
                max_tokens=512,
            )
        except LLMCallError as exc:
            return FaithfulnessResult(
                verdict="pass", score=1.0,
                notes=f"llm gate unavailable: {exc}",
                model_id=self.model_id,
            )
        per = _parse_faithfulness_verdicts(raw, len(claims))
        if per is None:
            # Unusable judge output — fail-safe pass with a note.
            return FaithfulnessResult(
                verdict="pass", score=1.0,
                notes="llm gate unparseable verdicts",
                model_id=self.model_id,
            )
        overall = sum(per) / max(1, len(per))
        return FaithfulnessResult(
            verdict=verdict_from_score(overall),
            score=overall,
            per_claim_scores=tuple(per),
            model_id=self.model_id,
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_faithfulness_gate() -> FaithfulnessGate:
    """Pick a gate based on `KB_FAITHFULNESS_GATE`.

      identity  → IdentityFaithfulnessGate (no-op, always passes —
                  use only when you explicitly want to disable the gate)
      heuristic → HeuristicFaithfulnessGate (pure-Python Jaccard;
                  catches obvious hallucinations like made-up vendor
                  names because the proper-noun tokens won't appear in
                  any cited snippet)
      hhem      → HHEMFaithfulnessGate (~600MB local model, real
                  per-sentence entailment scoring)
      auto      → heuristic. We used to default to identity but that
                  let the chat surface invent details about made-up
                  entities ("Zorblax-9000 contract" got a full answer
                  citing real MSA docs). Heuristic catches that without
                  needing a model checkpoint; explicit 'hhem' upgrades
                  to real entailment when the model is available.
    """
    selector = (os.environ.get("KB_FAITHFULNESS_GATE") or "auto").lower()
    if selector == "auto":
        selector = "heuristic"
    if selector == "identity":
        return IdentityFaithfulnessGate()
    if selector == "heuristic":
        return HeuristicFaithfulnessGate()
    if selector == "hhem":
        return HHEMFaithfulnessGate()
    raise ValueError(
        f"Unknown KB_FAITHFULNESS_GATE value: {selector!r} "
        f"(expected 'identity', 'heuristic', 'hhem', or 'auto')"
    )
