"""Phase 8a — query rewriting (Step-Back + HyDE + Query2Doc).

Per build_tracker §5.15.1 (10 locked decisions). Architecture §6 step 4.

Three rewriting strategies in one LLM call:
  - Step-Back: more abstract version capturing the underlying concept
    (Zheng et al. 2024, "Take a Step Back").
  - HyDE: synthetic "ideal answer" paragraph that would match relevant
    documents (Gao et al. 2022, "Precise Zero-Shot Dense Retrieval Without
    Relevance Labels").
  - Query2Doc: search-friendly expansion enriched with synonyms/keywords
    (Wang et al. 2023, "Query2doc: Query Expansion with LLMs").

Output: 4 query variants (original + 3 rewrites) for Phase 8b's parallel
retrieval channels to run against. Identity fallback returns the original
query for all 3 slots (no expansion — degraded recall but functional).

Factory `make_query_rewriter()` reads `KB_QUERY_LLM ∈ {gemini, anthropic,
identity, auto}`. Same pattern as 3b-bis/3d/5a/5b/5c/6/7.
"""

from __future__ import annotations

import json
import os
from typing import Any, Protocol

from pydantic import BaseModel


DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_ANTHROPIC_MODEL = "claude-opus-4-7"
_MAX_OUTPUT_TOKENS = 900  # ~200 tokens × 3 rewrites + ~300 tokens for ToC

# Tree-of-Clarifications cap. Architecture §6 step 2 says "2-4 branches".
# We default to 3 to match the HyDE×N=3 ensemble cadence.
_TOC_MAX_BRANCHES = 4

_SYSTEM_PROMPT = (
    "You are a query-rewriting system for retrieval-augmented generation. "
    "Given a user query, produce 3 reformulations plus an optional "
    "Tree-of-Clarifications branch set when the query is ambiguous.\n"
    "  - step_back: a more abstract version capturing the underlying concept.\n"
    "  - hyde: a synthetic 'ideal answer' paragraph that would match documents.\n"
    "  - query2doc: a search-friendly expansion enriched with synonyms/keywords.\n"
    "  - clarifications: a list of 2-4 disambiguated rewrites IF the query is "
    "    ambiguous (multiple plausible interpretations of a key term, entity, "
    "    or scope). Each branch should be a complete standalone query that "
    "    resolves the ambiguity one way. If the query is clear / unambiguous, "
    "    return an empty list. Examples of ambiguous queries: 'Tell me about "
    "    the Q1 results' (Q1 of which year?), 'What's the rate?' (which tier? "
    "    which contract?), 'How is John doing?' (which John? on what axis?).\n"
    "Output JSON exactly: "
    "{\"step_back\": str, \"hyde\": str, \"query2doc\": str, "
    " \"clarifications\": list[str]}."
)


class Rewrites(BaseModel):
    """4 query variants + optional Tree-of-Clarifications branches.

    `original` passes through unchanged for fidelity in case the rewrites
    drift semantically. `clarifications` is empty for clear queries and
    populated (2-4 entries) when the LLM detected ambiguity per
    architecture §6 step 2 (Tree-of-Clarifications, Kim et al. 2023).
    """

    original: str
    step_back: str
    hyde: str
    query2doc: str
    # Empty list when the query is unambiguous. When populated, each
    # entry is a fully-formed disambiguated query that the retrieval
    # channels run alongside the other 4 variants. RRF dedupes overlap.
    clarifications: list[str] = []


class QueryRewriter(Protocol):
    async def rewrite(self, query: str) -> Rewrites: ...


# ---------------------------------------------------------------------------
# Identity fallback
# ---------------------------------------------------------------------------


class IdentityQueryRewriter:
    """Returns the original query for all 3 slots. CI / no-key path.

    `model_id` semantics: there's no model — just a passthrough. Phase 8b
    still runs all 4 variants through retrieval channels; deduplication
    happens at RRF (since 4 identical queries → same hits → same ranks)."""

    async def rewrite(self, query: str) -> Rewrites:
        return Rewrites(
            original=query, step_back=query, hyde=query, query2doc=query,
        )


# ---------------------------------------------------------------------------
# Shared parser
# ---------------------------------------------------------------------------


def _parse_rewrites(raw: str, *, original: str) -> Rewrites:
    """Parse `{"step_back": ..., "hyde": ..., "query2doc": ...}` JSON.

    Fail-soft (decision #3): malformed JSON, missing keys, non-string values,
    or top-level non-dict all degrade to original-text in the affected slot.
    Worker doesn't fail on rewriting failure (decision #7).
    """
    text = (raw or "").strip()
    # Strip ```json ... ``` code fences if present.
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
        return Rewrites(
            original=original, step_back=original, hyde=original, query2doc=original,
        )
    if not isinstance(data, dict):
        return Rewrites(
            original=original, step_back=original, hyde=original, query2doc=original,
        )

    def _safe(key: str) -> str:
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            return v
        return original

    # Tree-of-Clarifications: parse the list, drop empty/non-string
    # entries, dedupe against the original (we already have it), cap
    # at _TOC_MAX_BRANCHES.
    clarifications: list[str] = []
    raw_clarifications = data.get("clarifications") or []
    if isinstance(raw_clarifications, list):
        seen: set[str] = {original.strip().lower()}
        for c in raw_clarifications:
            if not isinstance(c, str):
                continue
            cs = c.strip()
            if not cs or cs.lower() in seen:
                continue
            seen.add(cs.lower())
            clarifications.append(cs)
            if len(clarifications) >= _TOC_MAX_BRANCHES:
                break

    return Rewrites(
        original=original,
        step_back=_safe("step_back"),
        hyde=_safe("hyde"),
        query2doc=_safe("query2doc"),
        clarifications=clarifications,
    )


