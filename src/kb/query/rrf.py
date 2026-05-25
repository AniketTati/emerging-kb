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


def rrf_fuse(channels: list[list[Hit]], *, k: int = DEFAULT_K) -> list[Hit]:
    """Fuse N ranked channel results via Reciprocal Rank Fusion.

    Args:
      channels: list of channel results. Each channel is best-first ranked.
      k: RRF smoothing constant (60 per Cormack 2009).

    Returns:
      Deduplicated, score-descending list. RRF scores REPLACE the original
      per-channel scores. Snippet + metadata come from the FIRST channel
      the item appeared in (later channels' versions are discarded; only
      score is accumulated).
    """
    accumulated: dict[tuple[str, str], Hit] = {}
    rrf_scores: dict[tuple[str, str], float] = {}

    for channel in channels:
        for rank, hit in enumerate(channel):
            key = (hit.id, hit.kind)
            rrf_scores[key] = rrf_scores.get(key, 0.0) + 1.0 / (k + rank + 1)
            if key not in accumulated:
                accumulated[key] = hit

    fused: list[Hit] = []
    for key, hit in accumulated.items():
        fused.append(Hit(
            id=hit.id,
            kind=hit.kind,
            score=rrf_scores[key],
            snippet=hit.snippet,
            metadata=hit.metadata,
        ))
    fused.sort(key=lambda h: h.score, reverse=True)
    return fused
