"""B1 / WA-4 — open-triple extraction (architecture §5 stage 13).

Light OpenIE: per contextual chunk, emit `(subject, predicate, object)`
triples that downstream stage 16 resolves to entity-id relationships.

Three impls satisfy the `TripleExtractor` Protocol:

  1. `GeminiTripleExtractor` — Gemini 2.5 Flash with structured JSON output.
     Default impl when `KB_TRIPLES_EXTRACTOR=gemini` or auto + Gemini key.
  2. `AnthropicTripleExtractor` — Claude with JSON-mode. Alt path.
  3. `IdentityTripleExtractor` — returns []. CI / no-key path. Pipeline
     still completes; downstream relationships/graph builders just see no
     new triples for the file.

Factory `make_triple_extractor()` reads `KB_TRIPLES_EXTRACTOR` env
(values: gemini / anthropic / identity / auto). Same 4-value pattern as
all the other extraction factories (mentions, fields, etc.).
"""

from __future__ import annotations

import json
import os
from typing import Any, Protocol

from pydantic import BaseModel, Field


DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_ANTHROPIC_MODEL = "claude-opus-4-7"
_MAX_OUTPUT_TOKENS = 2000   # ~30-50 triples per chunk worst case

_SYSTEM_PROMPT = (
    "You are an open information extraction system. Given a chunk of text, "
    "lift FACTS as (subject, predicate, object) triples. "
    "Rules:\n"
    "- subject + object are noun phrases referring to specific entities "
    "(people, organizations, places, dates, monetary amounts).\n"
    "- predicate is a SHORT verb phrase (1-4 words) describing the relation.\n"
    "- Skip vague claims, opinions, hedged statements.\n"
    "- Skip triples where subject == object.\n"
    "- Aim for 5-20 high-quality triples per chunk. Empty list is fine.\n"
    "Return JSON only."
)

_USER_TEMPLATE = (
    "Extract triples from this chunk:\n"
    "<chunk>\n{chunk_text}\n</chunk>\n\n"
    "Return JSON exactly matching this shape (no preamble):\n"
    '{{"triples": [{{"subject": "...", "predicate": "...", "object": "...", "confidence": 0.85}}]}}\n'
    "confidence is 0.0-1.0; emit null if uncertain."
)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class TripleExtractionError(Exception):
    """Extractor refused / failed. Worker catches and emits failed event."""


class TripleCandidate(BaseModel):
    """One extracted triple — what the LLM returns. The repo writes it."""
    subject: str = Field(min_length=1, max_length=500)
    predicate: str = Field(min_length=1, max_length=200)
    object: str = Field(min_length=1, max_length=500)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class TripleExtractionResult(BaseModel):
    triples: list[TripleCandidate] = Field(default_factory=list)


class TripleExtractor(Protocol):
    async def extract(self, *, chunk_text: str) -> TripleExtractionResult: ...

    @property
    def model_id(self) -> str: ...


# ---------------------------------------------------------------------------
# IdentityTripleExtractor — empty result
# ---------------------------------------------------------------------------


class IdentityTripleExtractor:
    """No-LLM fallback. Always returns no triples. The downstream
    relationship + graph builders see an empty triple list for the file
    and produce no relationships from this file."""

    model_id: str = "identity"

    async def extract(self, *, chunk_text: str) -> TripleExtractionResult:
        return TripleExtractionResult(triples=[])


# ---------------------------------------------------------------------------
# Helpers shared by LLM extractors
# ---------------------------------------------------------------------------


def _strip_code_fence(text: str) -> str:
    """LLMs sometimes wrap JSON in ```json fences. Strip them."""
    if not text:
        return text
    s = text.strip()
    if s.startswith("```json"):
        s = s[len("```json"):].lstrip("\n")
    elif s.startswith("```"):
        s = s[len("```"):].lstrip("\n")
    if s.endswith("```"):
        s = s[: -len("```")]
    return s.strip()


