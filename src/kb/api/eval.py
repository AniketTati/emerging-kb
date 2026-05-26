"""Wave-A close-out — `POST /eval/run` + read endpoints.

Backs the Playground Eval tab. Three endpoints:

  POST /eval/run               — defer a run; returns 202 + run_id
  GET  /eval/runs              — list past runs (paginated, newest first)
  GET  /eval/runs/{run_id}     — single run with the aggregate summary
  GET  /eval/runs/{run_id}/results — per-question payload (paginated)

Async by design: the 45-question run takes 5+ minutes wall time, well
past SSE keepalive ergonomics. POST inserts an `eval_runs` row with
status='queued' + defers a Procrastinate task; the worker drives the
row through running → succeeded|failed. Clients poll GET on a 3s
cadence to render progress (matches the dashboard refresh loop).

Pre-flight: a fresh POST returns 503 when an existing
`queued|running` row already exists for the workspace. Avoids fork-on-
burst spend when a user double-clicks Run.

Idempotency: the optional Idempotency-Key header maps to
`eval_runs.idempotency_key` (UNIQUE per workspace). A retry with the
same key returns the existing row (200) instead of starting a new run.

No new exception classes — errors flow via the existing problem-json
handlers (400/404/503 from BadRequestError or per-route raise).
"""

from __future__ import annotations

import json as _json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from kb.api.deps import current_workspace_id, kb_app_connection
from kb.api.errors import BadRequestError
from kb.api.idempotency import idempotency_key_optional
from kb.db.pool import Connection


router = APIRouter(prefix="/eval", tags=["eval"])


# ---------------------------------------------------------------------------
# Request + response models
# ---------------------------------------------------------------------------


class EvalRunRequest(BaseModel):
    """POST /eval/run body. All fields optional — defaults match the CLI."""

    ragas: bool = False
    hhem: bool = False
    concurrency: int = Field(default=2, ge=1, le=16)
    # Optional path override (server-side; defaults to the bundled
    # golden_questions.yaml). Passing this from the UI is unusual but
    # useful for ad-hoc question sets.
    questions_path: str | None = None


class EvalRunResponse(BaseModel):
    """POST /eval/run response."""

    id: str
    workspace_id: str
    status: str
    started_at: str
    enable_ragas: bool
    enable_hhem: bool
    concurrency: int


class EvalRunDetail(BaseModel):
    """GET /eval/runs/{id} — adds summary + error + finished_at."""

    id: str
    workspace_id: str
    status: str
    enable_ragas: bool
    enable_hhem: bool
    concurrency: int
    questions_path: str | None
    started_at: str
    finished_at: str | None
    summary: dict[str, Any] | None
    error: str | None


class EvalRunListResponse(BaseModel):
    items: list[EvalRunDetail] = Field(default_factory=list)


class EvalRunResult(BaseModel):
    question_id: str
    payload: dict[str, Any]
    created_at: str


class EvalRunResultsResponse(BaseModel):
    items: list[EvalRunResult] = Field(default_factory=list)
    total: int


# ---------------------------------------------------------------------------
# POST /eval/run
# ---------------------------------------------------------------------------


