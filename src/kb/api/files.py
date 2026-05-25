"""Files endpoints — api_contracts §5.5–§5.9.

Phase 2a. 5 endpoints under `/files`:
- POST    /files            (multipart OR JSON) — upload + enqueue parse_file
- GET     /files            list
- GET     /files/:id        read with lifecycle history
- GET     /files/:id/pages  list raw pages (paginated)
- DELETE  /files/:id        soft delete (MinIO blob retained)

Dual-mode POST: server inspects Content-Type to branch — multipart for
direct upload, JSON for pre-staged-in-MinIO uploads (used by Phase 10a
streaming UI + tests with pre-staged content).
"""

from __future__ import annotations

import json as _json
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response
from starlette.datastructures import UploadFile
from starlette.requests import Request as StarletteRequest

from kb.api.deps import current_workspace_id, kb_app_connection
from kb.api.errors import BadRequestError
from kb.api.idempotency import (
    cache_response,
    get_cached,
    idempotency_key_optional,
    idempotency_key_required,
)
from kb.config import get_settings
from kb.db.pool import Connection
from kb.domain.files import (
    FileCreateJson,
    FileListResponse,
    FileResponse,
    FileWithLifecycleResponse,
    create_file,
    find_active_by_sha,
    get_file_with_lifecycle,
    list_files,
    soft_delete_file,
)
from kb.domain.raw_pages import RawPageListResponse, list_raw_pages
from kb.parsers import PayloadTooLargeError, UnsupportedMediaTypeError
from kb.storage.files import (
    KB_BUCKET,
    key_for_sha,
    object_exists,
    put_file_bytes,
    sha256_hex,
)
from kb.workers.tasks import parse_file


router = APIRouter(prefix="/files", tags=["files"])


# Phase 2a + 2b + Wave B demo-corpus accepted mime types.
_MIME_WHITELIST = {
    "application/pdf",
    # Phase 2b — xlsx + email
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",  # .xls — let the parser handle format detection
    "message/rfc822",
    # Wave B demo corpus — plain text + Markdown for memo / readme-style docs.
    "text/plain",
    "text/markdown",
}


def _check_pagination(limit: int, offset: int) -> None:
    if limit < 1 or limit > 200:
        raise BadRequestError(f"limit must be 1..200; got {limit}")
    if offset < 0:
        raise BadRequestError(f"offset must be >= 0; got {offset}")


def _check_mime_allowed(mime_type: str) -> None:
    if mime_type not in _MIME_WHITELIST:
        raise UnsupportedMediaTypeError(
            f"mime_type={mime_type!r} not accepted; "
            f"supported: {sorted(_MIME_WHITELIST)}"
        )


def _sniff_mime_from_magic(file_bytes: bytes, default: str) -> str:
    """Phase 2b decision #6: when Content-Type is missing or generic
    (application/octet-stream), classify the file by its magic bytes.

    Returns a mime from the whitelist, or `default` if no magic matches
    (caller will then 415 it via _check_mime_allowed).
    """
    head = file_bytes[:8]
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"PK\x03\x04"):
        # ZIP — Phase 2b treats this as xlsx. Other ZIP formats (pptx, docx)
        # not yet supported; the xlsx parser will surface as ParseError if
        # the ZIP isn't actually an Excel workbook.
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    # Email RFC822 header pattern in first ~200 bytes
    import re
    if re.search(rb"^[A-Z][a-zA-Z-]+:\s", file_bytes[:200], re.MULTILINE):
        return "message/rfc822"
    return default


def _check_size_allowed(size_bytes: int) -> None:
    settings = get_settings()
    if size_bytes > settings.max_upload_bytes:
        raise PayloadTooLargeError(
            f"size={size_bytes} > KB_MAX_UPLOAD_BYTES={settings.max_upload_bytes}"
        )


# ---------------------------------------------------------------------------
# POST /files — dual-mode
# ---------------------------------------------------------------------------


