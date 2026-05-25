"""WA-3 / Design 3 — Doc chain HTTP endpoints.

Per Design 3 §"UI surface" + ui_design.md §6.9 "Doc Detail panel —
Chain section":

  GET  /files/{file_id}/chain   chain (+ members) the file belongs to
  GET  /chains                  list chains for the workspace
  GET  /chains/{chain_id}       one chain + members + role/version
  POST /chains/{chain_id}/members/{doc_id}/unlink
                                 Design 3 §"Failure modes" Unlink action

Workspace-scoped via RLS (set by WorkspaceMiddleware).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from kb.api.deps import current_workspace_id, kb_app_connection
from kb.db.pool import Connection
from kb.domain.doc_chains import (
    CHAIN_TYPES,
    DocChainMemberRecord,
    DocChainRecord,
    find_chain_for_doc,
    get_chain,
    list_chains,
    read_members,
    remove_member,
)


router = APIRouter(tags=["doc_chains"])


# ---------------------------------------------------------------------------
# Response models — fix the contract for UI clients
# ---------------------------------------------------------------------------


class ChainMemberOut(BaseModel):
    chain_id: str
    doc_id: str
    version_index: int
    role: str
    parent_doc_id: str | None = None
    added_at: str


class ChainOut(BaseModel):
    id: str
    type: str
    title: str | None = None
    current_version_id: str | None = None
    chain_key: str | None = None
    member_count: int
    detection_confidence: float
    created_at: str
    members: list[ChainMemberOut] = Field(default_factory=list)


class ChainListResponse(BaseModel):
    items: list[ChainOut] = Field(default_factory=list)


class ChainForFileOut(BaseModel):
    """`/files/{id}/chain` — chain + this file's role in it."""
    chain: ChainOut
    file_role: str
    file_version_index: int


# ---------------------------------------------------------------------------
# Conversions
# ---------------------------------------------------------------------------


def _chain_to_response(
    chain: DocChainRecord, members: list[DocChainMemberRecord],
) -> ChainOut:
    return ChainOut(
        id=chain.id,
        type=chain.type,
        title=chain.title,
        current_version_id=chain.current_version_id,
        chain_key=chain.chain_key,
        member_count=chain.member_count,
        detection_confidence=chain.detection_confidence,
        created_at=chain.created_at,
        members=[
            ChainMemberOut(
                chain_id=m.chain_id,
                doc_id=m.doc_id,
                version_index=m.version_index,
                role=m.role,
                parent_doc_id=m.parent_doc_id,
                added_at=m.added_at,
            )
            for m in members
        ],
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/files/{file_id}/chain",
    response_model=ChainForFileOut | None,
    summary="The doc chain this file belongs to (404 if no chain)",
)
async def get_chain_for_file(
    file_id: str,
    workspace_id: Annotated[str, Depends(current_workspace_id)],  # noqa: ARG001
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> ChainForFileOut:
    pair = await find_chain_for_doc(conn, doc_id=file_id)
    if pair is None:
        raise HTTPException(status_code=404, detail="file is not in any chain")
    chain, member = pair
    members = await read_members(conn, chain_id=chain.id)
    return ChainForFileOut(
        chain=_chain_to_response(chain, members),
        file_role=member.role,
        file_version_index=member.version_index,
    )


@router.get(
    "/chains",
    response_model=ChainListResponse,
    summary="List doc chains in this workspace",
)
async def get_chains(
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
    chain_type: str | None = Query(default=None, description=f"Filter by type. Allowed: {', '.join(CHAIN_TYPES)}"),
    limit: int = Query(default=100, ge=1, le=500),
) -> ChainListResponse:
    if chain_type is not None and chain_type not in CHAIN_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"chain_type must be one of {CHAIN_TYPES} (got {chain_type!r})",
        )
    chains = await list_chains(
        conn, workspace_id=workspace_id, chain_type=chain_type, limit=limit,
    )
    items: list[ChainOut] = []
    for chain in chains:
        members = await read_members(conn, chain_id=chain.id)
        items.append(_chain_to_response(chain, members))
    return ChainListResponse(items=items)


@router.get(
    "/chains/{chain_id}",
    response_model=ChainOut,
    summary="Read one chain by id with all its members",
)
async def get_one_chain(
    chain_id: str,
    workspace_id: Annotated[str, Depends(current_workspace_id)],  # noqa: ARG001
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> ChainOut:
    chain = await get_chain(conn, chain_id=chain_id)
    if chain is None:
        raise HTTPException(status_code=404, detail="chain not found")
    members = await read_members(conn, chain_id=chain.id)
    return _chain_to_response(chain, members)


@router.post(
    "/chains/{chain_id}/members/{doc_id}/unlink",
    response_model=ChainOut,
    summary="Remove a doc from a chain (Design 3 §Failure modes Unlink action)",
)
async def post_unlink_member(
    chain_id: str,
    doc_id: str,
    workspace_id: Annotated[str, Depends(current_workspace_id)],  # noqa: ARG001
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> ChainOut:
    deleted = await remove_member(conn, chain_id=chain_id, doc_id=doc_id)
    if not deleted:
        raise HTTPException(
            status_code=404, detail="not a member of this chain",
        )
    chain = await get_chain(conn, chain_id=chain_id)
    if chain is None:
        raise HTTPException(status_code=404, detail="chain not found")
    members = await read_members(conn, chain_id=chain.id)
    return _chain_to_response(chain, members)
