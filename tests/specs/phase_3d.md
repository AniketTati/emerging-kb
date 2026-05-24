# Phase 3d — Test Spec (G3)

> **Status:** G3 open · drafted 2026-05-24 · awaiting sign-off.
> **Inputs:** Phase 3d G1 plan ([build_tracker §5.10](../../docs/build_tracker.md), 16 decisions) · Phase 3d G2 contract delta ([api_contracts.md §5.2 + §5.3](../../docs/api_contracts.md) — lifecycle enum widens with `raptor_building`; §5.3 example annotated with full post-2c stage transitions).
> **Outputs at G3:** this spec + 3 new red skeleton files. Imports point at `kb.raptor.{cluster_embeddings, build_tree_for_file}`, `kb.summarization.{Summarizer, GeminiSummarizer, AnthropicSummarizer, IdentitySummarizer, make_summarizer, SummarizationError}`, `kb.domain.raptor.{RaptorNode, insert_raptor_node, insert_raptor_edge, read_raptor_level_embeddings}`, migration 0012, the widened `files.lifecycle_state` CHECK including `raptor_building`, and `kb.workers.tasks.raptor_build_file_impl` — all land at G4.

---

## 1. Scope

Every §5.10 G1 decision gets matching tests. Total surface: **17 new tests** across 3 new files. On top of 258 from prior phases (275 expected at G5 green).

