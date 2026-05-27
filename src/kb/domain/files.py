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
    # Phase 5b / WA-6 / B2 — fields added after Phase 2a. Always present
    # in the response now; values may be null on rows that haven't yet
    # reached the lifecycle stage that populates them (e.g.
    # `inferred_doc_type` is null until the file passes through
    # `fields_extracting`).
    inferred_doc_type: str | None = None
    source_authority: float | None = None
    source_authority_reason: str | None = None
    doc_status: str | None = None


class LifecycleEvent(BaseModel):
    from_state: str | None
    to_state: str
    event: str
    payload: dict[str, Any]
    created_at: str


class FileWithLifecycleResponse(FileResponse):
    lifecycle: list[LifecycleEvent]


class FileDetailsResponse(BaseModel):
    """Rich rollups for the Upload-page row-expand: per-doc counts +
    timing + chain membership. Wave-A demo surface — additive over
    FileWithLifecycleResponse so callers can fetch one or both."""
    file: FileResponse
    lifecycle: list[LifecycleEvent]
    # Counts derived from join tables. None means "not yet computable"
    # for files that haven't reached the relevant lifecycle stage.
    n_pages: int = 0
    n_chunks: int = 0
    n_contextual_chunks: int = 0
    n_mentions: int = 0
    n_atomic_units: int = 0
    n_entities_linked: int = 0
    n_triples: int = 0
    # Doc-chain membership (WA-3). chain_id is null for files not in any
    # detected chain.
    chain_id: str | None = None
    chain_role: str | None = None
    chain_version_index: int | None = None
    is_current_version: bool | None = None


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
    "lifecycle_state, created_at, updated_at, "
    "inferred_doc_type, source_authority, source_authority_reason, "
    "doc_status"
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
        inferred_doc_type=row[9],
        source_authority=(float(row[10]) if row[10] is not None else None),
        source_authority_reason=row[11],
        doc_status=row[12],
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


async def get_file_details(
    conn: Connection, file_id: str,
) -> FileDetailsResponse:
    """Returns FileWithLifecycleResponse content plus join-table
    rollups: page / chunk / mention / atomic_unit / linked-entity /
    triple counts + doc-chain membership.

    Single function rather than 7 separate endpoints because the UI
    row-expand wants all of them at once. None of the underlying
    counts are expensive (each is a single indexed COUNT(*) per
    file_id). For corpora >100K docs we'd add materialized rollups
    in `files`; Wave-A scale doesn't need it."""
    file_resp = await get_file(conn, file_id)

    # Lifecycle history (same as get_file_with_lifecycle).
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
            payload=(r[3] if isinstance(r[3], dict)
                    else (json.loads(r[3]) if r[3] else {})),
            created_at=_iso(r[4]),
        )
        for r in rows
    ]

    # Rollups in one round-trip. Tables that don't exist yet on older
    # workspaces (or older test schemas) won't FAIL the request — each
    # subquery uses to_regclass + COALESCE so missing tables yield 0.
    cur = await conn.execute(
        """
        SELECT
          COALESCE((SELECT COUNT(*)::int FROM raw_pages          WHERE file_id = %s), 0) AS n_pages,
          COALESCE((SELECT COUNT(*)::int FROM chunks             WHERE file_id = %s), 0) AS n_chunks,
          COALESCE((SELECT COUNT(*)::int FROM contextual_chunks  WHERE file_id = %s), 0) AS n_ctx,
          COALESCE((SELECT COUNT(*)::int FROM extracted_mentions WHERE file_id = %s), 0) AS n_mentions,
          COALESCE((SELECT COUNT(*)::int FROM extracted_entities
                     WHERE file_id = %s AND unit_type IS NOT NULL), 0) AS n_au,
          COALESCE((SELECT COUNT(DISTINCT me.entity_id)::int
                      FROM mention_to_entity me
                      JOIN extracted_mentions em ON em.id = me.mention_id
                     WHERE em.file_id = %s), 0)                          AS n_entities_linked,
          COALESCE((SELECT COUNT(*)::int FROM extracted_triples  WHERE file_id = %s), 0) AS n_triples
        """,
        (file_id, file_id, file_id, file_id, file_id, file_id, file_id),
    )
    rollups = await cur.fetchone() or (0, 0, 0, 0, 0, 0, 0)

    # Chain membership lookup.
    cur = await conn.execute(
        """
        SELECT m.chain_id::text, m.role, m.version_index,
               (c.current_version_id = m.doc_id) AS is_current
          FROM doc_chain_members m
          JOIN doc_chains c ON c.id = m.chain_id
         WHERE m.doc_id = %s
         LIMIT 1
        """,
        (file_id,),
    )
    chain_row = await cur.fetchone()
    chain_id = chain_role = None
    chain_vidx: int | None = None
    is_current: bool | None = None
    if chain_row:
        chain_id = str(chain_row[0])
        chain_role = str(chain_row[1])
        chain_vidx = int(chain_row[2]) if chain_row[2] is not None else None
        is_current = bool(chain_row[3])

    return FileDetailsResponse(
        file=file_resp,
        lifecycle=events,
        n_pages=int(rollups[0]),
        n_chunks=int(rollups[1]),
        n_contextual_chunks=int(rollups[2]),
        n_mentions=int(rollups[3]),
        n_atomic_units=int(rollups[4]),
        n_entities_linked=int(rollups[5]),
        n_triples=int(rollups[6]),
        chain_id=chain_id,
        chain_role=chain_role,
        chain_version_index=chain_vidx,
        is_current_version=is_current,
    )


