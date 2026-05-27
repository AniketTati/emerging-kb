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

from kb.db.pool import Connection
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
        "4a. AMBIGUOUS-SUM RULE — when the user says 'sum of <thing>' or "
        "'total <things>' and the schema hints show MULTIPLE numeric "
        "columns and the user didn't name which one (e.g. 'sum of "
        "transactions' when transaction rows carry both `debit` and "
        "`credit`), emit MULTIPLE SUM aggregations — one per numeric "
        "column — so the user gets a complete picture rather than an "
        "arbitrary pick. Example: 'sum of transactions' →\n"
        "    aggregations: [\n"
        "      {op:'SUM', field:'fields.debit::numeric',  alias:'total_debits'},\n"
        "      {op:'SUM', field:'fields.credit::numeric', alias:'total_credits'}\n"
        "    ]\n"
        "  Identify 'numeric' columns from semantic naming (amount, total, "
        "debit, credit, price, value, *_usd, *_amount, qty, quantity, n_*). "
        "EXCLUDE columns that look like running state (balance, *_after, "
        "*_running). NEVER emit SUM(fields.balance) — balance is a running "
        "value, summing it across rows is meaningless.\n"
        "4b. NEVER hallucinate a column name like 'amount' or 'value' "
        "that isn't in the workspace schema hints; aggregating a missing "
        "key silently returns NULL.\n"
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
        "## Cross-doc entity questions — use `canonical_entities`\n"
        "Some questions are about entities (people, organizations, locations)\n"
        "deduplicated ACROSS the whole corpus, not about a single doc's\n"
        "structural rows. For these, use `canonical_entities` instead of\n"
        "`extracted_entities`. Each row is one unique entity; `entity_type`\n"
        "is the NER label (commonly: PERSON, ORG, LOC, GPE, EVENT,\n"
        "PRODUCT, FAC, NORP, LAW); `mention_count` is how many surface\n"
        "mentions across all docs resolved to it; `canonical_name` is\n"
        "the chosen display name.\n"
        "\n"
        "Use canonical_entities when the question is about cross-doc\n"
        "entity cardinality / listing, e.g.:\n"
        "  - 'how many distinct sub-contractors / vendors / parties' →\n"
        "    {from:'canonical_entities', filters:[{field:'entity_type',op:'eq',value:'ORG'}],\n"
        "     aggregations:[{op:'COUNT', field:'*', alias:'n'}]}\n"
        "  - 'list all people involved across the contracts' →\n"
        "    {from:'canonical_entities', filters:[{field:'entity_type',op:'eq',value:'PERSON'}],\n"
        "     aggregations:[{op:'COUNT', field:'*', alias:'n_people'}]}\n"
        "  - 'how many organizations are mentioned more than 5 times' →\n"
        "    {from:'canonical_entities',\n"
        "     filters:[{field:'entity_type',op:'eq',value:'ORG'},\n"
        "              {field:'mention_count',op:'gt',value:5}],\n"
        "     aggregations:[{op:'COUNT', field:'*', alias:'n'}]}\n"
        "\n"
        "Use extracted_entities (NOT canonical_entities) when the question\n"
        "is about a doc's structural rows — transactions, line_items,\n"
        "clauses, safety_incidents, RFI_responses, etc. (those rows live\n"
        "in extracted_entities with parent_entity_id pointing at the\n"
        "doc_root and unit_type naming the structural kind).\n"
        "\n"
        "## Cross-doc summary scalars — use `proposed_fields`\n"
        "Per-document summary values (contract_value, total_amount,\n"
        "total_cost_premium, effective_date, drawing_number, etc.) are\n"
        "stored in `proposed_fields` — one row per (file, field_name)\n"
        "with `value_text` (always TEXT — cast as needed) and\n"
        "`inferred_doc_type` directly on the row (no join needed).\n"
        "\n"
        "Use proposed_fields when the question asks for an aggregation\n"
        "over a TOP-LEVEL doc-summary field, e.g.:\n"
        "  - 'total cumulative change-order value' →\n"
        "    {from:'proposed_fields',\n"
        "     filters:[{field:'inferred_doc_type',op:'eq',value:'change_order'},\n"
        "              {field:'field_name',op:'eq',value:'total_cost_premium'}],\n"
        "     aggregations:[{op:'SUM', field:'value_text::numeric', alias:'total'}]}\n"
        "  - 'count of distinct drawing numbers' →\n"
        "    {from:'proposed_fields', filters:[{field:'field_name',op:'eq',value:'drawing_number'}],\n"
        "     aggregations:[{op:'COUNT_DISTINCT', field:'value_text', alias:'n'}]}\n"
        "  - 'earliest contract effective date' →\n"
        "    {from:'proposed_fields',\n"
        "     filters:[{field:'inferred_doc_type',op:'eq',value:'construction_contract'},\n"
        "              {field:'field_name',op:'eq',value:'effective_date'}],\n"
        "     aggregations:[{op:'MIN', field:'value_text::date', alias:'earliest'}]}\n"
        "\n"
        "Cast `value_text` to the right type for the aggregation:\n"
        "  - ::numeric for SUM/AVG/MIN/MAX over numeric values\n"
        "  - ::date for MIN/MAX over dates\n"
        "  - leave as text for COUNT / COUNT_DISTINCT\n"
        "\n"
        "## When sum/count needs to be over WHOLE DOCS, not sub-entities\n"
        "Alternative: filter `extracted_entities` by\n"
        "`parent_entity_id IS NULL` to get just the doc-root rows, then\n"
        "aggregate on `fields.<key>::numeric`. Prefer proposed_fields\n"
        "when the value is a top-level summary scalar (faster: no jsonb\n"
        "extraction).\n"
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


