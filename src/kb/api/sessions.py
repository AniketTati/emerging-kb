"""B6a / WA-12 — chat session + turn endpoints.

POST /sessions                  — create a new conversation session
GET  /sessions                  — list recent sessions for the workspace
GET  /sessions/{id}             — read one session + its carry-forward state
GET  /sessions/{id}/turns       — list turns in a session (chronological)
GET  /sessions/{id}/context     — build the ChatContext for the next turn
                                  (Plan inspector reads this)
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from kb.api.deps import current_workspace_id, kb_app_connection
from kb.db.pool import Connection
from kb.domain.chat_memory import (
    DEFAULT_HOT_TURNS,
    build_chat_context,
    create_session,
    list_recent_sessions,
    read_last_k_turns,
    read_session,
)


router = APIRouter(tags=["sessions"])


class SessionCreateRequest(BaseModel):
    user_id: str | None = None
    title: str | None = None


class SessionOut(BaseModel):
    id: str
    workspace_id: str
    user_id: str | None
    created_at: str
    last_active_at: str
    carry_forward_entities: list[str] = Field(default_factory=list)
    carry_forward_filters: dict = Field(default_factory=dict)
    prior_result_set_id: str | None = None
    older_turn_summary: str = ""
    title: str | None = None


class SessionsListResponse(BaseModel):
    items: list[SessionOut] = Field(default_factory=list)


class SessionCreateResponse(BaseModel):
    id: str


class TurnOut(BaseModel):
    id: str
    session_id: str
    turn_index: int
    user_query: str
    resolved_query: str | None
    answer: str | None
    citations: list
    created_at: str


class TurnsListResponse(BaseModel):
    items: list[TurnOut] = Field(default_factory=list)


class ContextResponse(BaseModel):
    session_id: str
    last_turn_id: str | None
    carry_forward_entities: list[str] = Field(default_factory=list)
    carry_forward_filters: dict = Field(default_factory=dict)
    prior_result_set_id: str | None = None
    older_turn_summary: str = ""
    last_k_verbatim_turns: list[dict] = Field(default_factory=list)


def _session_to_out(s) -> SessionOut:
    return SessionOut(
        id=s.id,
        workspace_id=s.workspace_id,
        user_id=s.user_id,
        created_at=s.created_at,
        last_active_at=s.last_active_at,
        carry_forward_entities=list(s.carry_forward_entities),
        carry_forward_filters=s.carry_forward_filters,
        prior_result_set_id=s.prior_result_set_id,
        older_turn_summary=s.older_turn_summary,
        title=s.title,
    )


@router.post(
    "/sessions",
    response_model=SessionCreateResponse,
    summary="Create a new chat session for conversational follow-ups",
)
async def post_session(
    body: SessionCreateRequest,
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> SessionCreateResponse:
    sid = await create_session(
        conn,
        workspace_id=workspace_id,
        user_id=body.user_id,
        title=body.title,
    )
    return SessionCreateResponse(id=sid)


@router.get(
    "/sessions",
    response_model=SessionsListResponse,
    summary="List recent sessions in this workspace",
)
async def get_sessions(
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
    limit: int = Query(default=50, ge=1, le=200),
) -> SessionsListResponse:
    rows = await list_recent_sessions(
        conn, workspace_id=workspace_id, limit=limit,
    )
    return SessionsListResponse(items=[_session_to_out(r) for r in rows])


@router.get(
    "/sessions/{session_id}",
    response_model=SessionOut,
    summary="Read one session + its carry-forward state",
)
async def get_session(
    session_id: str,
    workspace_id: Annotated[str, Depends(current_workspace_id)],  # noqa: ARG001
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> SessionOut:
    s = await read_session(conn, session_id=session_id)
    if s is None:
        raise HTTPException(status_code=404, detail="session not found")
    return _session_to_out(s)


@router.get(
    "/sessions/{session_id}/turns",
    response_model=TurnsListResponse,
    summary="List the chat turns in a session (most recent first)",
)
async def get_session_turns(
    session_id: str,
    workspace_id: Annotated[str, Depends(current_workspace_id)],  # noqa: ARG001
    conn: Annotated[Connection, Depends(kb_app_connection)],
    limit: int = Query(default=DEFAULT_HOT_TURNS, ge=1, le=500),
) -> TurnsListResponse:
    s = await read_session(conn, session_id=session_id)
    if s is None:
        raise HTTPException(status_code=404, detail="session not found")
    turns = await read_last_k_turns(conn, session_id=session_id, k=limit)
    return TurnsListResponse(items=[
        TurnOut(
            id=t.id,
            session_id=t.session_id,
            turn_index=t.turn_index,
            user_query=t.user_query,
            resolved_query=t.resolved_query,
            answer=t.answer,
            citations=t.citations,
            created_at=t.created_at,
        )
        for t in turns
    ])


@router.get(
    "/sessions/{session_id}/context",
    response_model=ContextResponse,
    summary="Build the ChatContext that the next chat turn would receive",
)
async def get_session_context(
    session_id: str,
    workspace_id: Annotated[str, Depends(current_workspace_id)],  # noqa: ARG001
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> ContextResponse:
    ctx = await build_chat_context(conn, session_id=session_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail="session not found")
    return ContextResponse(
        session_id=ctx.session_id,
        last_turn_id=ctx.last_turn_id,
        carry_forward_entities=list(ctx.carry_forward_entities),
        carry_forward_filters=ctx.carry_forward_filters,
        prior_result_set_id=ctx.prior_result_set_id,
        older_turn_summary=ctx.older_turn_summary,
        last_k_verbatim_turns=list(ctx.last_k_verbatim_turns),
    )
