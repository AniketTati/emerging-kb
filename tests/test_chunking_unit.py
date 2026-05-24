"""Phase 3a — chunker unit tests (pure-function, no DB).

RED at G3: imports from `kb.chunking` land at G4.

Spec: tests/specs/phase_3a.md §4.1.
"""

from __future__ import annotations

import hashlib

import pytest


def _make_page(page_number: int, text: str):
    """Build the minimal RawPage shape the chunker accepts.

    The chunker only reads page_number + text. Tests pass through whatever
    `kb.chunking` exposes as its input shape at G4 — likely the `Page`
    pydantic from `kb.parsers` reused, since raw_pages are stored by that
    same shape.
    """
    from kb.parsers import Page  # G4
    return Page(page_number=page_number, text=text, layout_json={})


def test_chunk_pages_single_short_page_returns_one_chunk():
    from kb.chunking import chunk_pages

    page = _make_page(1, "hello world. " * 5)  # ~25 tokens
    chunks = chunk_pages([page], budget_tokens=200, overlap_tokens=20)
    assert len(chunks) == 1
    assert chunks[0].text == page.text
    assert chunks[0].source_page_numbers == [1]


def test_chunk_pages_single_page_exceeds_budget_splits_at_paragraph_break():
    from kb.chunking import chunk_pages

    # Build a page with a clear paragraph break around the 200-token mark.
    para1 = "alpha beta gamma delta epsilon zeta eta theta. " * 30
    para2 = "iota kappa lambda mu nu xi omicron pi. " * 30
    text = para1 + "\n\n" + para2
    page = _make_page(1, text)

    chunks = chunk_pages([page], budget_tokens=200, overlap_tokens=20)
    assert len(chunks) >= 2, "single huge page should split"
    # First chunk should END at the paragraph boundary (it should contain
    # most of para1 and stop at-or-before the "\n\n").
    assert "\n\n" not in chunks[0].text or chunks[0].text.endswith("\n\n") or chunks[0].text.rstrip().endswith(para1.rstrip()[-30:])


def test_chunk_pages_small_pages_join_until_budget():
    from kb.chunking import chunk_pages

    # 5 pages of ~20 tokens each → all under joined budget of 200.
    pages = [_make_page(i, f"page {i} text here. " * 4) for i in range(1, 6)]
    chunks = chunk_pages(pages, budget_tokens=200, overlap_tokens=20)
    # 5 small pages joined should fit in 1-2 chunks, not 5.
    assert len(chunks) <= 2


def test_chunk_pages_source_page_numbers_tracks_all_contributing_pages():
    from kb.chunking import chunk_pages

    pages = [_make_page(i, f"page {i} short. ") for i in range(1, 4)]
    chunks = chunk_pages(pages, budget_tokens=200, overlap_tokens=0)
    # All three small pages should join into one chunk.
    assert len(chunks) == 1
    assert chunks[0].source_page_numbers == [1, 2, 3]


def test_chunk_pages_chunk_index_starts_at_zero_and_increments():
    from kb.chunking import chunk_pages

    # Force >1 chunk via over-budget single page.
    page = _make_page(1, "filler word " * 300)
    chunks = chunk_pages([page], budget_tokens=100, overlap_tokens=10)
    indices = [c.chunk_index for c in chunks]
    assert indices == list(range(len(chunks)))
    assert indices[0] == 0


def test_chunk_pages_overlap_preserves_tail_of_prior_chunk():
    from kb.chunking import chunk_pages

    # Build text without any paragraph breaks so splits happen on token boundaries.
    page = _make_page(1, "one two three four five six seven eight nine ten " * 30)
    chunks = chunk_pages([page], budget_tokens=100, overlap_tokens=30)
    assert len(chunks) >= 2

    # The last ~30 tokens of chunks[0] should appear at the head of chunks[1].
    tail_of_first = chunks[0].text.split()[-15:]  # tokens != words but close enough for substring test
    head_of_second = chunks[1].text.split()[:30]
    overlap_word = tail_of_first[-1]
    assert overlap_word in head_of_second, (
        f"expected '{overlap_word}' from end of chunk 0 to appear at start of chunk 1"
    )


def test_chunk_pages_xlsx_huge_sheet_splits_on_row_boundary():
    from kb.chunking import chunk_pages

    # Simulate xlsx sheet text: cells \t-separated, rows \n-separated.
    rows = [f"row{i}cell1\trow{i}cell2\trow{i}cell3" for i in range(50)]
    sheet_text = "# Sheet: Big\n" + "\n".join(rows)
    page = _make_page(1, sheet_text)

    chunks = chunk_pages([page], budget_tokens=100, overlap_tokens=10)
    assert len(chunks) >= 2

    # Every chunk except possibly the last should end on a newline (row boundary),
    # never mid-row (never inside a `\t`-separated cell).
    for c in chunks[:-1]:
        # Either ends on \n OR doesn't end in the middle of a tab-separated row.
        assert c.text.endswith("\n") or "\t" not in c.text.splitlines()[-1], (
            f"chunk ended mid-row: ...{c.text[-50:]!r}"
        )


def test_chunk_pages_empty_pages_list_raises_chunking_error():
    from kb.chunking import ChunkingError, chunk_pages

    with pytest.raises(ChunkingError):
        chunk_pages([], budget_tokens=200, overlap_tokens=20)


def test_chunk_pages_content_sha_matches_sha256_of_text():
    from kb.chunking import chunk_pages

    page = _make_page(1, "test content for sha verification.")
    chunks = chunk_pages([page], budget_tokens=200, overlap_tokens=20)
    for c in chunks:
        expected = hashlib.sha256(c.text.encode("utf-8")).hexdigest()
        assert c.content_sha == expected