| File | New tests | Covers |
|---|---|---|
| [`tests/test_raptor_unit.py`](../test_raptor_unit.py) | 6 | `cluster_embeddings()` returns one-label-per-vector + branching arithmetic (#1, #2) + determinism (required for reproducible rebuilds) · singleton input edge case · tree termination (`n ≤ branching` AND `level > MAX`) (#4) |
| [`tests/test_summarization_unit.py`](../test_summarization_unit.py) | 6 | `GeminiSummarizer` with mocked `google.genai.Client.aio.models.generate_content` — prompt shape (#7) · thinking disabled (#7) · model literal `gemini-2.5-flash` + `KB_SUMMARIZER_MODEL` override (#6) · `IdentitySummarizer` concat-with-truncation (#5 sharpened) · `make_summarizer()` 4-value selector matrix (#5) · API error → `SummarizationError` |
| [`tests/test_raptor_worker.py`](../test_raptor_worker.py) | 5 | `raptor_build_file_impl()` end-to-end against testcontainers — writes L2 nodes + edges + transitions `embedded→raptor_building→ready` (#9, #10, #12) · `raptor_build_done` lifecycle event payload shape (#12) · idempotency on already-ready (#11/#12) · `embed_file_impl` chains `raptor_build_file` via separate-tx defer (#13) · cluster/summarize/embed failure → `raptor_building→failed` (#14) |

---

## 2. Fixture strategy

**Mock summarizer + reuse 3c's mock embedder; use real DB + real migrations.**

- `test_raptor_unit.py` is pure-function; no DB, no LLM. Inputs are synthetic numpy/list embeddings (deterministic random vectors at known dim).
- `test_summarization_unit.py` mocks `google.genai.Client.aio.models.generate_content` via the same `_MockGeminiClient` pattern from `test_contextualization_gemini_unit.py` (3b-bis) — `last_kwargs` capture + `raise_exc` injection.
- `test_raptor_worker.py` uses the testcontainers DB (real migrations including 0012) + real Procrastinate worker app + real `make_summarizer()` factory (Identity branch, since `KB_GEMINI_API_KEY` is unset in CI) + real `make_embedder()` (mock branch, same). The worker tests assert **structural** correctness (lifecycle transitions, edge FK shape, idempotency) — NOT summary content quality (that's not what Identity proves).

**Why Identity for worker tests is acceptable here:** the worker tests assert that `raptor_build_file_impl` writes correctly-shaped tree rows + correct lifecycle events. Identity summarizer produces structurally-correct nodes (just with degenerate text); the structural assertions hold either way. Tree-shape integrity tests (using mocked Gemini with stubbed text) live in `test_raptor_unit.py` since `build_tree_for_file` is testable as a pure orchestrator given injected dependencies.

---

## 3. Decision → test mapping

| G1 # | Decision | Test(s) |
|---|---|---|
| 1 | AgglomerativeClustering(cosine) for per-doc | `test_cluster_embeddings_returns_one_label_per_vector` + `test_cluster_embeddings_is_deterministic` |
| 2 | `BRANCHING_FACTOR = 8` + arithmetic | `test_cluster_embeddings_branching_factor_arithmetic` |
| 3 | `MAX_LEVELS = 6` (covered by termination tests) | implicit in `test_tree_terminates_when_max_levels_reached` |
| 4 | Three termination conditions | `test_tree_terminates_when_n_le_branching` + `test_tree_terminates_when_max_levels_reached` + `test_cluster_singleton_returns_single_label` (degenerate `n=1` case) |
| 5 | 3-impl Summarizer + selector | `test_identity_summarizer_concatenates_input_texts` + `test_summarizer_factory_selector_matrix` |
| 6 | `gemini-2.5-flash` default + `KB_SUMMARIZER_MODEL` override | `test_gemini_summarizer_uses_configurable_model` |
| 7 | Prompt + `thinking_budget=0` + `max_output_tokens=600` | `test_gemini_summarizer_sends_chunks_with_correct_prompt` + `test_gemini_summarizer_disables_thinking` |
| 8 | `Semaphore(4)` concurrency | covered structurally by 3b-bis's same-pattern test — skipped here for surface bloat reasons; G5 verify exercises the chained-build end-to-end |
| 9 | L1 stays in contextual_chunks (no denormalization) | `test_raptor_build_file_impl_writes_l2_nodes_and_edges` asserts no `level=1` rows in raptor_nodes; edges reference `child_contextual_chunk_id` |
| 10 | Discriminated edge FK + CHECK | `test_raptor_build_file_impl_writes_l2_nodes_and_edges` asserts row CHECK enforcement via DB constraint behavior |
| 11 | Immutable raptor_nodes/edges | implicit — Phase 9 audit covers REVOKE; not 3d's surface |
| 12 | `embedded → raptor_building → ready` | `test_raptor_build_writes_raptor_build_done_lifecycle_event` |
| 13 | Chained defer from `embed_file_impl` | `test_embed_file_impl_chains_raptor_build_via_defer` |
| 14 | Failure mode | `test_raptor_build_failure_writes_failed_event` |
| 15 | Embedder reuse (3c's factory) | implicit — worker uses `make_embedder()` and writes to `raptor_nodes.embedding halfvec(3072)` |
| 16 | `scope` enum + nullable `file_id` forward-compat | implicit — schema visible via worker tests; explicit assertion lives in G5 verify_phase_3d.sh DDL checks |

---

## 4. Out-of-scope assertions (deliberate)

- **Real Gemini API smoke** — `KB_GEMINI_API_KEY`-gated; covered in `scripts/verify_phase_3d.sh` not pytest.
- **Summary content quality** — not a unit-test concern; cross-model summary benchmarking is eval-harness territory (deferred).
- **`Semaphore(4)` concurrency cap** — same pattern as 3b-bis (`test_gemini_contextualizer_caps_concurrent_calls_at_4`); not re-tested here to keep surface minimal.
- **HNSW + BM25 indexing on raptor_nodes** — Phase 4.
- **Corpus-level RAPTOR (`scope='corpus'`)** — Phase 3e (§5.10.1).
- **REVOKE UPDATE/DELETE on kb_app** — pattern proven at Phase 2a/3a/3b/3c; trust the migration; G5 verify asserts.
- **`audit_log` writes on tree builds** — Phase 9.

---

## 5. G3 exit criteria

- `pytest tests/test_raptor_unit.py tests/test_summarization_unit.py tests/test_raptor_worker.py` → all 16 fail (ImportError or constraint missing).
- Rest of suite (258 prior) remains green — no collateral damage.
- This spec file committed; build_tracker §5.10 status updated to `G1 ✅ + G2 ✅ + G3 ✅ + G4 🟡`.
