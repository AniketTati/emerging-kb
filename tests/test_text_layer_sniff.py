"""Phase 2c — text-layer sniff unit tests.

RED at G3: imports `kb.parsers.text_layer_sniff.sniff_pdf_text_layer` which
doesn't exist yet — lands at G4.

Spec: tests/specs/phase_2c.md §3 (decisions #8, #9).
"""

from __future__ import annotations

from pathlib import Path

import pytest


_TINY_PDF_PATH = Path(__file__).parent / "fixtures" / "tiny.pdf"
_TINY_SCANNED_PATH = Path(__file__).parent / "fixtures" / "tiny_scanned.pdf"


# ===========================================================================
# §5.6.1 decision #8 — digital PDF returns high text density (≥ 50 chars/page)
# ===========================================================================


def test_sniff_digital_pdf_returns_high_density():
    """tiny.pdf is a digital PDF with extractable text. The fixture is
    intentionally tiny (~38 chars), so we pass threshold=10 to assert the
    sniff's positive path. Production default (50) is right for typical
    A4 pages (~3000 chars typed)."""
    from kb.parsers.text_layer_sniff import sniff_pdf_text_layer

    result = sniff_pdf_text_layer(_TINY_PDF_PATH.read_bytes(), threshold=10)

    assert result.page_count >= 1
    assert result.avg_chars_per_page >= 10, (
        f"digital PDF should have extractable chars; got {result.avg_chars_per_page}"
    )
    assert result.has_text_layer is True


# ===========================================================================
# §5.6.1 decision #8 — scanned PDF returns ~0 text density
# ===========================================================================


def test_sniff_scanned_pdf_returns_zero_density():
    """tiny_scanned.pdf is a synthetic image-only PDF (generated at G4 from
    tiny.pdf via PIL.Image → PDF re-encode with no text layer). Sniff should
    return avg_chars_per_page < 50 → routes to OCR under `auto` strategy."""
    from kb.parsers.text_layer_sniff import sniff_pdf_text_layer

    if not _TINY_SCANNED_PATH.exists():
        pytest.skip(
            "tiny_scanned.pdf not generated yet — run "
            "scripts/make_tiny_scanned.py to produce it (G4 work)."
        )

    result = sniff_pdf_text_layer(_TINY_SCANNED_PATH.read_bytes())

    assert result.page_count >= 1
    assert result.avg_chars_per_page < 50, (
        f"scanned PDF should have <50 chars/page; got {result.avg_chars_per_page}"
    )
    assert result.has_text_layer is False


# ===========================================================================
# §5.6.1 decision #9 — sniff bounded to first 10 pages for cost
# ===========================================================================


def test_sniff_caps_at_10_pages_for_large_docs():
    """A 100-page PDF should be sniffed against the first 10 pages only —
    cost cap (~10ms/page). The reported page_count reflects the actual
    PDF (100), but the avg_chars_per_page is computed over the sampled
    subset (first 10)."""
    from kb.parsers.text_layer_sniff import sniff_pdf_text_layer

    # Build a synthetic 100-page PDF skeleton. We don't need actual text
    # content for this test — we're asserting the sampling-cap behavior,
    # which the function should expose via its return shape.
    kids = b" ".join(f"{i} 0 R".encode() for i in range(3, 103))
    pages_body = b"".join(
        f"{i} 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n".encode()
        for i in range(3, 103)
    )
    pdf_100_pages = (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[" + kids + b"]/Count 100>>endobj\n"
        + pages_body
        + b"xref\n0 103\n0000000000 65535 f\ntrailer<</Size 103/Root 1 0 R>>\nstartxref\n9\n%%EOF\n"
    )

    result = sniff_pdf_text_layer(pdf_100_pages)

    # page_count reflects the real PDF
    assert result.page_count == 100, (
        f"expected page_count=100 for 100-page PDF; got {result.page_count}"
    )
    # sampling cap means we never iterate all 100 pages — exposed via
    # `pages_sampled` field on the SniffResult
    assert hasattr(result, "pages_sampled"), (
        "SniffResult must expose `pages_sampled` for cost auditing"
    )
    assert result.pages_sampled <= 10, (
        f"sniff must cap at 10 pages; sampled {result.pages_sampled}"
    )
