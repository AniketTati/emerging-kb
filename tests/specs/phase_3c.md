# Phase 3c — Test Spec (G3)

> **Status:** G3 open · drafted 2026-05-23 · awaiting sign-off.
> **Inputs:** Phase 3c G1 plan ([build_tracker §5.9](../../docs/build_tracker.md)) · Phase 3c G2 contract delta ([api_contracts.md §5.1 #3 + §5.2](../../docs/api_contracts.md)).
> **Outputs at G3:** this spec + two new red skeleton files (`test_embeddings_unit.py`, `test_embeddings_worker.py`).

---

## 1. Scope

Every G1 decision in [build_tracker §5.9](../../docs/build_tracker.md) gets a matching test:
- Model choice & override (#1) · halfvec storage (#2) · adapter pattern (#3) · self-disable fallback (#4) · mock embedder determinism (#5) · batching (#6) · table immutability (#8) · UNIQUE composite (#9) · lifecycle widening (#10) · task chaining (#11) · idempotency (#12) · failure mode (#13).

Two files cover the surface (**13 new tests**, on top of 219 from prior phases):

| File | New tests | Covers |
|---|---|---|
| [`tests/test_embeddings_unit.py`](../test_embeddings_unit.py) | 7 | `GeminiEmbedder` with mock SDK client (request shape — batch input, model name) · `DeterministicMockEmbedder` reproducibility (same text → same vector across calls) · vector dim=3072 · L2 normalization (unit length) · model_id distinguishes mock vs real · batch returns N vectors for N inputs · factory returns mock when no API key, real when key set |
| [`tests/test_embeddings_worker.py`](../test_embeddings_worker.py) | 6 | `embed_file_impl()` end-to-end against testcontainers — contextualized→embedded transition · embedding_done event payload (embedding_count, dim, model_id) · idempotency · `contextualize_file` chains `embed_file` via defer · DeterministicMockEmbedder fallback path (lifecycle still advances) · REVOKE UPDATE on kb_app |

**Out of scope (Phase 3d / 4 / 9):**
- RAPTOR tree build → 3d.
- HNSW index on `chunk_embeddings.embedding` → Phase 4.
- BM25 index on contextual_text → Phase 4.
- Real-API integration tests (KB_GEMINI_API_KEY-gated) → optional path in G5 verify.
- `audit_log` writes → Phase 9.

---

## 2. Fixture strategy

Reuses Phase 0–3b's testcontainers + `client` + `db_url_superuser` fixture pattern unchanged. No new fixture files.

Unit tests inject a `MockGeminiClient` defined inline. Worker tests use the same chained pattern as 3b: POST tiny.pdf, await parse → chunk → contextualize → embed_file_impl directly.

---

## 3. Conventions

Same as Phase 0–3b. Plus:
- **Unit tests never make real Gemini calls** — every `GeminiEmbedder` test uses an injected mock SDK client.
- **Worker tests env-var-gate the API key:** the worker fixture sets `KB_GEMINI_API_KEY=""` so the worker selects `DeterministicMockEmbedder` via factory.
- **`KB_EMBEDDING_MODEL`** override is exercised — one unit test asserts the default model `gemini-embedding-001` and the override flow.

---

## 4. Test inventory

### 4.1 `tests/test_embeddings_unit.py` — adapter unit tests (7)

| Test | Intent |
|---|---|
| `test_gemini_embedder_sends_batch_with_model_name` | Mock client records the request kwargs. Assert: `model` matches default (`gemini-embedding-001`) and `KB_EMBEDDING_MODEL` override; input batch is sent as a list. Decision #1 + #6. |
| `test_deterministic_mock_embedder_is_reproducible_across_calls` | Same input text → same output vector across 3 calls. Decision #5. |
| `test_deterministic_mock_embedder_returns_3072_dim_vectors` | Output dim is exactly 3072. Decision #2. |
| `test_deterministic_mock_embedder_l2_normalizes_to_unit_length` | `numpy.linalg.norm(vec) == 1.0` within tolerance. Decision #5. |
| `test_mock_embedder_model_id_distinguishes_from_real` | `EmbeddingResult.model_id == 'mock-deterministic-v1'`. Decision #4. |
| `test_embedder_factory_returns_mock_when_no_api_key` | `make_embedder()` reads `KB_GEMINI_API_KEY`. Unset → DeterministicMockEmbedder; set → GeminiEmbedder. Decision #4. |
| `test_embed_batch_returns_one_vector_per_input` | `embed_batch([t1, t2, t3])` returns list of length 3. Decision #6. |

### 4.2 `tests/test_embeddings_worker.py` — worker integration tests (6)

| Test | Intent |
|---|---|
| `test_embed_file_impl_reads_contextual_chunks_and_writes_embedding_rows` | POST tiny.pdf → parse → chunk → contextualize → `embed_file_impl(fid)` → `SELECT count(*) FROM chunk_embeddings WHERE file_id=%s` ≥ 1 AND `files.lifecycle_state='embedded'`. Decision #10. |
| `test_embed_file_impl_writes_embedding_done_lifecycle_event` | After embed_file: lifecycle event with `from_state='contextualized'`, `to_state='embedded'`, `event='embedding_done'`, payload includes `embedding_count`, `dim`, `model_id`. |
| `test_embed_file_impl_is_idempotent_on_already_embedded` | Run embed twice on the same file → second is no-op; chunk_embeddings count unchanged; one embedding_done event. Decision #12. |
| `test_contextualize_file_impl_chains_embed_file_via_defer` | Run contextualize_file_impl on tiny.pdf → query `procrastinate_jobs` and confirm `embed_file` task queued with the file_id. Decision #11. |
| `test_embed_file_impl_uses_mock_when_no_api_key` | Without `KB_GEMINI_API_KEY`, embed advances lifecycle; `model_id='mock-deterministic-v1'` in every row. Decision #4. |
| `test_chunk_embeddings_table_rejects_update_via_kb_app` | kb_app role gets `psycopg.errors.InsufficientPrivilege` on UPDATE. Decision #8. |

---

## 5. What "green" means at G4

When all of the following pass, G3 is satisfied and G4 closes:
1. `uv run pytest tests/` exits 0.
2. All 219 prior tests stay green.
3. The 7 unit + 6 worker tests = 13 new tests, all green.
4. Coverage of `src/kb/embeddings/__init__.py` is ≥ 90%.
5. Total: 219 + 13 = **232 tests**.

---

## 6. Sign-off

When Aniket approves this spec + skeleton files, Phase 3c G3 cell in [build_tracker §5](../../docs/build_tracker.md) flips 🟡 → ✅ and G4 (build) opens. Sign-off recorded in `build_tracker.md` §9.

---

## 7. Change log

| Date | Change | By |
|---|---|---|
| 2026-05-23 | Spec drafted at Phase 3c G3 open. Two buckets: 7 adapter unit tests + 6 worker integration tests. 13 new tests total. Suite goes 219 → 232. Awaiting sign-off. | Aniket |
