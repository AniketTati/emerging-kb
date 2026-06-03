"""T2 (§6.2 / §6.12) — structured predicate resolver → `ResolvedPredicate`.

This is the "answer-or-narrow from structured data first" head. It takes the
planner's `Plan` (field_filters / seed_entities / doc_types / unit_types /
date expressions) and resolves each part into a **clause** with its own matched
`file_ids`, **coverage** (presence) and **confidence**, then assembles the
single `ResolvedPredicate` object that flows to the scope state machine
(§6.7), confidence-weighted retrieval (§6.8/P1), the structured answer paths
(§6.3/6.5), and Q-mode row-level filtering (§6.11).

Key rules implemented here:
  - **Per-clause coverage + confidence** (drives P1: hard vs soft scope).
  - **Fuzzy entity** resolution (trigram floor `ENTITY_TRGM_MIN`), and entity
    clauses are **always low-confidence** (§6.3 — mention resolution ~47%), so
    an entity scope is never a hard pre-filter and an entity miss can never
    claim "no documents match".
  - **Ambiguous field** (a surface name mapping to >1 canonical key) is marked,
    not silently picked (§6.2).
  - **AND-salvage** (§6.2 / W14): if the AND of clauses is empty while
    individual clauses are non-empty, fall back to the largest non-empty
    subset and lower confidence — a spurious clause must not collapse a good
    scope to whole-corpus.
  - **Date normalization** (absolute + relative + fiscal, §6.2 / W16) →
    `row_filters` applied at row level by Q-mode, not as a doc filter.
  - **Over-broad** predicate (selectivity > `OVERBROAD_FRAC`) → no narrowing.

The SQL translation is the single source of truth (§6.2 / A12); the F-mode
Python post-filter in `mode_router` remains a *fallback only*.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Any

from kb.domain.structured_schema import (
    LiveSchema,
    live_schema,
    resolve_entity,
)


# ---------------------------------------------------------------------------
# Constants (§5 defaults; the orchestrator may override via layered config).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolverConstants:
    scope_conf_hard: float = 0.75      # SCOPE_CONF_HARD
    overbroad_frac: float = 0.80       # OVERBROAD_FRAC
    entity_trgm_min: float = 0.45      # ENTITY_TRGM_MIN
    coverage_high: float = 0.90        # COVERAGE_HIGH
    coverage_complete: float = 0.98    # COVERAGE_COMPLETE
    min_docs_for_coverage: int = 3     # MIN_DOCS_FOR_COVERAGE
    # Entity clauses are capped below scope_conf_hard so they are always soft.
    entity_conf_cap: float = 0.70
    doctype_conf: float = 0.90
    unit_conf: float = 0.85
    token_subset_penalty: float = 0.85
    fiscal_year_start_month: int = 1   # calendar year by default


DEFAULT_CONSTANTS = ResolverConstants()

# JSONB-SQL operator whitelist (the op is mapped, never interpolated as text).
_NUM_OP_SYM = {"lt": "<", "le": "<=", "gt": ">", "ge": ">=", "eq": "=", "ne": "<>"}
_TEXT_OP_SYM = {"eq": "=", "ne": "<>"}
_LIVE_STATES_SQL = "f.lifecycle_state NOT IN ('deleted','failed')"


# ---------------------------------------------------------------------------
# Date normalization (§6.2 / W16) — pure, injectable `now`.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DateRange:
    start: str   # ISO 'YYYY-MM-DD' inclusive
    end: str     # ISO 'YYYY-MM-DD' inclusive

    def to_list(self) -> list[str]:
        return [self.start, self.end]


_MONTHS = {
    m.lower(): i for i, m in enumerate(calendar.month_name) if m
} | {
    m.lower(): i for i, m in enumerate(calendar.month_abbr) if m
}


def _month_range(year: int, month: int) -> DateRange:
    last = calendar.monthrange(year, month)[1]
    return DateRange(f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last:02d}")


def _quarter_range(year: int, q: int, *, fy_start_month: int = 1) -> DateRange:
    # Quarter q (1..4) of a (fiscal) year starting at fy_start_month.
    start_month_0 = (fy_start_month - 1 + (q - 1) * 3) % 12
    start_year = year + (fy_start_month - 1 + (q - 1) * 3) // 12
    start_month = start_month_0 + 1
    # end = start + 3 months - 1 day
    end_month_0 = (start_month_0 + 2) % 12
    end_year = start_year + (start_month_0 + 2) // 12
    end_month = end_month_0 + 1
    last = calendar.monthrange(end_year, end_month)[1]
    return DateRange(
        f"{start_year:04d}-{start_month:02d}-01",
        f"{end_year:04d}-{end_month:02d}-{last:02d}",
    )


def normalize_date_expression(
    expr: str, *, now: datetime, fiscal_year_start_month: int = 1,
) -> DateRange | None:
    """Normalize an absolute / relative / fiscal date expression to an
    inclusive [start, end] range, anchored on `now`. Returns None when the
    expression isn't a recognizable date window (caller then ignores it —
    fail-open, never narrows on a guess)."""
    if not expr:
        return None
    e = expr.strip().lower()
    today = now.date()

    # ---- relative ----
    if e in ("today",):
        return DateRange(today.isoformat(), today.isoformat())
    if e in ("yesterday",):
        y = today - timedelta(days=1)
        return DateRange(y.isoformat(), y.isoformat())
    if e in ("this month",):
        return _month_range(today.year, today.month)
    if e in ("last month", "previous month"):
        y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
        return _month_range(y, m)
    if e in ("this year",):
        return DateRange(f"{today.year:04d}-01-01", f"{today.year:04d}-12-31")
    if e in ("last year", "previous year"):
        return DateRange(f"{today.year - 1:04d}-01-01", f"{today.year - 1:04d}-12-31")
    if e in ("ytd", "year to date", "this year to date"):
        return DateRange(f"{today.year:04d}-01-01", today.isoformat())
    if e in ("this quarter", "current quarter"):
        q = (today.month - 1) // 3 + 1
        return _quarter_range(today.year, q, fy_start_month=fiscal_year_start_month)
    if e in ("last quarter", "previous quarter"):
        cur_q = (today.month - 1) // 3 + 1
        q, yr = (cur_q - 1, today.year) if cur_q > 1 else (4, today.year - 1)
        return _quarter_range(yr, q, fy_start_month=fiscal_year_start_month)
    m = re.fullmatch(r"last (\d{1,3}) (day|days|month|months|year|years)", e)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if unit.startswith("day"):
            return DateRange((today - timedelta(days=n)).isoformat(), today.isoformat())
        if unit.startswith("month"):
            # approx month window
            return DateRange((today - timedelta(days=30 * n)).isoformat(), today.isoformat())
        return DateRange((today - timedelta(days=365 * n)).isoformat(), today.isoformat())

    # ---- fiscal / calendar quarter: "Q1 2026", "q3 fy2026", "Q2 FY26" ----
    m = re.fullmatch(r"q([1-4])\s*(fy)?\s*'?(\d{2,4})", e)
    if m:
        q = int(m.group(1))
        is_fiscal = bool(m.group(2))
        yr = _coerce_year(m.group(3))
        return _quarter_range(
            yr, q,
            fy_start_month=fiscal_year_start_month if is_fiscal else 1,
        )

    # ---- fiscal year: "FY2026", "FY26" ----
    m = re.fullmatch(r"fy\s*'?(\d{2,4})", e)
    if m:
        yr = _coerce_year(m.group(1))
        start = date(yr, fiscal_year_start_month, 1)
        # fiscal year ends the day before the same month next year
        end_year = yr + 1 if fiscal_year_start_month > 1 else yr
        if fiscal_year_start_month == 1:
            return DateRange(f"{yr:04d}-01-01", f"{yr:04d}-12-31")
        end = date(end_year, fiscal_year_start_month, 1) - timedelta(days=1)
        return DateRange(start.isoformat(), end.isoformat())

    # ---- absolute: ISO date / month / year ----
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", e)
    if m:
        return DateRange(e, e)
    m = re.fullmatch(r"(\d{4})-(\d{2})", e)
    if m:
        return _month_range(int(m.group(1)), int(m.group(2)))
    m = re.fullmatch(r"(\d{4})", e)
    if m:
        return DateRange(f"{e}-01-01", f"{e}-12-31")

    # ---- "March 2026" / "Mar 2026" ----
    m = re.fullmatch(r"([a-z]+)\.?\s+(\d{4})", e)
    if m and m.group(1) in _MONTHS:
        return _month_range(int(m.group(2)), _MONTHS[m.group(1)])
    # ---- "2026 March" ----
    m = re.fullmatch(r"(\d{4})\s+([a-z]+)\.?", e)
    if m and m.group(2) in _MONTHS:
        return _month_range(int(m.group(1)), _MONTHS[m.group(2)])

    return None


def _coerce_year(s: str) -> int:
    y = int(s)
    if y < 100:           # "26" → 2026
        y += 2000
    return y


# ---------------------------------------------------------------------------
# Dataclasses (§6.12)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Clause:
    kind: str                              # field|entity|doctype|unit|date
    surface: str | None = None             # original surface form (audit/relax)
    canonical_key: str | None = None       # field clause
    op: str | None = None
    value: Any = None
    # entity clause
    entity_ids: tuple[str, ...] = ()
    entity_names: tuple[str, ...] = ()
    # resolution result
    file_ids: tuple[str, ...] = ()
    coverage: float = 0.0
    confidence: float = 0.0
    ambiguous: bool = False
    notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "surface": self.surface,
            "canonical_key": self.canonical_key, "op": self.op,
            "value": self.value,
            "entity_ids": list(self.entity_ids),
            "entity_names": list(self.entity_names),
            "file_ids": list(self.file_ids),
            "coverage": self.coverage, "confidence": self.confidence,
            "ambiguous": self.ambiguous, "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Clause":
        return cls(
            kind=str(d.get("kind") or "field"),
            surface=d.get("surface"),
            canonical_key=d.get("canonical_key"),
            op=d.get("op"), value=d.get("value"),
            entity_ids=tuple(d.get("entity_ids") or ()),
            entity_names=tuple(d.get("entity_names") or ()),
            file_ids=tuple(d.get("file_ids") or ()),
            coverage=float(d.get("coverage") or 0.0),
            confidence=float(d.get("confidence") or 0.0),
            ambiguous=bool(d.get("ambiguous") or False),
            notes=d.get("notes"),
        )


@dataclass(frozen=True)
class RowFilter:
    """A date window / value threshold pushed into the aggregate SQL WHERE at
    row level (§6.11), not used to pick docs."""
    column: str | None
    op: str                                # 'between' | lt/le/gt/ge/eq/ne
    value: Any                             # scalar, or [lo, hi] for 'between'
    grain: str = "doc_root"
    kind: str = "value"                    # 'date' | 'value'

    def to_dict(self) -> dict[str, Any]:
        return {
            "column": self.column, "op": self.op, "value": self.value,
            "grain": self.grain, "kind": self.kind,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RowFilter":
        return cls(
            column=d.get("column"), op=str(d.get("op") or "eq"),
            value=d.get("value"), grain=str(d.get("grain") or "doc_root"),
            kind=str(d.get("kind") or "value"),
        )


@dataclass(frozen=True)
class ResolvedPredicate:
    """The single object that flows Plan → resolver → {scope machine, retrieval,
    Q-mode, fallback} and is persisted for carry-forward (§6.12)."""
    clauses: tuple[Clause, ...] = ()
    # None == ALL (unscoped). A frozenset of file_ids == a narrowed scope.
    file_scope: frozenset[str] | None = None
    row_filters: tuple[RowFilter, ...] = ()
    overall_confidence: float = 0.0
    selectivity: float = 1.0               # scoped/total; 1.0 == ALL
    total_docs: int = 0
    dropped_clauses: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def is_all(self) -> bool:
        return self.file_scope is None

    @property
    def scoped_doc_count(self) -> int:
        return -1 if self.file_scope is None else len(self.file_scope)

    def is_hard(self, constants: ResolverConstants = DEFAULT_CONSTANTS) -> bool:
        """P1: hard pre-filter only when there IS a scope AND confidence is
        high. Otherwise the scope is a soft rerank boost (or ALL)."""
        return (
            self.file_scope is not None
            and self.overall_confidence >= constants.scope_conf_hard
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "clauses": [c.to_dict() for c in self.clauses],
            "file_scope": (None if self.file_scope is None
                           else sorted(self.file_scope)),
            "row_filters": [r.to_dict() for r in self.row_filters],
            "overall_confidence": self.overall_confidence,
            "selectivity": self.selectivity,
            "total_docs": self.total_docs,
            "dropped_clauses": list(self.dropped_clauses),
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "ResolvedPredicate":
        if not d:
            return cls()
        fs = d.get("file_scope")
        return cls(
            clauses=tuple(Clause.from_dict(c) for c in (d.get("clauses") or ())),
            file_scope=None if fs is None else frozenset(str(x) for x in fs),
            row_filters=tuple(
                RowFilter.from_dict(r) for r in (d.get("row_filters") or ())
            ),
            overall_confidence=float(d.get("overall_confidence") or 0.0),
            selectivity=float(d.get("selectivity") if d.get("selectivity") is not None else 1.0),
            total_docs=int(d.get("total_docs") or 0),
            dropped_clauses=tuple(d.get("dropped_clauses") or ()),
            notes=tuple(d.get("notes") or ()),
        )


# An ALL (unscoped) predicate — the safe default everywhere.
ALL_PREDICATE = ResolvedPredicate()


# ---------------------------------------------------------------------------
# SQL helpers
# ---------------------------------------------------------------------------


async def _safe_query(
    conn: Any, sp_name: str, sql: str, params: Any,
) -> list[tuple] | None:
    """Run a SELECT inside a SAVEPOINT so a single failure (malformed value,
    schema drift) rolls back cleanly and leaves the shared request txn usable —
    same pattern as `channels._run_channel_query`. Returns rows, or None on any
    failure (the caller maps that to 'clause unresolved', NOT to a corpus-wide
    degradation). Without this, a resolver query error would abort the txn and
    silently turn the subsequent scoped retrieval into a no-hits answer."""
    try:
        await conn.execute(f"SAVEPOINT {sp_name}")
    except Exception:  # noqa: BLE001
        return None
    try:
        cur = await conn.execute(sql, params)
        rows = await cur.fetchall()
        try:
            await conn.execute(f"RELEASE SAVEPOINT {sp_name}")
        except Exception:  # noqa: BLE001
            pass
        return rows
    except Exception:  # noqa: BLE001
        try:
            await conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
            await conn.execute(f"RELEASE SAVEPOINT {sp_name}")
        except Exception:  # noqa: BLE001
            pass
        return None


def _coerce_number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _field_predicate_sql(
    key: str, op: str, value: Any, is_numeric: bool,
) -> tuple[str, dict[str, Any]] | None:
    """Build a parameterized JSONB predicate fragment for a doc_root field.
    Returns (sql_fragment, params) or None when the op/value can't be built."""
    op = (op or "eq").lower()
    params: dict[str, Any] = {"k": key}
    num = _coerce_number(value)

    # Numeric comparison path — guard the cast on json-number typing so a
    # stray non-numeric value can never abort the query.
    if op in _NUM_OP_SYM and is_numeric and num is not None:
        params["v"] = num
        sym = _NUM_OP_SYM[op]
        return (
            f"(jsonb_typeof(ee.fields -> %(k)s) = 'number' "
            f"AND (ee.fields ->> %(k)s)::numeric {sym} %(v)s)",
            params,
        )
    if op in ("lt", "le", "gt", "ge"):
        # Range op on a non-numeric / unparseable value → can't build.
        if num is None:
            return None
        params["v"] = num
        sym = _NUM_OP_SYM[op]
        return (
            f"(jsonb_typeof(ee.fields -> %(k)s) = 'number' "
            f"AND (ee.fields ->> %(k)s)::numeric {sym} %(v)s)",
            params,
        )
    if op in ("eq", "ne"):
        sym = _TEXT_OP_SYM[op]
        params["v"] = str(value)
        return (f"(ee.fields ->> %(k)s {sym} %(v)s)", params)
    if op == "like":
        params["v"] = f"%{value}%"
        return ("(ee.fields ->> %(k)s ILIKE %(v)s)", params)
    if op == "in":
        vals = value if isinstance(value, (list, tuple)) else [value]
        params["v"] = [str(x) for x in vals]
        return ("(ee.fields ->> %(k)s = ANY(%(v)s))", params)
    return None


