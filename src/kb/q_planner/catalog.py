"""B4b — Q-mode catalog (Design 1 layer 1: field-name whitelist).

Pure data: defines which (table, column) tuples the validator will accept,
and what scalar SQL type each column has. Anything not in the catalog is
refused with "field doesn't exist".

The catalog is hand-curated for Wave A — kept narrow to lock down the
attack surface. Each table here is also GRANT-ed to kb_app_q in
migrations/sql/0027_q_mode.sql so a future per-mode connection pool can
enforce read-only at the role level (Design 1 layer 7).

When adding a new (table, column), make sure to:

  1. Add it to ALLOWED_COLUMNS below with its scalar type
  2. Add the table to migrations/sql/0027_q_mode.sql kb_app_q GRANT list
  3. Add a test in tests/test_b4b_unit.py that the validator accepts it
"""

from __future__ import annotations

from typing import Literal


# Wave A scope — single-table queries only. Joins + set ops are spec'd
# but deferred to keep B4b's attack surface auditable. The compiler
# refuses any plan with joins / set ops.
#
# `atomic_units` was removed by the nested-entities refactor — every
# structural row (transaction, line_item, clause, …) now lives in
# `extracted_entities` as a typed sub_entity with `unit_type` +
# `rarity_score` + `parent_entity_id` populated. The Q-mode LLM is
# instructed to filter `extracted_entities.unit_type` instead of
# pivoting through a separate table.
ALLOWED_TABLES: frozenset[str] = frozenset({
    "files",
    "extracted_entities",
    "canonical_entities",
    "proposed_fields",
    "relationships",
    "fact_conflicts",
    "doc_chains",
    "doc_chain_members",
})


# Scalar SQL types we accept in filters. Mapped to the operator types
# that make sense for each (e.g. SUM/AVG only on numeric; between on
# numeric/timestamp).
ColumnType = Literal[
    "uuid", "text", "integer", "bigint", "numeric", "real",
    "boolean", "timestamptz", "date", "jsonb",
]


# (table, column) → ColumnType. Validator looks up via this dict.
ALLOWED_COLUMNS: dict[tuple[str, str], ColumnType] = {
    # ----- files -----
    ("files", "id"):                       "uuid",
    ("files", "workspace_id"):             "uuid",
    ("files", "name"):                     "text",
    ("files", "mime_type"):                "text",
    ("files", "inferred_doc_type"):        "text",
    ("files", "doc_status"):               "text",
    ("files", "source_authority"):         "numeric",
    ("files", "size_bytes"):               "bigint",
    ("files", "lifecycle_state"):          "text",
    ("files", "created_at"):               "timestamptz",
    ("files", "updated_at"):               "timestamptz",
    # ----- extracted_entities -----
    # Every typed instance lives here — both parent (doc_root) and
    # children (sub_entity). Filter by `unit_type` to scope to a
    # specific child collection (e.g. transaction / line_item / clause)
    # or by `parent_entity_id IS NULL` to get the parent only.
    ("extracted_entities", "id"):                "uuid",
    ("extracted_entities", "workspace_id"):      "uuid",
    ("extracted_entities", "schema_entity_id"):  "uuid",
    ("extracted_entities", "file_id"):           "uuid",
    ("extracted_entities", "parent_entity_id"):  "uuid",
    ("extracted_entities", "fields"):            "jsonb",
    ("extracted_entities", "rarity_score"):      "real",
    ("extracted_entities", "unit_type"):         "text",
    ("extracted_entities", "created_at"):        "timestamptz",
    # ----- canonical_entities -----
    # Cross-doc deduplicated entity layer (one row per unique person /
    # org / location / etc., aggregated across all docs that mention
    # them). Use for cardinality questions that span the corpus:
    #
    #   "how many distinct sub-contractors are on the project"     →
    #     SELECT COUNT(*) FROM canonical_entities WHERE entity_type='ORG'
    #
    #   "list all people involved across the contracts"            →
    #     SELECT canonical_name FROM canonical_entities WHERE entity_type='PERSON'
    #
    # NOT a replacement for extracted_entities — extracted_entities
    # has the per-doc structural row (one safety_incident, one
    # transaction, …), canonical_entities has the dedup'd entity.
    ("canonical_entities", "id"):              "uuid",
    ("canonical_entities", "workspace_id"):    "uuid",
    ("canonical_entities", "canonical_name"):  "text",
    ("canonical_entities", "entity_type"):     "text",
    ("canonical_entities", "mention_count"):   "integer",
    ("canonical_entities", "created_at"):      "timestamptz",
    ("canonical_entities", "updated_at"):      "timestamptz",
    # ----- proposed_fields -----
    # Top-level scalar fields extracted per doc (KV+Tables phase).
    # These are the doc-summary values: contract_value, total_amount,
    # effective_date, etc. — one row per (file, field_name). Use for
    # cross-doc summary aggregations where the answer lives in a
    # named scalar field (e.g. "total cumulative change-order value"
    # = SUM of `total_cost_premium` across all change_order docs).
    # `value_text` is always TEXT — cast as needed (::numeric, ::date).
    ("proposed_fields", "id"):                 "uuid",
    ("proposed_fields", "file_id"):            "uuid",
    ("proposed_fields", "workspace_id"):       "uuid",
    ("proposed_fields", "inferred_doc_type"):  "text",
    ("proposed_fields", "field_name"):         "text",
    ("proposed_fields", "value_text"):         "text",
    ("proposed_fields", "value_type"):         "text",
    # Bug D Tier-1 #3 — normalized numeric form + ISO currency tag.
    # Populated by kb.extraction.value_normalize during ingest. NULL
    # for non-numeric fields. Aggregations should SUM/AVG over
    # value_numeric (a real `numeric` column) instead of casting
    # value_text — the cast silently NULLs rows formatted with
    # currency symbols, magnitude words ("22 lakh"), accounting
    # negatives, or Indian comma grouping.
    ("proposed_fields", "value_numeric"):      "numeric",
    ("proposed_fields", "value_currency"):     "text",
    ("proposed_fields", "is_pii"):             "boolean",
    ("proposed_fields", "model_id"):           "text",
    ("proposed_fields", "created_at"):         "timestamptz",
    # ----- relationships -----
    ("relationships", "id"):                  "uuid",
    ("relationships", "workspace_id"):        "uuid",
    ("relationships", "subject_entity_id"):   "uuid",
    ("relationships", "object_entity_id"):    "uuid",
    ("relationships", "predicate"):           "text",
    ("relationships", "confidence"):          "numeric",
    # ----- fact_conflicts -----
    ("fact_conflicts", "id"):              "uuid",
    ("fact_conflicts", "workspace_id"):    "uuid",
    ("fact_conflicts", "entity_id"):       "uuid",
    ("fact_conflicts", "predicate"):       "text",
    ("fact_conflicts", "resolution"):      "text",
    ("fact_conflicts", "resolved_value"):  "text",
    ("fact_conflicts", "observed_at"):     "timestamptz",
    ("fact_conflicts", "resolved_at"):     "timestamptz",
    # ----- doc_chains -----
    ("doc_chains", "id"):                       "uuid",
    ("doc_chains", "workspace_id"):             "uuid",
    ("doc_chains", "type"):                     "text",
    ("doc_chains", "title"):                    "text",
    ("doc_chains", "current_version_id"):       "uuid",
    ("doc_chains", "detection_confidence"):     "numeric",
    ("doc_chains", "created_at"):               "timestamptz",
    # ----- doc_chain_members -----
    ("doc_chain_members", "chain_id"):       "uuid",
    ("doc_chain_members", "doc_id"):         "uuid",
    ("doc_chain_members", "workspace_id"):   "uuid",
    ("doc_chain_members", "version_index"):  "integer",
    ("doc_chain_members", "role"):           "text",
}


