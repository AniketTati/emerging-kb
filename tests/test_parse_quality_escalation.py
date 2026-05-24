"""Phase 2c — quality-escalation logic unit tests.

RED at G3: imports `kb.parsers.quality.{score_parse_quality, should_escalate,
escalate_per_page, build_provenance}` which don't exist yet — lands at G4.

These are PURE-FUNCTION unit tests on the escalation decision logic. Full
worker integration (parse_file_impl wiring the escalation into a real
parsing→failed/parsed run) is covered by G5 `scripts/verify_phase_2c.sh`.

Spec: tests/specs/phase_2c.md §3 (decisions #10, #12).
"""

from __future__ import annotations

import pytest

from kb.parsers import Page, ParsedDocument


# ===========================================================================
# §5.6.1 decision #10 — signal 1: total chars == 0 → escalate (everything scanned)
# ===========================================================================


def test_escalate_on_empty_docling_output():
    """Docling extracted zero text across all pages → escalate whole doc.

    This catches the pure-scanned-PDF case where Docling+RapidOCR's
    auto-fallback failed (e.g., bad image quality, weird DPI).
    """
    from kb.parsers.quality import should_escalate

    parsed = ParsedDocument(pages=[
        Page(page_number=1, text="", layout_json={}),
        Page(page_number=2, text="", layout_json={}),
        Page(page_number=3, text="   \n  \t  ", layout_json={}),  # whitespace-only counts as empty
    ])

    escalate, reason = should_escalate(parsed)

    assert escalate is True
    assert "empty" in reason.lower() or "no text" in reason.lower()


# ===========================================================================
# §5.6.1 decision #10 — signal 2: printable_ratio < 0.7 → escalate (garbled)
# ===========================================================================


def test_escalate_on_garbled_output():
    """Docling extracted text but it's mostly non-printable garbage.

    This catches RapidOCR misfires where the model returns hallucinated
    control characters / mojibake on scanned input.
    """
    from kb.parsers.quality import should_escalate

    # Mix of valid text + heavy non-printable noise: ~30% printable.
    garbled = "Hello " + ("\x01\x02\x03\x04\x05\x06\x07\x08" * 20)
    parsed = ParsedDocument(pages=[
        Page(page_number=1, text=garbled, layout_json={}),
    ])

    escalate, reason = should_escalate(parsed)

    assert escalate is True
    assert "printable" in reason.lower() or "garbled" in reason.lower()


# ===========================================================================
# §5.6.1 decision #10 — signal 3: hybrid PDF → per-page escalation
# ===========================================================================


def test_escalate_per_page_for_hybrid_pdf():
    """One page has chars < 5 while peers have chars > 100 → escalate THAT
    PAGE only via Gemini OCR; keep the good pages from Docling.

    This is the realistic "scan inserted into a digital report" case.
    Returns the list of page numbers (1-indexed) that need re-OCR.
    """
    from kb.parsers.quality import escalate_per_page

    parsed = ParsedDocument(pages=[
        Page(page_number=1, text="This is a normal digital page with hundreds of characters " * 5, layout_json={}),
        Page(page_number=2, text="Another well-extracted page with plenty of normal text " * 5, layout_json={}),
        Page(page_number=3, text="", layout_json={}),  # bad — the inserted scan
        Page(page_number=4, text="Back to normal digital text content extending across the page " * 5, layout_json={}),
    ])

    bad_pages = escalate_per_page(parsed)

    assert bad_pages == [3], (
        f"expected page 3 to need escalation; got {bad_pages}"
    )


# ===========================================================================
# §5.6.1 decision #12 — provenance JSON shape
# ===========================================================================


def test_escalation_writes_provenance_json():
    """`build_provenance(...)` returns the dict that lands in
    `raw_pages.layout_json.provenance`. Shape per §5.6.1 #12."""
    from kb.parsers.quality import build_provenance

    # Case 1: no escalation, auto + Docling.
    prov = build_provenance(
        strategy="auto",
        forced_parser=None,
        tried=["docling"],
        chose="docling",
        reason="text_layer_present (avg=2730 chars/page over 3 pages)",
        quality_score=0.94,
    )
    assert prov == {
        "strategy": "auto",
        "forced_parser": None,
        "tried": ["docling"],
        "chose": "docling",
        "reason": "text_layer_present (avg=2730 chars/page over 3 pages)",
        "quality_score": 0.94,
    }

    # Case 2: escalation — both tried, Gemini chose.
    prov2 = build_provenance(
        strategy="auto",
        forced_parser=None,
        tried=["docling", "gemini_ocr"],
        chose="gemini_ocr",
        reason="docling output failed quality check: printable_ratio=0.42",
        quality_score=0.42,
    )
    assert prov2["tried"] == ["docling", "gemini_ocr"]
    assert prov2["chose"] == "gemini_ocr"
    assert "printable_ratio" in prov2["reason"]

    # Case 3: forced parser override.
    prov3 = build_provenance(
        strategy="auto",
        forced_parser="gemini",
        tried=["gemini_ocr"],
        chose="gemini_ocr",
        reason="caller override via ?parser=gemini",
        quality_score=None,
    )
    assert prov3["forced_parser"] == "gemini"
    assert prov3["chose"] == "gemini_ocr"
    # quality_score may be None when there's no Docling output to score
    assert "quality_score" in prov3
