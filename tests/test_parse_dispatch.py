"""Phase 2a — Parser Protocol + dispatcher unit tests.

RED at G3: imports from `kb.parsers` land at G4.

Spec: tests/specs/phase_2a.md §4.2. Pure unit tests — no DB, no MinIO,
no FastAPI. Tests the registration + routing logic of the dispatcher.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.asyncio


class FakePDFParser:
    """Test double: minimal Parser Protocol impl that records calls."""

    def __init__(self, name: str = "pdf_fake"):
        self.name = name
        self.called_with: bytes | None = None

    def can_handle(self, mime_type: str, magic_bytes: bytes) -> bool:
        return mime_type == "application/pdf" or magic_bytes.startswith(b"%PDF-")

    async def parse(self, file_bytes: bytes, *, file_id: str, workspace_id: str):
        from kb.parsers import ParsedDocument, Page
        self.called_with = file_bytes
        return ParsedDocument(pages=[
            Page(page_number=1, text="fake content", layout_json={}),
        ])


class FakeXLSXParser:
    def can_handle(self, mime_type: str, magic_bytes: bytes) -> bool:
        return mime_type in (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.ms-excel",
        )

    async def parse(self, file_bytes: bytes, *, file_id: str, workspace_id: str):
        from kb.parsers import ParsedDocument
        return ParsedDocument(pages=[])


# ===========================================================================
# Registration + routing
# ===========================================================================


async def test_register_and_dispatch_for_pdf_magic():
    """A registered parser is returned by dispatch() when MIME matches."""
    from kb.parsers import ParserRegistry

    registry = ParserRegistry()
    pdf = FakePDFParser()
    registry.register(pdf)

    chosen = registry.dispatch(mime_type="application/pdf", magic_bytes=b"%PDF-1.4")
    assert chosen is pdf


async def test_dispatch_falls_through_to_first_match():
    """Multiple parsers registered → dispatch picks the FIRST whose can_handle is True."""
    from kb.parsers import ParserRegistry

    registry = ParserRegistry()
    first = FakePDFParser(name="pdf_first")
    second = FakePDFParser(name="pdf_second")
    registry.register(first)
    registry.register(second)

    chosen = registry.dispatch(mime_type="application/pdf", magic_bytes=b"%PDF-")
    assert chosen is first


async def test_dispatch_raises_when_no_parser_matches():
    from kb.parsers import NoParserForMime, ParserRegistry

    registry = ParserRegistry()
    registry.register(FakePDFParser())
    with pytest.raises(NoParserForMime):
        registry.dispatch(mime_type="application/x-unknown", magic_bytes=b"xxxx")


async def test_dispatch_uses_mime_type_when_provided():
    from kb.parsers import ParserRegistry

    registry = ParserRegistry()
    registry.register(FakeXLSXParser())
    registry.register(FakePDFParser())  # second — order matters

    chosen = registry.dispatch(
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        magic_bytes=b"%PDF-",  # misleading magic; mime wins
    )
    assert isinstance(chosen, FakeXLSXParser)


async def test_dispatch_uses_magic_bytes_when_mime_missing():
    """When mime is None / empty, dispatcher falls back to magic-byte sniffing."""
    from kb.parsers import ParserRegistry

    registry = ParserRegistry()
    registry.register(FakePDFParser())

    chosen = registry.dispatch(mime_type="", magic_bytes=b"%PDF-1.4\nthe rest")
    assert isinstance(chosen, FakePDFParser)
