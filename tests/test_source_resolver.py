"""Unit tests for the LLM-snippet → source-offset resolver."""

from __future__ import annotations

from kb.extraction.source_resolver import resolve


def test_exact_match():
    chunk = "NorthWind Capital LLC shall pay Vertex on a net-thirty (30) day basis."
    snippet = "Vertex"
    r = resolve(snippet, chunk)
    assert r is not None
    assert chunk[r.char_start:r.char_end] == "Vertex"


def test_first_occurrence_wins():
    chunk = "Vertex Industries. Vertex also delivers."
    r = resolve("Vertex", chunk)
    assert r is not None
    assert r.char_start == 0
    assert r.char_end == 6


def test_strips_quotes():
    chunk = "The Effective Date is January 15, 2026."
    r = resolve('"January 15, 2026"', chunk)
    assert r is not None
    assert chunk[r.char_start:r.char_end] == "January 15, 2026"


def test_whitespace_normalized_match():
    # LLM returned the snippet on a single line; original had a newline.
    chunk = "The initial term of this Agreement\nshall be three (3) years from the Effective Date."
    snippet = "The initial term of this Agreement shall be three (3) years"
    r = resolve(snippet, chunk)
    assert r is not None
    # The match starts at "The" and ends at "years".
    assert chunk[r.char_start:].startswith("The initial term")
    assert chunk[r.char_start:r.char_end].endswith("years")


def test_no_match_returns_none():
    chunk = "Some unrelated text here."
    assert resolve("nothing in here", chunk) is None


def test_empty_inputs():
    assert resolve("", "any") is None
    assert resolve("any", "") is None
    assert resolve("   ", "any") is None


def test_collapsing_tabs_and_newlines():
    chunk = "Mumbai\t+\tPune\n+\tAurangabad"
    snippet = "Mumbai + Pune + Aurangabad"
    r = resolve(snippet, chunk)
    assert r is not None
    # Original span runs from M (index 0) through d (last char of Aurangabad).
    assert chunk[r.char_start] == "M"
    assert chunk[r.char_end - 1] == "d"
