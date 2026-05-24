"""Phase 6 — schema-driven entity extraction.

Per build_tracker §5.13 (13 locked decisions).

For each `schema_entity` whose parent `schema` is active in the workspace and
matches the file's `inferred_doc_type`, this module runs one LLM call:

- System prompt + chunk-numbered doc text (`[CHUNK_0] ... [CHUNK_1] ...`).
- `response_schema` constrains output to `{instances: [{fields, citations}]}`
  where fields keys match the schema_entity's schema_fields and citations
  keys mirror that with chunk_index values.
- Worker maps chunk_index → contextual_chunks.id post-call.

Three impls satisfy the same Protocol (`SchemaDrivenExtractor`):
- `GeminiSchemaDrivenExtractor` — default; uses google-genai response_schema.
- `AnthropicSchemaDrivenExtractor` — alt; uses Claude with JSON-mode prompt.
- `IdentitySchemaDrivenExtractor` — fallback; returns `[]`. CI / no-key path.
"""

from __future__ import annotations

import json
import os
from typing import Any, Protocol

from pydantic import BaseModel, Field


DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_ANTHROPIC_MODEL = "claude-opus-4-7"
_MAX_OUTPUT_TOKENS = 6000  # plenty for ~20 instances × ~10 fields each

_VALID_VALUE_TYPES = ("string", "number", "boolean", "date", "datetime")

_SYSTEM_PROMPT = (
    "You are a structured-extraction system. Given chunk-numbered document text and "
    "a target entity definition, you identify every INSTANCE of that entity in the "
    "doc and return its typed field values + a per-field citation pointing at the "
    "chunk index that supports each value. Cite the chunk_index integer (e.g., 0, 1, 2). "
    "Return null for fields you cannot determine confidently. Output JSON only."
)


class SchemaEntityRequest(BaseModel):
    """One request: extract instances of `schema_entity_name` whose fields match
    `field_defs`. `chunk_indexed_text` is the doc text with [CHUNK_N] markers."""

    schema_entity_name: str
    schema_entity_description: str = ""
    # [{name, type, nl_description}] — type ∈ string/number/boolean/date/datetime.
    field_defs: list[dict[str, str]]
    chunk_indexed_text: str


class ExtractedInstance(BaseModel):
    """One extracted entity instance."""
    fields: dict[str, Any] = Field(default_factory=dict)
    # field_name → chunk_index (int). Worker resolves to contextual_chunks.id.
    citations: dict[str, int] = Field(default_factory=dict)


class SchemaExtractionResult(BaseModel):
    instances: list[ExtractedInstance]
    model_id: str
    input_token_count: int = 0
    output_token_count: int = 0


class SchemaExtractionError(Exception):
    """LLM call refused or failed."""


class SchemaDrivenExtractor(Protocol):
    async def extract(
        self, *, request: SchemaEntityRequest
    ) -> SchemaExtractionResult: ...


# ---------------------------------------------------------------------------
# Identity fallback
# ---------------------------------------------------------------------------


class IdentitySchemaDrivenExtractor:
    """Returns no instances. CI / no-key path."""

    async def extract(
        self, *, request: SchemaEntityRequest
    ) -> SchemaExtractionResult:
        return SchemaExtractionResult(instances=[], model_id="identity")


# ---------------------------------------------------------------------------
# Helpers
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


