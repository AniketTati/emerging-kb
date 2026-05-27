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

import asyncio
import hashlib
import os
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

        # WA-3 / Design 3 — additive doc-chain detection runs in parallel
        # with chunking. Side-effect only: no lifecycle gating. If the
        # defer fails, file still progresses through the rest of the
        # pipeline; chains can be re-detected later by an admin trigger.
        try:
            await procrastinate_app.configure_task(
                name="detect_doc_chain_file"
            ).defer_async(file_id=file_id)
        except Exception:  # noqa: BLE001 — best-effort chain
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

    # Phase 2: contextualize each chunk in parallel under a concurrency cap.
    # §5.8 decision #4: KB_CONTEXTUAL_CONCURRENCY (default 8) — bounds the
    # in-flight Anthropic/Gemini calls per doc. Same shape as §5.10 decision
    # #8 for the Summarizer (Semaphore at the worker, not the adapter).
    import os as _os
    contextualizer = make_contextualizer()
    concurrency = int(_os.environ.get("KB_CONTEXTUAL_CONCURRENCY") or 8)
    semaphore = asyncio.Semaphore(concurrency)

    async def _contextualize_one(chunk_id: str, chunk_text: str):
        async with semaphore:
            result = await contextualizer.contextualize(
                doc_text=doc_text, chunk_text=chunk_text,
            )
            return chunk_id, chunk_text, result

    try:
        results = await asyncio.gather(*(
            _contextualize_one(cid, ctext) for cid, ctext in chunk_rows
        ))
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

    # Phase 3d decision #13: chain raptor_build_file in a SEPARATE tx so a
    # Procrastinate-defer failure doesn't roll back the successful embed.
    # Matches the 3a→3b, 3b→3c chaining shape.
    try:
        await procrastinate_app.configure_task(
            name="raptor_build_file"
        ).defer_async(file_id=file_id)
    except Exception:  # noqa: BLE001 — best-effort chain
        traceback.print_exc()


# ---------------------------------------------------------------------------
# Phase 3d — raptor_build_file_impl
# ---------------------------------------------------------------------------


async def raptor_build_file_impl(file_id: str) -> None:
    """Build the per-doc RAPTOR tree for an 'embedded' file → write
    raptor_nodes (L2+) + raptor_edges → advance lifecycle to 'ready'.

    Per build_tracker §5.10:
    - decision #9: L1 leaves stay in contextual_chunks (not denormalized).
    - decision #10: discriminated edge FK (L2 edges → child_contextual_chunk_id;
      L3+ edges → child_node_id).
    - decision #12: lifecycle is embedded → raptor_building → ready (or failed).
      Adds intermediate `raptor_building` state for observability.
    - decision #14: cluster/summarize/embed errors → raptor_building → failed;
      tree-writes happen in one tx so partial failures roll back atomically.
    - decision #15: reuses Phase 3c's `make_embedder()` so leaves + summaries
      live in the same halfvec(3072) vector space.

    Per-stage idempotency: returns immediately if already 'ready' or beyond.
    """
    from kb.config import get_settings
    from kb.domain.raptor import (
        insert_raptor_edge,
        insert_raptor_node,
        read_leaves_for_raptor_build,
    )
    from kb.raptor import (
        DEFAULT_BRANCHING_FACTOR,
        DEFAULT_MAX_LEVELS,
        cluster_embeddings,
    )
    import math
    import os

    settings = get_settings()
    db_url = settings.database_url

    # Phase 1: read file row + check idempotency + transition to raptor_building.
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

            if lifecycle_state in ("ready", "failed", "deleted"):
                return
            if lifecycle_state == "raptor_building":
                # Concurrent invocation in flight — let it finish.
                return
            if lifecycle_state != "embedded":
                # Not yet ready to build.
                return

            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)",
                (str(workspace_id),),
            )
            await transition_lifecycle(
                conn,
                workspace_id=str(workspace_id),
                file_id=file_id,
                to_state="raptor_building",
                event="raptor_build_started",
                payload={},
            )
            leaves = await read_leaves_for_raptor_build(conn, file_id=file_id)

    if not leaves:
        await _mark_failed(
            db_url, file_id, str(workspace_id),
            error_class="RaptorBuildError",
            message=f"no contextual_chunks/chunk_embeddings for file={file_id}",
            from_state="raptor_building",
            event="raptor_build_failed",
        )
        return

    # Phase 2: build tree in memory (cluster + summarize + embed each level).
    leaf_ids = [cc_id for cc_id, _, _, _ in leaves]
    leaf_texts = [text for _, text, _, _ in leaves]
    leaf_embeddings = [vec for _, _, vec, _ in leaves]

    branching_factor = int(
        os.environ.get("KB_RAPTOR_BRANCHING_FACTOR") or DEFAULT_BRANCHING_FACTOR
    )
    max_levels = int(
        os.environ.get("KB_RAPTOR_MAX_LEVELS") or DEFAULT_MAX_LEVELS
    )
    concurrency = int(
        os.environ.get("KB_SUMMARIZER_CONCURRENCY") or 4
    )

    # Build state: track level → list of (text, vector, raptor_node_id|None,
    # child_indexes_at_prev_level). At L=2, children are contextual_chunks
    # (no node IDs); at L>=3, children are raptor_nodes (have IDs).
    from kb.summarization import SummarizationError, make_summarizer
    summarizer = make_summarizer()
    embedder = make_embedder()
    summarizer_model_id = ""
    embedder_model_id = ""
    levels_built: list[int] = []
    total_summarizer_calls = 0

    # Per-level state: at L=2, prev_ids = contextual_chunk_ids, prev_kind="chunk";
    # at L>=3, prev_ids = raptor_node_ids,             prev_kind="node".
    prev_texts = list(leaf_texts)
    prev_embeddings = list(leaf_embeddings)
    prev_ids = list(leaf_ids)
    prev_kind = "chunk"

    semaphore = asyncio.Semaphore(concurrency)

    # Collect writes for a single atomic transaction (decision #14).
    writes: list[dict] = []  # list of {"kind": "node"|"edge", "row": {...}}

    try:
        for level in range(2, max_levels + 1):
            n = len(prev_embeddings)
            if n <= 1:
                break
            if n <= branching_factor:
                n_clusters = 1
                labels = [0] * n
            else:
                n_clusters = max(1, math.ceil(n / branching_factor))
                labels = cluster_embeddings(
                    prev_embeddings, branching_factor=branching_factor,
                )

            # Group child-indexes per cluster.
            clusters: dict[int, list[int]] = {}
            for idx, label in enumerate(labels):
                clusters.setdefault(label, []).append(idx)

            # Summarize each cluster in parallel under the semaphore.
            async def _summarize_one(cluster_idx: int, member_indexes: list[int]):
                async with semaphore:
                    cluster_texts = [prev_texts[i] for i in member_indexes]
                    summary = await summarizer.summarize(texts=cluster_texts)
                    return cluster_idx, member_indexes, summary

            summary_results = await asyncio.gather(*(
                _summarize_one(ci, mi) for ci, mi in clusters.items()
            ))
            summary_results.sort(key=lambda t: t[0])
            total_summarizer_calls += len(summary_results)

            # Embed all summaries in a single batch call.
            summary_texts_only = [s.text for _, _, s in summary_results]
            embedding_results = await embedder.embed_batch(summary_texts_only)
            if len(embedding_results) != len(summary_results):
                raise RuntimeError(
                    f"embedder returned {len(embedding_results)} vectors "
                    f"for {len(summary_results)} summaries"
                )

            # Stage node-writes for this level + edge-writes from children.
            new_level_texts: list[str] = []
            new_level_embeddings: list[list[float]] = []
            new_level_synthetic_ids: list[tuple[int, list[int]]] = []  # (cluster_idx, member_indexes)
            for (cluster_idx, member_indexes, summary), emb_result in zip(
                summary_results, embedding_results, strict=True,
            ):
                writes.append({
                    "kind": "node",
                    "scope": "per_doc",
                    "file_id": file_id,
                    "workspace_id": str(workspace_id),
                    "level": level,
                    "text": summary.text,
                    "vector": list(emb_result.vector),
                    "cluster_id_in_level": cluster_idx,
                    "summarizer_model_id": summary.model_id,
                    "embedder_model_id": emb_result.model_id,
                    "token_count": summary.output_token_count,
                })
                summarizer_model_id = summary.model_id
                embedder_model_id = emb_result.model_id
                # Edges from this node to its member children. We don't know
                # node_id yet (assigned at INSERT time) — write a placeholder
                # keyed on (level, cluster_idx) that gets resolved at write time.
                for child_idx in member_indexes:
                    child_id = prev_ids[child_idx]
                    if prev_kind == "chunk":
                        writes.append({
                            "kind": "edge",
                            "parent_level": level,
                            "parent_cluster_idx": cluster_idx,
                            "child_contextual_chunk_id": child_id,
                            "workspace_id": str(workspace_id),
                        })
                    else:
                        writes.append({
                            "kind": "edge",
                            "parent_level": level,
                            "parent_cluster_idx": cluster_idx,
                            "child_node_synthetic": (level - 1, child_idx),
                            "workspace_id": str(workspace_id),
                        })

                new_level_texts.append(summary.text)
                new_level_embeddings.append(list(emb_result.vector))
                new_level_synthetic_ids.append((cluster_idx, member_indexes))

            levels_built.append(level)

            # Advance state for the next level.
            prev_texts = new_level_texts
            prev_embeddings = new_level_embeddings
            prev_ids = [None] * len(new_level_texts)  # resolved at write time
            prev_kind = "node"

            if len(new_level_texts) <= 1:
                # Root reached.
                break

    except SummarizationError as exc:
        await _mark_failed(
            db_url, file_id, str(workspace_id),
            error_class="SummarizationError",
            message=str(exc),
            from_state="raptor_building",
            event="raptor_build_failed",
        )
        return
    except EmbeddingError as exc:
        await _mark_failed(
            db_url, file_id, str(workspace_id),
            error_class="EmbeddingError",
            message=str(exc),
            from_state="raptor_building",
            event="raptor_build_failed",
        )
        return
    except Exception as exc:
        await _mark_failed(
            db_url, file_id, str(workspace_id),
            error_class=type(exc).__name__,
            message=str(exc),
            from_state="raptor_building",
            event="raptor_build_failed",
            traceback_head=traceback.format_exc()[:2000],
        )
        return

    # Phase 3: atomic DB write of all nodes + edges + lifecycle transition.
    async with open_connection(db_url) as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)",
                (str(workspace_id),),
            )

            # Insert nodes first, building (level, cluster_idx) → node_id map.
            node_id_by_synthetic: dict[tuple[int, int], str] = {}
            for w in writes:
                if w["kind"] == "node":
                    node_id = await insert_raptor_node(
                        conn,
                        scope=w["scope"],
                        file_id=w["file_id"],
                        workspace_id=w["workspace_id"],
                        level=w["level"],
                        text=w["text"],
                        vector=w["vector"],
                        cluster_id_in_level=w["cluster_id_in_level"],
                        summarizer_model_id=w["summarizer_model_id"],
                        embedder_model_id=w["embedder_model_id"],
                        token_count=w["token_count"],
                    )
                    node_id_by_synthetic[(w["level"], w["cluster_id_in_level"])] = node_id

            # Now insert edges using the resolved node_ids.
            for w in writes:
                if w["kind"] == "edge":
                    parent_id = node_id_by_synthetic[
                        (w["parent_level"], w["parent_cluster_idx"])
                    ]
                    if "child_contextual_chunk_id" in w:
                        await insert_raptor_edge(
                            conn,
                            parent_node_id=parent_id,
                            workspace_id=w["workspace_id"],
                            child_contextual_chunk_id=w["child_contextual_chunk_id"],
                        )
                    else:
                        child_level, child_idx = w["child_node_synthetic"]
                        child_node_id = node_id_by_synthetic[(child_level, child_idx)]
                        await insert_raptor_edge(
                            conn,
                            parent_node_id=parent_id,
                            workspace_id=w["workspace_id"],
                            child_node_id=child_node_id,
                        )

            # Phase 5a §5.12.1 #7: transition to mentions_extracting (was 'ready'
            # in 3d; Phase 5a inserts mentions → fields → units before ready).
            await transition_lifecycle(
                conn,
                workspace_id=str(workspace_id),
                file_id=file_id,
                to_state="mentions_extracting",
                event="raptor_build_done",
                payload={
                    "leaf_count": len(leaves),
                    "levels_built": levels_built,
                    "total_summarizer_calls": total_summarizer_calls,
                    "summarizer_model_id": summarizer_model_id,
                    "embedder_model_id": embedder_model_id,
                },
            )

    # Phase 5a §5.12.1 #7: chain extract_mentions_file in a SEPARATE tx so a
    # Procrastinate-defer failure doesn't roll back the successful raptor build.
    # Matches the 3a→3b, 3b→3c, 3c→3d chaining shape.
    try:
        await procrastinate_app.configure_task(
            name="extract_mentions_file"
        ).defer_async(file_id=file_id)
    except Exception:  # noqa: BLE001 — best-effort chain
        traceback.print_exc()


