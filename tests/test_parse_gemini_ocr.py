"""Phase 2c — GeminiOCRParser adapter unit tests (no DB, no real Gemini API).

RED at G3: imports `kb.parsers.gemini_ocr_parser.GeminiOCRParser` which
doesn't exist yet — lands at G4.

Spec: tests/specs/phase_2c.md §3.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import contextmanager
from io import BytesIO

import pytest


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Test infrastructure — mirrors _MockGeminiClient in
# test_contextualization_gemini_unit.py (3b-bis), adapted for vision input.
# Each generate_content call carries an image Part; we return per-page text.
# ---------------------------------------------------------------------------


@contextmanager
def _env(**kwargs):
    prior = {k: os.environ.get(k) for k in kwargs}
    for k, v in kwargs.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in prior.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _build_mock_ocr_response(text: str):
    """Shape: google.genai.types.GenerateContentResponse with one text part."""
    part = type("Part", (), {"text": text})()
    content = type("Content", (), {"parts": [part], "role": "model"})()
    candidate = type("Candidate", (), {"content": content, "finish_reason": "STOP"})()
    usage = type("UsageMetadata", (), {
        "prompt_token_count": 258,  # Gemini's flat per-image rate
        "candidates_token_count": max(len(text.split()), 1),
        "total_token_count": 258 + len(text.split()),
    })()
    return type("GenerateContentResponse", (), {
        "candidates": [candidate],
        "usage_metadata": usage,
        "prompt_feedback": None,
        "text": text,
    })()


class _MockGeminiVisionClient:
    """Records every generate_content call's kwargs + tracks in-flight count
    so the Semaphore(4) concurrency cap can be asserted."""

    def __init__(
        self,
        *,
        per_page_text: list[str] | None = None,
        raise_exc: Exception | None = None,
        per_call_delay: float = 0.0,
    ) -> None:
        self.calls: list[dict] = []  # kwargs of every call
        self._per_page_text = per_page_text or ["OCR page text"]
        self._raise_exc = raise_exc
        self._per_call_delay = per_call_delay
        self._inflight = 0
        self.max_inflight = 0

        client_self = self

        class _Models:
            async def generate_content(self, **kwargs):
                client_self._inflight += 1
                client_self.max_inflight = max(
                    client_self.max_inflight, client_self._inflight
                )
                try:
                    if client_self._per_call_delay > 0:
                        await asyncio.sleep(client_self._per_call_delay)
                    client_self.calls.append(kwargs)
                    if client_self._raise_exc:
                        raise client_self._raise_exc
                    idx = len(client_self.calls) - 1
                    text = client_self._per_page_text[
                        idx % len(client_self._per_page_text)
                    ]
                    return _build_mock_ocr_response(text)
                finally:
                    client_self._inflight -= 1

        self.aio = type("Aio", (), {"models": _Models()})()


# Minimal valid PDF bytes — 1 page, no text layer. We don't actually invoke
# pypdfium2 in unit tests (mocked at the render boundary); these bytes only
# need to be acceptable to the parser's input gate.
_MINIMAL_PDF = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R 4 0 R 5 0 R]/Count 3>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
    b"4 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
    b"5 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
    b"xref\n0 6\n0000000000 65535 f\ntrailer<</Size 6/Root 1 0 R>>\nstartxref\n9\n%%EOF\n"
)


# ===========================================================================
# §5.6.1 decision #5 — prompt shape (image input + OCR instruction)
# ===========================================================================


async def test_gemini_ocr_sends_image_with_correct_prompt():
    """Each per-page call carries a PIL.Image (or image Part) + the verbatim
    OCR prompt from §5.6.1 #5."""
    from kb.parsers.gemini_ocr_parser import GeminiOCRParser

    mock = _MockGeminiVisionClient(per_page_text=["page 1 text"])
    parser = GeminiOCRParser(client=mock, api_key="fake")

    doc = await parser.parse(_MINIMAL_PDF, file_id="t", workspace_id="ws")

    assert len(mock.calls) >= 1, "expected at least one Gemini call"
    first = mock.calls[0]
    # contents must include some payload — string prompt + image part. The
    # exact wire shape can vary (Part vs dict vs string list); we assert
    # the OCR-instruction substring appears somewhere in the request.
    serialized = repr(first.get("contents", "")) + repr(first.get("config", ""))
    assert "Extract ALL text" in serialized or "extract" in serialized.lower(), (
        f"expected OCR instruction in request; got: {serialized[:300]}"
    )
    assert len(doc.pages) >= 1
    assert doc.pages[0].text == "page 1 text"


# ===========================================================================
# §5.6.1 decision #6 — per-page concurrency cap (asyncio.Semaphore(4))
# ===========================================================================


