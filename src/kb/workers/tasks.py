"""Procrastinate worker tasks — Phase 2a (`parse_file`) + Phase 3a (`chunk_file`).

`parse_file(file_id)` and `chunk_file(file_id)` are the wire-level Procrastinate
tasks. The actual work lives in `parse_file_impl(file_id)` + `chunk_file_impl
(file_id)` — regular async functions tests call directly without spinning up
the queue.

Per build_tracker §5.5 decision #6: 30-min lease. Per §5.5 #7 / §5.7 #9: worker
sets `app.workspace_id` from the file row before any subsequent query. Per
§5.5 #15 / §5.7 #11: parser/chunker exceptions write a `<prior>→failed`
lifecycle event and leave `files.lifecycle_state='failed'`.

Per build_tracker §5.7 decision #9: `parse_file_impl()`'s success path defers
`chunk_file(file_id)` in a SEPARATE transaction so a Procrastinate defer
failure doesn't roll back the successful parse.
"""

from __future__ import annotations

import hashlib
import traceback

from kb.chunking import Chunk, ChunkingError, chunk_pages
from kb.db.pool import open_connection
from kb.domain.chunks import (
    count_chunks_for_file,
    insert_chunk,
    read_pages_for_chunking,
)
from kb.domain.files import (
    FileNotFoundError,
    record_lifecycle_event,
    transition_lifecycle,
)
from kb.domain.raw_pages import insert_raw_page
from kb.parsers import (
    NoParserForMime,
    Page,
    ParseError,
    global_registry,
    register_default_parsers,
)
from kb.storage.files import get_file_bytes
from kb.workers.app import app as procrastinate_app


async def parse_file_impl(file_id: str) -> None:
    """Pure async core. Reads the file row, sets workspace context, fetches
    bytes from MinIO, dispatches to a parser, writes raw_pages + lifecycle.

    Per-stage idempotency: if `files.lifecycle_state == 'parsed'` at entry,
    return immediately.
    """
    register_default_parsers()  # idempotent

    from kb.config import get_settings
    settings = get_settings()
    # Worker runs as superuser — bypasses RLS to read files.workspace_id,
    # then SET LOCAL app.workspace_id for downstream queries that touch
    # other workspace-scoped tables (raw_pages, file_lifecycle, etc.).
    db_url = settings.database_url

    async with open_connection(db_url) as conn:
        async with conn.transaction():
            cur = await conn.execute(
                "SELECT workspace_id, lifecycle_state, object_key, mime_type "
                "FROM files WHERE id = %s",
                (file_id,),
            )
            row = await cur.fetchone()
            if row is None:
                raise FileNotFoundError(file_id)
            # Worker needs to bypass RLS-by-default since it has no incoming
            # request context — set app.workspace_id from the row itself.
            workspace_id, lifecycle_state, object_key, mime_type = row

            # Idempotency: already parsed → no-op.
            if lifecycle_state == "parsed":
                return

            # Set workspace context for all subsequent queries in this tx.
            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)",
                (str(workspace_id),),
            )

            # Now transition to 'parsing'. Reads lifecycle_state under FOR
            # UPDATE so concurrent task invocations serialize.
            await transition_lifecycle(
                conn,
                workspace_id=str(workspace_id),
                file_id=file_id,
                to_state="parsing",
                event="task_started",
                payload={},
            )

        # Outside the first transaction: fetch bytes (no DB), parse (no DB),
        # then re-open a tx to write results.
        try:
            file_bytes = get_file_bytes(object_key)
            magic = file_bytes[:8]
            parser = global_registry().dispatch(
                mime_type=mime_type, magic_bytes=magic,
            )
            doc = await parser.parse(
                file_bytes, file_id=file_id, workspace_id=str(workspace_id),
            )
        except (ParseError, NoParserForMime) as exc:
            await _mark_failed(
                db_url, file_id, str(workspace_id),
                error_class=type(exc).__name__,
                message=str(exc),
            )
            return
        except Exception as exc:
            await _mark_failed(
                db_url, file_id, str(workspace_id),
                error_class=type(exc).__name__,
                message=str(exc),
                traceback_head=traceback.format_exc()[:2000],
            )
            return

        # Success: write raw_pages + advance lifecycle to 'parsed'.
        async with open_connection(db_url) as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.workspace_id', %s, true)",
                    (str(workspace_id),),
                )
                for page in doc.pages:
                    sha = hashlib.sha256(page.text.encode("utf-8")).hexdigest()
                    await insert_raw_page(
                        conn,
                        file_id=file_id,
                        workspace_id=str(workspace_id),
                        page_number=page.page_number,
                        text=page.text,
                        layout_json=page.layout_json,
                        content_sha=sha,
                    )
                await transition_lifecycle(
                    conn,
                    workspace_id=str(workspace_id),
                    file_id=file_id,
                    to_state="parsed",
                    event="parse_done",
                    payload={
                        "parser": type(parser).__name__,
                        "pages": len(doc.pages),
                    },
                )

        # Phase 3a decision #9: chain chunk_file in a SEPARATE transaction so a
        # Procrastinate-defer failure (e.g., its task table is misconfigured)
        # doesn't roll back the successful parse. If defer fails, file stays
        # at 'parsed'; an out-of-band invocation can recover it.
        try:
            await procrastinate_app.configure_task(
                name="chunk_file"
            ).defer_async(file_id=file_id)
        except Exception:  # noqa: BLE001 — best-effort chain
            # Don't fail the parse over a chain defer error; log only.
            traceback.print_exc()


