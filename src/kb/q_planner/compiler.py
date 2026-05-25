"""B4b — Q-mode compiler (Design 1 layers 5 + 6: parameter-only,
no raw-SQL escape).

Takes a `ValidatedQPlan` and emits `(sql, params)` where:
  - sql is a parameterized SELECT string with %s placeholders
  - params is a list of primitive values, one per placeholder

The compiler is the only place that emits SQL strings. Every identifier
written into the SQL is taken from the validator (which checked the
catalog). Every value goes through a placeholder — there is no path that
writes a user value into the SQL string.

Workspace scoping is enforced here: an extra `workspace_id = %s` filter
is appended to every plan, with workspace_id as the FIRST positional
parameter.
"""

from __future__ import annotations

from kb.q_planner.grammar import Aggregation, Filter, QPlan
from kb.q_planner.validator import ValidatedQPlan


# Operator → SQL fragment template (with %s placeholder positions).
_OP_TEMPLATES: dict[str, str] = {
    "eq":           "{col} = %s",
    "ne":           "{col} <> %s",
    "lt":           "{col} < %s",
    "le":           "{col} <= %s",
    "gt":           "{col} > %s",
    "ge":           "{col} >= %s",
    "like":         "{col} LIKE %s",
    "between":      "{col} BETWEEN %s AND %s",
    "is_null":      "{col} IS NULL",
    "is_not_null":  "{col} IS NOT NULL",
    # in / not_in are handled specially (variable placeholder count).
}


def _quote_ident(s: str) -> str:
    """Defensive identifier quoting. The grammar already enforces strict
    identifier syntax (no special chars), but we double-quote for belt
    + braces — any future grammar relaxation would still be safe."""
    # Refuse anything with a double quote — the grammar already forbids it.
    if '"' in s:
        raise ValueError(f"identifier {s!r} contains double-quote")
    return f'"{s}"'


def _filter_clause(table: str, f: Filter) -> tuple[str, list]:
    """Return (sql_fragment, params) for a single filter."""
    col_sql = f"{_quote_ident(table)}.{_quote_ident(f.field)}"

    if f.op in ("is_null", "is_not_null"):
        return (_OP_TEMPLATES[f.op].format(col=col_sql), [])

    if f.op == "between":
        # Validator already enforces value is [low, high].
        low, high = f.value
        return (
            _OP_TEMPLATES["between"].format(col=col_sql),
            [low, high],
        )

    if f.op in ("in", "not_in"):
        # Validator enforces non-empty list of primitives, len ≤ 100.
        placeholders = ", ".join(["%s"] * len(f.value))
        kw = "IN" if f.op == "in" else "NOT IN"
        return (
            f"{col_sql} {kw} ({placeholders})",
            list(f.value),
        )

    template = _OP_TEMPLATES.get(f.op)
    if template is None:
        # The grammar should have caught this — defense in depth.
        raise ValueError(f"unknown operator {f.op!r} reached compiler")
    return (template.format(col=col_sql), [f.value])


def _agg_projection(table: str, a: Aggregation) -> str:
    """SQL fragment for one aggregation in the SELECT list."""
    op_to_sql = {
        "SUM": "SUM",
        "COUNT": "COUNT",
        "COUNT_DISTINCT": "COUNT(DISTINCT %COL%)",
        "AVG": "AVG",
        "MIN": "MIN",
        "MAX": "MAX",
    }
    sql_op = op_to_sql.get(a.op)
    if sql_op is None:
        raise ValueError(f"unknown aggregation {a.op!r} reached compiler")

    if a.field == "*":
        # COUNT(*) only.
        return f"COUNT(*) AS {_quote_ident(a.alias)}"

    col_sql = f"{_quote_ident(table)}.{_quote_ident(a.field)}"
    if a.op == "COUNT_DISTINCT":
        body = f"COUNT(DISTINCT {col_sql})"
    else:
        body = f"{sql_op}({col_sql})"
    return f"{body} AS {_quote_ident(a.alias)}"


def compile_plan(
    validated: ValidatedQPlan,
    *,
    workspace_id: str,
    row_cap: int,
) -> tuple[str, list]:
    """Compile a validated Q plan into a single parameterized SQL string +
    its bound parameter list.

    `workspace_id` becomes the FIRST positional parameter (filter on the
    base table's workspace_id). `row_cap` clamps the user-requested limit
    so a malicious / runaway plan can't exhaust memory."""
    plan = validated.plan
    table = plan.from_table
    table_sql = _quote_ident(table)

    # --- SELECT list ---------------------------------------------------
    select_parts: list[str] = []
    if plan.group_by:
        for col in plan.group_by:
            select_parts.append(
                f"{table_sql}.{_quote_ident(col)} AS {_quote_ident(col)}"
            )
    for agg in plan.aggregations:
        select_parts.append(_agg_projection(table, agg))

    if not select_parts:
        # validator.validate guarantees we have either aggregations or
        # group_by; this is just a safety net.
        raise ValueError("compile_plan: no SELECT projections produced")

    select_clause = ", ".join(select_parts)

    # --- WHERE ---------------------------------------------------------
    # Workspace scoping is ALWAYS the first predicate. Layer 5: the
    # workspace_id value comes from a server-derived string, never user
    # input.
    where_parts: list[str] = [
        f"{table_sql}.\"workspace_id\" = %s::uuid",
    ]
    params: list = [workspace_id]

    for f in plan.filters:
        fragment, fparams = _filter_clause(table, f)
        where_parts.append(fragment)
        params.extend(fparams)

    where_clause = " AND ".join(where_parts)

    # --- GROUP BY ------------------------------------------------------
    group_clause = ""
    if plan.group_by:
        group_clause = " GROUP BY " + ", ".join(
            f"{table_sql}.{_quote_ident(g)}" for g in plan.group_by
        )

    # --- ORDER BY ------------------------------------------------------
    order_clause = ""
    if plan.order_by:
        alias_set = {a.alias for a in plan.aggregations}
        parts: list[str] = []
        for (col, direction) in plan.order_by:
            if col in alias_set:
                # Reference the aggregation alias directly.
                ref = _quote_ident(col)
            else:
                ref = f"{table_sql}.{_quote_ident(col)}"
            dir_sql = "DESC" if direction == "desc" else "ASC"
            parts.append(f"{ref} {dir_sql}")
        order_clause = " ORDER BY " + ", ".join(parts)

    # --- LIMIT (layer 9: row cap, clamped) -----------------------------
    effective_limit = max(1, min(int(plan.limit), int(row_cap)))
    limit_clause = f" LIMIT {effective_limit}"

    sql = (
        f"SELECT {select_clause} FROM {table_sql} "
        f"WHERE {where_clause}{group_clause}{order_clause}{limit_clause}"
    )
    return sql, params
