"""Phase 2c — GeminiOCRParser.

Per-page PDF → PNG via pypdfium2 → Gemini 2.5 Flash VLM call → markdown text.
One ParsedDocument.pages[*] entry per page; per-page concurrency capped at
`asyncio.Semaphore(4)`.

Per build_tracker §5.6.1 decisions:
  #1 model: gemini-2.5-flash (configurable via KB_OCR_MODEL)
  #2 PDF→image: pypdfium2
  #3 render DPI: 150
  #4 image format: PNG (lossless; tables suffer with JPEG)
  #5 prompt: fixed OCR instruction (markdown-preserving)
  #6 concurrency: asyncio.Semaphore(4) (configurable via KB_OCR_CONCURRENCY)
  #13 missing-key → OCRConfigError at construction
"""

from __future__ import annotations

import asyncio
import os
from io import BytesIO
from typing import Any

import pypdfium2 as pdfium

from kb.parsers import Page, ParsedDocument, ParseError


DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_CONCURRENCY = 4
DEFAULT_RENDER_DPI = 150

# §5.6.1 decision #5 — fixed OCR prompt.
_OCR_PROMPT = (
    "Extract ALL text from this document page. "
    "Preserve tables as markdown tables, headings as # / ## / ###, "
    "lists as - bullets. "
    "Return only the extracted text, no preamble or commentary."
)


class OCRConfigError(Exception):
    """Raised when a Gemini-OCR strategy is requested but the configuration
    can't satisfy it (no API key, no client, etc.). Worker translates this
    into a parsing→failed lifecycle event with error_class='OCRConfigError'."""


def _render_pdf_pages_to_png_bytes(
    pdf_buffer: bytes, *, dpi: int = DEFAULT_RENDER_DPI
) -> list[bytes]:
    """Render each PDF page to a PNG (returned as bytes)."""
    doc = pdfium.PdfDocument(pdf_buffer)
    try:
        rendered: list[bytes] = []
        scale = dpi / 72.0
        for page in doc:
            bitmap = page.render(scale=scale)
            pil_img = bitmap.to_pil()
            if pil_img.mode != "RGB":
                pil_img = pil_img.convert("RGB")
            buf = BytesIO()
            pil_img.save(buf, format="PNG")
            rendered.append(buf.getvalue())
        return rendered
    finally:
        doc.close()


class GeminiOCRParser:
    """Adapter for Gemini 2.5 Flash multimodal OCR (architecture line 423)."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: Any | None = None,
        model: str | None = None,
        concurrency: int | None = None,
        render_dpi: int | None = None,
    ) -> None:
        if client is None:
            if not api_key:
                raise OCRConfigError(
                    "GeminiOCRParser requires api_key or client "
                    "(set KB_GEMINI_API_KEY)"
                )
            from google.genai import Client
            client = Client(api_key=api_key)
        self._client = client
        self._model = model or os.environ.get("KB_OCR_MODEL") or DEFAULT_MODEL
        self._concurrency = concurrency or int(
            os.environ.get("KB_OCR_CONCURRENCY") or DEFAULT_CONCURRENCY
        )
        self._render_dpi = render_dpi or int(
            os.environ.get("KB_OCR_RENDER_DPI") or DEFAULT_RENDER_DPI
        )

    def can_handle(self, mime_type: str, magic_bytes: bytes) -> bool:
        """Tell the registry we handle PDFs. The strategy-aware dispatcher
        (`select_parser_for`) is what actually routes — this method exists
        so the registry-level fallback can still find us."""
        if mime_type == "application/pdf":
            return True
        return magic_bytes.startswith(b"%PDF-")

    async def parse(
        self, file_bytes: bytes, *, file_id: str, workspace_id: str
    ) -> ParsedDocument:
        # Re-read env at call time so tests can swap KB_OCR_MODEL on each
        # call without rebuilding the parser.
        model = os.environ.get("KB_OCR_MODEL") or self._model

        # Step 1: render every PDF page to PNG bytes (sync via pypdfium2).
        try:
            page_pngs = await asyncio.to_thread(
                _render_pdf_pages_to_png_bytes,
                file_bytes,
                dpi=self._render_dpi,
            )
        except Exception as exc:
            raise ParseError(
                f"GeminiOCR: pypdfium2 render failed on file={file_id}: {exc}"
            ) from exc

        if not page_pngs:
            raise ParseError(
                f"GeminiOCR: pypdfium2 produced zero pages for file={file_id}"
            )

        # Step 2: per-page Gemini calls with concurrency cap (§5.6.1 #6).
        semaphore = asyncio.Semaphore(self._concurrency)
        from google.genai import types as genai_types

        async def _ocr_one(page_idx: int, png_bytes: bytes) -> Page:
            async with semaphore:
                image_part = genai_types.Part.from_bytes(
                    data=png_bytes, mime_type="image/png"
                )
                try:
                    response = await self._client.aio.models.generate_content(
                        model=model,
                        contents=[_OCR_PROMPT, image_part],
                    )
                except Exception as exc:
                    raise ParseError(
                        f"GeminiOCR: API call failed for file={file_id} "
                        f"page={page_idx + 1}: {exc}"
                    ) from exc

                # Extract text; tolerate the shape variations the mock uses.
                text = ""
                candidates = getattr(response, "candidates", None) or []
                if candidates:
                    content = getattr(candidates[0], "content", None)
                    parts = getattr(content, "parts", None) or []
                    for part in parts:
                        part_text = getattr(part, "text", None)
                        if part_text:
                            text = part_text
                            break
                if not text:
                    text = getattr(response, "text", "") or ""
                text = text.strip()

                usage = getattr(response, "usage_metadata", None)
                prompt_tokens = getattr(usage, "prompt_token_count", 0) or 0
                candidates_tokens = getattr(usage, "candidates_token_count", 0) or 0

                return Page(
                    page_number=page_idx + 1,
                    text=text,
                    layout_json={
                        "ocr_model": model,
                        "prompt_tokens": prompt_tokens,
                        "candidates_tokens": candidates_tokens,
                    },
                )

        pages = await asyncio.gather(
            *(_ocr_one(i, png) for i, png in enumerate(page_pngs))
        )
        return ParsedDocument(pages=list(pages))
