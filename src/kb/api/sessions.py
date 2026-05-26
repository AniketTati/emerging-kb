"""B6a / WA-12 — chat session + turn endpoints.

POST   /sessions                — create a new conversation session
GET    /sessions                — list recent sessions for the workspace
GET    /sessions/{id}           — read one session + its carry-forward state
GET    /sessions/{id}/turns     — list turns in a session (chronological)
GET    /sessions/{id}/context   — build the ChatContext for the next turn
                                  (Plan inspector reads this)
DELETE /sessions/{id}           — remove one session + its turns
POST   /sessions/delete-batch   — remove many sessions at once
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
    delete_session,
    delete_sessions_batch,
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
    # Pipeline-stage metadata — pulled from query_log via LEFT JOIN so
    # the chat UI can render Mode / Intent / CRAG / Faithfulness when
    # replaying a session (otherwise the inspector shows "?"). Each
    # field is optional because query_log_id can be NULL on older turns
    # or when the audit insert failed.
    mode: str | None = None
    intent: str | None = None
    intent_confidence: float | None = None
    crag_score: float | None = None
    faithfulness_verdict: str | None = None
    faithfulness_score: float | None = None
    refused: bool | None = None
    refusal_reason: str | None = None


class TurnsListResponse(BaseModel):
    items: list[TurnOut] = Field(default_factory=list)


class DeleteResponse(BaseModel):
    deleted: int


class DeleteBatchRequest(BaseModel):
    session_ids: list[str] = Field(default_factory=list, max_length=200)


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
    # Pull pipeline-stage metadata from query_log via LEFT JOIN. Without
    # this join, the chat UI replays a session with "Intent ?" /
    # "Faithfulness ?" because that data lived on query_log not
    # chat_turns. Returns chronological (oldest first) for replay.
    cur = await conn.execute(
        """
        SELECT t.id::text, t.session_id::text, t.turn_index,
               t.user_query, t.resolved_query, t.answer, t.citations,
               t.created_at::text, t.query_log_id::text,
               ql.mode, ql.intent, ql.intent_confidence,
               ql.crag_score, ql.faithfulness_verdict,
               ql.faithfulness_score, ql.refused, ql.refusal_reason
          FROM chat_turns t
          LEFT JOIN query_log ql ON ql.id = t.query_log_id
         WHERE t.session_id = %s
         ORDER BY t.turn_index ASC
         LIMIT %s
        """,
        (session_id, limit),
    )
    rows = await cur.fetchall()
    items: list[TurnOut] = []
    for r in rows:
        items.append(TurnOut(
            id=r[0],
            session_id=r[1],
            turn_index=int(r[2]),
            user_query=r[3],
            resolved_query=r[4],
            answer=r[5],
            citations=(r[6] if isinstance(r[6], list) else (r[6] or [])),
            created_at=r[7],
            mode=r[9],
            intent=r[10],
            intent_confidence=(float(r[11]) if r[11] is not None else None),
            crag_score=(float(r[12]) if r[12] is not None else None),
            faithfulness_verdict=r[13],
            faithfulness_score=(float(r[14]) if r[14] is not None else None),
            refused=r[15],
            refusal_reason=r[16],
        ))
    return TurnsListResponse(items=items)


@router.delete(
    "/sessions/{session_id}",
    response_model=DeleteResponse,
    summary="Permanently delete a chat session and all its turns",
)
async def delete_session_endpoint(
    session_id: str,
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> DeleteResponse:
    """Hard delete — session row + cascade to chat_turns. The chat-history
    sidebar's row-trash icon hits this directly. 404 if the session
    doesn't exist in the caller's workspace (RLS filters)."""
    n = await delete_session(
        conn, session_id=session_id, workspace_id=workspace_id,
    )
    if n == 0:
        raise HTTPException(status_code=404, detail="session not found")
    return DeleteResponse(deleted=n)


@router.post(
    "/sessions/delete-batch",
    response_model=DeleteResponse,
    summary="Delete many chat sessions in one round-trip (multi-select UI)",
)
async def delete_sessions_batch_endpoint(
    body: DeleteBatchRequest,
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> DeleteResponse:
    """Used by the sidebar's "select N, delete all" affordance. Capped
    at 200 ids per request (Pydantic max_length on the request body)
    to keep one txn bounded."""
    if not body.session_ids:
        return DeleteResponse(deleted=0)
    n = await delete_sessions_batch(
        conn, session_ids=body.session_ids, workspace_id=workspace_id,
    )
    return DeleteResponse(deleted=n)


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
