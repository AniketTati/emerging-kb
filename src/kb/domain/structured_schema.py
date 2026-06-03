"""T2 (§6.1) — live structured-schema introspection: linking-as-retrieval + epoch.

This is the shared "what structured data actually exists right now" helper that
the T2 doc-id resolver (`kb.query.structured_prefilter`) and, later, the T3
dynamic aggregate catalog both read. It answers, for one workspace:

  - which `doc_types` exist, and how many docs of each;
  - per doc_type, the **doc_root scalar fields** actually present in
    `extracted_entities.fields` (canonical key, value_type, display label,
    **coverage** = presence fraction, grain);
  - the `unit_types` (tables) and their **columns** (+ grain);
  - the T1 **display→canonical** map (so a renamed field is queryable);
  - fuzzy `resolve_entity(name)` against the canonical `entities` table.

Two design rules from §6.1:

  * **Schema-linking, not schema-dumping.** At real schema size you cannot put
    every field in the planner prompt. `rank_fields_for_query` returns the
    top-k schema elements *retrieved* for a query by a deterministic lexical
    score (no embedder needed — works in CI). Low-coverage fields are still
    retrievable: coverage gates *answering* (§6.3), not *visibility*.

  * **Cache keyed on `schema_epoch`.** Building the live schema is a handful of
    GROUP BYs; we cache the result per `(workspace_id, epoch)`. T1 convergence
    and a T1 manual display-rename both bump the epoch (see
    `kb.domain.schema_epoch`), so a cached schema invalidates the instant the
    schema moves — closing the v1 bug where a label-only rename was invisible.

Ground truth is the *stored extraction* (`extracted_entities.fields` jsonb
keys), not the promoted/inferred catalog, so the field set never drifts from
what the resolver can actually query. `inferred_schema_fields` is consulted
only to *enrich* (declared value_type + display label) via LEFT JOIN.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from kb.domain.schema_epoch import read_schema_epoch


# Declared value_type labels (from extraction) that we treat as numeric for
# aggregation / range predicates, in addition to the ground-truth json-number
# probe. Lowercased compare.
_NUMERIC_VALUE_TYPES = frozenset({
    "number", "numeric", "integer", "int", "float", "decimal",
    "currency", "money", "percent", "percentage", "amount",
})


async def _safe_fetch(conn: Any, sp_name: str, sql: str, params: Any) -> list[tuple] | None:
    """SELECT inside a SAVEPOINT so a failure leaves the shared request txn
    usable (same pattern as channels / prefilter). Returns rows or None."""
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


def normalize_field_key(s: str) -> str:
    """Fold a field name to a comparison key: lowercase, every run of
    non-alphanumeric chars → one underscore, strip edge underscores. Mirrors
    `kb.query.mode_router._normalize_field_key` (kept in sync; duplicated here
    so the domain layer doesn't import the query layer)."""
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")


@dataclass(frozen=True)
class FieldInfo:
    """One doc_root scalar field present in the live extraction of a doc_type."""
    doc_type: str
    canonical_key: str         # == the stored extracted_entities.fields jsonb key
    value_type: str            # declared type, else 'number'/'string' from probe
    display_name: str | None   # T1 user label (canonical→display), if any
    coverage: float            # presence: docs_with_field / docs_of_type
    n_docs_with_field: int
    n_docs_of_type: int
    is_numeric: bool
    grain: str = "doc_root"


@dataclass(frozen=True)
class UnitColumnInfo:
    """One column of a unit_type (table) present in the live extraction."""
    unit_type: str
    canonical_key: str
    value_type: str
    coverage: float            # presence: files_with_col / files_with_unit
    n_rows: int
    n_files: int
    is_numeric: bool
    grain: str = ""            # set to f"unit:{unit_type}" in __post_init__-ish


@dataclass(frozen=True)
class EntityMatch:
    """A fuzzy-resolved canonical entity candidate."""
    entity_id: str
    canonical_name: str
    entity_type: str
    score: float               # 1.0 exact, else trigram similarity in [0,1]
    method: str                # 'exact' | 'trigram' | 'prefix'


@dataclass(frozen=True)
class LiveSchema:
    """Snapshot of the workspace's queryable structured schema at one epoch."""
    workspace_id: str
    epoch: int
    doc_types: tuple[str, ...]
    doc_counts: dict[str, int]
    fields: tuple[FieldInfo, ...]
    unit_types: tuple[str, ...]
    unit_columns: tuple[UnitColumnInfo, ...]
    # normalized display label -> canonical key (T1 manual rename pointer).
    field_display_map: dict[str, str] = field(default_factory=dict)

    # -- lookups -------------------------------------------------------------

    def doc_count(self, doc_type: str) -> int:
        return int(self.doc_counts.get(doc_type, 0))

    def fields_for(self, doc_type: str) -> tuple[FieldInfo, ...]:
        return tuple(f for f in self.fields if f.doc_type == doc_type)

    def columns_for(self, unit_type: str) -> tuple[UnitColumnInfo, ...]:
        return tuple(c for c in self.unit_columns if c.unit_type == unit_type)

    def find_field(self, surface_name: str) -> list[FieldInfo]:
        """Resolve a planner-emitted surface field name to the matching live
        scalar field(s), ACROSS doc_types — the schema-level analogue of
        `mode_router._resolve_field_name`. Resolution order, first non-empty
        tier wins:
          0. T1 display label → canonical key translation.
          1. exact canonical-key hit.
          2. normalized equality (case/space/hyphen/underscore folded).
          3. unambiguous token-subset (query tokens ⊆ exactly one key's tokens).
        Returns ALL matching FieldInfo (possibly across doc_types) so the
        caller can detect ambiguity (>1 distinct canonical key). Empty when
        nothing matches.
        """
        if not surface_name or not self.fields:
            return []
        name = surface_name
        canon = self.field_display_map.get(normalize_field_key(name))
        if canon is not None:
            name = canon
        # Tier 1: exact canonical key.
        exact = [f for f in self.fields if f.canonical_key == name]
        if exact:
            return exact
        qn = normalize_field_key(name)
        if not qn:
            return []
        # Tier 2: normalized equality.
        norm_eq = [f for f in self.fields if normalize_field_key(f.canonical_key) == qn]
        if norm_eq:
            return norm_eq
        # Tier 3: token-subset. Only accept when it resolves to exactly ONE
        # distinct canonical key (else it's ambiguous — return the set so the
        # caller marks the clause ambiguous rather than guessing).
        q_tokens = {t for t in qn.split("_") if t}
        if not q_tokens:
            return []
        subset = [
            f for f in self.fields
            if q_tokens.issubset(
                {t for t in normalize_field_key(f.canonical_key).split("_") if t}
            )
        ]
        return subset

    def distinct_keys(self, infos: list[FieldInfo]) -> set[str]:
        return {f.canonical_key for f in infos}


# ---------------------------------------------------------------------------
# Build (the SQL)
# ---------------------------------------------------------------------------


_LIVE_FILE_STATES = ("deleted", "failed")


async def _build_doc_counts(conn: Any, workspace_id: str) -> dict[str, int]:
    rows = await _safe_fetch(
        conn, "ss_doc_counts",
        "SELECT inferred_doc_type, count(*)::int FROM files "
        "WHERE workspace_id = %s AND inferred_doc_type IS NOT NULL "
        "  AND inferred_doc_type <> 'unknown' "
        "  AND lifecycle_state NOT IN ('deleted','failed') "
        "GROUP BY inferred_doc_type",
        (workspace_id,),
    )
    return {str(r[0]): int(r[1]) for r in (rows or [])}


async def _build_scalar_fields(
    conn: Any, workspace_id: str, doc_counts: dict[str, int],
) -> list[FieldInfo]:
    """Per (doc_type, jsonb key) presence + value typing, from the live
    doc_root extraction, enriched with declared type + display label."""
    rows = await _safe_fetch(
        conn, "ss_scalar_fields",
        "SELECT f.inferred_doc_type, k.key, "
        "       count(DISTINCT ee.file_id)::int AS docs_with_field, "
        "       bool_or(jsonb_typeof(ee.fields -> k.key) = 'number') AS has_numeric, "
        "       max(isf.value_type) AS declared_value_type, "
        "       max(isf.display_name) AS display_name "
        "FROM extracted_entities ee "
        "JOIN files f ON f.id = ee.file_id "
        "  AND f.lifecycle_state NOT IN ('deleted','failed') "
        "  AND f.inferred_doc_type IS NOT NULL "
        "  AND f.inferred_doc_type <> 'unknown' "
        "CROSS JOIN LATERAL jsonb_object_keys(ee.fields) AS k(key) "
        "LEFT JOIN inferred_schema_fields isf "
        "  ON isf.workspace_id = ee.workspace_id "
        " AND isf.inferred_doc_type = f.inferred_doc_type "
        " AND isf.canonical_name = k.key "
        "WHERE ee.workspace_id = %s AND ee.unit_type IS NULL "
        "GROUP BY f.inferred_doc_type, k.key",
        (workspace_id,),
    )
    out: list[FieldInfo] = []
    for doc_type, key, docs_with, has_numeric, declared, display in (rows or []):
        n_of_type = int(doc_counts.get(str(doc_type), 0)) or int(docs_with)
        declared_l = (declared or "").lower()
        is_numeric = bool(has_numeric) or declared_l in _NUMERIC_VALUE_TYPES
        value_type = declared or ("number" if has_numeric else "string")
        out.append(FieldInfo(
            doc_type=str(doc_type),
            canonical_key=str(key),
            value_type=str(value_type),
            display_name=display,
            coverage=(float(docs_with) / n_of_type) if n_of_type else 0.0,
            n_docs_with_field=int(docs_with),
            n_docs_of_type=n_of_type,
            is_numeric=is_numeric,
        ))
    return out


async def _build_unit_columns(
    conn: Any, workspace_id: str,
) -> tuple[list[str], list[UnitColumnInfo]]:
    # Files-per-unit_type is the coverage denominator.
    rows = await _safe_fetch(
        conn, "ss_unit_files",
        "SELECT unit_type, count(DISTINCT file_id)::int "
        "FROM extracted_entities "
        "WHERE workspace_id = %s AND unit_type IS NOT NULL "
        "GROUP BY unit_type",
        (workspace_id,),
    )
    files_per_unit = {str(r[0]): int(r[1]) for r in (rows or [])}

    rows = await _safe_fetch(
        conn, "ss_unit_cols",
        "SELECT ee.unit_type, k.key, "
        "       count(*)::int AS n_rows, "
        "       count(DISTINCT ee.file_id)::int AS n_files, "
        "       bool_or(jsonb_typeof(ee.fields -> k.key) = 'number') AS has_numeric "
        "FROM extracted_entities ee "
        "JOIN files f ON f.id = ee.file_id "
        "  AND f.lifecycle_state NOT IN ('deleted','failed') "
        "CROSS JOIN LATERAL jsonb_object_keys(ee.fields) AS k(key) "
        "WHERE ee.workspace_id = %s AND ee.unit_type IS NOT NULL "
        "GROUP BY ee.unit_type, k.key",
        (workspace_id,),
    )
    cols: list[UnitColumnInfo] = []
    for unit_type, key, n_rows, n_files, has_numeric in (rows or []):
        denom = files_per_unit.get(str(unit_type), 0) or int(n_files)
        cols.append(UnitColumnInfo(
            unit_type=str(unit_type),
            canonical_key=str(key),
            value_type="number" if has_numeric else "string",
            coverage=(float(n_files) / denom) if denom else 0.0,
            n_rows=int(n_rows),
            n_files=int(n_files),
            is_numeric=bool(has_numeric),
            grain=f"unit:{unit_type}",
        ))
    return sorted(files_per_unit.keys()), cols


async def _build_live_schema(conn: Any, workspace_id: str, epoch: int) -> LiveSchema:
    from kb.domain.fields import read_field_display_map

    doc_counts = await _build_doc_counts(conn, workspace_id)
    fields = await _build_scalar_fields(conn, workspace_id, doc_counts)
    unit_types, unit_columns = await _build_unit_columns(conn, workspace_id)
    try:
        raw_display = await read_field_display_map(conn, workspace_id=workspace_id)
        display_map = {normalize_field_key(d): c for d, c in raw_display.items()}
    except Exception:  # noqa: BLE001
        display_map = {}

    return LiveSchema(
        workspace_id=str(workspace_id),
        epoch=int(epoch),
        doc_types=tuple(sorted(doc_counts.keys())),
        doc_counts=doc_counts,
        fields=tuple(fields),
        unit_types=tuple(unit_types),
        unit_columns=tuple(unit_columns),
        field_display_map=display_map,
    )


# ---------------------------------------------------------------------------
# Epoch-keyed cache
# ---------------------------------------------------------------------------

# Small LRU over (workspace_id, epoch) → LiveSchema. The epoch in the key means
# a stale entry can never be served after a schema change — it just lingers
# until evicted. Process-local; multiple workers each warm their own.
_CACHE_MAX = 64
_cache: "OrderedDict[tuple[str, int], LiveSchema]" = OrderedDict()


def clear_cache() -> None:
    """Drop the whole cache (tests / explicit invalidation)."""
    _cache.clear()


async def live_schema(
    conn: Any, *, workspace_id: str, force_refresh: bool = False,
) -> LiveSchema:
    """Return the workspace's `LiveSchema` for the current schema epoch,
    building + caching it on a miss. Reads the epoch first (one cheap SQL) so
    a T1 rename/convergence bump invalidates a stale entry immediately.

    `conn` must be a live connection; there is no offline mode (an empty
    workspace simply yields an empty schema)."""
    epoch = await read_schema_epoch(conn, workspace_id=workspace_id)
    key = (str(workspace_id), int(epoch))
    if not force_refresh and key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    schema = await _build_live_schema(conn, workspace_id, epoch)
    _cache[key] = schema
    _cache.move_to_end(key)
    while len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)
    return schema


