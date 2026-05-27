"""B3 / WA-7 — Design 5 polymorphic citation builder.

Pure-function. Consumed by `kb.query.generate` and `kb.query.orchestrator`
to turn a retrieval `Hit` + cached file metadata into a `RichCitation`
envelope that carries:

  - a `modality` label (one of CITATION_MODALITIES)
  - a `ref` JSONB locator whose shape depends on the modality
  - source-authority + doc_status + chain_id badges (Design 2 + WA-3)
  - a `confidence` derived from the retrieval score + modality semantics

Design 5 specifies 12 modalities. Wave A wires the modalities for which
we already have stored data:

  Wired with native refs:
    pdf_span        chunk on a PDF file
    xlsx_row        chunk on an xlsx workbook (sheet + row index)
    xlsx_cell       chunk on an xlsx workbook with a specific cell hint
    atomic_unit     hit.kind='atomic_unit' (clause / transaction / row)
    raptor_summary  hit.kind='raptor_node'
    email_message   chunk on an email file (thread + message id)
    entity_ref      hit with `matched_mention` (mentions_exact channel)
    chain_ref       file is a member of a doc_chain

  Forward-compat stubs (modality label set, ref carries best-effort
  data; the renderer treats them as 'pdf_span' until parsers grow):
    pdf_bbox, image_bbox, ocr_span, aggregate

When no modality fits, we fall back to `pdf_span` (the spec default).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from kb.query.rrf import Hit


# Per Design 5 §"Universal envelope" — 12 valid modality strings.
CITATION_MODALITIES: tuple[str, ...] = (
    "pdf_span", "pdf_bbox",
    "xlsx_row", "xlsx_cell",
    "image_bbox", "ocr_span",
    "email_message",
    "raptor_summary", "aggregate", "sub_entity",
    "entity_ref", "chain_ref",
)


# Per Design 5 §"Generation behavior — pick the most precise type" — ordered
# preference. Earlier = more precise. The picker walks this list and returns
# the first modality whose `applies()` predicate matches.
_PRECEDENCE: tuple[str, ...] = (
    "sub_entity",
    "xlsx_cell",
    "xlsx_row",
    "ocr_span",
    "email_message",
    "raptor_summary",
    "aggregate",
    "entity_ref",
    "chain_ref",
    "image_bbox",
    "pdf_bbox",
    "pdf_span",  # default
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FileMetaForCitation:
    """File-level fields pulled in one batch by the orchestrator and used
    to enrich each citation. None-tolerant — older docs may not have all
    fields populated."""
    file_id: str
    mime_type: str | None = None
    inferred_doc_type: str | None = None
    name: str | None = None
    source_authority: float | None = None
    doc_status: str | None = None
    chain_id: str | None = None


@dataclass(frozen=True)
class RichCitation:
    """The polymorphic Design 5 envelope. Inflated from a Hit + FileMeta.

    Serialized into query_log.citations (JSONB) and returned to the UI."""
    hit_id: str
    kind: str
    file_id: str | None
    snippet_preview: str
    score: float
    modality: str
    ref: dict[str, Any]
    label: str | None = None
    authority: float | None = None
    doc_status: str | None = None
    chain_id: str | None = None
    confidence: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSONB-serializable shape. Field order matches the query_log
        payload contract."""
        return {
            "hit_id": self.hit_id,
            "kind": self.kind,
            "file_id": self.file_id,
            "snippet_preview": self.snippet_preview,
            "score": self.score,
            "modality": self.modality,
            "ref": self.ref,
            "label": self.label,
            "authority": self.authority,
            "doc_status": self.doc_status,
            "chain_id": self.chain_id,
            "confidence": self.confidence,
        }


# ---------------------------------------------------------------------------
# Modality picker
# ---------------------------------------------------------------------------


