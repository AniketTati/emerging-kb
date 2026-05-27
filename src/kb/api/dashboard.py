"""B7 / WA-14 — Dashboard backend (architecture §10e).

Two read-only endpoints power the Dashboard UI page:

  GET /dashboard/summary         — workspace-wide counters + sparkline data
                                    (files by lifecycle, doc_type, doc_status;
                                    queries by mode, verdict, last 24h count;
                                    open conflicts; open corrections;
                                    active regressions; audit chain status)

  GET /dashboard/needs-attention — unified list of items needing review:
                                    open conflicts + open corrections +
                                    low-confidence chats + low-authority
                                    files. Items share a common envelope
                                    so the UI can render them in one
                                    sortable table.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from kb.api.deps import current_workspace_id, kb_app_connection
from kb.db.pool import Connection


router = APIRouter(tags=["dashboard"])


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class CountByLabel(BaseModel):
    label: str
    count: int


class DashboardSummary(BaseModel):
    workspace_id: str
    # Files
    files_total: int = 0
    files_by_lifecycle: list[CountByLabel] = Field(default_factory=list)
    files_by_doc_type: list[CountByLabel] = Field(default_factory=list)
    files_by_doc_status: list[CountByLabel] = Field(default_factory=list)
    files_low_authority: int = 0   # source_authority < 0.5
    # Queries
    queries_total: int = 0
    queries_last_24h: int = 0
    queries_by_mode: list[CountByLabel] = Field(default_factory=list)
    queries_by_faithfulness: list[CountByLabel] = Field(default_factory=list)
    queries_refused: int = 0
    queries_low_confidence: int = 0
    # Conflicts (B2)
    conflicts_open: int = 0
    conflicts_resolved: int = 0
    # Corrections (B6b)
    corrections_open: int = 0
    corrections_fixing: int = 0
    # Regression set (B6b)
    regressions_active: int = 0
    # Sessions (B6a)
    sessions_active_24h: int = 0
    # Audit chain (B5)
    audit_log_total_rows: int = 0


class NeedsAttentionItem(BaseModel):
    kind: str            # 'conflict' | 'correction' | 'low_confidence_chat' | 'low_authority_file'
    id: str
    title: str
    severity: str        # 'blocker' | 'important' | 'minor'
    created_at: str
    payload: dict = Field(default_factory=dict)


class NeedsAttentionResponse(BaseModel):
    items: list[NeedsAttentionItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# /dashboard/summary
# ---------------------------------------------------------------------------


async def _count_group(
    conn: Connection, sql: str, params: tuple,
) -> list[CountByLabel]:
    """Helper: run a `SELECT label, count(*) GROUP BY label` style query
    and return [{label, count}, …]. Skips NULL labels."""
    cur = await conn.execute(sql, params)
    rows = await cur.fetchall()
    return [
        CountByLabel(label=str(r[0]) if r[0] is not None else "(unknown)",
                     count=int(r[1] or 0))
        for r in rows
    ]


async def _scalar(conn: Connection, sql: str, params: tuple) -> int:
    cur = await conn.execute(sql, params)
    row = await cur.fetchone()
    return int(row[0] or 0) if row else 0


@router.get(
    "/dashboard/summary",
    response_model=DashboardSummary,
    summary="Workspace-wide counters for the Dashboard page",
)
async def get_dashboard_summary(
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> DashboardSummary:
    # Files
    files_total = await _scalar(
        conn,
        "SELECT COUNT(*)::int FROM files WHERE workspace_id = %s "
        "AND lifecycle_state <> 'deleted'",
        (workspace_id,),
    )
    files_by_lifecycle = await _count_group(
        conn,
        "SELECT lifecycle_state, COUNT(*)::int FROM files "
        "WHERE workspace_id = %s AND lifecycle_state <> 'deleted' "
        "GROUP BY lifecycle_state ORDER BY COUNT(*) DESC",
        (workspace_id,),
    )
    files_by_doc_type = await _count_group(
        conn,
        "SELECT inferred_doc_type, COUNT(*)::int FROM files "
        "WHERE workspace_id = %s AND lifecycle_state <> 'deleted' "
        "GROUP BY inferred_doc_type ORDER BY COUNT(*) DESC",
        (workspace_id,),
    )
    files_by_doc_status = await _count_group(
        conn,
        "SELECT doc_status, COUNT(*)::int FROM files "
        "WHERE workspace_id = %s AND lifecycle_state <> 'deleted' "
        "GROUP BY doc_status ORDER BY COUNT(*) DESC",
        (workspace_id,),
    )
    files_low_authority = await _scalar(
        conn,
        "SELECT COUNT(*)::int FROM files "
        "WHERE workspace_id = %s AND lifecycle_state <> 'deleted' "
        "AND source_authority < 0.5",
        (workspace_id,),
    )

    # Queries
    queries_total = await _scalar(
        conn,
        "SELECT COUNT(*)::int FROM query_log WHERE workspace_id = %s",
        (workspace_id,),
    )
    queries_last_24h = await _scalar(
        conn,
        "SELECT COUNT(*)::int FROM query_log "
        "WHERE workspace_id = %s AND created_at > NOW() - INTERVAL '24 hours'",
        (workspace_id,),
    )
    queries_by_mode = await _count_group(
        conn,
        "SELECT mode, COUNT(*)::int FROM query_log "
        "WHERE workspace_id = %s "
        "GROUP BY mode ORDER BY COUNT(*) DESC",
        (workspace_id,),
    )
    queries_by_faithfulness = await _count_group(
        conn,
        "SELECT faithfulness_verdict, COUNT(*)::int FROM query_log "
        "WHERE workspace_id = %s "
        "GROUP BY faithfulness_verdict ORDER BY COUNT(*) DESC",
        (workspace_id,),
    )
    queries_refused = await _scalar(
        conn,
        "SELECT COUNT(*)::int FROM query_log "
        "WHERE workspace_id = %s AND refused = true",
        (workspace_id,),
    )
    queries_low_confidence = await _scalar(
        conn,
        "SELECT COUNT(*)::int FROM query_log "
        "WHERE workspace_id = %s AND faithfulness_verdict = 'low_confidence'",
        (workspace_id,),
    )

    # Conflicts
    conflicts_open = await _scalar(
        conn,
        "SELECT COUNT(*)::int FROM fact_conflicts "
        "WHERE workspace_id = %s AND resolution = 'unresolved'",
        (workspace_id,),
    )
    conflicts_resolved = await _scalar(
        conn,
        "SELECT COUNT(*)::int FROM fact_conflicts "
        "WHERE workspace_id = %s AND resolution <> 'unresolved'",
        (workspace_id,),
    )

    # Corrections
    corrections_open = await _scalar(
        conn,
        "SELECT COUNT(*)::int FROM corrections "
        "WHERE workspace_id = %s AND status IN ('open', 'triaged')",
        (workspace_id,),
    )
    corrections_fixing = await _scalar(
        conn,
        "SELECT COUNT(*)::int FROM corrections "
        "WHERE workspace_id = %s AND status = 'fixing'",
        (workspace_id,),
    )

    # Regression set
    regressions_active = await _scalar(
        conn,
        "SELECT COUNT(*)::int FROM regression_set "
        "WHERE workspace_id = %s AND active = true",
        (workspace_id,),
    )

    # Sessions
    sessions_active_24h = await _scalar(
        conn,
        "SELECT COUNT(*)::int FROM chat_sessions "
        "WHERE workspace_id = %s "
        "AND last_active_at > NOW() - INTERVAL '24 hours'",
        (workspace_id,),
    )

    # Audit log
    audit_log_total_rows = await _scalar(
        conn,
        "SELECT COUNT(*)::int FROM audit_log WHERE workspace_id = %s",
        (workspace_id,),
    )

    return DashboardSummary(
        workspace_id=workspace_id,
        files_total=files_total,
        files_by_lifecycle=files_by_lifecycle,
        files_by_doc_type=files_by_doc_type,
        files_by_doc_status=files_by_doc_status,
        files_low_authority=files_low_authority,
        queries_total=queries_total,
        queries_last_24h=queries_last_24h,
        queries_by_mode=queries_by_mode,
        queries_by_faithfulness=queries_by_faithfulness,
        queries_refused=queries_refused,
        queries_low_confidence=queries_low_confidence,
        conflicts_open=conflicts_open,
        conflicts_resolved=conflicts_resolved,
        corrections_open=corrections_open,
        corrections_fixing=corrections_fixing,
        regressions_active=regressions_active,
        sessions_active_24h=sessions_active_24h,
        audit_log_total_rows=audit_log_total_rows,
    )


# ---------------------------------------------------------------------------
# /dashboard/needs-attention
# ---------------------------------------------------------------------------


_LOW_AUTHORITY_THRESHOLD: float = 0.5


@router.get(
    "/dashboard/needs-attention",
    response_model=NeedsAttentionResponse,
    summary="Unified list of items needing admin review",
)
async def get_needs_attention(
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
    limit: int = Query(default=50, ge=1, le=500),
) -> NeedsAttentionResponse:
    items: list[NeedsAttentionItem] = []

    # 1) Open conflicts
    cur = await conn.execute(
        "SELECT id::text, entity_id::text, predicate, observed_at "
        "FROM fact_conflicts "
        "WHERE workspace_id = %s AND resolution = 'unresolved' "
        "ORDER BY observed_at DESC LIMIT %s",
        (workspace_id, limit),
    )
    for r in await cur.fetchall():
        # entity_id can be NULL on doc-internal conflicts (e.g. one
        # row's `service_location.in_scope` disagrees with another in
        # the SAME doc). Format the title without slicing None.
        entity_label = (
            f"entity {r[1][:8]}..." if r[1] is not None
            else "(within-doc)"
        )
        items.append(NeedsAttentionItem(
            kind="conflict",
            id=str(r[0]),
            title=f"Conflict on '{r[2]}' for {entity_label}",
            severity="important",
            created_at=r[3].isoformat() if hasattr(r[3], "isoformat") else str(r[3]),
            payload={
                "entity_id": str(r[1]) if r[1] is not None else None,
                "predicate": str(r[2]),
            },
        ))

    # 2) Open + fixing corrections
    cur = await conn.execute(
        "SELECT id::text, scope, severity, status, reason, created_at "
        "FROM corrections "
        "WHERE workspace_id = %s AND status IN ('open', 'triaged', 'fixing') "
        "ORDER BY created_at DESC LIMIT %s",
        (workspace_id, limit),
    )
    for r in await cur.fetchall():
        items.append(NeedsAttentionItem(
            kind="correction",
            id=str(r[0]),
            title=f"Correction ({r[1]}): {(r[4] or '')[:80]}",
            severity=str(r[2]),
            created_at=r[5].isoformat() if hasattr(r[5], "isoformat") else str(r[5]),
            payload={"scope": str(r[1]), "status": str(r[3])},
        ))

    # 3) Low-confidence chats
    cur = await conn.execute(
        "SELECT id::text, query, faithfulness_score, created_at "
        "FROM query_log "
        "WHERE workspace_id = %s AND faithfulness_verdict = 'low_confidence' "
        "ORDER BY created_at DESC LIMIT %s",
        (workspace_id, limit),
    )
    for r in await cur.fetchall():
        items.append(NeedsAttentionItem(
            kind="low_confidence_chat",
            id=str(r[0]),
            title=f"Low-confidence answer: {(r[1] or '')[:80]}",
            severity="minor",
            created_at=r[3].isoformat() if hasattr(r[3], "isoformat") else str(r[3]),
            payload={"faithfulness_score": float(r[2]) if r[2] is not None else None},
        ))

    # 4) Low-authority files (< 0.5)
    cur = await conn.execute(
        "SELECT id::text, name, source_authority, source_authority_reason, created_at "
        "FROM files "
        "WHERE workspace_id = %s AND lifecycle_state <> 'deleted' "
        "AND source_authority < %s "
        "ORDER BY source_authority ASC, created_at DESC LIMIT %s",
        (workspace_id, _LOW_AUTHORITY_THRESHOLD, limit),
    )
    for r in await cur.fetchall():
        items.append(NeedsAttentionItem(
            kind="low_authority_file",
            id=str(r[0]),
            title=f"Low-authority file: {str(r[1])[:80]} "
                  f"(source_authority={float(r[2]):.2f})",
            severity="minor",
            created_at=r[4].isoformat() if hasattr(r[4], "isoformat") else str(r[4]),
            payload={
                "name": str(r[1]),
                "source_authority": float(r[2]) if r[2] is not None else None,
                "reason": r[3],
            },
        ))

    # Order newest first across all kinds.
    items.sort(key=lambda i: i.created_at, reverse=True)
    return NeedsAttentionResponse(items=items[:limit])
