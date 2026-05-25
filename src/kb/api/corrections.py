"""B6b / WA-13 — Corrections + overrides HTTP surface.

  POST /corrections                    — submit feedback (auto-routes)
  GET  /corrections                    — list (filter by scope/status)
  GET  /corrections/{id}               — fetch one
  PATCH /corrections/{id}              — admin status update
  GET  /entity-overrides               — active overrides for this workspace
  GET  /schema-field-overrides         — active overrides for this workspace
  GET  /regression-set                 — active regression entries
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from kb.api.deps import current_workspace_id, kb_app_connection
from kb.api.errors import BadRequestError
from kb.db.pool import Connection
from kb.domain.corrections import (
    CORRECTION_SCOPES,
    CORRECTION_SEVERITIES,
    CORRECTION_STATUSES,
    insert_correction,
    list_active_entity_overrides,
    list_active_regressions,
    list_active_schema_field_overrides,
    list_corrections,
    read_correction,
    route_correction,
    update_correction_status,
)


router = APIRouter(tags=["corrections"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class CorrectionRequest(BaseModel):
    scope: str
    target: dict = Field(default_factory=dict)
    observed_value: str | None = None
    correct_value: str | None = None
    reason: str | None = None
    severity: str = "important"
    user_id: str | None = None
    audit_query_id: str | None = None


class CorrectionOut(BaseModel):
    id: str
    workspace_id: str
    user_id: str | None
    scope: str
    target: dict
    observed_value: str | None
    correct_value: str | None
    reason: str | None
    severity: str
    status: str
    resolution: dict | None
    audit_query_id: str | None
    created_at: str
    resolved_at: str | None


class CorrectionsListResponse(BaseModel):
    items: list[CorrectionOut] = Field(default_factory=list)


class CorrectionCreateResponse(BaseModel):
    id: str
    status: str
    resolution: dict | None
    entity_override_id: str | None = None
    schema_field_override_id: str | None = None
    regression_entry_id: str | None = None
    notes: str | None = None


class CorrectionUpdateRequest(BaseModel):
    status: str
    resolution: dict | None = None


class EntityOverrideOut(BaseModel):
    id: str
    workspace_id: str
    rule_type: str
    entity_a: str | None
    entity_b: str | None
    rename_to: str | None
    reason: str | None
    active: bool
    correction_id: str | None
    created_at: str


class EntityOverridesListResponse(BaseModel):
    items: list[EntityOverrideOut] = Field(default_factory=list)


class SchemaFieldOverrideOut(BaseModel):
    id: str
    workspace_id: str
    field_path: str
    override_kind: str
    details: dict
    reason: str | None
    active: bool
    correction_id: str | None
    created_at: str


class SchemaFieldOverridesListResponse(BaseModel):
    items: list[SchemaFieldOverrideOut] = Field(default_factory=list)


class RegressionEntryOut(BaseModel):
    id: str
    workspace_id: str
    source_correction_id: str | None
    query_text: str
    expected_facts: dict
    implicated_docs: list[str]
    severity: str
    active: bool
    fail_count: int
    created_at: str


class RegressionListResponse(BaseModel):
    items: list[RegressionEntryOut] = Field(default_factory=list)


def _correction_to_out(c) -> CorrectionOut:
    return CorrectionOut(
        id=c.id,
        workspace_id=c.workspace_id,
        user_id=c.user_id,
        scope=c.scope,
        target=c.target,
        observed_value=c.observed_value,
        correct_value=c.correct_value,
        reason=c.reason,
        severity=c.severity,
        status=c.status,
        resolution=c.resolution,
        audit_query_id=c.audit_query_id,
        created_at=c.created_at,
        resolved_at=c.resolved_at,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/corrections",
    response_model=CorrectionCreateResponse,
    summary="Submit feedback at the point of complaint (auto-routes per scope)",
)
async def post_correction(
    body: CorrectionRequest,
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> CorrectionCreateResponse:
    if body.scope not in CORRECTION_SCOPES:
        raise BadRequestError(
            f"scope must be one of {list(CORRECTION_SCOPES)} (got {body.scope!r})"
        )
    if body.severity not in CORRECTION_SEVERITIES:
        raise BadRequestError(
            f"severity must be one of {list(CORRECTION_SEVERITIES)} (got {body.severity!r})"
        )

    cid = await insert_correction(
        conn,
        workspace_id=workspace_id,
        scope=body.scope,
        target=body.target,
        observed_value=body.observed_value,
        correct_value=body.correct_value,
        reason=body.reason,
        severity=body.severity,
        user_id=body.user_id,
        audit_query_id=body.audit_query_id,
    )
    fresh = await read_correction(conn, correction_id=cid)
    assert fresh is not None
    outcome = await route_correction(conn, correction=fresh)
    return CorrectionCreateResponse(
        id=cid,
        status=outcome.final_status,
        resolution=outcome.resolution,
        entity_override_id=outcome.entity_override_id,
        schema_field_override_id=outcome.schema_field_override_id,
        regression_entry_id=outcome.regression_entry_id,
        notes=outcome.notes,
    )


@router.get(
    "/corrections",
    response_model=CorrectionsListResponse,
    summary="List corrections for this workspace (filter by scope/status)",
)
async def get_corrections(
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
    scope: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> CorrectionsListResponse:
    if scope is not None and scope not in CORRECTION_SCOPES:
        raise BadRequestError(f"scope filter must be one of {list(CORRECTION_SCOPES)}")
    if status is not None and status not in CORRECTION_STATUSES:
        raise BadRequestError(f"status filter must be one of {list(CORRECTION_STATUSES)}")
    rows = await list_corrections(
        conn, workspace_id=workspace_id,
        scope=scope, status=status, limit=limit,
    )
    return CorrectionsListResponse(items=[_correction_to_out(r) for r in rows])


@router.get(
    "/corrections/{correction_id}",
    response_model=CorrectionOut,
    summary="Fetch one correction by id",
)
async def get_correction(
    correction_id: str,
    workspace_id: Annotated[str, Depends(current_workspace_id)],  # noqa: ARG001
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> CorrectionOut:
    c = await read_correction(conn, correction_id=correction_id)
    if c is None:
        raise HTTPException(status_code=404, detail="correction not found")
    return _correction_to_out(c)


@router.patch(
    "/corrections/{correction_id}",
    response_model=CorrectionOut,
    summary="Admin status update (triage / verify / close / reject)",
)
async def patch_correction(
    correction_id: str,
    body: CorrectionUpdateRequest,
    workspace_id: Annotated[str, Depends(current_workspace_id)],  # noqa: ARG001
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> CorrectionOut:
    if body.status not in CORRECTION_STATUSES:
        raise BadRequestError(
            f"status must be one of {list(CORRECTION_STATUSES)} (got {body.status!r})"
        )
    changed = await update_correction_status(
        conn, correction_id=correction_id,
        status=body.status, resolution=body.resolution,
    )
    if not changed:
        raise HTTPException(status_code=404, detail="correction not found")
    fresh = await read_correction(conn, correction_id=correction_id)
    assert fresh is not None
    return _correction_to_out(fresh)


@router.get(
    "/entity-overrides",
    response_model=EntityOverridesListResponse,
    summary="List active entity overrides (never_merge / split / rename)",
)
async def get_entity_overrides(
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> EntityOverridesListResponse:
    rows = await list_active_entity_overrides(conn, workspace_id=workspace_id)
    return EntityOverridesListResponse(items=[
        EntityOverrideOut(
            id=r.id, workspace_id=r.workspace_id,
            rule_type=r.rule_type, entity_a=r.entity_a, entity_b=r.entity_b,
            rename_to=r.rename_to, reason=r.reason,
            active=r.active, correction_id=r.correction_id,
            created_at=r.created_at,
        )
        for r in rows
    ])


@router.get(
    "/schema-field-overrides",
    response_model=SchemaFieldOverridesListResponse,
    summary="List active schema-field overrides (undo_promotion / retype / rename / blacklist)",
)
async def get_schema_field_overrides(
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> SchemaFieldOverridesListResponse:
    rows = await list_active_schema_field_overrides(
        conn, workspace_id=workspace_id,
    )
    return SchemaFieldOverridesListResponse(items=[
        SchemaFieldOverrideOut(
            id=r.id, workspace_id=r.workspace_id,
            field_path=r.field_path, override_kind=r.override_kind,
            details=r.details, reason=r.reason, active=r.active,
            correction_id=r.correction_id, created_at=r.created_at,
        )
        for r in rows
    ])


@router.get(
    "/regression-set",
    response_model=RegressionListResponse,
    summary="List active regression entries (eval harness reruns these)",
)
async def get_regression_set(
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
    limit: int = Query(default=200, ge=1, le=1000),
) -> RegressionListResponse:
    rows = await list_active_regressions(
        conn, workspace_id=workspace_id, limit=limit,
    )
    return RegressionListResponse(items=[
        RegressionEntryOut(
            id=r.id, workspace_id=r.workspace_id,
            source_correction_id=r.source_correction_id,
            query_text=r.query_text, expected_facts=r.expected_facts,
            implicated_docs=list(r.implicated_docs),
            severity=r.severity, active=r.active,
            fail_count=r.fail_count, created_at=r.created_at,
        )
        for r in rows
    ])
