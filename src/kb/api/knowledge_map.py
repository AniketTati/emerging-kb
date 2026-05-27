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
                   AND ee.unit_type IS NULL) AS file_count
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
        sid, name, desc, created, file_count = r
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