def _applies(modality: str, hit: Hit, meta: FileMetaForCitation | None) -> bool:
    """Predicate: does this modality fit the (hit, file_meta) tuple?"""
    md = hit.metadata or {}
    mime = (meta.mime_type if meta else None) or ""
    doc_type = (meta.inferred_doc_type if meta else None) or ""

    if modality == "sub_entity":
        return hit.kind == "sub_entity"

    if modality == "xlsx_cell":
        # Wave A: we have sheet + row but no explicit cell pointer yet.
        # Reserved for a Wave B parser upgrade. Don't pick by default.
        return False

    if modality == "xlsx_row":
        return hit.kind == "chunk" and (
            "spreadsheet" in mime or mime.endswith(".sheet")
            or doc_type in ("spreadsheet", "xlsx")
        )

    if modality == "ocr_span":
        # Reserved for Wave B (OCR parser writes ocr_confidence per char).
        return False

    if modality == "email_message":
        return hit.kind == "chunk" and (
            mime.startswith("message/")
            or mime == "application/vnd.ms-outlook"
            or doc_type in ("email", "email_thread")
        )

    if modality == "raptor_summary":
        return hit.kind == "raptor_node"

    if modality == "aggregate":
        # Q-mode SQL aggregate citation — set explicitly by the Q-mode handler
        # (B4 wave). The retrieval pipeline never picks this.
        return bool(md.get("aggregate") is True)

    if modality == "entity_ref":
        # The mentions_exact channel sets `matched_mention` on the hit.
        return bool(md.get("matched_mention"))

    if modality == "chain_ref":
        # Chain badge only — the file is in a doc-chain. We DON'T pick this
        # as the primary modality (it's an annotation), so applies()=False.
        # The chain_id is exposed via RichCitation.chain_id regardless.
        return False

    if modality == "image_bbox":
        return False  # Wave B parser upgrade.

    if modality == "pdf_bbox":
        return bool(md.get("bbox"))  # if any channel ever exposes one

    if modality == "pdf_span":
        return True  # default

    return False


def pick_modality(
    hit: Hit, meta: FileMetaForCitation | None = None,
) -> str:
    """Walk the precedence list and return the first matching modality.
    Pure-function — never raises."""
    for modality in _PRECEDENCE:
        if _applies(modality, hit, meta):
            return modality
    return "pdf_span"


# ---------------------------------------------------------------------------
# Per-modality ref builders
# ---------------------------------------------------------------------------


def _pdf_span_ref(hit: Hit, meta: FileMetaForCitation | None) -> dict[str, Any]:
    md = hit.metadata or {}
    pages = md.get("source_page_numbers") or md.get("pages") or []
    page = pages[0] if pages else md.get("page")
    # R2 — prefer the PR2 source-resolver output (worker-time char range
    # inside a specific chunk) over the chunk's whole-text range. When
    # both are present the resolver's narrower window wins because that
    # was the actual snippet the LLM extracted, not the whole chunk.
    src_chunk = md.get("source_chunk_id")
    src_start = md.get("source_char_start")
    src_end = md.get("source_char_end")
    return {
        "page": int(page) if page is not None else None,
        "char_start": src_start if src_start is not None else md.get("char_start"),
        "char_end": src_end if src_end is not None else md.get("char_end"),
        # Distinct from the citation's `hit_id` — `source_chunk_id` is
        # the RAW chunks.id the worker resolver pinpointed (not the
        # contextual_chunk that the BM25/dense channels return). UI
        # fetches /chunks/:id and slices [char_start:char_end] for an
        # exact verbatim highlight, just like DocDetail does.
        "source_chunk_id": src_chunk,
    }


def _pdf_bbox_ref(hit: Hit, meta: FileMetaForCitation | None) -> dict[str, Any]:
    md = hit.metadata or {}
    return {
        "page": md.get("page"),
        "bbox": md.get("bbox"),
    }


def _xlsx_row_ref(hit: Hit, meta: FileMetaForCitation | None) -> dict[str, Any]:
    md = hit.metadata or {}
    return {
        "sheet": md.get("sheet_name") or md.get("sheet"),
        "row_hash": md.get("row_hash"),
        "row_index": md.get("row_index"),
        "key_cols": md.get("key_cols") or {},
    }


