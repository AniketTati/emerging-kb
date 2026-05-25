"""Phase 5a — mention extraction (NER over contextual chunks).

Per build_tracker §5.12.1 (11 locked decisions).

Three `MentionExtractor` impls satisfy the same Protocol:

1. `GeminiMentionExtractor` — calls Gemini 2.5 Flash with structured output
   (response_schema constrains JSON shape). Default impl (matches single-key
   demo story).
2. `AnthropicMentionExtractor` — calls Claude with JSON-mode instructions.
   Alt path; activates when `KB_MENTIONS_EXTRACTOR=anthropic` or when only
   the Anthropic key is set under `auto`.
3. `IdentityMentionExtractor` — returns `[]` mentions. CI / no-key path so
   the pipeline completes without an LLM call (retrieval mention-channel
   degrades to no-op).

Factory `make_mention_extractor()` reads `KB_MENTIONS_EXTRACTOR` (4 values).
"""

from __future__ import annotations

import json
import os
from typing import Any, Protocol

from pydantic import BaseModel, Field


DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_ANTHROPIC_MODEL = "claude-opus-4-7"
# Long entity-rich docs (resumes, invoices, multi-page contracts) emit
# ~200+ mentions; the prior 2000-token cap truncated the JSON response
# mid-array and the strict parser then threw out the whole list. PR4
# raised this + added json_recovery.parse_tolerant_array_in_object so
# the rare ongoing truncation salvages everything that did parse.
_MAX_OUTPUT_TOKENS = 8000

ONTONOTES_18_TYPES: tuple[str, ...] = (
    "PERSON", "NORP", "FAC", "ORG", "GPE", "LOC", "PRODUCT",
    "EVENT", "WORK_OF_ART", "LAW", "LANGUAGE", "DATE", "TIME",
    "PERCENT", "MONEY", "QUANTITY", "ORDINAL", "CARDINAL",
)

_SYSTEM_PROMPT = (
    "You are a named-entity recognition system. Identify mentions in the "
    "chunk using ONLY these OntoNotes-18 types: "
    + ", ".join(ONTONOTES_18_TYPES) + ". "
    "Use the document context for disambiguation. "
    "Return JSON. Skip any mention you cannot type confidently."
)

_USER_TEMPLATE_DOC = (
    "Document context (for disambiguation only):\n"
    "<document>\n{doc_text}\n</document>\n\n"
)

_USER_TEMPLATE_CHUNK = (
    "Extract mentions from this chunk:\n"
    "<chunk>\n{chunk_text}\n</chunk>\n\n"
    "Return JSON exactly matching this shape (no preamble):\n"
    '{{"mentions": [{{"text": "...", "type": "ORG", "start": 0, "end": 7, "confidence": 0.95}}]}}\n'
    "start/end are 0-indexed character offsets in the chunk; emit null if uncertain. "
    "confidence is 0.0-1.0; emit null if uncertain."
)


class MentionExtractionError(Exception):
    """Mention extraction call refused or failed. Worker catches this and
    writes a `<state>→failed` lifecycle event."""


class Mention(BaseModel):
    """One extracted mention. Maps 1:1 to an `extracted_mentions` row."""

    mention_text: str = Field(min_length=1, max_length=1000)
    mention_type: str
    start_offset: int | None = None
    end_offset: int | None = None
    confidence: float | None = None


class MentionExtractionResult(BaseModel):
    """Result of `MentionExtractor.extract()`."""

    mentions: list[Mention]
    model_id: str
    input_token_count: int = 0
    output_token_count: int = 0


class MentionExtractor(Protocol):
    async def extract(
        self, *, doc_text: str, chunk_text: str
    ) -> MentionExtractionResult: ...


# ---------------------------------------------------------------------------
# IdentityMentionExtractor — fallback when no LLM key
# ---------------------------------------------------------------------------


