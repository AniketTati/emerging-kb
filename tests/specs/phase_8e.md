# Phase 8e — Test Spec (G3)

> **Status:** G3 open · drafted 2026-05-25.
> **Inputs:** Phase 8e G1 plan ([build_tracker §5.15.5](../../docs/build_tracker.md), 15 decisions) · G2 was a no-op (no API surface change at 8e — 8f owns HTTP).
> **Outputs at G3:** this spec + 1 new red skeleton file. Imports point at `kb.query.generate.{Generator, GeminiGenerator, IdentityGenerator, make_generator, GenerationResult, Citation, _parse_result, _build_user_prompt}` — module lands at G4.

---

## 1. Scope

~14 tests in `tests/test_query_generate_unit.py`. Pure-function + mocked Gemini. No DB, no real LLM, no HTTP.

## 2. Decision → test mapping

| G1 # | Test |
|---|---|
| 1 | `test_factory_selector_matrix` (auto/explicit/anthropic→Identity/missing-key) |
| 2 | `test_gemini_generator_passes_top_10_hits` (10 of 12 appear in prompt; 11+ do not) |
| 4 | `test_generation_result_pydantic_shape` + `test_citation_pydantic_shape` |
| 5 | `test_gemini_generator_returns_inline_citation_markers` (mock returns `"Foo [abcd1234] bar"`) |
| 6 | `test_force_refuse_skips_llm_returns_refusal_envelope` |
| 7 | `test_empty_hits_skips_llm_returns_no_hits_refusal` |
| 8 | `test_gemini_generator_respects_llm_refusal` (mock returns `{refused: true, refusal_reason: "..."}`) |
| 9 | `test_parse_result_bad_json_returns_parse_error_refusal` · `test_parse_result_missing_answer_field_returns_refusal` · `test_parse_result_strips_code_fence` |
| 10 | `test_gemini_generator_llm_exception_returns_llm_error_refusal` |
| 12 | `test_gemini_generator_disables_thinking` (capture `thinking_budget=0`) |
| 13 | `test_identity_generator_returns_templated_echo` · `test_identity_generator_with_empty_hits_returns_refusal` |
| 14 | (covered by `test_factory_selector_matrix` anthropic→Identity case) |
| 15 | `test_gemini_generator_uses_system_instruction_for_astute_prompt` |

## 3. G3 exit criteria

- `uv run pytest tests/test_query_generate_unit.py --collect-only` — RED (ModuleNotFoundError).
- Rest of suite (472) remains green.
