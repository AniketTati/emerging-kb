"""Phase 8b — Reciprocal Rank Fusion.

Per build_tracker §5.15.2 decisions #3, #5, #6. Algorithm per:
  Cormack, Clarke, Buettcher 2009, "Reciprocal Rank Fusion outperforms
  Condorcet and individual Rank Learning Methods" — k=60 default.

RRF score formula: for each unique (id, kind) tuple, sum across all channels
of `1 / (k + rank_in_channel + 1)`. Items appearing in more channels (or
higher in any channel) get higher fused scores.

Deduplication key is `(id, kind)` so a 'chunk' and a 'raptor_node' with
the same UUID (theoretically possible across separate tables) don't collide.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# Cormack-Clarke-Buettcher 2009 default. Both pgvector and pg_search return
# ranks starting at 1, so k=60 keeps the reciprocal weights well-distributed.
DEFAULT_K = 60


@dataclass
class Hit:
    """One retrieved item from any of Phase 8b's 6 channels.

    `id` is the underlying primary key:
      - kind='chunk' → contextual_chunks.id
      - kind='raptor_node' → raptor_nodes.id
      - kind='atomic_unit' → atomic_units.id

    `metadata` carries channel-specific context (file_id, level, scope,
    matched_mention, unit_type, ...) for downstream citation rendering.
    Phase 8e (Astute generation) reads `metadata` to build per-citation
    envelopes per architecture's Design 5.
    """

    id: str
    kind: str  # 'chunk' | 'raptor_node' | 'atomic_unit'
    score: float
    snippet: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# R2 — metadata keys that should ALWAYS overlay onto the accumulated
# Hit if any channel returned them. Other channels (BM25, dense) return
# the same chunk but lack these fields; the mentions_exact channel does
# pinpoint a char-range inside the chunk. Keeping the first-seen hit's
# blob but layering in these keys gives downstream citation enrichment
# access to the resolver output without coupling channel iteration order.
_METADATA_OVERLAY_KEYS = frozenset({
    "source_chunk_id",
    "source_char_start",
    "source_char_end",
    "source_page_numbers",
    "matched_mention",
    "matched_type",
})


def rrf_fuse(channels: list[list[Hit]], *, k: int = DEFAULT_K) -> list[Hit]:
    """Fuse N ranked channel results via Reciprocal Rank Fusion.

    Args:
      channels: list of channel results. Each channel is best-first ranked.
      k: RRF smoothing constant (60 per Cormack 2009).

    Returns:
      Deduplicated, score-descending list. RRF scores REPLACE the original
      per-channel scores. Snippet comes from the FIRST channel the item
      appeared in; metadata starts from the first channel but is *layered*
      with `_METADATA_OVERLAY_KEYS` from later channels (R2). This lets
      a chunk that surfaces from both bm25_chunks (no resolver positions)
      and mentions_exact (with positions) keep the BM25 snippet but
      gain the mentions_exact char-range info.
    """
    accumulated: dict[tuple[str, str], Hit] = {}
    rrf_scores: dict[tuple[str, str], float] = {}
    # Layered metadata — overlay non-None values for the curated key set.
    overlay_metadata: dict[tuple[str, str], dict] = {}

    for channel in channels:
        for rank, hit in enumerate(channel):
            key = (hit.id, hit.kind)
            rrf_scores[key] = rrf_scores.get(key, 0.0) + 1.0 / (k + rank + 1)
            if key not in accumulated:
                accumulated[key] = hit
            # Pull overlay-eligible metadata from EVERY channel, not just
            # the first. Later channels override earlier ones for these
            # specific keys only — the rest of the metadata blob stays
            # whatever the first-seen channel emitted.
            md = hit.metadata or {}
            for ok in _METADATA_OVERLAY_KEYS:
                v = md.get(ok)
                if v is None or v == "":
                    continue
                overlay_metadata.setdefault(key, {})[ok] = v

    fused: list[Hit] = []
    for key, hit in accumulated.items():
        # Merge overlay on top of the original metadata. We copy to avoid
        # mutating the original channel's dict (which may be reused in
        # other callers / tests).
        merged_md = dict(hit.metadata or {})
        merged_md.update(overlay_metadata.get(key, {}))
        fused.append(Hit(
            id=hit.id,
            kind=hit.kind,
            score=rrf_scores[key],
            snippet=hit.snippet,
            metadata=merged_md,
        ))
    fused.sort(key=lambda h: h.score, reverse=True)
    return fused
