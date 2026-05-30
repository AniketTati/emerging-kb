"""Phase 7 — identity resolution algorithm.

For each mention in a file, resolves to a canonical entity_id via 4 stages:
  1. deterministic: exact (lowercased name + type) match in workspace
  2. embedding: nearest-neighbor cosine ≥ 0.92 against entities.embedding
  3. llm_judge: borderline cosine ∈ [0.85, 0.92] → LLM yes/no
  4. else: create new entity row

Thresholds locked at §5.14 decision #3.
"""

from __future__ import annotations

from dataclasses import dataclass

EMBEDDING_HIGH_THRESHOLD = 0.92  # cosine sim — auto-match without LLM
EMBEDDING_LOW_THRESHOLD = 0.85   # cosine sim — borderline, send to LLM judge


# NER classifies a lot of numeric / temporal spans (`"30 days"`, `"0.6-1.1"`,
# `"$45M"`, `"Q1 2026"`) as entities. They have no canonical identity — two
# documents mentioning "$45M" aren't referring to the same `$45M` the way
# two mentions of "Vertex Industries Ltd." are referring to the same
# company. Creating canonical-entity rows for these:
#
#   - bloats `entities` (demo workspace ~30% of rows were numeric junk)
#   - pollutes the doc-detail "Entities mentioned" accordion with
#     uninformative chips ("`30 days`", "`8.6-10.2`")
#   - false-fragments the resolver's namespace (every numeric mention
#     creates a new entity with its own embedding)
#
# Skip them at the resolver. The mentions themselves remain in
# `extracted_mentions` (they still surface in the LLM context window
# for cited snippets) — we just don't promote them to canonical rows.
NOISE_MENTION_TYPES = frozenset({
    "CARDINAL",   # 30, 1, 2, ...
    "QUANTITY",   # 30 days, 5 kilometers
    "DATE",       # 2026, Q1, March, last Tuesday
    "TIME",       # 9:00 AM, noon
    "MONEY",      # $45M, USD 1000
    "ORDINAL",    # first, 2nd, third
    "PERCENT",    # 50%, twenty percent
})


def is_noise_mention_type(mention_type: str | None) -> bool:
    """Predicate for the resolver's skip-list. Case-tolerant on the
    spaCy/Gemini-NER convention of UPPERCASE labels."""
    if not mention_type:
        return False
    return mention_type.strip().upper() in NOISE_MENTION_TYPES


@dataclass
class ResolutionResult:
    entity_id: str
    confidence: float
    method: str  # 'deterministic' | 'embedding' | 'llm_judge' | 'identity'
    created_new: bool


async def select_entity_match(
    candidates,
    *,
    judge_same,
    high_threshold: float = EMBEDDING_HIGH_THRESHOLD,
    low_threshold: float = EMBEDDING_LOW_THRESHOLD,
):
    """I4 — pick the best entity match from top-k nearest-neighbour
    candidates (sorted DESC by similarity). Pure + unit-testable.

    Args:
      candidates: list of (entity_id, name, sim), best-first.
      judge_same: async callable `(candidate_name) -> bool` — asks the LLM
        judge whether a borderline candidate is the same entity.

    Returns (entity_id, method, confidence) or None. Top-1-only matching
    silently missed a true match that was the 2nd/3rd neighbour; this walks
    the list:
      - first candidate >= high_threshold → auto-match ('embedding')
      - else each borderline [low, high) candidate is judged (best-sim
        first); the first the judge confirms wins ('llm_judge')
      - a candidate below low_threshold stops the walk (sorted desc → nothing
        further can qualify)
    """
    for cand_id, cand_name, sim in candidates:
        if sim >= high_threshold:
            return cand_id, "embedding", sim
        if sim < low_threshold:
            return None
        try:
            same = await judge_same(cand_name)
        except Exception:  # noqa: BLE001 — judge failure → treat as no-match
            same = False
        if same:
            return cand_id, "llm_judge", sim
    return None
