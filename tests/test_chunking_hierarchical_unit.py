"""Hierarchical chunker unit tests — pure function, no DB, no LLM."""

from __future__ import annotations

import pytest

from kb.chunking import (
    chunk_pages,
    chunk_pages_hierarchical,
    chunk_pages_message_per_leaf,
    chunk_pages_row_per_leaf,
    ChunkingError,
)
from kb.parsers import Page


def _make_pages(*texts: str) -> list[Page]:
    return [
        Page(page_number=i + 1, text=t, layout_json={})
        for i, t in enumerate(texts)
    ]


# ===========================================================================
# chunk_pages_hierarchical
# ===========================================================================


def test_hierarchical_small_doc_produces_one_per_level():
    """A doc small enough to fit in the smallest chunk size emits one
    chunk at each level (root → mid → leaf), all containing the same
    content but distinguishable by node_level + parent linkage."""
    pages = _make_pages("Hello world. This is a tiny doc.")
    chunks = chunk_pages_hierarchical(pages)

    by_level: dict[int, int] = {}
    for c in chunks:
        by_level.setdefault(c.node_level, 0)
        by_level[c.node_level] += 1

    # Three levels, one chunk each.
    assert sorted(by_level.keys()) == [0, 1, 2]
    assert all(v == 1 for v in by_level.values())

    # Root has no parent; leaves do.
    root = next(c for c in chunks if c.node_level == 2)
    leaf = next(c for c in chunks if c.node_level == 0)
    assert root.parent_parser_node_id is None
    assert leaf.parent_parser_node_id is not None


def test_hierarchical_larger_doc_emits_tree():
    """A doc with many sentences produces a real tree: multiple leaves,
    several mids, fewer roots."""
    text = " ".join(
        f"Sentence {i} with enough words to fill space." for i in range(200)
    )
    chunks = chunk_pages_hierarchical(
        _make_pages(text), chunk_sizes=(512, 128, 64),
    )

    by_level: dict[int, int] = {}
    for c in chunks:
        by_level.setdefault(c.node_level, 0)
        by_level[c.node_level] += 1

    # Fewer roots than mids than leaves — the tree shape.
    assert by_level[2] <= by_level[1] <= by_level[0]
    assert by_level[0] > 5  # plenty of leaves


def test_hierarchical_chunks_are_topologically_ordered():
    """Chunks appear with parents BEFORE children — the worker iterates
    in order and resolves child→parent FKs from a running map."""
    pages = _make_pages("Test content. " * 100)
    chunks = chunk_pages_hierarchical(pages, chunk_sizes=(256, 64, 32))

    seen_node_ids: set[str] = set()
    for c in chunks:
        if c.parent_parser_node_id is not None:
            # If this chunk has a parent, the parent must have appeared first.
            assert c.parent_parser_node_id in seen_node_ids, (
                f"chunk_index={c.chunk_index} level={c.node_level} "
                f"references unseen parent {c.parent_parser_node_id[:8]}"
            )
        seen_node_ids.add(c.parser_node_id)


def test_hierarchical_raises_on_empty_pages():
    with pytest.raises(ChunkingError):
        chunk_pages_hierarchical([])


def test_hierarchical_preserves_page_numbers():
    """Page markers survive chunking; each chunk's source_page_numbers
    reflects which raw page(s) it came from."""
    pages = _make_pages(
        "Content on page one.",
        "Content on page two.",
        "Content on page three.",
    )
    chunks = chunk_pages_hierarchical(
        pages, chunk_sizes=(64, 32, 16),
    )
    # Every chunk's source_page_numbers should be a subset of {1, 2, 3}.
    for c in chunks:
        assert all(p in {1, 2, 3} for p in c.source_page_numbers), (
            f"unexpected page in source_page_numbers: {c.source_page_numbers}"
        )


# ===========================================================================
# chunk_pages_row_per_leaf
# ===========================================================================


def test_row_per_leaf_each_row_becomes_one_leaf():
    """Each non-empty line of input becomes a level-0 leaf chunk; the
    file gets a single root and one or more mid groupings."""
    pages = _make_pages(
        "Date,Amount,Description\n"
        "2024-01-15,4.50,Coffee\n"
        "2024-01-16,250.00,Rent\n"
        "2024-01-17,45.00,Gas"
    )
    chunks = chunk_pages_row_per_leaf(pages, rows_per_mid=2)

    leaves = [c for c in chunks if c.node_level == 0]
    mids = [c for c in chunks if c.node_level == 1]
    roots = [c for c in chunks if c.node_level == 2]

    assert len(leaves) == 4  # 4 non-empty lines
    assert len(roots) == 1
    assert len(mids) == 2     # 4 leaves / 2 rows_per_mid

    # Leaf texts match the original rows.
    leaf_texts = {c.text for c in leaves}
    assert "2024-01-15,4.50,Coffee" in leaf_texts
    assert "2024-01-16,250.00,Rent" in leaf_texts

    # Every leaf has a mid parent; every mid has the root as parent.
    for leaf in leaves:
        parent_mid = next(
            (m for m in mids if m.parser_node_id == leaf.parent_parser_node_id),
            None,
        )
        assert parent_mid is not None
    for mid in mids:
        assert mid.parent_parser_node_id == roots[0].parser_node_id


def test_row_per_leaf_raises_on_empty():
    with pytest.raises(ChunkingError):
        chunk_pages_row_per_leaf([])


# ===========================================================================
# chunk_pages_message_per_leaf
# ===========================================================================


def test_message_per_leaf_splits_on_headers():
    """An email thread with `From:` / `Sent:` headers gets split into
    one leaf per message."""
    pages = _make_pages(
        "From: alice@example.com\n"
        "Sent: 2024-01-15\n"
        "Subject: Hi\n"
        "Body of first message.\n\n"
        "From: bob@example.com\n"
        "Sent: 2024-01-16\n"
        "Subject: Re: Hi\n"
        "Reply text from bob."
    )
    chunks = chunk_pages_message_per_leaf(pages)
    leaves = [c for c in chunks if c.node_level == 0]
    assert len(leaves) >= 2


def test_message_per_leaf_no_headers_falls_back_to_one_message():
    pages = _make_pages("Plain text content with no email headers.")
    chunks = chunk_pages_message_per_leaf(pages)
    leaves = [c for c in chunks if c.node_level == 0]
    assert len(leaves) == 1


# ===========================================================================
# Legacy chunk_pages back-compat
# ===========================================================================


def test_legacy_chunk_pages_returns_only_leaves():
    """Old `chunk_pages` returns a flat list of leaves so pre-
    hierarchical callers see the same shape."""
    text = " ".join(f"Sentence {i}." for i in range(50))
    chunks = chunk_pages(_make_pages(text), budget_tokens=64, overlap_tokens=8)
    assert all(c.node_level == 0 for c in chunks)
    # Indices are 0-based contiguous.
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
