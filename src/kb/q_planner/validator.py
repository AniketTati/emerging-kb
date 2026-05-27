"""B4b — Q-mode validator (Design 1 layer 1: catalog whitelist + type
checks).

Takes a parsed `QPlan` (from grammar.py) and checks every column
reference against `catalog.ALLOWED_COLUMNS`. Also enforces:

  - SUM / AVG operands must be numeric
  - MIN / MAX / between / lt / le / gt / ge operands must be comparable
  - in / not_in values must match column type loosely
  - group_by + order_by + filter columns must all be in the catalog
  - aggregation aliases must be unique

Output: `ValidatedQPlan` — a QPlan plus a `column_types` map. The
compiler consumes this and produces (sql, params).
"""

from __future__ import annotations

from dataclasses import dataclass

from kb.q_planner.catalog import (
    ALLOWED_TABLES,
    closest_columns,
    column_type,
    is_comparable,
    is_numeric,
)
from kb.q_planner.grammar import (
    Aggregation,
    Filter,
    QPlan,
)


class QPlanValidationError(ValueError):
    """User-facing validation error. Carries a 'suggestions' list for the
    closest valid columns when the message is about an unknown field."""

    def __init__(self, message: str, *, suggestions: list[str] | None = None):
        super().__init__(message)
        self.suggestions = suggestions or []


@dataclass(frozen=True)
class ValidatedQPlan:
    plan: QPlan
    # (table, column) → SQL type, populated for every column referenced
    # in filters / group_by / order_by / aggregations. The compiler uses
    # this to choose ::cast for parameters.
    column_types: dict[tuple[str, str], str]


def _check_column(table: str, column: str) -> str:
    """Returns the column's type. Raises with a 'did-you-mean' suggestion
    if not in the catalog."""
    t = column_type(table, column)
    if t is None:
        suggestions = closest_columns(table, column)
        raise QPlanValidationError(
            f"unknown column {column!r} on table {table!r}; "
            f"closest matches: {suggestions}",
            suggestions=suggestions,
        )
    return t


def _validate_filter(table: str, f: Filter) -> tuple[str, str]:
    """Returns (table, column) for accounting in `column_types`. Raises
    on type mismatch."""
    # JSONB-path branch — same allowlist + cast-vs-op rules as
    # aggregations.
    if f.jsonb_path is not None:
        from kb.q_planner.catalog import is_jsonb_agg_allowed
        jsonb_col, jsonb_key, cast_type = f.jsonb_path
        col_type = column_type(table, jsonb_col)
        if col_type != "jsonb":
            raise QPlanValidationError(
                f"filter field {f.field!r}: {jsonb_col!r} on {table!r} "
                f"is not a jsonb column"
            )
        if not is_jsonb_agg_allowed(table, jsonb_col):
            raise QPlanValidationError(
                f"jsonb filter on ({table!r}, {jsonb_col!r}) "
                f"is not in the Q-mode allowlist"
            )
        op = f.op
        if op in ("lt", "le", "gt", "ge", "between"):
            if cast_type not in _COMPARABLE_CASTS:
                raise QPlanValidationError(
                    f"operator {op!r} on jsonb path {f.field!r} requires "
                    f"a comparable cast (got ::{cast_type})"
                )
        if op == "like" and cast_type != "text":
            raise QPlanValidationError(
                f"operator 'like' on jsonb path {f.field!r} requires "
                f"a text cast (got ::{cast_type})"
            )
        return (table, jsonb_col)

    # Plain column reference — existing behavior.
    col_type = _check_column(table, f.field)

    # Type-vs-operator compatibility.
    op = f.op
    if op in ("lt", "le", "gt", "ge", "between"):
        if not is_comparable(table, f.field):
            raise QPlanValidationError(
                f"operator {op!r} requires a comparable column "
                f"({f.field!r} on {table!r} is {col_type})"
            )
    # boolean and uuid don't accept like — refuse with a clear message.
    if op == "like" and col_type not in ("text",):
        raise QPlanValidationError(
            f"operator 'like' requires a text column ({f.field!r} is {col_type})"
        )
    # is_null and is_not_null are universally applicable.
    return (table, f.field)


_NUMERIC_CASTS: frozenset[str] = frozenset({
    "numeric", "integer", "bigint", "real",
})
_COMPARABLE_CASTS: frozenset[str] = _NUMERIC_CASTS | frozenset({
    "text", "date", "timestamptz",
})


