# Phase 8a — Test Spec (G3)

> **Status:** G3 open · drafted 2026-05-25 · awaiting sign-off.
> **Inputs:** Phase 8a G1 plan ([build_tracker §5.15.1](../../docs/build_tracker.md), 10 decisions) · G2 was a no-op (no `api_contracts.md` delta — 8a is module-only; HTTP surface lands at 8f).
> **Outputs at G3:** this spec + 1 new red skeleton file. Imports point at `kb.query.rewriter.{Rewrites, IdentityQueryRewriter, GeminiQueryRewriter, AnthropicQueryRewriter, make_query_rewriter, _parse_rewrites}` — module lands at G4.

---

## 1. Scope

Every §5.15.1 G1 decision that has assertable behavior gets matching tests. Total surface: **~12 new tests** in 1 new file.

| File | New tests | Covers |
|---|---|---|
| [`tests/test_query_rewriter_unit.py`](../test_query_rewriter_unit.py) | ~12 | `Rewrites` model shape (#9) · `IdentityQueryRewriter` returns original for all 3 (#7) · `_parse_rewrites` JSON parser edge cases (#3) · factory matrix `KB_QUERY_LLM` (#2) · `KB_QUERY_MODEL` env override (#8) · Mocked Gemini path end-to-end (#1, #4, #6) · Anthropic mock path (#2) · API-error → original fallback (#7). |

No worker integration tests in 8a — module is pure-function. Worker integration lands at 8f (full orchestrator).

---

## 2. Fixture strategy

**No DB, no real LLM.** Mocked Gemini client (`_FakeClient` pattern from Phase 5a/6/7) with controllable response strings. Tests assert (a) the parser handles various JSON shapes, (b) the factory selector picks the right impl, (c) error paths fall back gracefully.

---

## 3. Decision → test mapping

| G1 # | Decision | Test(s) |
|---|---|---|
| 1 | Single LLM call returns all 3 strategies | `test_gemini_rewriter_parses_all_three_strategies` (mock returns full JSON; assert all 3 fields populated) |
| 2 | 3-impl factory | `test_factory_selector_matrix` (8 cases: auto+no-keys, auto+Gemini, auto+Anthropic, explicit gemini/anthropic/identity, gemini-without-key=ValueError, bogus=ValueError) |
| 3 | Output schema + tolerant parser | `test_parse_rewrites_handles_code_fence` · `test_parse_rewrites_handles_missing_key_fallback_to_original` · `test_parse_rewrites_handles_invalid_json_fallback_to_original` · `test_parse_rewrites_handles_non_dict_top_level` |
| 4 | Token budget 600 | structurally enforced (no test — would test the Gemini SDK) |
| 5 | Prompt format | `test_gemini_rewriter_uses_system_prompt_template` (mock captures prompt; assert system_instruction includes all 3 strategy names) |
| 6 | Thinking budget = 0 | `test_gemini_rewriter_disables_thinking` (mock captures config; assert thinking_config.thinking_budget == 0) |
| 7 | Error → fall back to original | `test_gemini_rewriter_api_error_returns_original_in_all_slots` · `test_identity_rewriter_returns_original_for_all_three` |
| 8 | Model override env | `test_factory_honors_kb_query_model_env` (set env; verify the rewriter's `_model` reflects) |
| 9 | Named-attribute output | `test_rewrites_model_named_fields` (Rewrites instance has .original / .step_back / .hyde / .query2doc) |
| 10 | No prompt caching | structurally — no `cache_control` blocks in the code (covered by code review, not pytest) |

---

## 4. Out-of-scope assertions (deliberate)

- **Real Gemini API calls** — gated on `KB_GEMINI_API_KEY`; covered at 8f via `verify_phase_8f.sh` E2E.
- **Rewriting quality / semantic verification** — eval-harness territory (Phase 12 / Wave B).
- **Caching** — Wave B.
- **Conversational context** — Phase 10b polish.
- **Per-query hyperparameter tuning** — Wave B.

---

## 5. G3 exit criteria

- `uv run pytest tests/test_query_rewriter_unit.py` — all ~12 tests fail with `ModuleNotFoundError: No module named 'kb.query'` (G3 RED).
- Rest of suite (407 prior) remains green.
- This spec file committed; build_tracker §5.15.1 status updated to `G1 ✅ + G2 — + G3 ✅ + G4 🟡`.
