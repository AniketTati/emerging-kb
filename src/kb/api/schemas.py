"""Schema CRUD endpoints — api_contracts §2.2–§2.6."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse, Response
from starlette.requests import Request

from kb.api.deps import current_workspace_id, kb_app_connection
from kb.api.errors import BadRequestError
from kb.api.idempotency import (
    cache_response,
    get_cached,
    idempotency_key_optional,
    idempotency_key_required,
)
from kb.db.pool import Connection
from kb.domain.schemas import (
    SchemaCreate,
    SchemaListResponse,
    SchemaResponse,
    SchemaUpdate,
    create_schema,
    get_schema,
    list_schemas,
    soft_delete_schema,
    update_schema,
)

router = APIRouter(prefix="/schemas", tags=["schemas"])


# ---------------------------------------------------------------------------
# POST /schemas
# ---------------------------------------------------------------------------


@router.post(
    "",
    status_code=201,
    summary="Create a schema",
    responses={
        201: {"model": SchemaResponse},
        400: {"description": "Missing Idempotency-Key or malformed body"},
        409: {"description": "Schema name already exists in workspace"},
        422: {"description": "Validation error"},
    },
)
async def post_schema(
    body: SchemaCreate,
    request: Request,
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    idem_key: Annotated[str, Depends(idempotency_key_required)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> Response:
    cached = await get_cached(conn, workspace_id, idem_key)
    if cached is not None:
        body_dict, status_code = cached
        if status_code == 204:
            return Response(status_code=204, headers={"X-Idempotent-Replay": "true"})
        return JSONResponse(
            content=body_dict, status_code=status_code,
            headers={"X-Idempotent-Replay": "true"},
        )

    schema = await create_schema(conn, workspace_id, body)
    body_dict = schema.model_dump()
    await cache_response(conn, workspace_id, idem_key, body=body_dict, status_code=201)
    return JSONResponse(content=body_dict, status_code=201)


# ---------------------------------------------------------------------------
# GET /schemas — list
# ---------------------------------------------------------------------------


@router.get("", response_model=SchemaListResponse, summary="List active schemas")
async def get_schemas(
    request: Request,
    conn: Annotated[Connection, Depends(kb_app_connection)],
    limit: int = Query(default=50),
    offset: int = Query(default=0),
) -> SchemaListResponse:
    # Manual validation: api_contracts §2.3 wants 400 (not 422) for out-of-range
    # query params. Pydantic's Query(ge=..., le=...) would raise 422.
    if limit < 1 or limit > 200:
        raise BadRequestError(f"limit must be 1..200; got {limit}")
    if offset < 0:
        raise BadRequestError(f"offset must be >= 0; got {offset}")

    return await list_schemas(conn, limit=limit, offset=offset)


# ---------------------------------------------------------------------------
# GET /schemas/:id
# ---------------------------------------------------------------------------


@router.get(
    "/{schema_id}",
    response_model=SchemaResponse,
    summary="Read a schema",
    responses={404: {"description": "Not found (incl. soft-deleted, wrong workspace)"}},
)
async def get_schema_by_id(
    schema_id: str,
    request: Request,
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> SchemaResponse:
    return await get_schema(conn, schema_id)


# ---------------------------------------------------------------------------
# PUT /schemas/:id
# ---------------------------------------------------------------------------


@router.put(
    "/{schema_id}",
    response_model=SchemaResponse,
    summary="Update a schema (full replace)",
    responses={
        404: {"description": "Not found"},
        409: {"description": "Name collision"},
        422: {"description": "Validation error"},
    },
)
async def put_schema(
    schema_id: str,
    body: SchemaUpdate,
    request: Request,
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    idem_key: Annotated[str | None, Depends(idempotency_key_optional)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> Response:
    if idem_key is not None:
        cached = await get_cached(conn, workspace_id, idem_key)
        if cached is not None:
            body_dict, status_code = cached
            return JSONResponse(
                content=body_dict, status_code=status_code,
                headers={"X-Idempotent-Replay": "true"},
            )

    schema = await update_schema(conn, schema_id, body)
    body_dict = schema.model_dump()
    await cache_response(conn, workspace_id, idem_key, body=body_dict, status_code=200)
    return JSONResponse(content=body_dict, status_code=200)


# ---------------------------------------------------------------------------
# DELETE /schemas/:id
# ---------------------------------------------------------------------------


@router.delete(
    "/{schema_id}",
    status_code=204,
    summary="Soft-delete a schema",
    responses={
        204: {"description": "Deleted (or replayed for same Idempotency-Key)"},
        404: {"description": "Not found or already deleted"},
    },
)
async def delete_schema(
    schema_id: str,
    request: Request,
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    idem_key: Annotated[str | None, Depends(idempotency_key_optional)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> Response:
    if idem_key is not None:
        cached = await get_cached(conn, workspace_id, idem_key)
        if cached is not None:
            _, status_code = cached
            # Cached DELETE replays as a fresh 204 (no body for 204 per HTTP spec).
            return Response(status_code=status_code, headers={"X-Idempotent-Replay": "true"})

    await soft_delete_schema(conn, schema_id)
    await cache_response(conn, workspace_id, idem_key, body=None, status_code=204)
    return Response(status_code=204)
