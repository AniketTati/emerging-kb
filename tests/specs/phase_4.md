# Phase 4 — Test Spec (G3)

> **Status:** G3 open · drafted 2026-05-25 · awaiting sign-off.
> **Inputs:** Phase 4 G1 plan ([build_tracker §5.11](../../docs/build_tracker.md), 16 decisions) · G2 was a no-op per decision #16 (no `api_contracts.md` delta — Phase 4 has no HTTP surface).
> **Outputs at G3:** this spec + 2 new red skeleton files. Imports point at `kb.retrieval.smoke.{bm25_smoke, dense_smoke}` — module lands at G4 along with `migrations/sql/0013_indexes.sql`.

---

## 1. Scope

Every §5.11 G1 decision that has assertable behavior gets matching tests. Total surface: **10 new tests** across 2 new files. On top of 286 from prior phases (~296 expected at G5 green).

| File | New tests | Covers |
|---|---|---|
| [`tests/test_indexes.py`](../test_indexes.py) | 5 | DDL invariants — HNSW + BM25 on 4 columns with correct operator classes + kb_app role can query indexed tables (decisions #1, #2, #15) |
| [`tests/test_retrieval_smoke.py`](../test_retrieval_smoke.py) | 5 | `bm25_smoke()` + `dense_smoke()` return ranked results · multi-level hits (chunk + raptor_node) · workspace isolation via RLS · empty for unknown workspace (decisions #10, #11, #12) |

**Planner-usage tests moved to G5** (`scripts/verify_phase_4.sh`) instead of pytest. Rationale: at pytest-fixture scale (~200 fabricated rows per test) the planner correctly prefers btree index-scan + in-memory sort over HNSW — HNSW only wins above ~5K rows per workspace AND with up-to-date pg_statistic stats from ANALYZE. Forcing the planner via `SET LOCAL enable_*=off` flags tests a synthetic scenario rather than the real choice. verify_phase_4.sh exercises this at full-stack scale (5+ docs through the real ingestion pipeline + ANALYZE + EXPLAIN against realistic query shapes).

---

## 2. Fixture strategy

**Reuse existing testcontainers + lifecycle pipeline; fabricate raptor_nodes for speed.**

- `test_indexes.py` is pure DDL inspection. Uses `db_superuser` (bypasses RLS for `pg_indexes` / `EXPLAIN` reads). No seeding required for DDL bucket; planner bucket fabricates minimal data via direct SQL to give the planner something to choose between (seq scan vs index scan only differs above a row-count threshold).
- `test_retrieval_smoke.py` reuses the `client` fixture for end-to-end ingestion of 1-2 PDFs via the existing pipeline (covers `contextual_chunks` + `chunk_embeddings`), then fabricates `raptor_nodes` rows directly via SQL (so smoke calls can prove they return hits across BOTH levels without waiting for the full RAPTOR build). Same pattern as 3e's `_seeded_vectors`.

**Why fabricate raptor_nodes:** the smoke helper is the unit under test. Driving the full per-doc RAPTOR build for every smoke test would add ~30s × 5 tests = 2.5 min. Direct SQL inserts get us to "this workspace has 1 chunk + 1 raptor_node, both indexed, kick the smoke helper" in milliseconds.

**Determinism:** dense smoke uses a fixed 3072-d query vector (one-hot at index 0); seeded raptor_node embeddings use the same one-hot so cosine = 1.0 (perfect match — top hit guaranteed). BM25 smoke uses unique tokens injected at seed time (`zxqvbnm-marker-A`) so we know exactly which row should top-rank.

---

## 3. Decision → test mapping

| G1 # | Decision | Test(s) |
|---|---|---|
| 1 | 4 indexes (2 HNSW + 2 BM25) | `test_hnsw_index_exists_on_chunk_embeddings`, `test_hnsw_index_exists_on_raptor_nodes`, `test_bm25_index_exists_on_contextual_chunks`, `test_bm25_index_exists_on_raptor_nodes_text` |
| 2 | `halfvec_cosine_ops` operator class | Operator class assertion inside both `test_hnsw_*_exists` tests |
| 3 | HNSW `m=16` + `ef_construction=200` | Spot-checked at G5 via `pg_indexes.indexdef` inspection — not test-asserted (tuning knobs, not contracts). Documented in `phase_4.md` §4. |
| 4 | `CONCURRENTLY` build | Implicit — migration applies without lock errors against a live testcontainer (the migration runner's bootstrap test in Phase 0 catches DDL-lock regressions) |
| 10 | No HTTP surface | Test files import `kb.retrieval.smoke`, NOT `kb.api.*`. Code review catches violations. No HTTP test file. |
| 11 | Smoke helper signature | `test_bm25_smoke_returns_ranked_results`, `test_dense_smoke_returns_ranked_results` — both lock the `(id, score, level, scope)` 4-tuple shape |
| 12 | Test corpus via real pipeline | `seeded_indexed_workspace` fixture POSTs 1 PDF via `client` → waits for `ready` → fabricates 1 raptor_node row via SQL on top |
| 13 | Single migration `0013_indexes.sql` | Phase 0's `test_migrations.py` already asserts lexical file order; adding 0013 won't regress that test |
| 14 | No new lifecycle states | Implicit — no test references new states |
| 15 | kb_app role retains query access | `test_kb_app_can_query_indexed_tables` |
| 16 | No `api_contracts.md` delta | No new test file under `tests/` named for an HTTP endpoint |

**Planner-usage** verification happens at G5 via `scripts/verify_phase_4.sh` (not pytest) — see explanation in §1 above. At G5, EXPLAIN against realistic seed data + ANALYZE proves both HNSW and BM25 are chosen by the planner for the right query shapes.

Decisions #5 (single shared HNSW graph — no per-tenant partition), #6 (BM25 default tokenizer), #7 (BM25 Robertson defaults), #8 (`KB_HNSW_EF_SEARCH` env), #9 (`scripts/reindex_weekly.sh` stub) need no dedicated pytest — they're operational/configuration decisions verified at G5 via `verify_phase_4.sh` and code review.

---

## 4. Out-of-scope assertions (deliberate)

- **HNSW recall benchmarks** — Phase 12 eval-harness territory. Phase 4 asserts indexes EXIST and are USED, not that they're high-quality.
- **BM25 recall benchmarks** — same.
- **Per-query `ef_search` tuning** — Phase 8 (the planner can override `SET hnsw.ef_search` per query if a research-grade query wants higher recall; ambient suggestions can lower it for latency).
- **`POST /search` endpoint** — Phase 8 (decision #10).
- **RRF fusion across BM25 + dense** — Phase 8.
- **Cross-encoder rerank** — Phase 8.
- **Tree-aware top-K-per-level orchestration** — Phase 8.
- **Index rebuild scheduling** — Phase 9 (stub script only in Wave A).
- **HNSW build tuning (m, ef_construction) per-table** — Wave B / scale graduation.
- **Cross-workspace queries** — Wave C (RLS day-1 makes this an opt-out, not opt-in).

---

## 5. G3 exit criteria

- `uv run pytest tests/test_indexes.py tests/test_retrieval_smoke.py` → **9 fail (RED expected) + 1 pre-passes** (the 1 pre-passing test is `test_kb_app_can_query_indexed_tables` — decision #15 says "no GRANT changes; kb_app already has SELECT" so this test is a regression guard, not a new-behavior assertion; it correctly passes at both G3 and G5).
- Rest of suite (286 prior) remains green — no collateral damage.
- This spec file committed; build_tracker §5.11 status updated to `G1 ✅ + G2 — + G3 ✅ + G4 🟡`.

**RED-state failure mode breakdown:**
- 4 × DDL tests fail with `assert row is not None` (pg_indexes returns empty for the not-yet-created index).
- 5 × smoke helper tests fail with `ModuleNotFoundError: No module named 'kb.retrieval'`.
- 1 × kb_app SELECT test passes (regression guard — kb_app SELECT works regardless of indexes).