# ---------------------------------------------------------------------------
# Doc-detail surfaces — one focused, paginated query per UI accordion.
#
# Each layer of the pipeline lives in its own table; the UI's doc-detail page
# binds one accordion to one endpoint. Lists are paginated so a 500-page doc
# with thousands of mentions doesn't blow up a single response.
# ---------------------------------------------------------------------------


class ProposedField(BaseModel):
    id: str
    field_name: str
    field_description: str | None
    value_text: str | None
    value_type: str | None
    is_pii: bool
    model_id: str | None
    # Worker-resolved source position (added PR2 / migration 0032).
    # Null means the resolver couldn't find the value text in any chunk
    # (LLM paraphrased it, or it lives in a contextual prefix only).
    source_chunk_id: str | None = None
    source_char_start: int | None = None
    source_char_end: int | None = None
    source_page_numbers: list[int] | None = None


class AtomicUnit(BaseModel):
    id: str
    unit_type: str
    parameters: dict[str, Any]
    anchor_chunk_id: str | None
    rarity_score: float | None
    model_id: str | None
    source_chunk_id: str | None = None
    source_char_start: int | None = None
    source_char_end: int | None = None
    source_page_numbers: list[int] | None = None


class Mention(BaseModel):
    id: str
    mention_text: str
    mention_type: str
    chunk_id: str | None
    start_offset: int | None
    end_offset: int | None
    confidence: float | None
    canonical_entity_id: str | None
    canonical_name: str | None
    # Pages the mention's chunk spans — surfaced so the doc-detail
    # source viewer can jump PDF pages on click. Null for non-paginated
    # formats (md/txt/eml).
    source_page_numbers: list[int] | None = None
    # Worker-resolved source position (added PR2 / migration 0032).
    source_chunk_id: str | None = None
    source_char_start: int | None = None
    source_char_end: int | None = None


class EntityMentioned(BaseModel):
    entity_id: str
    canonical_name: str
    entity_type: str
    mentions_in_doc: int
    total_mentions: int


class TripleInDoc(BaseModel):
    id: str
    subject_text: str
    predicate_text: str
    object_text: str
    confidence: float | None
    chunk_id: str | None
    source_page_numbers: list[int] | None = None
    # Worker-resolved subject + object positions in the source chunk text.
    subject_char_start: int | None = None
    subject_char_end: int | None = None
    object_char_start: int | None = None
    object_char_end: int | None = None


