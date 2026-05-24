"""Procrastinate worker tasks — Phase 2a (`parse_file`) + Phase 3a (`chunk_file`)
+ Phase 3b (`contextualize_file`) + Phase 3c (`embed_file`).

Wire-level Procrastinate tasks delegate to *_impl functions tests can call
directly without spinning up the queue.

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
from kb.contextualization import (
    ContextualizationError,
    ContextualizedChunk,
    make_contextualizer,
)
from kb.db.pool import open_connection
from kb.domain.chunk_embeddings import (
    insert_chunk_embedding,
    read_contextual_chunks_for_embedding,
)
from kb.domain.chunks import (
    count_chunks_for_file,
    insert_chunk,
    read_pages_for_chunking,
)
from kb.domain.contextual_chunks import (
    insert_contextual_chunk,
    read_chunks_for_contextualization,
    read_doc_text,
)
from kb.domain.files import (
    FileNotFoundError,
    record_lifecycle_event,
    transition_lifecycle,
)
from kb.domain.raw_pages import insert_raw_page
from kb.embeddings import EmbeddingError, make_embedder
from kb.parsers import (
    NoParserForMime,
    Page,
    ParsedDocument,
    ParseError,
    global_registry,
    register_default_parsers,
    select_parser_for,
)
from kb.parsers.gemini_ocr_parser import OCRConfigError
from kb.parsers.quality import (
    build_provenance,
    escalate_per_page,
    score_parse_quality,
    should_escalate,
)
from kb.storage.files import get_file_bytes
from kb.workers.app import app as procrastinate_app


async def parse_file_impl(file_id: str, forced_parser: str | None = None) -> None:
    """Pure async core. Reads the file row, sets workspace context, fetches
    bytes from MinIO, dispatches to a parser, writes raw_pages + lifecycle.

    Per-stage idempotency: if `files.lifecycle_state == 'parsed'` at entry,
    return immediately.

    Phase 2c (§5.6.1): `forced_parser` (from `POST /files?parser=...`) overrides
    the dispatcher strategy when set. Quality-escalation is wired in after
    Docling parses — bad output triggers a Gemini OCR re-parse and the
    provenance metadata lands in `raw_pages.layout_json.provenance`.
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
        import os
        try:
            file_bytes = get_file_bytes(object_key)
            magic = file_bytes[:8]

            # Phase 2c §5.6.1: strategy-aware dispatch (sniff + forced_parser).
            parser = select_parser_for(
                mime_type=mime_type,
                magic_bytes=magic,
                file_bytes=file_bytes,
                forced_parser=forced_parser,
            )
            doc = await parser.parse(
                file_bytes, file_id=file_id, workspace_id=str(workspace_id),
            )

            # Phase 2c quality escalation (§5.6.1 #10): only meaningful when
            # the chosen parser is Docling. Gemini-OCR results are accepted
            # as-is; if Gemini failed, ParseError already fired above.
            chose, tried, doc, provenance_reason, quality = await _maybe_escalate_to_ocr(
                doc=doc,
                file_id=file_id,
                workspace_id=str(workspace_id),
                file_bytes=file_bytes,
                primary_parser=parser,
                forced_parser=forced_parser,
            )
            strategy_for_provenance = (
                os.environ.get("KB_PARSER_STRATEGY") or "auto"
            ).lower()
            provenance = build_provenance(
                strategy=strategy_for_provenance,
                forced_parser=forced_parser,
                tried=tried,
                chose=chose,
                reason=provenance_reason,
                quality_score=quality,
            )
        except (ParseError, NoParserForMime, OCRConfigError) as exc:
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
                    layout = dict(page.layout_json or {})
                    # Phase 2c §5.6.1 #12: stamp every raw_pages row with the
                    # provenance JSON so downstream consumers can attribute
                    # the OCR/parse source per page.
                    layout["provenance"] = provenance
                    await insert_raw_page(
                        conn,
                        file_id=file_id,
                        workspace_id=str(workspace_id),
                        page_number=page.page_number,
                        text=page.text,
                        layout_json=layout,
                        content_sha=sha,
                    )
                await transition_lifecycle(
                    conn,
                    workspace_id=str(workspace_id),
                    file_id=file_id,
                    to_state="parsed",
                    event="parse_done",
                    payload={
                        "parser": chose,
                        "pages": len(doc.pages),
                        "provenance": provenance,
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


async def _maybe_escalate_to_ocr(
    *,
    doc: ParsedDocument,
    file_id: str,
    workspace_id: str,
    file_bytes: bytes,
    primary_parser: object,
    forced_parser: str | None,
) -> tuple[str, list[str], ParsedDocument, str, float | None]:
    """Phase 2c §5.6.1 #10 — quality-escalation decision.

    Inputs: the document produced by the primary parser (typically Docling),
    plus the original file bytes for a potential per-page Gemini OCR retry.

    Returns `(chose, tried, final_doc, reason, quality_score)`:
      - `chose`: 'docling', 'gemini_ocr', or the underlying parser slug
      - `tried`: list of parser slugs in execution order
      - `final_doc`: the ParsedDocument written to raw_pages (may be the
        original, a per-page-patched version, or a full Gemini retry)
      - `reason`: human-readable explanation for the provenance JSON
      - `quality_score`: score in [0.0, 1.0] from the primary parse, or None
        when no Docling output existed to score (forced gemini path)
    """
    primary_slug = _parser_slug(primary_parser)
    tried = [primary_slug]

    # If the primary was already Gemini OCR (forced or strategy-driven), no
    # escalation makes sense — Gemini IS the escalation target.
    if primary_slug != "docling":
        return primary_slug, tried, doc, "primary parser produced output", None

    # Score quality + decide on escalation.
    quality = score_parse_quality(doc)
    escalate_whole, reason = should_escalate(doc)
    bad_pages = escalate_per_page(doc) if not escalate_whole else []

    # If neither signal fires, keep Docling output as-is.
    if not escalate_whole and not bad_pages:
        return primary_slug, tried, doc, "quality_ok", quality

    # If forced_parser explicitly said "docling", do NOT escalate — caller
    # asked for Docling and we respect that even when quality is poor.
    if forced_parser == "docling":
        return primary_slug, tried, doc, (
            f"{reason} (caller forced parser=docling — escalation suppressed)"
        ), quality

    # We need Gemini OCR. Verify the key is present before rendering pages.
    import os
    if not os.environ.get("KB_GEMINI_API_KEY"):
        # No key → can't escalate. Keep the bad Docling output but record
        # the reason in provenance so dashboards can alert on this.
        return primary_slug, tried, doc, (
            f"{reason} (escalation skipped: KB_GEMINI_API_KEY unset)"
        ), quality

    from kb.parsers.gemini_ocr_parser import GeminiOCRParser
    gemini = GeminiOCRParser(api_key=os.environ["KB_GEMINI_API_KEY"])

    if escalate_whole:
        # Re-parse the entire document via Gemini OCR.
        try:
            new_doc = await gemini.parse(
                file_bytes, file_id=file_id, workspace_id=workspace_id,
            )
        except ParseError as exc:
            return primary_slug, tried + ["gemini_ocr"], doc, (
                f"{reason} (escalation attempted but failed: {exc})"
            ), quality
        tried.append("gemini_ocr")
        return "gemini_ocr", tried, new_doc, (
            f"escalated whole doc: {reason}"
        ), quality

    # Hybrid case: per-page escalation for bad_pages only.
    try:
        gemini_doc = await gemini.parse(
            file_bytes, file_id=file_id, workspace_id=workspace_id,
        )
    except ParseError as exc:
        return primary_slug, tried + ["gemini_ocr"], doc, (
            f"{reason} (per-page escalation attempted but failed: {exc})"
        ), quality
    tried.append("gemini_ocr")

    # Patch in the Gemini pages for the bad_pages set; keep Docling for rest.
    gemini_by_pn = {p.page_number: p for p in gemini_doc.pages}
    patched: list[Page] = []
    for page in doc.pages:
        if page.page_number in bad_pages and page.page_number in gemini_by_pn:
            patched.append(gemini_by_pn[page.page_number])
        else:
            patched.append(page)
    patched_doc = ParsedDocument(pages=patched)
    return "gemini_ocr", tried, patched_doc, (
        f"per-page escalation: re-OCR'd pages {bad_pages}"
    ), quality


def _parser_slug(parser: object) -> str:
    """Map a parser instance to its provenance slug used in lifecycle events."""
    cls = type(parser).__name__
    return {
        "DoclingParser": "docling",
        "GeminiOCRParser": "gemini_ocr",
        "MistralOCRParser": "mistral_ocr",
        "XLSXParser": "xlsx",
        "EmailParser": "email",
    }.get(cls, cls.lower())


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

    # Phase 3b decision #13: chain contextualize_file in a SEPARATE tx so a
    # Procrastinate-defer failure doesn't roll back the chunked state.
    try:
        await procrastinate_app.configure_task(
            name="contextualize_file"
        ).defer_async(file_id=file_id)
    except Exception:  # noqa: BLE001 — best-effort chain
        traceback.print_exc()


# ---------------------------------------------------------------------------
# Phase 3b — contextualize_file_impl
# ---------------------------------------------------------------------------


async def contextualize_file_impl(file_id: str) -> None:
    """Run the Anthropic Contextual Retrieval prefix call on every chunk of a
    'chunked' file → write contextual_chunks → advance to 'contextualized'.

    Per build_tracker §5.8:
    - decision #6: KB_ANTHROPIC_API_KEY unset → IdentityContextualizer
      (lifecycle still advances; recall degrades to baseline).
    - decision #12: lifecycle widens with 'contextualized'.
    - decision #13: chained from chunk_file_impl via separate-tx defer.
    - decision #14: API errors → 'chunked→failed'.

    Per-stage idempotency: returns immediately if already 'contextualized'.
    """
    from kb.config import get_settings
    settings = get_settings()
    db_url = settings.database_url

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

            if lifecycle_state in ("contextualized", "failed", "deleted"):
                return
            if lifecycle_state != "chunked":
                # Not yet ready to contextualize (still parsing/parsed).
                return

            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)",
                (str(workspace_id),),
            )
            doc_text = await read_doc_text(conn, file_id=file_id)
            chunk_rows = await read_chunks_for_contextualization(
                conn, file_id=file_id,
            )

    if not chunk_rows:
        await _mark_failed(
            db_url, file_id, str(workspace_id),
            error_class="ContextualizationError",
            message=f"empty chunks for file={file_id}",
            from_state="chunked",
            event="contextualization_failed",
        )
        return

    # Phase 2: contextualize each chunk (factory picks Anthropic or Identity).
    contextualizer = make_contextualizer()
    results: list[tuple[str, str, ContextualizedChunk]] = []
    try:
        for chunk_id, chunk_text in chunk_rows:
            result = await contextualizer.contextualize(
                doc_text=doc_text, chunk_text=chunk_text,
            )
            results.append((chunk_id, chunk_text, result))
    except ContextualizationError as exc:
        await _mark_failed(
            db_url, file_id, str(workspace_id),
            error_class="ContextualizationError",
            message=str(exc),
            from_state="chunked",
            event="contextualization_failed",
        )
        return
    except Exception as exc:
        await _mark_failed(
            db_url, file_id, str(workspace_id),
            error_class=type(exc).__name__,
            message=str(exc),
            from_state="chunked",
            event="contextualization_failed",
            traceback_head=traceback.format_exc()[:2000],
        )
        return

    # Phase 3: write contextual_chunks + lifecycle event.
    total_cache_creation = sum(r.cache_creation_input_tokens for _, _, r in results)
    total_cache_read = sum(r.cache_read_input_tokens for _, _, r in results)
    model_id = results[0][2].model_id if results else "unknown"

    async with open_connection(db_url) as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)",
                (str(workspace_id),),
            )
            for chunk_id, _, result in results:
                await insert_contextual_chunk(
                    conn,
                    chunk_id=chunk_id,
                    file_id=file_id,
                    workspace_id=str(workspace_id),
                    contextual_prefix=result.contextual_prefix,
                    contextual_text=result.contextual_text,
                    model_id=result.model_id,
                    prefix_token_count=result.prefix_token_count,
                    cache_creation_input_tokens=result.cache_creation_input_tokens,
                    cache_read_input_tokens=result.cache_read_input_tokens,
                )
            await transition_lifecycle(
                conn,
                workspace_id=str(workspace_id),
                file_id=file_id,
                to_state="contextualized",
                event="contextualization_done",
                payload={
                    "prefix_count": len(results),
                    "total_cache_creation_tokens": total_cache_creation,
                    "total_cache_read_tokens": total_cache_read,
                    "model_id": model_id,
                },
            )

    # Phase 3c decision #11: chain embed_file in a SEPARATE tx so a
    # Procrastinate-defer failure doesn't roll back the contextualized state.
    try:
        await procrastinate_app.configure_task(
            name="embed_file"
        ).defer_async(file_id=file_id)
    except Exception:  # noqa: BLE001 — best-effort chain
        traceback.print_exc()


