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

    Order matters — **most specific SEMANTIC plugin first**, generic
    STRUCTURAL fallback last. Previous order picked structural plugins
    first (rows by MIME) which silently pre-empted semantic plugins
    for documents like bank_statement.xlsx: RowsPlugin would grab the
    file because it's xlsx, and TransactionsPlugin would never run.
    The result was 21 generic `row` atomic units with raw `cells[]`
    arrays instead of typed `transaction` units with
    {date, debit, credit, balance} parameters.

    Today's order, top-to-bottom:

      1. transactions   — bank_statement / statement / account_statement
                          doc_types (LLM-driven; produces typed
                          transaction units regardless of MIME).
      2. clauses        — contract-like doc_types (LLM, prose).
      3. email_messages — .eml mime / email_thread doc_type.
      4. rows           — generic xlsx fallback when no semantic plugin
                          claimed it. Still match-by-MIME, but only
                          runs as the LAST structural pass.
      5. generic_items  — final LLM fallback for prose doc_types none of
                          the specific plugins claimed.

    The key invariant: a doc with a semantic plugin match (transactions,
    clauses, email_messages) always uses it. Rows / generic_items are
    "we don't know what this is, do something reasonable" fallbacks.
    """
    from kb.extraction.plugins import (
        clauses, email_messages, generic_items, rows, transactions,
    )

    for plugin in (
        transactions.PLUGIN,
        clauses.PLUGIN,
        email_messages.PLUGIN,
        rows.PLUGIN,
        generic_items.PLUGIN,
    ):
        if plugin.matches(file_meta):
            return plugin
    return None