async def discover_unit_type_schema(
    conn: Connection | None,
    *,
    workspace_id: str | None,
    limit_keys_per_type: int = 20,
) -> dict[str, list[str]]:
    """Return `{unit_type: [field_key, …]}` listing the jsonb keys that
    actually appear in `extracted_entities.fields` for each unit_type
    in this workspace.

    The Q-mode planner LLM has no inherent knowledge of which jsonb
    keys exist per (workspace, unit_type) — the keys are corpus-
    discovered. Without this hint the LLM tends to hallucinate generic
    column names (e.g. `fields.amount` on a transactions table that
    actually carries `debit`/`credit`/`balance`) and the resulting
    aggregate returns NULL silently.

    Returns `{}` when the lookup can't run (no conn / no workspace_id);
    caller falls back to the catalog-only prompt.
    """
    if conn is None or not workspace_id:
        return {}
    cur = await conn.execute(
        "SELECT unit_type, jsonb_object_keys(fields) AS k, count(*) AS n "
        "FROM extracted_entities "
        "WHERE workspace_id = %s "
        "  AND unit_type IS NOT NULL "
        "  AND fields IS NOT NULL "
        "GROUP BY unit_type, k "
        "ORDER BY unit_type, n DESC",
        (workspace_id,),
    )
    rows = await cur.fetchall()
    out: dict[str, list[str]] = {}
    for unit_type, key, _n in rows:
        bucket = out.setdefault(str(unit_type), [])
        if len(bucket) < limit_keys_per_type:
            bucket.append(str(key))
    return out


async def discover_proposed_fields_schema(
    conn: Connection | None,
    *,
    workspace_id: str | None,
    limit_fields_per_type: int = 30,
) -> dict[str, list[str]]:
    """Return `{inferred_doc_type: [field_name, …]}` listing the
    proposed_fields field_names that exist for each doctype in this
    workspace. Mirrors `discover_unit_type_schema` but for the
    top-level scalar layer.

    Without this hint the LLM can't know e.g. that the construction
    workspace has `inferred_doc_type='change_order'` with fields
    `total_cost_premium`, `original_contract_value`, etc., so it
    refuses (or guesses) when asked for cross-doc summary
    aggregations. This was Bug q033 in the construction eval.
    """
    if conn is None or not workspace_id:
        return {}
    cur = await conn.execute(
        "SELECT inferred_doc_type, field_name, count(*) AS n "
        "FROM proposed_fields "
        "WHERE workspace_id = %s "
        "  AND inferred_doc_type IS NOT NULL "
        "GROUP BY inferred_doc_type, field_name "
        "ORDER BY inferred_doc_type, n DESC",
        (workspace_id,),
    )
    rows = await cur.fetchall()
    out: dict[str, list[str]] = {}
    for dt, fn, _n in rows:
        bucket = out.setdefault(str(dt), [])
        if len(bucket) < limit_fields_per_type:
            bucket.append(str(fn))
    return out