# ---------------------------------------------------------------------------
# Phase 5a — extract_mentions_file_impl
# ---------------------------------------------------------------------------


async def extract_mentions_file_impl(file_id: str) -> None:
    """Extract mentions for a `mentions_extracting` file → write
    extracted_mentions rows → advance lifecycle to `fields_extracting`.

    Per build_tracker §5.12.1:
    - decision #1: per-`contextual_chunks` granularity.
    - decision #4: nullable start/end/confidence (LLM may omit).
    - decision #5: immutable storage; re-extract = DELETE+INSERT in same tx.
    - decision #8: at-start idempotency via DELETE existing.
    - decision #9: asyncio.Semaphore(KB_MENTIONS_CONCURRENCY=4) per file.

    Per-stage idempotency: returns immediately if already past
    mentions_extracting.
    """
    import os

    from kb.config import get_settings
    from kb.domain.mentions import (
        delete_mentions_for_file,
        insert_mention,
        read_chunks_for_file_with_source,
    )
    from kb.extraction.mentions import MentionExtractionError, make_mention_extractor
    from kb.extraction.source_resolver import resolve as resolve_source_position

    settings = get_settings()
    db_url = settings.database_url

    # Phase 1: state check.
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

            if lifecycle_state in (
                "fields_extracting", "units_extracting", "ready",
                "failed", "deleted",
            ):
                return
            if lifecycle_state != "mentions_extracting":
                # Out-of-order or pre-Phase-5 stuck file; skip.
                return

            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)",
                (str(workspace_id),),
            )
            chunks = await read_chunks_for_file_with_source(conn, file_id=file_id)

    # Phase 2: build extractor + extract per chunk under semaphore.
    extractor = make_mention_extractor()
    concurrency = int(os.environ.get("KB_MENTIONS_CONCURRENCY") or 4)
    semaphore = asyncio.Semaphore(concurrency)

    # Use the joined contextual_text as both doc context AND chunk text. The
    # contextual prefix from 3b already gives intra-doc context; a few-chunk
    # join would help but adds cost without clear win at Wave A scale.
    async def _extract_one(cc_id: str, cc_text: str, chunk_id: str, chunk_text: str):
        async with semaphore:
            try:
                result = await extractor.extract(
                    doc_text=cc_text, chunk_text=cc_text
                )
                return cc_id, chunk_id, chunk_text, result
            except MentionExtractionError:
                # Single-chunk failure shouldn't fail the whole file —
                # log and return empty so other chunks proceed.
                traceback.print_exc()
                return cc_id, chunk_id, chunk_text, None

    results = await asyncio.gather(*(
        _extract_one(cc_id, cc_text, chunk_id, chunk_text)
        for cc_id, cc_text, chunk_id, chunk_text in chunks
    ))

    # Phase 3: atomic DB write — DELETE existing + INSERT all new in one tx.
    total_inserted = 0
    model_id_used = ""
    async with open_connection(db_url) as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)",
                (str(workspace_id),),
            )
            await delete_mentions_for_file(conn, file_id=file_id)

            for cc_id, chunk_id, chunk_text, result in results:
                if result is None:
                    continue
                model_id_used = result.model_id
                for mention in result.mentions:
                    # Resolve the exact char range in the original chunk
                    # body so the doc-detail citation UI can highlight
                    # without fuzzy text-search. Returns None when the
                    # mention only appears in the contextual prefix.
                    pos = resolve_source_position(
                        mention.mention_text, chunk_text,
                    )
                    src_chunk = chunk_id if pos else None
                    src_start = pos.char_start if pos else None
                    src_end = pos.char_end if pos else None
                    await insert_mention(
                        conn,
                        contextual_chunk_id=cc_id,
                        file_id=file_id,
                        workspace_id=str(workspace_id),
                        mention_text=mention.mention_text,
                        mention_type=mention.mention_type,
                        start_offset=mention.start_offset,
                        end_offset=mention.end_offset,
                        confidence=mention.confidence,
                        model_id=result.model_id,
                        source_chunk_id=src_chunk,
                        source_char_start=src_start,
                        source_char_end=src_end,
                    )
                    total_inserted += 1

            await transition_lifecycle(
                conn,
                workspace_id=str(workspace_id),
                file_id=file_id,
                to_state="fields_extracting",
                event="mentions_extracted",
                payload={
                    "mention_count": total_inserted,
                    "chunk_count": len(chunks),
                    "model_id": model_id_used or "identity",
                },
            )

    # KV+Tables collapse: defer the new single-call extractor that
    # replaces both extract_fields_file and the LLM-driven portion of
    # extract_atomic_units_file. The chained downstream is the same
    # extract_schema_entities_file once KV+Tables transitions lifecycle
    # to entities_extracting.
    try:
        await procrastinate_app.configure_task(
            name="extract_kv_tables_file"
        ).defer_async(file_id=file_id)
    except Exception:  # noqa: BLE001
        traceback.print_exc()


# ---------------------------------------------------------------------------
# Phase 5b — extract_fields_file_impl
# ---------------------------------------------------------------------------


async def extract_fields_file_impl(file_id: str) -> None:
    """Classify doc-type → propose fields → cluster across workspace+doc_type
    → check promotion thresholds → promote if crossed → advance lifecycle to
    `units_extracting`.

    Per build_tracker §5.12.2 (11 locked decisions).

    Per-stage idempotency: returns immediately if already past fields_extracting.
    """
    from kb.config import get_settings
    from kb.domain.fields import (
        count_docs_of_doctype,
        delete_proposed_fields_for_file,
        insert_proposed_field,
        mark_inferred_field_promoted,
        read_proposed_fields_for_doctype,
        update_file_inferred_doc_type,
        upsert_inferred_schema_field,
    )
    from kb.domain.conflicts import apply_source_authority_from_config
    from kb.extraction.fields import FieldExtractionError, make_field_extractor
    from kb.extraction.promotion import (
        PromotionThresholds,
        cluster_fields_for_doctype,
        ensure_auto_schema_entity,
        promote_field,
        should_promote,
    )

    settings = get_settings()
    db_url = settings.database_url

    # Phase 1: state check + read full doc text for classifier/proposer input.
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

            if lifecycle_state in ("units_extracting", "ready", "failed", "deleted"):
                return
            if lifecycle_state != "fields_extracting":
                return

            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)",
                (str(workspace_id),),
            )
            # Concat raw_pages text as the doc context for classify + propose.
            cur = await conn.execute(
                "SELECT text FROM raw_pages WHERE file_id = %s ORDER BY page_number",
                (file_id,),
            )
            page_rows = await cur.fetchall()
            doc_text = "\n\n".join(r[0] for r in page_rows if r[0])

            # Also pull every chunk for the file so the resolver can map
            # each proposed-field value_text back to a (chunk_id, char
            # offset) at insert time. Citation UI uses these.
            cur = await conn.execute(
                "SELECT id::text, text FROM chunks WHERE file_id = %s "
                "ORDER BY chunk_index ASC",
                (file_id,),
            )
            file_chunks: list[tuple[str, str]] = [
                (r[0], r[1] or "") for r in await cur.fetchall()
            ]

    # Phase 2: classify + propose (LLM calls).
    extractor = make_field_extractor()
    try:
        cls = await extractor.classify(doc_text=doc_text)
        doc_type = cls.doc_type or "unknown"
        proposal = await extractor.propose(doc_text=doc_text)
    except FieldExtractionError:
        # Don't block the chain on extractor failure — log + advance with
        # doc_type='unknown' + no fields. Phase 9 will re-run via admin endpoint.
        traceback.print_exc()
        doc_type = "unknown"
        proposal = None
        cls = None

    # Phase 3: atomic DB write — DELETE existing proposals + INSERT new +
    # update doc_type + recompute clusters + check promotions.
    async with open_connection(db_url) as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)",
                (str(workspace_id),),
            )

            await update_file_inferred_doc_type(
                conn, file_id=file_id, doc_type=doc_type,
            )

            # WA-6 / B2 — apply source_authority from config now that we
            # know the doc-type. Strategy B: additive side-effect only;
            # does not gate lifecycle. Swallow exceptions so an authority
            # lookup failure cannot block the pipeline.
            try:
                await apply_source_authority_from_config(
                    conn,
                    file_id=file_id,
                    workspace_id=str(workspace_id),
                    inferred_doc_type=doc_type,
                )
            except Exception:  # noqa: BLE001
                traceback.print_exc()

            await delete_proposed_fields_for_file(conn, file_id=file_id)

            from kb.extraction.source_resolver import (
                resolve as resolve_source_position,
            )

            n_proposed = 0
            if proposal is not None:
                for f in proposal.fields:
                    # Find the first chunk whose body contains the
                    # extracted value_text — that's the field's source
                    # provenance for the citation UI. Skipped when the
                    # LLM paraphrased or for fields with no value_text.
                    src_chunk = src_start = src_end = None
                    if f.value_text:
                        for cid, ctext in file_chunks:
                            pos = resolve_source_position(f.value_text, ctext)
                            if pos:
                                src_chunk = cid
                                src_start = pos.char_start
                                src_end = pos.char_end
                                break
                    await insert_proposed_field(
                        conn,
                        file_id=file_id,
                        workspace_id=str(workspace_id),
                        inferred_doc_type=doc_type,
                        field_name=f.field_name,
                        field_description=f.field_description,
                        value_text=f.value_text,
                        value_type=f.value_type,
                        is_pii=f.is_pii,
                        model_id=proposal.model_id,
                        source_chunk_id=src_chunk,
                        source_char_start=src_start,
                        source_char_end=src_end,
                    )
                    n_proposed += 1

            # Cross-doc clustering: read all proposed_fields for this
            # (workspace, doc_type) — including the rows we just inserted.
            proposed_per_doc = await read_proposed_fields_for_doctype(
                conn, workspace_id=str(workspace_id), inferred_doc_type=doc_type,
            )
            n_docs = await count_docs_of_doctype(
                conn, workspace_id=str(workspace_id), inferred_doc_type=doc_type,
            )
            clusters = cluster_fields_for_doctype(
                proposed_per_doc=proposed_per_doc,
                total_docs_of_type=n_docs,
            )

            # WA-2 / Design 6 — vocabulary discovery. When two clusters
            # in the same doc_type have semantically similar canonical
            # names ("non_compete" + "non_competition_clause"), emit a
            # synonym entry into `domain_vocabulary` so the query-time
            # BM25 expansion (architecture §6 step 2.5) can union them
            # together. Pre-fix: discovery function existed in
            # extraction/vocabulary.py but nothing called it, so the
            # table stayed empty + the Schema-Studio Vocabulary tab
            # had nothing to show.
            try:
                from kb.extraction.vocabulary import (
                    discover_vocabulary_candidates,
                )
                from kb.domain.vocabulary import upsert_vocabulary

                # Embed every cluster's canonical_name in one batch so
                # the pairwise cosine in discover_vocabulary_candidates
                # can compute similarity. The embedder is the same one
                # used for chunks (Gemini Embedding 001, 3072-dim).
                cluster_names_for_embed = [c.canonical_name for c in clusters]
                if cluster_names_for_embed:
                    from kb.embeddings import make_embedder
                    embedder = make_embedder()
                    embeddings = await embedder.embed_batch(
                        cluster_names_for_embed,
                    )
                    name_embed_map = {
                        n: list(e) for n, e in zip(
                            cluster_names_for_embed, embeddings,
                        )
                    }
                    vocab_candidates = discover_vocabulary_candidates(
                        clusters=clusters,
                        name_embeddings=name_embed_map,
                    )
                    # Resolve the workspace's domain_id for this
                    # vocabulary scope. Falls back to a workspace-
                    # scoped sentinel when no domain config maps the
                    # workspace explicitly — that way vocab still
                    # accumulates per workspace and we can promote
                    # to a shared domain later.
                    domain_id = (
                        os.environ.get("KB_DEFAULT_DOMAIN")
                        or f"workspace:{workspace_id}"
                    )
                    for cand in vocab_candidates:
                        await upsert_vocabulary(
                            conn,
                            domain_id=domain_id,
                            canonical_term=cand.canonical_term,
                            synonyms=cand.synonyms,
                            source="discovered",
                            confidence=cand.confidence,
                            n_docs_observed=cand.n_docs_observed,
                        )
            except Exception:  # noqa: BLE001
                # Vocabulary discovery is best-effort observability
                # — never fail the ingest chain over it.
                traceback.print_exc()

            # UPSERT inferred_schema_fields rows + check promotion.
            thresholds = PromotionThresholds.from_env()
            promotion_count = 0
            schema_entity_id: str | None = None
            for cluster in clusters:
                inferred_id = await upsert_inferred_schema_field(
                    conn,
                    workspace_id=str(workspace_id),
                    inferred_doc_type=doc_type,
                    canonical_name=cluster.canonical_name,
                    description=cluster.description,
                    value_type=cluster.value_type,
                    n_docs_observed=cluster.n_docs_observed,
                    prevalence=cluster.prevalence,
                    stability=cluster.stability,
                    value_type_confidence=cluster.value_type_confidence,
                )
                if should_promote(cluster, thresholds):
                    # Lazy-create schema_entity only when we need it.
                    if schema_entity_id is None:
                        _, schema_entity_id = await ensure_auto_schema_entity(
                            conn,
                            workspace_id=str(workspace_id),
                            doc_type=doc_type,
                        )
                    schema_field_id = await promote_field(
                        conn,
                        workspace_id=str(workspace_id),
                        schema_entity_id=schema_entity_id,
                        canonical_name=cluster.canonical_name,
                        description=cluster.description,
                        value_type=cluster.value_type,
                    )
                    await mark_inferred_field_promoted(
                        conn,
                        inferred_field_id=inferred_id,
                        promoted_schema_field_id=schema_field_id,
                    )
                    promotion_count += 1

            await transition_lifecycle(
                conn,
                workspace_id=str(workspace_id),
                file_id=file_id,
                to_state="units_extracting",
                event="fields_extracted",
                payload={
                    "doc_type": doc_type,
                    "field_count": n_proposed,
                    "n_clusters": len(clusters),
                    "promotions": promotion_count,
                    "model_id": (cls.model_id if cls else "identity"),
                },
            )

    # Phase 5c §5.12.3 #6: chain extract_atomic_units_file.
    try:
        await procrastinate_app.configure_task(
            name="extract_atomic_units_file"
        ).defer_async(file_id=file_id)
    except Exception:  # noqa: BLE001
        traceback.print_exc()