async def _file_ids_for_field(
    conn: Any, *, workspace_id: str, key: str, op: str, value: Any, is_numeric: bool,
) -> tuple[str, ...] | None:
    frag = _field_predicate_sql(key, op, value, is_numeric)
    if frag is None:
        return None
    sql_frag, params = frag
    params["ws"] = workspace_id
    sql = (
        "SELECT DISTINCT ee.file_id::text FROM extracted_entities ee "
        "JOIN files f ON f.id = ee.file_id AND " + _LIVE_STATES_SQL + " "
        "WHERE ee.workspace_id = %(ws)s AND ee.unit_type IS NULL AND " + sql_frag
    )
    rows = await _safe_query(conn, "sp_field_ids", sql, params)
    if rows is None:
        return None
    return tuple(str(r[0]) for r in rows)


async def _file_ids_for_entities(
    conn: Any, *, workspace_id: str, entity_ids: list[str],
) -> tuple[str, ...]:
    if not entity_ids:
        return ()
    rows = await _safe_query(
        conn, "sp_entity_ids",
        "SELECT DISTINCT em.file_id::text "
        "FROM mention_to_entity m2e "
        "JOIN extracted_mentions em ON em.id = m2e.mention_id "
        "JOIN files f ON f.id = em.file_id AND " + _LIVE_STATES_SQL + " "
        "WHERE m2e.workspace_id = %s AND m2e.entity_id = ANY(%s::uuid[])",
        (workspace_id, entity_ids),
    )
    return tuple(str(r[0]) for r in rows) if rows is not None else ()


