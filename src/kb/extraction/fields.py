"""Phase 5b — doc-type classifier + emergent field proposer.

Per build_tracker §5.12.2 (11 locked decisions).

Two LLM call shapes:

1. `classify_doc_type(doc_text) -> str` — one short LLM call returning a
   1-3 word doc-type label (e.g. "legal_contract", "bank_statement",
   "10k_filing"). Stored on `files.inferred_doc_type`.

2. `propose_fields(doc_text) -> list[ProposedField]` — bottom-up "what
   structured fields are in this doc?" LLM call. Each ProposedField has
   name + description + value + value_type + is_pii (per architecture
   step 12b).

Both use the same factory pattern: `KB_FIELD_EXTRACTOR ∈
{gemini, anthropic, identity, auto}`. Identity returns sensible defaults
(doc_type='unknown', fields=[]).
"""

from __future__ import annotations

import json
import os
from typing import Any, Protocol

from pydantic import BaseModel, Field


DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_ANTHROPIC_MODEL = "claude-opus-4-7"
_MAX_OUTPUT_TOKENS_CLASSIFY = 50
# Long financial/legal docs propose 50+ fields with descriptions; 4000
# truncates in the wild. PR4 raised to 12000 and added json_recovery to
# salvage on the rare ongoing truncation.
_MAX_OUTPUT_TOKENS_PROPOSE = 12000

VALUE_TYPES: tuple[str, ...] = ("text", "number", "date", "datetime", "boolean", "enum")

_CLASSIFY_SYSTEM_PROMPT = (
    "You classify documents into short snake_case doc-type labels. "
    "Examples: legal_contract, bank_statement, 10k_filing, invoice, "
    "employment_letter, land_record, drawing, handwritten_note, email_thread, "
    "vendor_spreadsheet. Output JSON only."
)
_CLASSIFY_USER_TEMPLATE = (
    "Classify this document:\n\n<doc>\n{doc_text}\n</doc>\n\n"
    'Return JSON: {{"doc_type": "snake_case_label"}}'
)

_PROPOSE_SYSTEM_PROMPT = (
    "You identify the STRUCTURED fields in a document — the kind of fields "
    "a person would extract into a database row. For each field, give the "
    "field name (snake_case), a one-line description, the value as a string, "
    "the inferred value_type (text|number|date|datetime|boolean|enum), and "
    "an is_pii flag (true if the value matches a PII pattern: SSN, Aadhaar, "
    "PAN, credit card, DOB, phone, email, medical record number, etc.). "
    "Output JSON only. Skip prose/headings."
)
_PROPOSE_USER_TEMPLATE = (
    "Identify structured fields in this document:\n\n<doc>\n{doc_text}\n</doc>\n\n"
    'Return JSON exactly: {{"fields": [{{'
    '"name": "vendor_name", '
    '"description": "Name of the vendor party", '
    '"value": "ACME Inc.", '
    '"value_type": "text", '
    '"is_pii": false'
    '}}]}}'
)


class FieldExtractionError(Exception):
    """Doc-type or field-extraction call refused or failed."""


class ProposedField(BaseModel):
    """One field emitted by the proposer. Maps 1:1 to `proposed_fields` row."""

    field_name: str = Field(min_length=1, max_length=200)
    field_description: str = ""
    value_text: str | None = None
    value_type: str = "text"
    is_pii: bool = False


class DocTypeResult(BaseModel):
    doc_type: str
    model_id: str
    input_token_count: int = 0
    output_token_count: int = 0


class FieldProposalResult(BaseModel):
    fields: list[ProposedField]
    model_id: str
    input_token_count: int = 0
    output_token_count: int = 0


class FieldExtractor(Protocol):
    async def classify(self, *, doc_text: str) -> DocTypeResult: ...
    async def propose(self, *, doc_text: str) -> FieldProposalResult: ...


# ---------------------------------------------------------------------------
# Identity fallback
# ---------------------------------------------------------------------------


class IdentityFieldExtractor:
    """Returns 'unknown' doc_type + empty fields list. No LLM call."""

    async def classify(self, *, doc_text: str) -> DocTypeResult:
        return DocTypeResult(doc_type="unknown", model_id="identity")

    async def propose(self, *, doc_text: str) -> FieldProposalResult:
        return FieldProposalResult(fields=[], model_id="identity")


