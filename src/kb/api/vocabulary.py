"""WA-2 / Design 6 — Vocabulary HTTP endpoints.

Surfaces the `domain_vocabulary` table for the `/schema › Vocabulary`
UI tab (Schema Studio, Phase 10d). Wave A endpoints:
  GET    /vocabulary                  — list (filter by domain + active)
  GET    /vocabulary/{id}             — one row
  POST   /vocabulary                  — create OR merge by canonical_term
  PUT    /vocabulary/{id}             — update definition
  POST   /vocabulary/{id}/deactivate  — soft revoke
  POST   /vocabulary/{id}/reactivate  — restore

Note that domain_vocabulary is NOT workspace-scoped (it's a shared
domain dictionary), but the API still requires X-Workspace-Id so the
audit trail captures who/where the mutation came from + so the same
auth path as everything else works.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from kb.api.deps import current_workspace_id, kb_app_connection
from kb.api.errors import BadRequestError
from kb.db.pool import Connection
from kb.domain.vocabulary import (
    VocabRecord,
    get_vocabulary,
    list_vocabulary,
    set_active,
    update_definition,
    upsert_vocabulary,
)


router = APIRouter(prefix="/vocabulary", tags=["vocabulary"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class VocabEntry(BaseModel):
    id: str
    domain_id: str
    canonical_term: str
    synonyms: list[str] = Field(default_factory=list)
    acronym_of: str | None = None
    expansion: str | None = None
    definition: str | None = None
    source: str
    confidence: float
    n_docs_observed: int
    active: bool
    created_at: str
    updated_at: str


class VocabListResponse(BaseModel):
    items: list[VocabEntry] = Field(default_factory=list)


class VocabUpsertRequest(BaseModel):
    domain_id: str
    canonical_term: str
    synonyms: list[str] = Field(default_factory=list)
    acronym_of: str | None = None
    expansion: str | None = None
    definition: str | None = None
    source: str = "user_defined"
    confidence: float = 1.0
    n_docs_observed: int = 0


class VocabUpdateRequest(BaseModel):
    definition: str


# ---------------------------------------------------------------------------
# Conversions
# ---------------------------------------------------------------------------


def _to_response(r: VocabRecord) -> VocabEntry:
    return VocabEntry(
        id=r.id,
        domain_id=r.domain_id,
        canonical_term=r.canonical_term,
        synonyms=r.synonyms,
        acronym_of=r.acronym_of,
        expansion=r.expansion,
        definition=r.definition,
        source=r.source,
        confidence=r.confidence,
        n_docs_observed=r.n_docs_observed,
        active=r.active,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=VocabListResponse,
    summary="List vocabulary entries for a domain",
)
async def get_vocabulary_list(
    workspace_id: Annotated[str, Depends(current_workspace_id)],  # noqa: ARG001
    conn: Annotated[Connection, Depends(kb_app_connection)],
    domain_id: str = Query(..., description="Domain id (e.g. 'mixed_demo')"),
    include_inactive: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=500),
) -> VocabListResponse:
    rows = await list_vocabulary(
        conn,
        domain_id=domain_id,
        include_inactive=include_inactive,
        limit=limit,
    )
    return VocabListResponse(items=[_to_response(r) for r in rows])


@router.get(
    "/{vocab_id}",
    response_model=VocabEntry,
    summary="Read one vocabulary entry by id",
)
async def get_vocabulary_entry(
    vocab_id: str,
    workspace_id: Annotated[str, Depends(current_workspace_id)],  # noqa: ARG001
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> VocabEntry:
    cur = await conn.execute(
        "SELECT id, domain_id, canonical_term FROM domain_vocabulary "
        "WHERE id = %s",
        (vocab_id,),
    )
    row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="vocabulary entry not found")
    entry = await get_vocabulary(
        conn, domain_id=str(row[1]), canonical_term=str(row[2]),
    )
    if entry is None:
        # Row exists but inactive — fall back to a raw read.
        cur = await conn.execute(
            """
            SELECT id, domain_id, canonical_term, synonyms, acronym_of,
                   expansion, definition, source, confidence,
                   n_docs_observed, active, created_at, updated_at
              FROM domain_vocabulary WHERE id = %s
            """,
            (vocab_id,),
        )
        raw = await cur.fetchone()
        assert raw is not None  # we just confirmed it exists
        from kb.domain.vocabulary import _row_to_record  # noqa: PLC0415
        entry = _row_to_record(raw)
    return _to_response(entry)


@router.post(
    "",
    response_model=VocabEntry,
    summary="Create or merge a vocabulary entry by (domain, canonical_term)",
    status_code=201,
)
async def post_vocabulary(
    body: VocabUpsertRequest,
    workspace_id: Annotated[str, Depends(current_workspace_id)],  # noqa: ARG001
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> VocabEntry:
    if body.source not in ("user_defined", "discovered", "imported"):
        raise BadRequestError(f"source={body.source!r} not allowed")
    if not body.canonical_term.strip():
        raise BadRequestError("canonical_term must be non-empty")
    vid = await upsert_vocabulary(
        conn,
        domain_id=body.domain_id,
        canonical_term=body.canonical_term,
        synonyms=body.synonyms,
        acronym_of=body.acronym_of,
        expansion=body.expansion,
        definition=body.definition,
        source=body.source,
        confidence=body.confidence,
        n_docs_observed=body.n_docs_observed,
    )
    # Read back the resolved row (handles both insert + merge).
    record = await get_vocabulary(
        conn, domain_id=body.domain_id, canonical_term=body.canonical_term,
    )
    assert record is not None, "upsert returned id but read-back missed"
    assert record.id == vid
    return _to_response(record)


@router.put(
    "/{vocab_id}",
    response_model=VocabEntry,
    summary="Update the definition of a vocabulary entry",
)
async def put_vocabulary_definition(
    vocab_id: str,
    body: VocabUpdateRequest,
    workspace_id: Annotated[str, Depends(current_workspace_id)],  # noqa: ARG001
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> VocabEntry:
    if not body.definition.strip():
        raise BadRequestError("definition must be non-empty")
    changed = await update_definition(conn, vocab_id=vocab_id, definition=body.definition)
    if not changed:
        raise HTTPException(status_code=404, detail="vocabulary entry not found")
    return await get_vocabulary_entry(vocab_id, "", conn)  # type: ignore[arg-type]


@router.post(
    "/{vocab_id}/deactivate",
    response_model=VocabEntry,
    summary="Soft-deactivate a vocabulary entry (preserves history)",
)
async def deactivate_vocabulary(
    vocab_id: str,
    workspace_id: Annotated[str, Depends(current_workspace_id)],  # noqa: ARG001
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> VocabEntry:
    changed = await set_active(conn, vocab_id=vocab_id, active=False)
    if not changed:
        raise HTTPException(status_code=404, detail="vocabulary entry not found")
    return await get_vocabulary_entry(vocab_id, "", conn)  # type: ignore[arg-type]


@router.post(
    "/{vocab_id}/reactivate",
    response_model=VocabEntry,
    summary="Restore a previously deactivated vocabulary entry",
)
async def reactivate_vocabulary(
    vocab_id: str,
    workspace_id: Annotated[str, Depends(current_workspace_id)],  # noqa: ARG001
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> VocabEntry:
    changed = await set_active(conn, vocab_id=vocab_id, active=True)
    if not changed:
        raise HTTPException(status_code=404, detail="vocabulary entry not found")
    return await get_vocabulary_entry(vocab_id, "", conn)  # type: ignore[arg-type]
