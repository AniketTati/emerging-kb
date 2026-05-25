"""Phase 9 — Server-Sent Events (SSE) endpoints.

Two streams:
- GET /upload/:file_id/status — polls `file_lifecycle` every
  KB_SSE_POLL_INTERVAL_MS (default 1000), emits each new event as JSON,
  closes when lifecycle_state ∈ {ready, failed}.
- GET /chat/:query_id/stream — replays the cached `query_log.answer` in
  KB_SSE_REPLAY_CHUNK_SIZE chunks (default 50 chars), KB_SSE_REPLAY_CHUNK_MS
  apart (default 50ms).

Wire format: standard text/event-stream
    event: <type>\\ndata: <json>\\n\\n
Event types: 'lifecycle' (upload), 'chunk' / 'done' (chat replay),
'heartbeat' (idle keepalive), 'error' (stream error).

Heartbeat every 15s prevents proxy idle timeouts (nginx default 60s).
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Annotated, Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from kb.api.deps import current_workspace_id, kb_app_connection
from kb.db.pool import Connection


_POLL_INTERVAL_MS = int(os.environ.get("KB_SSE_POLL_INTERVAL_MS", "1000"))
_HEARTBEAT_INTERVAL_S = float(os.environ.get("KB_SSE_HEARTBEAT_S", "15"))
_REPLAY_CHUNK_SIZE = int(os.environ.get("KB_SSE_REPLAY_CHUNK_SIZE", "50"))
_REPLAY_CHUNK_MS = int(os.environ.get("KB_SSE_REPLAY_CHUNK_MS", "50"))
_TERMINAL_STATES = {"ready", "failed"}


router = APIRouter(tags=["sse"])


# ---------------------------------------------------------------------------
# Wire-format helpers
# ---------------------------------------------------------------------------


def _event(event_type: str, payload: dict[str, Any] | None = None) -> str:
    body = json.dumps(payload or {}, default=str)
    return f"event: {event_type}\ndata: {body}\n\n"


def parse_event_stream(text: str) -> list[dict[str, Any]]:
    """Test helper — parse a `text/event-stream` blob into a list of
    `{event, data}` dicts. Skips empty blocks and malformed events."""
    out: list[dict[str, Any]] = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        event_type: str | None = None
        data: str | None = None
        for line in block.splitlines():
            if line.startswith("event:"):
                event_type = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_part = line[len("data:"):].strip()
                data = data_part if data is None else (data + data_part)
        if event_type is None:
            continue
        try:
            payload = json.loads(data) if data else {}
        except json.JSONDecodeError:
            payload = {}
        out.append({"event": event_type, "data": payload})
    return out


# ---------------------------------------------------------------------------
# GET /upload/:file_id/status — lifecycle SSE
# ---------------------------------------------------------------------------


async def _stream_upload_status(
    conn: Connection,
    workspace_id: str,
    file_id: str,
) -> AsyncIterator[str]:
    """Polls file_lifecycle for new events. Closes on terminal state."""
    last_created_at = None
    last_heartbeat = asyncio.get_event_loop().time()

    while True:
        if last_created_at is None:
            sql = (
                "SELECT id, file_id, from_state, to_state, event, payload, created_at "
                "FROM file_lifecycle WHERE workspace_id = %s AND file_id = %s "
                "ORDER BY created_at ASC, id ASC"
            )
            params: tuple = (workspace_id, file_id)
        else:
            sql = (
                "SELECT id, file_id, from_state, to_state, event, payload, created_at "
                "FROM file_lifecycle WHERE workspace_id = %s AND file_id = %s "
                "AND created_at > %s "
                "ORDER BY created_at ASC, id ASC"
            )
            params = (workspace_id, file_id, last_created_at)

        cur = await conn.execute(sql, params)
        rows = await cur.fetchall()

        terminal_seen = False
        for row in rows:
            event_payload = {
                "id": str(row[0]),
                "file_id": str(row[1]),
                "from_state": row[2],
                "to_state": row[3],
                "event": row[4],
                "payload": row[5] or {},
                "created_at": row[6].isoformat(),
            }
            yield _event("lifecycle", event_payload)
            last_created_at = row[6]
            if row[3] in _TERMINAL_STATES:
                terminal_seen = True

        if terminal_seen:
            yield _event("done", {"reason": "terminal_state"})
            return

        # Heartbeat keepalive (decision #4)
        now = asyncio.get_event_loop().time()
        if rows:
            last_heartbeat = now
        elif now - last_heartbeat >= _HEARTBEAT_INTERVAL_S:
            yield _event("heartbeat", {})
            last_heartbeat = now

        await asyncio.sleep(_POLL_INTERVAL_MS / 1000.0)


@router.get(
    "/upload/{file_id}/status",
    summary="Stream live lifecycle events for an in-flight upload (SSE)",
    responses={
        200: {
            "description": "text/event-stream of lifecycle events",
            "content": {"text/event-stream": {}},
        },
        404: {"description": "File not in workspace"},
    },
)
async def get_upload_status(
    file_id: str,
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> StreamingResponse:
    # Pre-flight 404 check — fail before opening the stream (decision #8 analog).
    cur = await conn.execute(
        "SELECT 1 FROM files WHERE id = %s AND workspace_id = %s",
        (file_id, workspace_id),
    )
    row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="file not found in workspace")

    async def _gen() -> AsyncIterator[str]:
        async for chunk in _stream_upload_status(conn, workspace_id, file_id):
            yield chunk

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# GET /chat/:query_id/stream — replay cached answer in chunks
# ---------------------------------------------------------------------------


async def _stream_chat_replay(
    conn: Connection,
    workspace_id: str,
    query_id: str,
) -> AsyncIterator[str]:
    cur = await conn.execute(
        "SELECT answer, citations, refused, refusal_reason, model_id "
        "FROM query_log WHERE id = %s AND workspace_id = %s",
        (query_id, workspace_id),
    )
    row = await cur.fetchone()
    if row is None:
        # Shouldn't happen — endpoint already 404'd. Safety net.
        yield _event("error", {"type": "not_found", "detail": "query_id not found"})
        return

    answer, citations, refused, refusal_reason, model_id = row
    answer = answer or ""

    # Wave A: deterministic char chunks (decision #7).
    if not answer:
        # Refusal envelope — emit empty chunk then done.
        yield _event("done", {
            "refused": bool(refused),
            "refusal_reason": refusal_reason,
            "citations": citations or [],
            "model_id": model_id or "",
        })
        return

    cursor = 0
    while cursor < len(answer):
        chunk = answer[cursor : cursor + _REPLAY_CHUNK_SIZE]
        yield _event("chunk", {"text": chunk, "offset": cursor})
        cursor += _REPLAY_CHUNK_SIZE
        if cursor < len(answer):
            await asyncio.sleep(_REPLAY_CHUNK_MS / 1000.0)

    yield _event("done", {
        "refused": bool(refused),
        "refusal_reason": refusal_reason,
        "citations": citations or [],
        "model_id": model_id or "",
    })


@router.get(
    "/chat/{query_id}/stream",
    summary="Re-stream the cached answer for a past /chat call (SSE)",
    responses={
        200: {
            "description": "text/event-stream of chunk + done events",
            "content": {"text/event-stream": {}},
        },
        404: {"description": "query_id not found in workspace"},
    },
)
async def get_chat_stream(
    query_id: str,
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> StreamingResponse:
    # Decision #8 — 404 BEFORE opening the stream.
    cur = await conn.execute(
        "SELECT 1 FROM query_log WHERE id = %s AND workspace_id = %s",
        (query_id, workspace_id),
    )
    row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="query_id not found in workspace")

    async def _gen() -> AsyncIterator[str]:
        async for chunk in _stream_chat_replay(conn, workspace_id, query_id):
            yield chunk

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