# ---------------------------------------------------------------------------
# Phase 5d — extract_kv_tables_file_impl (KV+Tables collapse)
# ---------------------------------------------------------------------------


async def extract_kv_tables_file_impl(file_id: str) -> None:
    """KV+Tables collapse — ONE LLM call covers L2b (classify+propose) AND
    L3 (atomic_units) for this file.

    Pipeline before this task: mentions_extracting (mentions written) →
    fields_extracting (this task) → entities_extracting (skipping the
    legacy 'units_extracting' slot).

    What this writes in a single transaction:
      - files.inferred_doc_type
      - proposed_fields  (from KV+Tables scalars)
      - inferred_schema_fields + schema_fields (via cluster + promote)
      - domain_vocabulary (discovery, best-effort)
      - schema_entities: bootstrapped doc_root + sub_entity types
        (BankStatement contains Transaction, etc.)
      - extracted_entities: child rows DIRECTLY (with unit_type,
        rarity_score, fields jsonb, source positions). parent_entity_id
        stays NULL — set later by extract_schema_entities_file lineage
        pass against the doc_root entity that LLM extraction creates.

    Net LLM-call savings: 5 → 2 per doc (mentions + KV+Tables).

    Per-stage idempotency: returns if already past fields_extracting.
    """
    from kb.config import get_settings
    from kb.domain.conflicts import apply_source_authority_from_config
    from kb.domain.extracted_entities import (
        delete_extracted_entities_children_for_file,
        insert_extracted_entity,
        read_existing_entity_fields_for_unit_type,
        update_entity_rarity,
    )
    from kb.domain.fields import (
        count_docs_of_doctype,
        delete_proposed_fields_for_file,
        insert_proposed_field,
        mark_inferred_field_promoted,
        read_proposed_fields_for_doctype,
        update_file_inferred_doc_type,
        upsert_inferred_schema_field,
    )
    from kb.extraction.anomaly import score_units_jit
    from kb.extraction.entities import build_chunk_indexed_text
    from kb.extraction.kv_tables import (
        KVTablesExtractionError,
        make_kv_tables_extractor,
    )
    from kb.extraction.promotion import (
        PromotionThresholds,
        cluster_fields_for_doctype,
        ensure_auto_schema_entity,
        ensure_contains_relationship,
        ensure_sub_entity_type,
        promote_field,
        should_promote,
    )

    settings = get_settings()
    db_url = settings.database_url

    # Phase 1: state check + read chunks for the LLM input.
    chunks: list[tuple[str, str]] = []
    workspace_id_str = ""
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

            # KV+Tables runs in the fields_extracting slot. Anything past
            # it (units_extracting / entities_extracting / ready / failed
            # / deleted) means an earlier ingest already processed this
            # file — no-op.
            if lifecycle_state in (
                "units_extracting",
                "entities_extracting",
                "ready",
                "failed",
                "deleted",
            ):
                return
            if lifecycle_state != "fields_extracting":
                return

            workspace_id_str = str(workspace_id)
            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)",
                (workspace_id_str,),
            )

            # Read chunks paired with their contextual text. The LLM
            # sees `contextual_text` (carries Anthropic-style context
            # prefixes). Storage targets:
            #   - `proposed_fields.source_chunk_id`  → chunks(id)
            #     (FK added in migration 0032)
            #   - `extracted_entities.source_chunk_id` → contextual_chunks(id)
            #     (FK added in migration 0037 — different target than
            #     the legacy atomic_units path took)
            cur = await conn.execute(
                "SELECT c.id::text, cc.id::text, cc.contextual_text "
                "FROM contextual_chunks cc "
                "JOIN chunks c ON c.id = cc.chunk_id "
                "WHERE cc.file_id = %s "
                "ORDER BY c.chunk_index ASC",
                (file_id,),
            )
            chunk_rows = await cur.fetchall()
            # chunks list shape: [(chunks.id, contextual_text), ...] —
            # chunks.id is used for proposed_fields.source_chunk_id;
            # cc_ids parallel list is used for extracted_entities.
            chunks = [(r[0], r[2] or "") for r in chunk_rows]
            cc_ids: list[str] = [r[1] for r in chunk_rows]

            # Existing sub_entity type names in this workspace — passed
            # as hints so the LLM reuses table names across docs of the
            # same kind (e.g. "transactions" not "txns").
            cur = await conn.execute(
                "SELECT DISTINCT name FROM schema_entities "
                "WHERE workspace_id = %s AND kind = 'sub_entity' "
                "AND lifecycle_state = 'active'",
                (workspace_id_str,),
            )
            existing_hints: list[str] = [r[0] for r in await cur.fetchall()]

    chunk_indexed_text = build_chunk_indexed_text(chunks) if chunks else ""

    # Phase 2: KV+Tables LLM call (single round-trip).
    extractor = make_kv_tables_extractor()
    try:
        payload = await extractor.extract(
            chunk_indexed_text=chunk_indexed_text,
            doc_type_hint=None,
            existing_sub_entity_hints=existing_hints or None,
        )
    except KVTablesExtractionError:
        # Don't block the chain on extractor failure — log + advance
        # with empty payload. Admin re-extract endpoint can rerun later.
        traceback.print_exc()
        from kb.extraction.kv_tables import KVTablesPayload
        payload = KVTablesPayload(model_id="identity")

    doc_type = payload.doc_type or "unknown"

    # Phase 3: atomic write across files / proposed_fields /
    # inferred_schema_fields / schema_fields / atomic_units.
    async with open_connection(db_url) as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)",
                (workspace_id_str,),
            )

            await update_file_inferred_doc_type(
                conn, file_id=file_id, doc_type=doc_type,
            )

            # WA-6 / B2 — source_authority side-effect, ungated.
            try:
                await apply_source_authority_from_config(
                    conn,
                    file_id=file_id,
                    workspace_id=workspace_id_str,
                    inferred_doc_type=doc_type,
                )
            except Exception:  # noqa: BLE001
                traceback.print_exc()

            # ---- Scalars → proposed_fields ----
            await delete_proposed_fields_for_file(conn, file_id=file_id)

            n_proposed = 0
            for sc in payload.scalars:
                src_chunk_id = None
                if (
                    sc.source_chunk is not None
                    and 0 <= sc.source_chunk < len(chunks)
                ):
                    src_chunk_id = chunks[sc.source_chunk][0]
                await insert_proposed_field(
                    conn,
                    file_id=file_id,
                    workspace_id=workspace_id_str,
                    inferred_doc_type=doc_type,
                    field_name=sc.name,
                    field_description=sc.description,
                    value_text=sc.value,
                    value_type=sc.value_type,
                    is_pii=sc.is_pii,
                    model_id=payload.model_id,
                    source_chunk_id=src_chunk_id,
                    source_char_start=None,
                    source_char_end=None,
                )
                n_proposed += 1

            # ---- Cluster + vocabulary + promote (unchanged from L2b) ----
            proposed_per_doc = await read_proposed_fields_for_doctype(
                conn, workspace_id=workspace_id_str, inferred_doc_type=doc_type,
            )
            n_docs = await count_docs_of_doctype(
                conn, workspace_id=workspace_id_str, inferred_doc_type=doc_type,
            )
            clusters = cluster_fields_for_doctype(
                proposed_per_doc=proposed_per_doc,
                total_docs_of_type=n_docs,
            )

            # Vocabulary discovery (best-effort).
            try:
                from kb.domain.vocabulary import upsert_vocabulary
                from kb.extraction.vocabulary import (
                    discover_vocabulary_candidates,
                )

                cluster_names_for_embed = [c.canonical_name for c in clusters]
                if cluster_names_for_embed:
                    embedder = make_embedder()
                    embeddings = await embedder.embed_batch(
                        cluster_names_for_embed,
                    )
                    name_embed_map = {
                        n: list(e) for n, e in zip(
                            cluster_names_for_embed, embeddings,
                        )
                    }
                    vocab_candidates = discover_vocabulary_candidates(
                        clusters=clusters,
                        name_embeddings=name_embed_map,
                    )
                    domain_id = (
                        os.environ.get("KB_DEFAULT_DOMAIN")
                        or f"workspace:{workspace_id}"
                    )
                    for cand in vocab_candidates:
                        await upsert_vocabulary(
                            conn,
                            domain_id=domain_id,
                            canonical_term=cand.canonical_term,
                            synonyms=cand.synonyms,
                            source="discovered",
                            confidence=cand.confidence,
                            n_docs_observed=cand.n_docs_observed,
                        )
            except Exception:  # noqa: BLE001
                traceback.print_exc()

            # UPSERT inferred_schema_fields + promote crossed clusters.
            thresholds = PromotionThresholds.from_env()
            promotion_count = 0
            schema_entity_id: str | None = None
            for cluster in clusters:
                inferred_id = await upsert_inferred_schema_field(
                    conn,
                    workspace_id=workspace_id_str,
                    inferred_doc_type=doc_type,
                    canonical_name=cluster.canonical_name,
                    description=cluster.description,
                    value_type=cluster.value_type,
                    n_docs_observed=cluster.n_docs_observed,
                    prevalence=cluster.prevalence,
                    stability=cluster.stability,
                    value_type_confidence=cluster.value_type_confidence,
                )
                if should_promote(cluster, thresholds):
                    if schema_entity_id is None:
                        _, schema_entity_id = await ensure_auto_schema_entity(
                            conn,
                            workspace_id=workspace_id_str,
                            doc_type=doc_type,
                        )
                    schema_field_id = await promote_field(
                        conn,
                        workspace_id=workspace_id_str,
                        schema_entity_id=schema_entity_id,
                        canonical_name=cluster.canonical_name,
                        description=cluster.description,
                        value_type=cluster.value_type,
                    )
                    await mark_inferred_field_promoted(
                        conn,
                        inferred_field_id=inferred_id,
                        promoted_schema_field_id=schema_field_id,
                    )
                    promotion_count += 1

            # ---- Tables → extracted_entities children (direct write) ----
            # Pre-collapse: rows went to atomic_units first, then
            # extract_schema_entities_file_impl promoted them to
            # extracted_entities children. We now skip the
            # atomic_units intermediate and write the canonical
            # storage shape directly, removing a whole layer of
            # duplication and the related chunks.id ↔
            # contextual_chunks.id FK-translation step.
            #
            # parent_entity_id stays NULL here; the lineage pass in
            # extract_schema_entities_file_impl walks
            # schema_relationships(kind='contains') to find each child's
            # parent doc_root instance for this file and UPDATEs the FK.

            # Bootstrap schema_entities types FIRST (the children need
            # their schema_entity_id FK to point at a real sub_entity
            # type row). doc_root_entity_type_id is the BankStatement /
            # MSA / etc. type row id we attach children's
            # parent_type_id to.
            doc_root_schema_id: str | None = None
            doc_root_entity_type_id: str | None = None
            sub_entity_type_by_unit: dict[str, str] = {}
            if payload.tables and doc_type and doc_type != "unknown":
                doc_root_schema_id, doc_root_entity_type_id = (
                    await ensure_auto_schema_entity(
                        conn,
                        workspace_id=workspace_id_str,
                        doc_type=doc_type,
                    )
                )
                for tbl in payload.tables:
                    sub_id = await ensure_sub_entity_type(
                        conn,
                        workspace_id=workspace_id_str,
                        schema_id=doc_root_schema_id,
                        parent_type_id=doc_root_entity_type_id,
                        unit_type=tbl.name,
                    )
                    sub_entity_type_by_unit[tbl.name] = sub_id
                    await ensure_contains_relationship(
                        conn,
                        workspace_id=workspace_id_str,
                        schema_id=doc_root_schema_id,
                        parent_entity_id=doc_root_entity_type_id,
                        child_entity_id=sub_id,
                    )

            await delete_extracted_entities_children_for_file(
                conn, file_id=file_id,
            )

            inserted_ids: list[str] = []
            inserted_params: list[dict] = []
            inserted_types: list[str] = []
            total_rows = 0
            for tbl in payload.tables:
                sub_entity_id = sub_entity_type_by_unit.get(tbl.name)
                if sub_entity_id is None:
                    # Unknown doc_type → no bootstrap happened above.
                    # Skip child writes; the rows have nowhere to attach.
                    continue
                for row in tbl.rows:
                    src_cc_id: str | None = None
                    if (
                        row.source_chunk is not None
                        and 0 <= row.source_chunk < len(cc_ids)
                    ):
                        src_cc_id = cc_ids[row.source_chunk]
                    eid = await insert_extracted_entity(
                        conn,
                        schema_entity_id=sub_entity_id,
                        file_id=file_id,
                        workspace_id=workspace_id_str,
                        fields=row.values,
                        citations={},
                        model_id=payload.model_id,
                        rarity_score=None,  # filled by JIT pass below
                        unit_type=tbl.name,
                        source_chunk_id=src_cc_id,
                        source_char_start=row.source_char_start,
                        source_char_end=row.source_char_end,
                    )
                    inserted_ids.append(eid)
                    inserted_params.append(row.values)
                    inserted_types.append(tbl.name)
                    total_rows += 1

            # JIT anomaly scoring — score per unit_type so cohorts match
            # (transactions only compared to other transactions).
            distinct_unit_types = sorted(set(inserted_types))
            for ut in distinct_unit_types:
                idx_for_type = [
                    i for i, t in enumerate(inserted_types) if t == ut
                ]
                params_for_type = [inserted_params[i] for i in idx_for_type]
                ids_for_type = [inserted_ids[i] for i in idx_for_type]
                historical = await read_existing_entity_fields_for_unit_type(
                    conn, workspace_id=workspace_id_str, unit_type=ut,
                )
                scores = score_units_jit(params_for_type, historical)
                for eid, sc in zip(ids_for_type, scores, strict=True):
                    if sc is not None:
                        await update_entity_rarity(
                            conn, entity_id=eid, rarity_score=float(sc),
                        )

            # Lifecycle: jump straight to entities_extracting, skipping
            # the legacy units_extracting slot (the L3 plugin call no
            # longer happens — KV+Tables ate that work).
            await transition_lifecycle(
                conn,
                workspace_id=workspace_id_str,
                file_id=file_id,
                to_state="entities_extracting",
                event="kv_tables_extracted",
                payload={
                    "doc_type": doc_type,
                    "scalar_count": n_proposed,
                    "n_clusters": len(clusters),
                    "promotions": promotion_count,
                    "table_count": len(payload.tables),
                    "row_count": total_rows,
                    "unit_types": distinct_unit_types,
                    "model_id": payload.model_id,
                    "input_tokens": payload.input_token_count,
                    "output_tokens": payload.output_token_count,
                },
            )

    # Chain extract_schema_entities_file + extract_triples_file in
    # parallel — same downstream wiring as the legacy atomic_units task.
    try:
        await procrastinate_app.configure_task(
            name="extract_schema_entities_file"
        ).defer_async(file_id=file_id)
    except Exception:  # noqa: BLE001
        traceback.print_exc()

    try:
        await procrastinate_app.configure_task(
            name="extract_triples_file"
        ).defer_async(file_id=file_id)
    except Exception:  # noqa: BLE001
        traceback.print_exc()