async def _file_ids_for_doctypes(
    conn: Any, *, workspace_id: str, doc_types: list[str],
) -> tuple[str, ...]:
    if not doc_types:
        return ()
    rows = await _safe_query(
        conn, "sp_doctype_ids",
        "SELECT id::text FROM files f WHERE workspace_id = %s "
        "AND inferred_doc_type = ANY(%s) AND " + _LIVE_STATES_SQL,
        (workspace_id, doc_types),
    )
    return tuple(str(r[0]) for r in rows) if rows is not None else ()


async def _file_ids_for_units(
    conn: Any, *, workspace_id: str, unit_types: list[str],
) -> tuple[str, ...]:
    if not unit_types:
        return ()
    rows = await _safe_query(
        conn, "sp_unit_ids",
        "SELECT DISTINCT ee.file_id::text FROM extracted_entities ee "
        "JOIN files f ON f.id = ee.file_id AND " + _LIVE_STATES_SQL + " "
        "WHERE ee.workspace_id = %s AND ee.unit_type = ANY(%s)",
        (workspace_id, unit_types),
    )
    return tuple(str(r[0]) for r in rows) if rows is not None else ()


# ---------------------------------------------------------------------------
# AND-salvage (§6.2 / W14)
# ---------------------------------------------------------------------------


