# Phase 8d — Test Spec (G3)

> **Status:** G3 open · drafted 2026-05-25.
> **Inputs:** Phase 8d G1 plan ([build_tracker §5.15.4](../../docs/build_tracker.md), 10 decisions) · G2 was a no-op.
> **Outputs at G3:** this spec + 1 new red skeleton file. Imports point at `kb.query.crag.{CragGate, GeminiCragGate, IdentityCragGate, make_crag_gate, CRAG_THRESHOLD, _parse_score}` — module lands at G4.

---

## 1. Scope

~11 tests in `tests/test_query_crag_unit.py`. Pure-function + mocked Gemini.

## 2. Decision → test mapping

| G1 # | Test |
|---|---|
| 1 | `test_factory_selector_matrix` (4-5 cases, KB_QUERY_LLM env) |
| 2 | `test_crag_threshold_constant` (= 0.5) |
| 4 | `test_parse_score_valid_float` · `test_parse_score_invalid_returns_default_1` · `test_parse_score_clamps_to_0_1` · `test_parse_score_handles_code_fence` · `test_parse_score_handles_non_dict_returns_1` |
| 5 | `test_crag_returns_zero_for_empty_hits` |
| 6 | `test_identity_crag_always_returns_1` |
| 7 | `test_gemini_crag_api_error_returns_1` |
| 8 | `test_gemini_crag_disables_thinking` |

## 3. G3 exit criteria

- `uv run pytest tests/test_query_crag_unit.py --collect-only` — RED (ModuleNotFoundError).
- Rest of suite (456) remains green.
