"""Phase 8f — Query HTTP surface.

Two endpoints per api_contracts §7:
- POST /search — read-only retrieval inspector; returns reranked top-10
  + CRAG score; NO generation.
- POST /chat — full pipeline (search + CRAG gate + generate); honors
  Idempotency-Key for replay.

Both write one row to `query_log` for audit (Phase 9 reads via /audit).
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from kb.api.deps import current_workspace_id, kb_app_connection
from kb.api.errors import InvalidQueryError, QueryPipelineError
from kb.api.idempotency import (
    cache_response,
    get_cached,
    idempotency_key_optional,
)
from kb.db.pool import Connection
from kb.query.orchestrator import ChatResult, Orchestrator, SearchResult


_LOG = logging.getLogger(__name__)
_MAX_QUERY_LEN = 4000

router = APIRouter(tags=["query"])


# ---------------------------------------------------------------------------
# Request shape
# ---------------------------------------------------------------------------


class QueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=_MAX_QUERY_LEN)
    mode: str = "H"
    # B6a / WA-12 — optional conversation memory binding. When set, the
    # orchestrator loads the session's ChatContext, runs the anaphora
    # resolver, and appends a chat_turns row.
    session_id: str | None = None


# ---------------------------------------------------------------------------
# Orchestrator factory (cached per-process for now; settings injection later)
# ---------------------------------------------------------------------------


_orchestrator_singleton: Orchestrator | None = None


def get_orchestrator() -> Orchestrator:
    """Lazy per-process orchestrator. The underlying factories read env at
    construction time; rebuild the process to pick up env changes."""
    global _orchestrator_singleton
    if _orchestrator_singleton is None:
        _orchestrator_singleton = Orchestrator.make_default()
    return _orchestrator_singleton


def reset_orchestrator() -> None:
    """Test hook — drops the cached orchestrator so the next request rebuilds."""
    global _orchestrator_singleton
    _orchestrator_singleton = None


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


# B4a + B4b — all 12 modes pass the API validator. Q-mode pipeline is
# wired in kb.query.mode_router via kb.q_planner (Design 1 10 layers).
_ALLOWED_MODES: set[str] = {
    "E", "F", "S", "H", "T", "M", "G", "D", "C", "A", "Q", "K",
}


def _validate_request(body: QueryRequest) -> None:
    if body.mode not in _ALLOWED_MODES:
        raise InvalidQueryError(
            f"mode={body.mode!r} not supported; expected one of "
            f"{sorted(_ALLOWED_MODES)}"
        )
    # min/max length is enforced by Pydantic; this is belt-and-braces for
    # whitespace-only queries that pass the min_length check.
    if not body.query.strip():
        raise InvalidQueryError("query may not be empty or whitespace-only")


async def _write_query_log(
    conn: Connection,
    *,
    workspace_id: str,
    query_id: str,
    endpoint: str,
    body: QueryRequest,
    search_result: SearchResult | None,
    chat_result: ChatResult | None,
    idempotency_key: str | None,
) -> None:
    """Insert one audit row. Best-effort — pipeline result is the user-facing
    success criterion, not the audit write."""
    source = chat_result or search_result
    if source is None:
        return

    refused = False
    refusal_reason: str | None = None
    answer: str | None = None
    citations_payload: Any = None
    model_id: str | None = None
    # B3 — faithfulness gate + modality denormalization
    faithfulness_verdict: str | None = None
    faithfulness_score: float | None = None
    faithfulness_regenerations: int = 0
    citation_modalities: list[str] | None = None
    # B4a — intent + planner observability
    intent_label: str | None = None
    intent_conf: float | None = None
    plan_payload: Any = None
    # The mode actually executed (may differ from the request's mode
    # when the planner overrode 'H' with something more precise).
    mode_used: str = body.mode

    if chat_result is not None:
        gen = chat_result.generation
        refused = gen.refused
        refusal_reason = gen.refusal_reason
        answer = gen.answer or None
        citations_payload = [c.model_dump(mode="json") for c in gen.citations]
        model_id = gen.model_id or None
        faithfulness_verdict = chat_result.faithfulness_verdict
        faithfulness_score = chat_result.faithfulness_score
        faithfulness_regenerations = chat_result.faithfulness_regenerations
        citation_modalities = chat_result.citation_modalities or None
        intent_label = chat_result.intent
        intent_conf = chat_result.intent_confidence
        plan_payload = chat_result.plan
        if chat_result.mode:
            mode_used = chat_result.mode
    elif search_result is not None:
        intent_label = search_result.intent
        intent_conf = search_result.intent_confidence
        plan_payload = search_result.plan
        if search_result.mode:
            mode_used = search_result.mode

    hit_ids_payload = [
        {"id": h.id, "kind": h.kind, "score": h.score}
        for h in source.hits
    ]
    rewrites_payload = source.rewrites

    try:
        await conn.execute(
            """
            INSERT INTO query_log (
                id, workspace_id, query, mode, endpoint,
                rewrites, hit_ids, crag_score,
                refused, refusal_reason, answer, citations, model_id,
                latency_ms, idempotency_key,
                faithfulness_score, faithfulness_verdict,
                faithfulness_regenerations, citation_modalities,
                intent, intent_confidence, plan
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s::jsonb, %s::jsonb, %s,
                %s, %s, %s, %s::jsonb, %s,
                %s, %s,
                %s, %s,
                %s, %s,
                %s, %s, %s::jsonb
            )
            """,
            (
                query_id,
                workspace_id,
                body.query,
                mode_used,
                endpoint,
                json.dumps(rewrites_payload),
                json.dumps(hit_ids_payload),
                source.crag_score,
                refused,
                refusal_reason,
                answer,
                json.dumps(citations_payload) if citations_payload is not None else None,
                model_id,
                source.latency_ms,
                idempotency_key,
                faithfulness_score,
                faithfulness_verdict,
                faithfulness_regenerations,
                citation_modalities,
                intent_label,
                intent_conf,
                json.dumps(plan_payload) if plan_payload is not None else None,
            ),
        )
    except Exception as exc:  # noqa: BLE001 — audit is best-effort
        _LOG.exception("query_log insert failed: %s", exc)


# ---------------------------------------------------------------------------
# POST /search — retrieval inspector
# ---------------------------------------------------------------------------


@router.post(
    "/search",
    summary="Run the full retrieval pipeline; return reranked top-10 hits + CRAG score (no generation)",
    response_model=SearchResult,
    responses={
        200: {"description": "Reranked hits + CRAG score"},
        400: {"description": "Empty / oversize query or unsupported mode"},
        500: {"description": "Internal pipeline error"},
    },
)
async def post_search(
    body: QueryRequest,
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> SearchResult:
    _validate_request(body)
    orchestrator = get_orchestrator()
    try:
        result = await orchestrator.search(
            body.query, workspace_id=workspace_id, conn=conn,
            requested_mode=body.mode,
        )
    except Exception as exc:  # noqa: BLE001
        _LOG.exception("search pipeline failed: %s", exc)
        raise QueryPipelineError(str(exc)) from exc

    await _write_query_log(
        conn,
        workspace_id=workspace_id,
        query_id=result.query_id,
        endpoint="search",
        body=body,
        search_result=result,
        chat_result=None,
        idempotency_key=None,
    )
    return result


# ---------------------------------------------------------------------------
# POST /chat — full pipeline
# ---------------------------------------------------------------------------


@router.post(
    "/chat",
    summary="Run the full pipeline (search + CRAG gate + Astute generation); cite-or-refuse envelope",
    response_model=ChatResult,
    responses={
        200: {"description": "ChatResult — answer + citations, or refusal envelope"},
        400: {"description": "Empty / oversize query or unsupported mode"},
        500: {"description": "Internal pipeline error"},
    },
)
async def post_chat(
    body: QueryRequest,
    workspace_id: Annotated[str, Depends(current_workspace_id)],
    conn: Annotated[Connection, Depends(kb_app_connection)],
    idempotency_key: Annotated[str | None, Depends(idempotency_key_optional)],
) -> Response:
    _validate_request(body)

    # Idempotency-Key replay (decision #13) — return cached envelope verbatim.
    if idempotency_key:
        cached = await get_cached(conn, workspace_id, idempotency_key)
        if cached is not None:
            cached_body, cached_status = cached
            if cached_body is not None and cached_status == 200:
                return JSONResponse(
                    content=cached_body, status_code=cached_status
                )

    orchestrator = get_orchestrator()
    try:
        result = await orchestrator.chat(
            body.query, workspace_id=workspace_id, conn=conn,
            requested_mode=body.mode,
            session_id=body.session_id,
        )
    except Exception as exc:  # noqa: BLE001
        _LOG.exception("chat pipeline failed: %s", exc)
        raise QueryPipelineError(str(exc)) from exc

    await _write_query_log(
        conn,
        workspace_id=workspace_id,
        query_id=result.query_id,
        endpoint="chat",
        body=body,
        search_result=None,
        chat_result=result,
        idempotency_key=idempotency_key,
    )

    # Cache the response for replay.
    response_body = result.model_dump(mode="json")
    await cache_response(
        conn,
        workspace_id,
        idempotency_key,
        body=response_body,
        status_code=200,
    )

    return JSONResponse(content=response_body, status_code=200)
