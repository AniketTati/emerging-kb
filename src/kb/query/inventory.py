"""Inventory mode — deterministic SQL-based answer for metadata queries.

Triggered when the intent classifier returns `inventory` for queries like
"what types of documents do I have", "list my files", "how many invoices".
The orchestrator short-circuits its usual retrieve → CRAG → generate
pipeline and calls `build_inventory_answer()` directly, returning a
markdown-table response with one citation per file mentioned.

No LLM call, no chunk retrieval, no hallucination risk. Latency drops
from ~13s to ~50ms because we just run a single SQL `GROUP BY`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kb.query.generate import Citation, GenerationResult


# Top-N file names shown inline per doc-type row. Anything beyond gets
# rolled up as `...and N more`. Keeps the table readable for workspaces
# with hundreds of files of the same type (e.g. a CUAD-style 510-contract
# import) while still surfacing every type.
_INLINE_FILES_PER_TYPE = 5


@dataclass(frozen=True)
class _TypeRow:
    doc_type: str | None  # NULL when the file was never classified
    count: int
    file_names: list[str]
    file_ids: list[str]


async def _read_workspace_inventory(
    conn: Any, *, workspace_id: str,
) -> list[_TypeRow]:
    """One SQL GROUP BY against the `files` table. Filters out
    soft-deleted dupes the same way retrieval channels do."""
    cur = await conn.execute(
        """
        SELECT inferred_doc_type,
               count(*) AS n,
               array_agg(name ORDER BY created_at DESC) AS file_names,
               array_agg(id::text ORDER BY created_at DESC) AS file_ids
          FROM files
         WHERE workspace_id = %s
           AND lifecycle_state NOT IN ('failed', 'deleted')
         GROUP BY inferred_doc_type
         ORDER BY n DESC, inferred_doc_type ASC NULLS LAST
        """,
        (workspace_id,),
    )
    rows = await cur.fetchall()
    return [
        _TypeRow(
            doc_type=r[0],
            count=int(r[1]),
            file_names=list(r[2] or []),
            file_ids=list(r[3] or []),
        )
        for r in rows
    ]


def _render_markdown(rows: list[_TypeRow]) -> str:
    """Compose the user-facing markdown answer from the SQL result."""
    if not rows:
        return (
            "Your workspace is empty — no documents have been uploaded "
            "yet. Drop files on the **Upload** page to get started."
        )

    total_files = sum(r.count for r in rows)
    total_types = sum(1 for r in rows if r.doc_type is not None)
    unclassified = sum(r.count for r in rows if r.doc_type is None)

    lines: list[str] = []
    header = (
        f"You have **{total_files} documents** across **{total_types} "
        f"document type{'s' if total_types != 1 else ''}**"
    )
    if unclassified:
        header += (
            f" (plus {unclassified} file"
            f"{'s' if unclassified != 1 else ''} still being classified)"
        )
    header += " in this workspace."
    lines.append(header)
    lines.append("")
    lines.append("| Type | Count | Files |")
    lines.append("|---|---:|---|")
    for r in rows:
        type_label = r.doc_type or "_(unclassified)_"
        inline = r.file_names[:_INLINE_FILES_PER_TYPE]
        suffix = ""
        if len(r.file_names) > _INLINE_FILES_PER_TYPE:
            suffix = f", _…and {len(r.file_names) - _INLINE_FILES_PER_TYPE} more_"
        files_cell = ", ".join(inline) + suffix
        lines.append(f"| `{type_label}` | {r.count} | {files_cell} |")
    return "\n".join(lines)


def _build_citations(rows: list[_TypeRow]) -> list[Citation]:
    """One Citation per file mentioned inline in the table. The user can
    click any file in the right rail to jump to its doc-detail page.

    File-level citations use a synthetic `hit_id == file_id` (no
    underlying chunk for inventory mode) and a fixed kind tag the UI
    treats like any other file citation.
    """
    cits: list[Citation] = []
    for r in rows:
        # Only cite the inline files — the "…and N more" rollup doesn't
        # surface individual ids so we'd be wrong to claim them.
        for name, fid in zip(
            r.file_names[:_INLINE_FILES_PER_TYPE],
            r.file_ids[:_INLINE_FILES_PER_TYPE],
            strict=True,
        ):
            cits.append(Citation(
                hit_id=fid,
                kind="file",
                file_id=fid,
                snippet_preview=f"{r.doc_type or 'unclassified'} · {name}",
                score=1.0,
                modality="file_ref",
                label=name,
            ))
    return cits


async def build_inventory_answer(
    conn: Any, *, workspace_id: str,
) -> GenerationResult:
    """End-to-end entry point — the orchestrator calls this and returns
    the result without running retrieval / CRAG / generator.

    Failure mode: if the SQL query raises (RLS denied, schema drift),
    the caller's exception handler kicks in and the user sees the
    generic "pipeline error" envelope. We don't try to recover here —
    a metadata query failing is rare enough to not warrant complexity.
    """
    rows = await _read_workspace_inventory(conn, workspace_id=workspace_id)
    return GenerationResult(
        answer=_render_markdown(rows),
        citations=_build_citations(rows),
        refused=False,
        refusal_reason=None,
        model_id="inventory-sql-v1",
    )