def _intersect(sets: list[set[str]]) -> set[str]:
    if not sets:
        return set()
    out = set(sets[0])
    for s in sets[1:]:
        out &= s
    return out


def and_salvage(
    constraining: list[Clause],
) -> tuple[set[str] | None, list[Clause], list[Clause]]:
    """Intersect the file-id sets of `constraining` clauses (each non-empty).
    If the full AND is empty, return the LARGEST subset whose intersection is
    non-empty (drop the rest). Returns (scope_set_or_None, kept, dropped).
    `None` scope means there was nothing to constrain on."""
    usable = [c for c in constraining if c.file_ids and not c.ambiguous]
    if not usable:
        return (None, [], [])
    sets = [set(c.file_ids) for c in usable]
    full = _intersect(sets)
    if full:
        return (full, usable, [])
    # Salvage: search subsets for a non-empty intersection. Prefer (a) the
    # MOST clauses kept, then (b) the LARGER resulting doc set, then (c) higher
    # total confidence. (b) is the I1 tie-break: when forced to drop a clause,
    # keep the more recall-preserving scope — a spurious clause must not
    # collapse a good (e.g. entity) scope to a narrower one.
    n = len(usable)
    best: tuple[int, int, float] | None = None
    best_pick: tuple[set[str], list[Clause]] | None = None
    for mask in range(1, 1 << n):
        idxs = [i for i in range(n) if mask & (1 << i)]
        inter = _intersect([sets[i] for i in idxs])
        if not inter:
            continue
        kept = [usable[i] for i in idxs]
        rank = (len(idxs), len(inter), sum(c.confidence for c in kept))
        if best is None or rank > best:
            best = rank
            best_pick = (inter, kept)
    if best_pick is None:
        # No subset intersects (all pairwise disjoint) — keep the single
        # clause with the LARGEST doc set (recall-preserving), then confidence.
        top = max(usable, key=lambda c: (len(c.file_ids), c.confidence))
        kept = [top]
        dropped = [c for c in usable if c is not top]
        return (set(top.file_ids), kept, dropped)
    inter, kept = best_pick
    kept_ids = {id(c) for c in kept}
    dropped = [c for c in usable if id(c) not in kept_ids]
    return (inter, kept, dropped)


