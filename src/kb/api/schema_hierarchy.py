"""Schema-hierarchy endpoints — api_contracts §4.5–§4.17.

Phase 1c. 11 endpoints under `/schemas/{schema_id}/`:
- entities: POST · GET-list · PUT · DELETE
- fields:   POST · GET-list · PUT · DELETE
- relationships: POST · GET-list · DELETE

Every mutating endpoint:
1. Acquires `SELECT ... FOR UPDATE` on the parent schemas row
   (`lock_and_assert_active_schema`) — serializes concurrent nested CRUDs
   per-schema per decision #12 + §4.1 #4.
2. Performs the mutation via the appropriate repo function.
3. Writes a new `schema_versions` row capturing the full subtree
   (`bump_schema_version`) — coarse-grained versioning per decision #7.
4. Idempotency-Key cache_response (for endpoints where it applies).
All inside the single tx wrapped by the `kb_app_connection` dep.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from starlette.requests import Request
from starlette.responses import Response

from kb.api.deps import current_workspace_id, kb_app_connection
from kb.api.errors import BadRequestError
from kb.api.idempotency import (
    cache_response,
    get_cached,
    idempotency_key_optional,
    idempotency_key_required,
)
from kb.db.pool import Connection
from kb.domain.schema_hierarchy import (
    EntityCreate,
    EntityListResponse,
    EntityResponse,
    EntityUpdate,
    FieldCreate,
    FieldListResponse,
    FieldResponse,
    FieldUpdate,
    RelationshipCreate,
    RelationshipListResponse,
    RelationshipResponse,
    create_entity,
    create_field,
    create_relationship,
    list_entities,
    list_fields,
    list_relationships,
    soft_delete_entity,
    soft_delete_field,
    soft_delete_relationship,
    update_entity,
    update_field,
)
from kb.domain.schemas import (
    bump_schema_version,
    get_schema,
    lock_and_assert_active_schema,
)


router = APIRouter(prefix="/schemas/{schema_id}", tags=["schema-hierarchy"])


def _check_pagination(limit: int, offset: int) -> None:
    if limit < 1 or limit > 200:
        raise BadRequestError(f"limit must be 1..200; got {limit}")
    if offset < 0:
        raise BadRequestError(f"offset must be >= 0; got {offset}")


# ===========================================================================
# §4.5–§4.8 — entities
# ===========================================================================


@router.post(
    "/entities",
    status_code=201,
    response_model=EntityResponse,
    summary="Create entity type within a schema",
)
async def post_entity(
    schema_id: str,
    body: EntityCreate,
    request: Request,
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    idem_key: Annotated[str, Depends(idempotency_key_required)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> Response:
    cached = await get_cached(conn, workspace_id, idem_key)
    if cached is not None:
        body_dict, status_code = cached
        return JSONResponse(
            content=body_dict, status_code=status_code,
            headers={"X-Idempotent-Replay": "true"},
        )

    await lock_and_assert_active_schema(conn, schema_id)
    entity = await create_entity(conn, workspace_id, schema_id, body)
    await bump_schema_version(conn, workspace_id, schema_id, kind="put")

    body_dict = entity.model_dump()
    await cache_response(conn, workspace_id, idem_key, body=body_dict, status_code=201)
    return JSONResponse(content=body_dict, status_code=201)


@router.get(
    "/entities",
    response_model=EntityListResponse,
    summary="List active entity types in a schema",
)
async def get_entities(
    schema_id: str,
    request: Request,
    conn: Annotated[Connection, Depends(kb_app_connection)],
    limit: int = Query(default=50),
    offset: int = Query(default=0),
) -> EntityListResponse:
    _check_pagination(limit, offset)
    # 404-gate via parent (also handles wrong-workspace via RLS).
    await get_schema(conn, schema_id)
    return await list_entities(conn, schema_id, limit=limit, offset=offset)


@router.put(
    "/entities/{entity_id}",
    response_model=EntityResponse,
    summary="Replace entity name + description",
)
async def put_entity(
    schema_id: str,
    entity_id: str,
    body: EntityUpdate,
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

    await lock_and_assert_active_schema(conn, schema_id)
    entity = await update_entity(conn, schema_id, entity_id, body)
    await bump_schema_version(conn, workspace_id, schema_id, kind="put")

    body_dict = entity.model_dump()
    await cache_response(conn, workspace_id, idem_key, body=body_dict, status_code=200)
    return JSONResponse(content=body_dict, status_code=200)


@router.delete(
    "/entities/{entity_id}",
    status_code=204,
    summary="Soft-delete an entity (cascades to its fields + referencing relationships)",
)
async def delete_entity(
    schema_id: str,
    entity_id: str,
    request: Request,
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    idem_key: Annotated[str | None, Depends(idempotency_key_optional)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> Response:
    if idem_key is not None:
        cached = await get_cached(conn, workspace_id, idem_key)
        if cached is not None:
            _, status_code = cached
            return Response(status_code=status_code, headers={"X-Idempotent-Replay": "true"})

    await lock_and_assert_active_schema(conn, schema_id)
    await soft_delete_entity(conn, schema_id, entity_id)
    await bump_schema_version(conn, workspace_id, schema_id, kind="put")

    await cache_response(conn, workspace_id, idem_key, body=None, status_code=204)
    return Response(status_code=204)


# ===========================================================================
# §4.10–§4.13 — fields
# ===========================================================================


@router.post(
    "/entities/{entity_id}/fields",
    status_code=201,
    response_model=FieldResponse,
    summary="Create field on an entity",
)
async def post_field(
    schema_id: str,
    entity_id: str,
    body: FieldCreate,
    request: Request,
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    idem_key: Annotated[str, Depends(idempotency_key_required)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> Response:
    cached = await get_cached(conn, workspace_id, idem_key)
    if cached is not None:
        body_dict, status_code = cached
        return JSONResponse(
            content=body_dict, status_code=status_code,
            headers={"X-Idempotent-Replay": "true"},
        )

    await lock_and_assert_active_schema(conn, schema_id)
    field = await create_field(conn, workspace_id, schema_id, entity_id, body)
    await bump_schema_version(conn, workspace_id, schema_id, kind="put")

    body_dict = field.model_dump()
    await cache_response(conn, workspace_id, idem_key, body=body_dict, status_code=201)
    return JSONResponse(content=body_dict, status_code=201)


@router.get(
    "/entities/{entity_id}/fields",
    response_model=FieldListResponse,
    summary="List active fields on an entity",
)
async def get_fields(
    schema_id: str,
    entity_id: str,
    request: Request,
    conn: Annotated[Connection, Depends(kb_app_connection)],
    limit: int = Query(default=50),
    offset: int = Query(default=0),
) -> FieldListResponse:
    _check_pagination(limit, offset)
    await get_schema(conn, schema_id)
    return await list_fields(conn, schema_id, entity_id, limit=limit, offset=offset)


@router.put(
    "/entities/{entity_id}/fields/{field_id}",
    response_model=FieldResponse,
    summary="Replace field name + type + nl_description + is_required",
)
async def put_field(
    schema_id: str,
    entity_id: str,
    field_id: str,
    body: FieldUpdate,
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

    await lock_and_assert_active_schema(conn, schema_id)
    field = await update_field(conn, schema_id, entity_id, field_id, body)
    await bump_schema_version(conn, workspace_id, schema_id, kind="put")

    body_dict = field.model_dump()
    await cache_response(conn, workspace_id, idem_key, body=body_dict, status_code=200)
    return JSONResponse(content=body_dict, status_code=200)


@router.delete(
    "/entities/{entity_id}/fields/{field_id}",
    status_code=204,
    summary="Soft-delete a field",
)
async def delete_field(
    schema_id: str,
    entity_id: str,
    field_id: str,
    request: Request,
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    idem_key: Annotated[str | None, Depends(idempotency_key_optional)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> Response:
    if idem_key is not None:
        cached = await get_cached(conn, workspace_id, idem_key)
        if cached is not None:
            _, status_code = cached
            return Response(status_code=status_code, headers={"X-Idempotent-Replay": "true"})

    await lock_and_assert_active_schema(conn, schema_id)
    await soft_delete_field(conn, schema_id, entity_id, field_id)
    await bump_schema_version(conn, workspace_id, schema_id, kind="put")

    await cache_response(conn, workspace_id, idem_key, body=None, status_code=204)
    return Response(status_code=204)


# ===========================================================================
# §4.15–§4.17 — relationships
# ===========================================================================


@router.post(
    "/relationships",
    status_code=201,
    response_model=RelationshipResponse,
    summary="Create typed edge between two entities in this schema",
)
async def post_relationship(
    schema_id: str,
    body: RelationshipCreate,
    request: Request,
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    idem_key: Annotated[str, Depends(idempotency_key_required)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> Response:
    cached = await get_cached(conn, workspace_id, idem_key)
    if cached is not None:
        body_dict, status_code = cached
        return JSONResponse(
            content=body_dict, status_code=status_code,
            headers={"X-Idempotent-Replay": "true"},
        )

    await lock_and_assert_active_schema(conn, schema_id)
    rel = await create_relationship(conn, workspace_id, schema_id, body)
    await bump_schema_version(conn, workspace_id, schema_id, kind="put")

    body_dict = rel.model_dump()
    await cache_response(conn, workspace_id, idem_key, body=body_dict, status_code=201)
    return JSONResponse(content=body_dict, status_code=201)


@router.get(
    "/relationships",
    response_model=RelationshipListResponse,
    summary="List active relationships in a schema",
)
async def get_relationships(
    schema_id: str,
    request: Request,
    conn: Annotated[Connection, Depends(kb_app_connection)],
    limit: int = Query(default=50),
    offset: int = Query(default=0),
) -> RelationshipListResponse:
    _check_pagination(limit, offset)
    await get_schema(conn, schema_id)
    return await list_relationships(conn, schema_id, limit=limit, offset=offset)


@router.delete(
    "/relationships/{relationship_id}",
    status_code=204,
    summary="Soft-delete a relationship",
)
async def delete_relationship(
    schema_id: str,
    relationship_id: str,
    request: Request,
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    idem_key: Annotated[str | None, Depends(idempotency_key_optional)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> Response:
    if idem_key is not None:
        cached = await get_cached(conn, workspace_id, idem_key)
        if cached is not None:
            _, status_code = cached
            return Response(status_code=status_code, headers={"X-Idempotent-Replay": "true"})

    await lock_and_assert_active_schema(conn, schema_id)
    await soft_delete_relationship(conn, schema_id, relationship_id)
    await bump_schema_version(conn, workspace_id, schema_id, kind="put")

    await cache_response(conn, workspace_id, idem_key, body=None, status_code=204)
    return Response(status_code=204)