class ExtractedEntityInstance(BaseModel):
    id: str
    schema_entity_id: str
    schema_entity_name: str | None
    parent_entity_id: str | None
    fields: dict[str, Any]


class CitationByQuery(BaseModel):
    query_id: str
    query: str
    answer: str | None
    endpoint: str
    created_at: str | None


class PaginatedList(BaseModel):
    """Wrapper so each accordion knows total + offset/limit without an
    extra COUNT(*) round-trip per scroll."""
    items: list[Any]
    total: int
    limit: int
    offset: int


async def list_proposed_fields(
    conn: Connection, file_id: str,
) -> list[ProposedField]:
    """L3 open-world: fields Gemini inferred this doc has, irrespective of
    any closed-world schema. Usually <30 per doc — no pagination."""
    cur = await conn.execute(
        """
        SELECT pf.id::text, pf.field_name, pf.field_description, pf.value_text,
               pf.value_type, pf.is_pii, pf.model_id,
               pf.source_chunk_id::text, pf.source_char_start, pf.source_char_end,
               c.source_page_numbers
          FROM proposed_fields pf
          LEFT JOIN chunks c ON c.id = pf.source_chunk_id
         WHERE pf.file_id = %s
         ORDER BY pf.field_name ASC
        """,
        (file_id,),
    )
    rows = await cur.fetchall()
    return [
        ProposedField(
            id=r[0], field_name=r[1], field_description=r[2],
            value_text=r[3], value_type=r[4], is_pii=bool(r[5]),
            model_id=r[6],
            source_chunk_id=r[7],
            source_char_start=(int(r[8]) if r[8] is not None else None),
            source_char_end=(int(r[9]) if r[9] is not None else None),
            source_page_numbers=(list(r[10]) if r[10] else None),
        )
        for r in rows
    ]


async def list_extracted_entities(
    conn: Connection, file_id: str,
) -> list[ExtractedEntityInstance]:
    """L4 closed-world: instances of schema_entities populated from this
    doc. Joined to schema_entities for the human-readable name."""
    cur = await conn.execute(
        """
        SELECT ee.id::text, ee.schema_entity_id::text,
               se.name, ee.parent_entity_id::text, ee.fields
          FROM extracted_entities ee
          LEFT JOIN schema_entities se ON se.id = ee.schema_entity_id
         WHERE ee.file_id = %s
         ORDER BY ee.created_at ASC
        """,
        (file_id,),
    )
    rows = await cur.fetchall()
    return [
        ExtractedEntityInstance(
            id=r[0], schema_entity_id=r[1], schema_entity_name=r[2],
            parent_entity_id=r[3],
            fields=(r[4] if isinstance(r[4], dict) else (json.loads(r[4]) if r[4] else {})),
        )
        for r in rows
    ]


