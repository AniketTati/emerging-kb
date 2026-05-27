"""Knowledge Map API — single backing module for the redesigned
Schema Studio UI (routed under `/schema-studio` in the frontend).

The Knowledge Map UI has 3 tabs and was designed against the actual
data shape in this workspace:

  📚 Catalog        — every doc-type the system has learned, with its
                      doc-root fields + sub-entity types + per-sub-entity
                      column shapes (jsonb keys observed across rows).
  🔍 Needs Review   — anomalies + unresolved fact_conflicts + emerging
                      fields + synonym proposals.
  🕓 History        — file_lifecycle timeline.

Each endpoint here is a focused read-only aggregation that the UI can
call once and render — avoiding N+1 fan-out from the browser side.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from kb.api.deps import current_workspace_id, kb_app_connection
from kb.db.pool import Connection

router = APIRouter(prefix="/knowledge-map", tags=["knowledge-map"])


# ===========================================================================
# 📚 Catalog
# ===========================================================================


class KMField(BaseModel):
    """One field — either a promoted schema_field (doc-root) OR a
    discovered jsonb key (sub-entity column). Same shape for both so
    the UI renders them identically."""

    name: str
    type: str | None = None
    prevalence: float | None = None   # 0..1 — for promoted schema_fields
    description: str | None = None


class KMSubEntityType(BaseModel):
    """One sub-entity type belonging to a schema, with the column
    shape observed in extracted_entities.fields jsonb."""

    name: str               # PascalCase entity type name (e.g. "Transaction")
    unit_type: str          # snake_case unit_type stored in DB (e.g. "transaction")
    row_count: int          # # extracted_entities rows of this unit_type
    fields: list[KMField] = Field(default_factory=list)


class KMSchemaCard(BaseModel):
    """One card on the Catalog tab. Self-contained — UI renders without
    follow-up calls."""

    id: str
    name: str               # raw `auto:<doc_type>` — UI humanizes
    description: str | None
    created_at: str         # ISO-8601
    file_count: int         # # files this schema applies to
    file_ids: list[str]     # up to 5 file UUIDs for "View file" deep-link
    doc_root_fields: list[KMField]
    sub_entity_types: list[KMSubEntityType]


class KMCatalogResponse(BaseModel):
    items: list[KMSchemaCard] = Field(default_factory=list)


@router.get(
    "/catalog",
    response_model=KMCatalogResponse,
    summary="One-shot Catalog payload — all schemas + their entity types + "
            "field shapes for the Knowledge Map UI.",
)
async def get_catalog(
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> KMCatalogResponse:
    # 1) Active schemas — basic shape + file count via JOIN on
    #    extracted_entities (any doc_root row = that schema was used
    #    on at least one file).
    cur = await conn.execute(
        """
        SELECT s.id::text, s.name, s.description, s.created_at,
               (SELECT count(DISTINCT ee.file_id)
                  FROM extracted_entities ee
                  JOIN schema_entities se ON se.id = ee.schema_entity_id
                 WHERE se.schema_id = s.id
                   AND ee.unit_type IS NULL) AS file_count,
               (SELECT array_agg(DISTINCT ee.file_id::text)
                  FROM extracted_entities ee
                  JOIN schema_entities se ON se.id = ee.schema_entity_id
                 WHERE se.schema_id = s.id
                   AND ee.unit_type IS NULL) AS file_ids
          FROM schemas s
         WHERE s.workspace_id = %s AND s.lifecycle_state = 'active'
         ORDER BY s.created_at DESC, s.name ASC
        """,
        (workspace_id,),
    )
    schema_rows = await cur.fetchall()
    if not schema_rows:
        return KMCatalogResponse(items=[])

    schema_ids = [r[0] for r in schema_rows]

    # 2) Doc-root entities + their promoted schema_fields, indexed by
    #    schema_id for assembly.
    cur = await conn.execute(
        """
        SELECT se.schema_id::text, se.id::text, se.name, se.kind,
               se.parent_type_id::text
          FROM schema_entities se
         WHERE se.schema_id = ANY(%s::uuid[]) AND se.lifecycle_state = 'active'
         ORDER BY se.kind, se.created_at
        """,
        (schema_ids,),
    )
    entity_rows = await cur.fetchall()
    doc_roots_by_schema: dict[str, str] = {}
    subs_by_schema: dict[str, list[tuple[str, str]]] = {}
    for r in entity_rows:
        schema_id, ent_id, ent_name, kind, _parent = r
        if kind == "doc_root":
            doc_roots_by_schema[schema_id] = ent_id
        elif kind == "sub_entity":
            subs_by_schema.setdefault(schema_id, []).append((ent_id, ent_name))

    # 3) Doc-root schema_fields per entity.
    doc_root_entity_ids = list(doc_roots_by_schema.values())
    fields_by_entity: dict[str, list[KMField]] = {}
    if doc_root_entity_ids:
        cur = await conn.execute(
            """
            SELECT entity_id::text, name, type, nl_description
              FROM schema_fields
             WHERE entity_id = ANY(%s::uuid[]) AND lifecycle_state = 'active'
             ORDER BY created_at
            """,
            (doc_root_entity_ids,),
        )
        for ent_id, fname, ftype, descr in await cur.fetchall():
            fields_by_entity.setdefault(ent_id, []).append(
                KMField(name=fname, type=ftype, description=descr),
            )

    # 4) Sub-entity row counts + observed jsonb keys per unit_type
    #    (one query, grouped). Sub-entity rows live in
    #    extracted_entities WHERE unit_type IS NOT NULL.
    sub_rollup: dict[str, dict[str, Any]] = {}  # unit_type → {count, keys}
    cur = await conn.execute(
        """
        SELECT unit_type, count(*) AS n, jsonb_object_keys(fields) AS k
          FROM extracted_entities
         WHERE workspace_id = %s AND unit_type IS NOT NULL
         GROUP BY unit_type, k
         ORDER BY unit_type
        """,
        (workspace_id,),
    )
    for unit_type, n, k in await cur.fetchall():
        bucket = sub_rollup.setdefault(unit_type, {"count": 0, "keys": []})
        # n is the # rows where this key is present — but we just want
        # the row count for the unit_type overall.
        bucket["count"] = max(int(bucket["count"]), int(n))
        if k not in bucket["keys"]:
            bucket["keys"].append(str(k))

    # Map schema_entity sub-entity name (PascalCase) → underscore unit_type.
    def _pascal_to_snake(s: str) -> str:
        import re
        return re.sub(r"(?<!^)(?=[A-Z])", "_", s).lower()

    # 5) Assemble cards.
    items: list[KMSchemaCard] = []
    for r in schema_rows:
        sid, name, desc, created, file_count, file_ids = r
        # Cap file_ids at 5 — that's enough for the "View file" UI;
        # full file listing per-schema lives in /upload filtered by
        # doc-type.
        file_ids_capped: list[str] = (
            [str(x) for x in (file_ids or [])][:5]
        )
        sub_types: list[KMSubEntityType] = []
        for _ent_id, ent_name in subs_by_schema.get(sid, []):
            unit_type = _pascal_to_snake(ent_name)
            stats = sub_rollup.get(unit_type, {"count": 0, "keys": []})
            sub_types.append(KMSubEntityType(
                name=ent_name,
                unit_type=unit_type,
                row_count=int(stats["count"]),
                fields=[KMField(name=k) for k in stats["keys"]],
            ))
        doc_root_id = doc_roots_by_schema.get(sid)
        doc_root_fields = fields_by_entity.get(doc_root_id, []) if doc_root_id else []
        items.append(KMSchemaCard(
            id=str(sid),
            name=str(name),
            description=desc,
            created_at=created.isoformat() if hasattr(created, "isoformat") else str(created),
            file_count=int(file_count or 0),
            file_ids=file_ids_capped,
            doc_root_fields=doc_root_fields,
            sub_entity_types=sub_types,
        ))
    return KMCatalogResponse(items=items)


# ===========================================================================
# 🔍 Needs Review — anomalies, conflicts (the demo state has 127 + 161)
# ===========================================================================


class KMAnomaly(BaseModel):
    id: str
    unit_type: str
    file_id: str
    file_name: str | None
    rarity_score: float
    fields: dict[str, Any]


class KMConflict(BaseModel):
    id: str
    entity_id: str | None
    predicate: str
    observed_at: str
    evidence_count: int
    evidence_preview: list[dict[str, Any]]   # truncated to first 5
    resolution: str
    notes: str | None


class KMNeedsReviewResponse(BaseModel):
    anomalies: list[KMAnomaly] = Field(default_factory=list)
    anomalies_total: int = 0
    conflicts: list[KMConflict] = Field(default_factory=list)
    conflicts_total: int = 0
    emerging_fields_total: int = 0
    synonym_proposals_total: int = 0


@router.get(
    "/needs-review",
    response_model=KMNeedsReviewResponse,
    summary="Single payload for the Needs Review tab: anomalies + "
            "conflicts + counts for emerging fields and synonym proposals.",
)
async def get_needs_review(
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
    anomaly_limit: int = Query(default=20, ge=1, le=200),
    conflict_limit: int = Query(default=20, ge=1, le=200),
) -> KMNeedsReviewResponse:
    # Anomalies — top-N by rarity_score.
    cur = await conn.execute(
        """
        SELECT ee.id::text, ee.unit_type, ee.file_id::text, f.name,
               ee.rarity_score, ee.fields
          FROM extracted_entities ee
          LEFT JOIN files f ON f.id = ee.file_id
         WHERE ee.workspace_id = %s
           AND ee.rarity_score IS NOT NULL
           AND ee.rarity_score > 0.8
         ORDER BY ee.rarity_score DESC NULLS LAST, ee.id
         LIMIT %s
        """,
        (workspace_id, anomaly_limit),
    )
    anomalies: list[KMAnomaly] = []
    for r in await cur.fetchall():
        anomalies.append(KMAnomaly(
            id=str(r[0]),
            unit_type=str(r[1]),
            file_id=str(r[2]),
            file_name=str(r[3]) if r[3] is not None else None,
            rarity_score=float(r[4]),
            fields=dict(r[5]) if isinstance(r[5], dict) else {},
        ))

    cur = await conn.execute(
        "SELECT count(*) FROM extracted_entities "
        "WHERE workspace_id = %s AND rarity_score IS NOT NULL "
        "  AND rarity_score > 0.8",
        (workspace_id,),
    )
    anomalies_total = int((await cur.fetchone())[0])

    # Conflicts — unresolved fact_conflicts.
    cur = await conn.execute(
        """
        SELECT id::text, entity_id::text, predicate, observed_at,
               evidence, resolution, notes
          FROM fact_conflicts
         WHERE workspace_id = %s AND resolution = 'unresolved'
         ORDER BY observed_at DESC, id
         LIMIT %s
        """,
        (workspace_id, conflict_limit),
    )
    conflicts: list[KMConflict] = []
    for r in await cur.fetchall():
        evidence = r[4] if isinstance(r[4], list) else []
        conflicts.append(KMConflict(
            id=str(r[0]),
            entity_id=(str(r[1]) if r[1] is not None else None),
            predicate=str(r[2]),
            observed_at=(r[3].isoformat() if hasattr(r[3], "isoformat") else str(r[3])),
            evidence_count=len(evidence),
            evidence_preview=evidence[:5],
            resolution=str(r[5]),
            notes=(str(r[6]) if r[6] is not None else None),
        ))

    cur = await conn.execute(
        "SELECT count(*) FROM fact_conflicts "
        "WHERE workspace_id = %s AND resolution = 'unresolved'",
        (workspace_id,),
    )
    conflicts_total = int((await cur.fetchone())[0])

    # Emerging fields — inferred_schema_fields not yet promoted.
    cur = await conn.execute(
        "SELECT count(*) FROM inferred_schema_fields "
        "WHERE workspace_id = %s AND is_promoted = false",
        (workspace_id,),
    )
    emerging_total = int((await cur.fetchone())[0])

    # Synonym proposals — Wave-A simplification: domain_vocabulary
    # rows are user-accepted, not proposals. Pending proposals would
    # land in `proposed_fields` clustering — track that as a future
    # enhancement. For now, surface 0 with the empty-state hint.
    return KMNeedsReviewResponse(
        anomalies=anomalies,
        anomalies_total=anomalies_total,
        conflicts=conflicts,
        conflicts_total=conflicts_total,
        emerging_fields_total=emerging_total,
        synonym_proposals_total=0,
    )


# ===========================================================================
# 🕓 History — file_lifecycle timeline (workspace-wide)
# ===========================================================================


class KMHistoryEvent(BaseModel):
    id: str
    file_id: str
    file_name: str | None
    event: str
    to_state: str | None
    payload: dict[str, Any]
    created_at: str


class KMHistoryResponse(BaseModel):
    items: list[KMHistoryEvent] = Field(default_factory=list)
    next_cursor: str | None = None
    total: int = 0


@router.get(
    "/history",
    response_model=KMHistoryResponse,
    summary="Workspace-wide file_lifecycle timeline — every event the "
            "pipeline emitted (schema creation, field extraction, "
            "identity resolution, …). Powers the History tab.",
)
async def get_history(
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
    limit: int = Query(default=100, ge=1, le=500),
    cursor: str | None = Query(default=None),
    event_filter: str | None = Query(
        default=None,
        description="Filter by event prefix (e.g. 'schema_', 'mentions_'). "
                    "Leave empty for all events.",
    ),
) -> KMHistoryResponse:
    # Cursor encodes (created_at, id) so we can paginate
    # deterministically across the timeline.
    cursor_ts: datetime | None = None
    cursor_id: str | None = None
    if cursor:
        try:
            import base64
            import json as _json
            decoded = _json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
            cursor_ts = datetime.fromisoformat(decoded["ts"])
            cursor_id = str(decoded["id"])
        except Exception:
            cursor_ts, cursor_id = None, None

    params: list[Any] = [workspace_id]
    where = ["fl.workspace_id = %s"]
    if event_filter:
        where.append("fl.event LIKE %s")
        params.append(f"{event_filter}%")
    if cursor_ts is not None and cursor_id is not None:
        where.append("(fl.created_at, fl.id::text) < (%s, %s)")
        params.append(cursor_ts)
        params.append(cursor_id)
    where_clause = " AND ".join(where)

    params.append(limit + 1)
    cur = await conn.execute(
        f"""
        SELECT fl.id::text, fl.file_id::text, f.name, fl.event,
               fl.to_state, fl.payload, fl.created_at
          FROM file_lifecycle fl
          LEFT JOIN files f ON f.id = fl.file_id
         WHERE {where_clause}
         ORDER BY fl.created_at DESC, fl.id::text DESC
         LIMIT %s
        """,
        tuple(params),
    )
    rows = await cur.fetchall()
    has_next = len(rows) > limit
    rows = rows[:limit]

    items: list[KMHistoryEvent] = []
    for r in rows:
        items.append(KMHistoryEvent(
            id=str(r[0]),
            file_id=str(r[1]),
            file_name=(str(r[2]) if r[2] is not None else None),
            event=str(r[3]),
            to_state=(str(r[4]) if r[4] is not None else None),
            payload=(dict(r[5]) if isinstance(r[5], dict) else {}),
            created_at=(r[6].isoformat() if hasattr(r[6], "isoformat") else str(r[6])),
        ))

    next_cursor = None
    if has_next and items:
        import base64
        import json as _json
        last = rows[-1]
        next_cursor = base64.urlsafe_b64encode(
            _json.dumps({
                "ts": last[6].isoformat() if hasattr(last[6], "isoformat") else str(last[6]),
                "id": str(last[0]),
            }).encode(),
        ).decode()

    cur = await conn.execute(
        "SELECT count(*) FROM file_lifecycle WHERE workspace_id = %s",
        (workspace_id,),
    )
    total = int((await cur.fetchone())[0])

    return KMHistoryResponse(items=items, next_cursor=next_cursor, total=total)


# ===========================================================================
# Header counts — single call so the 4-stat header isn't 4 round-trips
# ===========================================================================


class KMStats(BaseModel):
    doc_types: int
    files_ingested: int
    sub_entities: int
    pending_review: int   # anomalies + conflicts + emerging + synonyms total


@router.get(
    "/stats",
    response_model=KMStats,
    summary="Top-of-page stat cards: doc types, files ingested, sub-"
            "entities extracted, items pending review.",
)
async def get_stats(
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> KMStats:
    cur = await conn.execute(
        "SELECT count(*) FROM schemas "
        "WHERE workspace_id = %s AND lifecycle_state = 'active'",
        (workspace_id,),
    )
    doc_types = int((await cur.fetchone())[0])

    cur = await conn.execute(
        "SELECT count(*) FROM files "
        "WHERE workspace_id = %s AND lifecycle_state = 'ready'",
        (workspace_id,),
    )
    files_ingested = int((await cur.fetchone())[0])

    cur = await conn.execute(
        "SELECT count(*) FROM extracted_entities "
        "WHERE workspace_id = %s AND unit_type IS NOT NULL",
        (workspace_id,),
    )
    sub_entities = int((await cur.fetchone())[0])

    cur = await conn.execute(
        """
        SELECT
          (SELECT count(*) FROM extracted_entities
            WHERE workspace_id = %s AND rarity_score > 0.8) +
          (SELECT count(*) FROM fact_conflicts
            WHERE workspace_id = %s AND resolution = 'unresolved') +
          (SELECT count(*) FROM inferred_schema_fields
            WHERE workspace_id = %s AND is_promoted = false)
        """,
        (workspace_id, workspace_id, workspace_id),
    )
    pending = int((await cur.fetchone())[0])

    return KMStats(
        doc_types=doc_types,
        files_ingested=files_ingested,
        sub_entities=sub_entities,
        pending_review=pending,
    )


# ===========================================================================
# Per-schema sample — actual extracted values for the side panel.
# ===========================================================================


class KMDocRootField(BaseModel):
    """One row in the side panel's 'Doc-level fields' table.
    `value` is from the FIRST file (when file_count==1 that IS the only
    file's value; when file_count > 1, it's the first as a sample).
    `value` is None if the field is null on that row."""

    name: str
    type: str | None = None
    value: Any | None = None
    description: str | None = None


class KMSubEntitySample(BaseModel):
    unit_type: str
    name: str
    row_count: int
    columns: list[str]
    rows: list[dict[str, Any]]   # up to N rows of `fields` jsonb


class KMSchemaSample(BaseModel):
    schema_id: str
    file_count: int
    file_ids: list[str]
    doc_root_fields: list[KMDocRootField]
    sub_entity_samples: list[KMSubEntitySample]


@router.get(
    "/schema/{schema_id}/sample",
    response_model=KMSchemaSample,
    summary="Fetch a representative sample of extracted values for one "
            "schema: first file's doc-root field values + first N rows "
            "of each sub-entity type. Powers the Catalog side panel.",
)
async def get_schema_sample(
    schema_id: str,
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
    sub_rows: int = Query(default=5, ge=1, le=50),
) -> KMSchemaSample:
    # Validate schema belongs to this workspace.
    cur = await conn.execute(
        "SELECT id FROM schemas "
        "WHERE id = %s AND workspace_id = %s AND lifecycle_state = 'active'",
        (schema_id, workspace_id),
    )
    if (await cur.fetchone()) is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="schema not found")

    # File ids + count via the same path as the catalog endpoint.
    cur = await conn.execute(
        """
        SELECT array_agg(DISTINCT ee.file_id::text), count(DISTINCT ee.file_id)
          FROM extracted_entities ee
          JOIN schema_entities se ON se.id = ee.schema_entity_id
         WHERE se.schema_id = %s AND ee.unit_type IS NULL
        """,
        (schema_id,),
    )
    row = await cur.fetchone()
    file_ids: list[str] = [str(x) for x in (row[0] or [])]
    file_count = int(row[1] or 0)

    # Doc-root schema_fields + the FIRST file's doc-root fields jsonb.
    cur = await conn.execute(
        """
        SELECT sf.name, sf.type, sf.nl_description
          FROM schema_fields sf
          JOIN schema_entities se ON se.id = sf.entity_id
         WHERE se.schema_id = %s AND se.kind = 'doc_root'
           AND se.lifecycle_state = 'active'
           AND sf.lifecycle_state = 'active'
         ORDER BY sf.created_at
        """,
        (schema_id,),
    )
    field_defs = [
        (str(r[0]), (str(r[1]) if r[1] is not None else None),
         (str(r[2]) if r[2] is not None else None))
        for r in await cur.fetchall()
    ]

    # Pull the doc-root row from the first file (jsonb fields).
    sample_values: dict[str, Any] = {}
    if file_ids:
        cur = await conn.execute(
            """
            SELECT ee.fields
              FROM extracted_entities ee
              JOIN schema_entities se ON se.id = ee.schema_entity_id
             WHERE se.schema_id = %s AND ee.file_id = %s::uuid
               AND ee.unit_type IS NULL
             LIMIT 1
            """,
            (schema_id, file_ids[0]),
        )
        r = await cur.fetchone()
        if r and isinstance(r[0], dict):
            sample_values = r[0]

    doc_root_fields = [
        KMDocRootField(
            name=name,
            type=ftype,
            value=sample_values.get(name),
            description=descr,
        )
        for name, ftype, descr in field_defs
    ]

    # Sub-entity types + first N rows of each.
    cur = await conn.execute(
        """
        SELECT se.id::text, se.name
          FROM schema_entities se
         WHERE se.schema_id = %s AND se.kind = 'sub_entity'
           AND se.lifecycle_state = 'active'
         ORDER BY se.created_at
        """,
        (schema_id,),
    )
    sub_types = [(str(r[0]), str(r[1])) for r in await cur.fetchall()]

    import re
    def _pascal_to_snake(s: str) -> str:
        return re.sub(r"(?<!^)(?=[A-Z])", "_", s).lower()

    sub_samples: list[KMSubEntitySample] = []
    for sub_entity_id, sub_name in sub_types:
        unit_type = _pascal_to_snake(sub_name)
        cur = await conn.execute(
            """
            SELECT fields
              FROM extracted_entities
             WHERE workspace_id = %s AND schema_entity_id = %s::uuid
             ORDER BY created_at
             LIMIT %s
            """,
            (workspace_id, sub_entity_id, sub_rows),
        )
        rows_data = [dict(r[0]) for r in await cur.fetchall() if isinstance(r[0], dict)]

        # Total row count for this sub-entity type.
        cur = await conn.execute(
            """
            SELECT count(*) FROM extracted_entities
             WHERE workspace_id = %s AND schema_entity_id = %s::uuid
            """,
            (workspace_id, sub_entity_id),
        )
        total = int((await cur.fetchone())[0])

        # Column order: stable across rows by first observed.
        cols_seen: list[str] = []
        for r in rows_data:
            for k in r.keys():
                if k not in cols_seen:
                    cols_seen.append(k)

        sub_samples.append(KMSubEntitySample(
            unit_type=unit_type,
            name=sub_name,
            row_count=total,
            columns=cols_seen,
            rows=rows_data,
        ))

    return KMSchemaSample(
        schema_id=str(schema_id),
        file_count=file_count,
        file_ids=file_ids,
        doc_root_fields=doc_root_fields,
        sub_entity_samples=sub_samples,
    )


# ===========================================================================
# Anomaly cohort — what's THIS row vs what's TYPICAL.
# Powers the comparison-table view in the Needs Review side panel
# so a non-engineer can SEE why something was flagged.
# ===========================================================================


class KMCohortField(BaseModel):
    """One field's value on the anomaly row, plus a tiny stat
    describing how it compares to the cohort. `is_outlier` is True
    when the value sits outside the cohort's typical range for that
    field — gives the UI a flag to highlight."""

    name: str
    value: Any
    is_outlier: bool = False
    cohort_summary: str | None = None   # e.g. "typical: 1-2"


class KMCohortResponse(BaseModel):
    anomaly_id: str
    unit_type: str
    rarity_score: float
    rarity_label: str       # plain-English description for the UI
    file_id: str | None
    file_name: str | None
    cohort_size: int        # total rows of this unit_type (including the anomaly)
    columns: list[str]      # ordered union of column names
    anomaly_row: dict[str, Any]
    anomaly_field_stats: list[KMCohortField]   # parallel to columns; carries outlier flags
    typical_rows: list[dict[str, Any]]


def _summarize_numeric(values: list[float]) -> str:
    """Compact "typical: X-Y" or "typical: X" range string."""
    if not values: return ""
    lo, hi = min(values), max(values)
    if lo == hi:
        return f"typical: {_fmt_num(lo)}"
    return f"typical: {_fmt_num(lo)}-{_fmt_num(hi)}"


def _fmt_num(n: float) -> str:
    if n == int(n): return str(int(n))
    return f"{n:.2f}"


@router.get(
    "/anomaly/{entity_id}/cohort",
    response_model=KMCohortResponse,
    summary="Anomaly row + 3-5 typical rows of the same unit_type for "
            "side-by-side comparison. Lets a non-engineer SEE what "
            "stood out instead of reading 'rarity 1.00'.",
)
async def get_anomaly_cohort(
    entity_id: str,
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
    typical_count: int = Query(default=3, ge=1, le=10),
) -> KMCohortResponse:
    from fastapi import HTTPException

    # 1) The anomaly row itself.
    cur = await conn.execute(
        """
        SELECT ee.id::text, ee.unit_type, ee.rarity_score, ee.fields,
               ee.file_id::text, f.name
          FROM extracted_entities ee
          LEFT JOIN files f ON f.id = ee.file_id
         WHERE ee.workspace_id = %s AND ee.id = %s
        """,
        (workspace_id, entity_id),
    )
    row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="anomaly not found")
    a_id, a_unit_type, a_rarity, a_fields, a_file_id, a_file_name = row
    if a_unit_type is None:
        raise HTTPException(status_code=400, detail="not a sub-entity row")
    a_fields_dict: dict[str, Any] = dict(a_fields) if isinstance(a_fields, dict) else {}

    # 2) Cohort — all OTHER rows of the same unit_type. Sorted by
    #    rarity ASC so the "typical" sample is the LEAST anomalous.
    cur = await conn.execute(
        """
        SELECT id::text, fields, rarity_score
          FROM extracted_entities
         WHERE workspace_id = %s AND unit_type = %s AND id <> %s
         ORDER BY rarity_score ASC NULLS FIRST, id
        """,
        (workspace_id, a_unit_type, entity_id),
    )
    cohort_rows = await cur.fetchall()
    cohort_size = len(cohort_rows) + 1   # include the anomaly itself
    typical: list[dict[str, Any]] = []
    cohort_field_values: dict[str, list[Any]] = {}
    for cr in cohort_rows:
        cfields = dict(cr[1]) if isinstance(cr[1], dict) else {}
        if len(typical) < typical_count:
            typical.append(cfields)
        for k, v in cfields.items():
            cohort_field_values.setdefault(k, []).append(v)

    # 3) Union of columns — anomaly fields first (so the user reads
    #    the anomaly columns left-to-right), then any cohort-only ones.
    cols: list[str] = list(a_fields_dict.keys())
    for k in cohort_field_values.keys():
        if k not in cols:
            cols.append(k)

    # 4) Per-field outlier flags. Numeric: outside [min, max] of the
    #    cohort. Categorical: anomaly value not in the cohort set.
    field_stats: list[KMCohortField] = []
    for name in cols:
        val = a_fields_dict.get(name)
        cvals = [v for v in cohort_field_values.get(name, []) if v is not None]
        is_outlier = False
        summary: str | None = None
        if val is not None and cvals:
            if isinstance(val, (int, float)) and all(isinstance(v, (int, float)) for v in cvals):
                summary = _summarize_numeric([float(v) for v in cvals])
                lo, hi = min(cvals), max(cvals)
                if float(val) < float(lo) or float(val) > float(hi):
                    is_outlier = True
            else:
                # Categorical / textual — outlier if the exact value
                # never appears in the cohort.
                if str(val) not in {str(v) for v in cvals}:
                    is_outlier = True
                    unique_cohort = sorted({str(v)[:30] for v in cvals})[:5]
                    summary = f"typical: {', '.join(unique_cohort)}" if unique_cohort else None
        field_stats.append(KMCohortField(
            name=name, value=val,
            is_outlier=is_outlier, cohort_summary=summary,
        ))

    # 5) Rarity → plain-English label.
    r = float(a_rarity or 0.0)
    if r >= 2.0:    rarity_label = f"Very unusual · {r:.2f} (1 of {cohort_size})"
    elif r >= 1.5: rarity_label = f"Quite unusual · {r:.2f}"
    elif r >= 1.0: rarity_label = f"Mildly unusual · {r:.2f}"
    elif r >= 0.8: rarity_label = f"Slightly unusual · {r:.2f}"
    else:          rarity_label = f"Within normal range · {r:.2f}"

    return KMCohortResponse(
        anomaly_id=str(a_id),
        unit_type=str(a_unit_type),
        rarity_score=r,
        rarity_label=rarity_label,
        file_id=(str(a_file_id) if a_file_id is not None else None),
        file_name=(str(a_file_name) if a_file_name is not None else None),
        cohort_size=cohort_size,
        columns=cols,
        anomaly_row=a_fields_dict,
        anomaly_field_stats=field_stats,
        typical_rows=typical,
    )


# ===========================================================================
# 🕸 Entities — cross-doc canonical entity browser
# ===========================================================================


class KMEntity(BaseModel):
    """One canonical entity row for the Entities tab."""
    id: str
    canonical_name: str
    entity_type: str
    mention_count: int
    n_relationships: int = 0
    n_files: int = 0


class KMEntityListResponse(BaseModel):
    items: list[KMEntity] = Field(default_factory=list)
    total: int = 0
    has_more: bool = False


class KMEntityNeighbor(BaseModel):
    """One 1-hop neighbor of a canonical entity. Source of edge is
    either a `relationship` triple (with predicate text) or a
    `co_mention` (sub-doc proximity) or a graph_edges row."""
    entity_id: str
    canonical_name: str
    entity_type: str
    edge_kind: str        # 'relationship' | 'co_mention'
    direction: str        # 'out' (this→other) | 'in' (other→this) | 'undirected'
    predicate: str | None = None
    weight: float = 1.0
    n_evidence: int = 1


class KMEntityFile(BaseModel):
    file_id: str
    file_name: str
    n_mentions: int


class KMEntityDetailResponse(BaseModel):
    """Side-panel payload for one canonical entity — its 1-hop
    neighborhood + files that mention it."""
    entity: KMEntity
    neighbors: list[KMEntityNeighbor] = Field(default_factory=list)
    files: list[KMEntityFile] = Field(default_factory=list)


@router.get(
    "/entities",
    response_model=KMEntityListResponse,
    summary="Paginated list of canonical entities for the Entities tab.",
)
async def get_entities(
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
    entity_type: str | None = Query(default=None),
    q: str | None = Query(default=None, description="Substring of canonical_name"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> KMEntityListResponse:
    """Browse the cross-doc canonical entity layer. Each row has
    counts of how many relationships + files reference it so the UI
    can show the most-connected entities first."""
    where = ["ce.workspace_id = %s"]
    params: list[Any] = [workspace_id]
    if entity_type:
        where.append("ce.entity_type = %s")
        params.append(entity_type)
    if q:
        where.append("ce.canonical_name ILIKE %s")
        params.append(f"%{q}%")
    where_sql = " AND ".join(where)

    # Total for pagination
    cur = await conn.execute(
        f"SELECT count(*) FROM canonical_entities ce WHERE {where_sql}",
        tuple(params),
    )
    total = int((await cur.fetchone())[0])

    # Page of rows + counts. LEFT JOIN to relationships + mention map
    # so 0-relationship entities still show up.
    cur = await conn.execute(
        f"""
        SELECT ce.id::text, ce.canonical_name, ce.entity_type,
               COALESCE(ce.mention_count, 0)::int AS mc,
               (SELECT count(*) FROM relationships r
                 WHERE r.workspace_id = ce.workspace_id
                   AND (r.subject_entity_id = ce.id OR r.object_entity_id = ce.id)
               ) AS n_rels,
               (SELECT count(DISTINCT em.file_id)
                  FROM extracted_mentions em
                  JOIN mention_to_entity me ON me.mention_id = em.id
                 WHERE me.workspace_id = ce.workspace_id AND me.entity_id = ce.id
               ) AS n_files
          FROM canonical_entities ce
         WHERE {where_sql}
         ORDER BY ce.mention_count DESC NULLS LAST, ce.canonical_name ASC
         LIMIT %s OFFSET %s
        """,
        (*params, limit, offset),
    )
    rows = await cur.fetchall()
    items = [
        KMEntity(
            id=str(r[0]), canonical_name=str(r[1]),
            entity_type=str(r[2]), mention_count=int(r[3] or 0),
            n_relationships=int(r[4] or 0),
            n_files=int(r[5] or 0),
        )
        for r in rows
    ]
    return KMEntityListResponse(
        items=items, total=total,
        has_more=(offset + len(items)) < total,
    )


@router.get(
    "/entities/{entity_id}",
    response_model=KMEntityDetailResponse,
    summary="Side-panel payload for one entity — neighborhood + files.",
)
async def get_entity_detail(
    entity_id: str,
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
    neighbor_limit: int = Query(default=30, ge=1, le=200),
) -> KMEntityDetailResponse:
    # 1) the entity itself
    cur = await conn.execute(
        """
        SELECT id::text, canonical_name, entity_type,
               COALESCE(mention_count, 0)::int
          FROM canonical_entities
         WHERE workspace_id = %s AND id = %s
        """,
        (workspace_id, entity_id),
    )
    row = await cur.fetchone()
    if row is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="entity not found")
    entity = KMEntity(
        id=str(row[0]), canonical_name=str(row[1]),
        entity_type=str(row[2]), mention_count=int(row[3] or 0),
    )

    # 2) Neighbors from `relationships` (typed predicate edges) +
    # `graph_edges` co_mentions (proximity edges with no predicate).
    # Both joined to canonical_entities to render the other end.
    cur = await conn.execute(
        """
        WITH rel_out AS (
          SELECT r.object_entity_id AS other_id,
                 'relationship' AS edge_kind,
                 'out' AS direction,
                 r.predicate, COALESCE(r.confidence, 1.0) AS weight,
                 COALESCE(r.n_evidence, 1) AS n_evidence
            FROM relationships r
           WHERE r.workspace_id = %s AND r.subject_entity_id = %s
        ),
        rel_in AS (
          SELECT r.subject_entity_id AS other_id,
                 'relationship' AS edge_kind,
                 'in' AS direction,
                 r.predicate, COALESCE(r.confidence, 1.0) AS weight,
                 COALESCE(r.n_evidence, 1) AS n_evidence
            FROM relationships r
           WHERE r.workspace_id = %s AND r.object_entity_id = %s
        ),
        co AS (
          SELECT CASE WHEN ge.src_entity_id = %s
                      THEN ge.dst_entity_id
                      ELSE ge.src_entity_id END AS other_id,
                 ge.edge_kind, 'undirected' AS direction,
                 NULL::text AS predicate, ge.weight,
                 1 AS n_evidence
            FROM graph_edges ge
           WHERE ge.workspace_id = %s
             AND (ge.src_entity_id = %s OR ge.dst_entity_id = %s)
             AND ge.edge_kind = 'co_mention'
        )
        SELECT all_edges.other_id::text, ce.canonical_name, ce.entity_type,
               all_edges.edge_kind, all_edges.direction,
               all_edges.predicate, all_edges.weight, all_edges.n_evidence
          FROM (
            SELECT * FROM rel_out
            UNION ALL SELECT * FROM rel_in
            UNION ALL SELECT * FROM co
          ) all_edges
          JOIN canonical_entities ce ON ce.id = all_edges.other_id
         ORDER BY (all_edges.edge_kind = 'relationship') DESC,
                  all_edges.weight DESC
         LIMIT %s
        """,
        (
            workspace_id, entity_id,
            workspace_id, entity_id,
            entity_id,
            workspace_id, entity_id, entity_id,
            neighbor_limit,
        ),
    )
    neighbors = [
        KMEntityNeighbor(
            entity_id=str(r[0]),
            canonical_name=str(r[1]),
            entity_type=str(r[2]),
            edge_kind=str(r[3]),
            direction=str(r[4]),
            predicate=(str(r[5]) if r[5] is not None else None),
            weight=float(r[6] or 0.0),
            n_evidence=int(r[7] or 1),
        )
        for r in await cur.fetchall()
    ]

    # 3) Files that mention this entity (top 20 by mention count).
    cur = await conn.execute(
        """
        SELECT f.id::text, f.name, count(em.id)::int
          FROM extracted_mentions em
          JOIN mention_to_entity me ON me.mention_id = em.id
          JOIN files f ON f.id = em.file_id
         WHERE me.workspace_id = %s AND me.entity_id = %s
           AND f.lifecycle_state <> 'deleted'
         GROUP BY f.id, f.name
         ORDER BY count(em.id) DESC
         LIMIT 20
        """,
        (workspace_id, entity_id),
    )
    files = [
        KMEntityFile(file_id=str(r[0]), file_name=str(r[1]), n_mentions=int(r[2]))
        for r in await cur.fetchall()
    ]

    return KMEntityDetailResponse(
        entity=entity, neighbors=neighbors, files=files,
    )
