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

import re

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
    if f.jsonb_path is not None:
        jsonb_col, jsonb_key, cast = f.jsonb_path
        col_sql = _jsonb_extract_sql(table, jsonb_col, jsonb_key, cast)
    else:
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


# JSONB cast types we emit verbatim into the compiled SQL. Validator
# already enforces this set — defense in depth in the compiler keeps
# the SQL string-build site safe even if the validator gets bypassed.
_SAFE_JSONB_CASTS: frozenset[str] = frozenset({
    "numeric", "integer", "bigint", "real",
    "text", "date", "timestamptz",
})

# Numeric-family casts get a guarded emission (see `_jsonb_extract_sql`):
# a raw `(...)::numeric` ABORTS the whole query when one row holds a dirty
# value, so we validate-then-cast and yield NULL otherwise. All four widen
# to numeric — SUM/AVG/MIN/MAX are identical on numeric and it sidesteps
# `'1000.5'::integer` errors.
_NUMERIC_JSONB_CASTS: frozenset[str] = frozenset({
    "numeric", "integer", "bigint", "real",
})


def _jsonb_extract_sql(table: str, col: str, key: str, cast: str) -> str:
    """Emit one of:
      - `(<table>."<col>"->>'<key>')::<cast>` when key is non-empty
        (jsonb extraction + cast)
      - `(<table>."<col>")::<cast>` when key is "" (plain column cast,
        used for text-typed scalar columns like proposed_fields.value_text)

    All parts are safely quoted / cast-whitelisted. The key (when
    present) is single-quoted with internal single quotes doubled —
    defensive, even though the grammar's identifier regex already
    rejects quotes."""
    if cast not in _SAFE_JSONB_CASTS:
        raise ValueError(f"jsonb cast {cast!r} not whitelisted")
    if not key:
        # Plain column cast: `(t."value_text")`.
        extract = f"({_quote_ident(table)}.{_quote_ident(col)})"
    else:
        safe_key = key.replace("'", "''")
        extract = (
            f"({_quote_ident(table)}.{_quote_ident(col)}->>'{safe_key}')"
        )
    if cast in _NUMERIC_JSONB_CASTS:
        # SAFE numeric cast. A raw `(...)::numeric` raises and ABORTS the
        # whole aggregation the moment any row carries a non-numeric string
        # ('USD 2.2M', 'n/a', '-', '') — common once a concept spans
        # heterogeneous unit_types. So NULL-skip anything that isn't a clean
        # number instead of killing the query.
        #
        # Comma = THOUSANDS separator — correct for the formats this corpus
        # actually uses: Indian '4,82,40,000'→48240000 and US
        # '1,000.50'→1000.50. A decimal-comma locale ('3,14') is AMBIGUOUS vs
        # a truncated thousands group, and blind-stripping it would FABRICATE
        # 314 (confident-garbage — the worst failure mode for a grounded
        # answer). So we REJECT the decimal-comma signature — a comma
        # followed by only 1-2 trailing digits — and NULL-skip it rather than
        # guess; Indian/US numbers always end in a 3-digit group or a decimal,
        # so they pass. True locale normalization belongs at ingestion
        # (value_numeric / normalize_value), not in the query cast. POSIX
        # classes keep the regex backslash-free across string-literal settings.
        cleaned = f"replace({extract}, ',', '')"
        return (
            f"(CASE WHEN {extract} !~ ',[0-9]{{1,2}}[[:space:]]*$' "
            f"AND {cleaned} ~ "
            f"'^[[:space:]]*-?[0-9]+([.][0-9]+)?[[:space:]]*$' "
            f"THEN {cleaned}::numeric END)"
        )
    return f"{extract}::{cast}"


# Tables that carry a `file_id` column — the ones a supersedes-lineage dedup
# (§6.11 grain dedup) can exclude older versions on.
_TABLES_WITH_FILE_ID: frozenset[str] = frozenset({
    "extracted_entities", "proposed_fields",
})