@router.post(
    "",
    summary="Upload a file (multipart) OR register pre-staged content (JSON)",
    responses={
        201: {"model": FileResponse},
        200: {"description": "Content-hash dedup hit — existing file returned"},
        400: {"description": "Missing Idempotency-Key or malformed body"},
        413: {"description": "Payload too large"},
        415: {"description": "Unsupported media type"},
        422: {"description": "Validation error"},
    },
)
async def post_file(
    request: Request,
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    idem_key: Annotated[str, Depends(idempotency_key_required)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> Response:
    # ---- Phase 2c — caller-override ?parser= ----
    # §5.6.1 #11: valid values are auto | docling | gemini. None/empty → auto.
    raw_parser = request.query_params.get("parser")
    forced_parser = _validate_forced_parser(raw_parser)

    # ---- Idempotency-Key replay (HTTP layer) ----
    cached = await get_cached(conn, workspace_id, idem_key)
    if cached is not None:
        body_dict, status_code = cached
        return JSONResponse(
            content=body_dict, status_code=status_code,
            headers={"X-Idempotent-Replay": "true"},
        )

    # ---- Parse body — Content-Type branches the two modes ----
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        file_resp, status = await _handle_multipart(
            request, workspace_id, conn, forced_parser=forced_parser,
        )
    elif content_type.startswith("application/json"):
        file_resp, status = await _handle_json(
            request, workspace_id, conn, forced_parser=forced_parser,
        )
    else:
        raise BadRequestError(
            f"unsupported request Content-Type: {content_type!r} "
            "(expected multipart/form-data or application/json)"
        )

    body_dict = file_resp.model_dump()
    headers: dict[str, str] = {}
    if status == 200:
        headers["X-Dedup-Reason"] = "content-hash"

    # ---- Cache response for Idempotency-Key replay ----
    await cache_response(conn, workspace_id, idem_key, body=body_dict, status_code=status)
    return JSONResponse(content=body_dict, status_code=status, headers=headers)


_VALID_PARSER_OVERRIDES = {"auto", "docling", "gemini"}


def _validate_forced_parser(raw: str | None) -> str | None:
    """Return the normalized `forced_parser` token, or raise
    InvalidParserOverrideError. `None`/empty/`auto` all normalize to None
    (= use the server-side strategy)."""
    from kb.api.errors import InvalidParserOverrideError

    if raw is None or raw == "" or raw == "auto":
        return None
    if raw not in _VALID_PARSER_OVERRIDES:
        raise InvalidParserOverrideError(raw)
    return raw


async def _handle_multipart(
    request: StarletteRequest,
    workspace_id: str,
    conn: Connection,
    *,
    forced_parser: str | None = None,
) -> tuple[FileResponse, int]:
    """Mode A — multipart upload."""
    form = await request.form()
    upload = form.get("file")
    if not isinstance(upload, UploadFile):
        raise BadRequestError("multipart 'file' field is required")

    name = form.get("name") or upload.filename or "untitled"
    if not isinstance(name, str) or not name or len(name) > 500:
        raise BadRequestError("multipart 'name' must be 1-500 chars")

    mime_type = upload.content_type or "application/octet-stream"

    # Read body — enforce size limit
    file_bytes = await upload.read()
    _check_size_allowed(len(file_bytes))

    # Phase 2b decision #6: magic-byte sniff when caller didn't send a useful
    # Content-Type. Lets octet-stream uploads route to the right parser.
    if mime_type in ("application/octet-stream", ""):
        mime_type = _sniff_mime_from_magic(file_bytes, default=mime_type)

    _check_mime_allowed(mime_type)

    content_sha = sha256_hex(file_bytes)

    return await _create_or_dedup(
        conn,
        workspace_id=workspace_id,
        name=name,
        content_sha=content_sha,
        mime_type=mime_type,
        size_bytes=len(file_bytes),
        upload_bytes=file_bytes,
        forced_parser=forced_parser,
    )


async def _handle_json(
    request: StarletteRequest,
    workspace_id: str,
    conn: Connection,
    *,
    forced_parser: str | None = None,
) -> tuple[FileResponse, int]:
    """Mode B — JSON references pre-uploaded MinIO object."""
    try:
        raw = await request.body()
        data = _json.loads(raw)
    except Exception as exc:
        raise BadRequestError(f"invalid JSON body: {exc}") from exc

    body = FileCreateJson.model_validate(data)

    if not object_exists(body.minio_object_key):
        raise BadRequestError(
            f"minio_object_key={body.minio_object_key!r} does not exist in bucket={KB_BUCKET}"
        )

    # Fetch + hash so we know its content_sha and size
    from kb.storage.files import get_file_bytes
    file_bytes = get_file_bytes(body.minio_object_key)
    _check_size_allowed(len(file_bytes))

    content_sha = sha256_hex(file_bytes)

    # Re-derive mime_type from the magic bytes; Mode B doesn't carry it explicitly.
    # Phase 2b decision #6: Mode B doesn't carry mime explicitly — sniff
    # from magic bytes. Supports PDF, xlsx, email; falls back to octet-stream
    # (which then 415s via _check_mime_allowed).
    mime_type = _sniff_mime_from_magic(file_bytes, default="application/octet-stream")
    _check_mime_allowed(mime_type)

    return await _create_or_dedup(
        conn,
        workspace_id=workspace_id,
        name=body.name,
        content_sha=content_sha,
        mime_type=mime_type,
        size_bytes=len(file_bytes),
        upload_bytes=None,  # already in MinIO at minio_object_key; ensure_key_matches below
        prestaged_key=body.minio_object_key,
        forced_parser=forced_parser,
    )


async def _create_or_dedup(
    conn: Connection,
    *,
    workspace_id: str,
    name: str,
    content_sha: str,
    mime_type: str,
    size_bytes: int,
    upload_bytes: bytes | None,
    prestaged_key: str | None = None,
    forced_parser: str | None = None,
) -> tuple[FileResponse, int]:
    """Shared logic for both POST modes — content-hash dedup + create + enqueue."""
    # ---- Content-hash dedup ----
    existing = await find_active_by_sha(conn, content_sha)
    if existing is not None:
        # Return existing with 200 (NOT 409, NOT 201).
        return existing, 200

    # ---- Ensure MinIO has the bytes ----
    canonical_key = key_for_sha(content_sha)
    if upload_bytes is not None:
        put_file_bytes(content_sha, upload_bytes, mime_type=mime_type)
    elif prestaged_key and prestaged_key != canonical_key:
        # Caller staged at a non-canonical key; we re-stage at the canonical one
        # to keep the storage model consistent. Cheap because both keys live
        # in the same bucket.
        from kb.storage.files import get_file_bytes
        # `get_file_bytes` we already called above to compute sha; re-call OK
        bytes_again = get_file_bytes(prestaged_key)
        put_file_bytes(content_sha, bytes_again, mime_type=mime_type)
    # else: prestaged at canonical key — already there.

    # ---- Insert files + initial lifecycle event ----
    # §5.6.1 #11/#12: persist the `forced_parser` override into the initial
    # 'upload' lifecycle event payload so the worker (and any audit-trail
    # consumer) can see what the caller asked for.
    upload_payload: dict[str, str] = {}
    if forced_parser is not None:
        upload_payload["forced_parser"] = forced_parser

    file_resp = await create_file(
        conn,
        workspace_id=workspace_id,
        name=name,
        content_sha=content_sha,
        object_key=canonical_key,
        mime_type=mime_type,
        size_bytes=size_bytes,
        upload_payload=upload_payload,
    )

    # ---- Enqueue parse task ----
    # Procrastinate's defer needs an async DB connection; use the connector
    # that's already on the app. Phase 2c: forward `forced_parser` so the
    # worker hits select_parser_for(..., forced_parser=...) without
    # re-reading the upload event.
    await parse_file.defer_async(file_id=file_resp.id, forced_parser=forced_parser)

    return file_resp, 201


# ---------------------------------------------------------------------------
# GET /files
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=FileListResponse,
    summary="List active files in this workspace",
)
async def get_files(
    request: Request,
    conn: Annotated[Connection, Depends(kb_app_connection)],
    limit: int = Query(default=50),
    offset: int = Query(default=0),
) -> FileListResponse:
    _check_pagination(limit, offset)
    return await list_files(conn, limit=limit, offset=offset)


# ---------------------------------------------------------------------------
# GET /files/:id (with lifecycle history)
# ---------------------------------------------------------------------------


@router.get(
    "/{file_id}",
    response_model=FileWithLifecycleResponse,
    summary="Read one file + lifecycle history",
)
async def get_file_by_id(
    file_id: str,
    request: Request,
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> FileWithLifecycleResponse:
    return await get_file_with_lifecycle(conn, file_id)


# ---------------------------------------------------------------------------
# GET /files/:id/pages
# ---------------------------------------------------------------------------


@router.get(
    "/{file_id}/pages",
    response_model=RawPageListResponse,
    summary="List raw pages for a file (paginated, page_number ASC)",
)
async def get_file_pages(
    file_id: str,
    request: Request,
    conn: Annotated[Connection, Depends(kb_app_connection)],
    limit: int = Query(default=50),
    offset: int = Query(default=0),
) -> RawPageListResponse:
    _check_pagination(limit, offset)
    # 404-gate via the parent.
    from kb.domain.files import get_file
    await get_file(conn, file_id)
    return await list_raw_pages(conn, file_id, limit=limit, offset=offset)


# ---------------------------------------------------------------------------
# DELETE /files/:id (soft)
# ---------------------------------------------------------------------------


@router.delete(
    "/{file_id}",
    status_code=204,
    summary="Soft-delete a file (MinIO blob retained)",
)
async def delete_file(
    file_id: str,
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

    await soft_delete_file(conn, workspace_id, file_id)
    await cache_response(conn, workspace_id, idem_key, body=None, status_code=204)
    return Response(status_code=204)