# ---------------------------------------------------------------------------
# Clause builders
# ---------------------------------------------------------------------------


async def _resolve_field_clause(
    conn: Any, *, workspace_id: str, schema: LiveSchema, filt: dict,
    constants: ResolverConstants,
) -> Clause:
    surface = filt.get("field")
    op = (filt.get("op") or "eq").lower()
    value = filt.get("value")
    infos = schema.find_field(surface) if surface else []
    distinct_keys = sorted({i.canonical_key for i in infos})

    if not infos:
        return Clause(
            kind="field", surface=surface, op=op, value=value,
            confidence=0.0, notes="field_not_found",
        )
    if len(distinct_keys) > 1:
        # Ambiguous surface form → do NOT auto-pick (§6.2). Soft, low-conf.
        return Clause(
            kind="field", surface=surface, op=op, value=value,
            canonical_key=None, ambiguous=True, confidence=0.25,
            notes=f"ambiguous:{','.join(distinct_keys)}",
        )

    key = distinct_keys[0]
    # Representative FieldInfo for this key (highest coverage wins).
    rep = max((i for i in infos if i.canonical_key == key),
              key=lambda i: i.coverage)
    file_ids = await _file_ids_for_field(
        conn, workspace_id=workspace_id, key=key, op=op, value=value,
        is_numeric=rep.is_numeric,
    )
    if file_ids is None:
        return Clause(
            kind="field", surface=surface, canonical_key=key, op=op, value=value,
            coverage=rep.coverage, confidence=0.0, notes="predicate_unbuildable",
        )
    # Resolution quality: exact/normalized vs token-subset.
    from kb.domain.structured_schema import normalize_field_key
    qn = normalize_field_key(surface or "")
    clean = (surface == key) or (qn == normalize_field_key(key)) or (
        normalize_field_key(surface or "") in schema.field_display_map
    )
    quality = 1.0 if clean else constants.token_subset_penalty
    conf = rep.coverage * quality
    if rep.n_docs_of_type < constants.min_docs_for_coverage:
        conf = min(conf, 0.5)
    return Clause(
        kind="field", surface=surface, canonical_key=key, op=op, value=value,
        file_ids=file_ids, coverage=rep.coverage, confidence=conf,
    )


async def _resolve_entity_clause(
    conn: Any, *, workspace_id: str, name: str, constants: ResolverConstants,
) -> Clause:
    matches = await resolve_entity(
        conn, workspace_id=workspace_id, name=name, floor=constants.entity_trgm_min,
    )
    if not matches:
        return Clause(
            kind="entity", surface=name, confidence=0.0, notes="entity_unresolved",
        )
    distinct_ids = {m.entity_id for m in matches}
    # Single-value safety (§6.2): >1 distinct canonical entity → ambiguous.
    ambiguous = len(distinct_ids) > 1 and matches[0].method != "exact"
    entity_ids = [m.entity_id for m in matches]
    file_ids = await _file_ids_for_entities(
        conn, workspace_id=workspace_id, entity_ids=entity_ids,
    )
    best = matches[0].score
    # Entity clauses are ALWAYS low-confidence (§6.3): cap below scope_conf_hard
    # so an entity scope is a soft boost, never a hard pre-filter, and an
    # entity miss can never claim "no documents match".
    conf = min(best, constants.entity_conf_cap)
    if ambiguous:
        conf = min(conf, 0.3)
    return Clause(
        kind="entity", surface=name,
        entity_ids=tuple(entity_ids),
        entity_names=tuple(m.canonical_name for m in matches),
        file_ids=file_ids, coverage=best, confidence=conf, ambiguous=ambiguous,
        notes=None if not ambiguous else
        f"ambiguous_entity:{len(distinct_ids)}",
    )


# ---------------------------------------------------------------------------
# resolve()
# ---------------------------------------------------------------------------