def _parse_instances(raw: str, *, valid_field_names: set[str]) -> list[ExtractedInstance]:
    """Parse `{instances: [{fields: {...}, citations: {...}}, ...]}`.

    Filters out:
      - non-dict items in the instances list.
      - field names not declared in the schema (silent drop).
      - citation values that aren't integers.
    """
    text = _strip_code_fence(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SchemaExtractionError(
            f"LLM returned invalid JSON: {exc}; output: {raw[:200]}"
        ) from exc

    raw_list = data.get("instances") if isinstance(data, dict) else None
    if not isinstance(raw_list, list):
        return []

    out: list[ExtractedInstance] = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        raw_fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
        raw_citations = (
            item.get("citations") if isinstance(item.get("citations"), dict) else {}
        )
        # Filter to declared fields only; coerce citation values to int.
        fields = {k: v for k, v in raw_fields.items() if k in valid_field_names}
        citations: dict[str, int] = {}
        for k, v in raw_citations.items():
            if k not in valid_field_names:
                continue
            try:
                citations[k] = int(v)
            except (TypeError, ValueError):
                continue
        if not fields and not citations:
            continue
        try:
            out.append(ExtractedInstance(fields=fields, citations=citations))
        except Exception:  # noqa: BLE001
            continue
    return out


def _build_user_prompt(request: SchemaEntityRequest) -> str:
    field_lines = "\n".join(
        f"  - {fd['name']} ({fd['type']}): {fd.get('nl_description') or ''}".rstrip()
        for fd in request.field_defs
    )
    return (
        f"Target entity: {request.schema_entity_name}\n"
        f"Description: {request.schema_entity_description or '(none)'}\n"
        f"Fields to extract per instance:\n{field_lines}\n\n"
        f"Document (chunk-numbered):\n{request.chunk_indexed_text}\n\n"
        'Return JSON exactly: {"instances": [{"fields": {"<field_name>": <value or null>}, '
        '"citations": {"<field_name>": <chunk_index_integer>}}]}. '
        "Omit a field from citations if you can't pin a specific chunk. "
        "Skip an instance entirely if no fields are determinable."
    )


# ---------------------------------------------------------------------------
# GeminiSchemaDrivenExtractor
# ---------------------------------------------------------------------------


class GeminiSchemaDrivenExtractor:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: Any | None = None,
        model: str | None = None,
    ) -> None:
        if client is None:
            if not api_key:
                raise SchemaExtractionError(
                    "GeminiSchemaDrivenExtractor requires api_key or client"
                )
            from google.genai import Client
            client = Client(api_key=api_key)
        self._client = client
        self._model = (
            model or os.environ.get("KB_ENTITY_MODEL") or DEFAULT_GEMINI_MODEL
        )

    async def extract(
        self, *, request: SchemaEntityRequest
    ) -> SchemaExtractionResult:
        from google.genai import types

        model = os.environ.get("KB_ENTITY_MODEL") or self._model
        valid_field_names = {fd["name"] for fd in request.field_defs}

        config = types.GenerateContentConfig(
            system_instruction=_SYSTEM_PROMPT,
            max_output_tokens=_MAX_OUTPUT_TOKENS,
            response_mime_type="application/json",
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )

        try:
            response = await self._client.aio.models.generate_content(
                model=model,
                contents=_build_user_prompt(request),
                config=config,
            )
        except Exception as exc:
            block_reason = getattr(
                getattr(exc, "prompt_feedback", None), "block_reason", None
            )
            suffix = f" (block_reason={block_reason})" if block_reason else ""
            raise SchemaExtractionError(
                f"Gemini schema-extract call failed: {exc}{suffix}"
            ) from exc

        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            raise SchemaExtractionError("Gemini returned no candidates")

        raw_text = ""
        content = getattr(candidates[0], "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            t = getattr(part, "text", None)
            if t:
                raw_text = t
                break

        instances = _parse_instances(raw_text, valid_field_names=valid_field_names)
        usage = getattr(response, "usage_metadata", None)
        return SchemaExtractionResult(
            instances=instances,
            model_id=model,
            input_token_count=getattr(usage, "prompt_token_count", 0) or 0,
            output_token_count=getattr(usage, "candidates_token_count", 0) or 0,
        )


# ---------------------------------------------------------------------------
# AnthropicSchemaDrivenExtractor
# ---------------------------------------------------------------------------


class AnthropicSchemaDrivenExtractor:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: Any | None = None,
        model: str | None = None,
    ) -> None:
        if client is None:
            if not api_key:
                raise SchemaExtractionError(
                    "AnthropicSchemaDrivenExtractor requires api_key or client"
                )
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=api_key)
        self._client = client
        self._model = (
            model or os.environ.get("KB_ENTITY_MODEL") or DEFAULT_ANTHROPIC_MODEL
        )

    async def extract(
        self, *, request: SchemaEntityRequest
    ) -> SchemaExtractionResult:
        import anthropic
        model = os.environ.get("KB_ENTITY_MODEL") or self._model
        valid_field_names = {fd["name"] for fd in request.field_defs}

        try:
            response = await self._client.messages.create(
                model=model,
                max_tokens=_MAX_OUTPUT_TOKENS,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": _build_user_prompt(request)}],
            )
        except anthropic.APIError as exc:
            raise SchemaExtractionError(f"Anthropic call failed: {exc}") from exc

        raw_text = ""
        for block in response.content:
            if getattr(block, "type", None) == "text":
                raw_text = getattr(block, "text", "")
                break
        instances = _parse_instances(raw_text, valid_field_names=valid_field_names)
        usage = response.usage
        return SchemaExtractionResult(
            instances=instances,
            model_id=model,
            input_token_count=getattr(usage, "input_tokens", 0) or 0,
            output_token_count=getattr(usage, "output_tokens", 0) or 0,
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_schema_driven_extractor() -> SchemaDrivenExtractor:
    selector = (os.environ.get("KB_ENTITY_EXTRACTOR") or "auto").lower()

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
                "KB_ENTITY_EXTRACTOR=gemini requires KB_GEMINI_API_KEY"
            )
        return GeminiSchemaDrivenExtractor(api_key=api_key)

    if selector == "anthropic":
        api_key = os.environ.get("KB_ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError(
                "KB_ENTITY_EXTRACTOR=anthropic requires KB_ANTHROPIC_API_KEY"
            )
        return AnthropicSchemaDrivenExtractor(api_key=api_key)

    if selector == "identity":
        return IdentitySchemaDrivenExtractor()

    raise ValueError(
        f"Unknown KB_ENTITY_EXTRACTOR value: {selector!r} "
        f"(expected 'gemini', 'anthropic', 'identity', or 'auto')"
    )


def build_chunk_indexed_text(chunks: list[tuple[str, str]]) -> str:
    """Format chunks as `[CHUNK_0]\n<text>\n[CHUNK_1]\n<text>...` for the LLM.

    `chunks` is [(contextual_chunk_id, contextual_text), ...] in order. The
    returned string + the chunk_id ordering is the LLM's chunk_index map.
    """
    parts = []
    for idx, (_cc_id, text) in enumerate(chunks):
        parts.append(f"[CHUNK_{idx}]\n{text}")
    return "\n\n".join(parts)
