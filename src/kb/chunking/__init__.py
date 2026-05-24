"""Phase 3a chunker — layout-aware, token-bounded chunks of raw pages.

Pure-function module. No DB, no I/O. Consumes a list of `Page` objects (the
same shape `kb.parsers` emits) and produces `Chunk` rows ready for INSERT
into the `chunks` table.

Architecture §5 step 6 calls this "late chunking (~2–4K tokens, layout-aware)".
True Jina-style late chunking (per-token embeddings aggregated to chunks) is
deferred to Wave B — current implementations of BGE-M3 and Gemini Embedding
001 don't expose per-token outputs. Phase 3a delivers the practical
layout-aware token-bounded approximation that the Anthropic Contextual
Retrieval write-up recommends.

Algorithm summary (G1 decisions §5.7):
1. Tokenize each page once via tiktoken cl100k_base.
2. Walk pages left-to-right, building a "current accumulator":
   - If a page fits in remaining budget → add to accumulator + record source.
   - If a page is small (< budget // 4) and there's more to come → keep
     accumulating across pages.
   - If a page on its own exceeds the budget → flush the accumulator first,
     then split that page on paragraph breaks (`\n\n`) or row boundaries
     (`\n`) closest to the budget point. Apply overlap between successive
     chunks of the same page.
3. Each emitted chunk records: text, token_count, source_page_numbers,
   chunk_index, content_sha (sha256 of text).
"""

from __future__ import annotations

import hashlib
from functools import lru_cache

import tiktoken
from pydantic import BaseModel

from kb.parsers import Page


class ChunkingError(Exception):
    """Raised when the chunker cannot produce any chunks from the input.
    Worker catches this and writes a `parsed→failed` lifecycle event."""


class Chunk(BaseModel):
    """One chunk emitted by `chunk_pages`. The worker maps this to a row
    in the `chunks` table 1:1."""

    chunk_index: int
    text: str
    source_page_numbers: list[int]
    token_count: int
    content_sha: str


@lru_cache(maxsize=1)
def _encoder() -> tiktoken.Encoding:
    return tiktoken.get_encoding("cl100k_base")


def _count_tokens(text: str) -> int:
    return len(_encoder().encode(text))


def _decode(tokens: list[int]) -> str:
    return _encoder().decode(tokens)


def _build_chunk(
    *,
    index: int,
    text: str,
    source_pages: list[int],
) -> Chunk:
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return Chunk(
        chunk_index=index,
        text=text,
        source_page_numbers=source_pages,
        token_count=_count_tokens(text),
        content_sha=sha,
    )


def _split_huge_page(
    *,
    text: str,
    page_number: int,
    budget_tokens: int,
    overlap_tokens: int,
    starting_index: int,
) -> list[Chunk]:
    """Split a single page that exceeds the budget. Prefer paragraph (`\\n\\n`)
    boundaries when available, fall back to row (`\\n`) boundaries (xlsx case),
    fall back to raw token boundaries.

    Returns a list of Chunks all tagged with `page_number` as the single source.
    Successive chunks include `overlap_tokens` of trailing context from the
    prior chunk (decision #2).
    """
    enc = _encoder()
    tokens = enc.encode(text)
    if len(tokens) <= budget_tokens:
        # Shouldn't be called in this case, but defensive.
        return [_build_chunk(index=starting_index, text=text, source_pages=[page_number])]

    chunks: list[Chunk] = []
    start = 0
    out_index = starting_index
    while start < len(tokens):
        end = min(start + budget_tokens, len(tokens))
        if end < len(tokens):
            # Try to back off to a paragraph break, then to a row break.
            window_text = enc.decode(tokens[start:end])
            adjusted_end = _backoff_to_boundary(window_text, tokens, start, end, enc)
            if adjusted_end is not None and adjusted_end > start + budget_tokens // 2:
                end = adjusted_end

        chunk_text = enc.decode(tokens[start:end])
        chunks.append(_build_chunk(
            index=out_index,
            text=chunk_text,
            source_pages=[page_number],
        ))
        out_index += 1

        if end >= len(tokens):
            break
        # Step forward leaving the requested overlap.
        start = end - overlap_tokens if overlap_tokens > 0 else end

    return chunks