async def resolve(
    conn: Any,
    *,
    workspace_id: str,
    plan: Any,
    inherited_predicate: ResolvedPredicate | None = None,  # used by §6.7 (Phase C)
    schema: LiveSchema | None = None,
    now: datetime | None = None,
    constants: ResolverConstants = DEFAULT_CONSTANTS,
) -> ResolvedPredicate:
    """Resolve the plan into a `ResolvedPredicate` (fresh, this turn). The
    combination with `inherited_predicate` is performed by the scope state
    machine (§6.7); this function resolves the fresh predicate and accepts the
    inherited one only so callers can pass it through uniformly.

    Degrades safely: any resolution failure yields a clause with empty
    file_ids / zero confidence; an all-empty resolution yields ALL_PREDICATE
    (unscoped), so plain RAG is always reachable (I1/I2)."""
    if conn is None or plan is None:
        return ALL_PREDICATE
    if schema is None:
        schema = await live_schema(conn, workspace_id=workspace_id)
    total_docs = sum(schema.doc_counts.values())

    clauses: list[Clause] = []
    row_filters: list[RowFilter] = []
    notes: list[str] = []

    # ---- field clauses ----
    for filt in (getattr(plan, "field_filters", None) or ()):
        if not isinstance(filt, dict):
            continue
        clauses.append(await _resolve_field_clause(
            conn, workspace_id=workspace_id, schema=schema, filt=filt,
            constants=constants,
        ))

    # ---- entity clauses ----
    for name in (getattr(plan, "seed_entities", None) or ()):
        if not name:
            continue
        clauses.append(await _resolve_entity_clause(
            conn, workspace_id=workspace_id, name=str(name), constants=constants,
        ))

    # ---- doc_type clause ----
    doc_types = [d for d in (getattr(plan, "doc_types", None) or ()) if d]
    if doc_types:
        fids = await _file_ids_for_doctypes(
            conn, workspace_id=workspace_id, doc_types=doc_types,
        )
        clauses.append(Clause(
            kind="doctype", surface=",".join(doc_types), value=doc_types,
            file_ids=fids, coverage=1.0,
            confidence=constants.doctype_conf if fids else 0.0,
        ))

    # ---- unit_type clause ----
    unit_types = [u for u in (getattr(plan, "unit_types", None) or ()) if u]
    if unit_types:
        fids = await _file_ids_for_units(
            conn, workspace_id=workspace_id, unit_types=unit_types,
        )
        clauses.append(Clause(
            kind="unit", surface=",".join(unit_types), value=unit_types,
            file_ids=fids, coverage=1.0,
            confidence=constants.unit_conf if fids else 0.0,
        ))

    # ---- date clauses → row_filters (§6.11 row-level, not a doc filter) ----
    anchor = now or datetime.now()
    for df in (getattr(plan, "date_filters", None) or ()):
        if not isinstance(df, dict):
            continue
        rng = normalize_date_expression(
            str(df.get("expr") or ""), now=anchor,
            fiscal_year_start_month=constants.fiscal_year_start_month,
        )
        if rng is None:
            continue
        col = df.get("field") or df.get("column")
        row_filters.append(RowFilter(
            column=col, op="between", value=rng.to_list(),
            grain=str(df.get("grain") or "doc_root"), kind="date",
        ))
        clauses.append(Clause(
            kind="date", surface=str(df.get("expr")), canonical_key=col,
            value=rng.to_list(), coverage=0.0, confidence=0.0,
            notes="row_filter_only",
        ))

    # ---- assemble file_scope via AND-salvage ----
    # Only field / entity / doctype / unit clauses constrain the doc scope;
    # date clauses are row-level only.
    constraining = [c for c in clauses if c.kind in ("field", "entity", "doctype", "unit")]
    scope_set, kept, dropped = and_salvage(constraining)

    file_scope: frozenset[str] | None
    dropped_desc: list[str] = []
    if scope_set is None:
        # Nothing resolved to a usable set → unscoped (ALL). Plain RAG path.
        file_scope = None
        overall_conf = 0.0
    else:
        file_scope = frozenset(scope_set)
        if dropped:
            for c in dropped:
                dropped_desc.append(f"{c.kind}:{c.surface}")
            notes.append("and_salvage")
        # Weakest-link confidence over the KEPT constraining clauses (§6.3),
        # with a penalty when salvage had to drop a clause.
        kept_confs = [c.confidence for c in kept] or [0.0]
        overall_conf = min(kept_confs)
        if dropped:
            overall_conf *= 0.8

    # ---- over-broad → no narrowing (§6.10) ----
    selectivity = 1.0
    if file_scope is not None and total_docs > 0:
        selectivity = len(file_scope) / total_docs
        if selectivity > constants.overbroad_frac:
            notes.append(f"overbroad:{selectivity:.2f}->ALL")
            file_scope = None
            overall_conf = 0.0
            selectivity = 1.0

    return ResolvedPredicate(
        clauses=tuple(clauses),
        file_scope=file_scope,
        row_filters=tuple(row_filters),
        overall_confidence=round(overall_conf, 4),
        selectivity=round(selectivity, 4),
        total_docs=total_docs,
        dropped_clauses=tuple(dropped_desc),
        notes=tuple(notes),
    )


# ---------------------------------------------------------------------------
# Scope state machine (§6.7) — carry the PREDICATE across turns.
# ---------------------------------------------------------------------------