async def test_gemini_ocr_caps_concurrent_calls_at_4():
    """Render 8 pages with a per-call delay; assert max_inflight ≤ 4."""
    from kb.parsers.gemini_ocr_parser import GeminiOCRParser

    # 8-page PDF — generate 8 page bytes so pypdfium2 produces 8 pages.
    pdf_with_8_pages = (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids["
        + b" ".join(f"{i} 0 R".encode() for i in range(3, 11))
        + b"]/Count 8>>endobj\n"
        + b"".join(
            f"{i} 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n".encode()
            for i in range(3, 11)
        )
        + b"xref\n0 11\n0000000000 65535 f\ntrailer<</Size 11/Root 1 0 R>>\nstartxref\n9\n%%EOF\n"
    )

    mock = _MockGeminiVisionClient(
        per_page_text=[f"page {i}" for i in range(1, 9)],
        per_call_delay=0.05,  # 50ms per call — gives the semaphore time to bite
    )
    parser = GeminiOCRParser(client=mock, api_key="fake")

    await parser.parse(pdf_with_8_pages, file_id="t8", workspace_id="ws")

    assert len(mock.calls) == 8, f"expected 8 calls for 8 pages; got {len(mock.calls)}"
    assert mock.max_inflight <= 4, (
        f"expected max_inflight ≤ 4 (Semaphore cap); got {mock.max_inflight}"
    )


# ===========================================================================
# §5.6.1 decision #1 + #9 — model literal + KB_OCR_MODEL override
# ===========================================================================


async def test_gemini_ocr_uses_configurable_model():
    """Default `gemini-2.5-flash`; KB_OCR_MODEL overrides per parser instance."""
    from kb.parsers.gemini_ocr_parser import GeminiOCRParser

    mock = _MockGeminiVisionClient()

    with _env(KB_OCR_MODEL=None):
        parser = GeminiOCRParser(client=mock, api_key="fake")
        await parser.parse(_MINIMAL_PDF, file_id="t", workspace_id="ws")
        assert mock.calls[0]["model"] == "gemini-2.5-flash"

    mock2 = _MockGeminiVisionClient()
    with _env(KB_OCR_MODEL="gemini-2.5-pro"):
        parser = GeminiOCRParser(client=mock2, api_key="fake")
        await parser.parse(_MINIMAL_PDF, file_id="t", workspace_id="ws")
        assert mock2.calls[0]["model"] == "gemini-2.5-pro"


# ===========================================================================
# §5.6.1 decision #13 — empty-key error
# ===========================================================================


async def test_gemini_ocr_parser_no_key_raises():
    """Instantiating without api_key or client must raise OCRConfigError."""
    from kb.parsers.gemini_ocr_parser import GeminiOCRParser, OCRConfigError

    with _env(KB_GEMINI_API_KEY=None):
        with pytest.raises(OCRConfigError):
            GeminiOCRParser(api_key=None, client=None)


# ===========================================================================
# Error path — Gemini API exception surfaces as ParseError
# ===========================================================================


async def test_gemini_ocr_api_error_raises_parse_error():
    """Underlying google-genai exceptions surface as ParseError so the
    worker's parsing→failed transition fires cleanly."""
    from kb.parsers import ParseError
    from kb.parsers.gemini_ocr_parser import GeminiOCRParser

    mock = _MockGeminiVisionClient(raise_exc=RuntimeError("gemini 500 internal"))
    parser = GeminiOCRParser(client=mock, api_key="fake")

    with pytest.raises(ParseError):
        await parser.parse(_MINIMAL_PDF, file_id="t", workspace_id="ws")


# ===========================================================================
# Per-page rendering — pages list reflects PDF page count
# ===========================================================================


async def test_gemini_ocr_renders_per_page():
    """3-page PDF → 3 ParsedDocument.pages, each with `page_number` 1/2/3
    and its own OCR text from a separate Gemini call."""
    from kb.parsers.gemini_ocr_parser import GeminiOCRParser

    mock = _MockGeminiVisionClient(per_page_text=["page1", "page2", "page3"])
    parser = GeminiOCRParser(client=mock, api_key="fake")

    doc = await parser.parse(_MINIMAL_PDF, file_id="t3", workspace_id="ws")

    assert len(doc.pages) == 3
    assert doc.pages[0].page_number == 1
    assert doc.pages[1].page_number == 2
    assert doc.pages[2].page_number == 3
    page_texts = {p.text for p in doc.pages}
    assert page_texts == {"page1", "page2", "page3"}
    assert len(mock.calls) == 3, f"expected 3 calls for 3 pages; got {len(mock.calls)}"
