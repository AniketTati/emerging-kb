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
    """Register Phase 2a's parsers into the global registry.

    Idempotent — re-call is a no-op (checks before registering).
    """
    from kb.parsers.docling_parser import DoclingParser

    # Don't double-register
    for existing in _GLOBAL_REGISTRY._parsers:  # noqa: SLF001 — internal access OK in same module
        if isinstance(existing, DoclingParser):
            return
    _GLOBAL_REGISTRY.register(DoclingParser())
