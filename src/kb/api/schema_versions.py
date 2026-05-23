"""Schema-versions endpoints — api_contracts §3.7–§3.9.

Phase 1b. Three endpoints under `/schemas/{schema_id}/versions`:
- GET (list)
- GET /:v (read with computed diff)
- POST /:v/rollback (clone-forward; Idempotency-Key required)

Mutations to POST /schemas + PUT /schemas/:id remain in `kb.api.schemas`;
their domain-layer functions (`create_schema`, `update_schema`) now write
version rows in-tx (see `kb.domain.schemas`).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import JSONResponse
from starlette.requests import Request
from starlette.responses import Response

from kb.api.deps import current_workspace_id, kb_app_connection
from kb.api.errors import BadRequestError
from kb.api.idempotency import (
    cache_response,
    get_cached,
    idempotency_key_required,
)
from kb.db.pool import Connection
from kb.domain.schemas import (
    NotFoundError,
    get_schema,
    rollback_to_version,
)
from kb.domain.schema_versions import (
    VersionListResponse,
    VersionRead,
    get_version,
    list_versions,
)

router = APIRouter(prefix="/schemas/{schema_id}/versions", tags=["schema-versions"])


# ---------------------------------------------------------------------------
# GET /schemas/:id/versions
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=VersionListResponse,
    summary="List versions for a schema (newest-first, lightweight summary)",
    responses={
        400: {"description": "limit > 200 or offset < 0"},
        404: {"description": "Parent schema not found or soft-deleted"},
    },
)
async def get_versions(
    schema_id: str,
    request: Request,
    conn: Annotated[Connection, Depends(kb_app_connection)],
    limit: int = Query(default=50),
    offset: int = Query(default=0),
) -> VersionListResponse:
    if limit < 1 or limit > 200:
        raise BadRequestError(f"limit must be 1..200; got {limit}")
    if offset < 0:
        raise BadRequestError(f"offset must be >= 0; got {offset}")

    # 404-gate via the parent (also handles wrong-workspace via RLS).
    await get_schema(conn, schema_id)
    return await list_versions(conn, schema_id, limit=limit, offset=offset)


# ---------------------------------------------------------------------------
# GET /schemas/:id/versions/:v
# ---------------------------------------------------------------------------


@router.get(
    "/{version}",
    response_model=VersionRead,
    summary="Read one version (full body + computed diff_from_prior)",
    responses={
        404: {"description": "Schema or version not found"},
        422: {"description": "version is not a positive integer"},
    },
)
async def get_one_version(
    schema_id: str,
    request: Request,
    conn: Annotated[Connection, Depends(kb_app_connection)],
    version: int = Path(..., ge=1),
) -> VersionRead:
    # 404-gate via the parent first.
    await get_schema(conn, schema_id)
    return await get_version(conn, schema_id, version)


# ---------------------------------------------------------------------------
# POST /schemas/:id/versions/:v/rollback
# ---------------------------------------------------------------------------


@router.post(
    "/{version}/rollback",
    summary="Rollback to a prior version (clone-forward as new current version)",
    status_code=200,
    responses={
        200: {"description": "Rolled back; response is the updated schema object"},
        400: {"description": "Missing Idempotency-Key or malformed body"},
        404: {"description": "Schema or version not found"},
        409: {"description": "Target version is already the current version (rollback-noop)"},
        422: {"description": "version is not a positive integer"},
    },
)
async def post_rollback(
    schema_id: str,
    request: Request,
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    idem_key: Annotated[str, Depends(idempotency_key_required)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
    version: int = Path(..., ge=1),
) -> Response:
    cached = await get_cached(conn, workspace_id, idem_key)
    if cached is not None:
        body_dict, status_code = cached
        return JSONResponse(
            content=body_dict, status_code=status_code,
            headers={"X-Idempotent-Replay": "true"},
        )

    # 404-gate parent before touching the version (better error than RollbackNoopError
    # when caller can't see the schema).
    await get_schema(conn, schema_id)

    schema = await rollback_to_version(conn, workspace_id, schema_id, version)
    body_dict = schema.model_dump()
    await cache_response(conn, workspace_id, idem_key, body=body_dict, status_code=200)
    return JSONResponse(content=body_dict, status_code=200)
