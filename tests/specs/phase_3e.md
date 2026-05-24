# Phase 3e — Test Spec (G3)

> **Status:** G3 open · drafted 2026-05-24 · awaiting sign-off.
> **Inputs:** Phase 3e G1 plan ([build_tracker §5.10.1](../../docs/build_tracker.md), 15 decisions) · Phase 3e G2 contract delta ([api_contracts.md §6](../../docs/api_contracts.md) — new section with POST /corpus/raptor/rebuild).
> **Outputs at G3:** this spec + 3 new red skeleton files. Imports point at `kb.raptor.corpus.{cluster_embeddings_corpus, read_doc_roots_for_workspace, build_corpus_tree}`, `kb.workers.tasks.raptor_build_corpus_impl`, the new `kb.api.corpus` router + its mount in `kb.api.main` — all land at G4.

---

## 1. Scope

Every §5.10.1 G1 decision gets matching tests. Total surface: **11 new tests** across 3 new files. On top of 275 from prior phases (286 expected at G5 green).

| File | New tests | Covers |
|---|---|---|
| [`tests/test_raptor_corpus_unit.py`](../test_raptor_corpus_unit.py) | 4 | `cluster_embeddings_corpus()` returns one label per vector + branching arithmetic + determinism (#1, #4, #10) · `read_doc_roots_for_workspace` returns heterogeneous (per-doc roots + singleton contextual_chunks) (#6) |
| [`tests/test_raptor_corpus_worker.py`](../test_raptor_corpus_worker.py) | 4 | `raptor_build_corpus_impl()` end-to-end against testcontainers — writes `scope='corpus'` nodes + cross-scope edges (#7, #9) · atomic DELETE-all + INSERT-new on re-trigger (#9) · deterministic rebuild produces stable IDs given stable input (#10) · tiny-corpus skip-when-N≤1 (#13) |
| [`tests/test_corpus_api.py`](../test_corpus_api.py) | 3 | `POST /corpus/raptor/rebuild` → 202 with task_id (#11, #12) · `400 corpus-rebuild-no-input` when workspace has no docs · `503 corpus-rebuild-in-flight` when a job is already queued |

---

## 2. Fixture strategy

**Bypass the per-doc pipeline; seed corpus inputs directly.**

- `test_raptor_corpus_unit.py` is pure-function; no DB, no LLM, no UMAP-internal randomness (uses fixed seed).
- `test_raptor_corpus_worker.py` uses testcontainers DB + real migrations. The worker takes a workspace_id, reads doc-roots, and writes corpus nodes. Tests seed N=10 doc-roots via direct SQL — fabricate file rows + per-doc raptor_nodes rows (or contextual_chunks rows for the singleton-leaf test). Faster + more isolated than driving real Docling+Anthropic+Gemini.
- `test_corpus_api.py` uses the httpx test `client` fixture. Tests POST /corpus/raptor/rebuild and assert on response + `procrastinate_jobs` state. Worker doesn't actually execute (jobs sit in `todo` state in the test DB).

**Why fabricated seeds:** the corpus worker is the unit under test. Driving the full upstream chain (parse → chunk → contextualize → embed → per-doc RAPTOR for 10 files) would take 5+ minutes and exercise code we've already tested. Fabricated SQL inserts get us to "10 doc-roots in this workspace, kick the corpus build" in milliseconds.

---

## 3. Decision → test mapping

| G1 # | Decision | Test(s) |
|---|---|---|
| 1 | UMAP+GMM clustering | `test_cluster_embeddings_corpus_returns_one_label_per_vector` |
| 2/3 | UMAP `n_components=10` + `n_neighbors=15` defaults | covered structurally inside #1 (we don't unit-test specific UMAP outputs — that's testing the library; we test API contract + determinism) |
| 4 | `n_components = ceil(N/branching)` arithmetic | `test_cluster_embeddings_corpus_branching_arithmetic` |
| 6 | Heterogeneous doc-root source (per-doc roots + singleton contextual_chunks) | `test_read_doc_roots_returns_heterogeneous_kinds` |
| 7 | Discriminated edge FK for corpus L2 (cross-scope edges) | `test_raptor_build_corpus_writes_scope_corpus_nodes_and_cross_scope_edges` |
| 8 | Explicit POST trigger (not auto) | `test_post_corpus_rebuild_returns_202_with_task_id` (POST defers; no auto-fire from any file event in the worker module) |
| 9 | Atomic rebuild (DELETE all + INSERT new) | `test_raptor_build_corpus_atomic_rebuild_replaces_old_rows` |
| 10 | Determinism via random_state | `test_cluster_embeddings_corpus_is_deterministic` |
| 11 | Open endpoint in Wave A | covered structurally — no auth dep in test fixture |
| 12 | 202 Accepted response shape | `test_post_corpus_rebuild_returns_202_with_task_id` |
| 13 | Tiny-corpus skip (N≤1) | `test_raptor_build_corpus_skips_when_only_one_doc` |
| 14 | Reuse Summarizer + Embedder factories | implicit — worker calls `make_summarizer()` + `make_embedder()`; tests use the Identity + DeterministicMockEmbedder branches (no API keys in CI) |
| **API** | `400 corpus-rebuild-no-input` | `test_post_corpus_rebuild_rejects_empty_workspace` |
| **API** | `503 corpus-rebuild-in-flight` | `test_post_corpus_rebuild_rejects_when_job_already_queued` |

Decisions #5 (MAX_LEVELS=6 reused from 3d) and #15 (no new tables, audit via procrastinate_jobs) need no dedicated tests — structural reuse.

---

## 4. Out-of-scope assertions (deliberate)

- **Real Gemini/UMAP API smoke** — `KB_GEMINI_API_KEY`-gated; covered in `scripts/verify_phase_3e.sh` not pytest.
- **Corpus tree quality / semantic coherence** — not a unit-test concern; cross-model corpus benchmarking is eval-harness territory (deferred).
- **UMAP-specific hyperparameter behavior** (n_components, n_neighbors, metric) — trust the upstream library; we assert the wrapper interface.
- **Status polling endpoint** — Phase 9.
- **`GET /corpus/raptor`** read endpoint — Phase 8+.
- **Incremental rebuild** — Phase 5+.
- **Admin RBAC** — Phase 9 (Wave A ships open per user direction).

---

## 5. G3 exit criteria

- `pytest tests/test_raptor_corpus_unit.py tests/test_raptor_corpus_worker.py tests/test_corpus_api.py` → all 11 fail (ImportError or `kb.api.corpus` module missing).
- Rest of suite (275 prior) remains green — no collateral damage.
- This spec file committed; build_tracker §5.10.1 status updated to `G1 ✅ + G2 ✅ + G3 ✅ + G4 🟡`.