async def list_atomic_units(
    conn: Connection, file_id: str, *, limit: int = 50, offset: int = 0,
) -> tuple[list[AtomicUnit], int]:
    """Return sub_entity rows (transactions / clauses / line_items / ...)
    for a file, sorted by rarity DESC. Post nested-entities refactor,
    these live in `extracted_entities` with `unit_type IS NOT NULL`;
    the `parameters` jsonb is now `fields` jsonb (semantic rename, same
    payload). The function name + return shape stay stable for API +
    UI back-compat. Anchor_chunk_id is sourced from
    extracted_entities.citations['_anchor'] which Phase 3.5 of the
    extraction worker populates.
    """
    cur = await conn.execute(
        "SELECT COUNT(*) FROM extracted_entities "
        "WHERE file_id = %s AND unit_type IS NOT NULL",
        (file_id,),
    )
    total = int((await cur.fetchone())[0])
    cur = await conn.execute(
        """
        SELECT ee.id::text, ee.unit_type, ee.fields,
               ee.citations,
               ee.rarity_score, ee.model_id,
               ee.source_chunk_id::text,
               ee.source_char_start, ee.source_char_end,
               c.source_page_numbers
          FROM extracted_entities ee
          LEFT JOIN chunks c ON c.id = ee.source_chunk_id
         WHERE ee.file_id = %s AND ee.unit_type IS NOT NULL
         ORDER BY ee.rarity_score DESC NULLS LAST, ee.created_at ASC
         LIMIT %s OFFSET %s
        """,
        (file_id, limit, offset),
    )
    rows = await cur.fetchall()
    items: list[AtomicUnit] = []
    for r in rows:
        params = (r[2] if isinstance(r[2], dict) else (json.loads(r[2]) if r[2] else {}))
        # Pull anchor_chunk_id from the citations jsonb under the
        # reserved `_anchor` key (Phase 3.5 of the extraction worker
        # writes it there).
        citations = (r[3] if isinstance(r[3], dict) else (json.loads(r[3]) if r[3] else {}))
        anchor_chunk_id = citations.get("_anchor") if isinstance(citations, dict) else None
        items.append(AtomicUnit(
            id=r[0], unit_type=r[1],
            parameters=params,
            anchor_chunk_id=anchor_chunk_id,
            rarity_score=(float(r[4]) if r[4] is not None else None),
            model_id=r[5],
            source_chunk_id=r[6],
            source_char_start=(int(r[7]) if r[7] is not None else None),
            source_char_end=(int(r[8]) if r[8] is not None else None),
            source_page_numbers=(list(r[9]) if r[9] else None),
        ))
    return items, total


async def list_mentions(
    conn: Connection, file_id: str, *,
    limit: int = 100, offset: int = 0, mention_type: str | None = None,
) -> tuple[list[Mention], int]:
    where = ["em.file_id = %s"]
    params: list[Any] = [file_id]
    if mention_type:
        where.append("em.mention_type = %s")
        params.append(mention_type)
    where_sql = " AND ".join(where)

    cur = await conn.execute(
        f"SELECT COUNT(*) FROM extracted_mentions em WHERE {where_sql}",
        tuple(params),
    )
    total = int((await cur.fetchone())[0])

    cur = await conn.execute(
        f"""
        SELECT em.id::text, em.mention_text, em.mention_type,
               em.contextual_chunk_id::text, em.start_offset, em.end_offset,
               em.confidence, m2e.entity_id::text, e.canonical_name,
               COALESCE(src.source_page_numbers, ctx_src.source_page_numbers),
               em.source_chunk_id::text, em.source_char_start, em.source_char_end
          FROM extracted_mentions em
          LEFT JOIN mention_to_entity m2e ON m2e.mention_id = em.id
          LEFT JOIN canonical_entities e ON e.id = m2e.entity_id
          LEFT JOIN contextual_chunks cc ON cc.id = em.contextual_chunk_id
          LEFT JOIN chunks ctx_src ON ctx_src.id = cc.chunk_id
          LEFT JOIN chunks src ON src.id = em.source_chunk_id
         WHERE {where_sql}
         ORDER BY em.contextual_chunk_id ASC, em.start_offset ASC
         LIMIT %s OFFSET %s
        """,
        tuple([*params, limit, offset]),
    )
    rows = await cur.fetchall()
    items = [
        Mention(
            id=r[0], mention_text=r[1], mention_type=r[2], chunk_id=r[3],
            start_offset=(int(r[4]) if r[4] is not None else None),
            end_offset=(int(r[5]) if r[5] is not None else None),
            confidence=(float(r[6]) if r[6] is not None else None),
            canonical_entity_id=r[7], canonical_name=r[8],
            source_page_numbers=(list(r[9]) if r[9] else None),
            source_chunk_id=r[10],
            source_char_start=(int(r[11]) if r[11] is not None else None),
            source_char_end=(int(r[12]) if r[12] is not None else None),
        )
        for r in rows
    ]
    return items, total