# Row-level filter operators (date windows / value thresholds carried by the
# ResolvedPredicate, §6.11) — symbol-mapped, never interpolated as text.
_ROWFILTER_OP_SYM: dict[str, str] = {
    "lt": "<", "le": "<=", "gt": ">", "ge": ">=", "eq": "=", "ne": "<>",
}
_ROWFILTER_COL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def _guarded_jsonb_date_extract(table: str, col: str, key: str) -> str:
    """`(t."fields"->>'key')::date`, but ISO-guarded so a dirty date string
    yields NULL (row excluded) instead of ABORTING the whole aggregate — the
    same fail-safe stance as the numeric cast in `_jsonb_extract_sql`."""
    safe_key = key.replace("'", "''")
    extract = f"({_quote_ident(table)}.{_quote_ident(col)}->>'{safe_key}')"
    return (
        f"(CASE WHEN {extract} ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}' "
        f"THEN {extract}::date END)"
    )


def compile_row_filters(
    table: str,
    row_filters: list[dict] | tuple[dict, ...] | None,
    *,
    unit_types: list[str] | None = None,
    live_catalog: object | None = None,
) -> list[tuple[str, list]]:
    """Compile `ResolvedPredicate.row_filters` (date windows / value thresholds)
    into ROW-LEVEL WHERE fragments on `extracted_entities.fields` (§6.11), so a
    'last quarter' window applies per transaction row, not just to doc-pick.
    Reuses the guarded jsonb casts (a dirty value is NULL-skipped, never aborts).

    Each `rf` is a `RowFilter.to_dict()`: {column, op, value, grain, kind}.

    **Fail-open** — any filter that doesn't map cleanly (unsupported table,
    column not a real key on the queried rows, bad op/value) is SKIPPED: a row
    filter may narrow the aggregate, never silently zero it out or error it."""
    out: list[tuple[str, list]] = []
    if not row_filters or table != "extracted_entities":
        return out
    for rf in row_filters:
        if not isinstance(rf, dict):
            continue
        col = rf.get("column")
        op = rf.get("op") or "eq"
        val = rf.get("value")
        kind = rf.get("kind") or "value"
        if not isinstance(col, str) or not _ROWFILTER_COL_RE.match(col):
            continue
        # Skip a filter whose column isn't a real key on these rows (a mis-named
        # date field would otherwise NULL-exclude every row → empty aggregate).
        if live_catalog is not None and hasattr(live_catalog, "has_key"):
            try:
                if not live_catalog.has_key(col, unit_types=unit_types):
                    continue
            except Exception:  # noqa: BLE001
                pass
        if kind == "date":
            col_sql = _guarded_jsonb_date_extract(table, "fields", col)
        else:
            col_sql = _jsonb_extract_sql(table, "fields", col, "numeric")
        if op == "between":
            if not (isinstance(val, (list, tuple)) and len(val) == 2):
                continue
            out.append((f"{col_sql} BETWEEN %s AND %s", [val[0], val[1]]))
        elif op in _ROWFILTER_OP_SYM:
            out.append((f"{col_sql} {_ROWFILTER_OP_SYM[op]} %s", [val]))
        # unknown op → skip (fail-open)
    return out


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

    # JSONB path: project `(t."fields"->>'debit')::numeric`.
    if a.jsonb_path is not None:
        jsonb_col, jsonb_key, cast = a.jsonb_path
        col_sql = _jsonb_extract_sql(table, jsonb_col, jsonb_key, cast)
    else:
        col_sql = f"{_quote_ident(table)}.{_quote_ident(a.field)}"

    if a.op == "COUNT_DISTINCT":
        body = f"COUNT(DISTINCT {col_sql})"
    else:
        body = f"{sql_op}({col_sql})"
    return f"{body} AS {_quote_ident(a.alias)}"


def _group_by_expr(table: str, g) -> str:
    """SQL fragment for ONE entry in the GROUP BY clause. Plain
    identifiers render as `t."col"`; jsonb-path forms render as
    `(t."fields"->>'key')::cast` via `_jsonb_extract_sql`."""
    if g.jsonb_path is not None:
        jsonb_col, jsonb_key, cast = g.jsonb_path
        return _jsonb_extract_sql(table, jsonb_col, jsonb_key, cast)
    return f"{_quote_ident(table)}.{_quote_ident(g.field)}"


