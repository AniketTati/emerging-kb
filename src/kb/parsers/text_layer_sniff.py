"""Phase 2c — pre-flight PDF text-layer sniff.

Cheap (~10ms/page) check that answers: does this PDF have a real text layer,
or is it just page images? The strategy-aware dispatcher routes the file
based on the answer.

Per build_tracker §5.6.1 decisions #8 + #9:
- threshold: 50 chars/page average (KB_PDF_TEXT_LAYER_THRESHOLD)
- bounded sampling: only the first 10 pages (large docs don't pay a 100-page
  sniff cost before the actual parse even starts)
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import pypdfium2 as pdfium


DEFAULT_THRESHOLD = 50           # chars/page
DEFAULT_MAX_PAGES_SAMPLED = 10   # first N pages of a doc


@dataclass
class SniffResult:
    page_count: int           # actual number of pages in the PDF
    pages_sampled: int        # how many pages we inspected (bounded by max)
    avg_chars_per_page: float # average over sampled pages
    has_text_layer: bool      # True iff avg >= threshold


def sniff_pdf_text_layer(
    buffer: bytes,
    *,
    threshold: int | None = None,
    max_pages_sampled: int | None = None,
) -> SniffResult:
    """Inspect a PDF's text layer without invoking Docling or Gemini.

    Uses pypdfium2 to count extractable chars per page across the first
    `max_pages_sampled` pages. Returns a SniffResult the dispatcher uses
    to decide between Docling (text-layer present) and Gemini OCR
    (text-layer missing).

    Falls back to `has_text_layer=False` on any pypdfium2 error — better to
    over-route to OCR than to crash the dispatcher.
    """
    threshold = threshold if threshold is not None else int(
        os.environ.get("KB_PDF_TEXT_LAYER_THRESHOLD") or DEFAULT_THRESHOLD
    )
    max_pages_sampled = max_pages_sampled if max_pages_sampled is not None else int(
        os.environ.get("KB_PDF_TEXT_LAYER_MAX_PAGES_SAMPLED")
        or DEFAULT_MAX_PAGES_SAMPLED
    )

    try:
        doc = pdfium.PdfDocument(buffer)
    except Exception:
        # Malformed PDF? Route to OCR — it's more forgiving than text-layer
        # extraction on damaged files.
        return SniffResult(
            page_count=0,
            pages_sampled=0,
            avg_chars_per_page=0.0,
            has_text_layer=False,
        )

    try:
        page_count = len(doc)
        pages_to_sample = min(page_count, max_pages_sampled)
        total_chars = 0
        for i in range(pages_to_sample):
            try:
                page = doc[i]
                textpage = page.get_textpage()
                text = textpage.get_text_range() or ""
                total_chars += len(text.strip())
            except Exception:
                # Per-page failures shouldn't abort the sniff; treat as 0.
                continue

        avg = (total_chars / pages_to_sample) if pages_to_sample > 0 else 0.0

        return SniffResult(
            page_count=page_count,
            pages_sampled=pages_to_sample,
            avg_chars_per_page=avg,
            has_text_layer=avg >= threshold,
        )
    finally:
        doc.close()
