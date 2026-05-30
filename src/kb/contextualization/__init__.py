"""Phase 3b + 3b-bis contextual retrieval — adapter-pattern prefix LLM call.

Per build_tracker §5.8 (14 decisions, Anthropic) + §5.8.1 (10 decisions,
Gemini adapter additive).

Three `Contextualizer` impls satisfy the same Protocol:

1. `AnthropicContextualizer` — calls Claude with the doc context as a cached
   system block + the chunk text in the user message. Records cache metrics
   from the response. Default model: `claude-opus-4-7` (per the claude-api
   skill mandate; user overrides via `KB_CONTEXTUAL_MODEL`).

2. `GeminiContextualizer` — calls Gemini Flash with the doc context in the
   GenerateContentConfig.system_instruction + chunk text in the user content.
   No explicit caching at demo scale (per §5.8.1 #4): `prompt_token_count`
   from usage_metadata is stored in `cache_creation_input_tokens` to keep the
   schema additive (cost reporting works uniformly across providers).
   Default model: `gemini-2.5-flash`; same `KB_CONTEXTUAL_MODEL` override env.

3. `IdentityContextualizer` — fallback when no provider key is set.
   Returns `prefix=""` so `contextual_text == chunk_text` byte-for-byte.
   Lets the pipeline complete without an API key (retrieval recall degrades
   to "no contextual retrieval" baseline).

Prompt template is copied verbatim from Anthropic's published Contextual
Retrieval recipe — recipe is model-agnostic so both adapters use it:
  https://github.com/anthropics/anthropic-cookbook/tree/main/skills/contextual-embeddings

The factory `make_contextualizer()` reads `KB_CONTEXTUALIZER` to choose
the adapter:
  - `auto` (default): probe `KB_GEMINI_API_KEY` → `KB_ANTHROPIC_API_KEY` →
    Identity in that order. Gemini-first matches the demo's "one API key,
    Gemini" default story (§5.8.1 #2).
  - `gemini` | `anthropic` | `identity`: explicit override.
"""

from __future__ import annotations

import os
from typing import Any, Protocol

import anthropic
from pydantic import BaseModel


DEFAULT_MODEL = "claude-opus-4-7"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
_SYSTEM_TEMPLATE = (
    "Here is the full document for context (cached for efficiency):\n\n"
    "<document>\n{doc_text}\n</document>"
)
_USER_TEMPLATE = (
    "Here is a chunk from that document:\n\n"
    "<chunk>\n{chunk_text}\n</chunk>\n\n"
    "Provide a short (50-100 token) context line that situates this chunk "
    "within the document. Return ONLY the context line, no preamble."
)
_MAX_OUTPUT_TOKENS = 200


class ContextualizationError(Exception):
    """A contextualization call refused or failed. Worker catches this and
    writes a `chunked→failed` lifecycle event."""


class ContextualizedChunk(BaseModel):
    """Output of `Contextualizer.contextualize()`. The worker maps this to a
    row in the `contextual_chunks` table 1:1."""

    contextual_prefix: str
    contextual_text: str  # prefix + "\n\n" + chunk_text
    model_id: str
    prefix_token_count: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int


class Contextualizer(Protocol):
    async def contextualize(
        self, *, doc_text: str, chunk_text: str
    ) -> ContextualizedChunk: ...


# ---------------------------------------------------------------------------
# IdentityContextualizer — fallback when no API key
# ---------------------------------------------------------------------------


class IdentityContextualizer:
    """Returns a degenerate (empty prefix) contextualized chunk. Used when
    `KB_ANTHROPIC_API_KEY` is unset — lets the pipeline complete without
    requiring an LLM call, at the cost of "no contextual retrieval" recall.

    `model_id='identity'` so dashboards can alarm on this in production."""

    async def contextualize(
        self, *, doc_text: str, chunk_text: str
    ) -> ContextualizedChunk:
        return ContextualizedChunk(
            contextual_prefix="",
            contextual_text=chunk_text,
            model_id="identity",
            prefix_token_count=0,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )


