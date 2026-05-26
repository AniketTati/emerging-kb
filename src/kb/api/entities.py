"""B1 / WA-4 + WA-5 — entity-relationship + graph + triples HTTP surface.

Endpoints:
  GET /entities/{entity_id}/relationships        WA-4
  GET /entities/{entity_id}/graph-neighbors      WA-5
  GET /triples?file_id=...                       WA-4 (debug + audit)

All workspace-scoped via X-Workspace-Id + RLS.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from kb.api.deps import current_workspace_id, kb_app_connection
from kb.db.pool import Connection
from kb.domain.graph import (
    EDGE_KINDS,
    GraphEdgeRecord,
    list_neighbors,
)
from kb.domain.relationships import (
    RelationshipRecord,
    list_relationships_for_entity,
    read_evidence_for_relationship,
)
from kb.domain.triples import (
    TripleRecord,
    read_triples_for_file,
    read_triples_for_workspace,
)


router = APIRouter(tags=["entities"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class RelationshipOut(BaseModel):
    id: str
    subject_entity_id: str
    object_entity_id: str
    predicate: str
    confidence: float
    n_evidence: int
    created_at: str
    updated_at: str


class RelationshipListResponse(BaseModel):
    items: list[RelationshipOut] = Field(default_factory=list)


class GraphEdgeOut(BaseModel):
    id: str
    src_entity_id: str
    dst_entity_id: str
    edge_kind: str
    weight: float
    source_refs: list[Any] = Field(default_factory=list)


class GraphNeighborsResponse(BaseModel):
    items: list[GraphEdgeOut] = Field(default_factory=list)


class TripleOut(BaseModel):
    id: str
    file_id: str
    chunk_id: str | None = None
    subject_text: str
    predicate_text: str
    object_text: str
    confidence: float
    model_id: str
    created_at: str


class TripleListResponse(BaseModel):
    items: list[TripleOut] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Conversions
# ---------------------------------------------------------------------------


def _rel_to_resp(r: RelationshipRecord) -> RelationshipOut:
    return RelationshipOut(
        id=r.id,
        subject_entity_id=r.subject_entity_id,
        object_entity_id=r.object_entity_id,
        predicate=r.predicate,
        confidence=r.confidence,
        n_evidence=r.n_evidence,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


def _edge_to_resp(e: GraphEdgeRecord) -> GraphEdgeOut:
    return GraphEdgeOut(
        id=e.id,
        src_entity_id=e.src_entity_id,
        dst_entity_id=e.dst_entity_id,
        edge_kind=e.edge_kind,
        weight=e.weight,
        source_refs=e.source_refs,
    )


def _triple_to_resp(t: TripleRecord) -> TripleOut:
    return TripleOut(
        id=t.id,
        file_id=t.file_id,
        chunk_id=t.chunk_id,
        subject_text=t.subject_text,
        predicate_text=t.predicate_text,
        object_text=t.object_text,
        confidence=t.confidence,
        model_id=t.model_id,
        created_at=t.created_at,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/entities/{entity_id}/relationships",
    response_model=RelationshipListResponse,
    summary="List relationships involving an entity",
)
async def get_entity_relationships(
    entity_id: str,
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
    direction: str = Query(default="both", description="subject | object | both"),
    limit: int = Query(default=200, ge=1, le=500),
) -> RelationshipListResponse:
    if direction not in ("subject", "object", "both"):
        raise HTTPException(
            status_code=400,
            detail="direction must be one of: subject, object, both",
        )
    rels = await list_relationships_for_entity(
        conn,
        workspace_id=workspace_id,
        entity_id=entity_id,
        direction=direction,
        limit=limit,
    )
    return RelationshipListResponse(items=[_rel_to_resp(r) for r in rels])


@router.get(
    "/entities/{entity_id}/graph-neighbors",
    response_model=GraphNeighborsResponse,
    summary="List 1-hop graph neighbors for an entity (HippoRAG adjacency)",
)
async def get_entity_graph_neighbors(
    entity_id: str,
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
    direction: str = Query(default="both", description="out | in | both"),
    edge_kind: str | None = Query(default=None, description=f"Filter by kind. Allowed: {', '.join(EDGE_KINDS)}"),
    limit: int = Query(default=200, ge=1, le=500),
) -> GraphNeighborsResponse:
    if direction not in ("out", "in", "both"):
        raise HTTPException(
            status_code=400, detail="direction must be one of: out, in, both",
        )
    if edge_kind is not None and edge_kind not in EDGE_KINDS:
        raise HTTPException(
            status_code=400,
            detail=f"edge_kind must be one of {EDGE_KINDS} (got {edge_kind!r})",
        )
    edges = await list_neighbors(
        conn,
        workspace_id=workspace_id,
        entity_id=entity_id,
        direction=direction,
        edge_kind=edge_kind,
        limit=limit,
    )
    return GraphNeighborsResponse(items=[_edge_to_resp(e) for e in edges])


class RenameEntityRequest(BaseModel):
    """Body for PATCH /entities/{id}/canonical-name."""
    canonical_name: str = Field(min_length=1, max_length=512)

    @field_validator("canonical_name")
    @classmethod
    def _strip_and_nonempty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("canonical_name must not be blank")
        return v


class RenameEntityResponse(BaseModel):
    id: str
    canonical_name: str
    previous_canonical_name: str
    entity_type: str


@router.patch(
    "/entities/{entity_id}/canonical-name",
    response_model=RenameEntityResponse,
    summary=(
        "Rename an entity's canonical name (Explore 'Edit canonical' "
        "action). Workspace-scoped via RLS; 404 when the entity doesn't "
        "exist in the caller's workspace."
    ),
)
async def patch_entity_canonical_name(
    entity_id: str,
    body: RenameEntityRequest,
    workspace_id: Annotated[str, Depends(current_workspace_id)],  # noqa: ARG001
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> RenameEntityResponse:
    # Read first so we can return the previous value (audit-friendly)
    # AND surface 404 cleanly instead of "0 rows updated" silent-no-op.
    cur = await conn.execute(
        "SELECT canonical_name, entity_type FROM entities WHERE id = %s",
        (entity_id,),
    )
    row = await cur.fetchone()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"entity {entity_id} not found in this workspace",
        )
    prev_name, entity_type = row[0], row[1]

    # No-op when the user re-submits the same value — saves a write
    # and avoids spurious bumps to updated_at downstream.
    if prev_name == body.canonical_name:
        return RenameEntityResponse(
            id=entity_id,
            canonical_name=body.canonical_name,
            previous_canonical_name=prev_name,
            entity_type=entity_type,
        )

    await conn.execute(
        "UPDATE entities SET canonical_name = %s, updated_at = NOW() "
        "WHERE id = %s",
        (body.canonical_name, entity_id),
    )
    # No explicit commit: kb_app_connection wraps the call in a
    # transaction context (see src/kb/api/deps.py) that commits on
    # success and rolls back on exception.

    return RenameEntityResponse(
        id=entity_id,
        canonical_name=body.canonical_name,
        previous_canonical_name=prev_name,
        entity_type=entity_type,
    )


@router.get(
    "/triples",
    response_model=TripleListResponse,
    summary="List extracted triples (debug + audit surface)",
)
async def get_triples(
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
    file_id: str | None = Query(default=None, description="If set, list triples for this file only"),
    limit: int = Query(default=200, ge=1, le=500),
) -> TripleListResponse:
    if file_id is not None:
        triples = await read_triples_for_file(conn, file_id=file_id)
        triples = triples[:limit]
    else:
        triples = await read_triples_for_workspace(
            conn, workspace_id=workspace_id, limit=limit,
        )
    return TripleListResponse(items=[_triple_to_resp(t) for t in triples])
