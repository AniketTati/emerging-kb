"""Docling parser — digital PDF (layout-aware) per Phase 2a.

Docling is sync + CPU-bound; we run it in a worker thread via asyncio.to_thread
so the worker's event loop stays responsive. The DocumentConverter is heavy
to construct (loads layout models on first init) so we cache one process-wide.
"""

from __future__ import annotations

import asyncio
import io
import threading
from typing import Any

from kb.parsers import Page, ParsedDocument, ParseError


# Lazy-construct the converter on first parse — keeps import time fast for
# tests that don't actually parse (e.g., the dispatch tests).
_converter_lock = threading.Lock()
_converter = None


def _get_converter():
    global _converter
    if _converter is not None:
        return _converter
    with _converter_lock:
        if _converter is None:
            # Force CPU device: Mac MPS doesn't support float64 which Docling's
            # layout model needs. Production runs on Linux (worker container)
            # where CUDA or CPU is the default anyway — no regression there.
            from docling.datamodel.accelerator_options import (
                AcceleratorDevice,
                AcceleratorOptions,
            )
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import (
                DocumentConverter,
                PdfFormatOption,
            )
            from docling.datamodel.base_models import InputFormat

            pipeline_opts = PdfPipelineOptions()
            pipeline_opts.accelerator_options = AcceleratorOptions(
                device=AcceleratorDevice.CPU,
            )
            _converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_opts),
                },
            )
        return _converter


class DoclingParser:
    """Digital PDF parser — extracts per-page text + layout metadata."""

    def can_handle(self, mime_type: str, magic_bytes: bytes) -> bool:
        if mime_type == "application/pdf":
            return True
        # Magic-byte fallback (used when mime is missing or empty)
        return magic_bytes.startswith(b"%PDF-")

    async def parse(
        self, file_bytes: bytes, *, file_id: str, workspace_id: str
    ) -> ParsedDocument:
        try:
            doc = await asyncio.to_thread(self._parse_sync, file_bytes, file_id)
        except ParseError:
            raise
        except Exception as exc:
            raise ParseError(f"docling failed on file={file_id}: {exc}") from exc
        return doc

    def _parse_sync(self, file_bytes: bytes, file_id: str) -> ParsedDocument:
        """Sync core — invoked inside asyncio.to_thread."""
        # Basic sanity check before invoking the heavyweight Docling pipeline
        if not file_bytes.startswith(b"%PDF-"):
            raise ParseError(f"not a PDF: file={file_id}")

        from docling.datamodel.base_models import DocumentStream

        stream = DocumentStream(name=f"{file_id}.pdf", stream=io.BytesIO(file_bytes))
        converter = _get_converter()
        result = converter.convert(stream)
        doc = result.document

        pages: list[Page] = []
        # Docling's DoclingDocument exposes pages via doc.pages (dict-like by
        # page number) or via iter_items(). Use export_to_text per page if
        # available; otherwise fall back to splitting markdown.
        try:
            # Newer docling: doc.pages is a dict {page_no: PageItem}
            for page_no in sorted(doc.pages.keys()):
                page_item = doc.pages[page_no]
                # Docling page text can be obtained via export_to_markdown or
                # by walking the content; use the simple route.
                text = self._page_text(doc, page_no)
                layout = self._page_layout(page_item)
                pages.append(Page(
                    page_number=page_no, text=text, layout_json=layout,
                ))
        except (AttributeError, TypeError):
            # Older/different docling API — fall back to whole-doc text split
            full_text = doc.export_to_text() if hasattr(doc, "export_to_text") else doc.export_to_markdown()
            pages.append(Page(page_number=1, text=full_text, layout_json={}))

        if not pages:
            raise ParseError(f"docling produced 0 pages: file={file_id}")
        return ParsedDocument(pages=pages)

    def _page_text(self, doc, page_no: int) -> str:
        """Best-effort per-page text extraction."""
        try:
            # Some docling versions support export_to_text(page_no=...)
            return doc.export_to_text(page_no=page_no)
        except (AttributeError, TypeError):
            pass
        try:
            return doc.export_to_markdown(page_no=page_no)
        except (AttributeError, TypeError):
            pass
        # Last resort: whole-doc text
        try:
            return doc.export_to_text()
        except Exception:
            return ""

    def _page_layout(self, page_item) -> dict[str, Any]:
        """Best-effort layout extraction — Docling's PageItem has size + items."""
        try:
            return {
                "size": {
                    "width": getattr(page_item.size, "width", None),
                    "height": getattr(page_item.size, "height", None),
                } if hasattr(page_item, "size") and page_item.size else None,
            }
        except Exception:
            return {}