# ---------------------------------------------------------------------------
# Phase 3c — embed_file_impl
# ---------------------------------------------------------------------------


async def embed_file_impl(file_id: str) -> None:
    """Embed every contextual_chunks row for a 'contextualized' file → write
    chunk_embeddings → advance lifecycle to 'embedded'.

    Per build_tracker §5.9:
    - decision #4: KB_GEMINI_API_KEY unset → DeterministicMockEmbedder
      (lifecycle still advances; clustering quality degrades for Phase 3d).
    - decision #10: lifecycle widens with 'embedded'.
    - decision #11: chained from contextualize_file_impl via separate-tx defer.
    - decision #13: API errors → 'contextualized→failed'.

    Per-stage idempotency: returns immediately if already 'embedded' or beyond.
    """
    from kb.config import get_settings
    settings = get_settings()
    db_url = settings.database_url

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

            if lifecycle_state in ("embedded", "ready", "failed", "deleted"):
                return
            if lifecycle_state != "contextualized":
                # Not yet ready to embed.
                return

            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)",
                (str(workspace_id),),
            )
            ctx_rows = await read_contextual_chunks_for_embedding(
                conn, file_id=file_id,
            )

    if not ctx_rows:
        await _mark_failed(
            db_url, file_id, str(workspace_id),
            error_class="EmbeddingError",
            message=f"empty contextual_chunks for file={file_id}",
            from_state="contextualized",
            event="embedding_failed",
        )
        return

    # Phase 2: batch-embed (factory picks Gemini or Mock).
    embedder = make_embedder()
    texts = [text for _, text in ctx_rows]
    try:
        results = await embedder.embed_batch(texts)
    except EmbeddingError as exc:
        await _mark_failed(
            db_url, file_id, str(workspace_id),
            error_class="EmbeddingError",
            message=str(exc),
            from_state="contextualized",
            event="embedding_failed",
        )
        return
    except Exception as exc:
        await _mark_failed(
            db_url, file_id, str(workspace_id),
            error_class=type(exc).__name__,
            message=str(exc),
            from_state="contextualized",
            event="embedding_failed",
            traceback_head=traceback.format_exc()[:2000],
        )
        return

    if len(results) != len(ctx_rows):
        await _mark_failed(
            db_url, file_id, str(workspace_id),
            error_class="EmbeddingError",
            message=(
                f"embedder returned {len(results)} vectors for "
                f"{len(ctx_rows)} chunks"
            ),
            from_state="contextualized",
            event="embedding_failed",
        )
        return

    # Phase 3: write chunk_embeddings + lifecycle event.
    dim = results[0].dim
    model_id = results[0].model_id

    async with open_connection(db_url) as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)",
                (str(workspace_id),),
            )
            for (ctx_id, _), embedding in zip(ctx_rows, results, strict=True):
                await insert_chunk_embedding(
                    conn,
                    contextual_chunk_id=ctx_id,
                    file_id=file_id,
                    workspace_id=str(workspace_id),
                    vector=embedding.vector,
                    model_id=embedding.model_id,
                )
            await transition_lifecycle(
                conn,
                workspace_id=str(workspace_id),
                file_id=file_id,
                to_state="embedded",
                event="embedding_done",
                payload={
                    "embedding_count": len(results),
                    "dim": dim,
                    "model_id": model_id,
                },
            )


