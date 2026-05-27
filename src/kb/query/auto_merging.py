"""AutoMergingRetriever — swap leaf hits for their parent when ≥N
siblings of the same parent get retrieved.

Implements the LlamaIndex `AutoMergingRetriever` pattern in our
retrieval pipeline. Runs between rerank and CRAG:

    rerank → auto_merge → CRAG → mode_router

The rule:
  1. Group retrieved leaves by their `parent_chunk_id`.
  2. For each parent, if ≥ `merge_threshold_ratio` of its children are
     present in the hit list, REPLACE all those children with the
     parent (giving the generator a larger, denser context for that
     subsection).
  3. Otherwise leave the children as-is (the parent isn't a great
     summary of just one matched leaf).

Defaults (matching LlamaIndex):
  * `merge_threshold_ratio = 0.5` — half of a parent's children
  * Children can themselves get merged into THEIR parent (multi-level).
    We iterate until no more merges happen.

For Wave A we apply the merge only at the leaf-level (rerank returns
chunks; we don't currently mix levels in the candidate pool). When a
caller passes a hit whose `kind` isn't `chunk` (e.g. `extracted_entity`,
`raptor_node`, `aggregate`), we pass it through untouched.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kb.db.pool import Connection
from kb.query.rrf import Hit


DEFAULT_MERGE_THRESHOLD = 0.5
MAX_MERGE_LEVELS = 3   # safety cap; our tree is 3 levels deep


@dataclass(frozen=True)
class AutoMergeStats:
    """Audit trail surfaced in the Plan Inspector: how many leaves got
    swapped for how many parents, at which levels."""

    initial_leaf_hits: int
    final_hit_count: int
    merges_by_level: dict[int, int]   # level → number of parents created
    leaves_replaced: int


async def auto_merge_hits(
    hits: list[Hit],
    *,
    conn: Any,
    workspace_id: str,
    merge_threshold: float = DEFAULT_MERGE_THRESHOLD,
) -> tuple[list[Hit], AutoMergeStats]:
    """Apply the AutoMerging pattern to a top-K hit list.

    Returns (new_hits, stats). `new_hits` preserves the original order
    where possible (merged parents take the position of the highest-
    scored child they replaced).
    """
    chunk_hits = [h for h in hits if h.kind == "chunk"]
    other_hits = [h for h in hits if h.kind != "chunk"]
    if not chunk_hits or conn is None:
        return hits, AutoMergeStats(
            initial_leaf_hits=len(chunk_hits),
            final_hit_count=len(hits),
            merges_by_level={},
            leaves_replaced=0,
        )

    stats_merges: dict[int, int] = {}
    leaves_replaced = 0
    current_hits = list(chunk_hits)

    for iteration in range(MAX_MERGE_LEVELS):
        # Look up parent + sibling counts for each hit's underlying
        # chunk row. We need:
        #   1. parent_chunk_id (FK into chunks)
        #   2. total siblings under that parent (denominator for the
        #      merge threshold)
        chunk_ids = [
            (h.metadata or {}).get("contextual_chunk_id") or h.id
            for h in current_hits
        ]
        # Hit.id for chunk hits is the contextual_chunk_id (the
        # retrieval index's primary key). We need to translate to
        # chunks.id via contextual_chunks.chunk_id.
        parent_map = await _resolve_parent_chunks(
            conn,
            workspace_id=workspace_id,
            contextual_chunk_ids=chunk_ids,
        )
        if not parent_map:
            break

        # Group hits by parent_chunk_id.
        # parent_map: {contextual_chunk_id: (chunk_id, parent_chunk_id,
        #              parent_level, siblings_total)}
        grouped: dict[str, list[Hit]] = {}
        unmerged: list[Hit] = []
        for h in current_hits:
            cc_id = (h.metadata or {}).get("contextual_chunk_id") or h.id
            entry = parent_map.get(cc_id)
            if entry is None or entry[1] is None:
                # Orphan leaf — no parent to merge into.
                unmerged.append(h)
                continue
            _chunk_id, parent_chunk_id, _parent_level, _siblings_total = entry
            grouped.setdefault(parent_chunk_id, []).append(h)

        any_merged = False
        merged_hits: list[Hit] = list(unmerged)
        for parent_id, sibling_hits in grouped.items():
            # First entry's metadata gives us the sibling-count
            # denominator (all entries share the same parent so share
            # the same total).
            first_cc_id = (sibling_hits[0].metadata or {}).get(
                "contextual_chunk_id"
            ) or sibling_hits[0].id
            _, _, parent_level, siblings_total = parent_map[first_cc_id]
            if siblings_total <= 0:
                merged_hits.extend(sibling_hits)
                continue
            ratio = len(sibling_hits) / siblings_total
            if ratio >= merge_threshold and len(sibling_hits) >= 2:
                # Swap N children for one parent. Synthesize a
                # parent-hit by reading the parent chunk's text.
                parent_hit = await _build_parent_hit(
                    conn,
                    workspace_id=workspace_id,
                    parent_chunk_id=parent_id,
                    parent_level=parent_level,
                    children=sibling_hits,
                )
                if parent_hit is not None:
                    merged_hits.append(parent_hit)
                    leaves_replaced += len(sibling_hits)
                    stats_merges[parent_level] = (
                        stats_merges.get(parent_level, 0) + 1
                    )
                    any_merged = True
                else:
                    merged_hits.extend(sibling_hits)
            else:
                merged_hits.extend(sibling_hits)

        # Sort by score, preserving original order via stable sort.
        merged_hits.sort(key=lambda h: h.score, reverse=True)
        current_hits = merged_hits

        if not any_merged:
            break

    final = current_hits + other_hits
    return final, AutoMergeStats(
        initial_leaf_hits=len(chunk_hits),
        final_hit_count=len(final),
        merges_by_level=stats_merges,
        leaves_replaced=leaves_replaced,
    )


async def _resolve_parent_chunks(
    conn: Any,
    *,
    workspace_id: str,
    contextual_chunk_ids: list[str],
) -> dict[str, tuple[str, str | None, int, int]]:
    """For each contextual_chunk_id, return its (chunk_id,
    parent_chunk_id, parent_node_level, total_siblings_under_parent).

    `total_siblings_under_parent` is the count of OTHER chunks with the
    same parent_chunk_id — including the hit's own row. Used to
    compute the merge ratio.
    """
    ids = [i for i in contextual_chunk_ids if i]
    if not ids or conn is None:
        return {}
    out: dict[str, tuple[str, str | None, int, int]] = {}
    try:
        # Step 1: contextual_chunk_id → chunks row + parent chunk row +
        # the parent's node_level.
        cur = await conn.execute(
            """
            SELECT cc.id::text,
                   c.id::text,
                   c.parent_chunk_id::text,
                   p.node_level
              FROM contextual_chunks cc
              JOIN chunks c   ON c.id = cc.chunk_id
              LEFT JOIN chunks p   ON p.id = c.parent_chunk_id
             WHERE cc.id::text = ANY(%s)
            """,
            (ids,),
        )
        chunk_info_rows = await cur.fetchall()

        # Step 2: sibling counts per parent.
        parent_ids = sorted({
            r[2] for r in chunk_info_rows if r[2] is not None
        })
        sibling_counts: dict[str, int] = {}
        if parent_ids:
            cur = await conn.execute(
                "SELECT parent_chunk_id::text, count(*) "
                "FROM chunks WHERE parent_chunk_id::text = ANY(%s) "
                "GROUP BY parent_chunk_id",
                (parent_ids,),
            )
            sibling_counts = {
                str(r[0]): int(r[1]) for r in await cur.fetchall()
            }
        for cc_id, chunk_id, parent_id, parent_level in chunk_info_rows:
            sib_total = (
                sibling_counts.get(parent_id, 0) if parent_id else 0
            )
            out[str(cc_id)] = (
                str(chunk_id),
                str(parent_id) if parent_id else None,
                int(parent_level) if parent_level is not None else 0,
                sib_total,
            )
    except Exception:
        return {}
    return out


async def _build_parent_hit(
    conn: Any,
    *,
    workspace_id: str,
    parent_chunk_id: str,
    parent_level: int,
    children: list[Hit],
) -> Hit | None:
    """Materialize a single Hit representing the parent chunk. Score is
    the MAX of the merged children's scores (so it sits at least as
    high as any individual child in the reranked list)."""
    try:
        cur = await conn.execute(
            "SELECT c.id::text, c.text, c.token_count, c.file_id::text "
            "FROM chunks c WHERE c.id::text = %s",
            (parent_chunk_id,),
        )
        row = await cur.fetchone()
        if not row:
            return None
        parent_id, parent_text, token_count, file_id = row
    except Exception:
        return None

    # Inherit metadata from the highest-scored child (file_id,
    # source_page_numbers if present).
    children_sorted = sorted(children, key=lambda h: h.score, reverse=True)
    head = children_sorted[0]
    new_meta = dict(head.metadata or {})
    new_meta["auto_merged"] = True
    new_meta["merged_from"] = [c.id for c in children]
    new_meta["merged_count"] = len(children)
    new_meta["node_level"] = parent_level
    new_meta["channel"] = "auto_merging"
    # Replace contextual_chunk_id pointer since the parent isn't in
    # contextual_chunks (parents don't get contextualized).
    new_meta.pop("contextual_chunk_id", None)
    return Hit(
        id=str(parent_id),
        kind="chunk",
        score=head.score,
        snippet=parent_text or head.snippet,
        metadata=new_meta,
    )
