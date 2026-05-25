# Phase 8f — Test Spec (G3)

> **Status:** G3 open · drafted 2026-05-25.
> **Inputs:** Phase 8f G1 plan ([build_tracker §5.15.6](../../docs/build_tracker.md), 17 decisions) + G2 ([api_contracts §7](../../docs/api_contracts.md)).
> **Outputs at G3:** this spec + 2 new red test files.

---

## 1. Scope

Two test files:

- `tests/test_query_orchestrator_unit.py` — pure-function + mocked-component orchestrator tests (~12, no DB).
- `tests/test_api_query.py` — HTTP endpoint tests over testcontainers (~14, real DB + real query_log table).

Imports point at `kb.query.orchestrator.{Orchestrator, SearchResult, ChatResult}` + `kb.api.query.{router, reset_orchestrator}`. Both land at G4 (orchestrator already drafted; HTTP router already drafted).

## 2. Decision → test mapping

### Orchestrator unit (mocked components)

| G1 # | Test |
|---|---|
| 2 | `test_orchestrator_fans_out_all_4_rewrites_to_channels` (capture call args) |
| 3 | `test_orchestrator_fuses_24_channel_lists_via_rrf` (4 rewrites × 6 channels = 24 inputs) |
| 6 | `test_orchestrator_caps_at_top_10_after_rerank` |
| 7 | `test_orchestrator_calls_crag_after_rerank` (mock crag called with reranked hits) |
| 8 | `test_orchestrator_force_refuses_generator_when_crag_below_threshold` |
| 9 | `test_orchestrator_search_returns_no_generation` (SearchResult has hits + crag_score, NO answer field) |
| 10 | `test_orchestrator_chat_returns_chat_result_envelope` |
| 16 | `test_orchestrator_chat_with_empty_corpus_returns_refusal_envelope` |

Plus shape tests:
- `test_search_result_pydantic_shape`
- `test_chat_result_pydantic_shape`
- `test_orchestrator_make_default_uses_env_factories`

### HTTP endpoint (testcontainers)

| G2 § | Test |
|---|---|
| 7.2 | `test_post_search_returns_200_with_envelope` (empty corpus is fine — refusal-style payload) |
| 7.2 | `test_post_search_400_on_empty_query` |
| 7.2 | `test_post_search_400_on_oversize_query` (>4000 chars) |
| 7.2 | `test_post_search_400_on_unsupported_mode` |
| 7.3 | `test_post_chat_returns_200_with_chat_envelope` |
| 7.3 | `test_post_chat_refusal_envelope_is_200_not_4xx` (empty corpus → 200 + refused=true) |
| 7.5 | `test_post_chat_500_translated_via_query_pipeline_error_slug` (pipeline raised → 500 with type=query-pipeline-error) |
| 7.1 #4 | `test_query_log_row_written_per_search_call` |
| 7.1 #4 | `test_query_log_row_written_per_chat_call_with_refused_true_when_no_corpus` |
| 7.1 #5 | `test_chat_idempotency_replay_returns_cached_envelope_without_re_executing` |
| 7.1 #1 | `test_search_workspace_isolation_via_x_workspace_id` (A's call doesn't see B's data) |
| 7.1 #4 | `test_query_log_rls_workspace_b_cannot_see_a_rows` |
| migration | `test_query_log_table_exists_with_rls_forced` |
| migration | `test_kb_app_cannot_update_or_delete_query_log` (immutability per GRANT) |

## 3. G3 exit criteria

- `uv run pytest tests/test_query_orchestrator_unit.py tests/test_api_query.py --collect-only` — RED (ModuleNotFoundError + endpoint 404).
- Rest of suite (491) remains green.