# ---------------------------------------------------------------------------
# Entity resolution (fuzzy)
# ---------------------------------------------------------------------------


async def resolve_entity(
    conn: Any, *, workspace_id: str, name: str, floor: float, limit: int = 10,
) -> list[EntityMatch]:
    """Resolve a surface entity name to canonical `entities` candidates.

    Order: exact (case-insensitive) → trigram similarity ≥ `floor` (also
    catches prefix). Returns candidates sorted by score desc. Best-effort:
    any SQL error (e.g. pg_trgm missing) yields []. The caller decides whether
    >1 distinct candidate means ambiguity (§6.2 single-value safety)."""
    n = (name or "").strip()
    if not n or conn is None:
        return []
    exact = await _safe_fetch(
        conn, "ss_entity_exact",
        "SELECT id::text, canonical_name, entity_type "
        "FROM canonical_entities "
        "WHERE workspace_id = %s AND merged_into IS NULL "
        "  AND lower(canonical_name) = lower(%s)",
        (workspace_id, n),
    )
    if exact:
        return [
            EntityMatch(
                entity_id=str(r[0]), canonical_name=str(r[1]),
                entity_type=str(r[2]), score=1.0, method="exact",
            )
            for r in exact
        ]
    rows = await _safe_fetch(
        conn, "ss_entity_trgm",
        "SELECT id::text, canonical_name, entity_type, "
        "       similarity(canonical_name, %s) AS sim, "
        "       (lower(canonical_name) LIKE lower(%s) || '%%') AS is_prefix "
        "FROM canonical_entities "
        "WHERE workspace_id = %s AND merged_into IS NULL "
        "  AND (similarity(canonical_name, %s) >= %s "
        "       OR lower(canonical_name) LIKE lower(%s) || '%%') "
        "ORDER BY sim DESC LIMIT %s",
        (n, n, workspace_id, n, floor, n, limit),
    )
    if rows is None:
        return []
    return [
        EntityMatch(
            entity_id=str(r[0]), canonical_name=str(r[1]), entity_type=str(r[2]),
            score=float(r[3] or 0.0),
            method="prefix" if (r[4] and float(r[3] or 0.0) < floor) else "trigram",
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Schema-linking-as-retrieval (deterministic lexical ranking)
# ---------------------------------------------------------------------------


_STOPWORDS = frozenset({
    "the", "a", "an", "of", "for", "to", "in", "on", "by", "is", "are",
    "what", "which", "show", "list", "all", "me", "my", "give", "and",
    "with", "value", "field", "fields", "doc", "docs", "document", "documents",
})


def _query_tokens(query: str) -> set[str]:
    toks = re.split(r"[^a-z0-9]+", (query or "").lower())
    return {t for t in toks if len(t) >= 3 and t not in _STOPWORDS}


def _field_tokens(info: FieldInfo) -> set[str]:
    parts = [info.canonical_key, info.display_name or "", info.doc_type]
    toks: set[str] = set()
    for p in parts:
        toks |= {t for t in normalize_field_key(p).split("_") if t}
    return toks


def rank_fields_for_query(
    schema: LiveSchema, query: str, *, top_k: int = 20,
) -> list[FieldInfo]:
    """Schema-LINKING: return the top-k live scalar fields most lexically
    relevant to `query`, so the planner prompt carries a *retrieved* slice of
    the schema, not the whole thing (§6.1). Deterministic (token overlap), so
    it works with no embedder. Ties broken by higher coverage then key, so the
    ranking is stable. When the query has no usable tokens, returns the
    highest-coverage fields (a sensible default surface)."""
    if not schema.fields:
        return []
    q = _query_tokens(query)
    scored: list[tuple[float, float, str, FieldInfo]] = []
    for info in schema.fields:
        ft = _field_tokens(info)
        overlap = len(q & ft)
        # Jaccard-ish score; 0 when no query tokens (falls back to coverage).
        score = (overlap / len(q | ft)) if (q and ft) else 0.0
        scored.append((score, info.coverage, info.canonical_key, info))
    scored.sort(key=lambda t: (-t[0], -t[1], t[2]))
    return [t[3] for t in scored[:top_k]]


def rank_unit_columns_for_query(
    schema: LiveSchema, query: str, *, top_k: int = 20,
) -> list[UnitColumnInfo]:
    """Unit-column analogue of `rank_fields_for_query`."""
    if not schema.unit_columns:
        return []
    q = _query_tokens(query)
    scored: list[tuple[float, float, str, UnitColumnInfo]] = []
    for col in schema.unit_columns:
        ct = {t for t in normalize_field_key(col.canonical_key).split("_") if t}
        ct |= {t for t in normalize_field_key(col.unit_type).split("_") if t}
        overlap = len(q & ct)
        score = (overlap / len(q | ct)) if (q and ct) else 0.0
        scored.append((score, col.coverage, col.canonical_key, col))
    scored.sort(key=lambda t: (-t[0], -t[1], t[2]))
    return [t[3] for t in scored[:top_k]]
