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

        # R5 — extract per-element provenance up-front so we can attach
        # bbox + label + text-preview to each page's layout_json. Docling
        # exposes items via `doc.iterate_items()` with `.prov[].bbox`
        # (l/t/r/b + coord_origin). We bucket by page_no so a multi-page
        # PDF surfaces its layout per-page instead of as a flat list.
        elements_by_page = self._extract_elements_by_page(doc)

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
                # Attach per-element provenance for this page (may be []).
                layout["elements"] = elements_by_page.get(page_no, [])
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

    # R5 — per-element provenance extraction
    # ------------------------------------------------------------------
    # Docling's `iterate_items()` yields `(item, level)` for every
    # text-block / table / picture / heading the layout model detected.
    # Each item has a `prov` list of ProvenanceItem with page_no + bbox
    # (l/t/r/b, coord_origin) + charspan. We bucket by page_no and
    # include label (the Docling DocItemLabel — section_header / text /
    # table / picture / page_header / etc.) so the UI can colour-code or
    # filter the overlay.
    #
    # Defensive on the API surface: Docling has changed shape across
    # 2.x — any AttributeError on a per-item walk gets silently swallowed
    # so a partial layout is still better than none, and a totally
    # missing API just yields `{}` per page.
    def _extract_elements_by_page(self, doc) -> dict[int, list[dict[str, Any]]]:
        out: dict[int, list[dict[str, Any]]] = {}
        try:
            iterator = doc.iterate_items()
        except (AttributeError, TypeError):
            return out

        for entry in iterator:
            # iterate_items yields (item, level) in newer docling versions.
            item = entry[0] if isinstance(entry, tuple) else entry
            label = self._item_label(item)
            text = self._item_text_preview(item)
            for prov in getattr(item, "prov", None) or []:
                page_no = getattr(prov, "page_no", None)
                if not isinstance(page_no, int):
                    continue
                bbox = self._bbox_to_dict(getattr(prov, "bbox", None))
                if bbox is None:
                    continue
                element = {
                    "label": label,
                    "bbox": bbox,
                }
                if text:
                    # Cap at 240 chars — enough to identify the block in
                    # the UI without bloating the row JSON (a 50-page PDF
                    # with 30 elements/page would otherwise blow up).
                    element["text"] = text[:240]
                charspan = getattr(prov, "charspan", None)
                if charspan and len(charspan) == 2:
                    element["charspan"] = [int(charspan[0]), int(charspan[1])]
                out.setdefault(page_no, []).append(element)
        return out

    @staticmethod
    def _item_label(item) -> str | None:
        label = getattr(item, "label", None)
        if label is None:
            return None
        # DocItemLabel is a StrEnum in newer docling-core — str(label)
        # gives the readable form ("section_header", "text", "table", …).
        try:
            return str(label.value) if hasattr(label, "value") else str(label)
        except Exception:
            return None

    @staticmethod
    def _item_text_preview(item) -> str:
        # TextItem / SectionHeaderItem / ListItem all expose `.text`.
        # TableItem doesn't — its content lives in `.data` (DataFrame-
        # shaped). For Wave A we only surface text-bearing elements;
        # tables get an empty preview but their bbox still renders.
        text = getattr(item, "text", None)
        if isinstance(text, str):
            return text.strip()
        return ""

    @staticmethod
    def _bbox_to_dict(bbox) -> dict[str, Any] | None:
        if bbox is None:
            return None
        try:
            origin = getattr(bbox, "coord_origin", None)
            origin_str = (
                origin.value if origin is not None and hasattr(origin, "value")
                else str(origin) if origin is not None else None
            )
            return {
                "l": float(bbox.l),
                "t": float(bbox.t),
                "r": float(bbox.r),
                "b": float(bbox.b),
                "coord_origin": origin_str,
            }
        except (AttributeError, TypeError, ValueError):
            return None