async def _mark_failed(
    db_url: str,
    file_id: str,
    workspace_id: str,
    *,
    error_class: str,
    message: str,
    from_state: str = "parsing",
    event: str = "parse_failed",
    traceback_head: str | None = None,
) -> None:
    payload: dict[str, str] = {"error_class": error_class, "message": message}
    if traceback_head:
        payload["traceback_head"] = traceback_head

    async with open_connection(db_url) as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)",
                (workspace_id,),
            )
            # Write the audit row directly; update files.lifecycle_state too.
            await conn.execute(
                "UPDATE files SET lifecycle_state = 'failed', updated_at = now() "
                "WHERE id = %s",
                (file_id,),
            )
            await record_lifecycle_event(
                conn,
                file_id=file_id,
                workspace_id=workspace_id,
                from_state=from_state,
                to_state="failed",
                event=event,
                payload=payload,
            )


# ---------------------------------------------------------------------------
# Phase 3a — chunk_file_impl
# ---------------------------------------------------------------------------


async def chunk_file_impl(file_id: str) -> None:
    """Chunk a 'parsed' file's raw_pages → write to chunks → advance
    lifecycle to 'chunked'.

    Per build_tracker §5.7:
    - decision #8: lifecycle widens with `chunked`.
    - decision #9: this task is chained from `parse_file_impl()`'s success
      path via Procrastinate defer.
    - decision #10: idempotent — return immediately if already chunked.
    - decision #11: empty raw_pages → ChunkingError → `parsed→failed`.
    """
    from kb.config import get_settings
    settings = get_settings()
    db_url = settings.database_url  # superuser; worker bypasses RLS to read

    # Phase 1: read file row + check idempotency.
    async with open_connection(db_url) as conn:
        async with conn.transaction():
            cur = await conn.execute(
                "SELECT workspace_id, lifecycle_state FROM files WHERE id = %s",
                (file_id,),
            )
            row = await cur.fetchone()
            if row is None:
                raise FileNotFoundError(file_id)
            workspace_id, lifecycle_state = row

            # Idempotency: already advanced past 'parsed' → no-op.
            if lifecycle_state in ("chunked", "failed", "deleted"):
                return
            if lifecycle_state != "parsed":
                # Not yet ready to chunk (still queued/parsing).
                return

            # Set workspace context for downstream queries.
            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)",
                (str(workspace_id),),
            )

            # Read raw_pages for this file.
            page_rows = await read_pages_for_chunking(conn, file_id=file_id)

    if not page_rows:
        await _mark_failed(
            db_url, file_id, str(workspace_id),
            error_class="ChunkingError",
            message=f"empty raw_pages for file={file_id}",
            from_state="parsed",
            event="chunking_failed",
        )
        return

    # Phase 2: run pure-function chunker (no DB).
    pages = [Page(page_number=pn, text=text, layout_json={}) for pn, text in page_rows]
    try:
        budget = settings.chunk_tokens
        overlap = settings.chunk_overlap_tokens
        chunks: list[Chunk] = chunk_pages(
            pages, budget_tokens=budget, overlap_tokens=overlap,
        )
    except ChunkingError as exc:
        await _mark_failed(
            db_url, file_id, str(workspace_id),
            error_class="ChunkingError",
            message=str(exc),
            from_state="parsed",
            event="chunking_failed",
        )
        return
    except Exception as exc:
        await _mark_failed(
            db_url, file_id, str(workspace_id),
            error_class=type(exc).__name__,
            message=str(exc),
            from_state="parsed",
            event="chunking_failed",
            traceback_head=traceback.format_exc()[:2000],
        )
        return

    # Phase 3: write chunks + lifecycle event.
    async with open_connection(db_url) as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)",
                (str(workspace_id),),
            )
            for chunk in chunks:
                await insert_chunk(
                    conn,
                    file_id=file_id,
                    workspace_id=str(workspace_id),
                    chunk_index=chunk.chunk_index,
                    text=chunk.text,
                    source_page_numbers=chunk.source_page_numbers,
                    token_count=chunk.token_count,
                    content_sha=chunk.content_sha,
                )
            total_tokens = sum(c.token_count for c in chunks)
            await transition_lifecycle(
                conn,
                workspace_id=str(workspace_id),
                file_id=file_id,
                to_state="chunked",
                event="chunking_done",
                payload={
                    "chunk_count": len(chunks),
                    "total_tokens": total_tokens,
                },
            )


# ---------------------------------------------------------------------------
# Procrastinate task registration
# ---------------------------------------------------------------------------


@procrastinate_app.task(name="parse_file", queue="kb", pass_context=False)
async def parse_file(file_id: str) -> None:
    """Wire-level Procrastinate task. Delegates to the testable impl."""
    await parse_file_impl(file_id)


@procrastinate_app.task(name="chunk_file", queue="kb", pass_context=False)
async def chunk_file(file_id: str) -> None:
    """Wire-level Procrastinate task. Delegates to the testable impl."""
    await chunk_file_impl(file_id)