# ---------------------------------------------------------------------------
# Phase 5c — extract_atomic_units_file_impl
# ---------------------------------------------------------------------------


async def extract_atomic_units_file_impl(file_id: str) -> None:
    """Dispatch a doc-type-aware plugin → write atomic_units rows → JIT
    anomaly scoring → advance lifecycle to `ready`.

    Per build_tracker §5.12.3 (10 locked decisions).
    """
    from kb.config import get_settings
    from kb.domain.atomic_units import (
        delete_atomic_units_for_file,
        insert_atomic_unit,
        read_existing_unit_parameters,
        update_atomic_unit_rarity,
    )
    from kb.extraction.anomaly import score_units_jit
    from kb.extraction.plugins import FileMeta, dispatch

    settings = get_settings()
    db_url = settings.database_url

    # Phase 1: state check + read file metadata + raw_pages.
    file_meta: FileMeta | None = None
    raw_pages: list[tuple[int, str, dict]] = []
    workspace_id_str = ""
    async with open_connection(db_url) as conn:
        async with conn.transaction():
            cur = await conn.execute(
                "SELECT workspace_id, lifecycle_state, mime_type, name, "
                "inferred_doc_type FROM files WHERE id = %s",
                (file_id,),
            )
            row = await cur.fetchone()
            if row is None:
                raise FileNotFoundError(file_id)
            workspace_id, lifecycle_state, mime_type, name, inferred_doc_type = row

            if lifecycle_state in ("ready", "failed", "deleted"):
                return
            if lifecycle_state != "units_extracting":
                return

            workspace_id_str = str(workspace_id)
            file_meta = FileMeta(
                file_id=file_id,
                workspace_id=workspace_id_str,
                mime_type=mime_type,
                inferred_doc_type=inferred_doc_type,
                name=name,
            )

            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)",
                (workspace_id_str,),
            )
            cur = await conn.execute(
                "SELECT page_number, text, layout_json FROM raw_pages "
                "WHERE file_id = %s ORDER BY page_number",
                (file_id,),
            )
            page_rows = await cur.fetchall()
            raw_pages = [(int(p[0]), p[1] or "", p[2] or {}) for p in page_rows]

    if file_meta is None:
        return

    plugin = dispatch(file_meta)
    units = []
    if plugin is not None:
        doc_text = "\n\n".join(p[1] for p in raw_pages if p[1])
        try:
            units = await plugin.extract(
                file_meta=file_meta, doc_text=doc_text, raw_pages=raw_pages,
            )
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            units = []

    # Pre-fetch chunks for source-position resolution. For row-style
    # units (xlsx) we already have parameters.row_index; for clause-style
    # units the resolver finds the summary text in the source chunk.
    unit_chunks: list[tuple[str, str]] = []
    if units:
        async with open_connection(db_url) as conn:
            cur = await conn.execute(
                "SELECT id::text, text FROM chunks WHERE file_id = %s "
                "ORDER BY chunk_index ASC",
                (file_id,),
            )
            unit_chunks = [(r[0], r[1] or "") for r in await cur.fetchall()]

    # Phase 2: atomic write — DELETE existing + INSERT new + JIT anomaly.
    async with open_connection(db_url) as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)",
                (workspace_id_str,),
            )
            await delete_atomic_units_for_file(conn, file_id=file_id)

            unit_type = plugin.UNIT_TYPE if plugin is not None else ""
            model_id_used = "identity" if not units else (
                "rows" if plugin is dispatch(file_meta) and plugin.UNIT_TYPE == "row"
                else "gemini-2.5-flash"
            )

            from kb.extraction.source_resolver import (
                resolve as resolve_source_position,
            )

            inserted_ids: list[str] = []
            inserted_params: list[dict] = []
            for u in units:
                # For clause-style units the LLM returns parameters.summary
                # — find which chunk's body contains it and store the
                # offset. For row-style xlsx units, parameters.row_index +
                # sheet_name already pinpoint the source so we leave the
                # offset columns null.
                src_chunk = src_start = src_end = None
                summary = (u.parameters or {}).get("summary")
                if isinstance(summary, str) and summary.strip():
                    for cid, ctext in unit_chunks:
                        pos = resolve_source_position(summary, ctext)
                        if pos:
                            src_chunk = cid
                            src_start = pos.char_start
                            src_end = pos.char_end
                            break
                uid = await insert_atomic_unit(
                    conn,
                    file_id=file_id,
                    workspace_id=workspace_id_str,
                    unit_type=u.unit_type,
                    parameters=u.parameters,
                    anchor_chunk_id=u.anchor_chunk_id,
                    rarity_score=None,
                    model_id=model_id_used,
                    source_chunk_id=src_chunk,
                    source_char_start=src_start,
                    source_char_end=src_end,
                )
                inserted_ids.append(uid)
                inserted_params.append(u.parameters)

            # JIT anomaly: read all units for this (workspace, unit_type)
            # AFTER insert, score new units, UPDATE their rarity_score.
            if units and unit_type:
                historical = await read_existing_unit_parameters(
                    conn, workspace_id=workspace_id_str, unit_type=unit_type,
                )
                scores = score_units_jit(inserted_params, historical)
                for uid, sc in zip(inserted_ids, scores, strict=True):
                    if sc is not None:
                        await update_atomic_unit_rarity(
                            conn, unit_id=uid, rarity_score=float(sc),
                        )

            # Phase 6 §5.13 #9: transition to entities_extracting (was 'ready'
            # in 5c; Phase 6 inserts schema-driven extraction before ready).
            await transition_lifecycle(
                conn,
                workspace_id=workspace_id_str,
                file_id=file_id,
                to_state="entities_extracting",
                event="atomic_units_extracted",
                payload={
                    "unit_type": unit_type or "none",
                    "unit_count": len(units),
                    "plugin": plugin.__class__.__name__ if plugin else "none",
                },
            )

    # Phase 6 §5.13 #1: chain extract_schema_entities_file in a SEPARATE tx.
    try:
        await procrastinate_app.configure_task(
            name="extract_schema_entities_file"
        ).defer_async(file_id=file_id)
    except Exception:  # noqa: BLE001
        traceback.print_exc()

    # B1 / WA-4: also defer extract_triples_file in parallel — triples
    # extraction is additive and doesn't depend on schema entities. It
    # reads contextual_chunks (Phase 3b) which are already in place by
    # the time atomic_units finished.
    try:
        await procrastinate_app.configure_task(
            name="extract_triples_file"
        ).defer_async(file_id=file_id)
    except Exception:  # noqa: BLE001
        traceback.print_exc()


# ---------------------------------------------------------------------------
# Phase 6 — extract_schema_entities_file_impl
# ---------------------------------------------------------------------------


