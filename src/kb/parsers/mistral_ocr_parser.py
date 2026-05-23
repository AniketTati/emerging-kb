"""Mistral OCR adapter — external API parser for scanned PDFs.

Phase 2b. Per build_tracker §5.6 decisions:
- #7: registered AFTER Docling so it's currently inert at dispatch (Docling
      always wins for application/pdf). Activates when a force-parser
      mechanism lands (Phase 2c).
- #8: constructor takes an optional `http_client` for test injection. CI tests
      use a mock; real API integration unblocks when KB_MISTRAL_API_KEY is set.
- #9: `can_handle()` returns False when KB_MISTRAL_API_KEY is unset — the
      parser self-disables in environments without a key.

Real API docs: https://docs.mistral.ai/capabilities/document-ai/basic_ocr/
Endpoint shape: POST https://api.mistral.ai/v1/ocr with multipart file upload;
response is `{"pages": [{"index": int, "markdown": str, "images": [...]}]}`.
"""

from __future__ import annotations

import os
from typing import Any, Protocol

import httpx

from kb.parsers import Page, ParsedDocument, ParseError


_MISTRAL_OCR_URL = "https://api.mistral.ai/v1/ocr"


class _HTTPClient(Protocol):
    """Minimal HTTP-client interface — both httpx.AsyncClient and our test
    mock satisfy this shape."""

    async def post(self, url: str, *, headers: dict, files: dict | None = None,
                   json: dict | None = None) -> Any: ...


class MistralOCRParser:
    """Adapter for Mistral OCR 3 (architecture line 419)."""

    def __init__(self, http_client: _HTTPClient | None = None) -> None:
        # Tests inject a mock; production uses the lazy-initialized real client.
        self._http_client = http_client

    def can_handle(self, mime_type: str, magic_bytes: bytes) -> bool:
        # Decision #9: self-disable when no API key.
        if not os.environ.get("KB_MISTRAL_API_KEY"):
            return False
        if mime_type == "application/pdf":
            return True
        return magic_bytes.startswith(b"%PDF-")

    async def parse(
        self, file_bytes: bytes, *, file_id: str, workspace_id: str
    ) -> ParsedDocument:
        api_key = os.environ.get("KB_MISTRAL_API_KEY")
        if not api_key:
            raise ParseError(
                f"Mistral OCR called but KB_MISTRAL_API_KEY is not set "
                f"(file={file_id}); should not have been dispatched"
            )

        client = self._http_client or self._build_real_client()
        try:
            response = await client.post(
                _MISTRAL_OCR_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": (f"{file_id}.pdf", file_bytes, "application/pdf")},
                json=None,
            )
        except Exception as exc:
            raise ParseError(
                f"Mistral OCR HTTP call failed on file={file_id}: {exc}"
            ) from exc

        # Both httpx.Response and our MockResponse expose .status_code + .json()
        status_code = getattr(response, "status_code", 200)
        if status_code >= 400:
            body = getattr(response, "text", "") or ""
            raise ParseError(
                f"Mistral OCR returned HTTP {status_code} on file={file_id}: "
                f"{body[:500]}"
            )

        data = response.json()
        return self._parse_response(data, file_id=file_id)

    def _build_real_client(self) -> httpx.AsyncClient:
        # httpx.AsyncClient is meant to be reused. For Phase 2b we instantiate
        # per-call — Mistral OCR isn't called per-request in the hot path
        # (it's a worker job). A pool can be added in 2c if needed.
        return httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=300, write=60, pool=10))

    def _parse_response(
        self, data: dict[str, Any], *, file_id: str
    ) -> ParsedDocument:
        raw_pages = data.get("pages") or []
        if not raw_pages:
            raise ParseError(
                f"Mistral OCR response had no pages: file={file_id}"
            )

        pages: list[Page] = []
        for i, raw in enumerate(raw_pages, start=1):
            # Mistral's response: {index: 0|1|2..., markdown: str, images: [...]}
            text = raw.get("markdown") or raw.get("text") or ""
            pages.append(Page(
                page_number=i,  # 1-indexed per our raw_pages contract
                text=text,
                layout_json={
                    "mistral_index": raw.get("index"),
                    "images": raw.get("images", []),
                },
            ))
        return ParsedDocument(pages=pages)
