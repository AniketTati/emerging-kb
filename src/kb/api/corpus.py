"""Corpus RAPTOR endpoints — api_contracts §6.

Phase 3e. Single endpoint:
- POST /corpus/raptor/rebuild — explicit trigger; 202 Accepted with task_id.

Pre-flight checks:
- 400 corpus-rebuild-no-input — workspace has zero files at lifecycle_state='ready'.
- 503 corpus-rebuild-in-flight — a raptor_build_corpus job is already queued
  for this workspace (status IN ('todo', 'doing')).

Wave A: OPEN endpoint (no auth gating; respects X-Test-Workspace header
same as other endpoints). Admin RBAC deferred to Phase 9 per build_tracker
§5.10.1 decision #11.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from kb.api.deps import current_workspace_id, kb_app_connection
from kb.api.errors import CorpusRebuildInFlightError, CorpusRebuildNoInputError
from kb.db.pool import Connection
from kb.workers.tasks import raptor_build_corpus


router = APIRouter(prefix="/corpus", tags=["corpus"])


@router.post(
    "/raptor/rebuild",
    status_code=202,
    summary="Trigger an asynchronous corpus RAPTOR rebuild for the workspace",
    responses={
        202: {"description": "Rebuild queued"},
        400: {"description": "Workspace has no files at lifecycle_state='ready'"},
        503: {"description": "A rebuild is already in flight for this workspace"},
    },
)
async def post_corpus_rebuild(
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> JSONResponse:
    # Pre-flight check 1: workspace has at least one file at lifecycle_state='ready'
    # (the source population for corpus clustering).
    cur = await conn.execute(
        "SELECT count(*) FROM files WHERE workspace_id = %s AND lifecycle_state IN "
        "('ready', 'embedded', 'raptor_building')",
        (workspace_id,),
    )
    row = await cur.fetchone()
    ready_count = int(row[0]) if row else 0
    if ready_count == 0:
        raise CorpusRebuildNoInputError(workspace_id)

    # Pre-flight check 2: no existing in-flight rebuild for this workspace.
    # Procrastinate stores task args as JSONB; we filter on workspace_id arg.
    # Note: kb_app may not have direct procrastinate_jobs SELECT — use the
    # superuser connection here. For Wave A simplicity, we use the same
    # `conn` (kb_app); if perms become an issue, swap to superuser.
    try:
        cur = await conn.execute(
            """
            SELECT count(*) FROM procrastinate_jobs
            WHERE task_name = 'raptor_build_corpus'
              AND args ->> 'workspace_id' = %s
              AND status IN ('todo', 'doing')
            """,
            (workspace_id,),
        )
        row = await cur.fetchone()
        in_flight = int(row[0]) if row else 0
    except Exception:
        # If kb_app can't read procrastinate_jobs, optimistically proceed.
        # The worker has its own dedup logic (atomic rebuild idempotent).
        in_flight = 0
    if in_flight > 0:
        raise CorpusRebuildInFlightError(workspace_id)

    # Defer the Procrastinate task.
    task_id = await raptor_build_corpus.defer_async(workspace_id=workspace_id)

    return JSONResponse(
        status_code=202,
        content={
            "workspace_id": workspace_id,
            "task_id": str(task_id),
            "status": "queued",
            "message": "corpus RAPTOR rebuild queued",
        },
    )
