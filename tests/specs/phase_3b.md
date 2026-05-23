# Phase 3b — Test Spec (G3)

> **Status:** G3 open · drafted 2026-05-23 · awaiting sign-off.
> **Inputs:** Phase 3b G1 plan ([build_tracker §5.8](../../docs/build_tracker.md)) · Phase 3b G2 contract delta ([api_contracts.md §5.1 #3 + §5.2](../../docs/api_contracts.md)).
> **Outputs at G3:** this spec + two new red skeleton files (`test_contextualization_unit.py`, `test_contextualization_worker.py`). Imports point at `kb.contextualization` + `kb.domain.contextual_chunks` + extended `kb.workers.tasks` (all land at G4).

---

## 1. Scope

Every G1 decision in [build_tracker §5.8](../../docs/build_tracker.md) gets a matching test:
- Model choice & override (#1) · prompt-cache placement (#2) · adapter pattern (#5) · IdentityContextualizer self-disable (#6) · prompt template (#7) · output budget (#8) · thinking disabled (#9) · table immutability (#10) · cache metrics persisted (#11) · lifecycle widening (#12) · task chaining (#13) · failure mode (#14).

Two files cover the surface (**15 new tests**, on top of 204 from prior phases):

| File | New tests | Covers |
|---|---|---|
| [`tests/test_contextualization_unit.py`](../test_contextualization_unit.py) | 9 | `AnthropicContextualizer` with mock client — request shape (system+cache_control+user role split) · prompt template · response parsing · cache metrics on response · `IdentityContextualizer` empty-prefix fallback · `model_id='identity'` · 4xx → `ContextualizationError` · `thinking={'type':'disabled'}` request shape · factory returns Identity when no API key |
| [`tests/test_contextualization_worker.py`](../test_contextualization_worker.py) | 6 | `contextualize_file_impl()` end-to-end against testcontainers — chunked→contextualized transition · `contextualization_done` event with cache totals · idempotency · `chunk_file_impl` chains via defer · IdentityContextualizer fallback when API key absent (lifecycle still advances; `contextual_text == chunk_text`) · REVOKE UPDATE on kb_app |

**Out of scope (Phase 3c / 4 / 9):**
- Embedding contextual chunks → 3c.
- RAPTOR build → 3c.
- HNSW + BM25 indexes on `contextual_chunks.contextual_text` → Phase 4.
- Real-API integration tests (KB_ANTHROPIC_API_KEY-gated) → Phase 3b G5 verify script optional path; not in pytest.
- `audit_log` writes → Phase 9.

---

## 2. Fixture strategy

Reuses Phase 0+1a+1b+1c+2a+2b+3a's testcontainers + `client` + `db_url_superuser` fixture pattern unchanged. No new fixture files.

Unit tests inject a `MockAnthropicClient` class defined inline that mimics `anthropic.AsyncAnthropic` — a `messages.create()` async method returning a fake response object with `.content` (list of content blocks) and `.usage` (with `cache_creation_input_tokens`, `cache_read_input_tokens`).

Worker tests use the same end-to-end pattern as 3a: POST tiny.pdf via HTTP client, await `parse_file_impl(fid)`, await `chunk_file_impl(fid)`, then await `contextualize_file_impl(fid)` — all direct calls bypassing Procrastinate's queue for bounded per-test runtime.

---

## 3. Conventions

Same as Phase 0+1a+1b+1c+2a+2b+3a. Plus:
- **Unit tests never make real Anthropic calls** — every `AnthropicContextualizer` test uses an injected mock.
- **Worker tests env-var-gate the API key:** the worker fixture sets `KB_ANTHROPIC_API_KEY=""` so the worker selects `IdentityContextualizer` via factory; tests asserting on the Anthropic path inject a mock directly.
- **`KB_CONTEXTUAL_MODEL`** override is exercised — one test asserts the user-overridable model defaults to `claude-opus-4-7`.

---

## 4. Test inventory

### 4.1 `tests/test_contextualization_unit.py` — adapter unit tests (9)

| Test | Intent |
|---|---|
| `test_anthropic_contextualizer_sends_doc_as_cached_system_block` | Mock client records the request. Assert: `system` is a list with at least one block, last block carries `cache_control={'type':'ephemeral'}`, system text contains the doc context. Decision #2 + #7. |
| `test_anthropic_contextualizer_sends_chunk_in_user_message` | Same recorded request — chunk text appears in `messages[0]` with `role='user'`, NOT in system. Decision #7. |
| `test_anthropic_contextualizer_parses_prefix_from_response` | Mock returns `content=[{'type':'text','text':'This chunk is from the ACME 10-K and describes Q3 revenue.'}]`. `contextualize()` returns `ContextualizedChunk(prefix='This chunk is from...', ...)`. |
| `test_anthropic_contextualizer_records_cache_metrics` | Mock response has `usage.cache_creation_input_tokens=4500` + `usage.cache_read_input_tokens=4500`. Returned ContextualizedChunk carries both values. Decision #11. |
| `test_identity_contextualizer_returns_empty_prefix` | `IdentityContextualizer().contextualize(doc_text='foo', chunk_text='bar')` returns `ContextualizedChunk(prefix='', contextual_text='bar', model_id='identity', cache_creation_input_tokens=0, cache_read_input_tokens=0)`. Decision #6. |
| `test_contextualizer_factory_returns_identity_when_no_api_key` | `make_contextualizer()` reads `KB_ANTHROPIC_API_KEY`. With env unset → returns `IdentityContextualizer` instance. With env set → returns `AnthropicContextualizer`. Decision #6. |
| `test_anthropic_contextualizer_4xx_raises_contextualization_error` | Mock client raises `anthropic.RateLimitError`. `contextualize()` raises `ContextualizationError` with `error_class='RateLimitError'`. Decision #14. |
| `test_anthropic_contextualizer_uses_disabled_thinking` | Mock records `thinking={'type':'disabled'}` in the request kwargs. Decision #9. |
| `test_anthropic_contextualizer_uses_configurable_model` | Default model is `claude-opus-4-7`. Setting `KB_CONTEXTUAL_MODEL='claude-haiku-4-5'` env → request uses that model. Decision #1. |

### 4.2 `tests/test_contextualization_worker.py` — worker integration tests (6)

| Test | Intent |
|---|---|
| `test_contextualize_file_impl_reads_chunks_and_writes_contextual_rows` | POST tiny.pdf → parse → chunk → `contextualize_file_impl(fid)` (with mock Anthropic client injected via env or constructor) → `SELECT count(*) FROM contextual_chunks WHERE file_id=%s` ≥ 1 AND `files.lifecycle_state='contextualized'`. Decision #12. |
| `test_contextualize_file_impl_writes_contextualized_lifecycle_event` | After contextualize: lifecycle event with `from_state='chunked'`, `to_state='contextualized'`, `event='contextualization_done'`, payload includes `prefix_count`, `total_cache_creation_tokens`, `total_cache_read_tokens`, `model_id`. |
| `test_contextualize_file_impl_is_idempotent_on_already_contextualized` | Run contextualize twice on the same file → second is no-op; chunks count unchanged; only one `contextualization_done` event. Decision (per-stage idempotency). |
| `test_chunk_file_impl_chains_contextualize_file_via_defer` | Run chunk_file_impl on a tiny.pdf → query `procrastinate_jobs` and confirm `contextualize_file` task is queued with that file_id. Decision #13. |
| `test_contextualize_file_impl_identity_fallback_when_no_api_key` | Set `KB_ANTHROPIC_API_KEY=''` in test env. `contextualize_file_impl(fid)` still advances file to `contextualized`; `model_id='identity'` in every row; `contextual_text == chunks.text` byte-for-byte. Decision #6. |
| `test_contextual_chunks_table_rejects_update_via_kb_app` | kb_app role gets `psycopg.errors.InsufficientPrivilege` on `UPDATE contextual_chunks SET contextual_prefix='x' WHERE ...`. Decision #10. |

---

## 5. What "green" means at G4

When all of the following pass, G3 is satisfied and G4 closes:
1. `uv run pytest tests/` exits 0.
2. All 204 prior tests stay green.
3. The 9 unit + 6 worker tests = 15 new tests, all green.
4. Coverage of `src/kb/contextualization/__init__.py` is ≥ 90% (a small adapter module).
5. Total: 204 + 15 = **219 tests**. (`pytest --collect-only` is authoritative.)

---

## 6. Sign-off

When Aniket approves this spec + the skeleton files, the Phase 3b G3 cell in [build_tracker §5](../../docs/build_tracker.md) flips 🟡 → ✅ and G4 (build) opens. Sign-off recorded in `build_tracker.md` §9.

---

## 7. Change log

| Date | Change | By |
|---|---|---|
| 2026-05-23 | Spec drafted at Phase 3b G3 open. Two buckets: 9 adapter unit tests + 6 worker integration tests. 15 new tests total. Suite goes 204 → 219. Awaiting sign-off. | Aniket |
