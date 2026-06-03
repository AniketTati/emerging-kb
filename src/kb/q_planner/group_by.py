"""T3 (§6.11) — group-by canonicalization + cardinality cap (post-exec).

A GROUP BY over an entity/string column fragments the same real-world entity
across spelling variants: `HDFC`, `HDFC Bank`, `HDFC Ltd` become three buckets,
so "average interest rate by lender" splits one lender three ways. SQL can't
safely normalize entity names, so this runs over the *result rows*: it folds
group keys through a canonical normalizer (lowercase, strip legal/banking
suffixes + punctuation) and re-merges the matching buckets.

Merge math is exact for SUM / COUNT / MIN / MAX. AVG and COUNT_DISTINCT can't be
re-derived from grouped output, so they're merged approximately and FLAGGED
(`approximate`) rather than silently presented as exact — the §6.6 "never trust
a number you can't audit" stance.

It also enforces `GROUPBY_CARDINALITY_CAP` — a sanity + DoS bound on distinct
groups: beyond the cap the result is sorted by the leading aggregate and
truncated, with a note (never a silent cut).
"""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import Any


# §5 default; overridable via layered-config (resolve_query_threshold).
DEFAULT_GROUPBY_CARDINALITY_CAP: int = 200

# Legal / org noise stripped before comparison so 'HDFC Bank Ltd' and 'HDFC'
# share a core token set (mirrors identity.merge._NOISE_SUFFIX_TOKENS + a few
# banking words).
_NOISE_TOKENS: frozenset[str] = frozenset({
    "pvt", "private", "ltd", "limited", "inc", "incorporated", "co",
    "company", "corp", "corporation", "llp", "llc", "plc", "gmbh", "sa",
    "the", "and", "of", "bank", "group", "holdings", "holding",
})

_TOKEN_STRIP_RE = re.compile(r"[^\w]+")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _canon_value(s: Any) -> str:
    """Fold an entity/string group key to a comparable token string:
    'HDFC Bank Ltd.' / 'HDFC' → 'hdfc'. Empty when fully stripped (caller keeps
    the original key in that case)."""
    toks: list[str] = []
    seen: set[str] = set()
    for raw in str(s).lower().split():
        t = _TOKEN_STRIP_RE.sub("", raw)
        if not t or t in _NOISE_TOKENS or t in seen:
            continue
        seen.add(t)
        toks.append(t)
    return " ".join(toks)


def _to_num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _looks_numeric_or_date(values: list[Any]) -> bool:
    """A group column is left ALONE (no canon) when most of its values are
    numbers or ISO dates — normalizing those would mangle '2025-03' into
    '202503'. Uses the same numeric parser as the value_type gate."""
    try:
        from kb.extraction.value_normalize import normalize_value
    except Exception:  # noqa: BLE001
        normalize_value = None  # type: ignore
    n = 0
    n_hit = 0
    for v in values:
        if v is None:
            continue
        n += 1
        sv = str(v)
        if _ISO_DATE_RE.match(sv) or (
            normalize_value is not None and normalize_value(sv) is not None
        ):
            n_hit += 1
    return n > 0 and (n_hit / n) >= 0.5


def canonicalize_and_cap(
    column_names: tuple[str, ...] | list[str],
    rows: tuple[tuple, ...] | list[tuple],
    *,
    group_by: tuple | list,
    aggregations: tuple | list,
    cap: int = DEFAULT_GROUPBY_CARDINALITY_CAP,
) -> tuple[list[str], list[tuple], list[str]]:
    """Return (column_names, rows, notes). `rows` are re-bucketed by canonical
    group key (when any merges) and capped to `cap` distinct groups.

    The SELECT projection is [group_by cols...] then [aggregation cols...], so
    aggregate op `aggregations[i].op` aligns to result column `len(group_by)+i`.
    """
    notes: list[str] = []
    cols = list(column_names)
    g = len(group_by or ())
    a = len(aggregations or ())
    work = [list(r) for r in rows]

    if g == 0 or not work:
        return _apply_cap(cols, work, g, aggregations, cap, notes)

    # Which group columns are string/entity (eligible for canonicalization)?
    eligible = {
        gi for gi in range(g)
        if not _looks_numeric_or_date([r[gi] for r in work])
    }

    def canon_key(r: list) -> tuple:
        out = []
        for gi in range(g):
            v = r[gi]
            if gi in eligible and v is not None and _canon_value(v):
                out.append(_canon_value(v))
            else:
                out.append(v)
        return tuple(out)

    buckets: "OrderedDict[tuple, list[list]]" = OrderedDict()
    for r in work:
        buckets.setdefault(canon_key(r), []).append(r)

    n_merged = sum(1 for v in buckets.values() if len(v) > 1)
    if n_merged == 0:
        return _apply_cap(cols, work, g, aggregations, cap, notes)

    approximate = False
    out_rows: list[list] = []
    for group_rows in buckets.values():
        if len(group_rows) == 1:
            out_rows.append(group_rows[0])
            continue
        merged = list(group_rows[0])
        # Display the most descriptive original key (longest surface form).
        for gi in range(g):
            merged[gi] = max(
                (gr[gi] for gr in group_rows),
                key=lambda x: len(str(x)) if x is not None else 0,
            )
        for ai in range(a):
            ci = g + ai
            op = (getattr(aggregations[ai], "op", "") or "").upper()
            vals = [gr[ci] for gr in group_rows if gr[ci] is not None]
            if not vals:
                merged[ci] = None
            elif op in ("SUM", "COUNT"):
                merged[ci] = sum(_to_num(v) for v in vals)
            elif op == "COUNT_DISTINCT":
                merged[ci] = sum(_to_num(v) for v in vals)
                approximate = True  # upper bound (shared values double-count)
            elif op == "MIN":
                merged[ci] = min(vals)
            elif op == "MAX":
                merged[ci] = max(vals)
            elif op == "AVG":
                merged[ci] = sum(_to_num(v) for v in vals) / len(vals)
                approximate = True  # unweighted mean-of-means
            else:
                merged[ci] = vals[0]
        out_rows.append(merged)

    note = (
        f"merged {n_merged} spelling-variant group(s) via canonical "
        f"entity normalization"
    )
    if approximate:
        note += " (AVG / COUNT_DISTINCT values are approximate after merge)"
    notes.append(note)
    return _apply_cap(cols, out_rows, g, aggregations, cap, notes)


def _apply_cap(
    cols: list[str],
    rows: list[list],
    g: int,
    aggregations: tuple | list,
    cap: int,
    notes: list[str],
) -> tuple[list[str], list[tuple], list[str]]:
    if cap > 0 and len(rows) > cap:
        if aggregations:
            ci = g  # first aggregate column
            try:
                rows = sorted(
                    rows,
                    key=lambda r: (
                        _to_num(r[ci]) if ci < len(r) and r[ci] is not None
                        else float("-inf")
                    ),
                    reverse=True,
                )
            except Exception:  # noqa: BLE001
                pass
        total = len(rows)
        rows = rows[:cap]
        notes.append(
            f"group-by cardinality capped to {cap} of {total} groups "
            f"(GROUPBY_CARDINALITY_CAP)"
        )
    return cols, [tuple(r) for r in rows], notes