def _xlsx_cell_ref(hit: Hit, meta: FileMetaForCitation | None) -> dict[str, Any]:
    md = hit.metadata or {}
    return {
        "sheet": md.get("sheet_name") or md.get("sheet"),
        "row_hash": md.get("row_hash"),
        "col": md.get("col"),
    }


def _image_bbox_ref(hit: Hit, meta: FileMetaForCitation | None) -> dict[str, Any]:
    md = hit.metadata or {}
    return {
        "page": md.get("page"),
        "bbox": md.get("bbox"),
        "caption": md.get("caption"),
    }


def _ocr_span_ref(hit: Hit, meta: FileMetaForCitation | None) -> dict[str, Any]:
    md = hit.metadata or {}
    return {
        "page": md.get("page"),
        "ocr_char_start": md.get("char_start"),
        "ocr_char_end": md.get("char_end"),
        "src_bbox": md.get("bbox"),
        "ocr_conf": md.get("ocr_conf"),
    }


def _email_message_ref(hit: Hit, meta: FileMetaForCitation | None) -> dict[str, Any]:
    md = hit.metadata or {}
    return {
        "thread_id": md.get("thread_id") or (meta.chain_id if meta else None),
        "message_id": md.get("message_id"),
        "char_start": md.get("char_start"),
        "char_end": md.get("char_end"),
    }


def _raptor_summary_ref(hit: Hit, meta: FileMetaForCitation | None) -> dict[str, Any]:
    md = hit.metadata or {}
    return {
        "node_id": hit.id,
        "level": md.get("level"),
        "scope": md.get("scope"),
        "leaf_chunk_ids": md.get("leaf_chunk_ids") or [],
    }


def _aggregate_ref(hit: Hit, meta: FileMetaForCitation | None) -> dict[str, Any]:
    md = hit.metadata or {}
    return {
        "audit_query_id": md.get("audit_query_id"),
        "Q_plan_id": md.get("Q_plan_id"),
        "row_count": md.get("row_count"),
        "csv_artifact_id": md.get("csv_artifact_id"),
    }


def _sub_entity_ref(hit: Hit, meta: FileMetaForCitation | None) -> dict[str, Any]:
    md = hit.metadata or {}
    return {
        "unit_id": hit.id,
        "unit_type": md.get("unit_type"),
        "doc_id": meta.file_id if meta else md.get("file_id"),
        "page": md.get("page"),
        "bbox": md.get("bbox"),
        # R2 — same as _pdf_span_ref. Atomic-unit channel surfaces the
        # PR2 source-resolver output so the chat right rail can render
        # an exact verbatim slice instead of just the chunk preview.
        "source_chunk_id": md.get("source_chunk_id"),
        "char_start": md.get("source_char_start"),
        "char_end": md.get("source_char_end"),
    }


def _entity_ref(hit: Hit, meta: FileMetaForCitation | None) -> dict[str, Any]:
    md = hit.metadata or {}
    return {
        "entity_id": md.get("entity_id"),
        "alias_used": md.get("matched_mention"),
    }


def _chain_ref(hit: Hit, meta: FileMetaForCitation | None) -> dict[str, Any]:
    md = hit.metadata or {}
    chain_id = md.get("chain_id") or (meta.chain_id if meta else None)
    return {
        "chain_id": chain_id,
        "highlight_members": md.get("highlight_members") or [],
    }


_REF_BUILDERS = {
    "pdf_span": _pdf_span_ref,
    "pdf_bbox": _pdf_bbox_ref,
    "xlsx_row": _xlsx_row_ref,
    "xlsx_cell": _xlsx_cell_ref,
    "image_bbox": _image_bbox_ref,
    "ocr_span": _ocr_span_ref,
    "email_message": _email_message_ref,
    "raptor_summary": _raptor_summary_ref,
    "aggregate": _aggregate_ref,
    "sub_entity": _sub_entity_ref,
    "entity_ref": _entity_ref,
    "chain_ref": _chain_ref,
}