# ---------------------------------------------------------------------------
# GeminiQueryRewriter
# ---------------------------------------------------------------------------


class GeminiQueryRewriter:
    """Single Gemini call with response_mime_type=application/json returns all
    3 rewrites. Identity-fallback on any exception or empty candidates."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: Any | None = None,
        model: str | None = None,
    ) -> None:
        if client is None:
            if not api_key:
                raise ValueError("GeminiQueryRewriter requires api_key or client")
            from google.genai import Client
            client = Client(api_key=api_key)
        self._client = client
        self._model = (
            model or os.environ.get("KB_QUERY_MODEL") or DEFAULT_GEMINI_MODEL
        )

    async def rewrite(self, query: str) -> Rewrites:
        from google.genai import types

        # Re-read env at call time so tests can swap KB_QUERY_MODEL per call.
        model = os.environ.get("KB_QUERY_MODEL") or self._model

        config = types.GenerateContentConfig(
            system_instruction=_SYSTEM_PROMPT,
            max_output_tokens=_MAX_OUTPUT_TOKENS,
            response_mime_type="application/json",
            # Query rewriting is the ONE place where diversity is the
            # point — step_back / HyDE / query2doc rewrites should
            # phrase the same intent in different ways. 0.5 keeps
            # rewrites varied without going gibberish-creative. See
            # docs/RAG_AUDIT_AND_ACTION_PLAN.md Phase 1.1.
            temperature=0.5,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )

        try:
            response = await self._client.aio.models.generate_content(
                model=model,
                contents=f"Query: {query}",
                config=config,
            )
        except Exception:
            # Decision #7: any error → return original for all 3.
            return Rewrites(
                original=query, step_back=query, hyde=query, query2doc=query,
            )

        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return Rewrites(
                original=query, step_back=query, hyde=query, query2doc=query,
            )

        raw_text = ""
        content = getattr(candidates[0], "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            text = getattr(part, "text", None)
            if text:
                raw_text = text
                break

        return _parse_rewrites(raw_text, original=query)


# ---------------------------------------------------------------------------
# AnthropicQueryRewriter
# ---------------------------------------------------------------------------


class AnthropicQueryRewriter:
    """Claude with JSON-mode instructions in the system prompt."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: Any | None = None,
        model: str | None = None,
    ) -> None:
        if client is None:
            if not api_key:
                raise ValueError("AnthropicQueryRewriter requires api_key or client")
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=api_key)
        self._client = client
        self._model = (
            model or os.environ.get("KB_QUERY_MODEL") or DEFAULT_ANTHROPIC_MODEL
        )

    async def rewrite(self, query: str) -> Rewrites:
        import anthropic

        model = os.environ.get("KB_QUERY_MODEL") or self._model

        try:
            response = await self._client.messages.create(
                model=model,
                max_tokens=_MAX_OUTPUT_TOKENS,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": f"Query: {query}"}],
            )
        except anthropic.APIError:
            return Rewrites(
                original=query, step_back=query, hyde=query, query2doc=query,
            )
        except Exception:
            return Rewrites(
                original=query, step_back=query, hyde=query, query2doc=query,
            )

        raw_text = ""
        for block in response.content:
            if getattr(block, "type", None) == "text":
                raw_text = getattr(block, "text", "")
                break

        return _parse_rewrites(raw_text, original=query)


# ---------------------------------------------------------------------------
# Factory — KB_QUERY_LLM selector
# ---------------------------------------------------------------------------


def make_query_rewriter() -> QueryRewriter:
    """Pick a rewriter based on `KB_QUERY_LLM`.

    Values: gemini | anthropic | identity | auto (default auto).
    auto probes Gemini key → Anthropic key → Identity.
    """
    selector = (os.environ.get("KB_QUERY_LLM") or "auto").lower()

    if selector == "auto":
        if os.environ.get("KB_GEMINI_API_KEY"):
            selector = "gemini"
        elif os.environ.get("KB_ANTHROPIC_API_KEY"):
            selector = "anthropic"
        else:
            selector = "identity"

    if selector == "gemini":
        api_key = os.environ.get("KB_GEMINI_API_KEY")
        if not api_key:
            raise ValueError(
                "KB_QUERY_LLM=gemini requires KB_GEMINI_API_KEY"
            )
        return GeminiQueryRewriter(api_key=api_key)

    if selector == "anthropic":
        api_key = os.environ.get("KB_ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError(
                "KB_QUERY_LLM=anthropic requires KB_ANTHROPIC_API_KEY"
            )
        return AnthropicQueryRewriter(api_key=api_key)

    if selector == "identity":
        return IdentityQueryRewriter()

    raise ValueError(
        f"Unknown KB_QUERY_LLM value: {selector!r} "
        f"(expected 'gemini', 'anthropic', 'identity', or 'auto')"
    )
