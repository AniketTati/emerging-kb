"""Phase 3b contextual retrieval — Anthropic prefix LLM call with prompt caching.

Per build_tracker §5.8 (14 decisions) + Anthropic's Contextual Retrieval
cookbook recipe.

Two `Contextualizer` impls satisfy the same Protocol:

1. `AnthropicContextualizer` — calls Claude with the doc context as a cached
   system block + the chunk text in the user message. Records cache metrics
   from the response. Default model: `claude-opus-4-7` (per the claude-api
   skill mandate; user overrides via `KB_CONTEXTUAL_MODEL`).

2. `IdentityContextualizer` — fallback when `KB_ANTHROPIC_API_KEY` is unset.
   Returns `prefix=""` so `contextual_text == chunk_text` byte-for-byte.
   Lets the pipeline complete without an API key (retrieval recall degrades
   to "no contextual retrieval" baseline).

Prompt template + cache placement copied verbatim from Anthropic's published
Contextual Retrieval recipe:
  https://github.com/anthropics/anthropic-cookbook/tree/main/skills/contextual-embeddings
"""

from __future__ import annotations

import os
from typing import Any, Protocol

import anthropic
from pydantic import BaseModel


DEFAULT_MODEL = "claude-opus-4-7"
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
# Factory — picks Anthropic vs Identity based on env
# ---------------------------------------------------------------------------


def make_contextualizer() -> Contextualizer:
    """Return the appropriate Contextualizer based on env.

    Decision #6: KB_ANTHROPIC_API_KEY unset → IdentityContextualizer
    (degraded mode); set → AnthropicContextualizer.
    """
    api_key = os.environ.get("KB_ANTHROPIC_API_KEY")
    if not api_key:
        return IdentityContextualizer()
    return AnthropicContextualizer(api_key=api_key)
