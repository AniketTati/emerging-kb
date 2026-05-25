"""Parser layer — Protocol + registry + strategy-aware dispatcher + exceptions.

Phase 2a. Each parser implements the `Parser` Protocol:
- `can_handle(mime_type, magic_bytes) -> bool`
- `async parse(file_bytes, *, file_id, workspace_id) -> ParsedDocument`

The `ParserRegistry` is populated at module load (in `kb.api.main` lifespan
or worker startup) with the registered parsers; `dispatch(mime, magic)`
picks the first whose `can_handle` returns True (preserved for non-PDF
mimes + back-compat).

Phase 2c adds **strategy-aware PDF dispatch** via `select_parser_for(...)`:
  - reads `KB_PARSER_STRATEGY ∈ {auto, docling_first, gemini_first, gemini_only}`
  - for PDFs under `auto`, invokes the text-layer sniff and routes:
      avg ≥ KB_PDF_TEXT_LAYER_THRESHOLD (default 50) chars/page → Docling
      else                                                       → GeminiOCRParser
  - `gemini_only` + no `KB_GEMINI_API_KEY` raises `OCRConfigError`
  - non-PDF mimes fall through to the registry (first-match-wins, unchanged)

Per build_tracker §5.6.1 #7, #8, #13.
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
                                  when KB_MISTRAL_API_KEY is unset; never wins
                                  dispatch under registry-only ordering.
    5. GeminiOCRParser          — Phase 2c — application/pdf BUT only routes
                                  via `select_parser_for(...)` strategy logic
                                  (registry order would still pick Docling first).

    Idempotent — re-call is a no-op (checks before registering).
    """
    from kb.parsers.docling_parser import DoclingParser
    from kb.parsers.email_parser import EmailParser
    from kb.parsers.mistral_ocr_parser import MistralOCRParser
    from kb.parsers.text_parser import TextParser
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
    _GLOBAL_REGISTRY.register(TextParser())
    _GLOBAL_REGISTRY.register(MistralOCRParser())


# ---------------------------------------------------------------------------
# Phase 2c — strategy-aware PDF dispatch
# ---------------------------------------------------------------------------


_VALID_STRATEGIES = {"auto", "docling_first", "gemini_first", "gemini_only"}
_VALID_FORCED_PARSERS = {None, "auto", "docling", "gemini"}


def select_parser_for(
    *,
    mime_type: str,
    magic_bytes: bytes,
    file_bytes: bytes,
    forced_parser: str | None = None,
) -> "Parser":
    """Strategy + caller-override aware parser selection.

    For PDFs:
      1. If `forced_parser` is set (caller override per §5.6.1 #11), honor it.
         Values: `docling`, `gemini`, `auto`, or None.
      2. Otherwise read `KB_PARSER_STRATEGY` env (default `auto`).
      3. `auto`: run pre-flight text-layer sniff; route by result.
      4. `docling_first`: always Docling (worker may escalate on bad quality).
      5. `gemini_first` / `gemini_only`: always Gemini OCR. `gemini_only` +
         no KB_GEMINI_API_KEY raises OCRConfigError so the worker writes
         parsing→failed with a descriptive error_class.

    For non-PDF mimes: falls through to the registry's first-match dispatch
    (unchanged from Phase 2a/2b behavior).

    Per §5.6.1 #7, #8, #11, #13.
    """
    # Non-PDF mimes use the existing registry-based dispatch unchanged.
    is_pdf = mime_type == "application/pdf" or magic_bytes.startswith(b"%PDF-")
    if not is_pdf:
        return _GLOBAL_REGISTRY.dispatch(
            mime_type=mime_type, magic_bytes=magic_bytes
        )

    import os
    from kb.parsers.docling_parser import DoclingParser
    from kb.parsers.gemini_ocr_parser import (
        GeminiOCRParser,
        OCRConfigError,
    )

    def _make_gemini(*, strict: bool) -> "Parser":
        """Build a GeminiOCRParser; if no key:
          - strict=True (explicit user opt-in: gemini_only / ?parser=gemini)
            → raise OCRConfigError so the worker writes parsing→failed.
          - strict=False (auto-routed via sniff): fall back to Docling so the
            pipeline keeps moving. Provenance metadata still records what we
            wanted vs. what we did."""
        api_key = os.environ.get("KB_GEMINI_API_KEY")
        if not api_key:
            if strict:
                raise OCRConfigError(
                    "Gemini OCR was explicitly requested but "
                    "KB_GEMINI_API_KEY is unset"
                )
            # Soft fallback for sniff-routed picks.
            return DoclingParser()
        return GeminiOCRParser(api_key=api_key)

    # Step 1: caller override (explicit → strict if user picked gemini).
    if forced_parser is not None and forced_parser != "auto":
        if forced_parser not in _VALID_FORCED_PARSERS:
            raise ValueError(
                f"invalid forced_parser={forced_parser!r}; "
                f"expected one of {_VALID_FORCED_PARSERS}"
            )
        if forced_parser == "docling":
            return DoclingParser()
        if forced_parser == "gemini":
            return _make_gemini(strict=True)

    # Step 2: strategy env.
    strategy = (os.environ.get("KB_PARSER_STRATEGY") or "auto").lower()
    if strategy not in _VALID_STRATEGIES:
        raise ValueError(
            f"KB_PARSER_STRATEGY={strategy!r} is invalid; "
            f"expected one of {_VALID_STRATEGIES}"
        )

    if strategy == "docling_first":
        return DoclingParser()

    if strategy == "gemini_first":
        # `gemini_first` is opportunistic: prefer Gemini but allow Docling
        # fallback when no key is present.
        return _make_gemini(strict=False)

    if strategy == "gemini_only":
        # `gemini_only` is an explicit operator opt-out of Docling — must
        # fail loudly when the key is missing.
        return _make_gemini(strict=True)

    # strategy == "auto": pre-flight sniff. Soft fallback if Gemini key
    # is unset — the pipeline can still complete via Docling.
    from kb.parsers.text_layer_sniff import sniff_pdf_text_layer
    result = sniff_pdf_text_layer(file_bytes)
    if result.has_text_layer:
        return DoclingParser()
    return _make_gemini(strict=False)
