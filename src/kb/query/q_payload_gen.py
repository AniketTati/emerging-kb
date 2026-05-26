"""Q-mode payload generator — natural language → structured QPlan.

Closes the half-built Q-mode gap: the q_planner module (`kb.q_planner`)
ships grammar / validator / compiler / executor / audit-artifact, but
nothing turns natural language into the `q_payload` dict that pipeline
expects. The main planner (`kb.query.planner`) just emits `mode='Q'` and
leaves `q_payload=None`, so `_route_q_mode` always refuses.

This module fixes that with a focused second LLM call, provider-neutral
via `kb.query.llm_client.JsonLLMClient`. Gemini and Anthropic adapters
both ship; identity falls back cleanly to a refusal.

The flow:

  user query  →  LLMPlanner.plan()  →  mode='Q'
                            ↓
                  generate_q_payload(query, llm)
                            ↓
                  JsonLLMClient.generate_json(query, catalog+grammar prompt)
                            ↓
                  JSON {from, filters, aggregations, group_by, ...}
                            ↓
                  q_planner.parse_plan()  →  q_planner.validate()
                            ↓
                  validated dict attached to plan.q_payload
                            ↓
                  _route_q_mode compiles + executes against PG

When the second call fails (no LLM, parse error, validator rejection),
this returns `(None, reason)` and the caller surfaces a clean refusal
message — instead of the previous misleading "configure KB_PLANNER=gemini"
lie.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from kb.q_planner.catalog import ALLOWED_COLUMNS, ALLOWED_TABLES
from kb.q_planner.grammar import (
    ALLOWED_AGGREGATIONS, ALLOWED_OPERATORS, parse_plan, QPlanParseError,
)
from kb.q_planner.validator import QPlanValidationError, validate
from kb.query.llm_client import JsonLLMClient, LLMCallError


logger = logging.getLogger(__name__)


# Cached system prompt — built once from the catalog so changes to
# ALLOWED_COLUMNS automatically refresh it on next import.
_SYSTEM_PROMPT: str | None = None


def _build_system_prompt() -> str:
    """Embed the catalog + grammar enums so the LLM emits valid plans.

    Sorted for stable cache-hit behavior across process restarts.
    """
    # Group columns by table for readability — the LLM does materially
    # better when the catalog is structured rather than a flat list.
    by_table: dict[str, list[tuple[str, str]]] = {}
    for (table, col), col_type in ALLOWED_COLUMNS.items():
        by_table.setdefault(table, []).append((col, col_type))

    catalog_lines: list[str] = []
    for table in sorted(by_table.keys()):
        cols_for_table = sorted(by_table[table])
        col_block = "\n".join(
            f"    {col} ({col_type})" for col, col_type in cols_for_table
        )
        catalog_lines.append(f"  {table}:\n{col_block}")

    catalog_str = "\n".join(catalog_lines)

    ops_str = ", ".join(sorted(ALLOWED_OPERATORS))
    aggs_str = ", ".join(sorted(ALLOWED_AGGREGATIONS))
    tables_str = ", ".join(sorted(ALLOWED_TABLES))

    return (
        "You are a SQL query planner for a knowledge base. Convert the "
        "user's natural-language aggregation question into a STRICT JSON "
        "plan that the structured-query compiler will execute. You MUST "
        "use only the tables and columns in the catalog below — any other "
        "field name will be rejected with no answer reaching the user.\n"
        "\n"
        "## Allowed tables\n"
        f"{tables_str}\n"
        "\n"
        "## Catalog (table → column (type))\n"
        f"{catalog_str}\n"
        "\n"
        "## Allowed filter operators\n"
        f"{ops_str}\n"
        "(use `in` with a list value; `between` with a [low, high] list; "
        "`is_null` / `is_not_null` take no value)\n"
        "\n"
        "## Allowed aggregations\n"
        f"{aggs_str}\n"
        "(use COUNT with field='*' to count rows)\n"
        "\n"
        "## Output schema (return JSON exactly matching this)\n"
        "{\n"
        '  "from":        "<table_name>",\n'
        '  "filters":     [{"field": "<col>", "op": "<op>", "value": <primitive|list>}],\n'
        '  "aggregations": [{"op": "<AGG>", "field": "<col>|*", "alias": "<safe_ident>"}],\n'
        '  "group_by":    ["<col1>", "<col2>"],\n'
        '  "order_by":    [{"field": "<col_or_alias>", "direction": "asc|desc"}],\n'
        '  "limit":       <int 1..10000>\n'
        "}\n"
        "\n"
        "## Rules\n"
        "1. Only ONE table per query (no joins) — pick the table that has the columns you need.\n"
        "2. Aliases must be lowercase identifiers (letters, digits, underscore; "
        "max 63 chars).\n"
        "3. For 'how many' / 'count' questions, use COUNT with field='*'.\n"
        "4. For 'total' / 'sum of' questions, use SUM on the relevant numeric column.\n"
        "5. For 'breakdown by X' / 'per X' / 'grouped by X', set group_by=['X'] and "
        "include the grouping column in the SELECT via an aggregation alias.\n"
        "6. Always include an aggregation — bare row dumps are not Q-mode.\n"
        "7. When the user's question can't be answered with the catalog, return "
        "the EXACT JSON {\"refuse\": true, \"reason\": \"<one-sentence reason>\"} "
        "and nothing else.\n"
        "8. Default limit=100. Use higher only when the user asked for it explicitly.\n"
        "\n"
        "## Typed nested entities (IMPORTANT)\n"
        "Documents with repeated structures are stored in `extracted_entities`\n"
        "as one parent row (`parent_entity_id IS NULL`) plus N child rows\n"
        "(each with `parent_entity_id` set to the parent's id, and `unit_type`\n"
        "naming the structural kind — 'transaction', 'clause', 'line_item',\n"
        "'email_message', 'row', etc.). Per-row column values live in the\n"
        "`fields` jsonb column.\n"
        "\n"
        "To aggregate over a jsonb column key, use the syntax\n"
        "  `<col>.<key>::<cast>`  (e.g. `fields.debit::numeric`)\n"
        "in the aggregation `field`. Allowed casts: numeric, integer,\n"
        "bigint, real, text, date, timestamptz. The compiler emits\n"
        "`(t.\"fields\"->>'key')::cast` safely.\n"
        "\n"
        "Examples:\n"
        "  - 'sum debits across all transactions' →\n"
        "    {from:'extracted_entities', filters:[{field:'unit_type',op:'eq',value:'transaction'}],\n"
        "     aggregations:[{op:'SUM', field:'fields.debit::numeric', alias:'total_debits'}]}\n"
        "  - 'count transactions' →\n"
        "    filter unit_type='transaction', aggregations:[{op:'COUNT', field:'*', alias:'n'}]\n"
        "  - 'count line items per invoice' →\n"
        "    filter unit_type='line_item', group_by:['file_id'],\n"
        "    aggregations:[{op:'COUNT', field:'*', alias:'n'}]\n"
        "  - 'earliest transaction date' →\n"
        "    filter unit_type='transaction',\n"
        "    aggregations:[{op:'MIN', field:'fields.date::date', alias:'earliest'}]\n"
        "  - 'distinct counterparties' →\n"
        "    filter unit_type='transaction',\n"
        "    aggregations:[{op:'COUNT_DISTINCT', field:'fields.counterparty::text', alias:'n'}]\n"
        "  - 'invoices with > 10 line items' → still not expressible (HAVING\n"
        "    not supported); refuse with reason.\n"
        "  - Use `extracted_entities.rarity_score` (real, not jsonb) to find\n"
        "    anomalies — `aggregations:[{op:'AVG', field:'rarity_score',\n"
        "    alias:'avg_rarity'}]`.\n"
        "\n"
        "Use SUM/AVG on jsonb keys ONLY with numeric casts. Use MIN/MAX with\n"
        "numeric, text, date, or timestamptz casts. Refuse only when the\n"
        "user is asking for HAVING-style filtering or set-op queries.\n"
        "\n"
        "Return JSON only. No prose, no markdown fences."
    )


def _get_system_prompt() -> str:
    global _SYSTEM_PROMPT
    if _SYSTEM_PROMPT is None:
        _SYSTEM_PROMPT = _build_system_prompt()
    return _SYSTEM_PROMPT


def _coerce_plan_shape(raw: dict[str, Any]) -> dict[str, Any]:
    """Tolerate a few common LLM misshapes before handing off to the
    grammar parser. We DON'T relax safety — the validator still has
    final say."""
    # `from_table` is the canonical key; the grammar also accepts `from`.
    if "from" not in raw and "from_table" in raw:
        raw["from"] = raw["from_table"]
    # Some LLM responses wrap the plan in {"plan": {...}}.
    if (
        isinstance(raw.get("plan"), dict)
        and "from" not in raw and "aggregations" not in raw
    ):
        return _coerce_plan_shape(raw["plan"])
    return raw


async def generate_q_payload(
    query: str,
    *,
    llm: JsonLLMClient | None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Run the LLM-to-QPlan path. Returns ``(payload, reason)``:

    - ``(payload, None)`` — the LLM produced a plan that parsed AND
      validated against the catalog. The dict is safe to attach to
      ``plan.q_payload``.
    - ``(None, "refuse: ...")`` — the LLM explicitly refused (catalog
      can't answer the question). Bubble up to user as a refusal.
    - ``(None, "parse_error: ...")`` / ``(None, "validation: ...")`` —
      the LLM's response failed grammar/catalog checks. Caller should
      refuse with a clean message.
    - ``(None, "no_llm: ...")`` — no LLM client was provided; the
      deterministic fallback isn't viable for SQL generation, so this
      is a refusal reason (not a silent pass).
    - ``(None, "llm_error: ...")`` — the LLM transport failed
      (network/auth/quota). Caller refuses; future work could retry.

    The function never raises — every failure mode maps to a reason
    string so the orchestrator can render a single coherent refusal
    surface.
    """
    if llm is None:
        return None, (
            "no_llm: Q-mode requires an LLM planner (set KB_PLANNER=gemini "
            "or anthropic with the matching API key)"
        )

    try:
        raw_text = await llm.generate_json(
            user=f"User question: {query}",
            system=_get_system_prompt(),
            max_tokens=800,
        )
    except LLMCallError as exc:
        logger.warning("Q-payload LLM call failed: %s", exc)
        return None, f"llm_error: {exc}"

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        return None, f"parse_error: not valid JSON ({exc})"
    if not isinstance(data, dict):
        return None, "parse_error: top-level JSON must be an object"

    # Honour explicit refusal (rule 7 in the system prompt).
    if data.get("refuse") is True:
        reason = str(data.get("reason") or "the catalog can't answer this question")
        return None, f"refuse: {reason}"

    data = _coerce_plan_shape(data)

    # Parse → grammar enums. Then validate → catalog whitelist.
    try:
        typed = parse_plan(data)
    except QPlanParseError as exc:
        return None, f"parse_error: {exc}"
    try:
        validated = validate(typed)
    except QPlanValidationError as exc:
        return None, f"validation: {exc}"

    _ = validated  # validator passes mean the dict is safe to round-trip.
    return data, None
