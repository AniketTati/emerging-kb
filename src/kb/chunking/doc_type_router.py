"""Doc-type → chunker-kind routing.

The worker calls `select_chunker(workspace_id, doc_type, mime_type)` at
the start of every chunk_file run. The router looks up
`chunker_configs` for a doc-type-specific row, falls back to the
workspace-wide '*' row, then to the in-code defaults below.

This lets an admin override "bank_statement uses row_per_leaf with
chunk_sizes=[2048,512,128]" without a code deploy — the company-scale
shape we want.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kb.chunking import (
    DEFAULT_CHUNK_SIZES,
    DEFAULT_OVERLAP_TOKENS,
    Chunk,
    chunk_pages_hierarchical,
    chunk_pages_message_per_leaf,
    chunk_pages_row_per_leaf,
)
from kb.db.pool import Connection
from kb.parsers import Page


# Built-in defaults applied when no chunker_configs row matches. These
# capture the 2026 production-benchmark consensus: hierarchical for
# generic text, row-per-leaf for tabular formats, message-per-leaf for
# email threads.
#
# Each entry: (chunker_kind, chunk_sizes-tuple, overlap)
#   chunker_kind ∈ {'hierarchical','row_per_leaf','message_per_leaf','clause_per_leaf'}
_DEFAULT_BY_DOC_TYPE: dict[str, dict[str, Any]] = {
    # Tabular docs — row IS the unit.
    "bank_statement":      {"kind": "row_per_leaf", "extra": {"rows_per_mid": 15}},
    "invoice":             {"kind": "row_per_leaf", "extra": {"rows_per_mid": 10}},
    "lab_report":          {"kind": "row_per_leaf", "extra": {"rows_per_mid": 10}},
    "vendor_spreadsheet":  {"kind": "row_per_leaf", "extra": {"rows_per_mid": 20}},
    # Email threads — message IS the unit.
    "email_thread":        {"kind": "message_per_leaf"},
    "incident_report":     {"kind": "message_per_leaf"},
    # Contracts — clauses are the unit. Falls back to hierarchical for
    # Wave A; Docling-section integration is a follow-up.
    "master_services_agreement":  {"kind": "clause_per_leaf"},
    "subscription_agreement":     {"kind": "clause_per_leaf"},
    "nda":                        {"kind": "clause_per_leaf"},
    "lease":                      {"kind": "clause_per_leaf"},
    "employment":                 {"kind": "clause_per_leaf"},
    "side_letter":                {"kind": "clause_per_leaf"},
    # Everything else uses the hierarchical default.
}

# Tabular MIME types route to row_per_leaf even when doc_type is
# unknown (gives us sensible behaviour on day one before the LLM has
# classified the doc).
_TABULAR_MIME = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "text/csv",
    "text/tab-separated-values",
}
_EMAIL_MIME = {"message/rfc822"}


@dataclass(frozen=True)
class ChunkerConfig:
    """Resolved configuration for one chunk_file run."""

    kind: str
    chunk_sizes: tuple[int, ...]
    overlap_tokens: int
    extra: dict[str, Any]
    source: str  # 'db_specific' | 'db_wildcard' | 'doc_type_default' | 'mime_default' | 'fallback'


async def select_chunker(
    conn: Connection | None,
    *,
    workspace_id: str,
    doc_type: str | None,
    mime_type: str | None,
) -> ChunkerConfig:
    """Resolve which chunker to use for this file.

    Precedence:
      1. chunker_configs row for (workspace_id, doc_type)
      2. chunker_configs row for (workspace_id, '*')   — wildcard
      3. _DEFAULT_BY_DOC_TYPE built-in for the doc_type
      4. MIME-based default (xlsx → row_per_leaf, eml → message_per_leaf)
      5. Hierarchical default

    `conn` may be None in tests that don't want a DB; we skip layers
    1 + 2 in that case.
    """
    # 1 & 2: DB lookup.
    if conn is not None:
        try:
            row = await _lookup_chunker_config(
                conn, workspace_id=workspace_id, doc_type=doc_type,
            )
            if row is not None:
                return row
        except Exception:
            # DB miss / RLS / migration not yet applied — fall through.
            pass

    # 3: doc_type-based built-in default.
    builtin = _DEFAULT_BY_DOC_TYPE.get(doc_type or "")
    if builtin is not None:
        return ChunkerConfig(
            kind=builtin["kind"],
            chunk_sizes=tuple(
                builtin.get("chunk_sizes") or DEFAULT_CHUNK_SIZES
            ),
            overlap_tokens=builtin.get("overlap_tokens") or DEFAULT_OVERLAP_TOKENS,
            extra=builtin.get("extra") or {},
            source="doc_type_default",
        )

    # 4: MIME-based default for files the LLM hasn't classified yet.
    if mime_type in _TABULAR_MIME:
        return ChunkerConfig(
            kind="row_per_leaf",
            chunk_sizes=DEFAULT_CHUNK_SIZES,
            overlap_tokens=DEFAULT_OVERLAP_TOKENS,
            extra={"rows_per_mid": 20},
            source="mime_default",
        )
    if mime_type in _EMAIL_MIME:
        return ChunkerConfig(
            kind="message_per_leaf",
            chunk_sizes=DEFAULT_CHUNK_SIZES,
            overlap_tokens=DEFAULT_OVERLAP_TOKENS,
            extra={},
            source="mime_default",
        )

    # 5: hierarchical default.
    return ChunkerConfig(
        kind="hierarchical",
        chunk_sizes=DEFAULT_CHUNK_SIZES,
        overlap_tokens=DEFAULT_OVERLAP_TOKENS,
        extra={},
        source="fallback",
    )


async def _lookup_chunker_config(
    conn: Connection,
    *,
    workspace_id: str,
    doc_type: str | None,
) -> ChunkerConfig | None:
    """Two SELECTs: doc-type-specific first, then wildcard."""
    if doc_type:
        cur = await conn.execute(
            "SELECT chunker_kind, chunk_sizes, overlap_tokens, extra "
            "FROM chunker_configs "
            "WHERE workspace_id = %s AND doc_type = %s",
            (workspace_id, doc_type),
        )
        row = await cur.fetchone()
        if row:
            return _row_to_config(row, source="db_specific")
    cur = await conn.execute(
        "SELECT chunker_kind, chunk_sizes, overlap_tokens, extra "
        "FROM chunker_configs "
        "WHERE workspace_id = %s AND doc_type = '*'",
        (workspace_id,),
    )
    row = await cur.fetchone()
    if row:
        return _row_to_config(row, source="db_wildcard")
    return None


def _row_to_config(row: tuple, *, source: str) -> ChunkerConfig:
    kind, sizes, overlap, extra = row
    return ChunkerConfig(
        kind=kind,
        chunk_sizes=tuple(sizes) if sizes else DEFAULT_CHUNK_SIZES,
        overlap_tokens=overlap if overlap is not None else DEFAULT_OVERLAP_TOKENS,
        extra=dict(extra) if isinstance(extra, dict) else {},
        source=source,
    )


def run_chunker(
    pages: list[Page], *, config: ChunkerConfig,
) -> list[Chunk]:
    """Dispatch to the appropriate chunker for `config.kind`. Returns
    a flat list of Chunks in topological order (parents before children)."""
    if config.kind == "row_per_leaf":
        rows_per_mid = int(config.extra.get("rows_per_mid", 20))
        return chunk_pages_row_per_leaf(pages, rows_per_mid=rows_per_mid)

    if config.kind == "message_per_leaf":
        return chunk_pages_message_per_leaf(pages)

    # clause_per_leaf currently delegates to hierarchical; proper
    # Docling-section integration lands as a follow-up.
    # hierarchical is the default path.
    return chunk_pages_hierarchical(
        pages,
        chunk_sizes=config.chunk_sizes,
        overlap_tokens=config.overlap_tokens,
    )
