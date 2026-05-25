# Phase 8c — Test Spec (G3)

> **Status:** G3 open · drafted 2026-05-25.
> **Inputs:** Phase 8c G1 plan ([build_tracker §5.15.3](../../docs/build_tracker.md), 12 decisions) · G2 was a no-op.
> **Outputs at G3:** this spec + 1 new red skeleton file. Imports point at `kb.query.rerank.{Reranker, IdentityReranker, CohereReranker, MxBaiReranker, make_reranker}` — module lands at G4.

---

## 1. Scope

~13 tests in `tests/test_query_rerank_unit.py`. All pure-function / mocked-client — no DB, no real Cohere API.

---

## 2. Decision → test mapping

| G1 # | Test |
|---|---|
| 1, 4 | `test_factory_default_is_cohere_when_key_set` · `test_factory_selector_matrix` (6 cases) |
| 2 | `test_factory_auto_falls_back_to_identity_when_no_cohere_key` · `test_factory_mxbai_is_opt_in_not_auto` |
| 5 | `test_cohere_reranker_honors_kb_cohere_rerank_model_env` |
| 6 | `test_reranker_top_k_truncates_input` |
| 7 | `test_cohere_api_error_falls_back_to_passthrough` · `test_mxbai_missing_dep_falls_back_to_passthrough` |
| 8 | `test_reranker_sees_hit_snippet_as_document` |
| 9 | `test_cohere_rerank_updates_score_and_metadata` |
| 10 | `test_reranker_returns_empty_for_empty_input` |
| 11, 12 | `test_cohere_reranker_uses_async_client_v2_pattern` (mock) · `test_mxbai_reranker_lazy_singleton` (mock) |
| — | `test_identity_reranker_passthrough_preserves_order` |

---

## 3. G3 exit criteria

- `uv run pytest tests/test_query_rerank_unit.py` — collection fails with `ModuleNotFoundError: No module named 'kb.query.rerank'` (RED).
- Rest of suite (441 prior) remains green.