_RELAX_RE = re.compile(
    r"\b(make it|change (it )?to|set it to|lower (it )?to|raise (it )?to|"
    r"bring it (down|up) to|now use|instead of|rather than|not\s+\d|"
    r"drop the|remove the|without the|loosen|widen|relax)\b",
    re.IGNORECASE,
)
_DROP_FILTER_RE = re.compile(
    r"\b(drop|remove|without|ignore)\s+the\s+([a-z0-9 _\-]+?)\s+filter\b",
    re.IGNORECASE,
)
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")
_GENERIC_CORPUS_NOUNS = (
    "docs", "documents", "files", "everything", "the whole corpus",
    "whole corpus", "entire corpus", "across the board", "all of them",
)
_RESET_PHRASES = (
    "across all", "in all", "over all", "all documents", "all docs",
    "all files", "whole corpus", "entire corpus", "reset the filter",
    "ignore the previous filter", "ignore the prior filter", "no filter",
    "start over", "forget the", "clear the filter",
)


def looks_like_relax(query: str) -> bool:
    return bool(_RELAX_RE.search(query or ""))


def _relax_new_value(query: str) -> float | None:
    m = _NUM_RE.search(query or "")
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _doc_type_nouns(schema: LiveSchema | None) -> set[str]:
    """Doc-type vocabulary (+ naive singular/plural) for the schema-driven
    CLEAR reset, so 'across all contracts' resets when 'contract' is a doc_type."""
    out: set[str] = set()
    for dt in (schema.doc_types if schema else ()):
        base = (dt or "").lower().strip()
        if not base:
            continue
        out.add(base)
        out.add(base + "s")
        if base.endswith("s"):
            out.add(base[:-1])
    return out


def looks_like_clear(query: str, schema: LiveSchema | None = None) -> bool:
    """Schema-driven reset (§6.7 CLEAR): `all|every|each` + a doc-type noun
    (from the live schema) or a generic corpus noun, or an explicit reset
    phrase. Fixes the v1 miss where 'across all contracts' didn't reset."""
    q = (query or "").lower()
    if any(p in q for p in _RESET_PHRASES):
        return True
    if re.search(r"\b(all|every|each)\b", q):
        nouns = _doc_type_nouns(schema) | set(_GENERIC_CORPUS_NOUNS)
        if any(re.search(rf"\b{re.escape(n)}\b", q) for n in nouns):
            return True
    return False


def _recompute_scope(
    clauses: list[Clause], total_docs: int, constants: ResolverConstants,
) -> tuple[frozenset[str] | None, float, float, list[str], list[str]]:
    """Re-run AND-salvage + over-broad over a clause list. Returns
    (file_scope, overall_confidence, selectivity, dropped_desc, notes)."""
    constraining = [c for c in clauses if c.kind in ("field", "entity", "doctype", "unit")]
    scope_set, kept, dropped = and_salvage(constraining)
    notes: list[str] = []
    dropped_desc: list[str] = []
    if scope_set is None:
        return (None, 0.0, 1.0, [], [])
    file_scope: frozenset[str] | None = frozenset(scope_set)
    if dropped:
        dropped_desc = [f"{c.kind}:{c.surface}" for c in dropped]
        notes.append("and_salvage")
    overall_conf = min([c.confidence for c in kept] or [0.0])
    if dropped:
        overall_conf *= 0.8
    selectivity = 1.0
    if total_docs > 0:
        selectivity = len(file_scope) / total_docs
        if selectivity > constants.overbroad_frac:
            return (None, 0.0, 1.0, [], [f"overbroad:{selectivity:.2f}->ALL"])
    return (file_scope, round(overall_conf, 4), round(selectivity, 4), dropped_desc, notes)


def _intersect_predicates(
    inherited: ResolvedPredicate, fresh: ResolvedPredicate,
) -> tuple[ResolvedPredicate, str]:
    """INTERSECT: prior_scope ∩ new clause. Empty-after-intersect (§6.7) →
    fall back to the fresh predicate across ALL + a surfaced note."""
    a = inherited.file_scope
    b = fresh.file_scope
    if a is None:
        combined: frozenset[str] | None = b
    elif b is None:
        combined = a
    else:
        combined = a & b
    clauses = inherited.clauses + fresh.clauses
    row_filters = inherited.row_filters + fresh.row_filters
    confs = [c for c in (inherited.overall_confidence, fresh.overall_confidence) if c > 0]
    conf = min(confs) if confs else 0.0
    total = fresh.total_docs or inherited.total_docs
    if combined is not None and len(combined) == 0:
        # Nothing in the prior focus matched the new clause — answer the new
        # clause across ALL (drop the prior focus) and surface it.
        return (
            replace(
                fresh,
                clauses=clauses,
                notes=tuple(fresh.notes) + ("intersect_empty_widened_to_all",),
            ),
            "intersect_empty",
        )
    sel = (len(combined) / total) if (combined is not None and total) else 1.0
    return (
        ResolvedPredicate(
            clauses=clauses, file_scope=combined, row_filters=row_filters,
            overall_confidence=round(conf, 4), selectivity=round(sel, 4),
            total_docs=total, notes=("intersect",),
        ),
        "intersect",
    )


