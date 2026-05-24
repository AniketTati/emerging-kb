# Phase 8b — Test Spec (G3)

> **Status:** G3 open · drafted 2026-05-25 · awaiting sign-off.
> **Inputs:** Phase 8b G1 plan ([build_tracker §5.15.2](../../docs/build_tracker.md), 12 decisions) · G2 was a no-op (no `api_contracts.md` delta — 8b is module-only; HTTP surface lands at 8f).
> **Outputs at G3:** this spec + 2 new red skeleton files. Imports point at `kb.query.rrf.{Hit, rrf_fuse, DEFAULT_K}` + `kb.query.channels.{6 channel functions, run_all_channels}` — modules land at G4.

---

## 1. Scope

Every §5.15.2 G1 decision that has assertable behavior gets matching tests. Total surface: **~17 new tests** across 2 new files.

| File | New tests | Covers |
|---|---|---|
| [`tests/test_query_rrf_unit.py`](../test_query_rrf_unit.py) | ~8 | Pure-function RRF math: empty input · single channel passthrough · two-channel dedup · RRF formula correctness (1/(k+r+1) summation) · order monotonicity · DEFAULT_K constant · Hit dataclass shape · metadata preservation through fusion. (Decisions #3, #5, #6) |
| [`tests/test_query_channels_unit.py`](../test_query_channels_unit.py) | ~9 | Each of the 6 channels gets a happy-path test seeded via testcontainer SQL: bm25_chunks finds keyword · bm25_raptor finds keyword · dense_chunks ranks by cosine · dense_raptor ranks by cosine · mentions_exact resolves to chunk · atomic_units_rarity filters by unit_type when query mentions it. Plus: `run_all_channels` returns dict of all 6 channels + `gather` swallows per-channel exceptions per decision #4 + #12. |

No worker integration tests in 8b — module is pure-function with DB reads. Worker orchestration lands at 8f.

---

## 2. Fixture strategy

**RRF tests** (`test_query_rrf_unit.py`): no DB, no LLM — just `Hit` dataclass construction + `rrf_fuse` math.

**Channel tests** (`test_query_channels_unit.py`): use `db_superuser` for direct seed. Seed minimal data per channel: contextual_chunks with known text for BM25; chunk_embeddings with one-hot vectors for dense; extracted_mentions with known names; atomic_units with known rarity scores. Channel functions are called directly against `db_superuser` (workspace context is set inside each test).

---

## 3. Decision → test mapping

| G1 # | Decision | Test(s) |
|---|---|---|
| 1 | 6 channels | One happy-path test per channel (6 tests) |
| 2 | Top-K per channel = 20 | RRF tests verify N-result handling; channel tests use small N with explicit limit |
| 3 | RRF k=60 | `test_rrf_uses_default_k_60_in_formula` (verify summation math) |
| 4 | `asyncio.gather(return_exceptions=True)` | `test_run_all_channels_swallows_channel_exception` (one channel raises; others succeed; dict still returned) |
| 5 | Dedup by `(id, kind)` | `test_rrf_dedupes_same_id_kind_across_channels` (same hit in 2 channels → 1 fused row with summed score) |
| 6 | Hit shape (`id, kind, score, snippet, metadata`) | `test_hit_dataclass_shape` + structural assertions in all channel tests |
| 7 | mentions_exact returns chunk-kind Hit | `test_mentions_exact_channel_returns_chunk_kind_hit` (kind='chunk', metadata includes matched_mention) |
| 8 | atomic_units_rarity query-keyword filter | `test_atomic_units_rarity_channel_filters_by_unit_type_keyword` (query mentions "clause" → only clause units returned) |
| 9 | All variants get separate embeddings | Tested at 8f (orchestrator); 8b just exposes channels that consume `query_vec` parameter |
| 10 | Per-channel workspace WHERE filter | Channel tests use 2 workspaces (A, B); seed in A; query as A returns hits; query as B returns 0 |
| 11 | snippet[:500] truncation | `test_channel_truncates_snippet_to_500_chars` (seed 2000-char chunk; verify hit.snippet length) |
| 12 | Per-channel exception swallowing | covered by decision #4 test |

---

## 4. Out-of-scope assertions (deliberate)

- Real LLM embeddings for query_vec — 8b consumes pre-computed vectors; 8f provides them via Phase 3c embedder.
- Phase 8e generation quality — Phase 12 eval.
- 8c rerank ordering — 8c phase tests.
- 8d CRAG threshold logic — 8d.

---

## 5. G3 exit criteria

- `uv run pytest tests/test_query_rrf_unit.py tests/test_query_channels_unit.py` — all ~17 tests fail with `ModuleNotFoundError` for the rrf/channels modules.
- Rest of suite (421 prior) remains green.
- This spec file committed; build_tracker §5.15.2 status updated to `G1 ✅ + G2 — + G3 ✅ + G4 🟡`.