def build_ref(modality: str, hit: Hit, meta: FileMetaForCitation | None) -> dict[str, Any]:
    """Dispatch to the right ref builder. Falls back to `pdf_span` if the
    modality is unknown (forward-compat for future modality strings)."""
    builder = _REF_BUILDERS.get(modality) or _pdf_span_ref
    return builder(hit, meta)


# ---------------------------------------------------------------------------
# Label + confidence
# ---------------------------------------------------------------------------


def _format_label(hit: Hit, meta: FileMetaForCitation | None, modality: str) -> str:
    """Human-readable display label. Best-effort; UI may override."""
    name = (meta.name if meta else None) or "document"
    md = hit.metadata or {}
    if modality == "xlsx_row":
        sheet = md.get("sheet_name") or md.get("sheet")
        row = md.get("row_index")
        if sheet and row is not None:
            return f"{name} · Sheet: {sheet} · Row {row}"
        return f"{name} · spreadsheet"
    if modality == "raptor_summary":
        level = md.get("level")
        scope = md.get("scope")
        if scope == "corpus":
            # Corpus-scope RAPTOR nodes don't belong to a single file —
            # they're per-workspace summaries. Label them by what they
            # are (root vs cluster) rather than dangling a "document ·"
            # prefix that points nowhere clickable.
            if level == 3 or (level and level >= 3):
                return "Workspace summary"
            if level == 2:
                return "Topic cluster summary"
            return f"Corpus summary L{level}"
        return f"{name} · RAPTOR L{level}" if level is not None else f"{name} · summary"
    if modality == "sub_entity":
        unit_type = md.get("unit_type") or "unit"
        return f"{name} · {unit_type}"
    if modality == "entity_ref":
        return md.get("matched_mention") or "entity"
    if modality == "chain_ref":
        return f"{name} · doc chain"
    if modality == "email_message":
        return f"{name} · email"
    if modality == "pdf_span":
        pages = md.get("source_page_numbers") or md.get("pages") or []
        if pages:
            return f"{name} · p. {pages[0]}"
        return name
    return name


def _modality_confidence(
    modality: str, hit: Hit, meta: FileMetaForCitation | None,
) -> float | None:
    """Per Design 5 §"Universal envelope" confidence sources.

    We return a 0-1 score using the data we have today. Where the spec
    asks for OCR/VLM confidence we don't yet store, we fall back to
    the retrieval score (clamped)."""
    md = hit.metadata or {}
    s = max(0.0, min(1.0, float(hit.score))) if hit.score else 0.0

    if modality in ("xlsx_row", "xlsx_cell", "aggregate"):
        return 1.0  # exact lookup (Design 5)
    if modality == "entity_ref":
        # No identity-resolution cluster confidence stored yet — use score.
        return s
    if modality == "chain_ref":
        return s
    if modality == "raptor_summary":
        # Geometric mean approximation — we only have the node score today.
        return s
    if modality == "sub_entity":
        # Should be L3_extraction_confidence × rerank_score. We have rerank.
        return s
    if modality == "ocr_span":
        return md.get("ocr_conf") or s
    return s


# ---------------------------------------------------------------------------
# Public builder
# ---------------------------------------------------------------------------


def build_citation(
    hit: Hit, meta: FileMetaForCitation | None = None,
) -> RichCitation:
    """Build a polymorphic citation from a retrieval hit + (optional)
    file-level metadata. Pure-function.

    `meta` is None-tolerant — when it's None we still emit a citation
    with file_id pulled from hit.metadata['file_id'] when available."""
    modality = pick_modality(hit, meta)
    ref = build_ref(modality, hit, meta)
    md = hit.metadata or {}
    file_id = (meta.file_id if meta else None) or md.get("file_id")

    return RichCitation(
        hit_id=str(hit.id),
        kind=str(hit.kind),
        file_id=str(file_id) if file_id else None,
        snippet_preview=(hit.snippet or "")[:500],
        score=float(hit.score) if hit.score is not None else 0.0,
        modality=modality,
        ref=ref,
        label=_format_label(hit, meta, modality),
        authority=meta.source_authority if meta else None,
        doc_status=meta.doc_status if meta else None,
        chain_id=(meta.chain_id if meta else None) or md.get("chain_id"),
        confidence=_modality_confidence(modality, hit, meta),
    )


