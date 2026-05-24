"""Files domain layer — pydantic models + repo functions for files +
file_lifecycle (append-only audit) tables.

Phase 2a. Lifecycle transitions go through `record_lifecycle_event` which
INSERTs into the immutable `file_lifecycle` table — never UPDATEs.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from kb.db.pool import Connection


# ---------------------------------------------------------------------------
# Pydantic — request bodies + response shapes
# ---------------------------------------------------------------------------


class FileCreateJson(BaseModel):
    """Mode-B body: pre-staged file at a known MinIO object key."""

    model_config = ConfigDict(extra="forbid")

    minio_object_key: Annotated[str, Field(min_length=1, max_length=500)]
    name: Annotated[str, Field(min_length=1, max_length=500)]


class FileResponse(BaseModel):
    id: str
    name: str
    content_sha: str
    mime_type: str
    size_bytes: int
    doc_type: str | None
    lifecycle_state: str
    created_at: str
    updated_at: str


class LifecycleEvent(BaseModel):
    from_state: str | None
    to_state: str
    event: str
    payload: dict[str, Any]
    created_at: str


class FileWithLifecycleResponse(FileResponse):
    lifecycle: list[LifecycleEvent]


class FileListResponse(BaseModel):
    items: list[FileResponse]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class FileNotFoundError(Exception):
    """File missing / soft-deleted / wrong workspace."""


class FileAlreadyExistsByShaError(Exception):
    """A non-deleted file with this content_sha already exists in the workspace
    — caller should return the existing row instead of creating a new one."""

    def __init__(self, existing: FileResponse) -> None:
        self.existing = existing
        super().__init__(f"dedup hit: existing file id={existing.id}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso(ts: datetime) -> str:
    return ts.astimezone().isoformat().replace("+00:00", "Z")


_FILE_COLS = (
    "id, name, content_sha, mime_type, size_bytes, doc_type, "
    "lifecycle_state, created_at, updated_at"
)


def _row_to_file(row: tuple) -> FileResponse:
    return FileResponse(
        id=str(row[0]),
        name=row[1],
        content_sha=row[2],
        mime_type=row[3],
        size_bytes=row[4],
        doc_type=row[5],
        lifecycle_state=row[6],
        created_at=_iso(row[7]),
        updated_at=_iso(row[8]),
    )


# ---------------------------------------------------------------------------
# Repo — files + lifecycle
# ---------------------------------------------------------------------------


async def find_active_by_sha(
    conn: Connection, content_sha: str
) -> FileResponse | None:
    """Returns the existing non-deleted file in this workspace with the given
    content_sha, or None. Used by POST /files for content-hash dedup."""
    cur = await conn.execute(
        f"SELECT {_FILE_COLS} FROM files "
        f"WHERE content_sha = %s AND lifecycle_state <> 'deleted'",
        (content_sha,),
    )
    row = await cur.fetchone()
    return _row_to_file(row) if row else None


async def create_file(
    conn: Connection,
    *,
    workspace_id: str,
    name: str,
    content_sha: str,
    object_key: str,
    mime_type: str,
    size_bytes: int,
    upload_payload: dict[str, Any] | None = None,
) -> FileResponse:
    """INSERT a new files row + initial file_lifecycle event (null → 'queued').

    Caller has already done content-hash dedup (find_active_by_sha returned
    None) — if there's still a UNIQUE violation we surface as FileAlreadyExists.

    `upload_payload` is recorded verbatim on the initial 'upload' event so
    callers can persist context (e.g., Phase 2c §5.6.1 #11's `forced_parser`).
    """
    cur = await conn.execute(
        f"INSERT INTO files "
        f"(workspace_id, name, content_sha, object_key, mime_type, size_bytes) "
        f"VALUES (%s, %s, %s, %s, %s, %s) "
        f"RETURNING {_FILE_COLS}",
        (workspace_id, name, content_sha, object_key, mime_type, size_bytes),
    )
    row = await cur.fetchone()
    file_response = _row_to_file(row)

    await record_lifecycle_event(
        conn,
        file_id=file_response.id,
        workspace_id=workspace_id,
        from_state=None,
        to_state="queued",
        event="upload",
        payload=upload_payload or {},
    )
    return file_response


async def record_lifecycle_event(
    conn: Connection,
    *,
    file_id: str,
    workspace_id: str,
    from_state: str | None,
    to_state: str,
    event: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """Append a row to file_lifecycle. Caller is responsible for also UPDATEing
    files.lifecycle_state when the transition advances state (this is just
    the audit append)."""
    await conn.execute(
        "INSERT INTO file_lifecycle "
        "(file_id, workspace_id, from_state, to_state, event, payload) "
        "VALUES (%s, %s, %s, %s, %s, %s::jsonb)",
        (file_id, workspace_id, from_state, to_state, event,
         json.dumps(payload or {})),
    )


async def list_files(
    conn: Connection, limit: int, offset: int
) -> FileListResponse:
    cur = await conn.execute(
        f"SELECT {_FILE_COLS} FROM files "
        f"WHERE lifecycle_state <> 'deleted' "
        f"ORDER BY created_at DESC, id DESC "
        f"LIMIT %s OFFSET %s",
        (limit, offset),
    )
    rows = await cur.fetchall()

    cur = await conn.execute(
        "SELECT count(*) FROM files WHERE lifecycle_state <> 'deleted'"
    )
    total = (await cur.fetchone())[0]

    return FileListResponse(
        items=[_row_to_file(r) for r in rows],
        total=total, limit=limit, offset=offset,
    )


async def get_file(conn: Connection, file_id: str) -> FileResponse:
    cur = await conn.execute(
        f"SELECT {_FILE_COLS} FROM files "
        f"WHERE id = %s AND lifecycle_state <> 'deleted'",
        (file_id,),
    )
    row = await cur.fetchone()
    if row is None:
        raise FileNotFoundError(file_id)
    return _row_to_file(row)


async def get_file_with_lifecycle(
    conn: Connection, file_id: str
) -> FileWithLifecycleResponse:
    file_resp = await get_file(conn, file_id)

    cur = await conn.execute(
        "SELECT from_state, to_state, event, payload, created_at "
        "FROM file_lifecycle "
        "WHERE file_id = %s "
        "ORDER BY created_at ASC, id ASC",
        (file_id,),
    )
    rows = await cur.fetchall()
    events = [
        LifecycleEvent(
            from_state=r[0],
            to_state=r[1],
            event=r[2],
            payload=r[3] if isinstance(r[3], dict) else (json.loads(r[3]) if r[3] else {}),
            created_at=_iso(r[4]),
        )
        for r in rows
    ]
    return FileWithLifecycleResponse(
        **file_resp.model_dump(), lifecycle=events,
    )


async def soft_delete_file(
    conn: Connection, workspace_id: str, file_id: str
) -> None:
    cur = await conn.execute(
        "UPDATE files SET lifecycle_state = 'deleted', updated_at = now() "
        "WHERE id = %s AND lifecycle_state <> 'deleted' "
        "RETURNING lifecycle_state",
        (file_id,),
    )
    if await cur.fetchone() is None:
        raise FileNotFoundError(file_id)
    # Audit
    await record_lifecycle_event(
        conn,
        file_id=file_id,
        workspace_id=workspace_id,
        from_state=None,  # could fetch prior, but the deleted state is what matters
        to_state="deleted",
        event="soft_delete",
        payload={},
    )


async def transition_lifecycle(
    conn: Connection,
    *,
    workspace_id: str,
    file_id: str,
    to_state: str,
    event: str,
    payload: dict[str, Any] | None = None,
) -> str:
    """Helper for the worker: read current state under FOR UPDATE, write the
    new state to `files`, and append the audit event. Returns the old state
    so the worker can branch on it (e.g., refuse to re-parse if 'parsed').
    """
    cur = await conn.execute(
        "SELECT lifecycle_state FROM files WHERE id = %s FOR UPDATE",
        (file_id,),
    )
    row = await cur.fetchone()
    if row is None:
        raise FileNotFoundError(file_id)
    from_state = row[0]

    await conn.execute(
        "UPDATE files SET lifecycle_state = %s, updated_at = now() WHERE id = %s",
        (to_state, file_id),
    )
    await record_lifecycle_event(
        conn,
        file_id=file_id,
        workspace_id=workspace_id,
        from_state=from_state,
        to_state=to_state,
        event=event,
        payload=payload or {},
    )
    return from_state
