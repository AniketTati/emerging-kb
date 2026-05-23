"""Parser layer — Protocol + registry + dispatcher + base exceptions.

Phase 2a. Each parser implements the `Parser` Protocol:
- `can_handle(mime_type, magic_bytes) -> bool`
- `async parse(file_bytes, *, file_id, workspace_id) -> ParsedDocument`

The `ParserRegistry` is populated at module load (in `kb.api.main` lifespan
or worker startup) with the registered parsers; `dispatch(mime, magic)`
picks the first whose `can_handle` returns True.

Phase 2a registers only `DoclingParser` for `application/pdf`. Phase 2b
will add xlsx, email, and Mistral OCR parsers via the same protocol.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Models — ParsedDocument + Page
# ---------------------------------------------------------------------------


class Page(BaseModel):
    """One page extracted from a source document."""

    page_number: int
    text: str
    layout_json: dict[str, Any] = {}


class ParsedDocument(BaseModel):
    """Output of a Parser.parse() call — the unit a worker writes into raw_pages."""

    pages: list[Page]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ParseError(Exception):
    """A parser refused or failed to parse the input. Worker catches this
    and writes a parsing→failed lifecycle event."""


class NoParserForMime(Exception):
    """The dispatcher couldn't find a registered parser for the given
    mime_type / magic_bytes combination."""


class UnsupportedMediaTypeError(Exception):
    """The Phase 2a upload endpoint rejects this mime_type (returns 415)."""


class PayloadTooLargeError(Exception):
    """The upload's Content-Length exceeded KB_MAX_UPLOAD_BYTES (413)."""


# ---------------------------------------------------------------------------
# Parser Protocol
# ---------------------------------------------------------------------------


class Parser(Protocol):
    def can_handle(self, mime_type: str, magic_bytes: bytes) -> bool: ...

    async def parse(
        self, file_bytes: bytes, *, file_id: str, workspace_id: str
    ) -> ParsedDocument: ...


# ---------------------------------------------------------------------------
# Registry + dispatcher
# ---------------------------------------------------------------------------


class ParserRegistry:
    """Ordered list of parsers. Dispatch picks the first whose can_handle
    returns True for the given mime + magic bytes.

    Order matters: more-specific parsers should be registered before
    fall-back parsers (e.g., Docling before a generic PDF VLM fallback).
    """

    def __init__(self) -> None:
        self._parsers: list[Parser] = []

    def register(self, parser: Parser) -> None:
        self._parsers.append(parser)

    def dispatch(self, *, mime_type: str, magic_bytes: bytes) -> Parser:
        for p in self._parsers:
            if p.can_handle(mime_type, magic_bytes):
                return p
        raise NoParserForMime(
            f"no registered parser for mime={mime_type!r} "
            f"magic={magic_bytes[:8]!r}"
        )

    def __len__(self) -> int:
        return len(self._parsers)


# Process-wide singleton (populated at app/worker startup).
_GLOBAL_REGISTRY = ParserRegistry()


def global_registry() -> ParserRegistry:
    return _GLOBAL_REGISTRY


def register_default_parsers() -> None:
    """Register the default parsers into the global registry.

    Order matters — `ParserRegistry.dispatch()` returns the FIRST parser
    whose `can_handle()` is True. Registration order:

    1. DoclingParser            — Phase 2a — application/pdf
    2. XLSXParser               — Phase 2b — application/vnd.openxml...sheet + ZIP magic
    3. EmailParser              — Phase 2b — message/rfc822 + header magic
    4. MistralOCRParser         — Phase 2b — application/pdf BUT self-disabled
                                  when KB_MISTRAL_API_KEY is unset; even when set,
                                  Docling wins dispatch by registration order
                                  (Phase 2c will add a force-parser route).

    Idempotent — re-call is a no-op (checks before registering).
    """
    from kb.parsers.docling_parser import DoclingParser
    from kb.parsers.email_parser import EmailParser
    from kb.parsers.mistral_ocr_parser import MistralOCRParser
    from kb.parsers.xlsx_parser import XLSXParser

    # Don't double-register — keyed on parser class.
    existing_types = {
        type(p) for p in _GLOBAL_REGISTRY._parsers  # noqa: SLF001 — same module
    }
    if DoclingParser in existing_types:
        return  # already initialized

    _GLOBAL_REGISTRY.register(DoclingParser())
    _GLOBAL_REGISTRY.register(XLSXParser())
    _GLOBAL_REGISTRY.register(EmailParser())
    _GLOBAL_REGISTRY.register(MistralOCRParser())