async def extract_schema_entities_file_impl(file_id: str) -> None:
    """Run Gemini structured-output extraction per active schema_entity for
    the file's inferred_doc_type. Write extracted_entities rows with
    per-field citations + lineage_path. Advance lifecycle to `ready`.

    Per build_tracker §5.13 (13 locked decisions).

    Per-stage idempotency: returns immediately if already at `ready`.
    """
    import os

    from kb.config import get_settings
    from kb.domain.extracted_entities import (
        count_extracted_entities_for_file,
        delete_extracted_entities_for_file,
        insert_extracted_entity,
        read_active_schemas_for_doctype,
        read_contextual_chunks_for_extraction,
        read_schema_entities_with_fields,
        update_lineage,
    )
    from kb.extraction.entities import (
        SchemaEntityRequest,
        SchemaExtractionError,
        build_chunk_indexed_text,
        make_schema_driven_extractor,
    )
    from kb.extraction.lineage import assign_lineage_for_entity

    settings = get_settings()
    db_url = settings.database_url

    # Phase 1: state check + read schemas + chunks.
    inferred_doc_type: str | None = None
    workspace_id_str = ""
    schemas_with_entities: list[dict] = []
    chunks: list[tuple[str, str]] = []

    async with open_connection(db_url) as conn:
        async with conn.transaction():
            cur = await conn.execute(
                "SELECT workspace_id, lifecycle_state, inferred_doc_type "
                "FROM files WHERE id = %s",
                (file_id,),
            )
            row = await cur.fetchone()
            if row is None:
                raise FileNotFoundError(file_id)
            workspace_id, lifecycle_state, inferred_doc_type = row

            if lifecycle_state in ("ready", "failed", "deleted"):
                return
            if lifecycle_state != "entities_extracting":
                return

            workspace_id_str = str(workspace_id)
            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)",
                (workspace_id_str,),
            )

            # No matching schema? Decision #4: no-op advance to ready.
            if not inferred_doc_type:
                inferred_doc_type = "unknown"

            # Nested-entities defensive bootstrap. Normally this work
            # already happened inside extract_kv_tables_file_impl —
            # KV+Tables creates the doc_root entity_type, the sub_entity
            # types for each table, the `contains` relationships, AND
            # writes the child extracted_entities directly. We run the
            # bootstrap again here (idempotent) as a safety net for
            # the rare path where a file lands in entities_extracting
            # without having gone through KV+Tables (e.g. a manual
            # lifecycle nudge, or a future ingestion mode).
            from kb.extraction.promotion import (
                ensure_auto_schema_entity, ensure_contains_relationship,
                ensure_sub_entity_type,
            )

            schema_id_for_doctype: str | None = None
            doc_root_entity_id: str | None = None
            if inferred_doc_type and inferred_doc_type != "unknown":
                schema_id_for_doctype, doc_root_entity_id = (
                    await ensure_auto_schema_entity(
                        conn, workspace_id=workspace_id_str,
                        doc_type=inferred_doc_type,
                    )
                )

            # Discover sub_entity types from the extracted_entities
            # children that extract_kv_tables_file_impl already wrote.
            # This replaces the legacy `read_atomic_units_for_file`
            # path — same purpose, new source of truth.
            cur = await conn.execute(
                "SELECT DISTINCT unit_type FROM extracted_entities "
                "WHERE file_id = %s AND unit_type IS NOT NULL",
                (file_id,),
            )
            unit_types_in_file = sorted({r[0] for r in await cur.fetchall()})
            sub_entity_type_by_unit: dict[str, str] = {}
            if (
                doc_root_entity_id is not None
                and schema_id_for_doctype is not None
                and unit_types_in_file
            ):
                for unit_type in unit_types_in_file:
                    sub_id = await ensure_sub_entity_type(
                        conn,
                        workspace_id=workspace_id_str,
                        schema_id=schema_id_for_doctype,
                        parent_type_id=doc_root_entity_id,
                        unit_type=unit_type,
                    )
                    await ensure_contains_relationship(
                        conn,
                        workspace_id=workspace_id_str,
                        schema_id=schema_id_for_doctype,
                        parent_entity_id=doc_root_entity_id,
                        child_entity_id=sub_id,
                    )
                    sub_entity_type_by_unit[unit_type] = sub_id

            active_schemas = await read_active_schemas_for_doctype(
                conn,
                workspace_id=workspace_id_str,
                inferred_doc_type=inferred_doc_type,
            )
            for schema_id, schema_name in active_schemas:
                entities = await read_schema_entities_with_fields(
                    conn, schema_id=schema_id,
                )
                for e in entities:
                    e["schema_id"] = schema_id
                    e["schema_name"] = schema_name
                    schemas_with_entities.append(e)

            if schemas_with_entities:
                chunks = await read_contextual_chunks_for_extraction(
                    conn, file_id=file_id,
                )

    # Phase 2: LLM calls (parallel under semaphore).
    extractor = make_schema_driven_extractor()
    concurrency = int(os.environ.get("KB_ENTITY_CONCURRENCY") or 4)
    semaphore = asyncio.Semaphore(concurrency)
    chunk_indexed_text = build_chunk_indexed_text(chunks) if chunks else ""

    async def _extract_one(entity_def: dict):
        async with semaphore:
            request = SchemaEntityRequest(
                schema_entity_name=entity_def["entity_name"],
                schema_entity_description=entity_def.get("entity_description") or "",
                field_defs=entity_def["field_defs"],
                chunk_indexed_text=chunk_indexed_text,
            )
            try:
                result = await extractor.extract(request=request)
                return entity_def, result
            except SchemaExtractionError:
                traceback.print_exc()
                return entity_def, None

    results: list[tuple[dict, Any]] = []
    if schemas_with_entities and chunks:
        results = await asyncio.gather(*(
            _extract_one(ed) for ed in schemas_with_entities
        ))

    # Phase 3: atomic write — DELETE existing + INSERT new + lineage in one tx.
    total_inserted = 0
    model_id_used = "identity"
    async with open_connection(db_url) as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)",
                (workspace_id_str,),
            )
            await delete_extracted_entities_for_file(conn, file_id=file_id)

            # PASS 1: insert LLM-extracted parent (doc_root) entities.
            # The LLM only runs against schema_entities that have
            # promoted schema_fields — in practice, this is the parent
            # type (e.g. BankStatement) carrying scalars like
            # account_holder / opening_balance. Sub_entity types
            # created in Phase 1.5 have no schema_fields yet, so the
            # LLM is never invoked for them; their instances come from
            # PASS 1.5 below.
            inserted: list[tuple[str, str]] = []  # (entity_id, schema_entity_id)
            for entity_def, result in results:
                if result is None or not result.instances:
                    continue
                model_id_used = result.model_id
                schema_entity_id = entity_def["entity_id"]
                for instance in result.instances:
                    # Resolve chunk_index → contextual_chunk_id.
                    citation_map: dict[str, str] = {}
                    for field_name, chunk_index in (instance.citations or {}).items():
                        if 0 <= chunk_index < len(chunks):
                            citation_map[field_name] = chunks[chunk_index][0]
                    eid = await insert_extracted_entity(
                        conn,
                        schema_entity_id=schema_entity_id,
                        file_id=file_id,
                        workspace_id=workspace_id_str,
                        fields=instance.fields,
                        citations=citation_map,
                        model_id=result.model_id,
                    )
                    inserted.append((eid, schema_entity_id))
                    total_inserted += 1

            # Pick up children KV+Tables already wrote so the lineage
            # pass (PASS 2/3) sees them and assigns parent_entity_id +
            # lineage_path. We do NOT re-insert — those rows are
            # already in place from extract_kv_tables_file_impl. We
            # just need their (eid, schema_entity_id) pairs in the
            # `inserted` list.
            cur = await conn.execute(
                "SELECT id::text, schema_entity_id::text "
                "FROM extracted_entities "
                "WHERE file_id = %s AND unit_type IS NOT NULL",
                (file_id,),
            )
            for eid, se_id in await cur.fetchall():
                inserted.append((eid, se_id))
                total_inserted += 1

            # PASS 2: topologically sort `inserted` so parents come BEFORE
            # children, then assign lineage in that order. Otherwise Clause
            # (whose parent is Doc) could be processed before Doc when
            # schema_entity created_at timestamps tie — Doc's lineage_path
            # would still be NULL when Clause queries it.
            cur = await conn.execute(
                "SELECT to_entity_id::text, from_entity_id::text "
                "FROM schema_relationships "
                "WHERE workspace_id = %s AND kind = 'contains' "
                "AND lifecycle_state = 'active'",
                (workspace_id_str,),
            )
            parent_se_map: dict[str, str] = {
                child: parent for child, parent in await cur.fetchall()
            }

            def _depth(seid: str, memo: dict[str, int]) -> int:
                if seid in memo:
                    return memo[seid]
                if seid not in parent_se_map:
                    memo[seid] = 0
                    return 0
                memo[seid] = 1 + _depth(parent_se_map[seid], memo)
                return memo[seid]

            depth_memo: dict[str, int] = {}
            inserted.sort(key=lambda t: _depth(t[1], depth_memo))

            # PASS 3: assign lineage (parents now guaranteed to have lineage_path).
            for entity_id, schema_entity_id in inserted:
                parent_id, lineage_path = await assign_lineage_for_entity(
                    conn,
                    workspace_id=workspace_id_str,
                    file_id=file_id,
                    entity_id=entity_id,
                    schema_entity_id=schema_entity_id,
                )
                await update_lineage(
                    conn,
                    entity_id=entity_id,
                    parent_entity_id=parent_id,
                    lineage_path=lineage_path,
                )

            # Phase 7 §5.14 #1: transition to identity_resolving (was 'ready'
            # in Phase 6; Phase 7 resolves mentions → entities before ready).
            await transition_lifecycle(
                conn,
                workspace_id=workspace_id_str,
                file_id=file_id,
                to_state="identity_resolving",
                event="schema_entities_extracted",
                payload={
                    "entity_count": total_inserted,
                    "schema_entity_calls": len(results),
                    "inferred_doc_type": inferred_doc_type,
                    "model_id": model_id_used,
                },
            )

    # Phase 7 §5.14 #1: chain resolve_identities_file in a SEPARATE tx.
    try:
        await procrastinate_app.configure_task(
            name="resolve_identities_file"
        ).defer_async(file_id=file_id)
    except Exception:  # noqa: BLE001
        traceback.print_exc()


# ---------------------------------------------------------------------------
# Phase 7 — resolve_identities_file_impl
# ---------------------------------------------------------------------------


async def resolve_identities_file_impl(file_id: str) -> None:
    """For every mention in the file, resolve to a canonical entity in the
    workspace via the 4-stage pipeline (deterministic → embedding → llm_judge
    → new). Advance lifecycle `identity_resolving → ready`.
    """
    from kb.config import get_settings
    from kb.domain.entities import (
        delete_mention_to_entity_for_file,
        find_entity_by_embedding,
        find_entity_deterministic,
        increment_mention_count,
        insert_entity,
        insert_mention_to_entity,
        read_mentions_for_file,
    )
    from kb.embeddings import make_embedder
    from kb.identity.judge import make_identity_judge
    from kb.identity.resolve import (
        EMBEDDING_HIGH_THRESHOLD,
        EMBEDDING_LOW_THRESHOLD,
        is_noise_mention_type,
    )

    settings = get_settings()
    db_url = settings.database_url

    # Phase 1: state check + read mentions.
    workspace_id_str = ""
    mentions: list[tuple[str, str, str]] = []
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

            if lifecycle_state in ("ready", "failed", "deleted"):
                return
            if lifecycle_state != "identity_resolving":
                return

            workspace_id_str = str(workspace_id)
            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)",
                (workspace_id_str,),
            )
            mentions = await read_mentions_for_file(conn, file_id=file_id)

    # Phase 2: embed all mention texts upfront (one batched call).
    embedder = make_embedder()
    judge = make_identity_judge()

    mention_embeddings: dict[str, list[float]] = {}
    if mentions:
        try:
            results = await embedder.embed_batch([m[1] for m in mentions])
            for (mid, _, _), emb in zip(mentions, results, strict=True):
                mention_embeddings[mid] = list(emb.vector)
        except Exception:
            traceback.print_exc()
            # Fall through with no embeddings → all resolve as new entities.

    # Phase 3: atomic resolution + insert links in one tx.
    async with open_connection(db_url) as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)",
                (workspace_id_str,),
            )
            await delete_mention_to_entity_for_file(conn, file_id=file_id)

            method_counts: dict[str, int] = {
                "deterministic": 0, "embedding": 0, "llm_judge": 0, "new": 0,
                "skipped_noise": 0,
            }

            for mention_id, mention_text, mention_type in mentions:
                resolved_entity_id: str | None = None
                resolution_method: str = "deterministic"
                confidence: float = 1.0
                mention_emb = mention_embeddings.get(mention_id)

                # Stage 0 — skip noise mention types (CARDINAL / QUANTITY /
                # DATE / MONEY / ORDINAL / PERCENT / TIME). NER routinely
                # flags numeric / temporal spans as entities, but they have
                # no canonical identity ("30 days" in doc A is not the
                # same entity as "30 days" in doc B). Leaving them out of
                # `entities` and `mention_to_entity` cleans up the doc-
                # detail entity accordion + reduces resolver noise. The
                # mention itself stays in `extracted_mentions` so the
                # LLM still sees it inside cited snippets.
                if is_noise_mention_type(mention_type):
                    method_counts["skipped_noise"] += 1
                    continue

                # Stage 1: deterministic
                resolved_entity_id = await find_entity_deterministic(
                    conn,
                    workspace_id=workspace_id_str,
                    name=mention_text,
                    entity_type=mention_type,
                )
                if resolved_entity_id:
                    method_counts["deterministic"] += 1
                    confidence = 1.0
                else:
                    # Stage 2 + 3: embedding + LLM judge
                    if mention_emb:
                        candidates = await find_entity_by_embedding(
                            conn,
                            workspace_id=workspace_id_str,
                            entity_type=mention_type,
                            embedding=mention_emb,
                            limit=1,
                        )
                        if candidates:
                            cand_id, cand_name, sim = candidates[0]
                            if sim >= EMBEDDING_HIGH_THRESHOLD:
                                resolved_entity_id = cand_id
                                resolution_method = "embedding"
                                confidence = sim
                                method_counts["embedding"] += 1
                            elif sim >= EMBEDDING_LOW_THRESHOLD:
                                # Stage 3: LLM judge
                                try:
                                    same = await judge.same_entity(
                                        text_a=mention_text, type_a=mention_type,
                                        text_b=cand_name, type_b=mention_type,
                                    )
                                except Exception:
                                    same = False
                                if same:
                                    resolved_entity_id = cand_id
                                    resolution_method = "llm_judge"
                                    confidence = sim
                                    method_counts["llm_judge"] += 1

                    # Stage 4: create new entity
                    if resolved_entity_id is None:
                        resolved_entity_id = await insert_entity(
                            conn,
                            workspace_id=workspace_id_str,
                            canonical_name=mention_text,
                            entity_type=mention_type,
                            embedding=mention_emb,
                        )
                        resolution_method = "deterministic"  # the create itself
                        confidence = 1.0
                        method_counts["new"] += 1

                await insert_mention_to_entity(
                    conn,
                    mention_id=mention_id,
                    entity_id=resolved_entity_id,
                    workspace_id=workspace_id_str,
                    confidence=confidence,
                    resolved_method=resolution_method,
                )
                await increment_mention_count(conn, entity_id=resolved_entity_id)

            await transition_lifecycle(
                conn,
                workspace_id=workspace_id_str,
                file_id=file_id,
                to_state="ready",
                event="identities_resolved",
                payload={
                    "mention_count": len(mentions),
                    **method_counts,
                },
            )

    # B1 / WA-4 + WA-5: chain post-ready graph layers in SEPARATE
    # transactions so a defer failure doesn't roll back the successful
    # identity resolution. Both are additive (no lifecycle gating).
    try:
        await procrastinate_app.configure_task(
            name="build_relationships_file"
        ).defer_async(file_id=file_id)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
    try:
        await procrastinate_app.configure_task(
            name="build_graph_file"
        ).defer_async(file_id=file_id)
    except Exception:  # noqa: BLE001
        traceback.print_exc()