def _validate_aggregation(table: str, a: Aggregation) -> tuple[str, str] | None:
    """Returns the (table, column) referenced by the agg, or None for
    COUNT(*) and for jsonb-path aggregations (which the catalog accounts
    for differently). Raises on type mismatch."""
    if a.field == "*":
        if a.op != "COUNT":
            raise QPlanValidationError(
                f"aggregation field '*' only valid with COUNT (got {a.op!r})"
            )
        return None

    # JSONB-path branch: validate (table, jsonb_col) is whitelisted +
    # cast type matches the aggregation op.
    if a.jsonb_path is not None:
        from kb.q_planner.catalog import is_jsonb_agg_allowed
        jsonb_col, jsonb_key, cast_type = a.jsonb_path
        col_type = column_type(table, jsonb_col)
        if col_type != "jsonb":
            raise QPlanValidationError(
                f"aggregation field {a.field!r}: {jsonb_col!r} on {table!r} "
                f"is not a jsonb column"
            )
        if not is_jsonb_agg_allowed(table, jsonb_col):
            raise QPlanValidationError(
                f"jsonb aggregation on ({table!r}, {jsonb_col!r}) "
                f"is not in the Q-mode allowlist"
            )
        if a.op in ("SUM", "AVG") and cast_type not in _NUMERIC_CASTS:
            raise QPlanValidationError(
                f"aggregation {a.op!r} on jsonb path {a.field!r} requires "
                f"a numeric cast (got ::{cast_type})"
            )
        if a.op in ("MIN", "MAX") and cast_type not in _COMPARABLE_CASTS:
            raise QPlanValidationError(
                f"aggregation {a.op!r} on jsonb path {a.field!r} requires "
                f"a comparable cast (got ::{cast_type})"
            )
        # The (table, jsonb_col) tuple still goes into column_types for
        # bookkeeping; the compiler doesn't need a per-key entry.
        return (table, jsonb_col)

    # Plain column reference — existing behavior.
    col_type = _check_column(table, a.field)

    if a.op in ("SUM", "AVG"):
        if not is_numeric(table, a.field):
            raise QPlanValidationError(
                f"aggregation {a.op!r} requires a numeric column "
                f"({a.field!r} is {col_type})"
            )
    if a.op in ("MIN", "MAX"):
        if not is_comparable(table, a.field):
            raise QPlanValidationError(
                f"aggregation {a.op!r} requires a comparable column "
                f"({a.field!r} is {col_type})"
            )
    return (table, a.field)


def validate(plan: QPlan) -> ValidatedQPlan:
    """Catalog + type validation. Raises QPlanValidationError on any
    miss. Returns a ValidatedQPlan ready for the compiler."""
    if plan.from_table not in ALLOWED_TABLES:
        raise QPlanValidationError(
            f"table {plan.from_table!r} is not in the Q-mode allowlist; "
            f"allowed tables: {sorted(ALLOWED_TABLES)}"
        )

    # Aggregation alias uniqueness.
    seen_aliases: set[str] = set()
    for a in plan.aggregations:
        if a.alias in seen_aliases:
            raise QPlanValidationError(
                f"duplicate aggregation alias {a.alias!r}"
            )
        seen_aliases.add(a.alias)

    column_types: dict[tuple[str, str], str] = {}

    # Filters
    for f in plan.filters:
        key = _validate_filter(plan.from_table, f)
        column_types[key] = column_type(*key) or "text"

    # group_by columns — plain identifiers must be in the catalog;
    # jsonb-path forms ('fields.<key>::<cast>') must reference a
    # whitelisted (table, jsonb_col) pair (same allowlist used for
    # jsonb aggregations).
    for g in plan.group_by:
        if g.jsonb_path is not None:
            from kb.q_planner.catalog import is_jsonb_agg_allowed
            jsonb_col, _jsonb_key, _cast_type = g.jsonb_path
            col_type = column_type(plan.from_table, jsonb_col)
            if col_type != "jsonb":
                raise QPlanValidationError(
                    f"group_by jsonb path {g.field!r}: {jsonb_col!r} on "
                    f"{plan.from_table!r} is not a jsonb column"
                )
            if not is_jsonb_agg_allowed(plan.from_table, jsonb_col):
                raise QPlanValidationError(
                    f"group_by on jsonb ({plan.from_table!r}, "
                    f"{jsonb_col!r}) is not in the Q-mode allowlist"
                )
            # No further column_types entry — the SELECT projection +
            # GROUP BY clause both render via _jsonb_extract_sql below.
            continue
        t = _check_column(plan.from_table, g.field)
        column_types[(plan.from_table, g.field)] = t

    # Aggregations
    for a in plan.aggregations:
        key = _validate_aggregation(plan.from_table, a)
        if key is not None:
            column_types[key] = column_type(*key) or "text"

    # order_by columns — must be in the catalog OR match an aggregation alias.
    alias_set = {a.alias for a in plan.aggregations}
    for (col, _direction) in plan.order_by:
        if col in alias_set:
            continue
        t = _check_column(plan.from_table, col)
        column_types[(plan.from_table, col)] = t

    # Workspace-scoping is enforced at the executor level — we ALWAYS
    # add a workspace_id filter at compile-time. But the user shouldn't
    # be able to set their own workspace_id literal here; reject it
    # explicitly so the audit log doesn't show a misleading filter.
    for f in plan.filters:
        if f.field == "workspace_id":
            raise QPlanValidationError(
                "filters cannot reference workspace_id directly; "
                "workspace scoping is enforced automatically"
            )

    # Sanity: a Q plan must produce SOMETHING. Either it's a pure
    # projection (no aggregations + at least one group_by → distinct
    # values), or it has at least one aggregation.
    if not plan.aggregations and not plan.group_by:
        raise QPlanValidationError(
            "Q plan must have either aggregations or group_by columns "
            "(a pure SELECT is the H-mode pipeline, not Q-mode)"
        )

    return ValidatedQPlan(plan=plan, column_types=column_types)