def _format_proposed_fields_hints(schema: dict[str, list[str]]) -> str:
    """Render the discovered doctype→field_name map as a hint block.

    Tells the LLM which (inferred_doc_type, field_name) pairs exist in
    `proposed_fields` for this workspace, so cross-doc summary
    aggregations don't refuse with "no such doctype available".
    """
    if not schema:
        return ""
    lines = [
        "",
        "## proposed_fields by doc_type (authoritative)",
        "",
        "Cross-doc summary scalars live in `proposed_fields` (one row per "
        "(file, field_name) with `value_text` always TEXT — cast via "
        "`value_text::numeric` / `::date` etc. as needed). The "
        "(inferred_doc_type, field_name) pairs available in THIS "
        "workspace are listed below. Filter `inferred_doc_type` AND "
        "`field_name` with `eq` to scope a SUM/COUNT/MIN/MAX to the "
        "right field across all docs of that type.",
        "",
        "inferred_doc_type → available field_names:",
    ]
    all_types = sorted(schema.keys())
    truncated = all_types[60:]
    for dt in all_types[:60]:
        fns = schema[dt]
        lines.append(f"  {dt}: [{', '.join(fns)}]")
    if truncated:
        lines.append(
            f"  …and {len(truncated)} more doc_types not shown "
            f"({len(all_types)} total)."
        )
    return "\n".join(lines)


def _format_schema_hints(schema: dict[str, list[str]]) -> str:
    """Render the discovered jsonb-key map as a per-call hint block
    prepended to the LLM user message. Kept terse because the system
    prompt is already large.

    Stored unit_types are SINGULAR (`transaction`, `expense`,
    `line_item`) — see `singularize_unit_type` in the extraction
    worker. We tell the LLM explicitly to stem the user's plural to
    the singular form before matching against this list.
    """
    if not schema:
        return ""
    lines = [
        "## WORKSPACE DATA (authoritative — overrides the catalog "
        "examples in the system prompt)",
        "",
        "The data in this workspace has these unit_types in "
        "`extracted_entities.unit_type` and these jsonb keys in "
        "`extracted_entities.fields`. Use ONLY these values — do NOT "
        "invent unit_type values or column names. Stored unit_types "
        "are SINGULAR.",
        "",
        "Plural-to-singular stemming is YOUR job. Examples:",
        "  user says 'transactions' → filter value 'transaction'",
        "  user says 'expenses'     → filter value 'expense'",
        "  user says 'line items'   → filter value 'line_item'",
        "  user says 'attendees'    → filter value 'attendee'",
        "  user says 'quotes'       → filter value 'quote'",
        "",
        "When exactly one unit_type below matches the user's stemmed "
        "term, pick it. Do NOT refuse with 'ambiguous' when the "
        "match is unique.",
        "",
        "unit_type → available jsonb keys (column names you may use "
        "in `fields.<key>::cast` aggregations + filters):",
    ]
    # Stable order; cap line count so the prompt doesn't explode on
    # corpora with hundreds of unit_types. 200 keeps us under typical
    # context windows even with 20-key rows.
    all_unit_types = sorted(schema.keys())
    truncated = all_unit_types[200:]
    for unit_type in all_unit_types[:200]:
        keys = schema[unit_type]
        lines.append(f"  {unit_type}: [{', '.join(keys)}]")
    if truncated:
        lines.append(
            f"  …and {len(truncated)} more unit_types not shown "
            f"(workspace has {len(all_unit_types)} total). If the user's "
            f"term doesn't match any unit_type above, refuse rather "
            f"than guess."
        )
    return "\n".join(lines)


async def generate_q_payload(
    query: str,
    *,
    llm: JsonLLMClient | None,
    schema_hints: dict[str, list[str]] | None = None,
    proposed_fields_hints: dict[str, list[str]] | None = None,
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

    hint_block = _format_schema_hints(schema_hints or {})
    pf_block = _format_proposed_fields_hints(proposed_fields_hints or {})
    combined_hints = "\n\n".join(b for b in (hint_block, pf_block) if b)
    user_msg = (
        f"{combined_hints}\n\nUser question: {query}" if combined_hints
        else f"User question: {query}"
    )
    try:
        raw_text = await llm.generate_json(
            user=user_msg,
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