class IdentityMentionExtractor:
    """Returns an empty mention list. Used when no provider key is set.

    `model_id='identity'` so dashboards can alarm on this in production
    (mention extraction silently doing nothing is a major recall hit).
    """

    async def extract(
        self, *, doc_text: str, chunk_text: str
    ) -> MentionExtractionResult:
        return MentionExtractionResult(
            mentions=[],
            model_id="identity",
            input_token_count=0,
            output_token_count=0,
        )


# ---------------------------------------------------------------------------
# Shared helpers — JSON parsing + filtering against ONTONOTES_18
# ---------------------------------------------------------------------------


def _parse_mentions_json(raw_text: str) -> list[Mention]:
    """Parse the LLM's JSON output → list of valid Mention objects.

    Filters out:
      - mentions whose type is NOT in ONTONOTES_18_TYPES (LLM hallucinates
        types like "CITY" or "AMOUNT" sometimes — drop them).
      - mentions whose text is empty or > 1000 chars (CHECK constraint).

    Tolerant of code-fenced output and of TRUNCATED responses: when the
    LLM hits max_output_tokens mid-array, we recover every mention that
    closed cleanly rather than throwing the whole list away (E1 root
    cause, PR4).
    """
    from kb.extraction.json_recovery import parse_tolerant_array_in_object

    raw_list, truncated = parse_tolerant_array_in_object(raw_text, "mentions")
    if truncated:
        # Visible signal so operators know the cap is biting. Dump the
        # tail of the raw output too — useful when recovery=0 because
        # Gemini occasionally returns a verbose preamble before the
        # JSON, blowing the array open without ever closing an element.
        import logging
        log = logging.getLogger(__name__)
        log.warning(
            "mentions response was truncated; recovered %d items "
            "(raw len=%d, last 200 chars: %r)",
            len(raw_list), len(raw_text), raw_text[-200:] if raw_text else "",
        )

    mentions: list[Mention] = []
    valid_types = set(ONTONOTES_18_TYPES)
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        m_text = item.get("text") or item.get("mention_text")
        m_type = item.get("type") or item.get("mention_type")
        if not isinstance(m_text, str) or not m_text.strip():
            continue
        m_text = m_text.strip()[:1000]
        if not isinstance(m_type, str) or m_type not in valid_types:
            continue
        try:
            mentions.append(Mention(
                mention_text=m_text,
                mention_type=m_type,
                start_offset=item.get("start"),
                end_offset=item.get("end"),
                confidence=item.get("confidence"),
            ))
        except Exception:  # noqa: BLE001 — bad row, skip
            continue
    return mentions


# ---------------------------------------------------------------------------
# GeminiMentionExtractor — default impl
# ---------------------------------------------------------------------------


