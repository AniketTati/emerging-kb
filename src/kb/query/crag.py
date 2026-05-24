"""Phase 8d — Corrective RAG (CRAG) relevance gate.

Per build_tracker §5.15.4 (10 locked decisions). Per CRAG paper (Yan et al.
2024). Cheap LLM-judge of top-3 rerank output → confidence score (0-1).
Orchestrator (Phase 8f) refuses with "insufficient evidence" when below
CRAG_THRESHOLD.

Two impls (decision #10 defers Anthropic for Wave A):
  - GeminiCragGate: real Gemini judge call
  - IdentityCragGate: always returns 1.0 (passes — fail-safe)

Factory reuses `KB_QUERY_LLM` env (same family as 8a — KB_QUERY_LLM=
anthropic maps to Identity per decision #10).
"""

from __future__ import annotations

import json
import os
from typing import Any, Protocol

from kb.query.rrf import Hit


# Decision #2: confidence threshold below which orchestrator refuses with
# "insufficient evidence" message.
CRAG_THRESHOLD = 0.5

# Decision #3: only top-N snippets fed to LLM (cost cap).
_TOP_N_SNIPPETS = 3

# Decision #9: max output tokens (one float in tiny JSON).
_MAX_OUTPUT_TOKENS = 100

_SYSTEM_PROMPT = (
    "You are a relevance judge. Given a user query and up to 3 candidate "
    "snippets retrieved by a search system, return a single JSON object: "
    "{\"avg_relevance\": 0.0-1.0}. 1.0 = all snippets directly answer the "
    "query. 0.0 = none are relevant. Be honest — judging too generously "
    "hurts downstream answer quality."
)


class CragGate(Protocol):
    async def assess(self, query: str, hits: list[Hit]) -> float: ...


# ---------------------------------------------------------------------------
# IdentityCragGate — fail-safe pass
# ---------------------------------------------------------------------------


class IdentityCragGate:
    """Always returns 1.0 (passes). CI / no-key path.

    Per decision #6: degrades quality (no relevance gate) but doesn't block
    the query. Same fail-safe rationale as Phase 8a #7."""

    async def assess(self, query: str, hits: list[Hit]) -> float:
        return 1.0


# ---------------------------------------------------------------------------
# Parser (decision #4)
# ---------------------------------------------------------------------------


def _parse_score(raw: str) -> float:
    """Parse `{"avg_relevance": 0.0-1.0}` JSON. Tolerant + fail-safe:
    - Strip ```json ... ``` fences.
    - Invalid JSON / non-dict / missing key / non-numeric value → 1.0 (pass).
    - Numeric value clamped to [0, 1].
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 2 and lines[-1].strip() == "```":
            lines = lines[1:-1]
        else:
            lines = lines[1:]
        text = "\n".join(lines)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return 1.0
    if not isinstance(data, dict):
        return 1.0
    raw_val = data.get("avg_relevance")
    if raw_val is None:
        return 1.0
    try:
        v = float(raw_val)
    except (TypeError, ValueError):
        return 1.0
    return max(0.0, min(1.0, v))


def _build_user_prompt(query: str, hits: list[Hit]) -> str:
    """Build the user message — top-3 snippets per decision #3."""
    snippets = "\n\n".join(
        f"[Snippet {i+1}] {(h.snippet or '')[:500]}"
        for i, h in enumerate(hits[:_TOP_N_SNIPPETS])
    )
    return f"Query: {query}\n\n{snippets}\n\nReturn JSON only."


# ---------------------------------------------------------------------------
# GeminiCragGate
# ---------------------------------------------------------------------------


class GeminiCragGate:
    """Gemini judges relevance. Cheap call (single float output)."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: Any | None = None,
    ) -> None:
        if client is None:
            if not api_key:
                raise ValueError("GeminiCragGate requires api_key or client")
            from google.genai import Client
            client = Client(api_key=api_key)
        self._client = client
        self._model = os.environ.get("KB_QUERY_MODEL") or "gemini-2.5-flash"

    async def assess(self, query: str, hits: list[Hit]) -> float:
        # Decision #5: empty hits = guaranteed refusal.
        if not hits:
            return 0.0

        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=_SYSTEM_PROMPT,
            max_output_tokens=_MAX_OUTPUT_TOKENS,
            response_mime_type="application/json",
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )

        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=_build_user_prompt(query, hits),
                config=config,
            )
        except Exception:
            # Decision #7: error → fail-safe pass (1.0).
            return 1.0

        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return 1.0

        raw_text = ""
        content = getattr(candidates[0], "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            t = getattr(part, "text", None)
            if t:
                raw_text = t
                break

        return _parse_score(raw_text)


# ---------------------------------------------------------------------------
# Factory — KB_QUERY_LLM selector
# ---------------------------------------------------------------------------


def make_crag_gate() -> CragGate:
    """Pick a CRAG gate based on `KB_QUERY_LLM`.

    Decision #1 + #10:
      - gemini → GeminiCragGate (requires KB_GEMINI_API_KEY)
      - anthropic → IdentityCragGate (Wave A defer; per decision #10)
      - identity → IdentityCragGate
      - auto → gemini if key else identity
    """
    selector = (os.environ.get("KB_QUERY_LLM") or "auto").lower()

    if selector == "auto":
        if os.environ.get("KB_GEMINI_API_KEY"):
            selector = "gemini"
        else:
            # Skip Anthropic auto-probe — decision #10 maps it to Identity anyway.
            selector = "identity"

    if selector == "gemini":
        api_key = os.environ.get("KB_GEMINI_API_KEY")
        if not api_key:
            raise ValueError(
                "KB_QUERY_LLM=gemini requires KB_GEMINI_API_KEY"
            )
        return GeminiCragGate(api_key=api_key)

    if selector == "anthropic":
        # Decision #10: Wave A maps Anthropic CRAG to Identity (defer real
        # impl to Wave B once we've tested it).
        return IdentityCragGate()

    if selector == "identity":
        return IdentityCragGate()

    raise ValueError(
        f"Unknown KB_QUERY_LLM value: {selector!r} "
        f"(expected 'gemini', 'anthropic', 'identity', or 'auto')"
    )
