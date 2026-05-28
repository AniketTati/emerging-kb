"""Phase 5c — KV+Tables collapse.

Per the dev-velocity refactor: instead of three LLM-driven passes (L2b
propose, L3 atomic_units plugin, L4 schema-driven re-extract), we make
ONE structured-output call per file that returns

    {
      "doc_type": "<snake_case label>",
      "scalars": [
        {
          "name": "...",
          "description": "...",
          "value": "...",          # string form; downstream coerces
          "value_type": "text|number|date|datetime|boolean|enum",
          "is_pii": false,
          "source_chunk": <int>
        }, ...
      ],
      "tables": [
        {
          "name": "transactions",          # snake_case → PascalCase sub_entity
          "description": "...",
          "cardinality": "many",           # many | one
          "columns": [
            {"name": "...", "value_type": "..."}, ...
          ],
          "rows": [
            {
              "values": {"<col>": "<str|null>", ...},
              "source_chunk": <int>,
              "source_char_start": <int|null>,
              "source_char_end": <int|null>
            }, ...
          ]
        }, ...
      ]
    }

The worker layer then:
  - Stamps `files.inferred_doc_type` from the returned `doc_type`.
  - Promotes `scalars` into `proposed_fields` → `inferred_schema_fields`
    → `schema_fields` (auto_promoted=true past the threshold) on the
    doc_root entity.
  - For each `tables[]`, ensures a `schema_entities` row with
    kind='sub_entity' + parent_type_id pointing at the doc_root, then
    writes one `extracted_entities` row per `rows[i]` with
    parent_entity_id set, unit_type=table.name, source_chunk_id and
    source_char_* preserved.

Provider plumbing mirrors fields.py / entities.py:
- GeminiKVTablesExtractor (default via auto)
- AnthropicKVTablesExtractor
- IdentityKVTablesExtractor (no-key path; returns empty payload)

Failure semantics: any non-JSON output or transport error raises
`KVTablesExtractionError`. The worker catches + parks lifecycle, exactly
like the existing fields/entities tasks.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Protocol

from pydantic import BaseModel, Field


logger = logging.getLogger(__name__)


DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_ANTHROPIC_MODEL = "claude-opus-4-7"

# One call has to fit doc-type label + all scalars + every table row.
# A 200-row bank statement at ~6 cols is ~15k tokens; 24k gives headroom
# for resumes (multiple tables) and amended contracts. json_recovery
# salvages partial output if we still overflow.
_MAX_OUTPUT_TOKENS = 24000

# Doc-text budget for the user prompt. Chunks past this get truncated
# with a "[... TRUNCATED ...]" marker — we leave a hard limit so the
# call doesn't blow input quota on a 500-page PDF.
_MAX_DOC_CHARS = 60000

VALUE_TYPES: tuple[str, ...] = (
    "text", "number", "date", "datetime", "boolean", "enum",
)
CARDINALITIES: tuple[str, ...] = ("many", "one")


_SYSTEM_PROMPT = (
    "You are a structured-extraction system. Given a chunk-numbered document, "
    "you return a SINGLE JSON object that captures the doc as scalars + tables.\n"
    "\n"
    "SCALARS are doc-level fields — one value per document. Examples: "
    "account_holder, statement_period_start, total_debits, contract_party_a, "
    "candidate_name, doc_date, total_amount.\n"
    "\n"
    "TABLES are repeated sub-entities — a list of rows of the same shape. "
    "Examples: transactions (bank_statement), clauses (contract), "
    "work_experiences (resume), line_items (invoice), messages (email_thread), "
    "lab_results (lab_report). A doc can have ZERO, ONE, or MANY tables. "
    "Resumes typically have 2-4 tables (work, education, certifications). "
    "Contracts have clauses + payment_milestones. Drawings/NDAs have no tables.\n"
    "\n"
    "Rules:\n"
    "  - Output JSON only, no preamble, no markdown.\n"
    "  - Every scalar and every table row MUST include source_chunk (the "
    "    chunk_index integer where the value comes from).\n"
    "  - Use snake_case for all field names, column names, and table names.\n"
    "  - value_type ∈ {text, number, date, datetime, boolean, enum}.\n"
    "  - cardinality ∈ {many, one}. Use 'one' only for repeated structures "
    "    where the doc literally has one row (rare — most tables are 'many').\n"
    "  - Do NOT extract the same data twice (don't promote a transaction row "
    "    column into a doc-level scalar). Scalars and tables are disjoint.\n"
    "  - Skip fields you can't determine confidently — never hallucinate.\n"
    "  - For PII detection (SSN, Aadhaar, PAN, DOB, phone, email, medical "
    "    record numbers, credit card numbers), set is_pii=true on the scalar."
)


def _build_user_prompt(
    *,
    chunk_indexed_text: str,
    doc_type_hint: str | None = None,
    existing_sub_entity_hints: list[str] | None = None,
    existing_scalar_hints: dict[str, list[str]] | None = None,
) -> str:
    """Build the user-prompt for one extraction call.

    `existing_scalar_hints` maps {doctype: [field_name, ...]} listing
    scalar field names ALREADY USED for each doctype in this workspace.
    Bug D fix: the prior prompt instructed the LLM to reuse table
    names but said nothing about scalar names — so the LLM kept
    inventing new names like `total_cost_premium` on one doc and
    `total_cost_inr` on the next doc of the SAME doctype, making
    cross-doc aggregations impossible. This hint block tells the
    LLM to reuse established names when the meaning matches; the
    LLM still invents new names for genuinely new concepts.
    """
    hints: list[str] = []
    if doc_type_hint:
        hints.append(f"Likely doc_type: {doc_type_hint}")
    if existing_sub_entity_hints:
        hints.append(
            "Existing sub-entity table names in this workspace "
            "(reuse if applicable): "
            + ", ".join(existing_sub_entity_hints)
        )
    if existing_scalar_hints:
        # Format: per-doctype field name catalog. Capped to keep the
        # prompt budget reasonable on big workspaces (>20 doctypes,
        # >50 fields/doctype is uncommon but happens).
        lines = [
            "Existing scalar field names by doc_type in this workspace. "
            "When you extract a scalar whose MEANING matches one of "
            "these names, REUSE the exact existing name (snake_case + "
            "everything). Only invent a new name when the concept is "
            "genuinely different — never paraphrase an established "
            "name (e.g. don't write `total_cost_inr` if "
            "`total_cost_premium` is already established for "
            "change_order docs).",
        ]
        for dt in sorted(existing_scalar_hints):
            fns = existing_scalar_hints[dt]
            if not fns:
                continue
            shown = fns[:50]  # cap per-doctype
            line = f"  {dt}: {', '.join(shown)}"
            if len(fns) > len(shown):
                line += f" (+{len(fns) - len(shown)} more)"
            lines.append(line)
        hints.append("\n".join(lines))
    hint_block = ("\n\n".join(hints) + "\n\n") if hints else ""

    return (
        f"{hint_block}"
        f"Document (chunk-numbered):\n{chunk_indexed_text}\n\n"
        'Return JSON exactly: {'
        '"doc_type": "<snake_case>", '
        '"scalars": [{"name": "...", "description": "...", "value": "...", '
        '"value_type": "text|number|date|datetime|boolean|enum", '
        '"is_pii": false, "source_chunk": 0}], '
        '"tables": [{"name": "<snake_case>", "description": "...", '
        '"cardinality": "many", '
        '"columns": [{"name": "...", "value_type": "..."}], '
        '"rows": [{"values": {"<col>": "<str|null>"}, "source_chunk": 0}]'
        '}]}.'
    )


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class KVScalar(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    value: str | None = None
    value_type: str = "text"
    is_pii: bool = False
    source_chunk: int | None = None


class KVColumn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    value_type: str = "text"


class KVRow(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    source_chunk: int | None = None
    source_char_start: int | None = None
    source_char_end: int | None = None


class KVTable(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    cardinality: str = "many"
    columns: list[KVColumn] = Field(default_factory=list)
    rows: list[KVRow] = Field(default_factory=list)


class KVTablesPayload(BaseModel):
    """The full extraction result for one file."""

    doc_type: str = "unknown"
    scalars: list[KVScalar] = Field(default_factory=list)
    tables: list[KVTable] = Field(default_factory=list)
    model_id: str = "identity"
    input_token_count: int = 0
    output_token_count: int = 0


class KVTablesExtractionError(Exception):
    """LLM call refused, returned bad JSON, or transport failed."""


class KVTablesExtractor(Protocol):
    async def extract(
        self,
        *,
        chunk_indexed_text: str,
        doc_type_hint: str | None = None,
        existing_sub_entity_hints: list[str] | None = None,
        existing_scalar_hints: dict[str, list[str]] | None = None,
    ) -> KVTablesPayload: ...


# ---------------------------------------------------------------------------
# Identity fallback
# ---------------------------------------------------------------------------


class IdentityKVTablesExtractor:
    """No-key path. Returns empty payload so the worker no-ops cleanly."""

    async def extract(
        self,
        *,
        chunk_indexed_text: str,
        doc_type_hint: str | None = None,
        existing_sub_entity_hints: list[str] | None = None,
        existing_scalar_hints: dict[str, list[str]] | None = None,
    ) -> KVTablesPayload:
        return KVTablesPayload(model_id="identity")


# ---------------------------------------------------------------------------
# Shared JSON parsing
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


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _snake_case(name: str) -> str:
    return (
        (name or "")
        .strip()
        .lower()
        .replace(" ", "_")
        .replace("-", "_")[:200]
    )


def _parse_scalars(raw: list[Any]) -> list[KVScalar]:
    out: list[KVScalar] = []
    valid_types = set(VALUE_TYPES)
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = _snake_case(item.get("name") or item.get("field_name") or "")
        if not name:
            continue
        vt = item.get("value_type") or "text"
        if vt not in valid_types:
            vt = "text"
        value = item.get("value")
        if value is not None and not isinstance(value, str):
            value = str(value)
        try:
            out.append(KVScalar(
                name=name,
                description=str(item.get("description") or "")[:1000],
                value=value,
                value_type=vt,
                is_pii=bool(item.get("is_pii", False)),
                source_chunk=_coerce_int(item.get("source_chunk")),
            ))
        except Exception:  # noqa: BLE001
            continue
    return out


def _parse_tables(raw: list[Any]) -> list[KVTable]:
    out: list[KVTable] = []
    valid_types = set(VALUE_TYPES)
    valid_card = set(CARDINALITIES)
    for tbl in raw:
        if not isinstance(tbl, dict):
            continue
        name = _snake_case(tbl.get("name") or "")
        if not name:
            continue
        cardinality = tbl.get("cardinality") or "many"
        if cardinality not in valid_card:
            cardinality = "many"

        col_raw = tbl.get("columns") or []
        columns: list[KVColumn] = []
        if isinstance(col_raw, list):
            for col in col_raw:
                if not isinstance(col, dict):
                    continue
                cname = _snake_case(col.get("name") or "")
                if not cname:
                    continue
                cvt = col.get("value_type") or "text"
                if cvt not in valid_types:
                    cvt = "text"
                try:
                    columns.append(KVColumn(name=cname, value_type=cvt))
                except Exception:  # noqa: BLE001
                    continue

        row_raw = tbl.get("rows") or []
        rows: list[KVRow] = []
        if isinstance(row_raw, list):
            for row in row_raw:
                if not isinstance(row, dict):
                    continue
                values = row.get("values")
                if not isinstance(values, dict):
                    continue
                # Snake-case + filter to columns we know about (or keep all
                # if columns weren't declared by the LLM).
                cleaned: dict[str, Any] = {}
                col_names = {c.name for c in columns} if columns else None
                for k, v in values.items():
                    sk = _snake_case(str(k))
                    if not sk:
                        continue
                    if col_names is not None and sk not in col_names:
                        continue
                    cleaned[sk] = v
                if not cleaned:
                    continue
                try:
                    rows.append(KVRow(
                        values=cleaned,
                        source_chunk=_coerce_int(row.get("source_chunk")),
                        source_char_start=_coerce_int(row.get("source_char_start")),
                        source_char_end=_coerce_int(row.get("source_char_end")),
                    ))
                except Exception:  # noqa: BLE001
                    continue

        try:
            out.append(KVTable(
                name=name,
                description=str(tbl.get("description") or "")[:1000],
                cardinality=cardinality,
                columns=columns,
                rows=rows,
            ))
        except Exception:  # noqa: BLE001
            continue
    return out


def _recover_partial_table(raw: str) -> dict | None:
    """Best-effort recovery of a single table dict that the LLM started
    writing but didn't finish.

    `raw` should be a substring beginning at the opening `{` of the
    truncated table. Returns a dict with whatever name/columns/rows could
    be salvaged, or None if nothing useful survived.

    Why this matters: a 200-row bank statement that truncates inside
    `tables[0].rows[157]` would otherwise drop ALL 157 closed rows
    because the outer table dict never closes. We salvage `name` via
    regex, `columns` if the columns array closed, and as many `rows` as
    survived via the tolerant array parser.
    """
    from kb.extraction.json_recovery import parse_tolerant_array_in_object

    # Salvage `name`: first `"name": "..."` after the table opens.
    name_match = re.search(r'"name"\s*:\s*"([^"\\]+)"', raw)
    if not name_match:
        return None

    # Treat the partial substring as the body of an object — the tolerant
    # array parser uses regex to locate `"<key>":[`, so a partial outer
    # object is fine.
    rows_raw, _ = parse_tolerant_array_in_object(raw, "rows")
    cols_raw, _ = parse_tolerant_array_in_object(raw, "columns")

    if not rows_raw and not cols_raw:
        return None

    # Salvage cardinality + description if present (best-effort scalar matches).
    card_match = re.search(r'"cardinality"\s*:\s*"([^"]+)"', raw)
    desc_match = re.search(r'"description"\s*:\s*"([^"\\]*)"', raw)

    return {
        "name": name_match.group(1),
        "description": desc_match.group(1) if desc_match else "",
        "cardinality": card_match.group(1) if card_match else "many",
        "columns": cols_raw,
        "rows": rows_raw,
    }


def _recover_tables_with_partial_last(raw: str) -> list[dict]:
    """Recover the `tables` array tolerantly, then attempt rescue of the
    next table that started but didn't close (the typical truncation
    point for long docs)."""
    from kb.extraction.json_recovery import parse_tolerant_array_in_object

    tables_raw, _ = parse_tolerant_array_in_object(raw, "tables")

    # Locate where the tables array started so we can scan past the last
    # complete element for a partial follow-on.
    array_match = re.search(r'"tables"\s*:\s*\[', raw)
    if array_match is None:
        return tables_raw

    # Find the position right after the LAST closed `{...}` element we
    # already recovered — anything after that is the candidate partial.
    cursor = array_match.end()
    depth = 0
    in_string = False
    escape = False
    last_close = cursor
    elem_starts: list[int] = []
    n = len(raw)
    i = cursor
    while i < n:
        ch = raw[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                if depth == 0:
                    elem_starts.append(i)
                depth += 1
            elif ch == "[":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    last_close = i + 1
            elif ch == "]":
                if depth == 0:
                    return tables_raw
                depth -= 1
        i += 1

    # If we ended with depth > 0, the LAST `{` we entered never closed →
    # rescue it.
    if depth > 0 and elem_starts:
        partial_start = elem_starts[-1]
        recovered = _recover_partial_table(raw[partial_start:])
        if recovered is not None:
            tables_raw.append(recovered)
    return tables_raw


def _parse_payload(raw: str) -> tuple[str, list[KVScalar], list[KVTable]]:
    text = _strip_code_fence(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Best-effort recovery on truncation. Top-level scalars + tables
        # arrays are recovered tolerantly; for `tables`, we additionally
        # try to salvage a partial last table's nested rows.
        from kb.extraction.json_recovery import parse_tolerant_array_in_object
        scalars_raw, sc_trunc = parse_tolerant_array_in_object(raw, "scalars")
        tables_raw = _recover_tables_with_partial_last(raw)
        if sc_trunc or tables_raw:
            logger.warning(
                "kv_tables: truncated output recovered (scalars=%d, tables=%d)",
                len(scalars_raw), len(tables_raw),
            )
        return "unknown", _parse_scalars(scalars_raw), _parse_tables(tables_raw)

    if not isinstance(data, dict):
        return "unknown", [], []

    doc_type_raw = data.get("doc_type") or "unknown"
    doc_type = (
        str(doc_type_raw).strip().lower().replace(" ", "_").replace("-", "_")[:50]
        or "unknown"
    )

    scalars_raw = data.get("scalars") or []
    if not isinstance(scalars_raw, list):
        scalars_raw = []
    tables_raw = data.get("tables") or []
    if not isinstance(tables_raw, list):
        tables_raw = []

    return doc_type, _parse_scalars(scalars_raw), _parse_tables(tables_raw)


def _truncate_doc(text: str) -> str:
    if not text:
        return ""
    if len(text) <= _MAX_DOC_CHARS:
        return text
    return text[:_MAX_DOC_CHARS] + "\n[... TRUNCATED ...]"


# ---------------------------------------------------------------------------
# GeminiKVTablesExtractor
# ---------------------------------------------------------------------------


class GeminiKVTablesExtractor:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: Any | None = None,
        model: str | None = None,
    ) -> None:
        if client is None:
            if not api_key:
                raise KVTablesExtractionError(
                    "GeminiKVTablesExtractor requires api_key or client"
                )
            from google.genai import Client
            client = Client(api_key=api_key)
        self._client = client
        self._model = (
            model
            or os.environ.get("KB_KV_TABLES_MODEL")
            or os.environ.get("KB_FIELD_MODEL")
            or DEFAULT_GEMINI_MODEL
        )

    async def extract(
        self,
        *,
        chunk_indexed_text: str,
        doc_type_hint: str | None = None,
        existing_sub_entity_hints: list[str] | None = None,
        existing_scalar_hints: dict[str, list[str]] | None = None,
    ) -> KVTablesPayload:
        from google.genai import types

        model = self._model
        config = types.GenerateContentConfig(
            system_instruction=_SYSTEM_PROMPT,
            max_output_tokens=_MAX_OUTPUT_TOKENS,
            response_mime_type="application/json",
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )
        user_prompt = _build_user_prompt(
            chunk_indexed_text=_truncate_doc(chunk_indexed_text),
            doc_type_hint=doc_type_hint,
            existing_sub_entity_hints=existing_sub_entity_hints,
            existing_scalar_hints=existing_scalar_hints,
        )
        try:
            response = await self._client.aio.models.generate_content(
                model=model,
                contents=user_prompt,
                config=config,
            )
        except Exception as exc:  # noqa: BLE001
            block_reason = getattr(
                getattr(exc, "prompt_feedback", None), "block_reason", None
            )
            suffix = f" (block_reason={block_reason})" if block_reason else ""
            raise KVTablesExtractionError(
                f"Gemini kv_tables call failed: {exc}{suffix}"
            ) from exc

        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            raise KVTablesExtractionError("Gemini returned no candidates")

        raw_text = ""
        content = getattr(candidates[0], "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            t = getattr(part, "text", None)
            if t:
                raw_text = t
                break

        doc_type, scalars, tables = _parse_payload(raw_text)
        usage = getattr(response, "usage_metadata", None)
        return KVTablesPayload(
            doc_type=doc_type,
            scalars=scalars,
            tables=tables,
            model_id=model,
            input_token_count=getattr(usage, "prompt_token_count", 0) or 0,
            output_token_count=getattr(usage, "candidates_token_count", 0) or 0,
        )


# ---------------------------------------------------------------------------
# AnthropicKVTablesExtractor
# ---------------------------------------------------------------------------


class AnthropicKVTablesExtractor:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: Any | None = None,
        model: str | None = None,
    ) -> None:
        if client is None:
            if not api_key:
                raise KVTablesExtractionError(
                    "AnthropicKVTablesExtractor requires api_key or client"
                )
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=api_key)
        self._client = client
        self._model = (
            model
            or os.environ.get("KB_KV_TABLES_MODEL")
            or os.environ.get("KB_FIELD_MODEL")
            or DEFAULT_ANTHROPIC_MODEL
        )

    async def extract(
        self,
        *,
        chunk_indexed_text: str,
        doc_type_hint: str | None = None,
        existing_sub_entity_hints: list[str] | None = None,
        existing_scalar_hints: dict[str, list[str]] | None = None,
    ) -> KVTablesPayload:
        import anthropic

        model = self._model
        user_prompt = _build_user_prompt(
            chunk_indexed_text=_truncate_doc(chunk_indexed_text),
            doc_type_hint=doc_type_hint,
            existing_sub_entity_hints=existing_sub_entity_hints,
            existing_scalar_hints=existing_scalar_hints,
        )
        try:
            response = await self._client.messages.create(
                model=model,
                max_tokens=_MAX_OUTPUT_TOKENS,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
        except anthropic.APIError as exc:
            raise KVTablesExtractionError(
                f"Anthropic kv_tables call failed: {exc}"
            ) from exc

        raw_text = ""
        for block in response.content or []:
            if getattr(block, "type", None) == "text":
                raw_text = getattr(block, "text", "")
                break

        doc_type, scalars, tables = _parse_payload(raw_text)
        usage = response.usage
        return KVTablesPayload(
            doc_type=doc_type,
            scalars=scalars,
            tables=tables,
            model_id=model,
            input_token_count=getattr(usage, "input_tokens", 0) or 0,
            output_token_count=getattr(usage, "output_tokens", 0) or 0,
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_kv_tables_extractor() -> KVTablesExtractor:
    """Selector mirrors fields.py / entities.py.

    Reads `KB_KV_TABLES_EXTRACTOR`; falls back to `KB_FIELD_EXTRACTOR` so
    that existing deployments don't need a config change to roll forward.
    """
    selector = (
        os.environ.get("KB_KV_TABLES_EXTRACTOR")
        or os.environ.get("KB_FIELD_EXTRACTOR")
        or "auto"
    ).lower()

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
                "KB_KV_TABLES_EXTRACTOR=gemini requires KB_GEMINI_API_KEY"
            )
        return GeminiKVTablesExtractor(api_key=api_key)

    if selector == "anthropic":
        api_key = os.environ.get("KB_ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError(
                "KB_KV_TABLES_EXTRACTOR=anthropic requires KB_ANTHROPIC_API_KEY"
            )
        return AnthropicKVTablesExtractor(api_key=api_key)

    if selector == "identity":
        return IdentityKVTablesExtractor()

    raise ValueError(
        f"Unknown KB_KV_TABLES_EXTRACTOR value: {selector!r} "
        f"(expected 'gemini', 'anthropic', 'identity', or 'auto')"
    )
