"""Schema CRUD endpoints — api_contracts §2.2–§2.6."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
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
# GET /schemas/export.yaml — dump typed schemas as a single YAML file
# (Schema Studio header "Export YAML" button — prototype parity)
# ---------------------------------------------------------------------------


@router.get(
    "/export.yaml",
    summary="Export all active typed schemas + entities + fields as YAML",
    responses={200: {"content": {"application/x-yaml": {}}}},
)
async def export_schemas_yaml(
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> Response:
    """Streams a single YAML document the curator can commit to version
    control or share with another workspace. Format keeps things
    importable later — top-level `schemas:` list, each item with
    `name` / `description` / `entities` (list) / `fields` (per entity).

    YAML hand-rolled to avoid a runtime PyYAML dependency (we already
    use json.dumps everywhere else); the structure is small + flat so
    the indent rules are trivial.
    """
    # `schemas` table has `current_version_id` (FK to schema_versions),
    # not a `current_version` int. Re-derive the version number via the
    # FK so the YAML can carry it.
    # `schemas` table has `current_version_id` (FK to schema_versions),
    # not a `current_version` int. Re-derive the version number via the
    # FK so the YAML can carry it.
    cur = await conn.execute(
        "SELECT s.id::text, s.name, s.description, s.lifecycle_state, "
        "       sv.version_number "
        "  FROM schemas s "
        "  LEFT JOIN schema_versions sv ON sv.id = s.current_version_id "
        " WHERE s.workspace_id = %s AND s.lifecycle_state = 'active' "
        " ORDER BY s.name",
        (workspace_id,),
    )
    schemas_rows = await cur.fetchall()

    lines: list[str] = []
    lines.append("# Emerging KB — schema export")
    lines.append(f"# workspace_id: {workspace_id}")
    lines.append("schemas:")
    if not schemas_rows:
        lines.append("  []")
    for sid, sname, sdesc, _slc, sver in schemas_rows:
        lines.append(f"  - name: {_yaml_str(sname)}")
        lines.append(f"    description: {_yaml_str(sdesc or '')}")
        lines.append(f"    current_version: {sver or 1}")
        # Entities
        cur = await conn.execute(
            "SELECT id::text, name, description "
            "  FROM schema_entities "
            " WHERE schema_id = %s AND lifecycle_state = 'active' "
            " ORDER BY name",
            (sid,),
        )
        ent_rows = await cur.fetchall()
        if not ent_rows:
            lines.append("    entities: []")
            continue
        lines.append("    entities:")
        for eid, ename, edesc in ent_rows:
            lines.append(f"      - name: {_yaml_str(ename)}")
            lines.append(f"        description: {_yaml_str(edesc or '')}")
            cur = await conn.execute(
                "SELECT name, type, nl_description, is_required "
                "  FROM schema_fields "
                " WHERE entity_id = %s AND lifecycle_state = 'active' "
                " ORDER BY name",
                (eid,),
            )
            field_rows = await cur.fetchall()
            if not field_rows:
                lines.append("        fields: []")
                continue
            lines.append("        fields:")
            for fname, ftype, fdesc, frequired in field_rows:
                lines.append(f"          - name: {_yaml_str(fname)}")
                lines.append(f"            type: {_yaml_str(ftype or 'string')}")
                if fdesc:
                    lines.append(f"            description: {_yaml_str(fdesc)}")
                if frequired:
                    lines.append("            required: true")

    body = "\n".join(lines) + "\n"
    return Response(
        content=body,
        media_type="application/x-yaml",
        headers={
            "Content-Disposition": (
                f'attachment; filename="kb-schemas-{workspace_id[:8]}.yaml"'
            ),
        },
    )


def _yaml_str(value: str) -> str:
    """Quote a YAML scalar safely. Avoids importing PyYAML for one feature."""
    s = (value or "").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


# ===========================================================================
# B7 / WA-14 — Inferred fields (Schema Studio "Inferred" tab)
# ===========================================================================


class InferredFieldOut(BaseModel):
    id: str
    workspace_id: str
    inferred_doc_type: str
    canonical_name: str
    description: str | None = None
    value_type: str | None = None
    n_docs_observed: int = 0
    prevalence: float = 0.0
    stability: float = 0.0
    value_type_confidence: float = 0.0
    is_promoted: bool = False
    promoted_schema_field_id: str | None = None
    created_at: str | None = None


class InferredFieldsListResponse(BaseModel):
    items: list[InferredFieldOut] = Field(default_factory=list)


class InferredFieldRenameRequest(BaseModel):
    canonical_name: str = Field(min_length=1, max_length=128)


class InferredFieldPromotedResponse(BaseModel):
    inferred_field_id: str
    schema_field_id: str
    schema_entity_id: str


class InferredFieldDeleteResponse(BaseModel):
    deleted: int


class InferredFieldSampleValue(BaseModel):
    value_text: str
    file_id: str
    file_name: str | None = None
    n_occurrences: int = 1


class InferredFieldSampleValuesResponse(BaseModel):
    field_id: str
    canonical_name: str
    items: list[InferredFieldSampleValue] = Field(default_factory=list)


@router.get(
    "/inferred-fields",
    response_model=InferredFieldsListResponse,
    summary="List inferred_schema_fields (Schema Studio 'Inferred' tab)",
)
async def get_inferred_fields(
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
    doc_type: str | None = Query(default=None, description="Filter by inferred_doc_type"),
    only_promotable: bool = Query(
        default=False,
        description="Only return rows above auto-promotion thresholds "
                    "(prevalence >= 0.8 AND stability >= 0.7) that are NOT yet promoted",
    ),
    limit: int = Query(default=200, ge=1, le=1000),
) -> InferredFieldsListResponse:
    clauses = ["workspace_id = %s"]
    params: list = [workspace_id]
    if doc_type is not None:
        clauses.append("inferred_doc_type = %s")
        params.append(doc_type)
    if only_promotable:
        clauses.append(
            "is_promoted = false AND prevalence >= 0.8 AND stability >= 0.7"
        )
    where = " AND ".join(clauses)
    params.append(limit)
    cur = await conn.execute(
        f"SELECT id::text, workspace_id::text, inferred_doc_type, "
        f"       canonical_name, description, value_type, n_docs_observed, "
        f"       prevalence, stability, value_type_confidence, "
        f"       is_promoted, promoted_schema_field_id::text, created_at "
        f"FROM inferred_schema_fields "
        f"WHERE {where} "
        f"ORDER BY prevalence DESC, stability DESC, n_docs_observed DESC "
        f"LIMIT %s",
        tuple(params),
    )
    rows = await cur.fetchall()
    items = [
        InferredFieldOut(
            id=str(r[0]),
            workspace_id=str(r[1]),
            inferred_doc_type=str(r[2]),
            canonical_name=str(r[3]),
            description=r[4],
            value_type=r[5],
            n_docs_observed=int(r[6] or 0),
            prevalence=float(r[7] or 0.0),
            stability=float(r[8] or 0.0),
            value_type_confidence=float(r[9] or 0.0),
            is_promoted=bool(r[10]),
            promoted_schema_field_id=str(r[11]) if r[11] else None,
            created_at=(
                r[12].isoformat() if hasattr(r[12], "isoformat") else
                (str(r[12]) if r[12] else None)
            ),
        )
        for r in rows
    ]
    return InferredFieldsListResponse(items=items)


# ---------------------------------------------------------------------------
# Inferred-field actions (Schema Studio row buttons)
# ---------------------------------------------------------------------------
#
# Three thin endpoints sit on top of the worker's existing
# `promote_field` / `mark_inferred_field_promoted` / `ensure_auto_schema_entity`
# helpers — Promote / Rename / Discard are the row buttons the
# Schema Studio prototype shows on every Inferred-tab row. Without these
# the buttons are decoration; with them the curator can keep moving
# fields through to the typed schema without touching SQL or the worker
# CLI.


@router.post(
    "/inferred-fields/{field_id}/promote",
    response_model=InferredFieldPromotedResponse,
    summary="Force-promote an inferred field to the typed schema",
)
async def promote_inferred_field(
    field_id: str,
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> InferredFieldPromotedResponse:
    """Idempotent — running twice returns the same schema_field_id.

    Pulls the inferred row, ensures the auto-schema entity exists for
    its doc_type, inserts the schema_field, marks the inferred row
    promoted. All four operations run in one transaction so a mid-
    flow failure leaves nothing partially promoted.
    """
    from kb.extraction.promotion import ensure_auto_schema_entity, promote_field
    from kb.domain.fields import mark_inferred_field_promoted

    cur = await conn.execute(
        "SELECT inferred_doc_type, canonical_name, description, value_type, "
        "       is_promoted, promoted_schema_field_id::text "
        "  FROM inferred_schema_fields "
        " WHERE id = %s AND workspace_id = %s",
        (field_id, workspace_id),
    )
    row = await cur.fetchone()
    if row is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="inferred field not found")
    doc_type, canonical_name, description, value_type, was_promoted, sfid = row

    if was_promoted and sfid:
        # Already done — idempotent return.
        cur2 = await conn.execute(
            "SELECT entity_id::text FROM schema_fields WHERE id = %s",
            (sfid,),
        )
        eid_row = await cur2.fetchone()
        return InferredFieldPromotedResponse(
            inferred_field_id=field_id,
            schema_field_id=str(sfid),
            schema_entity_id=str(eid_row[0]) if eid_row else "",
        )

    _, schema_entity_id = await ensure_auto_schema_entity(
        conn,
        workspace_id=workspace_id,
        doc_type=doc_type or "unknown",
    )
    schema_field_id = await promote_field(
        conn,
        workspace_id=workspace_id,
        schema_entity_id=schema_entity_id,
        canonical_name=canonical_name,
        description=description or "",
        value_type=value_type or "text",
    )
    await mark_inferred_field_promoted(
        conn,
        inferred_field_id=field_id,
        promoted_schema_field_id=schema_field_id,
    )
    return InferredFieldPromotedResponse(
        inferred_field_id=field_id,
        schema_field_id=schema_field_id,
        schema_entity_id=schema_entity_id,
    )


@router.patch(
    "/inferred-fields/{field_id}",
    response_model=InferredFieldOut,
    summary="Rename the canonical_name on an inferred field (curator override)",
)
async def rename_inferred_field(
    field_id: str,
    body: InferredFieldRenameRequest,
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> InferredFieldOut:
    """Updates the cluster's canonical_name. Useful when L2b picked
    `non_compete` but the curator wants `non_competition_clause`.
    Returns the updated row."""
    new_name = body.canonical_name.strip()
    cur = await conn.execute(
        "UPDATE inferred_schema_fields "
        "   SET canonical_name = %s "
        " WHERE id = %s AND workspace_id = %s "
        "RETURNING id::text, workspace_id::text, inferred_doc_type, "
        "          canonical_name, description, value_type, "
        "          n_docs_observed, prevalence, stability, "
        "          value_type_confidence, is_promoted, "
        "          promoted_schema_field_id::text, created_at",
        (new_name, field_id, workspace_id),
    )
    r = await cur.fetchone()
    if r is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="inferred field not found")
    return InferredFieldOut(
        id=str(r[0]),
        workspace_id=str(r[1]),
        inferred_doc_type=str(r[2]),
        canonical_name=str(r[3]),
        description=r[4],
        value_type=r[5],
        n_docs_observed=int(r[6] or 0),
        prevalence=float(r[7] or 0.0),
        stability=float(r[8] or 0.0),
        value_type_confidence=float(r[9] or 0.0),
        is_promoted=bool(r[10]),
        promoted_schema_field_id=str(r[11]) if r[11] else None,
        created_at=(
            r[12].isoformat() if hasattr(r[12], "isoformat") else
            (str(r[12]) if r[12] else None)
        ),
    )


@router.get(
    "/inferred-fields/{field_id}/sample-values",
    response_model=InferredFieldSampleValuesResponse,
    summary="Top distinct example values for an inferred field (Schema Studio expand)",
)
async def get_inferred_field_sample_values(
    field_id: str,
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
    limit: int = 5,
) -> InferredFieldSampleValuesResponse:
    """Pull example values from `proposed_fields` joined to `files` for
    one inferred-cluster row. The cluster key is
    (workspace_id, inferred_doc_type, field_name), so we match by name.

    Returns at most `limit` distinct values, picking the most-occurring
    first. Used by the Schema Studio Inferred-row expander — pre-fix
    that block showed "Sample values land in Pass B".
    """
    cur = await conn.execute(
        "SELECT inferred_doc_type, canonical_name "
        "  FROM inferred_schema_fields "
        " WHERE id = %s AND workspace_id = %s",
        (field_id, workspace_id),
    )
    row = await cur.fetchone()
    if row is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="inferred field not found")
    doc_type, canonical_name = row

    # Pull most-frequent distinct values. We match on
    # proposed_fields.field_name = canonical_name (the L2b clusterer
    # populates field_name with the canonical at cluster time).
    cur = await conn.execute(
        """
        SELECT pf.value_text,
               (array_agg(pf.file_id::text))[1] AS first_file_id,
               (array_agg(f.name))[1] AS first_file_name,
               count(*)::int AS n
          FROM proposed_fields pf
          JOIN files f ON f.id = pf.file_id
         WHERE pf.workspace_id = %s
           AND pf.inferred_doc_type = %s
           AND pf.field_name = %s
           AND pf.value_text IS NOT NULL
           AND length(trim(pf.value_text)) > 0
           AND f.lifecycle_state NOT IN ('deleted','failed')
         GROUP BY pf.value_text
         ORDER BY n DESC, pf.value_text ASC
         LIMIT %s
        """,
        (workspace_id, doc_type, canonical_name, limit),
    )
    rows = await cur.fetchall()
    items = [
        InferredFieldSampleValue(
            value_text=str(r[0])[:240],
            file_id=str(r[1]),
            file_name=r[2],
            n_occurrences=int(r[3] or 0),
        )
        for r in rows
    ]
    return InferredFieldSampleValuesResponse(
        field_id=field_id,
        canonical_name=canonical_name,
        items=items,
    )


@router.delete(
    "/inferred-fields/{field_id}",
    response_model=InferredFieldDeleteResponse,
    summary="Discard an inferred field (curator hides it from the Inferred tab)",
)
async def discard_inferred_field(
    field_id: str,
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> InferredFieldDeleteResponse:
    """Hard-delete the row. The L2b clusterer may re-emit it later
    if the doc-set still supports it; that's intentional — discard
    is a UI signal, not a permanent block. (For permanent
    blocklisting we'd add a separate `schema_field_overrides`
    rule; Wave B.)
    """
    cur = await conn.execute(
        "DELETE FROM inferred_schema_fields "
        " WHERE id = %s AND workspace_id = %s",
        (field_id, workspace_id),
    )
    n = getattr(cur, "rowcount", 0) or 0
    if n == 0:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="inferred field not found")
    return InferredFieldDeleteResponse(deleted=n)


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

    schema = await update_schema(conn, workspace_id, schema_id, body)
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