@router.post(
    "/run",
    status_code=202,
    response_model=EvalRunResponse,
    summary=(
        "Defer a 45-question eval run against the live /chat pipeline. "
        "Returns 202 + run_id; poll GET /eval/runs/{id} for status."
    ),
    responses={
        202: {"description": "Run accepted + queued"},
        200: {"description": "Idempotent replay — returns the existing run"},
        503: {"description": "An eval run is already in flight for this workspace"},
    },
)
async def post_eval_run(
    body: EvalRunRequest,
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    idem_key: Annotated[str | None, Depends(idempotency_key_optional)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> JSONResponse:
    # Idempotency replay — return the row the previous call inserted.
    if idem_key:
        cur = await conn.execute(
            "SELECT id::text, status, started_at::text, "
            "       enable_ragas, enable_hhem, concurrency "
            "  FROM eval_runs "
            " WHERE workspace_id = %s AND idempotency_key = %s",
            (workspace_id, idem_key),
        )
        row = await cur.fetchone()
        if row:
            return JSONResponse(
                status_code=200,
                content={
                    "id": row[0], "workspace_id": workspace_id,
                    "status": row[1], "started_at": row[2],
                    "enable_ragas": bool(row[3]), "enable_hhem": bool(row[4]),
                    "concurrency": int(row[5]),
                },
                headers={"X-Idempotent-Replay": "true"},
            )

    # Pre-flight: refuse if a run is already queued or running for this
    # workspace. Avoids a double-click triggering two concurrent runs.
    cur = await conn.execute(
        "SELECT id::text FROM eval_runs "
        " WHERE workspace_id = %s AND status IN ('queued', 'running') "
        " LIMIT 1",
        (workspace_id,),
    )
    in_flight = await cur.fetchone()
    if in_flight:
        raise HTTPException(
            status_code=503,
            detail=(
                f"eval-run-in-flight: run {in_flight[0]} is still "
                f"{'queued or running'!r}; wait for it to finish or "
                f"delete it first"
            ),
        )

    # Insert the queued row + capture id so the worker has a target.
    cur = await conn.execute(
        "INSERT INTO eval_runs "
        "  (workspace_id, status, enable_ragas, enable_hhem, "
        "   concurrency, questions_path, idempotency_key) "
        "VALUES (%s, 'queued', %s, %s, %s, %s, %s) "
        "RETURNING id::text, started_at::text",
        (workspace_id, body.ragas, body.hhem, body.concurrency,
         body.questions_path, idem_key),
    )
    row = await cur.fetchone()
    run_id, started_at = str(row[0]), str(row[1])

    # Defer the Procrastinate task. The worker is responsible for
    # transitioning status → running → succeeded|failed.
    try:
        from kb.workers.tasks import run_eval_suite
        await run_eval_suite.defer_async(
            run_id=run_id,
            workspace_id=workspace_id,
            ragas=body.ragas,
            hhem=body.hhem,
            concurrency=body.concurrency,
            questions_path=body.questions_path,
        )
    except Exception as exc:  # noqa: BLE001
        # Procrastinate misconfigured / network blip — flip the row to
        # failed so the UI shows a clean "couldn't enqueue" instead of
        # a stuck queued row.
        await conn.execute(
            "UPDATE eval_runs SET status='failed', error=%s, "
            "finished_at=NOW() WHERE id=%s",
            (f"failed to enqueue: {exc}", run_id),
        )
        raise HTTPException(
            status_code=503,
            detail=f"failed to enqueue eval run: {exc}",
        ) from exc

    return JSONResponse(
        status_code=202,
        content={
            "id": run_id, "workspace_id": workspace_id,
            "status": "queued", "started_at": started_at,
            "enable_ragas": body.ragas, "enable_hhem": body.hhem,
            "concurrency": body.concurrency,
        },
    )


# ---------------------------------------------------------------------------
# GET /eval/runs — list
# ---------------------------------------------------------------------------


@router.get(
    "/runs",
    response_model=EvalRunListResponse,
    summary="List past eval runs for this workspace, newest first",
)
async def get_eval_runs(
    workspace_id: Annotated[str, Depends(current_workspace_id)],  # noqa: ARG001
    conn: Annotated[Connection, Depends(kb_app_connection)],
    limit: int = Query(default=50, ge=1, le=200),
) -> EvalRunListResponse:
    cur = await conn.execute(
        """
        SELECT id::text, workspace_id::text, status,
               enable_ragas, enable_hhem, concurrency,
               questions_path, started_at::text, finished_at::text,
               summary, error
          FROM eval_runs
         ORDER BY started_at DESC
         LIMIT %s
        """,
        (limit,),
    )
    rows = await cur.fetchall()
    return EvalRunListResponse(items=[_row_to_detail(r) for r in rows])


# ---------------------------------------------------------------------------
# GET /eval/runs/{id}
# ---------------------------------------------------------------------------


@router.get(
    "/runs/{run_id}",
    response_model=EvalRunDetail,
    summary="Single eval run with the aggregate summary (when complete)",
    responses={404: {"description": "Run not found in this workspace"}},
)
async def get_eval_run(
    run_id: str,
    workspace_id: Annotated[str, Depends(current_workspace_id)],  # noqa: ARG001
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> EvalRunDetail:
    cur = await conn.execute(
        """
        SELECT id::text, workspace_id::text, status,
               enable_ragas, enable_hhem, concurrency,
               questions_path, started_at::text, finished_at::text,
               summary, error
          FROM eval_runs
         WHERE id = %s
        """,
        (run_id,),
    )
    row = await cur.fetchone()
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"eval run {run_id} not found",
        )
    return _row_to_detail(row)


# ---------------------------------------------------------------------------
# GET /eval/runs/{id}/results
# ---------------------------------------------------------------------------


@router.get(
    "/runs/{run_id}/results",
    response_model=EvalRunResultsResponse,
    summary="Per-question payloads for an eval run (paginated)",
)
async def get_eval_run_results(
    run_id: str,
    workspace_id: Annotated[str, Depends(current_workspace_id)],  # noqa: ARG001
    conn: Annotated[Connection, Depends(kb_app_connection)],
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> EvalRunResultsResponse:
    cur = await conn.execute(
        "SELECT count(*)::int FROM eval_run_results WHERE run_id = %s",
        (run_id,),
    )
    total_row = await cur.fetchone()
    total = int(total_row[0]) if total_row else 0

    cur = await conn.execute(
        """
        SELECT question_id, payload, created_at::text
          FROM eval_run_results
         WHERE run_id = %s
         ORDER BY created_at ASC, question_id ASC
         LIMIT %s OFFSET %s
        """,
        (run_id, limit, offset),
    )
    rows = await cur.fetchall()
    return EvalRunResultsResponse(
        total=total,
        items=[
            EvalRunResult(
                question_id=str(r[0]),
                payload=_loads_jsonb(r[1]),
                created_at=str(r[2]),
            )
            for r in rows
        ],
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_detail(row: tuple) -> EvalRunDetail:
    """Marshal an eval_runs row (11-tuple) into the response model."""
    return EvalRunDetail(
        id=str(row[0]),
        workspace_id=str(row[1]),
        status=str(row[2]),
        enable_ragas=bool(row[3]),
        enable_hhem=bool(row[4]),
        concurrency=int(row[5]),
        questions_path=row[6],
        started_at=str(row[7]),
        finished_at=str(row[8]) if row[8] else None,
        summary=_loads_jsonb(row[9]),
        error=row[10],
    )


def _loads_jsonb(v: Any) -> Any:
    """psycopg returns jsonb columns as already-decoded Python objects;
    safety-net handle stringified case too."""
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        return v
    if isinstance(v, str):
        try:
            return _json.loads(v)
        except Exception:
            return v
    return v
