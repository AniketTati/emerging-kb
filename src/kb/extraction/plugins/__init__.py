"""Phase 5c — atomic-unit plugin registry.

Per build_tracker §5.12.3 decisions #1/#2.

Each plugin module exposes:
  - `UNIT_TYPE: str` — the unit_type stored in `atomic_units.unit_type`.
  - `matches(file_meta) -> bool` — checks file mime_type / inferred_doc_type.
  - `async extract(file_meta, doc_text, raw_pages) -> list[AtomicUnit]`.

Dispatcher `dispatch(file_meta)` returns the FIRST matching plugin or None
(file types not supported in Wave A yield no atomic units).

Wave A plugins:
  - clauses (contracts / NDAs / employment letters)
  - transactions (bank statements)
  - rows (xlsx)

PR8 additions (cover the long tail of "0 atomic units" docs):
  - email_messages (.eml files + email_thread classifications)
  - generic_items (LLM-driven fallback for prose doc_types — postmortems,
    RFCs, performance reviews, press releases, lab reports, …)
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field


class AtomicUnit(BaseModel):
    """One row of `atomic_units`. `parameters` is plugin-specific."""

    unit_type: str = Field(min_length=1, max_length=50)
    parameters: dict[str, Any]
    anchor_chunk_id: str | None = None


class FileMeta(BaseModel):
    """The plugin dispatcher's input — file-level metadata for matching."""

    file_id: str
    workspace_id: str
    mime_type: str
    inferred_doc_type: str | None
    name: str


class AtomicUnitPlugin(Protocol):
    UNIT_TYPE: str

    def matches(self, file_meta: FileMeta) -> bool: ...
    async def extract(
        self,
        *,
        file_meta: FileMeta,
        doc_text: str,
        raw_pages: list[tuple[int, str, dict]],
    ) -> list[AtomicUnit]: ...


def dispatch(file_meta: FileMeta) -> AtomicUnitPlugin | None:
    """Return the first matching plugin or None.

    Order matters — most specific structural plugin first, generic
    LLM-driven plugin last:

      1. rows           — xlsx mime / spreadsheet doc_type (no LLM)
      2. email_messages — .eml mime / email_thread doc_type (no LLM)
      3. transactions   — narrow doc_type list (LLM, bank statements)
      4. clauses        — contract-like doc_types (LLM, prose)
      5. generic_items  — fallback for prose doc_types that none of the
                          specific plugins claimed (LLM, doc-type-aware)

    `generic_items` is LAST so it doesn't pre-empt the specific plugins
    (which produce richer, structured `parameters` shapes). Its
    `matches` predicate also early-rejects xlsx + .eml mimes as a
    second line of defense.
    """
    from kb.extraction.plugins import (
        clauses, email_messages, generic_items, rows, transactions,
    )

    for plugin in (
        rows.PLUGIN,
        email_messages.PLUGIN,
        transactions.PLUGIN,
        clauses.PLUGIN,
        generic_items.PLUGIN,
    ):
        if plugin.matches(file_meta):
            return plugin
    return None