def _backoff_to_boundary(
    window_text: str,
    all_tokens: list[int],
    start: int,
    end: int,
    enc: tiktoken.Encoding,
) -> int | None:
    """Given a token window [start, end), try to back off `end` to the largest
    paragraph (`\\n\\n`) boundary first, else row (`\\n`) boundary. Returns the
    new end-index (in tokens) or None if no good boundary found.

    Strategy: locate the last `\\n\\n` (then `\\n`) in `window_text`, re-encode
    the prefix to find its token count, and use that as the adjusted end.
    """
    for sep in ("\n\n", "\n"):
        idx = window_text.rfind(sep)
        if idx <= 0:
            continue
        prefix = window_text[: idx + len(sep)]
        prefix_token_count = len(enc.encode(prefix))
        candidate = start + prefix_token_count
        if candidate > start and candidate <= end:
            return candidate
    return None


def chunk_pages(
    pages: list[Page],
    *,
    budget_tokens: int = 2500,
    overlap_tokens: int = 250,
) -> list[Chunk]:
    """Layout-aware token-bounded chunker.

    Per build_tracker §5.7:
    - Each `Page` is the default boundary unit.
    - Small pages (token_count < budget // 4) join with neighbours.
    - Over-budget pages are split on paragraph/row boundaries with overlap.
    - Output chunks have monotonic `chunk_index` starting at 0.
    """
    if not pages:
        raise ChunkingError("empty raw_pages")

    enc = _encoder()
    page_tokens = [enc.encode(p.text) for p in pages]

    chunks: list[Chunk] = []
    chunk_index = 0

    # Accumulator for small-page joining.
    acc_text_parts: list[str] = []
    acc_pages: list[int] = []
    acc_token_count = 0
    small_page_threshold = max(1, budget_tokens // 4)

    def flush_accumulator() -> None:
        """Emit accumulated small pages as one chunk, then clear."""
        nonlocal chunk_index, acc_text_parts, acc_pages, acc_token_count
        if not acc_text_parts:
            return
        text = "\n\n".join(acc_text_parts)
        chunks.append(_build_chunk(
            index=chunk_index,
            text=text,
            source_pages=list(acc_pages),
        ))
        chunk_index += 1
        acc_text_parts = []
        acc_pages = []
        acc_token_count = 0

    for page, tokens in zip(pages, page_tokens, strict=True):
        tc = len(tokens)

        if tc > budget_tokens:
            # This page on its own exceeds budget — flush accumulator, then split.
            flush_accumulator()
            split_chunks = _split_huge_page(
                text=page.text,
                page_number=page.page_number,
                budget_tokens=budget_tokens,
                overlap_tokens=overlap_tokens,
                starting_index=chunk_index,
            )
            chunks.extend(split_chunks)
            chunk_index += len(split_chunks)
            continue

        # Will this page fit in the current accumulator?
        if acc_token_count + tc <= budget_tokens:
            acc_text_parts.append(page.text)
            acc_pages.append(page.page_number)
            acc_token_count += tc
        else:
            # Doesn't fit; flush + start new accumulator with this page.
            flush_accumulator()
            acc_text_parts.append(page.text)
            acc_pages.append(page.page_number)
            acc_token_count = tc

        # If we're already over the small-page threshold and the next page
        # (if any) won't be a small-page join candidate, flush now.
        # (Keep accumulating only while the current bundle is still small.)
        if acc_token_count >= small_page_threshold:
            # Look ahead: only keep accumulating if the very next page exists
            # and is also small. Otherwise flush.
            current_idx = pages.index(page)
            if current_idx + 1 >= len(pages):
                flush_accumulator()
            else:
                next_tc = len(page_tokens[current_idx + 1])
                if next_tc >= small_page_threshold:
                    flush_accumulator()

    flush_accumulator()

    if not chunks:
        # Shouldn't happen given the empty-input guard, but defensive.
        raise ChunkingError("chunker produced zero chunks")

    return chunks
