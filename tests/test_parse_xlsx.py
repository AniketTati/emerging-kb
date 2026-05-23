"""Phase 2b — xlsx parser unit tests.

RED at G3: imports from `kb.parsers.xlsx_parser` land at G4.

Spec: tests/specs/phase_2b.md §4.1.
"""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = pytest.mark.asyncio


_FIXTURE = Path(__file__).parent / "fixtures" / "tiny.xlsx"


async def test_xlsx_parses_one_page_per_sheet():
    """tiny.xlsx has 2 sheets → ParsedDocument with 2 pages."""
    from kb.parsers.xlsx_parser import XLSXParser

    parser = XLSXParser()
    doc = await parser.parse(_FIXTURE.read_bytes(), file_id="t", workspace_id="ws")
    assert len(doc.pages) == 2
    assert doc.pages[0].page_number == 1
    assert doc.pages[1].page_number == 2


async def test_xlsx_text_is_tsv_with_sheet_header():
    """First page text starts with '# Sheet: <name>' header; rows are tab-separated."""
    from kb.parsers.xlsx_parser import XLSXParser

    doc = await XLSXParser().parse(
        _FIXTURE.read_bytes(), file_id="t", workspace_id="ws"
    )
    text = doc.pages[0].text
    assert text.startswith("# Sheet: Sheet1\n")
    # At least one tab somewhere in the body — TSV cells
    assert "\t" in text


async def test_xlsx_handles_empty_sheet():
    """Sheet2 of the fixture is (near-)empty — page exists but text is just the header
    (or empty). Asserts G1 decision #13: empty content still emits a row."""
    from kb.parsers.xlsx_parser import XLSXParser

    doc = await XLSXParser().parse(
        _FIXTURE.read_bytes(), file_id="t", workspace_id="ws"
    )
    # Page 2 exists regardless of content density
    assert doc.pages[1].page_number == 2
    # Allow either: empty text, or just the sheet-header line
    assert doc.pages[1].text == "" or doc.pages[1].text.startswith("# Sheet:")


async def test_xlsx_layout_includes_rows_cols_per_sheet():
    """layout_json carries sheet_name + row/col counts."""
    from kb.parsers.xlsx_parser import XLSXParser

    doc = await XLSXParser().parse(
        _FIXTURE.read_bytes(), file_id="t", workspace_id="ws"
    )
    layout = doc.pages[0].layout_json
    assert layout["sheet_name"] == "Sheet1"
    assert isinstance(layout.get("rows"), int)
    assert isinstance(layout.get("cols"), int)
    assert layout["rows"] >= 1
    assert layout["cols"] >= 1


async def test_xlsx_can_handle_pk_zip_magic():
    """When Content-Type is missing, ZIP magic identifies an xlsx (decision #6)."""
    from kb.parsers.xlsx_parser import XLSXParser

    parser = XLSXParser()
    # ZIP magic + xlsx is a ZIP under the hood
    assert parser.can_handle(
        mime_type="application/octet-stream",
        magic_bytes=b"PK\x03\x04stub",
    ) is True
    # Explicit xlsx mime also handled
    assert parser.can_handle(
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        magic_bytes=b"",
    ) is True
    # PDF magic must NOT be handled by the xlsx parser
    assert parser.can_handle(
        mime_type="application/pdf", magic_bytes=b"%PDF-1.4",
    ) is False
