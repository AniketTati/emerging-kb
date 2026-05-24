"""Phase 3d — Summarizer adapter for RAPTOR cluster summaries.

Per build_tracker §5.10 (16 decisions, see decision #5/#6/#7). Mirrors the
adapter pattern from 3b-bis's contextualization module — three implementations
of the same `Summarizer` Protocol:

1. `GeminiSummarizer` — Gemini 2.5 Flash via google-genai SDK. Default model;
   used when `KB_GEMINI_API_KEY` is set. Configurable via `KB_SUMMARIZER_MODEL`.

2. `AnthropicSummarizer` — Claude Haiku (or whatever `KB_SUMMARIZER_MODEL`
   says). Used when only `KB_ANTHROPIC_API_KEY` is set. Mirrors Phase 3b's
   AnthropicContextualizer adapter shape.

3. `IdentitySummarizer` — concatenates input texts with `\\n\\n---\\n\\n`
   separator + truncates to ~600 tokens' worth (~2400 chars at 4 chars/tok).
   No semantic abstraction — exists ONLY as the no-key smoke path so the
   pipeline can complete without an API key. NOT real CI coverage of RAPTOR
   (tree-shape tests use mocked Gemini with deterministic stubbed text).

The factory `make_summarizer()` reads `KB_SUMMARIZER ∈ {gemini, anthropic,
identity, auto}`. Default `auto` probes: KB_GEMINI_API_KEY →
KB_ANTHROPIC_API_KEY → Identity (same Gemini-first probe order as 3b-bis's
make_contextualizer for consistency with the "single Gemini key" demo story).
"""

from __future__ import annotations

import os
from typing import Any, Protocol

from pydantic import BaseModel


DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_ANTHROPIC_MODEL = "claude-opus-4-7"
DEFAULT_MAX_OUTPUT_TOKENS = 600
_IDENTITY_TRUNCATION_CHARS = 2400  # ~600 tokens at 4 chars/token rough

_PROMPT_TEMPLATE = (
    "You are summarizing {n_chunks} chunks from a single document. "
    "Produce a concise summary (200-400 tokens) that preserves key facts "
    "and themes. Use markdown. Return only the summary."
)
_CHUNK_SEPARATOR = "\n\n---\n\n"


class SummarizationError(Exception):
    """A summarization call refused or failed. Worker catches this and
    writes a `raptor_building→failed` lifecycle event."""


class Summary(BaseModel):
    """Output of `Summarizer.summarize()`. The RAPTOR builder maps this to a
    new `raptor_nodes` row at level L+1."""

    text: str
    model_id: str
    input_token_count: int
    output_token_count: int


class Summarizer(Protocol):
    async def summarize(
        self, *, texts: list[str], doc_context: str | None = None
    ) -> Summary: ...


# ---------------------------------------------------------------------------
# IdentitySummarizer — concat-with-truncation fallback when no API key
# ---------------------------------------------------------------------------


class IdentitySummarizer:
    """Degenerate "summary" = concatenated input texts truncated to ~600
    tokens-equivalent. `model_id='identity'` so dashboards can alarm on it.

    Per §5.10 decision #5 (sharpened post-deliberation): this is the no-key
    SMOKE PATH only. It produces structurally-correct raptor_nodes rows but
    NO semantic abstraction — higher-level summaries are just truncated
    duplicates of leaf content. Useful for "pipeline doesn't crash without
    a key" mechanical coverage. NOT useful for tree-quality testing.
    """

    async def summarize(
        self, *, texts: list[str], doc_context: str | None = None
    ) -> Summary:
        joined = _CHUNK_SEPARATOR.join(t.strip() for t in texts if t and t.strip())
        if len(joined) > _IDENTITY_TRUNCATION_CHARS:
            joined = joined[:_IDENTITY_TRUNCATION_CHARS].rstrip() + "..."
        return Summary(
            text=joined,
            model_id="identity",
            input_token_count=0,
            output_token_count=0,
        )


# ---------------------------------------------------------------------------
# GeminiSummarizer — real LLM call via google-genai
# ---------------------------------------------------------------------------


