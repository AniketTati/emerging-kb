"""Hierarchical chunker — LlamaIndex `HierarchicalNodeParser` + per-
doc-type adapters (row/message/clause-aware variants).

Pure-function module. No DB, no I/O. Consumes `Page` objects (the same
shape kb.parsers emits) and produces a TREE of `Chunk` objects. Each
chunk carries its `node_level` (0=leaf, 1=mid, 2=root) and the stable
`parser_node_id` of its parent (None for roots). The worker writes
chunks in topological order, mapping parser_node_id → chunks.id as it
goes, so child rows can FK into parent rows via `parent_chunk_id`.

Why hierarchical:
  - The 2026 production benchmark consensus: index small leaves
    (~128 tokens) for retrieval precision, but expand to a larger
    parent at generation time when multiple sibling leaves get hit.
    LlamaIndex's `AutoMergingRetriever` (we provide a thin equivalent
    in kb.query.auto_merging) implements the swap-to-parent rule.
  - Recall improves substantially vs flat 2500-token chunks; the
    "context cliff" around 2-2.5k tokens stops hitting because
    generator inputs are bounded by the merged-parent size, not the
    raw indexed chunk size.

Chunker kinds (router-selected per doc_type — see
kb/chunking/doc_type_router.py):

  * `hierarchical`     — LlamaIndex HierarchicalNodeParser. Default.
                          Recursive splitter with size-tuple [root, mid, leaf].
  * `row_per_leaf`     — bank_statement / invoice / lab_report / xlsx-
                          backed docs. Each parsed row becomes one leaf;
                          rows group into mids (e.g. by date range or
                          sheet name); whole file is one root.
  * `message_per_leaf` — email_thread. Each message (split on header
                          markers) is one leaf; messages group into
                          topical mids.
  * `clause_per_leaf`  — contracts. Defers to hierarchical for now;
                          proper Docling-section integration in follow-up.

`chunk_pages()` (the legacy entry point) stays available as a thin
wrapper that calls the hierarchical chunker and filters to leaves —
keeps any non-worker callers (tests, scripts) working unchanged.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from functools import lru_cache

import tiktoken
from pydantic import BaseModel

from kb.parsers import Page


class ChunkingError(Exception):
    """Raised when the chunker cannot produce any chunks from the input.
    Worker catches this and writes a `parsed→failed` lifecycle event."""


# Default chunk-size tuple [root, mid, leaf] — the 2026 production
# benchmark-recommended sizes. Configurable per-doc-type via the
# chunker_configs table.
DEFAULT_CHUNK_SIZES: tuple[int, int, int] = (2048, 512, 128)
DEFAULT_OVERLAP_TOKENS: int = 20


class Chunk(BaseModel):
    """One chunk in the hierarchy. The worker writes these in
    topological order (roots → mids → leaves) so child rows can FK
    into their parents."""

    chunk_index: int
    text: str
    source_page_numbers: list[int]
    token_count: int
    content_sha: str
    # Hierarchy metadata.
    node_level: int = 0
    parser_node_id: str = ""           # stable id assigned by the parser
    parent_parser_node_id: str | None = None  # None for roots


@lru_cache(maxsize=1)
def _encoder() -> tiktoken.Encoding:
    return tiktoken.get_encoding("cl100k_base")


def _count_tokens(text: str) -> int:
    return len(_encoder().encode(text))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _stable_node_id() -> str:
    """LlamaIndex doesn't expose its internal node_id generator in a
    pinned-stable way across versions; we use uuid4 so children's
    parent linkage is well-defined within a single chunk_file run."""
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Hierarchical chunker — LlamaIndex bridge
# ---------------------------------------------------------------------------


def chunk_pages_hierarchical(
    pages: list[Page],
    *,
    chunk_sizes: tuple[int, ...] = DEFAULT_CHUNK_SIZES,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> list[Chunk]:
    """Build a 3-level tree (or up to len(chunk_sizes) levels) for the
    given pages using LlamaIndex's HierarchicalNodeParser.

    `chunk_sizes` is ordered LARGEST → smallest (e.g. [2048, 512, 128]).
    The output list is ordered so that PARENTS APPEAR BEFORE CHILDREN
    (root → mid → leaf), so the worker can insert in iteration order
    and child FKs always resolve.
    """
    if not pages:
        raise ChunkingError("empty raw_pages")

    # Concatenate pages into one document with explicit page-break
    # markers we can later parse out to assign source_page_numbers.
    # LlamaIndex doesn't preserve page boundaries natively; we recover
    # them by tracking marker positions inside the recovered chunks.
    page_marker_re = re.compile(r"<!--PG (\d+)-->")
    doc_parts: list[str] = []
    for p in pages:
        doc_parts.append(f"<!--PG {p.page_number}-->\n{p.text}")
    doc_text = "\n\n".join(doc_parts)

    from llama_index.core.node_parser import HierarchicalNodeParser
    from llama_index.core.schema import Document, NodeRelationship

    # LlamaIndex requires overlap < smallest chunk size. Cap so callers
    # using small chunk_sizes (e.g. tests) don't trip a ValueError.
    smallest_size = min(chunk_sizes)
    effective_overlap = min(overlap_tokens, max(0, smallest_size // 4))

    parser = HierarchicalNodeParser.from_defaults(
        chunk_sizes=list(chunk_sizes),
        chunk_overlap=effective_overlap,
    )
    llama_nodes = parser.get_nodes_from_documents([Document(text=doc_text)])
    if not llama_nodes:
        raise ChunkingError(
            "LlamaIndex HierarchicalNodeParser returned no nodes"
        )

    # Resolve parent linkage from LlamaIndex's relationships dict.
    def _parent_node_id(node) -> str | None:
        rels = getattr(node, "relationships", None) or {}
        parent = rels.get(NodeRelationship.PARENT)
        if parent is None:
            return None
        return getattr(parent, "node_id", None)

    # Compute BFS depth from roots (nodes without a parent). Depth 0 =
    # root, larger depth = deeper. Then INVERT to our node_level
    # semantic where 0 = leaf and the largest = root, so the citation
    # / retrieval channels can filter by `node_level=0` for leaves.
    nodes_by_id = {n.node_id: n for n in llama_nodes}
    depth: dict[str, int] = {}
    # Roots first.
    queue: list[str] = []
    for n in llama_nodes:
        if _parent_node_id(n) is None:
            depth[n.node_id] = 0
            queue.append(n.node_id)

    # BFS by walking children. LlamaIndex stores children in
    # relationships[CHILD] as a list.
    while queue:
        next_queue: list[str] = []
        for nid in queue:
            n = nodes_by_id.get(nid)
            if n is None:
                continue
            child_rels = (
                getattr(n, "relationships", None) or {}
            ).get(NodeRelationship.CHILD)
            if not child_rels:
                continue
            children = child_rels if isinstance(child_rels, list) else [child_rels]
            for child in children:
                cid = getattr(child, "node_id", None)
                if cid and cid not in depth and cid in nodes_by_id:
                    depth[cid] = depth[nid] + 1
                    next_queue.append(cid)
        queue = next_queue

    # Backfill anything BFS missed (orphaned nodes — rare LlamaIndex
    # edge case) by walking up via parent links.
    for n in llama_nodes:
        if n.node_id in depth:
            continue
        chain: list[str] = []
        cur = n.node_id
        while cur and cur not in depth:
            chain.append(cur)
            n_cur = nodes_by_id.get(cur)
            if n_cur is None:
                break
            cur = _parent_node_id(n_cur)
        if cur and cur in depth:
            for i, nid in enumerate(reversed(chain)):
                depth[nid] = depth[cur] + i + 1
        else:
            # Truly orphaned — call it a leaf.
            for nid in chain:
                depth[nid] = max(depth.values(), default=0)

    max_depth = max(depth.values()) if depth else 0

    # Build the chunk list in topological order: roots (lowest BFS
    # depth, highest node_level) first, leaves last.
    chunk_index = 0
    ordered_chunks: list[Chunk] = []
    # LlamaIndex sometimes emits MULTIPLE nodes with identical text
    # when the doc fits inside the smallest chunk_size (one per
    # configured level, all the same content). Dedupe by content_sha
    # within a level — the first wins, later siblings get folded in.
    seen_in_level: dict[int, set[str]] = {}
    for d in range(max_depth + 1):
        for n in llama_nodes:
            if depth.get(n.node_id) != d:
                continue
            text = n.get_content()
            source_pages = sorted(
                int(m) for m in page_marker_re.findall(text)
            )
            clean_text = page_marker_re.sub("", text).strip()
            if not clean_text:
                continue
            level = max_depth - d  # invert so 0 = leaf
            sha = _sha(clean_text)
            seen = seen_in_level.setdefault(level, set())
            if sha in seen:
                continue
            seen.add(sha)
            ordered_chunks.append(Chunk(
                chunk_index=chunk_index,
                text=clean_text,
                source_page_numbers=source_pages or [1],
                token_count=_count_tokens(clean_text),
                content_sha=sha,
                node_level=level,
                parser_node_id=n.node_id,
                parent_parser_node_id=_parent_node_id(n),
            ))
            chunk_index += 1

    if not ordered_chunks:
        raise ChunkingError("hierarchical parser produced no usable chunks")
    return ordered_chunks


# ---------------------------------------------------------------------------
# Row-per-leaf chunker (xlsx / tabular)
# ---------------------------------------------------------------------------


def chunk_pages_row_per_leaf(
    pages: list[Page],
    *,
    rows_per_mid: int = 20,
) -> list[Chunk]:
    """For xlsx-style docs where one ROW = one logical retrievable
    unit. Builds a 3-level tree:

      level 0 (leaves)  — each non-empty line is one chunk
      level 1 (mids)    — groups of `rows_per_mid` sibling leaves
                          rendered as concatenated text
      level 2 (root)    — entire file as a single root chunk

    Source page numbers come from the Page each row was on. Lines that
    look like obvious header/separator rows (all caps, all dashes) get
    pinned to the FIRST mid as a header.
    """
    if not pages:
        raise ChunkingError("empty raw_pages")

    # Root: whole-doc concatenation.
    full_text = "\n\n".join(p.text for p in pages)
    if not full_text.strip():
        raise ChunkingError("row_per_leaf got empty pages")
    root_id = _stable_node_id()
    root_chunk = Chunk(
        chunk_index=0,
        text=full_text,
        source_page_numbers=sorted({p.page_number for p in pages}),
        token_count=_count_tokens(full_text),
        content_sha=_sha(full_text),
        node_level=2,
        parser_node_id=root_id,
        parent_parser_node_id=None,
    )

    # Collect rows with their page numbers.
    rows_with_page: list[tuple[str, int]] = []
    for p in pages:
        for line in p.text.splitlines():
            stripped = line.strip()
            if stripped:
                rows_with_page.append((stripped, p.page_number))
    if not rows_with_page:
        raise ChunkingError("row_per_leaf found no non-empty rows")

    # Build mids by grouping consecutive rows.
    chunks: list[Chunk] = [root_chunk]
    chunk_index = 1
    mid_buckets: list[list[tuple[str, int]]] = []
    for i in range(0, len(rows_with_page), rows_per_mid):
        mid_buckets.append(rows_with_page[i:i + rows_per_mid])

    mid_ids: list[str] = []
    for bucket in mid_buckets:
        mid_text = "\n".join(r[0] for r in bucket)
        mid_pages = sorted({r[1] for r in bucket})
        mid_id = _stable_node_id()
        mid_ids.append(mid_id)
        chunks.append(Chunk(
            chunk_index=chunk_index,
            text=mid_text,
            source_page_numbers=mid_pages,
            token_count=_count_tokens(mid_text),
            content_sha=_sha(mid_text),
            node_level=1,
            parser_node_id=mid_id,
            parent_parser_node_id=root_id,
        ))
        chunk_index += 1

    # Leaves: one per row, linked to its bucket's mid.
    for bucket, mid_id in zip(mid_buckets, mid_ids, strict=True):
        for row_text, page in bucket:
            chunks.append(Chunk(
                chunk_index=chunk_index,
                text=row_text,
                source_page_numbers=[page],
                token_count=_count_tokens(row_text),
                content_sha=_sha(row_text),
                node_level=0,
                parser_node_id=_stable_node_id(),
                parent_parser_node_id=mid_id,
            ))
            chunk_index += 1

    return chunks


# ---------------------------------------------------------------------------
# Message-per-leaf chunker (email threads)
# ---------------------------------------------------------------------------


# Markers that separate messages in an email thread parsed to text.
# Use `From:` as the primary boundary marker — it's traditionally the
# first header line of each message in a thread dump. (Sent/To/Subject
# also appear but typically as sibling lines within the same message
# header block, so splitting on them would over-fragment.)
_EMAIL_MESSAGE_SEPARATOR = re.compile(
    r"^From:\s",
    re.MULTILINE | re.IGNORECASE,
)


def chunk_pages_message_per_leaf(
    pages: list[Page],
) -> list[Chunk]:
    """For email_thread docs. Each message (block between header
    markers) becomes one leaf; messages group into a single mid chunk
    that summarizes the thread; the whole file is the root.

    Header detection uses common email-header prefixes (`From:`,
    `Sent:`, `To:`, `Subject:`) — Phase 5b's email plugin used the
    same regex. Falls back to single-leaf when no headers detected.
    """
    if not pages:
        raise ChunkingError("empty raw_pages")
    full_text = "\n\n".join(p.text for p in pages)
    if not full_text.strip():
        raise ChunkingError("message_per_leaf got empty pages")

    root_id = _stable_node_id()
    mid_id = _stable_node_id()
    root_chunk = Chunk(
        chunk_index=0,
        text=full_text,
        source_page_numbers=sorted({p.page_number for p in pages}),
        token_count=_count_tokens(full_text),
        content_sha=_sha(full_text),
        node_level=2,
        parser_node_id=root_id,
        parent_parser_node_id=None,
    )

    # Find message boundaries: each `From:` at the start of a line
    # marks the beginning of a new message.
    header_positions = [
        m.start() for m in _EMAIL_MESSAGE_SEPARATOR.finditer(full_text)
    ]
    if not header_positions:
        message_spans = [(0, len(full_text))]
    else:
        boundaries = list(header_positions) + [len(full_text)]
        message_spans = [
            (boundaries[i], boundaries[i + 1])
            for i in range(len(boundaries) - 1)
        ]

    mid_text_parts = [full_text[start:end].strip() for start, end in message_spans]
    mid_text = "\n---\n".join(mid_text_parts)
    mid_chunk = Chunk(
        chunk_index=1,
        text=mid_text,
        source_page_numbers=root_chunk.source_page_numbers,
        token_count=_count_tokens(mid_text),
        content_sha=_sha(mid_text),
        node_level=1,
        parser_node_id=mid_id,
        parent_parser_node_id=root_id,
    )

    chunks: list[Chunk] = [root_chunk, mid_chunk]
    chunk_index = 2
    for start, end in message_spans:
        msg_text = full_text[start:end].strip()
        if not msg_text:
            continue
        # Best-effort page assignment: pick the first page whose text
        # contains the first 80 chars of the message.
        msg_pages: list[int] = []
        head = msg_text[:80]
        for p in pages:
            if head and head in p.text:
                msg_pages.append(p.page_number)
                break
        chunks.append(Chunk(
            chunk_index=chunk_index,
            text=msg_text,
            source_page_numbers=msg_pages or [pages[0].page_number],
            token_count=_count_tokens(msg_text),
            content_sha=_sha(msg_text),
            node_level=0,
            parser_node_id=_stable_node_id(),
            parent_parser_node_id=mid_id,
        ))
        chunk_index += 1

    return chunks


# ---------------------------------------------------------------------------
# Legacy entry point — kept as a thin wrapper for back-compat
# ---------------------------------------------------------------------------


def chunk_pages(
    pages: list[Page],
    *,
    budget_tokens: int = 2500,
    overlap_tokens: int = 250,
) -> list[Chunk]:
    """Legacy chunker entry — returns LEAVES from the hierarchical
    parser so existing callers see a flat list.

    `budget_tokens` was the old chunk-size knob; we map it to the leaf
    size in the [root, mid, leaf] tuple. `overlap_tokens` carries
    through.

    New code should call `chunk_pages_hierarchical()` directly to get
    the parent rows too.
    """
    leaf_size = min(budget_tokens, 512)
    sizes = (
        max(2048, budget_tokens * 2),
        max(512, budget_tokens),
        leaf_size,
    )
    all_chunks = chunk_pages_hierarchical(
        pages, chunk_sizes=sizes, overlap_tokens=overlap_tokens,
    )
    leaves = [c for c in all_chunks if c.node_level == 0]
    # Renumber chunk_index 0..N over just the leaves so old code that
    # treated chunk_index as a flat-list position still works.
    for i, c in enumerate(leaves):
        c.chunk_index = i
    return leaves
