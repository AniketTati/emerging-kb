"""Phase 9 + B5 / WA-11 — Audit endpoints.

GET /audit              — paginated query_log list (Phase 9)
GET /audit-log          — paginated audit_log list (B5)
GET /audit-log/integrity — verify hash-chain integrity for the workspace (B5)
"""

from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from kb.api.deps import current_workspace_id, kb_app_connection
from kb.api.errors import BadRequestError
from kb.db.pool import Connection
from kb.domain.audit_chain import (
    ChainWalkResult,
    read_audit_log,
    walk_chain,
)


_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200
_ANSWER_TRUNCATE_AT = 500  # decision #6

router = APIRouter(tags=["audit"])


class AuditEntry(BaseModel):
    id: str
    created_at: str
    endpoint: str
    query: str
    mode: str
    crag_score: float | None
    refused: bool
    refusal_reason: str | None
    answer: str | None
    latency_ms: int | None
    model_id: str | None


class AuditResponse(BaseModel):
    items: list[AuditEntry] = Field(default_factory=list)
    next_cursor: str | None = None


def _encode_cursor(created_at: datetime, row_id: str) -> str:
    payload = {"created_at": created_at.isoformat(), "id": row_id}
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def _decode_cursor(raw: str) -> tuple[datetime, str]:
    try:
        data = json.loads(base64.urlsafe_b64decode(raw.encode()).decode())
        return datetime.fromisoformat(data["created_at"]), str(data["id"])
    except Exception as exc:  # noqa: BLE001
        raise BadRequestError(f"invalid cursor: {exc}") from exc


@router.get(
    "/audit",
    summary="List past /search and /chat calls (paginated, cursor-based, newest first)",
    response_model=AuditResponse,
    responses={
        200: {"description": "Paginated list of past queries for the workspace"},
        400: {"description": "Invalid cursor or limit > 200"},
    },
)
async def get_audit(
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
    cursor: str | None = Query(default=None, description="Opaque cursor from prior response's next_cursor"),
    limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
) -> AuditResponse:
    if limit > _MAX_LIMIT:
        raise BadRequestError(f"limit must be <= {_MAX_LIMIT} (got {limit})")

    cursor_pair: tuple[datetime, str] | None = (
        _decode_cursor(cursor) if cursor else None
    )

    if cursor_pair is None:
        sql = """
            SELECT id, created_at, endpoint, query, mode, crag_score,
                   refused, refusal_reason, answer, latency_ms, model_id
              FROM query_log
             WHERE workspace_id = %s
          ORDER BY created_at DESC, id DESC
             LIMIT %s
        """
        params: tuple = (workspace_id, limit + 1)
    else:
        ts, last_id = cursor_pair
        sql = """
            SELECT id, created_at, endpoint, query, mode, crag_score,
                   refused, refusal_reason, answer, latency_ms, model_id
              FROM query_log
             WHERE workspace_id = %s
               AND (created_at, id) < (%s, %s)
          ORDER BY created_at DESC, id DESC
             LIMIT %s
        """
        params = (workspace_id, ts, last_id, limit + 1)

    cur = await conn.execute(sql, params)
    rows = await cur.fetchall()

    has_next = len(rows) > limit
    rows = rows[:limit]

    items: list[AuditEntry] = []
    for row in rows:
        answer_raw = row[8]
        if answer_raw is not None and len(answer_raw) > _ANSWER_TRUNCATE_AT:
            answer_raw = answer_raw[:_ANSWER_TRUNCATE_AT]
        items.append(
            AuditEntry(
                id=str(row[0]),
                created_at=row[1].isoformat(),
                endpoint=str(row[2]),
                query=str(row[3]),
                mode=str(row[4]),
                crag_score=float(row[5]) if row[5] is not None else None,
                refused=bool(row[6]),
                refusal_reason=row[7],
                answer=answer_raw,
                latency_ms=int(row[9]) if row[9] is not None else None,
                model_id=row[10],
            )
        )

    next_cursor: str | None = None
    if has_next and items:
        last = items[-1]
        # We need the raw datetime for the cursor; re-parse from the iso string.
        next_cursor = _encode_cursor(
            datetime.fromisoformat(last.created_at), last.id
        )

    return AuditResponse(items=items, next_cursor=next_cursor)


# ===========================================================================
# B5 — audit_log + integrity endpoints
# ===========================================================================


class AuditLogItem(BaseModel):
    id: str
    workspace_id: str
    created_at: str
    actor: str
    action: str
    entity_type: str | None = None
    entity_id: str | None = None
    query_id: str | None = None
    payload: dict
    prev_hash: str
    hash: str


class AuditLogListResponse(BaseModel):
    items: list[AuditLogItem] = Field(default_factory=list)


class IntegrityResponse(BaseModel):
    ok: bool
    workspace_id: str
    total_rows: int
    broken_at_row_id: str | None = None
    broken_at_position: int | None = None
    expected_hash: str | None = None
    actual_hash: str | None = None
    notes: str | None = None


@router.get(
    "/audit-log",
    summary="List service-level audit events (hash-chained) for this workspace",
    response_model=AuditLogListResponse,
)
async def get_audit_log(
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
    limit: int = Query(default=100, ge=1, le=500),
) -> AuditLogListResponse:
    rows = await read_audit_log(conn, workspace_id=workspace_id, limit=limit)
    return AuditLogListResponse(items=[
        AuditLogItem(
            id=r.id, workspace_id=r.workspace_id, created_at=r.created_at,
            actor=r.actor, action=r.action,
            entity_type=r.entity_type, entity_id=r.entity_id,
            query_id=r.query_id, payload=r.payload,
            prev_hash=r.prev_hash, hash=r.hash,
        )
        for r in rows
    ])


@router.get(
    "/audit-log/integrity",
    summary="Walk the audit_log SHA-256 chain for this workspace; report the first divergence",
    response_model=IntegrityResponse,
)
async def get_audit_log_integrity(
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
    limit: int = Query(default=5000, ge=1, le=50000),
) -> IntegrityResponse:
    result: ChainWalkResult = await walk_chain(
        conn, workspace_id=workspace_id, limit=limit,
    )
    return IntegrityResponse(
        ok=result.ok,
        workspace_id=result.workspace_id,
        total_rows=result.total_rows,
        broken_at_row_id=result.broken_at_row_id,
        broken_at_position=result.broken_at_position,
        expected_hash=result.expected_hash,
        actual_hash=result.actual_hash,
        notes=result.notes,
    )