async def scope_state_machine(
    conn: Any,
    *,
    workspace_id: str,
    query: str,
    inherited: ResolvedPredicate | None,
    fresh: ResolvedPredicate,
    schema: LiveSchema | None = None,
    refinement: bool = False,
    is_aggregate: bool = False,
    original_query: str | None = None,
    constants: ResolverConstants = DEFAULT_CONSTANTS,
) -> tuple[ResolvedPredicate, str]:
    """Combine the carried `inherited` predicate with this turn's `fresh` one
    per §6.7 (first match wins): CLEAR / RELAX / INTERSECT / REPLACE / INHERIT /
    NONE. Returns (combined_predicate, decision). Ambiguity defaults to the
    safe side (REPLACE/NONE with the fresh predicate), never a silent narrow.

    The phrasing detectors (clear/relax) check BOTH `query` (the resolved
    effective query) AND `original_query` — the context resolver can launder a
    follow-up like 'make it 8%' (anaphoric) and strip the relax cue, so the
    user's raw phrasing is the authoritative signal for these decisions."""
    # No prior scope to carry → fresh (CLEAR is moot).
    if inherited is None or inherited.is_all:
        return (fresh, "none")

    orig = original_query or query

    # 1. CLEAR — schema-driven reset, or an aggregate (a fresh global question
    #    by nature) that wasn't explicitly re-scoped this turn.
    if looks_like_clear(query, schema) or looks_like_clear(orig, schema):
        return (fresh, "clear")
    if is_aggregate:
        # Aggregates default to ALL unless this turn explicitly added a scope.
        return (fresh, "clear")

    # 2. RELAX — loosen an existing clause (re-resolve from the base predicate).
    #    Check the raw phrasing too (anaphora resolution may strip 'make it …').
    relax_text = orig if looks_like_relax(orig) else (
        query if looks_like_relax(query) else None
    )
    if relax_text is not None:
        relaxed, ok = await _apply_relax(
            conn, workspace_id=workspace_id, query=relax_text, inherited=inherited,
            schema=schema, constants=constants,
        )
        if ok:
            return (relaxed, "relax")
        # Couldn't map the relax onto a base clause → safe fallback.
        return (fresh if not fresh.is_all else inherited, "relax_fallback")

    # 3. INTERSECT — a refinement that ADDS a clause to the prior focus.
    if refinement and not fresh.is_all:
        return _intersect_predicates(inherited, fresh)

    # 4. REPLACE — a new predicate (topic change), not a refinement.
    if not fresh.is_all:
        return (fresh, "replace")

    # 5. INHERIT — anaphoric / no new clause → reuse the prior predicate.
    return (inherited, "inherit")


async def _apply_relax(
    conn: Any,
    *,
    workspace_id: str,
    query: str,
    inherited: ResolvedPredicate,
    schema: LiveSchema | None,
    constants: ResolverConstants,
) -> tuple[ResolvedPredicate, bool]:
    """RELAX a carried predicate. Two cases:
      - "drop the <X> filter" → remove the matching inherited clause (broaden).
      - "make it 8% (not 9%)" → re-resolve the inherited numeric field clause
        with the new bound (keeping its field + op), so the widened bound is
        applied to the ORIGINAL clause — set-intersection cannot do this."""
    if schema is None:
        schema = await live_schema(conn, workspace_id=workspace_id)
    clauses = list(inherited.clauses)

    # --- drop a named filter ---
    m = _DROP_FILTER_RE.search(query)
    if m:
        target = normalize_field_key(m.group(2))
        kept = [
            c for c in clauses
            if not (
                (c.canonical_key and normalize_field_key(c.canonical_key) == target)
                or (c.surface and target in normalize_field_key(c.surface))
                or (c.kind == "date" and "date" in target)
            )
        ]
        if len(kept) < len(clauses):
            fs, conf, sel, dropped, notes = _recompute_scope(
                kept, inherited.total_docs, constants,
            )
            return (
                ResolvedPredicate(
                    clauses=tuple(kept), file_scope=fs,
                    row_filters=inherited.row_filters,
                    overall_confidence=conf, selectivity=sel,
                    total_docs=inherited.total_docs,
                    dropped_clauses=tuple(dropped),
                    notes=tuple(notes) + ("relax_drop",),
                ),
                True,
            )
        return (inherited, False)

    # --- loosen a numeric bound on the inherited field clause ---
    new_val = _relax_new_value(query)
    if new_val is None:
        return (inherited, False)
    # Pick the field clause carrying a numeric range op to relax.
    idx = next(
        (i for i, c in enumerate(clauses)
         if c.kind == "field" and (c.op in ("gt", "ge", "lt", "le"))
         and c.canonical_key),
        None,
    )
    if idx is None:
        return (inherited, False)
    base = clauses[idx]
    relaxed_clause = await _resolve_field_clause(
        conn, workspace_id=workspace_id, schema=schema,
        filt={"field": base.canonical_key, "op": base.op, "value": new_val},
        constants=constants,
    )
    clauses[idx] = relaxed_clause
    fs, conf, sel, dropped, notes = _recompute_scope(
        clauses, inherited.total_docs, constants,
    )
    return (
        ResolvedPredicate(
            clauses=tuple(clauses), file_scope=fs,
            row_filters=inherited.row_filters,
            overall_confidence=conf, selectivity=sel,
            total_docs=inherited.total_docs,
            dropped_clauses=tuple(dropped),
            notes=tuple(notes) + ("relax_value",),
        ),
        True,
    )
