"""Phase 4 — internal retrieval primitives.

Wave A scope: only the `smoke` submodule lands here. It exposes two helpers
(`bm25_smoke` + `dense_smoke`) used by `verify_phase_4.sh` and the Phase 4
test suite to prove the HNSW + BM25 indexes work end-to-end.

These helpers are deliberately INTERNAL:
  - NOT mounted on any HTTP router (decision #10 in build_tracker §5.11).
  - NOT importable from `kb.api.*` modules — if a future Phase tries to
    expose them via /search, that work belongs to Phase 8 (which builds the
    real query planner + RRF + rerank + CRAG + Astute generation on top of
    the same indexes).

Phase 8 will introduce `kb.retrieval.{planner, channels, rerank, ...}`
modules wrapping these indexes for production use.
"""