# ---------------------------------------------------------------------------
# WA-3 / Design 3 — detect_doc_chain_file_impl
# ---------------------------------------------------------------------------


async def detect_doc_chain_file_impl(file_id: str) -> None:
    """Per-file doc-chain detection (Design 3 §"Pipeline integration").

    Runs as an **additive** post-parse task — does NOT gate the existing
    parse → chunk → … chain. parse_file_impl defers BOTH chunk_file and
    detect_doc_chain_file; they run in parallel, doc-chain writes are
    side-effects on doc_chains / doc_chain_members. No lifecycle state
    transition (the new `doc_chaining` state is in the CHECK constraint
    for forward-compat if Wave B switches to a gating model).

    Idempotency: if a chain membership row already exists for this file,
    skip. Otherwise run detect_chain() over the workspace's prior files
    and upsert chain + member rows.
    """
    from kb.config import get_settings
    from kb.domain.doc_chains import (
        add_member,
        find_chain_for_doc,
        set_current_version,
        upsert_chain,
    )
    from kb.extraction.doc_chains import (
        DetectionInput,
        SiblingFile,
        detect_chain,
    )

    settings = get_settings()
    db_url = settings.database_url

    async with open_connection(db_url) as conn:
        # Single transaction wraps the entire read+detect+write so chain
        # rows + lifecycle event commit atomically. Matches the existing
        # worker-impl pattern (see resolve_identities_file_impl etc.).
        async with conn.transaction():
            # Read file + workspace.
            cur = await conn.execute(
                "SELECT workspace_id, name, mime_type, inferred_doc_type, "
                "lifecycle_state FROM files WHERE id = %s",
                (file_id,),
            )
            row = await cur.fetchone()
            if row is None:
                raise FileNotFoundError(file_id)
            workspace_id, name, mime_type, inferred_doc_type, lifecycle_state = row
            workspace_id_str = str(workspace_id)

            # Bail if file is failed / deleted.
            if lifecycle_state in ("failed", "deleted"):
                return

            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)",
                (workspace_id_str,),
            )

            # Idempotency: already a chain member.
            existing_membership = await find_chain_for_doc(conn, doc_id=file_id)
            if existing_membership is not None:
                return

            # Build DetectionInput from raw_pages. First page text is the
            # title-text proxy for contracts + circulars; email headers live
            # in layout_json regardless of which page they came from.
            cur = await conn.execute(
                "SELECT text, layout_json FROM raw_pages "
                "WHERE file_id = %s ORDER BY page_number ASC LIMIT 1",
                (file_id,),
            )
            first_page_row = await cur.fetchone()
            title_text = first_page_row[0] if first_page_row else None
            layout = first_page_row[1] if first_page_row else {}

            # Email fields (mime message/rfc822 → email parser stores
            # headers in layout_json).
            email_message_id = None
            email_in_reply_to = None
            email_references: tuple[str, ...] = ()
            email_subject = None
            email_sender = None
            email_recipients: tuple[str, ...] = ()
            if isinstance(layout, dict):
                headers = layout.get("headers") or {}
                if isinstance(headers, dict):
                    email_message_id = headers.get("message_id")
                    email_in_reply_to = headers.get("in_reply_to")
                    refs = headers.get("references") or ""
                    if isinstance(refs, str) and refs:
                        email_references = tuple(refs.split())
                    elif isinstance(refs, list):
                        email_references = tuple(str(r) for r in refs)
                    email_subject = headers.get("subject")
                    email_sender = headers.get("from")
                    recipients = headers.get("to") or []
                    if isinstance(recipients, str):
                        email_recipients = (recipients,)
                    elif isinstance(recipients, list):
                        email_recipients = tuple(str(r) for r in recipients)

            # Siblings: other files in this workspace already parsed.
            # Cap at 200 most-recent for cost.
            cur = await conn.execute(
                "SELECT id::text, name, mime_type, inferred_doc_type "
                "FROM files WHERE workspace_id = %s AND id <> %s "
                "AND lifecycle_state NOT IN "
                "('queued', 'parsing', 'failed', 'deleted') "
                "ORDER BY created_at DESC LIMIT 200",
                (workspace_id, file_id),
            )
            sib_rows = await cur.fetchall()
            siblings: list[SiblingFile] = []
            for sib_id, sib_name, sib_mime, sib_doc_type in sib_rows:
                sib_cur = await conn.execute(
                    "SELECT text, layout_json FROM raw_pages "
                    "WHERE file_id = %s ORDER BY page_number ASC LIMIT 1",
                    (sib_id,),
                )
                sib_first_page = await sib_cur.fetchone()
                sib_title = sib_first_page[0] if sib_first_page else None
                sib_layout = (sib_first_page[1] if sib_first_page else {}) or {}
                sib_msg_id = None
                sib_subject = None
                sib_sender = None
                sib_recipients: tuple[str, ...] = ()
                sib_references: tuple[str, ...] = ()
                if isinstance(sib_layout, dict):
                    sib_headers = sib_layout.get("headers") or {}
                    if isinstance(sib_headers, dict):
                        sib_msg_id = sib_headers.get("message_id")
                        sib_subject = sib_headers.get("subject")
                        sib_sender = sib_headers.get("from")
                        sib_to = sib_headers.get("to") or []
                        if isinstance(sib_to, list):
                            sib_recipients = tuple(str(r) for r in sib_to)
                        elif isinstance(sib_to, str):
                            sib_recipients = (sib_to,)
                        srefs = sib_headers.get("references") or ""
                        if isinstance(srefs, str) and srefs:
                            sib_references = tuple(srefs.split())
                        elif isinstance(srefs, list):
                            sib_references = tuple(str(r) for r in srefs)
                siblings.append(SiblingFile(
                    file_id=sib_id,
                    name=sib_name,
                    mime_type=sib_mime,
                    inferred_doc_type=sib_doc_type,
                    title_text=sib_title,
                    email_message_id=sib_msg_id,
                    email_subject=sib_subject,
                    email_sender=sib_sender,
                    email_recipients=sib_recipients,
                    email_references=sib_references,
                ))

            # Run the detector chain.
            det_input = DetectionInput(
                file_id=file_id,
                name=name,
                mime_type=mime_type,
                inferred_doc_type=inferred_doc_type,
                title_text=title_text,
                email_message_id=email_message_id,
                email_in_reply_to=email_in_reply_to,
                email_references=email_references,
                email_subject=email_subject,
                email_sender=email_sender,
                email_recipients=email_recipients,
                siblings=tuple(siblings),
            )
            candidate = detect_chain(det_input)

            # Additive: from_state == to_state == file's current
            # lifecycle_state so file_lifecycle stays a faithful audit
            # trail without claiming a state transition that didn't happen.
            current_state = str(lifecycle_state)
            if candidate is None:
                await record_lifecycle_event(
                    conn,
                    workspace_id=workspace_id_str,
                    file_id=file_id,
                    from_state=current_state,
                    to_state=current_state,
                    event="doc_chain_detected",
                    payload={"matched": False},
                )
                return

            # Find-or-create the chain.
            chain_id = await upsert_chain(
                conn,
                workspace_id=workspace_id_str,
                chain_type=candidate.chain_type,
                title=candidate.title,
                chain_key=candidate.chain_key,
                detection_confidence=candidate.confidence,
                current_version_id=file_id,
            )
            inserted = await add_member(
                conn,
                chain_id=chain_id,
                doc_id=file_id,
                workspace_id=workspace_id_str,
                version_index=candidate.version_index,
                role=candidate.role,
                parent_doc_id=candidate.parent_doc_id,
            )
            # Ensure each sibling member referenced by the detector is
            # in the chain (e.g., the first email needs an "original"
            # row even though it wasn't added when first parsed).
            for sib_id in candidate.sibling_member_ids:
                cur = await conn.execute(
                    "SELECT 1 FROM doc_chain_members "
                    "WHERE chain_id = %s AND doc_id = %s LIMIT 1",
                    (chain_id, sib_id),
                )
                exists = await cur.fetchone()
                if exists is None:
                    await add_member(
                        conn,
                        chain_id=chain_id,
                        doc_id=sib_id,
                        workspace_id=workspace_id_str,
                        version_index=0,
                        role="original",
                    )
            # Promote the new file as current_version for amendments /
            # revisions (newer supersedes).
            if candidate.role in (
                "amendment", "revision", "side_letter", "corrigendum",
            ):
                await set_current_version(
                    conn, chain_id=chain_id, current_version_id=file_id,
                )

            await record_lifecycle_event(
                conn,
                workspace_id=workspace_id_str,
                file_id=file_id,
                from_state=current_state,
                to_state=current_state,
                event="doc_chain_detected",
                payload={
                    "matched": True,
                    "chain_id": chain_id,
                    "chain_type": candidate.chain_type,
                    "role": candidate.role,
                    "confidence": candidate.confidence,
                    "inserted_member": inserted,
                },
            )


# ---------------------------------------------------------------------------
# B1 / WA-4 — extract_triples_file_impl (arch §5 stage 13)
# ---------------------------------------------------------------------------


async def extract_triples_file_impl(file_id: str) -> None:
    """Per-file open-triple extraction (architecture §5 stage 13).

    Runs as an additive post-L3 task. Calls the configured extractor
    (Identity / Gemini / Anthropic) on every contextual chunk for the
    file, INSERTs extracted_triples rows. Single transaction wraps the
    reads + writes + lifecycle event.
    """
    from kb.config import get_settings
    from kb.domain.triples import insert_triples_batch
    from kb.extraction.triples import (
        TripleExtractionError,
        make_triple_extractor,
    )

    settings = get_settings()
    db_url = settings.database_url
    extractor = make_triple_extractor()

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
            workspace_id_str = str(workspace_id)
            if lifecycle_state in ("failed", "deleted"):
                return

            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)",
                (workspace_id_str,),
            )

            # Read this file's contextual chunks (cc_id + cc_text + source
            # chunk_id + source chunk_text). Source pair powers the
            # citation-position resolver.
            cur = await conn.execute(
                "SELECT cc.id::text, cc.contextual_text, "
                "       c.id::text, c.text "
                "FROM contextual_chunks cc "
                "JOIN chunks c ON c.id = cc.chunk_id "
                "WHERE cc.file_id = %s "
                "ORDER BY c.chunk_index ASC",
                (file_id,),
            )
            chunk_rows = await cur.fetchall()
            if not chunk_rows:
                # No contextual chunks → no triples. Emit event and return.
                await record_lifecycle_event(
                    conn,
                    workspace_id=workspace_id_str,
                    file_id=file_id,
                    from_state=str(lifecycle_state),
                    to_state=str(lifecycle_state),
                    event="triples_extracted",
                    payload={"triple_count": 0, "model_id": extractor.model_id},
                )
                return

            from kb.extraction.source_resolver import (
                resolve as resolve_source_position,
            )

            total_triples = 0
            for _cc_id, cc_text, src_chunk_id, src_chunk_text in chunk_rows:
                if not cc_text:
                    continue
                try:
                    result = await extractor.extract(chunk_text=cc_text)
                except TripleExtractionError:
                    traceback.print_exc()
                    continue  # advisory — skip this chunk, keep going
                if not result.triples:
                    continue
                triples_to_insert: list[tuple] = []
                for t in result.triples:
                    # Resolve subject + object positions in the ORIGINAL
                    # chunk text. None when the snippet only appears in
                    # the contextual prefix (UI shows "no source loc").
                    s_pos = resolve_source_position(t.subject, src_chunk_text or "")
                    o_pos = resolve_source_position(t.object, src_chunk_text or "")
                    triples_to_insert.append((
                        t.subject, t.predicate, t.object, t.confidence,
                        src_chunk_id,
                        s_pos.char_start if s_pos else None,
                        s_pos.char_end if s_pos else None,
                        o_pos.char_start if o_pos else None,
                        o_pos.char_end if o_pos else None,
                    ))
                inserted_ids = await insert_triples_batch(
                    conn,
                    workspace_id=workspace_id_str,
                    file_id=file_id,
                    model_id=extractor.model_id,
                    triples=triples_to_insert,
                )
                total_triples += len(inserted_ids)

            await record_lifecycle_event(
                conn,
                workspace_id=workspace_id_str,
                file_id=file_id,
                from_state=str(lifecycle_state),
                to_state=str(lifecycle_state),
                event="triples_extracted",
                payload={
                    "triple_count": total_triples,
                    "model_id": extractor.model_id,
                    "chunks_processed": len(chunk_rows),
                },
            )


# ---------------------------------------------------------------------------
# B1 / WA-4 — build_relationships_file_impl (arch §5 stage 16)
# ---------------------------------------------------------------------------


