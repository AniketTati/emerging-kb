"""Procrastinate worker tasks — Phase 2a.

`parse_file(file_id)` is the wire-level Procrastinate task. The actual work
lives in `parse_file_impl(file_id)` — a regular async function that tests
can call directly without spinning up the queue.

Per build_tracker §5.5 decision #6: 30-min lease. Per decision #7: worker
sets `app.workspace_id` from the file row before any subsequent query.
Per decision #15: parser exceptions write a `parsing→failed` lifecycle event
and leave `files.lifecycle_state='failed'`.
"""

from __future__ import annotations

import hashlib
import traceback

from kb.db.pool import open_connection
from kb.domain.files import (
    FileNotFoundError,
    record_lifecycle_event,
    transition_lifecycle,
)
from kb.domain.raw_pages import insert_raw_page
from kb.parsers import (
    NoParserForMime,
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


async def _mark_failed(
    db_url: str,
    file_id: str,
    workspace_id: str,
    *,
    error_class: str,
    message: str,
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
            # Write the audit row directly (the file's current state may be
            # 'parsing' from the prior tx); update files.lifecycle_state too.
            await conn.execute(
                "UPDATE files SET lifecycle_state = 'failed', updated_at = now() "
                "WHERE id = %s",
                (file_id,),
            )
            await record_lifecycle_event(
                conn,
                file_id=file_id,
                workspace_id=workspace_id,
                from_state="parsing",
                to_state="failed",
                event="parse_failed",
                payload=payload,
            )


# ---------------------------------------------------------------------------
# Procrastinate task registration
# ---------------------------------------------------------------------------


@procrastinate_app.task(name="parse_file", queue="kb", pass_context=False)
async def parse_file(file_id: str) -> None:
    """Wire-level Procrastinate task. Delegates to the testable impl."""
    await parse_file_impl(file_id)
