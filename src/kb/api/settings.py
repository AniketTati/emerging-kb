"""WA-1 / Design 9 — Settings endpoints.

Wave A surfaces two things:
- `GET /settings/effective-config` — every config key with its resolved value
  and the layer that produced it. Powers the "Effective Configuration" panel
  in Settings → Auto-discovery (UI design §6.10).
- `POST /settings/overrides` — create / update a runtime override.
- `DELETE /settings/overrides` — revoke (soft) an existing override.

`GET /settings/models` returns the resolved `models.*` subset, which is
what the Settings → Models card consumes (UI design §6.10).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from kb.api.deps import current_workspace_id, kb_app_connection
from kb.api.errors import BadRequestError
from kb.db.pool import Connection
from kb.layered_config import (
    ConfigKeyNotFoundError,
    effective_config,
    insert_override,
    resolve_config,
    revoke_override,
)
from kb.layered_config.repo import ALLOWED_SCOPE_KINDS, read_workspace_overrides


router = APIRouter(prefix="/settings", tags=["settings"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class EffectiveEntry(BaseModel):
    key: str
    value: Any
    layer: str
    scope_id: str | None = None


class EffectiveConfigResponse(BaseModel):
    entries: list[EffectiveEntry] = Field(default_factory=list)


class ModelChoicesResponse(BaseModel):
    extraction_llm: str | None = None
    hard_query_llm: str | None = None
    embedder: str | None = None
    reranker: str | None = None
    faithfulness: str | None = None
    intent_classifier: str | None = None
    conflict_detector: str | None = None
    generation: str | None = None
    generation_hard: str | None = None


class OverrideRequest(BaseModel):
    scope_kind: str
    scope_id: str
    config_key: str
    config_value: Any
    reason: str | None = None
    set_by: str | None = None


class OverrideRevokeRequest(BaseModel):
    scope_kind: str
    scope_id: str
    config_key: str


class OverrideResponse(BaseModel):
    id: str | None = None
    revoked: bool = False


# ---------------------------------------------------------------------------
# GET /settings/effective-config
# ---------------------------------------------------------------------------


@router.get(
    "/effective-config",
    response_model=EffectiveConfigResponse,
    summary="Every config key with its resolved value + layer of origin",
)
async def get_effective_config(
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
    domain: str | None = Query(default=None, description="Optional domain scope (config/domains/<domain>.yaml)"),
    doc_type: str | None = Query(default=None, description="Optional doc_type scope filter"),
    doc_id: str | None = Query(default=None, description="Optional doc_id scope filter"),
    user_id: str | None = Query(default=None, description="Optional user_id scope filter"),
) -> EffectiveConfigResponse:
    rc = await effective_config(
        workspace_id=workspace_id,
        conn=conn,
        domain=domain,
        doc_type=doc_type,
        doc_id=doc_id,
        user_id=user_id,
    )
    return EffectiveConfigResponse(
        entries=[
            EffectiveEntry(
                key=e.key, value=e.value, layer=e.layer, scope_id=e.scope_id,
            )
            for e in rc.entries
        ],
    )


# ---------------------------------------------------------------------------
# GET /settings/models
# ---------------------------------------------------------------------------


@router.get(
    "/models",
    response_model=ModelChoicesResponse,
    summary="Resolved per-stage LLM / embedder / reranker / faithfulness model choices",
)
async def get_models(
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
    domain: str | None = Query(default=None),
) -> ModelChoicesResponse:
    payload: dict[str, Any] = {}
    for field_name in ModelChoicesResponse.model_fields:
        key = f"models.{field_name}"
        try:
            payload[field_name] = await resolve_config(
                key,
                workspace_id=workspace_id,
                conn=conn,
                domain=domain,
            )
        except ConfigKeyNotFoundError:
            payload[field_name] = None
    return ModelChoicesResponse(**payload)


# ---------------------------------------------------------------------------
# POST /settings/overrides
# ---------------------------------------------------------------------------


@router.post(
    "/overrides",
    response_model=OverrideResponse,
    summary="Create or replace a runtime config override at a given scope",
    status_code=201,
)
async def post_override(
    body: OverrideRequest,
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> OverrideResponse:
    if body.scope_kind not in ALLOWED_SCOPE_KINDS:
        raise BadRequestError(
            f"scope_kind={body.scope_kind!r} not in {ALLOWED_SCOPE_KINDS}"
        )
    if not body.config_key:
        raise BadRequestError("config_key must be non-empty")
    new_id = await insert_override(
        conn,
        workspace_id=workspace_id,
        scope_kind=body.scope_kind,
        scope_id=body.scope_id,
        config_key=body.config_key,
        config_value=body.config_value,
        reason=body.reason,
        set_by=body.set_by,
    )
    return OverrideResponse(id=new_id, revoked=False)


# ---------------------------------------------------------------------------
# DELETE /settings/overrides
# ---------------------------------------------------------------------------


@router.delete(
    "/overrides",
    response_model=OverrideResponse,
    summary="Soft-revoke an existing override (keeps history)",
)
async def delete_override(
    body: OverrideRevokeRequest,
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> OverrideResponse:
    if body.scope_kind not in ALLOWED_SCOPE_KINDS:
        raise BadRequestError(
            f"scope_kind={body.scope_kind!r} not in {ALLOWED_SCOPE_KINDS}"
        )
    revoked = await revoke_override(
        conn,
        workspace_id=workspace_id,
        scope_kind=body.scope_kind,
        scope_id=body.scope_id,
        config_key=body.config_key,
    )
    return OverrideResponse(revoked=revoked)


# ---------------------------------------------------------------------------
# B7 / WA-14 — GET /settings/overrides
# ---------------------------------------------------------------------------


class OverrideOut(BaseModel):
    id: str
    workspace_id: str
    scope_kind: str
    scope_id: str | None
    config_key: str
    config_value: Any
    reason: str | None
    set_by: str | None
    set_at: str
    active: bool


class OverridesListResponse(BaseModel):
    items: list[OverrideOut] = Field(default_factory=list)


@router.get(
    "/overrides",
    response_model=OverridesListResponse,
    summary="List active config overrides for this workspace (Settings UI)",
)
async def get_overrides(
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> OverridesListResponse:
    rows = await read_workspace_overrides(conn, workspace_id=workspace_id)
    return OverridesListResponse(items=[
        OverrideOut(
            id=str(r.id),
            workspace_id=str(r.workspace_id),
            scope_kind=str(r.scope_kind),
            scope_id=str(r.scope_id) if r.scope_id else None,
            config_key=str(r.config_key),
            config_value=r.config_value,
            reason=r.reason,
            set_by=r.set_by,
            set_at=str(r.set_at),
            active=bool(r.active),
        )
        for r in rows
    ])