async def build_relationships_file_impl(file_id: str) -> None:
    """Resolve this file's extracted_triples → relationships rows.

    Runs after identity_resolving → ready (entities exist in the
    workspace). Reads the file's triples + uses Phase 7's deterministic
    entity lookup. Single transaction wraps everything.
    """
    from kb.config import get_settings
    from kb.domain.entities import find_entity_deterministic
    from kb.domain.relationships import add_evidence, upsert_relationship
    from kb.domain.triples import read_triples_for_file
    from kb.extraction.relationships_resolver import resolve_triples

    settings = get_settings()
    db_url = settings.database_url

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
            workspace_id_str = str(workspace_id)
            if lifecycle_state in ("failed", "deleted"):
                return

            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)",
                (workspace_id_str,),
            )

            triples = await read_triples_for_file(conn, file_id=file_id)
            if not triples:
                await record_lifecycle_event(
                    conn,
                    workspace_id=workspace_id_str,
                    file_id=file_id,
                    from_state=str(lifecycle_state),
                    to_state=str(lifecycle_state),
                    event="relationships_built",
                    payload={"relationship_count": 0, "triple_count": 0},
                )
                return

            # Wave A lookup: try ANY entity type (None) so the resolver
            # doesn't need to know the mention_type. Phase 7's
            # find_entity_deterministic accepts entity_type=None.
            async def lookup(workspace: str, text: str) -> str | None:
                # The Phase 7 helper requires entity_type — try the most
                # common types in priority order (heuristic Wave A).
                for et in ("ORG", "PERSON", "GPE", "FAC", "PRODUCT", "EVENT"):
                    res = await find_entity_deterministic(
                        conn,
                        workspace_id=workspace,
                        name=text,
                        entity_type=et,
                    )
                    if res:
                        return res
                return None

            resolved = await resolve_triples(
                triples=triples,
                workspace_id=workspace_id_str,
                lookup=lookup,
            )

            n_relationships = 0
            n_evidence = 0
            for rr in resolved:
                rel_id, _ = await upsert_relationship(
                    conn,
                    workspace_id=workspace_id_str,
                    subject_entity_id=rr.subject_entity_id,
                    object_entity_id=rr.object_entity_id,
                    predicate=rr.predicate,
                    confidence=rr.confidence,
                )
                n_relationships += 1
                for triple_id in rr.triple_ids:
                    await add_evidence(
                        conn,
                        workspace_id=workspace_id_str,
                        relationship_id=rel_id,
                        triple_id=triple_id,
                        file_id=rr.file_id,
                        chunk_id=rr.chunk_id,
                        confidence=rr.confidence,
                    )
                    n_evidence += 1

            await record_lifecycle_event(
                conn,
                workspace_id=workspace_id_str,
                file_id=file_id,
                from_state=str(lifecycle_state),
                to_state=str(lifecycle_state),
                event="relationships_built",
                payload={
                    "relationship_count": n_relationships,
                    "evidence_count": n_evidence,
                    "triple_count": len(triples),
                    "resolved_count": len(resolved),
                },
            )


# ---------------------------------------------------------------------------
# B1 / WA-5 — build_graph_file_impl (arch §5 stage 17)
# ---------------------------------------------------------------------------


async def build_graph_file_impl(file_id: str) -> None:
    """Per-file incremental HippoRAG graph build.

    Reads new relationships + mention co-occurrences + lineage pairs
    that involve this file's entities, derives edges via
    graph_builder.build_edges_for_file, UPSERTs into graph_edges.
    """
    from kb.config import get_settings
    from kb.domain.graph import upsert_edge
    from kb.domain.relationships import RelationshipRecord
    from kb.extraction.graph_builder import (
        LineagePair,
        MentionInUnit,
        build_edges_for_file,
    )

    settings = get_settings()
    db_url = settings.database_url

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
            workspace_id_str = str(workspace_id)
            if lifecycle_state in ("failed", "deleted"):
                return

            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)",
                (workspace_id_str,),
            )

            # 1) Relationships sourced from this file's evidence rows.
            cur = await conn.execute(
                """
                SELECT DISTINCT r.id::text, r.workspace_id::text,
                       r.subject_entity_id::text, r.object_entity_id::text,
                       r.predicate, r.confidence, r.n_evidence,
                       r.created_at, r.updated_at
                  FROM relationships r
                  JOIN relationship_evidence e ON e.relationship_id = r.id
                 WHERE e.file_id = %s
                """,
                (file_id,),
            )
            rel_rows = await cur.fetchall()
            relationships = [
                RelationshipRecord(
                    id=str(r[0]), workspace_id=str(r[1]),
                    subject_entity_id=str(r[2]), object_entity_id=str(r[3]),
                    predicate=str(r[4]), confidence=float(r[5]),
                    n_evidence=int(r[6]),
                    created_at=r[7].isoformat() if hasattr(r[7], "isoformat") else str(r[7]),
                    updated_at=r[8].isoformat() if hasattr(r[8], "isoformat") else str(r[8]),
                )
                for r in rel_rows
            ]

            # 2) Co-mentions: pair entities mentioned in the same atomic_unit.
            # mention_to_entity joins mentions ← atomic_units via the
            # mention's source chunk → atomic_unit's source chunk. Wave A
            # simplification: pair entities whose mentions share a chunk_id
            # (the same chunk that backed an atomic_unit). This avoids a
            # complex JOIN through atomic_units while still capturing
            # tight co-occurrence.
            cur = await conn.execute(
                """
                SELECT me.entity_id::text, m.contextual_chunk_id::text
                  FROM mention_to_entity me
                  JOIN extracted_mentions m ON m.id = me.mention_id
                 WHERE m.file_id = %s
                """,
                (file_id,),
            )
            mention_rows = await cur.fetchall()
            mentions_in_units = [
                MentionInUnit(entity_id=str(r[0]), unit_id=str(r[1]))
                for r in mention_rows
            ]

            # 3) Lineage: extracted_entities.parent_entity_id pairs.
            cur = await conn.execute(
                """
                SELECT parent_entity_id::text, id::text
                  FROM extracted_entities
                 WHERE workspace_id = %s
                   AND parent_entity_id IS NOT NULL
                """,
                (workspace_id_str,),
            )
            lineage_rows = await cur.fetchall()
            lineage_pairs = [
                LineagePair(parent_entity_id=str(r[0]), child_entity_id=str(r[1]))
                for r in lineage_rows
            ]

            edges = build_edges_for_file(
                relationships=relationships,
                mentions_in_units=mentions_in_units,
                lineage_pairs=lineage_pairs,
            )

            for edge in edges:
                await upsert_edge(
                    conn,
                    workspace_id=workspace_id_str,
                    src_entity_id=edge.src_entity_id,
                    dst_entity_id=edge.dst_entity_id,
                    edge_kind=edge.edge_kind,
                    weight_delta=edge.weight_delta,
                    source_ref=edge.source_ref,
                )

            await record_lifecycle_event(
                conn,
                workspace_id=workspace_id_str,
                file_id=file_id,
                from_state=str(lifecycle_state),
                to_state=str(lifecycle_state),
                event="graph_built",
                payload={
                    "edges_upserted": len(edges),
                    "n_relationships": len(relationships),
                    "n_mention_pairs": len(mentions_in_units),
                    "n_lineage_pairs": len(lineage_pairs),
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


@procrastinate_app.task(name="raptor_build_file", queue="kb", pass_context=False)
async def raptor_build_file(file_id: str) -> None:
    """Wire-level Procrastinate task. Delegates to the testable impl.

    Phase 3d + 5a: builds the per-doc RAPTOR tree (L2+ summary nodes + edges)
    from an 'embedded' file's contextual_chunks + chunk_embeddings, then
    chains extract_mentions_file. Lifecycle: embedded → raptor_building →
    mentions_extracting (or failed).
    """
    await raptor_build_file_impl(file_id)


@procrastinate_app.task(name="extract_mentions_file", queue="kb", pass_context=False)
async def extract_mentions_file(file_id: str) -> None:
    """Wire-level Procrastinate task. Delegates to the testable impl.

    Phase 5a: extracts mentions via LLM NER → extracted_mentions rows;
    chains extract_fields_file. Lifecycle: mentions_extracting →
    fields_extracting (or failed).
    """
    await extract_mentions_file_impl(file_id)


@procrastinate_app.task(name="extract_fields_file", queue="kb", pass_context=False)
async def extract_fields_file(file_id: str) -> None:
    """Wire-level Procrastinate task. Delegates to the testable impl.

    Phase 5b: classifies doc-type, proposes emergent fields, clusters across
    workspace+doc_type, auto-promotes if thresholds cross. Chains
    extract_atomic_units_file. Lifecycle: fields_extracting →
    units_extracting (or failed).

    NOTE: Superseded by extract_kv_tables_file in the KV+Tables collapse.
    Retained here for rollback safety until the demo corpus has been
    re-extracted and verified end-to-end.
    """
    await extract_fields_file_impl(file_id)


@procrastinate_app.task(name="extract_kv_tables_file", queue="kb", pass_context=False)
async def extract_kv_tables_file(file_id: str) -> None:
    """KV+Tables collapse — replaces extract_fields_file + the LLM-driven
    portion of extract_atomic_units_file.

    Single LLM call returns scalars + N typed tables. Scalars feed the
    existing L2b promotion pipeline (proposed_fields → inferred_schema_
    fields → schema_fields). Table rows land in atomic_units, where
    Phase 1.5 bootstrap in extract_schema_entities_file promotes them to
    extracted_entities under typed sub_entity schemas.

    Lifecycle: fields_extracting → entities_extracting (skipping
    'units_extracting' — the legacy plugin slot is now dead code).
    """
    await extract_kv_tables_file_impl(file_id)


@procrastinate_app.task(name="extract_atomic_units_file", queue="kb", pass_context=False)
async def extract_atomic_units_file(file_id: str) -> None:
    """Wire-level Procrastinate task. Delegates to the testable impl.

    Phase 5c + 6: dispatches a doc-type-aware plugin (clauses / transactions /
    rows / none) → atomic_units rows with JIT anomaly scoring → chains
    extract_schema_entities_file. Lifecycle: units_extracting →
    entities_extracting (or failed).
    """
    await extract_atomic_units_file_impl(file_id)


@procrastinate_app.task(name="extract_schema_entities_file", queue="kb", pass_context=False)
async def extract_schema_entities_file(file_id: str) -> None:
    """Wire-level Procrastinate task. Delegates to the testable impl.

    Phase 6 + 7: runs Gemini structured-output extraction per active
    schema_entity → extracted_entities rows with per-field citations +
    lineage_path → chains resolve_identities_file. Lifecycle:
    entities_extracting → identity_resolving (or failed).
    """
    await extract_schema_entities_file_impl(file_id)


@procrastinate_app.task(name="resolve_identities_file", queue="kb", pass_context=False)
async def resolve_identities_file(file_id: str) -> None:
    """Wire-level Procrastinate task. Delegates to the testable impl.

    Phase 7: resolves every mention in the file to a canonical entity via
    deterministic → embedding → LLM judge → new. Final transition to `ready`.
    Last stage in the ingestion chain.
    """
    await resolve_identities_file_impl(file_id)


@procrastinate_app.task(name="detect_doc_chain_file", queue="kb", pass_context=False)
async def detect_doc_chain_file(file_id: str) -> None:
    """Wire-level Procrastinate task. Delegates to the testable impl.

    WA-3 / Design 3: per-file doc-chain detection. Runs as an additive
    post-parse task in parallel with chunk_file — does not gate lifecycle.
    """
    await detect_doc_chain_file_impl(file_id)


@procrastinate_app.task(name="extract_triples_file", queue="kb", pass_context=False)
async def extract_triples_file(file_id: str) -> None:
    """Wire-level Procrastinate task. Delegates to the testable impl.

    B1 / WA-4: per-file open-triple extraction (arch §5 stage 13).
    """
    await extract_triples_file_impl(file_id)


@procrastinate_app.task(name="build_relationships_file", queue="kb", pass_context=False)
async def build_relationships_file(file_id: str) -> None:
    """Wire-level Procrastinate task. Delegates to the testable impl.

    B1 / WA-4: per-file relationship resolution (arch §5 stage 16).
    """
    await build_relationships_file_impl(file_id)


@procrastinate_app.task(name="build_graph_file", queue="kb", pass_context=False)
async def build_graph_file(file_id: str) -> None:
    """Wire-level Procrastinate task. Delegates to the testable impl.

    B1 / WA-5: per-file incremental HippoRAG graph build (arch §5 stage 17).
    """
    await build_graph_file_impl(file_id)


# ---------------------------------------------------------------------------
# Phase 3e — raptor_build_corpus_impl
# ---------------------------------------------------------------------------


async def raptor_build_corpus_impl(*, workspace_id: str) -> None:
    """Build the corpus-level RAPTOR tree for a workspace.

    Per build_tracker §5.10.1:
    - decision #1: UMAP+GMM (sklearn GaussianMixture) for clustering.
    - decision #6: heterogeneous doc-root source (per-doc raptor roots +
      singleton contextual_chunks).
    - decision #7: discriminated edge FK — corpus L2 → raptor_nodes
      (multi-leaf doc roots) OR contextual_chunks (singleton doc roots).
    - decision #8: explicit trigger only (not chained from any file event).
    - decision #9: atomic rebuild — DELETE old scope='corpus' rows +
      INSERT new tree in ONE transaction. All-or-nothing.
    - decision #10: deterministic via random_state=42.
    - decision #13: skip when N≤1 (no corpus tree for trivial workspaces).
    - decision #14: reuses Summarizer + Embedder factories from 3d/3c.
    """
    from kb.config import get_settings
    from kb.domain.raptor import insert_raptor_edge, insert_raptor_node
    from kb.raptor import DEFAULT_BRANCHING_FACTOR, DEFAULT_MAX_LEVELS
    from kb.raptor.corpus import (
        cluster_embeddings_corpus,
        delete_corpus_rows_for_workspace,
        read_doc_roots_for_workspace,
    )
    from kb.summarization import SummarizationError, make_summarizer
    import math
    import os

    settings = get_settings()
    db_url = settings.database_url

    branching_factor = int(
        os.environ.get("KB_RAPTOR_BRANCHING_FACTOR") or DEFAULT_BRANCHING_FACTOR
    )
    max_levels = int(
        os.environ.get("KB_RAPTOR_MAX_LEVELS") or DEFAULT_MAX_LEVELS
    )
    concurrency = int(
        os.environ.get("KB_SUMMARIZER_CONCURRENCY") or 4
    )

    summarizer = make_summarizer()
    embedder = make_embedder()
    semaphore = asyncio.Semaphore(concurrency)

    # Phase 1: read doc-roots outside any tx (read-only).
    async with open_connection(db_url) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)",
            (workspace_id,),
        )
        roots = await read_doc_roots_for_workspace(conn, workspace_id=workspace_id)

    if len(roots) <= 1:
        # Decision #13: skip — N≤1 means no clustering to do.
        return

    # Phase 2: build the corpus tree in memory.
    # prev_level: list of (root_id, root_text, root_embedding, root_kind).
    # At L=2, prev_level = doc-roots (heterogeneous: kind ∈ {'node', 'chunk'}).
    # At L≥3, prev_level = raptor_nodes from the previous corpus level (all
    # kind='node').
    prev_level: list[tuple[str, str, list[float], str]] = list(roots)
    levels_built: list[int] = []

    # Staged writes: applied in a single tx after the in-memory tree is fully
    # computed (decision #9 — atomic rebuild).
    writes: list[dict] = []

    for level in range(2, max_levels + 1):
        n = len(prev_level)
        if n <= 1:
            break

        embeddings = [r[2] for r in prev_level]
        if n <= branching_factor:
            n_clusters = 1
            labels = [0] * n
        else:
            n_clusters = max(1, math.ceil(n / branching_factor))
            labels = cluster_embeddings_corpus(
                embeddings, branching_factor=branching_factor,
            )

        # Group members per cluster.
        clusters: dict[int, list[int]] = {}
        for idx, label in enumerate(labels):
            clusters.setdefault(label, []).append(idx)

        # Summarize each cluster (parallel under semaphore).
        async def _summarize_one(cluster_idx: int, member_indexes: list[int]):
            async with semaphore:
                cluster_texts = [prev_level[i][1] for i in member_indexes]
                summary = await summarizer.summarize(texts=cluster_texts)
                return cluster_idx, member_indexes, summary

        summary_results = await asyncio.gather(*(
            _summarize_one(ci, mi) for ci, mi in clusters.items()
        ))
        summary_results.sort(key=lambda t: t[0])

        # Embed all summaries in one batch.
        summary_texts = [s.text for _, _, s in summary_results]
        embedding_results = await embedder.embed_batch(summary_texts)
        if len(embedding_results) != len(summary_results):
            raise RuntimeError(
                f"embedder returned {len(embedding_results)} vectors for "
                f"{len(summary_results)} summaries"
            )

        # Stage node-writes + edge-writes.
        new_level: list[tuple[str, str, list[float], str]] = []
        for (cluster_idx, member_indexes, summary), emb_result in zip(
            summary_results, embedding_results, strict=True,
        ):
            writes.append({
                "kind": "node",
                "scope": "corpus",
                "file_id": None,  # corpus nodes have no file_id (decision #16 from 3d)
                "workspace_id": workspace_id,
                "level": level,
                "text": summary.text,
                "vector": list(emb_result.vector),
                "cluster_id_in_level": cluster_idx,
                "summarizer_model_id": summary.model_id,
                "embedder_model_id": emb_result.model_id,
                "token_count": summary.output_token_count,
            })
            for child_idx in member_indexes:
                child_id, _, _, child_kind = prev_level[child_idx]
                if child_kind == "chunk":
                    # Singleton-leaf doc root → contextual_chunks FK.
                    writes.append({
                        "kind": "edge",
                        "parent_level": level,
                        "parent_cluster_idx": cluster_idx,
                        "child_contextual_chunk_id": child_id,
                        "workspace_id": workspace_id,
                    })
                else:
                    # Multi-leaf doc root or higher-level corpus node →
                    # raptor_nodes FK. For L=2, child_id is an existing
                    # per-doc raptor_nodes ID (real, not synthetic).
                    # For L≥3, child_id is a synthetic placeholder we
                    # resolve at write time.
                    if level == 2:
                        writes.append({
                            "kind": "edge",
                            "parent_level": level,
                            "parent_cluster_idx": cluster_idx,
                            "child_node_existing_id": child_id,
                            "workspace_id": workspace_id,
                        })
                    else:
                        # Synthetic: (child_level, child_cluster_idx)
                        # Extract from the synthetic id format below.
                        synthetic_label = child_id  # encoded as "Lx-cN"
                        writes.append({
                            "kind": "edge",
                            "parent_level": level,
                            "parent_cluster_idx": cluster_idx,
                            "child_node_synthetic": synthetic_label,
                            "workspace_id": workspace_id,
                        })

            # Use a synthetic id for the next-level lookup.
            new_level.append((
                f"L{level}-c{cluster_idx}",
                summary.text,
                list(emb_result.vector),
                "node",
            ))

        levels_built.append(level)
        prev_level = new_level

        if len(new_level) <= 1:
            break

    # Phase 3: atomic write — DELETE old corpus rows + INSERT new tree.
    async with open_connection(db_url) as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)",
                (workspace_id,),
            )
            await delete_corpus_rows_for_workspace(conn, workspace_id=workspace_id)

            # Insert nodes first; build (level, cluster_idx) → real id map.
            node_id_by_synthetic: dict[str, str] = {}
            for w in writes:
                if w["kind"] == "node":
                    node_id = await insert_raptor_node(
                        conn,
                        scope=w["scope"],
                        file_id=w["file_id"],
                        workspace_id=w["workspace_id"],
                        level=w["level"],
                        text=w["text"],
                        vector=w["vector"],
                        cluster_id_in_level=w["cluster_id_in_level"],
                        summarizer_model_id=w["summarizer_model_id"],
                        embedder_model_id=w["embedder_model_id"],
                        token_count=w["token_count"],
                    )
                    node_id_by_synthetic[f"L{w['level']}-c{w['cluster_id_in_level']}"] = node_id

            # Edges: resolve synthetic IDs to real raptor_nodes IDs.
            for w in writes:
                if w["kind"] == "edge":
                    parent_id = node_id_by_synthetic[
                        f"L{w['parent_level']}-c{w['parent_cluster_idx']}"
                    ]
                    if "child_contextual_chunk_id" in w:
                        await insert_raptor_edge(
                            conn,
                            parent_node_id=parent_id,
                            workspace_id=w["workspace_id"],
                            child_contextual_chunk_id=w["child_contextual_chunk_id"],
                        )
                    elif "child_node_existing_id" in w:
                        # L=2 edges from corpus → existing per-doc raptor_nodes.
                        await insert_raptor_edge(
                            conn,
                            parent_node_id=parent_id,
                            workspace_id=w["workspace_id"],
                            child_node_id=w["child_node_existing_id"],
                        )
                    else:
                        # L≥3 edges within the corpus tree (synthetic → real).
                        child_id = node_id_by_synthetic[w["child_node_synthetic"]]
                        await insert_raptor_edge(
                            conn,
                            parent_node_id=parent_id,
                            workspace_id=w["workspace_id"],
                            child_node_id=child_id,
                        )


