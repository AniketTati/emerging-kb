"""Phase 7 — identity resolution package.

Per build_tracker §5.14 + architecture §5 step 15.

4-stage pipeline:
  a) deterministic: exact (lowercased name + type) match against existing entities.
  b) embedding: nearest-neighbor via HNSW on entities.embedding (cosine ≥ 0.92 = match).
  c) llm_judge: borderline cosine ∈ [0.85, 0.92] → LLM yes/no.
  d) else: create new entity row.

Workspace-scoped throughout. Re-runs are idempotent (PRIMARY KEY on mention_id).
"""
