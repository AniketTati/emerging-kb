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
ALLOWED_TABLES: frozenset[str] = frozenset({
    "files",
    "extracted_entities",
    "atomic_units",
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
    ("extracted_entities", "id"):                "uuid",
    ("extracted_entities", "workspace_id"):      "uuid",
    ("extracted_entities", "schema_entity_id"):  "uuid",
    ("extracted_entities", "file_id"):           "uuid",
    ("extracted_entities", "fields"):            "jsonb",
    ("extracted_entities", "created_at"):        "timestamptz",
    # ----- atomic_units -----
    ("atomic_units", "id"):                 "uuid",
    ("atomic_units", "workspace_id"):       "uuid",
    ("atomic_units", "file_id"):            "uuid",
    ("atomic_units", "unit_type"):          "text",
    ("atomic_units", "parameters"):         "jsonb",
    ("atomic_units", "rarity_score"):       "numeric",
    ("atomic_units", "created_at"):         "timestamptz",
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
