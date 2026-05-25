"""B2 / WA-6 — Conflicts HTTP surface.

Endpoints (per Design 2 §"UI surface"):

  GET  /conflicts?resolution=unresolved&limit=
       Dashboard Needs-attention list.

  GET  /entities/{entity_id}/conflicts
       Surfaces the entity's open conflicts in Doc Detail / entity profile.

  POST /conflicts/{conflict_id}/resolve
       Admin resolution from the Dashboard.

  POST /files/{file_id}/source-authority
       Doc Detail panel override (set authority + reason).

  POST /files/{file_id}/doc-status
       Set doc_status (mark superseded/draft/archived/retracted/live).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from kb.api.deps import current_workspace_id, kb_app_connection
from kb.api.errors import BadRequestError
from kb.db.pool import Connection
from kb.domain.conflicts import (
    DOC_STATUSES,
    RESOLUTIONS,
    ConflictRecord,
    insert_conflict,
    mark_conflict_resolved,
    read_conflict_by_id,
    read_conflicts_for_entity,
    read_conflicts_for_workspace,
    read_file_authority,
    set_doc_status,
    set_source_authority_override,
)


router = APIRouter(tags=["conflicts"])


# ---------------------------------------------------------------------------
# Response + request models
# ---------------------------------------------------------------------------


class ConflictOut(BaseModel):
    id: str
    entity_id: str
    predicate: str
    observed_at: str
    evidence: list[dict] = Field(default_factory=list)
    resolution: str
    resolved_value: str | None = None
    resolved_doc_id: str | None = None
    notes: str | None = None
    resolved_by: str | None = None
    resolved_at: str | None = None


class ConflictListResponse(BaseModel):
    items: list[ConflictOut] = Field(default_factory=list)


class ConflictResolveRequest(BaseModel):
    resolution: str
    resolved_value: str | None = None
    resolved_doc_id: str | None = None
    resolved_by: str | None = None
    notes: str | None = None


class SourceAuthorityRequest(BaseModel):
    authority: float = Field(ge=0.0, le=1.0)
    reason: str


class DocStatusRequest(BaseModel):
    doc_status: str


class FileAuthorityOut(BaseModel):
    source_authority: float
    source_authority_reason: str | None = None
    doc_status: str


# ---------------------------------------------------------------------------
# Conversions
# ---------------------------------------------------------------------------


def _to_response(c: ConflictRecord) -> ConflictOut:
    return ConflictOut(
        id=c.id,
        entity_id=c.entity_id,
        predicate=c.predicate,
        observed_at=c.observed_at,
        evidence=c.evidence,
        resolution=c.resolution,
        resolved_value=c.resolved_value,
        resolved_doc_id=c.resolved_doc_id,
        notes=c.notes,
        resolved_by=c.resolved_by,
        resolved_at=c.resolved_at,
    )


# ---------------------------------------------------------------------------
# Conflicts endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/conflicts",
    response_model=ConflictListResponse,
    summary="List fact conflicts in this workspace",
)
async def get_conflicts(
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
    resolution: str | None = Query(default=None, description=f"Filter. Allowed: {', '.join(RESOLUTIONS)}"),
    limit: int = Query(default=200, ge=1, le=500),
) -> ConflictListResponse:
    if resolution is not None and resolution not in RESOLUTIONS:
        raise BadRequestError(
            f"resolution must be one of {RESOLUTIONS} (got {resolution!r})"
        )
    rows = await read_conflicts_for_workspace(
        conn, workspace_id=workspace_id, resolution=resolution, limit=limit,
    )
    return ConflictListResponse(items=[_to_response(r) for r in rows])


@router.get(
    "/entities/{entity_id}/conflicts",
    response_model=ConflictListResponse,
    summary="List open conflicts involving an entity",
)
async def get_entity_conflicts(
    entity_id: str,
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> ConflictListResponse:
    rows = await read_conflicts_for_entity(
        conn, workspace_id=workspace_id, entity_id=entity_id,
    )
    return ConflictListResponse(items=[_to_response(r) for r in rows])


@router.post(
    "/conflicts/{conflict_id}/resolve",
    response_model=ConflictOut,
    summary="Admin resolution for a conflict",
)
async def post_resolve_conflict(
    conflict_id: str,
    body: ConflictResolveRequest,
    workspace_id: Annotated[str, Depends(current_workspace_id)],  # noqa: ARG001
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> ConflictOut:
    if body.resolution not in RESOLUTIONS:
        raise BadRequestError(f"resolution must be one of {RESOLUTIONS}")
    changed = await mark_conflict_resolved(
        conn,
        conflict_id=conflict_id,
        resolution=body.resolution,
        resolved_value=body.resolved_value,
        resolved_doc_id=body.resolved_doc_id,
        resolved_by=body.resolved_by,
        notes=body.notes,
    )
    if not changed:
        raise HTTPException(status_code=404, detail="conflict not found")
    fresh = await read_conflict_by_id(conn, conflict_id=conflict_id)
    if fresh is None:
        raise HTTPException(status_code=404, detail="conflict not found after resolve")
    return _to_response(fresh)


# ---------------------------------------------------------------------------
# Files: authority + status setters
# ---------------------------------------------------------------------------


@router.get(
    "/files/{file_id}/authority",
    response_model=FileAuthorityOut,
    summary="Read a file's source_authority + reason + doc_status",
)
async def get_file_authority(
    file_id: str,
    workspace_id: Annotated[str, Depends(current_workspace_id)],  # noqa: ARG001
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> FileAuthorityOut:
    info = await read_file_authority(conn, file_id=file_id)
    if info is None:
        raise HTTPException(status_code=404, detail="file not found")
    authority, reason, status = info
    return FileAuthorityOut(
        source_authority=authority,
        source_authority_reason=reason,
        doc_status=status,
    )


@router.post(
    "/files/{file_id}/source-authority",
    response_model=FileAuthorityOut,
    summary="Admin override: set source_authority + reason for a file",
)
async def post_set_source_authority(
    file_id: str,
    body: SourceAuthorityRequest,
    workspace_id: Annotated[str, Depends(current_workspace_id)],  # noqa: ARG001
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> FileAuthorityOut:
    if not body.reason.strip():
        raise BadRequestError("reason must be non-empty (audit trail)")
    changed = await set_source_authority_override(
        conn, file_id=file_id, authority=body.authority, reason=body.reason,
    )
    if not changed:
        raise HTTPException(status_code=404, detail="file not found")
    info = await read_file_authority(conn, file_id=file_id)
    assert info is not None
    authority, reason, status = info
    return FileAuthorityOut(
        source_authority=authority,
        source_authority_reason=reason,
        doc_status=status,
    )


@router.post(
    "/files/{file_id}/doc-status",
    response_model=FileAuthorityOut,
    summary="Set a file's doc_status (live / superseded / draft / archived / retracted)",
)
async def post_set_doc_status(
    file_id: str,
    body: DocStatusRequest,
    workspace_id: Annotated[str, Depends(current_workspace_id)],  # noqa: ARG001
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> FileAuthorityOut:
    if body.doc_status not in DOC_STATUSES:
        raise BadRequestError(
            f"doc_status must be one of {DOC_STATUSES} (got {body.doc_status!r})"
        )
    changed = await set_doc_status(conn, file_id=file_id, new_status=body.doc_status)
    if not changed:
        raise HTTPException(status_code=404, detail="file not found")
    info = await read_file_authority(conn, file_id=file_id)
    assert info is not None
    authority, reason, status = info
    return FileAuthorityOut(
        source_authority=authority,
        source_authority_reason=reason,
        doc_status=status,
    )
