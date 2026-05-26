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