def _parse_triples(raw: str) -> list[TripleCandidate]:
    """Best-effort JSON parse. Drops bad entries silently — extraction is
    advisory; loud-fail would let one bad triple kill the file."""
    if not raw:
        return []
    text = _strip_code_fence(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    candidates_raw = data.get("triples") or []
    if not isinstance(candidates_raw, list):
        return []
    out: list[TripleCandidate] = []
    for entry in candidates_raw:
        if not isinstance(entry, dict):
            continue
        try:
            t = TripleCandidate(
                subject=str(entry.get("subject") or "").strip(),
                predicate=str(entry.get("predicate") or "").strip(),
                object=str(entry.get("object") or "").strip(),
                confidence=float(entry.get("confidence") or 0.5),
            )
        except (TypeError, ValueError):
            continue
        # Skip empties + self-loops + redundant whitespace.
        if not t.subject or not t.predicate or not t.object:
            continue
        if t.subject.lower() == t.object.lower():
            continue
        out.append(t)
    return out


# ---------------------------------------------------------------------------
# GeminiTripleExtractor
# ---------------------------------------------------------------------------


class GeminiTripleExtractor:
    """Gemini 2.5 Flash with structured JSON output via response_mime_type."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: Any | None = None,
        model: str = DEFAULT_GEMINI_MODEL,
    ) -> None:
        if client is None:
            if not api_key:
                raise ValueError("GeminiTripleExtractor requires api_key or client")
            from google.genai import Client
            client = Client(api_key=api_key)
        self._client = client
        self._model = model

    @property
    def model_id(self) -> str:
        return self._model

    async def extract(self, *, chunk_text: str) -> TripleExtractionResult:
        if not chunk_text.strip():
            return TripleExtractionResult(triples=[])
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
                contents=_USER_TEMPLATE.format(chunk_text=chunk_text[:8000]),
                config=config,
            )
        except Exception as exc:  # noqa: BLE001 — extraction is advisory
            raise TripleExtractionError(f"Gemini call failed: {exc}") from exc

        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return TripleExtractionResult(triples=[])
        raw_text = ""
        content = getattr(candidates[0], "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            t = getattr(part, "text", None)
            if t:
                raw_text = t
                break
        return TripleExtractionResult(triples=_parse_triples(raw_text))


# ---------------------------------------------------------------------------
# AnthropicTripleExtractor
# ---------------------------------------------------------------------------


class AnthropicTripleExtractor:
    """Claude with JSON-mode instructions."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: Any | None = None,
        model: str = DEFAULT_ANTHROPIC_MODEL,
    ) -> None:
        if client is None:
            if not api_key:
                raise ValueError("AnthropicTripleExtractor requires api_key or client")
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=api_key)
        self._client = client
        self._model = model

    @property
    def model_id(self) -> str:
        return self._model

    async def extract(self, *, chunk_text: str) -> TripleExtractionResult:
        if not chunk_text.strip():
            return TripleExtractionResult(triples=[])
        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=_MAX_OUTPUT_TOKENS,
                system=_SYSTEM_PROMPT,
                messages=[{
                    "role": "user",
                    "content": _USER_TEMPLATE.format(chunk_text=chunk_text[:8000]),
                }],
            )
        except Exception as exc:  # noqa: BLE001
            raise TripleExtractionError(f"Anthropic call failed: {exc}") from exc

        raw_text = ""
        for block in getattr(response, "content", None) or []:
            text = getattr(block, "text", None)
            if text:
                raw_text = text
                break
        return TripleExtractionResult(triples=_parse_triples(raw_text))


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_triple_extractor() -> TripleExtractor:
    """Pick an extractor by env. Default `auto` probes Gemini key first
    (matches the demo single-key story), then Anthropic, then Identity."""
    selector = (os.environ.get("KB_TRIPLES_EXTRACTOR") or "auto").lower()
    if selector == "auto":
        if os.environ.get("KB_GEMINI_API_KEY"):
            selector = "gemini"
        elif os.environ.get("KB_ANTHROPIC_API_KEY"):
            selector = "anthropic"
        else:
            selector = "identity"

    if selector == "gemini":
        key = os.environ.get("KB_GEMINI_API_KEY")
        if not key:
            raise ValueError("KB_TRIPLES_EXTRACTOR=gemini requires KB_GEMINI_API_KEY")
        return GeminiTripleExtractor(api_key=key)
    if selector == "anthropic":
        key = os.environ.get("KB_ANTHROPIC_API_KEY")
        if not key:
            raise ValueError("KB_TRIPLES_EXTRACTOR=anthropic requires KB_ANTHROPIC_API_KEY")
        return AnthropicTripleExtractor(api_key=key)
    if selector == "identity":
        return IdentityTripleExtractor()
    raise ValueError(
        f"Unknown KB_TRIPLES_EXTRACTOR value: {selector!r} "
        f"(expected gemini, anthropic, identity, or auto)"
    )
