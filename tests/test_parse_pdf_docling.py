"""Phase 2a — Docling parser tests (api_contracts §5.1 #4 — raw_pages content).

RED at G3: imports from `kb.parsers.docling_parser` land at G4.

Spec: tests/specs/phase_2a.md §4.3. Uses a tiny fixture PDF (lands at G4
under tests/fixtures/tiny.pdf). Pure parser-layer tests; no DB or HTTP.
"""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = pytest.mark.asyncio


_FIXTURE = Path(__file__).parent / "fixtures" / "tiny.pdf"


async def test_docling_parses_tiny_pdf_into_pages():
    from kb.parsers.docling_parser import DoclingParser

    pdf_bytes = _FIXTURE.read_bytes()
    parser = DoclingParser()
    doc = await parser.parse(pdf_bytes, file_id="test-file-id", workspace_id="ws")
    assert len(doc.pages) >= 1
    page = doc.pages[0]
    assert page.page_number == 1
    assert isinstance(page.text, str)
    assert len(page.text) > 0


async def test_docling_returns_text_and_layout():
    from kb.parsers.docling_parser import DoclingParser

    pdf_bytes = _FIXTURE.read_bytes()
    parser = DoclingParser()
    doc = await parser.parse(pdf_bytes, file_id="t", workspace_id="ws")
    page = doc.pages[0]
    # layout_json is dict (may be empty for super-minimal PDFs; just check shape)
    assert isinstance(page.layout_json, dict)


async def test_docling_raises_on_invalid_pdf():
    from kb.parsers import ParseError
    from kb.parsers.docling_parser import DoclingParser

    parser = DoclingParser()
    with pytest.raises(ParseError):
        await parser.parse(b"this is not a pdf", file_id="t", workspace_id="ws")