def _group_by_projection(table: str, g) -> str:
    """SELECT-list projection for ONE group_by entry. We add an AS
    alias so the result row has a stable column name — for plain
    identifiers the alias is the column name; for jsonb paths we
    derive it from the jsonb key (e.g. `fields.category::text` →
    alias `category`)."""
    expr = _group_by_expr(table, g)
    if g.jsonb_path is not None:
        _jsonb_col, jsonb_key, _cast = g.jsonb_path
        alias = jsonb_key
    else:
        alias = g.field
    return f"{expr} AS {_quote_ident(alias)}"


def compile_plan(
    validated: ValidatedQPlan,
    *,
    workspace_id: str,
    row_cap: int,
    row_filters: list[dict] | tuple[dict, ...] | None = None,
    live_catalog: object | None = None,
    exclude_file_ids: list[str] | tuple[str, ...] | None = None,
) -> tuple[str, list]:
    """Compile a validated Q plan into a single parameterized SQL string +
    its bound parameter list.

    `workspace_id` becomes the FIRST positional parameter (filter on the
    base table's workspace_id). `row_cap` clamps the user-requested limit
    so a malicious / runaway plan can't exhaust memory.

    `row_filters` (T3 §6.11) are the `ResolvedPredicate.row_filters` (as dicts);
    when present they compile into ROW-LEVEL WHERE fragments (date windows /
    value thresholds applied per row, not just to doc-pick). Fail-open.

    `exclude_file_ids` (T3 §6.11 grain dedup) are superseded doc versions to
    drop, so a loan/account that appears in both an original and its amendment
    isn't counted twice. Applied only on tables that carry `file_id`."""
    plan = validated.plan
    table = plan.from_table
    table_sql = _quote_ident(table)

    # --- SELECT list ---------------------------------------------------
    select_parts: list[str] = []
    if plan.group_by:
        for g in plan.group_by:
            select_parts.append(_group_by_projection(table, g))
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

    # Auto-inject soft-delete filters per table. Without these, Q-mode
    # silently counts logically-deleted rows (e.g. canonical_entities
    # losers from the dedup pipeline → wrong-by-double-counting).
    _SOFT_DELETE_PREDICATES = {
        "canonical_entities": '"merged_into" IS NULL',
    }
    if table in _SOFT_DELETE_PREDICATES:
        where_parts.append(f"{table_sql}.{_SOFT_DELETE_PREDICATES[table]}")

    for f in plan.filters:
        fragment, fparams = _filter_clause(table, f)
        where_parts.append(fragment)
        params.extend(fparams)

    # T3 §6.11 — row-level date/value filters from the ResolvedPredicate,
    # appended AFTER the plan filters so positional params stay in order.
    if row_filters:
        unit_types = [
            str(v)
            for f in plan.filters
            if f.field == "unit_type" and f.op in ("eq", "in")
            for v in (f.value if isinstance(f.value, list) else [f.value])
            if v is not None
        ] or None
        for frag, fparams in compile_row_filters(
            table, row_filters, unit_types=unit_types, live_catalog=live_catalog,
        ):
            where_parts.append(frag)
            params.extend(fparams)

    # T3 §6.11 grain dedup — drop superseded doc versions so an entity present in
    # both an original and its amendment isn't double-counted. Signal-gated (only
    # when there ARE superseded ids) → no-op for the common single-version case.
    if exclude_file_ids and table in _TABLES_WITH_FILE_ID:
        where_parts.append(f'{table_sql}."file_id" <> ALL(%s::uuid[])')
        params.append(list(exclude_file_ids))

    where_clause = " AND ".join(where_parts)

    # --- GROUP BY ------------------------------------------------------
    group_clause = ""
    if plan.group_by:
        group_clause = " GROUP BY " + ", ".join(
            _group_by_expr(table, g) for g in plan.group_by
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