# ---------------------------------------------------------------------------
# AnthropicContextualizer — real LLM call with prompt caching
# ---------------------------------------------------------------------------


class AnthropicContextualizer:
    """Adapter for Anthropic's Messages API + prompt caching.

    Decision #2: doc text in a system block with cache_control: ephemeral.
    Decision #7: prompt template verbatim from Anthropic's Contextual
    Retrieval cookbook.
    Decision #8: max_tokens=200 (2× the 50-100 token target).
    Decision #9: thinking disabled (short description task).
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: Any | None = None,
        model: str | None = None,
    ) -> None:
        if client is None:
            if not api_key:
                raise ContextualizationError(
                    "AnthropicContextualizer requires api_key or client"
                )
            client = anthropic.AsyncAnthropic(api_key=api_key)
        self._client = client
        self._model = model or os.environ.get("KB_CONTEXTUAL_MODEL") or DEFAULT_MODEL

    async def contextualize(
        self, *, doc_text: str, chunk_text: str
    ) -> ContextualizedChunk:
        system_blocks = [
            {
                "type": "text",
                "text": _SYSTEM_TEMPLATE.format(doc_text=doc_text),
                "cache_control": {"type": "ephemeral"},
            }
        ]
        messages = [
            {
                "role": "user",
                "content": _USER_TEMPLATE.format(chunk_text=chunk_text),
            }
        ]

        # Re-read env at call time so tests can swap KB_CONTEXTUAL_MODEL on
        # each call without rebuilding the contextualizer.
        model = os.environ.get("KB_CONTEXTUAL_MODEL") or self._model

        try:
            response = await self._client.messages.create(
                model=model,
                max_tokens=_MAX_OUTPUT_TOKENS,
                thinking={"type": "disabled"},
                system=system_blocks,
                messages=messages,
            )
        except anthropic.APIStatusError as exc:
            raise ContextualizationError(
                f"Anthropic API error {getattr(exc, 'status_code', '?')}: {exc}"
            ) from exc
        except anthropic.APIError as exc:
            raise ContextualizationError(
                f"Anthropic API error: {exc}"
            ) from exc
        except Exception as exc:
            raise ContextualizationError(
                f"Anthropic call failed: {exc}"
            ) from exc

        # Extract text from the first text content block.
        prefix = ""
        for block in response.content:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                prefix = getattr(block, "text", "")
                break
        prefix = prefix.strip()

        contextual_text = (
            f"{prefix}\n\n{chunk_text}" if prefix else chunk_text
        )

        usage = response.usage
        return ContextualizedChunk(
            contextual_prefix=prefix,
            contextual_text=contextual_text,
            model_id=model,
            prefix_token_count=getattr(usage, "output_tokens", 0) or 0,
            cache_creation_input_tokens=
                getattr(usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_input_tokens=
                getattr(usage, "cache_read_input_tokens", 0) or 0,
        )


# ---------------------------------------------------------------------------
# GeminiContextualizer — real LLM call via google-genai (§5.8.1)
# ---------------------------------------------------------------------------


class GeminiContextualizer:
    """Adapter for Google's google-genai SDK + Gemini 2.5 Flash.

    Decision #3: prompt template verbatim from §5.8 #7 (Anthropic cookbook —
        model-agnostic recipe).
    Decision #4: no explicit caching at demo scale. `prompt_token_count`
        from usage_metadata is stored in `cache_creation_input_tokens` to keep
        the schema additive across providers; `cache_read_input_tokens` stays 0.
    Decision #6: max_output_tokens=200 (mirrors Anthropic adapter).
    Decision #7: thinking_config.thinking_budget=0 (short description task —
        no reasoning needed).
    Decision #8: any exception → ContextualizationError; if the response carries
        prompt_feedback (safety block), include block_reason in the message.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: Any | None = None,
        model: str | None = None,
    ) -> None:
        if client is None:
            if not api_key:
                raise ContextualizationError(
                    "GeminiContextualizer requires api_key or client"
                )
            from google.genai import Client
            client = Client(api_key=api_key)
        self._client = client
        self._model = (
            model or os.environ.get("KB_CONTEXTUAL_MODEL") or DEFAULT_GEMINI_MODEL
        )

    async def contextualize(
        self, *, doc_text: str, chunk_text: str
    ) -> ContextualizedChunk:
        # Re-read env at call time so tests can swap KB_CONTEXTUAL_MODEL on
        # each call without rebuilding the contextualizer (mirrors Anthropic).
        model = os.environ.get("KB_CONTEXTUAL_MODEL") or self._model

        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=_SYSTEM_TEMPLATE.format(doc_text=doc_text),
            max_output_tokens=_MAX_OUTPUT_TOKENS,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )

        try:
            response = await self._client.aio.models.generate_content(
                model=model,
                contents=_USER_TEMPLATE.format(chunk_text=chunk_text),
                config=config,
            )
        except Exception as exc:
            # Decision #8: capture prompt_feedback.block_reason if surfaced
            # via the exception (some SDK error paths attach it).
            block_reason = getattr(
                getattr(exc, "prompt_feedback", None), "block_reason", None
            )
            suffix = f" (block_reason={block_reason})" if block_reason else ""
            raise ContextualizationError(
                f"Gemini contextualize call failed: {exc}{suffix}"
            ) from exc

        # Defensive: response could carry a top-level prompt_feedback safety
        # block with empty candidates. Surface as ContextualizationError so
        # the worker writes chunked→failed cleanly.
        prompt_feedback = getattr(response, "prompt_feedback", None)
        block_reason = getattr(prompt_feedback, "block_reason", None) if prompt_feedback else None
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            raise ContextualizationError(
                f"Gemini returned no candidates"
                + (f" (block_reason={block_reason})" if block_reason else "")
            )

        # Extract text from the first candidate's first text part.
        prefix = ""
        content = getattr(candidates[0], "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            text = getattr(part, "text", None)
            if text:
                prefix = text
                break
        prefix = prefix.strip()

        contextual_text = (
            f"{prefix}\n\n{chunk_text}" if prefix else chunk_text
        )

        usage = getattr(response, "usage_metadata", None)
        prompt_tokens = getattr(usage, "prompt_token_count", 0) or 0
        candidates_tokens = getattr(usage, "candidates_token_count", 0) or 0

        return ContextualizedChunk(
            contextual_prefix=prefix,
            contextual_text=contextual_text,
            model_id=model,
            prefix_token_count=candidates_tokens,
            # Decision #4: prompt_token_count → cache_creation_input_tokens
            # (billed-input tokens; no explicit cache used at demo scale).
            cache_creation_input_tokens=prompt_tokens,
            cache_read_input_tokens=0,
        )


# ---------------------------------------------------------------------------
# Factory — picks adapter via KB_CONTEXTUALIZER selector (§5.8.1 #2)
# ---------------------------------------------------------------------------


def make_contextualizer() -> Contextualizer:
    """Return the appropriate Contextualizer based on `KB_CONTEXTUALIZER`.

    §5.8.1 decision #2: selector ∈ {gemini, anthropic, identity, auto},
    default `auto`. `auto` probes Gemini key → Anthropic key → Identity
    (Gemini-first matches the demo's "one API key, Gemini" default story).
    Explicit values override the probe; explicit gemini/anthropic without
    the required key raises ValueError so misconfigs fail loudly at startup.
    """
    selector = (os.environ.get("KB_CONTEXTUALIZER") or "auto").lower()

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
                "KB_CONTEXTUALIZER=gemini requires KB_GEMINI_API_KEY"
            )
        return GeminiContextualizer(api_key=api_key)

    if selector == "anthropic":
        api_key = os.environ.get("KB_ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError(
                "KB_CONTEXTUALIZER=anthropic requires KB_ANTHROPIC_API_KEY"
            )
        return AnthropicContextualizer(api_key=api_key)

    if selector == "identity":
        return IdentityContextualizer()

    raise ValueError(
        f"Unknown KB_CONTEXTUALIZER value: {selector!r} "
        f"(expected 'gemini', 'anthropic', 'identity', or 'auto')"
    )
