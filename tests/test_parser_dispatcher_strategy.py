"""Phase 2c — strategy-aware parser dispatcher unit tests.

RED at G3: imports the widened `kb.parsers.select_parser_for(...)` factory
which doesn't exist yet — lands at G4 alongside `KB_PARSER_STRATEGY` env
var handling and pre-flight text-layer sniff integration.

Spec: tests/specs/phase_2c.md §3 (decisions #7, #13).
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

import pytest


_TINY_PDF_PATH = Path(__file__).parent / "fixtures" / "tiny.pdf"
_TINY_SCANNED_PATH = Path(__file__).parent / "fixtures" / "tiny_scanned.pdf"


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


# ===========================================================================
# §5.6.1 decision #7 — strategy = auto, digital PDF → Docling
# ===========================================================================


def test_select_parser_for_auto_digital_pdf_picks_docling():
    """`auto` + PDF with text layer → DoclingParser (via pre-flight sniff).

    tiny.pdf is a small fixture (~38 chars), so we lower
    KB_PDF_TEXT_LAYER_THRESHOLD to 10 to keep the sniff routing positive
    on the fixture. Production default of 50 chars/page is right for typical
    A4 pages (~3000 chars typed)."""
    from kb.parsers import select_parser_for
    from kb.parsers.docling_parser import DoclingParser

    pdf_bytes = _TINY_PDF_PATH.read_bytes()

    with _env(
        KB_PARSER_STRATEGY=None,
        KB_GEMINI_API_KEY="fake",
        KB_PDF_TEXT_LAYER_THRESHOLD="10",
    ):
        # Default strategy = auto when env unset.
        parser = select_parser_for(
            mime_type="application/pdf",
            magic_bytes=pdf_bytes[:8],
            file_bytes=pdf_bytes,
        )
    assert isinstance(parser, DoclingParser)


# ===========================================================================
# §5.6.1 decision #7 — strategy = auto, scanned PDF → GeminiOCRParser
# ===========================================================================


def test_select_parser_for_auto_scanned_pdf_picks_gemini_ocr():
    """`auto` + PDF without text layer → GeminiOCRParser (via pre-flight sniff)."""
    from kb.parsers import select_parser_for
    from kb.parsers.gemini_ocr_parser import GeminiOCRParser

    if not _TINY_SCANNED_PATH.exists():
        pytest.skip(
            "tiny_scanned.pdf not generated yet — G4 lands the fixture."
        )

    pdf_bytes = _TINY_SCANNED_PATH.read_bytes()

    with _env(KB_PARSER_STRATEGY="auto", KB_GEMINI_API_KEY="fake-key"):
        parser = select_parser_for(
            mime_type="application/pdf",
            magic_bytes=pdf_bytes[:8],
            file_bytes=pdf_bytes,
        )
    assert isinstance(parser, GeminiOCRParser)


# ===========================================================================
# §5.6.1 decision #7 — explicit `docling_first` always picks Docling
# ===========================================================================


def test_select_parser_for_docling_first_skips_sniff():
    """`docling_first` strategy bypasses sniff: even a scanned PDF routes
    to Docling. (Quality escalation kicks in later if Docling output is bad —
    that's a worker-level concern, not the dispatcher.)"""
    from kb.parsers import select_parser_for
    from kb.parsers.docling_parser import DoclingParser

    pdf_bytes = (
        _TINY_SCANNED_PATH.read_bytes() if _TINY_SCANNED_PATH.exists()
        else _TINY_PDF_PATH.read_bytes()
    )

    with _env(KB_PARSER_STRATEGY="docling_first", KB_GEMINI_API_KEY="fake"):
        parser = select_parser_for(
            mime_type="application/pdf",
            magic_bytes=pdf_bytes[:8],
            file_bytes=pdf_bytes,
        )
    assert isinstance(parser, DoclingParser)


# ===========================================================================
# §5.6.1 decision #13 — gemini_only without API key raises OCRConfigError
# ===========================================================================


def test_select_parser_for_gemini_only_without_key_raises():
    """`gemini_only` strategy requires KB_GEMINI_API_KEY — without it, the
    dispatcher must fail loudly with OCRConfigError so the worker writes
    parsing→failed with a descriptive error_class."""
    from kb.parsers.gemini_ocr_parser import OCRConfigError
    from kb.parsers import select_parser_for

    pdf_bytes = _TINY_PDF_PATH.read_bytes()

    with _env(KB_PARSER_STRATEGY="gemini_only", KB_GEMINI_API_KEY=None):
        with pytest.raises(OCRConfigError):
            select_parser_for(
                mime_type="application/pdf",
                magic_bytes=pdf_bytes[:8],
                file_bytes=pdf_bytes,
            )


# ===========================================================================
# §5.6.1 decision #7 — unknown strategy → ValueError
# ===========================================================================


def test_select_parser_for_unknown_strategy_raises():
    """Loud-fail on misconfig: KB_PARSER_STRATEGY=bogus raises ValueError
    immediately. (Loud-fail at startup beats silent fallback.)"""
    from kb.parsers import select_parser_for

    pdf_bytes = _TINY_PDF_PATH.read_bytes()

    with _env(KB_PARSER_STRATEGY="bogus_strategy"):
        with pytest.raises(ValueError, match="KB_PARSER_STRATEGY"):
            select_parser_for(
                mime_type="application/pdf",
                magic_bytes=pdf_bytes[:8],
                file_bytes=pdf_bytes,
            )