# Numeric types (eligible for SUM / AVG).
NUMERIC_TYPES: frozenset[str] = frozenset({"integer", "bigint", "numeric", "real"})
# Comparable types (eligible for MIN / MAX / between / lt / le / gt / ge).
COMPARABLE_TYPES: frozenset[str] = (
    NUMERIC_TYPES | frozenset({"timestamptz", "date", "text"})
)


# (table, jsonb_col) tuples where Q-mode is allowed to do key-based
# aggregation via `<col>.<key>::<cast>` syntax. Locked down to the
# specific jsonb columns that carry KV+Tables row data — adding a new
# entry here is an audited decision.
#
# For extracted_entities.fields specifically: this is where every typed
# row's column values live after the KV+Tables collapse (transactions,
# line_items, clauses, etc.). Filtering by `unit_type` first scopes the
# aggregation to a specific table; then `fields.<col>::<cast>` extracts
# the column the row needs to sum/avg/min/max.
JSONB_AGG_ALLOWED: frozenset[tuple[str, str]] = frozenset({
    ("extracted_entities", "fields"),
})


def is_jsonb_agg_allowed(table: str, jsonb_col: str) -> bool:
    """Whether Q-mode may aggregate over a jsonb key on this column."""
    return (table, jsonb_col) in JSONB_AGG_ALLOWED


def column_type(table: str, column: str) -> str | None:
    """Return the type for (table, column) or None if not allowed."""
    return ALLOWED_COLUMNS.get((table, column))


def is_numeric(table: str, column: str) -> bool:
    t = column_type(table, column)
    return t is not None and t in NUMERIC_TYPES


def is_comparable(table: str, column: str) -> bool:
    t = column_type(table, column)
    return t is not None and t in COMPARABLE_TYPES


def closest_columns(table: str, missing: str, *, k: int = 3) -> list[str]:
    """Suggest the k closest allowed columns for a table. Helps the
    validator's error message: "Did you mean ...?".

    Uses a cheap shared-prefix score — no extra dependencies, deterministic."""
    cols = sorted({c for (t, c) in ALLOWED_COLUMNS if t == table})
    if not cols:
        return []
    missing_lower = missing.lower()

    def _score(c: str) -> int:
        c_low = c.lower()
        if c_low == missing_lower:
            return 100
        if c_low.startswith(missing_lower) or missing_lower.startswith(c_low):
            return 50
        # Shared-prefix length.
        n = 0
        for a, b in zip(c_low, missing_lower):
            if a == b:
                n += 1
            else:
                break
        # Bonus for shared substrings of length ≥ 3.
        if any(
            missing_lower[i:i + 3] in c_low
            for i in range(len(missing_lower) - 2)
        ):
            n += 3
        return n

    return sorted(cols, key=lambda c: -_score(c))[:k]
