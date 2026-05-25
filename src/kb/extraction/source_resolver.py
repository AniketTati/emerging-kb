"""Find the source character offsets of an LLM-extracted snippet.

The LLM returns the verbatim text it extracted (mention_text,
proposed_field.value_text, triple.subject/object_text, atomic_unit
clause summary). We don't need an "offset-aware LLM" — the worker can
re-search the snippet in the chunk that was sent to the LLM and record
the exact character range. Deterministic; no prompt changes.

Two-pass match strategy:
  1. Exact substring match on the original chunk text.
  2. Whitespace-normalized match (collapses runs of \\s+) with offsets
     mapped back to the original — handles LLM-output that lost line
     breaks or rewrote tabs to single spaces.

Returns None when the snippet genuinely isn't in the chunk (e.g. the
LLM paraphrased rather than quoted). Callers should store the resolved
offsets as nullable and let the UI surface "no source location" honestly.
"""

from __future__ import annotations

import re
from typing import NamedTuple


class ResolvedPosition(NamedTuple):
    char_start: int
    char_end: int


_WS = re.compile(r"\s+")


def resolve(snippet: str, chunk_text: str) -> ResolvedPosition | None:
    """Locate `snippet` in `chunk_text`. Returns (start, end) into
    chunk_text, or None if not found. Always picks the FIRST occurrence
    — callers send snippets that are already chunk-scoped, so collisions
    across chunks are not a concern."""
    if not snippet or not chunk_text:
        return None

    # Trim outer whitespace + balanced surrounding quotes the LLM
    # sometimes adds.
    needle = snippet.strip().strip("\"'")
    if not needle:
        return None

    # Pass 1: exact match.
    idx = chunk_text.find(needle)
    if idx >= 0:
        return ResolvedPosition(idx, idx + len(needle))

    # Pass 2: whitespace-normalized match. We collapse \s+ in both sides
    # and remember the offset map so we can return real positions.
    norm_chunk, mapping = _normalize_with_map(chunk_text)
    norm_needle = _WS.sub(" ", needle).strip()
    if not norm_needle:
        return None
    nidx = norm_chunk.find(norm_needle)
    if nidx < 0:
        return None
    # Map back: mapping[i] is the position in chunk_text of norm_chunk[i].
    start = mapping[nidx]
    # End maps to mapping[nidx + len - 1] + 1 (inclusive last char + 1).
    last_norm = nidx + len(norm_needle) - 1
    end = mapping[last_norm] + 1
    return ResolvedPosition(start, end)


def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Return (normalized_text, offset_map) where offset_map[i] is the
    index in `text` of the character that produced normalized_text[i].
    Collapses runs of whitespace to a single space."""
    out: list[str] = []
    mapping: list[int] = []
    in_ws = False
    for i, ch in enumerate(text):
        if ch.isspace():
            if not in_ws:
                out.append(" ")
                mapping.append(i)
                in_ws = True
        else:
            out.append(ch)
            mapping.append(i)
            in_ws = False
    return "".join(out), mapping
