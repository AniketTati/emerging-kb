"""Phase 8 — query layer (the architecture's "big one").

Per build_tracker §5.15. Split into 8a-f sub-phases per architecture §12.

Sub-modules:
  - rewriter (8a) — Step-Back + HyDE + Query2Doc query rewrites.
  - channels (8b — future) — 6-channel parallel retrieval.
  - rrf (8b — future) — Reciprocal Rank Fusion.
  - rerank (8c — future) — Cohere / mxbai cross-encoder.
  - crag (8d — future) — Corrective RAG relevance gate.
  - generate (8e — future) — Astute generation with citations.
  - orchestrator (8f — future) — top-level pipeline coordinator.

8f also lands the HTTP surface (`POST /search` + `POST /chat`).
"""