class GeminiSummarizer:
    """Adapter for Gemini 2.5 Flash text-only summarization."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: Any | None = None,
        model: str | None = None,
    ) -> None:
        if client is None:
            if not api_key:
                raise SummarizationError(
                    "GeminiSummarizer requires api_key or client"
                )
            from google.genai import Client
            client = Client(api_key=api_key)
        self._client = client
        self._model = (
            model or os.environ.get("KB_SUMMARIZER_MODEL") or DEFAULT_GEMINI_MODEL
        )

    async def summarize(
        self, *, texts: list[str], doc_context: str | None = None
    ) -> Summary:
        # Re-read env at call time so tests can swap KB_SUMMARIZER_MODEL on
        # each call without rebuilding the summarizer.
        model = os.environ.get("KB_SUMMARIZER_MODEL") or self._model

        from google.genai import types as genai_types

        prompt = _PROMPT_TEMPLATE.format(n_chunks=len(texts))
        joined_chunks = _CHUNK_SEPARATOR.join(
            f"<chunk>\n{t}\n</chunk>" for t in texts
        )
        user_content = f"{prompt}\n\n{joined_chunks}"

        config = genai_types.GenerateContentConfig(
            max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
            thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
        )

        try:
            response = await self._client.aio.models.generate_content(
                model=model,
                contents=user_content,
                config=config,
            )
        except Exception as exc:
            raise SummarizationError(
                f"Gemini summarize call failed: {exc}"
            ) from exc

        # Extract text (tolerate shape variations).
        text = ""
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            content = getattr(candidates[0], "content", None)
            parts = getattr(content, "parts", None) or []
            for part in parts:
                part_text = getattr(part, "text", None)
                if part_text:
                    text = part_text
                    break
        if not text:
            text = getattr(response, "text", "") or ""
        text = text.strip()

        usage = getattr(response, "usage_metadata", None)
        return Summary(
            text=text,
            model_id=model,
            input_token_count=getattr(usage, "prompt_token_count", 0) or 0,
            output_token_count=getattr(usage, "candidates_token_count", 0) or 0,
        )


# ---------------------------------------------------------------------------
# AnthropicSummarizer — Claude alternative
# ---------------------------------------------------------------------------


class AnthropicSummarizer:
    """Adapter for Anthropic Claude. Same prompt as Gemini (model-agnostic
    recipe). Uses anthropic.AsyncAnthropic.messages.create."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: Any | None = None,
        model: str | None = None,
    ) -> None:
        if client is None:
            if not api_key:
                raise SummarizationError(
                    "AnthropicSummarizer requires api_key or client"
                )
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=api_key)
        self._client = client
        self._model = (
            model or os.environ.get("KB_SUMMARIZER_MODEL") or DEFAULT_ANTHROPIC_MODEL
        )

    async def summarize(
        self, *, texts: list[str], doc_context: str | None = None
    ) -> Summary:
        model = os.environ.get("KB_SUMMARIZER_MODEL") or self._model

        prompt = _PROMPT_TEMPLATE.format(n_chunks=len(texts))
        joined_chunks = _CHUNK_SEPARATOR.join(
            f"<chunk>\n{t}\n</chunk>" for t in texts
        )
        user_content = f"{prompt}\n\n{joined_chunks}"

        try:
            response = await self._client.messages.create(
                model=model,
                max_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
                thinking={"type": "disabled"},
                messages=[{"role": "user", "content": user_content}],
            )
        except Exception as exc:
            raise SummarizationError(
                f"Anthropic summarize call failed: {exc}"
            ) from exc

        text = ""
        for block in getattr(response, "content", []) or []:
            if getattr(block, "type", None) == "text":
                text = getattr(block, "text", "")
                break
        text = text.strip()

        usage = getattr(response, "usage", None)
        return Summary(
            text=text,
            model_id=model,
            input_token_count=getattr(usage, "input_tokens", 0) or 0,
            output_token_count=getattr(usage, "output_tokens", 0) or 0,
        )


# ---------------------------------------------------------------------------
# Factory — picks adapter via KB_SUMMARIZER selector (§5.10 #5)
# ---------------------------------------------------------------------------


def make_summarizer() -> Summarizer:
    """Return the appropriate Summarizer based on `KB_SUMMARIZER`.

    §5.10 decision #5: selector ∈ {gemini, anthropic, identity, auto},
    default `auto`. `auto` probes Gemini key → Anthropic key → Identity
    (Gemini-first matches 3b-bis's make_contextualizer probe order and the
    "single Gemini key" demo story).

    Explicit `gemini`/`anthropic` without the matching key raises ValueError
    so misconfigs fail loudly at worker startup.
    """
    selector = (os.environ.get("KB_SUMMARIZER") or "auto").lower()

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
                "KB_SUMMARIZER=gemini requires KB_GEMINI_API_KEY"
            )
        return GeminiSummarizer(api_key=api_key)

    if selector == "anthropic":
        api_key = os.environ.get("KB_ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError(
                "KB_SUMMARIZER=anthropic requires KB_ANTHROPIC_API_KEY"
            )
        return AnthropicSummarizer(api_key=api_key)

    if selector == "identity":
        return IdentitySummarizer()

    raise ValueError(
        f"Unknown KB_SUMMARIZER value: {selector!r} "
        f"(expected 'gemini', 'anthropic', 'identity', or 'auto')"
    )
