"""Plain-text + Markdown parser.

Handles `text/plain` and `text/markdown`. One Page per ~3000-char chunk
so very long docs don't end up as a single page (matches the per-page
abstraction the rest of the pipeline assumes).

For Markdown we keep the raw source as the page text — the chunker is
char-based and the contextualizer is LLM-based, so neither needs a
rendered DOM. Headings remain visible inline.

Used by the demo corpus and any text-first workflow.
"""

from __future__ import annotations

import re

from kb.parsers import Page, ParsedDocument, ParseError


_ACCEPTED_MIMES = frozenset({"text/plain", "text/markdown"})

# Soft max per page. The pipeline's chunker re-chunks downstream anyway;
# this is just to keep `raw_pages` rows from becoming absurdly large.
_PAGE_CHAR_LIMIT = 3000


class TextParser:
    """Accepts text/plain and text/markdown. Magic-byte sniffing is
    skipped: text has no signature. Caller's mime decision is trusted."""

    def can_handle(self, mime_type: str, magic_bytes: bytes) -> bool:
        return mime_type in _ACCEPTED_MIMES

    async def parse(
        self, file_bytes: bytes, *, file_id: str, workspace_id: str,
    ) -> ParsedDocument:
        try:
            text = file_bytes.decode("utf-8")
        except UnicodeDecodeError:
            # Try latin-1 as a fallback; gives us SOME content for the
            # ingest pipeline even if the file is in an exotic encoding.
            try:
                text = file_bytes.decode("latin-1")
            except Exception as exc:  # noqa: BLE001
                raise ParseError(
                    f"text decode failed for file={file_id}: {exc}"
                ) from exc

        text = text.strip()
        if not text:
            # Empty file — emit a single empty page so the file still
            # advances through the lifecycle (chunker will produce 0
            # chunks; downstream handles that case).
            return ParsedDocument(pages=[Page(page_number=1, text="")])

        pages_text = _split_into_pages(text, _PAGE_CHAR_LIMIT)
        pages = [
            Page(page_number=i, text=t, layout_json={})
            for i, t in enumerate(pages_text, start=1)
        ]
        return ParsedDocument(pages=pages)


def _split_into_pages(text: str, limit: int) -> list[str]:
    """Split text into pages of <= `limit` chars, preferring paragraph
    boundaries. Pure-function; never raises."""
    if len(text) <= limit:
        return [text]

    out: list[str] = []
    # Split on blank-line paragraph boundaries first.
    paragraphs = re.split(r"\n\s*\n", text)
    buf: list[str] = []
    buf_len = 0
    for para in paragraphs:
        p = para.strip()
        if not p:
            continue
        if buf_len + len(p) + 2 > limit and buf:
            out.append("\n\n".join(buf))
            buf = [p]
            buf_len = len(p)
        else:
            buf.append(p)
            buf_len += len(p) + 2
    if buf:
        out.append("\n\n".join(buf))

    # Safety: if a single paragraph is bigger than the limit, hard-cut it.
    final: list[str] = []
    for p in out:
        if len(p) <= limit:
            final.append(p)
            continue
        for i in range(0, len(p), limit):
            final.append(p[i:i + limit])
    return final or [text]