# ---------------------------------------------------------------------------
# Procrastinate task registration
# ---------------------------------------------------------------------------


@procrastinate_app.task(name="parse_file", queue="kb", pass_context=False)
async def parse_file(file_id: str, forced_parser: str | None = None) -> None:
    """Wire-level Procrastinate task. Delegates to the testable impl.

    Phase 2c §5.6.1: `forced_parser` (from `POST /files?parser=...`) is
    forwarded to the dispatcher so the worker honors caller overrides.
    """
    await parse_file_impl(file_id, forced_parser=forced_parser)


@procrastinate_app.task(name="chunk_file", queue="kb", pass_context=False)
async def chunk_file(file_id: str) -> None:
    """Wire-level Procrastinate task. Delegates to the testable impl."""
    await chunk_file_impl(file_id)


@procrastinate_app.task(name="contextualize_file", queue="kb", pass_context=False)
async def contextualize_file(file_id: str) -> None:
    """Wire-level Procrastinate task. Delegates to the testable impl."""
    await contextualize_file_impl(file_id)


@procrastinate_app.task(name="embed_file", queue="kb", pass_context=False)
async def embed_file(file_id: str) -> None:
    """Wire-level Procrastinate task. Delegates to the testable impl."""
    await embed_file_impl(file_id)