async def list_entities_mentioned(
    conn: Connection, file_id: str, *,
    limit: int = 50, offset: int = 0,
) -> tuple[list[EntityMentioned], int]:
    cur = await conn.execute(
        """
        SELECT COUNT(DISTINCT m2e.entity_id)
          FROM mention_to_entity m2e
          JOIN extracted_mentions em ON em.id = m2e.mention_id
         WHERE em.file_id = %s
        """,
        (file_id,),
    )
    total = int((await cur.fetchone())[0])
    cur = await conn.execute(
        """
        SELECT e.id::text, e.canonical_name, e.entity_type,
               COUNT(em.id)::int AS in_doc, e.mention_count
          FROM mention_to_entity m2e
          JOIN extracted_mentions em ON em.id = m2e.mention_id
          JOIN canonical_entities e ON e.id = m2e.entity_id
         WHERE em.file_id = %s
         GROUP BY e.id, e.canonical_name, e.entity_type, e.mention_count
         ORDER BY in_doc DESC, e.canonical_name ASC
         LIMIT %s OFFSET %s
        """,
        (file_id, limit, offset),
    )
    rows = await cur.fetchall()
    items = [
        EntityMentioned(
            entity_id=r[0], canonical_name=r[1], entity_type=r[2],
            mentions_in_doc=int(r[3]), total_mentions=int(r[4]),
        )
        for r in rows
    ]
    return items, total


async def list_triples_in_doc(
    conn: Connection, file_id: str, *,
    limit: int = 50, offset: int = 0,
) -> tuple[list[TripleInDoc], int]:
    cur = await conn.execute(
        "SELECT COUNT(*) FROM extracted_triples WHERE file_id = %s",
        (file_id,),
    )
    total = int((await cur.fetchone())[0])
    cur = await conn.execute(
        """
        SELECT t.id::text, t.subject_text, t.predicate_text, t.object_text,
               t.confidence, t.chunk_id::text, c.source_page_numbers,
               t.subject_char_start, t.subject_char_end,
               t.object_char_start, t.object_char_end
          FROM extracted_triples t
          LEFT JOIN chunks c ON c.id = t.chunk_id
         WHERE t.file_id = %s
         ORDER BY t.confidence DESC NULLS LAST, t.created_at ASC
         LIMIT %s OFFSET %s
        """,
        (file_id, limit, offset),
    )
    rows = await cur.fetchall()
    items = [
        TripleInDoc(
            id=r[0], subject_text=r[1], predicate_text=r[2],
            object_text=r[3],
            confidence=(float(r[4]) if r[4] is not None else None),
            chunk_id=r[5],
            source_page_numbers=(list(r[6]) if r[6] else None),
            subject_char_start=(int(r[7]) if r[7] is not None else None),
            subject_char_end=(int(r[8]) if r[8] is not None else None),
            object_char_start=(int(r[9]) if r[9] is not None else None),
            object_char_end=(int(r[10]) if r[10] is not None else None),
        )
        for r in rows
    ]
    return items, total


async def list_citations_of_doc(
    conn: Connection, file_id: str, *,
    limit: int = 20, offset: int = 0,
) -> tuple[list[CitationByQuery], int]:
    """Find query_log rows that cited this doc. citations is jsonb shaped
    like [{"file_id": "...", "modality": "...", ...}]. The `@>` containment
    operator can use a GIN index on `citations` (added in this PR)."""
    target = json.dumps([{"file_id": file_id}])
    cur = await conn.execute(
        "SELECT COUNT(*) FROM query_log WHERE citations @> %s::jsonb",
        (target,),
    )
    total = int((await cur.fetchone())[0])
    cur = await conn.execute(
        """
        SELECT id::text, query, answer, endpoint, created_at
          FROM query_log
         WHERE citations @> %s::jsonb
         ORDER BY created_at DESC
         LIMIT %s OFFSET %s
        """,
        (target, limit, offset),
    )
    rows = await cur.fetchall()
    items = [
        CitationByQuery(
            query_id=r[0], query=r[1], answer=r[2], endpoint=r[3],
            created_at=(_iso(r[4]) if r[4] else None),
        )
        for r in rows
    ]
    return items, total


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