# ---------------------------------------------------------------------------
# Shared JSON parsers
# ---------------------------------------------------------------------------


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 2 and lines[-1].strip() == "```":
            lines = lines[1:-1]
        else:
            lines = lines[1:]
        text = "\n".join(lines)
    return text


def _parse_doc_type(raw: str) -> str:
    """Parse classifier JSON → doc_type string. Tolerant of fences + missing keys."""
    text = _strip_code_fence(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise FieldExtractionError(
            f"classifier returned invalid JSON: {exc}; output: {raw[:200]}"
        ) from exc
    if not isinstance(data, dict):
        return "unknown"
    dt = data.get("doc_type") or data.get("type") or "unknown"
    if not isinstance(dt, str):
        return "unknown"
    # Normalize: lowercase, replace spaces/dashes with underscore.
    dt = dt.strip().lower().replace(" ", "_").replace("-", "_")
    return dt[:50] or "unknown"


def _parse_proposed_fields(raw: str) -> list[ProposedField]:
    """Parse proposer JSON → list[ProposedField]. Tolerant of truncation
    via json_recovery — when Gemini hits max_output_tokens we still
    salvage the fields that closed cleanly rather than dropping all."""
    from kb.extraction.json_recovery import parse_tolerant_array_in_object

    raw_list, truncated = parse_tolerant_array_in_object(raw, "fields")
    if truncated:
        import logging
        logging.getLogger(__name__).warning(
            "proposer response was truncated; recovered %d fields from "
            "partial JSON (consider raising _MAX_OUTPUT_TOKENS_PROPOSE)",
            len(raw_list),
        )

    fields: list[ProposedField] = []
    valid_types = set(VALUE_TYPES)
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("field_name")
        if not isinstance(name, str) or not name.strip():
            continue
        name = name.strip().lower().replace(" ", "_").replace("-", "_")[:200]
        vt = item.get("value_type") or "text"
        if vt not in valid_types:
            vt = "text"
        value = item.get("value")
        if value is not None and not isinstance(value, str):
            value = str(value)
        try:
            fields.append(ProposedField(
                field_name=name,
                field_description=str(item.get("description") or "")[:1000],
                value_text=value,
                value_type=vt,
                is_pii=bool(item.get("is_pii", False)),
            ))
        except Exception:  # noqa: BLE001
            continue
    return fields


# ---------------------------------------------------------------------------
# GeminiFieldExtractor
# ---------------------------------------------------------------------------


class GeminiFieldExtractor:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: Any | None = None,
        model: str | None = None,
    ) -> None:
        if client is None:
            if not api_key:
                raise FieldExtractionError(
                    "GeminiFieldExtractor requires api_key or client"
                )
            from google.genai import Client
            client = Client(api_key=api_key)
        self._client = client
        self._model = (
            model or os.environ.get("KB_FIELD_MODEL") or DEFAULT_GEMINI_MODEL
        )

    async def _call_gemini(self, *, system: str, user: str, max_tokens: int) -> tuple[str, int, int]:
        from google.genai import types
        model = os.environ.get("KB_FIELD_MODEL") or self._model
        config = types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
            response_mime_type="application/json",
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )
        try:
            response = await self._client.aio.models.generate_content(
                model=model, contents=user, config=config,
            )
        except Exception as exc:
            raise FieldExtractionError(f"Gemini call failed: {exc}") from exc

        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            raise FieldExtractionError("Gemini returned no candidates")
        raw_text = ""
        content = getattr(candidates[0], "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            t = getattr(part, "text", None)
            if t:
                raw_text = t
                break
        usage = getattr(response, "usage_metadata", None)
        in_tok = getattr(usage, "prompt_token_count", 0) or 0
        out_tok = getattr(usage, "candidates_token_count", 0) or 0
        return raw_text, in_tok, out_tok

    async def classify(self, *, doc_text: str) -> DocTypeResult:
        doc_snippet = doc_text[:4000] if doc_text else ""
        raw, in_tok, out_tok = await self._call_gemini(
            system=_CLASSIFY_SYSTEM_PROMPT,
            user=_CLASSIFY_USER_TEMPLATE.format(doc_text=doc_snippet),
            max_tokens=_MAX_OUTPUT_TOKENS_CLASSIFY,
        )
        return DocTypeResult(
            doc_type=_parse_doc_type(raw),
            model_id=os.environ.get("KB_FIELD_MODEL") or self._model,
            input_token_count=in_tok,
            output_token_count=out_tok,
        )

    async def propose(self, *, doc_text: str) -> FieldProposalResult:
        # Larger snippet for field proposal — needs more context.
        doc_snippet = doc_text[:8000] if doc_text else ""
        raw, in_tok, out_tok = await self._call_gemini(
            system=_PROPOSE_SYSTEM_PROMPT,
            user=_PROPOSE_USER_TEMPLATE.format(doc_text=doc_snippet),
            max_tokens=_MAX_OUTPUT_TOKENS_PROPOSE,
        )
        return FieldProposalResult(
            fields=_parse_proposed_fields(raw),
            model_id=os.environ.get("KB_FIELD_MODEL") or self._model,
            input_token_count=in_tok,
            output_token_count=out_tok,
        )


# ---------------------------------------------------------------------------
# AnthropicFieldExtractor
# ---------------------------------------------------------------------------


class AnthropicFieldExtractor:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: Any | None = None,
        model: str | None = None,
    ) -> None:
        if client is None:
            if not api_key:
                raise FieldExtractionError(
                    "AnthropicFieldExtractor requires api_key or client"
                )
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=api_key)
        self._client = client
        self._model = (
            model or os.environ.get("KB_FIELD_MODEL") or DEFAULT_ANTHROPIC_MODEL
        )

    async def _call_claude(self, *, system: str, user: str, max_tokens: int) -> tuple[str, int, int]:
        import anthropic
        model = os.environ.get("KB_FIELD_MODEL") or self._model
        try:
            response = await self._client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except anthropic.APIError as exc:
            raise FieldExtractionError(f"Anthropic call failed: {exc}") from exc

        raw_text = ""
        for block in response.content:
            if getattr(block, "type", None) == "text":
                raw_text = getattr(block, "text", "")
                break
        usage = response.usage
        return raw_text, getattr(usage, "input_tokens", 0) or 0, getattr(usage, "output_tokens", 0) or 0

    async def classify(self, *, doc_text: str) -> DocTypeResult:
        doc_snippet = doc_text[:4000] if doc_text else ""
        raw, in_tok, out_tok = await self._call_claude(
            system=_CLASSIFY_SYSTEM_PROMPT,
            user=_CLASSIFY_USER_TEMPLATE.format(doc_text=doc_snippet),
            max_tokens=_MAX_OUTPUT_TOKENS_CLASSIFY,
        )
        return DocTypeResult(
            doc_type=_parse_doc_type(raw),
            model_id=os.environ.get("KB_FIELD_MODEL") or self._model,
            input_token_count=in_tok,
            output_token_count=out_tok,
        )

    async def propose(self, *, doc_text: str) -> FieldProposalResult:
        doc_snippet = doc_text[:8000] if doc_text else ""
        raw, in_tok, out_tok = await self._call_claude(
            system=_PROPOSE_SYSTEM_PROMPT,
            user=_PROPOSE_USER_TEMPLATE.format(doc_text=doc_snippet),
            max_tokens=_MAX_OUTPUT_TOKENS_PROPOSE,
        )
        return FieldProposalResult(
            fields=_parse_proposed_fields(raw),
            model_id=os.environ.get("KB_FIELD_MODEL") or self._model,
            input_token_count=in_tok,
            output_token_count=out_tok,
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_field_extractor() -> FieldExtractor:
    """Pick an extractor based on `KB_FIELD_EXTRACTOR`."""
    selector = (os.environ.get("KB_FIELD_EXTRACTOR") or "auto").lower()

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
                "KB_FIELD_EXTRACTOR=gemini requires KB_GEMINI_API_KEY"
            )
        return GeminiFieldExtractor(api_key=api_key)

    if selector == "anthropic":
        api_key = os.environ.get("KB_ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError(
                "KB_FIELD_EXTRACTOR=anthropic requires KB_ANTHROPIC_API_KEY"
            )
        return AnthropicFieldExtractor(api_key=api_key)

    if selector == "identity":
        return IdentityFieldExtractor()

    raise ValueError(
        f"Unknown KB_FIELD_EXTRACTOR value: {selector!r} "
        f"(expected 'gemini', 'anthropic', 'identity', or 'auto')"
    )