@procrastinate_app.task(name="raptor_build_corpus", queue="kb", pass_context=False)
async def raptor_build_corpus(workspace_id: str) -> None:
    """Wire-level Procrastinate task. Delegates to the testable impl.

    Phase 3e: builds the corpus-level RAPTOR tree for a workspace from all
    per-doc roots (or singleton contextual_chunks). Triggered explicitly
    via POST /corpus/raptor/rebuild (not chained from any file event).
    """
    await raptor_build_corpus_impl(workspace_id=workspace_id)


# ---------------------------------------------------------------------------
# Wave-A close-out — eval suite runner
#
# Drives the 45-question regression set against the live /chat endpoint
# (via loopback HTTP — keeps the runner unchanged, exercises the same
# API surface as the CLI). Transitions `eval_runs.status`:
#   queued → running → succeeded | failed
# and persists the per-question payload into `eval_run_results`.
# ---------------------------------------------------------------------------


async def run_eval_suite_impl(
    *,
    run_id: str,
    workspace_id: str,
    ragas: bool = False,
    hhem: bool = False,
    concurrency: int = 2,
    questions_path: str | None = None,
) -> None:
    """Testable impl. Mutates eval_runs / eval_run_results via the
    superuser connection (RLS is workspace-scoped + we don't have a
    request context here)."""
    import json as _json
    import os
    import traceback

    import httpx
    import psycopg

    from kb.config import get_settings
    from kb.eval.runner import load_golden_questions, run_eval
    from kb.eval.scorer import (
        reset_sidecars, score_results,
    )

    settings = get_settings()

    async def _set_status(
        status: str,
        *,
        summary: dict | None = None,
        error: str | None = None,
        finished: bool = False,
    ) -> None:
        async with await psycopg.AsyncConnection.connect(
            settings.database_url_superuser
        ) as conn:
            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)",
                (workspace_id,),
            )
            await conn.execute(
                "UPDATE eval_runs SET status = %s, "
                "  summary = COALESCE(%s::jsonb, summary), "
                "  error = COALESCE(%s, error), "
                "  finished_at = CASE WHEN %s THEN NOW() ELSE finished_at END "
                "WHERE id = %s",
                (
                    status,
                    _json.dumps(summary) if summary is not None else None,
                    error,
                    finished,
                    run_id,
                ),
            )
            await conn.commit()

    try:
        await _set_status("running")

        questions = load_golden_questions(questions_path)
        # Worker container talks to the API over docker-compose internal
        # DNS; localhost wouldn't resolve. Falls back to localhost for
        # dev-machine `python -m kb.workers.run` invocations.
        base_url = os.environ.get("KB_API_BASE_URL", "http://localhost:8000")
        async with httpx.AsyncClient(
            base_url=base_url, timeout=httpx.Timeout(120.0),
        ) as client:
            results = await run_eval(
                client, questions,
                workspace_id=workspace_id,
                concurrency=concurrency,
            )

        # Sidecar dicts are module-level; clear before scoring so this
        # run doesn't pick up another worker's leftovers.
        reset_sidecars()
        report = score_results(
            results, enable_ragas=ragas, enable_hhem=hhem,
        )

        # Persist per-question payloads + flip status → succeeded with
        # the aggregate summary blob in one transaction.
        async with await psycopg.AsyncConnection.connect(
            settings.database_url_superuser
        ) as conn:
            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)",
                (workspace_id,),
            )
            for r in results:
                await conn.execute(
                    "INSERT INTO eval_run_results "
                    "  (run_id, workspace_id, question_id, payload) "
                    "VALUES (%s, %s, %s, %s::jsonb)",
                    (run_id, workspace_id, r.question.id,
                     _json.dumps(r.to_dict())),
                )
            await conn.commit()

        await _set_status(
            "succeeded", summary=report.to_dict(), finished=True,
        )
    except Exception as exc:  # noqa: BLE001
        # Truncate the traceback so the eval_runs.error column stays
        # bounded; full trace lives in worker logs.
        tail = traceback.format_exc()[-2000:]
        await _set_status(
            "failed", error=f"{exc}\n\n{tail}", finished=True,
        )


@procrastinate_app.task(name="run_eval_suite", queue="kb", pass_context=False)
async def run_eval_suite(
    *,
    run_id: str,
    workspace_id: str,
    ragas: bool = False,
    hhem: bool = False,
    concurrency: int = 2,
    questions_path: str | None = None,
) -> None:
    """Wire-level Procrastinate task. Delegates to the testable impl."""
    await run_eval_suite_impl(
        run_id=run_id, workspace_id=workspace_id,
        ragas=ragas, hhem=hhem, concurrency=concurrency,
        questions_path=questions_path,
    )