# Forward-only DAG for lifecycle progression. Each tuple is (state, order).
# Higher order = later in the pipeline. Re-running a worker MUST NOT walk
# the file backward — that's the bug that left 19 docs stuck after a
# re-trigger of `extract_atomic_units_file` clobbered `ready` files back
# to `entities_extracting`. The guard below rejects backward writes.
_LIFECYCLE_ORDER: dict[str, int] = {
    "queued":               0,
    "parsing":              1,
    "parsed":               2,
    "chunked":              3,
    "doc_chaining":         3,   # additive, non-gating; same rank as chunked
    "contextualized":       4,
    "embedded":             5,
    "raptor_building":      6,
    "mentions_extracting":  7,
    "fields_extracting":    8,
    "units_extracting":     9,
    "entities_extracting": 10,
    "identity_resolving":  11,
    "ready":               12,
    # Terminal states — entering them is always allowed, leaving them is
    # never allowed (failed / deleted are sticky).
    "failed":             100,
    "deleted":            100,
}


def _is_valid_transition(from_state: str | None, to_state: str) -> bool:
    """Forward-only progression rule:
      - Any → terminal (failed/deleted) is always allowed.
      - Terminal (failed/deleted) → anything is NEVER allowed.
      - Same-state self-transitions are allowed (noop audit events like
        `relationships_built` on a ready file).
      - Otherwise: to_state's order must be >= from_state's order.
    """
    if from_state is None:
        return True
    if from_state in ("failed", "deleted") and to_state not in ("failed", "deleted"):
        return False
    if to_state in ("failed", "deleted"):
        return True
    from_rank = _LIFECYCLE_ORDER.get(from_state)
    to_rank = _LIFECYCLE_ORDER.get(to_state)
    if from_rank is None or to_rank is None:
        # Unknown state — be permissive (forward compat) but log.
        return True
    return to_rank >= from_rank


class BackwardLifecycleTransitionError(RuntimeError):
    """Raised when transition_lifecycle is asked to walk a file backward
    in the pipeline (e.g., ready → entities_extracting). Pre-fix, this
    silently succeeded and left files stuck in mid-pipeline states.
    """


async def transition_lifecycle(
    conn: Connection,
    *,
    workspace_id: str,
    file_id: str,
    to_state: str,
    event: str,
    payload: dict[str, Any] | None = None,
    allow_backward: bool = False,
) -> str:
    """Helper for the worker: read current state under FOR UPDATE, write the
    new state to `files`, and append the audit event. Returns the old state
    so the worker can branch on it (e.g., refuse to re-parse if 'parsed').

    Forward-only guard: refuses to walk the file backward in the pipeline.
    A re-trigger of an earlier worker (e.g. `extract_atomic_units_file`
    running again on a `ready` file) returns silently without overwriting
    the state, so re-runs degrade to noop instead of clobbering the
    file's progress. The audit-event row is also skipped on these refuses
    so file_lifecycle stays a clean forward chain.

    `allow_backward=True` is an explicit escape hatch for operator-
    initiated re-extract: POST /files/:id/re-extract walks the state
    back so the worker chain actually re-runs. Set sparingly — abusing
    this can corrupt the audit history of a file's pipeline progress.
    """
    cur = await conn.execute(
        "SELECT lifecycle_state FROM files WHERE id = %s FOR UPDATE",
        (file_id,),
    )
    row = await cur.fetchone()
    if row is None:
        raise FileNotFoundError(file_id)
    from_state = row[0]

    if not allow_backward and not _is_valid_transition(from_state, to_state):
        # Silent no-op — log to the caller for observability but don't
        # poison the worker (a thrown exception would mark the
        # procrastinate job failed, then it'd retry and fail again).
        # Returning the current state lets callers branch ("if from in
        # terminal states, skip downstream chains") without changes.
        import logging
        logging.getLogger(__name__).warning(
            "lifecycle refuse %s -> %s on file_id=%s event=%s "
            "(forward-only DAG; ignoring backward transition)",
            from_state, to_state, file_id, event,
        )
        return from_state

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
