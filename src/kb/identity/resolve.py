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


@dataclass
class ResolutionResult:
    entity_id: str
    confidence: float
    method: str  # 'deterministic' | 'embedding' | 'llm_judge' | 'identity'
    created_new: bool