# ---------------------------------------------------------------------------
# DB enrichment
# ---------------------------------------------------------------------------


async def fetch_file_metas(
    conn: Any, *, file_ids: Iterable[str],
) -> dict[str, FileMetaForCitation]:
    """Batch-fetch the file-level columns needed to enrich citations.

    Returns a dict keyed by file_id (str). Files not present in the DB
    are omitted. Tolerant: if any column is missing (older schema), the
    missing field stays None on the resulting `FileMetaForCitation`.

    Wraps the SELECT in a SAVEPOINT so that if the query fails (e.g.
    bad UUID, missing column on an older schema), the outer
    transaction stays usable for downstream writes (audit log,
    idempotency cache). Without the SAVEPOINT, a Python try/except
    swallows the exception but PostgreSQL still marks the txn as
    aborted, and the next conn.execute() raises
    InFailedSqlTransaction even though we caught the original error.
    """
    ids = sorted({fid for fid in file_ids if fid})
    if not ids:
        return {}
    rows: list[tuple] = []
    try:
        await conn.execute("SAVEPOINT fetch_file_metas")
        in_savepoint = True
    except Exception:
        in_savepoint = False

    try:
        cur = await conn.execute(
            "SELECT f.id::text, f.mime_type, f.inferred_doc_type, f.name, "
            "       f.source_authority, f.doc_status, "
            "       (SELECT m.chain_id::text FROM doc_chain_members m "
            "          WHERE m.doc_id = f.id LIMIT 1) "
            "FROM files f WHERE f.id::text = ANY(%s)",
            (ids,),
        )
        rows = await cur.fetchall()
        if in_savepoint:
            try:
                await conn.execute("RELEASE SAVEPOINT fetch_file_metas")
            except Exception:
                pass
    except Exception:
        # PG marks txn aborted on any error inside a SAVEPOINT scope;
        # ROLLBACK TO SAVEPOINT clears that state so the caller can
        # keep using `conn`. Without this, the next execute() would
        # raise InFailedSqlTransaction and propagate as a 500.
        if in_savepoint:
            try:
                await conn.execute("ROLLBACK TO SAVEPOINT fetch_file_metas")
                await conn.execute("RELEASE SAVEPOINT fetch_file_metas")
            except Exception:
                pass
        return {}

    out: dict[str, FileMetaForCitation] = {}
    for r in rows:
        fid = str(r[0])
        out[fid] = FileMetaForCitation(
            file_id=fid,
            mime_type=r[1],
            inferred_doc_type=r[2],
            name=r[3],
            source_authority=(float(r[4]) if r[4] is not None else None),
            doc_status=r[5],
            chain_id=str(r[6]) if r[6] else None,
        )
    return out


async def build_citations_for_hits(
    conn: Any, hits: Iterable[Hit], *, limit: int = 10,
) -> list[RichCitation]:
    """Convenience: take the top-N hits, batch-fetch file metas, build
    polymorphic citations. Used as the canonical fallback when the LLM
    doesn't return well-formed citations (or when the generator runs in
    Identity mode)."""
    hits_list = list(hits)[:limit]
    if not hits_list:
        return []
    file_ids = [
        (h.metadata or {}).get("file_id") for h in hits_list
    ]
    metas = await fetch_file_metas(conn, file_ids=[f for f in file_ids if f])
    return [
        build_citation(h, metas.get((h.metadata or {}).get("file_id") or ""))
        for h in hits_list
    ]


def distinct_modalities(citations: Iterable[RichCitation]) -> list[str]:
    """Order-preserving distinct modality list — used to populate
    query_log.citation_modalities for the dashboard."""
    seen: set[str] = set()
    out: list[str] = []
    for c in citations:
        if c.modality not in seen:
            seen.add(c.modality)
            out.append(c.modality)
    return out