class GeminiMentionExtractor:
    """Gemini 2.5 Flash with structured JSON output."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: Any | None = None,
        model: str | None = None,
    ) -> None:
        if client is None:
            if not api_key:
                raise MentionExtractionError(
                    "GeminiMentionExtractor requires api_key or client"
                )
            from google.genai import Client
            client = Client(api_key=api_key)
        self._client = client
        self._model = (
            model or os.environ.get("KB_MENTIONS_MODEL") or DEFAULT_GEMINI_MODEL
        )

    async def extract(
        self, *, doc_text: str, chunk_text: str
    ) -> MentionExtractionResult:
        model = os.environ.get("KB_MENTIONS_MODEL") or self._model

        from google.genai import types

        # Truncate doc context for cost; mention extraction doesn't need
        # the full document, just enough to disambiguate.
        doc_context = doc_text[:2000] if doc_text else ""

        prompt = (
            (_USER_TEMPLATE_DOC.format(doc_text=doc_context) if doc_context else "")
            + _USER_TEMPLATE_CHUNK.format(chunk_text=chunk_text)
        )
        config = types.GenerateContentConfig(
            system_instruction=_SYSTEM_PROMPT,
            max_output_tokens=_MAX_OUTPUT_TOKENS,
            response_mime_type="application/json",
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )

        try:
            response = await self._client.aio.models.generate_content(
                model=model,
                contents=prompt,
                config=config,
            )
        except Exception as exc:
            block_reason = getattr(
                getattr(exc, "prompt_feedback", None), "block_reason", None
            )
            suffix = f" (block_reason={block_reason})" if block_reason else ""
            raise MentionExtractionError(
                f"Gemini mention call failed: {exc}{suffix}"
            ) from exc

        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            pf = getattr(response, "prompt_feedback", None)
            block_reason = getattr(pf, "block_reason", None) if pf else None
            raise MentionExtractionError(
                "Gemini returned no candidates"
                + (f" (block_reason={block_reason})" if block_reason else "")
            )

        raw_text = ""
        content = getattr(candidates[0], "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            text = getattr(part, "text", None)
            if text:
                raw_text = text
                break

        mentions = _parse_mentions_json(raw_text)
        usage = getattr(response, "usage_metadata", None)
        return MentionExtractionResult(
            mentions=mentions,
            model_id=model,
            input_token_count=getattr(usage, "prompt_token_count", 0) or 0,
            output_token_count=getattr(usage, "candidates_token_count", 0) or 0,
        )


# ---------------------------------------------------------------------------
# AnthropicMentionExtractor — alt impl
# ---------------------------------------------------------------------------


class AnthropicMentionExtractor:
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
                raise MentionExtractionError(
                    "AnthropicMentionExtractor requires api_key or client"
                )
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=api_key)
        self._client = client
        self._model = (
            model or os.environ.get("KB_MENTIONS_MODEL") or DEFAULT_ANTHROPIC_MODEL
        )

    async def extract(
        self, *, doc_text: str, chunk_text: str
    ) -> MentionExtractionResult:
        import anthropic
        model = os.environ.get("KB_MENTIONS_MODEL") or self._model

        doc_context = doc_text[:2000] if doc_text else ""
        user_content = (
            (_USER_TEMPLATE_DOC.format(doc_text=doc_context) if doc_context else "")
            + _USER_TEMPLATE_CHUNK.format(chunk_text=chunk_text)
        )

        try:
            response = await self._client.messages.create(
                model=model,
                max_tokens=_MAX_OUTPUT_TOKENS,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
            )
        except anthropic.APIError as exc:
            raise MentionExtractionError(
                f"Anthropic mention call failed: {exc}"
            ) from exc
        except Exception as exc:
            raise MentionExtractionError(
                f"Anthropic mention call failed: {exc}"
            ) from exc

        raw_text = ""
        for block in response.content:
            if getattr(block, "type", None) == "text":
                raw_text = getattr(block, "text", "")
                break

        mentions = _parse_mentions_json(raw_text)
        usage = response.usage
        return MentionExtractionResult(
            mentions=mentions,
            model_id=model,
            input_token_count=getattr(usage, "input_tokens", 0) or 0,
            output_token_count=getattr(usage, "output_tokens", 0) or 0,
        )


# ---------------------------------------------------------------------------
# Factory — KB_MENTIONS_EXTRACTOR selector
# ---------------------------------------------------------------------------


def make_mention_extractor() -> MentionExtractor:
    """Pick an extractor based on `KB_MENTIONS_EXTRACTOR`.

    Values: gemini | anthropic | identity | auto (default auto).
    auto probes Gemini key → Anthropic key → Identity.
    """
    selector = (os.environ.get("KB_MENTIONS_EXTRACTOR") or "auto").lower()

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
                "KB_MENTIONS_EXTRACTOR=gemini requires KB_GEMINI_API_KEY"
            )
        return GeminiMentionExtractor(api_key=api_key)

    if selector == "anthropic":
        api_key = os.environ.get("KB_ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError(
                "KB_MENTIONS_EXTRACTOR=anthropic requires KB_ANTHROPIC_API_KEY"
            )
        return AnthropicMentionExtractor(api_key=api_key)

    if selector == "identity":
        return IdentityMentionExtractor()

    raise ValueError(
        f"Unknown KB_MENTIONS_EXTRACTOR value: {selector!r} "
        f"(expected 'gemini', 'anthropic', 'identity', or 'auto')"
    )
