"""B4b — Q-mode grammar (Design 1 layers 2-4: operator + aggregation +
set-op enums).

Parses an untyped plan dict (LLM output or HTTP request body) into a
typed `QPlan` dataclass. Everything not in the allowed enums → ValueError.

This is parse-time validation. The catalog whitelist (layer 1) runs in
`validator.py`. The compiler (`compiler.py`) consumes a `ValidatedQPlan`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal


# Layer 2 — Operator enum.
ALLOWED_OPERATORS: frozenset[str] = frozenset({
    "eq", "ne", "lt", "le", "gt", "ge",
    "in", "not_in", "between", "like", "is_null", "is_not_null",
})

# Layer 3 — Aggregation enum.
ALLOWED_AGGREGATIONS: frozenset[str] = frozenset({
    "SUM", "COUNT", "AVG", "MIN", "MAX", "COUNT_DISTINCT",
})

# Layer 4 — Set-op enum. Wave A doesn't compile these (single-table only),
# but the grammar accepts the value for forward-compat parsing.
ALLOWED_SET_OPS: frozenset[str] = frozenset({"intersect", "union", "except"})


# Identifier guard — column names + aliases must match this. Locks the
# attack surface tight: no commas, no quotes, no SQL keywords.
_IDENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,62}$")

# JSONB path guard — accepts `<col>.<key>::<cast>` where col, key are
# identifiers and cast is one of the allowed scalar casts. The compiler
# emits `(<table>."<col>"->>'<key>')::<cast>` for these. Locked down
# tight: only flat one-level key access, only safe cast types.
_JSONB_PATH_RE = re.compile(
    r"^([a-zA-Z_][a-zA-Z0-9_]{0,62})\.([a-zA-Z_][a-zA-Z0-9_]{0,62})"
    r"::([a-z]+)$"
)
# Column-level cast (no jsonb key): `value_text::numeric`. Used for
# text-typed scalar columns like proposed_fields.value_text where the
# value needs to be cast for SUM/AVG/MIN/MAX. Encoded as a (col, "",
# cast) triple to reuse the jsonb_path slot in Filter/Aggregation
# dataclasses; the compiler treats empty key as "no ->> extraction".
_COLUMN_CAST_RE = re.compile(
    r"^([a-zA-Z_][a-zA-Z0-9_]{0,62})::([a-z]+)$"
)
ALLOWED_JSONB_CASTS: frozenset[str] = frozenset({
    "numeric", "integer", "bigint", "real", "text", "date", "timestamptz",
})


def _is_valid_ident(s: str) -> bool:
    return bool(s) and bool(_IDENT_RE.match(s))


def _parse_jsonb_path(s: str) -> tuple[str, str, str] | None:
    """Parse `<col>.<key>::<cast>` → (col, key, cast) for jsonb extraction,
    OR `<col>::<cast>` → (col, "", cast) for column-level cast (no jsonb
    key extraction; used for text columns like proposed_fields.value_text
    where we just need a type cast). Returns None when the string isn't
    in either form. Raises on partial / unsafe forms so the user gets a
    clear error instead of a fallthrough."""
    if "." not in s and "::" not in s:
        return None  # plain identifier — caller handles
    # Try jsonb-path first (more specific).
    m = _JSONB_PATH_RE.match(s)
    if m is not None:
        col, key, cast = m.group(1), m.group(2), m.group(3)
        if cast not in ALLOWED_JSONB_CASTS:
            raise QPlanParseError(
                f"jsonb cast={cast!r} not allowed; expected one of "
                f"{sorted(ALLOWED_JSONB_CASTS)}"
            )
        return col, key, cast
    # Then plain column cast.
    m2 = _COLUMN_CAST_RE.match(s)
    if m2 is not None:
        col, cast = m2.group(1), m2.group(2)
        if cast not in ALLOWED_JSONB_CASTS:
            raise QPlanParseError(
                f"column cast={cast!r} not allowed; expected one of "
                f"{sorted(ALLOWED_JSONB_CASTS)}"
            )
        return col, "", cast
    raise QPlanParseError(
        f"field={s!r} is not a plain identifier, a valid jsonb path, "
        f"or a valid column cast; expected '<col>' OR '<col>.<key>::<cast>' "
        f"(e.g. 'fields.debit::numeric') OR '<col>::<cast>' "
        f"(e.g. 'value_text::numeric')"
    )


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Filter:
    field: str          # column name OR jsonb path (e.g. 'fields.date::date')
    op: str             # one of ALLOWED_OPERATORS
    value: Any = None   # primitive or list (for in/not_in/between)
    # When `field` uses jsonb-path syntax, the parsed (col, key, cast)
    # tuple. None for plain identifier filters.
    jsonb_path: tuple[str, str, str] | None = None


@dataclass(frozen=True)
class Aggregation:
    op: str              # one of ALLOWED_AGGREGATIONS
    field: str           # column name OR "*" for COUNT OR jsonb path
                         # (e.g. 'fields.debit::numeric')
    alias: str           # safe identifier for the result column
    # When `field` uses the jsonb-path form, the parsed (col, key, cast)
    # tuple. When `field` is a plain identifier or '*', this is None.
    # Set by the grammar parser so the validator/compiler don't re-parse.
    jsonb_path: tuple[str, str, str] | None = None


@dataclass(frozen=True)
class GroupByCol:
    """One entry in a `group_by` list. Mirrors Filter/Aggregation:
    `field` is the raw text from the LLM (plain identifier OR jsonb
    path like 'fields.category::text'); `jsonb_path` is the parsed
    (col, key, cast) tuple set by the grammar parser when the field
    used the jsonb-path syntax. None for plain identifier group_bys.
    """
    field: str
    jsonb_path: tuple[str, str, str] | None = None


@dataclass(frozen=True)
class QPlan:
    """Typed Q-plan. The validator widens this into ValidatedQPlan with
    a resolved table + column types attached."""
    from_table: str
    filters: tuple[Filter, ...] = field(default_factory=tuple)
    group_by: tuple[GroupByCol, ...] = field(default_factory=tuple)
    aggregations: tuple[Aggregation, ...] = field(default_factory=tuple)
    order_by: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    # User-requested limit; the executor clamps to DEFAULT_ROW_CAP.
    limit: int = 100


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


class QPlanParseError(ValueError):
    """Raised when the raw plan can't be parsed into a QPlan. The message
    is the user-facing reason."""


def _expect_str(d: dict, key: str, *, max_len: int = 64) -> str:
    val = d.get(key)
    if not isinstance(val, str) or not val.strip():
        raise QPlanParseError(f"{key!r} must be a non-empty string")
    if len(val) > max_len:
        raise QPlanParseError(f"{key!r} exceeds max length {max_len}")
    return val


def _parse_filter(raw: Any, *, idx: int) -> Filter:
    if not isinstance(raw, dict):
        raise QPlanParseError(f"filters[{idx}] must be an object")
    # Same field syntax as aggregations: plain identifier OR jsonb path.
    field_name = _expect_str(raw, "field", max_len=160)
    jsonb_path: tuple[str, str, str] | None = None
    if not _is_valid_ident(field_name):
        jsonb_path = _parse_jsonb_path(field_name)
        if jsonb_path is None:
            raise QPlanParseError(
                f"filters[{idx}].field={field_name!r} is not a valid "
                f"identifier or jsonb path"
            )
    op = _expect_str(raw, "op", max_len=16)
    if op not in ALLOWED_OPERATORS:
        raise QPlanParseError(
            f"filters[{idx}].op={op!r} not allowed; "
            f"expected one of {sorted(ALLOWED_OPERATORS)}"
        )
    value = raw.get("value")
    # Layer 5 enforcement starts here: values must be primitives or
    # short lists of primitives. We refuse nested dicts / arbitrary
    # objects — those have no role in a Q plan.
    if op in ("is_null", "is_not_null"):
        if value is not None:
            raise QPlanParseError(
                f"filters[{idx}].value must be omitted for op={op!r}"
            )
    elif op == "between":
        if not (isinstance(value, list) and len(value) == 2
                and all(_is_primitive(v) for v in value)):
            raise QPlanParseError(
                f"filters[{idx}].value must be a [low, high] pair for between"
            )
    elif op in ("in", "not_in"):
        if not (isinstance(value, list) and value
                and all(_is_primitive(v) for v in value)):
            raise QPlanParseError(
                f"filters[{idx}].value must be a non-empty list of primitives "
                f"for op={op!r}"
            )
        if len(value) > 100:
            raise QPlanParseError(
                f"filters[{idx}].value list exceeds max length 100"
            )
    else:
        if not _is_primitive(value):
            raise QPlanParseError(
                f"filters[{idx}].value must be a primitive for op={op!r} "
                f"(no nested dicts / objects)"
            )
    return Filter(field=field_name, op=op, value=value, jsonb_path=jsonb_path)


def _is_primitive(v: Any) -> bool:
    return isinstance(v, (str, int, float, bool)) or v is None


def _parse_aggregation(raw: Any, *, idx: int) -> Aggregation:
    if not isinstance(raw, dict):
        raise QPlanParseError(f"aggregations[{idx}] must be an object")
    op = _expect_str(raw, "op", max_len=16).upper()
    if op not in ALLOWED_AGGREGATIONS:
        raise QPlanParseError(
            f"aggregations[{idx}].op={op!r} not allowed; "
            f"expected one of {sorted(ALLOWED_AGGREGATIONS)}"
        )
    # max_len bumped because `fields.<key>::<cast>` can be up to
    # 62+1+62+2+11 = 138 chars in the worst case.
    field_name = _expect_str(raw, "field", max_len=160)

    # Two valid field shapes:
    #   1. '*' (COUNT only)
    #   2. plain identifier — e.g. 'rarity_score'
    #   3. jsonb path — e.g. 'fields.debit::numeric'
    jsonb_path: tuple[str, str, str] | None = None
    if field_name == "*":
        if op != "COUNT":
            raise QPlanParseError(
                f"aggregations[{idx}].field='*' is only allowed with COUNT, "
                f"not {op!r}"
            )
    elif _is_valid_ident(field_name):
        pass  # plain column reference — existing behavior
    else:
        # Try jsonb-path form. Raises with a clear error if malformed.
        jsonb_path = _parse_jsonb_path(field_name)
        if jsonb_path is None:
            raise QPlanParseError(
                f"aggregations[{idx}].field={field_name!r} is not a valid "
                f"identifier, '*', or jsonb path (e.g. 'fields.debit::numeric')"
            )

    alias = _expect_str(raw, "alias")
    if not _is_valid_ident(alias):
        raise QPlanParseError(
            f"aggregations[{idx}].alias={alias!r} is not a valid identifier"
        )
    return Aggregation(
        op=op, field=field_name, alias=alias, jsonb_path=jsonb_path,
    )


def _parse_order_by(raw: Any) -> tuple[tuple[str, str], ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise QPlanParseError("order_by must be a list")
    out: list[tuple[str, str]] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise QPlanParseError(f"order_by[{i}] must be an object")
        field_name = _expect_str(item, "field")
        if not _is_valid_ident(field_name):
            raise QPlanParseError(
                f"order_by[{i}].field={field_name!r} is not a valid identifier"
            )
        direction = (item.get("direction") or "asc").lower()
        if direction not in ("asc", "desc"):
            raise QPlanParseError(
                f"order_by[{i}].direction must be 'asc' or 'desc'"
            )
        out.append((field_name, direction))
    if len(out) > 5:
        raise QPlanParseError("order_by has more than 5 entries")
    return tuple(out)


def parse_plan(raw: Any) -> QPlan:
    """Parse an untyped plan dict into a typed QPlan. Enforces layers
    2 + 3 (operator + aggregation enums) at parse time."""
    if not isinstance(raw, dict):
        raise QPlanParseError("Q plan must be a JSON object")

    # Layer 4 set-op guard — refuse anything with set_op until Wave B
    # ships the compiler support.
    set_op = raw.get("set_op")
    if set_op is not None and set_op != "":
        if set_op not in ALLOWED_SET_OPS:
            raise QPlanParseError(
                f"set_op={set_op!r} not allowed; expected one of "
                f"{sorted(ALLOWED_SET_OPS)}"
            )
        raise QPlanParseError(
            "set_op queries are not implemented in Wave A — single-table only"
        )

    if raw.get("joins"):
        raise QPlanParseError(
            "joins are not implemented in Wave A — single-table only"
        )

    if "from" not in raw and "from_table" not in raw:
        raise QPlanParseError("Q plan requires 'from' (the base table name)")

    from_table = raw.get("from") or raw.get("from_table")
    if not isinstance(from_table, str) or not _is_valid_ident(from_table):
        raise QPlanParseError(
            f"from={from_table!r} is not a valid table identifier"
        )

    raw_filters = raw.get("filters") or []
    if not isinstance(raw_filters, list):
        raise QPlanParseError("filters must be a list")
    if len(raw_filters) > 20:
        raise QPlanParseError("filters list exceeds max length 20")
    filters = tuple(
        _parse_filter(f, idx=i) for i, f in enumerate(raw_filters)
    )

    raw_aggs = raw.get("aggregations") or []
    if not isinstance(raw_aggs, list):
        raise QPlanParseError("aggregations must be a list")
    if len(raw_aggs) > 10:
        raise QPlanParseError("aggregations list exceeds max length 10")
    aggs = tuple(_parse_aggregation(a, idx=i) for i, a in enumerate(raw_aggs))

    raw_group = raw.get("group_by") or []
    if not isinstance(raw_group, list):
        raise QPlanParseError("group_by must be a list of column names")
    if len(raw_group) > 10:
        raise QPlanParseError("group_by list exceeds max length 10")
    group_by_cols: list[GroupByCol] = []
    for g in raw_group:
        if not isinstance(g, str):
            raise QPlanParseError(
                f"group_by entry {g!r} must be a string (plain column "
                f"name or jsonb path 'fields.<key>::<cast>')"
            )
        # Plain identifier (e.g. 'unit_type', 'file_id') → no jsonb path
        if _is_valid_ident(g):
            group_by_cols.append(GroupByCol(field=g))
            continue
        # jsonb-path form (e.g. 'fields.category::text') — same syntax
        # accepted by Filter.field and Aggregation.field. Validator
        # later checks the (table, jsonb_col) is in the allowlist.
        jsonb_path = _parse_jsonb_path(g)
        if jsonb_path is None:
            raise QPlanParseError(
                f"group_by entry {g!r} is not a valid identifier or "
                f"jsonb path (expected 'col' or 'col.key::cast')"
            )
        group_by_cols.append(GroupByCol(field=g, jsonb_path=jsonb_path))
    group_by = tuple(group_by_cols)

    order_by = _parse_order_by(raw.get("order_by"))

    try:
        limit = int(raw.get("limit", 100))
    except (TypeError, ValueError) as exc:
        raise QPlanParseError(f"limit must be an integer: {exc}") from exc
    if limit < 1 or limit > 10000:
        raise QPlanParseError(
            f"limit must be between 1 and 10000 (got {limit})"
        )

    return QPlan(
        from_table=from_table,
        filters=filters,
        group_by=group_by,
        aggregations=aggs,
        order_by=order_by,
        limit=limit,
    )
