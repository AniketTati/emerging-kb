"""P5 — citations report a page RANGE (source → page range → excerpt, §2.4),
not just the chunk's first page."""

from __future__ import annotations

from kb.query.citations import _format_label, _page_span, _pdf_span_ref
from kb.query.rrf import Hit


def _hit(pages):
    return Hit(id="c1", kind="chunk", score=1.0, snippet="x",
              metadata={"source_page_numbers": pages, "file_id": "F1"})


def test_page_span_multi_page():
    assert _page_span({"source_page_numbers": [4, 5]}) == (4, 5)
    assert _page_span({"source_page_numbers": [7, 6, 8]}) == (6, 8)


def test_page_span_single_and_empty():
    assert _page_span({"source_page_numbers": [3]}) == (3, 3)
    assert _page_span({"page": 2}) == (2, 2)
    assert _page_span({}) == (None, None)


def test_ref_carries_range():
    ref = _pdf_span_ref(_hit([4, 5]), None)
    assert ref["page_start"] == 4 and ref["page_end"] == 5
    assert ref["page"] == 4  # back-compat first-page field kept


def test_label_renders_range_vs_single():
    assert _format_label(_hit([4, 5]), None, "pdf_span").endswith("pp. 4–5")
    assert _format_label(_hit([3]), None, "pdf_span").endswith("p. 3")
