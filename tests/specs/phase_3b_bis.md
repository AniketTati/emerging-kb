# Phase 3b-bis — Test Spec (G3)

> **Status:** G3 open · drafted 2026-05-24 · awaiting sign-off.
> **Inputs:** Phase 3b-bis G1 plan ([build_tracker §5.8.1](../../docs/build_tracker.md)). No G2 (no API contract delta, no migration).
> **Outputs at G3:** this spec + one new red skeleton file (`tests/test_contextualization_gemini_unit.py`) + small mutation to `tests/test_contextualization_unit.py` (factory selector test widens to cover all 4 `KB_CONTEXTUALIZER` values). Imports point at `kb.contextualization.GeminiContextualizer` + the widened `make_contextualizer()` (both land at G4).

---

## 1. Scope

Every §5.8.1 G1 decision gets a matching test. Total surface: **6 new tests + 1 mutated test** (the existing factory test). On top of 238 from prior phases (232 existing + 6 new = 238).

| File | New tests | Covers |
|---|---|---|
| [`tests/test_contextualization_gemini_unit.py`](../test_contextualization_gemini_unit.py) | 6 | `GeminiContextualizer` with mocked `google.genai.Client.aio.models.generate_content` — model literal `'gemini-2.5-flash'` (#1) · prompt template verbatim from §5.8 #7 (#3) · thinking disabled (#7) · max_output_tokens=200 (#6) · response parsing + `cache_creation_input_tokens` repurposed to hold `prompt_token_count` (#4) · 4xx/network → `ContextualizationError` with `prompt_feedback` captured (#8) |
| [`tests/test_contextualization_unit.py`](../test_contextualization_unit.py) | 0 net (1 mutated) | `test_contextualizer_factory_returns_identity_when_no_api_key` widens to a parametrized matrix over `KB_CONTEXTUALIZER` (#2) — `auto`+no-keys → Identity, `auto`+Gemini-only → Gemini, `auto`+Anthropic-only → Anthropic, `auto`+both → Gemini (Gemini-first probe order), explicit `gemini` → Gemini, explicit `anthropic` → Anthropic, explicit `identity` → Identity, unknown value → raises |

**Worker-level parameterization (decision #10) is intentionally deferred to G4**, not G3. Reason: the worker tests already exercise `IdentityContextualizer` end-to-end. Parameterization is a code-only refactor with no new assertion — folding it into G4 keeps the red-skeleton surface focused on what's actually new (the Gemini adapter) instead of cosmetic rearrangement.

**Out of scope:**
- Worker E2E with Gemini path → Phase 3b-bis G5 verify script (Docker stack).
- Real Gemini API integration in pytest → `KB_GEMINI_API_KEY`-gated; G5 verify covers it.
- New migration / lifecycle / endpoint changes → none in this phase.

---

## 2. Fixture strategy

**Mock client only.** No `KB_GEMINI_API_KEY` required for pytest. The mock mimics google-genai's `Client.aio.models.generate_content` surface:
- Records `last_kwargs` for request-shape assertions.
- Returns a `GenerateContentResponse`-shaped object with `.candidates[0].content.parts[0].text`, `.usage_metadata.prompt_token_count`, `.usage_metadata.candidates_token_count`, `.prompt_feedback`.
- Configurable `raise_exc` for error-path tests.

Same shape as `_MockAnthropicClient` in `test_contextualization_unit.py` — kept side-by-side so anyone reading the tests sees the symmetry of the two adapters.

---

## 3. Decision → test mapping

| G1 # | Decision | Test(s) |
|---|---|---|
| 1 | `gemini-2.5-flash` literal + `KB_CONTEXTUAL_MODEL` override | `test_gemini_contextualizer_uses_configurable_model` |
| 2 | `KB_CONTEXTUALIZER` selector | `test_contextualizer_factory_selector_matrix` (mutated existing) |
| 3 | Verbatim Anthropic-cookbook prompt | `test_gemini_contextualizer_sends_doc_as_system_instruction` + `test_gemini_contextualizer_sends_chunk_in_user_content` |
| 4 | No caching; `prompt_token_count` → `cache_creation_input_tokens` | `test_gemini_contextualizer_records_prompt_tokens_as_cache_creation` |
| 6 | `max_output_tokens=200` | covered inside prompt-shape test |
| 7 | Thinking disabled (`thinking_budget=0`) | `test_gemini_contextualizer_disables_thinking` |
| 8 | Error → `ContextualizationError` w/ `prompt_feedback` | `test_gemini_contextualizer_api_error_raises_contextualization_error` |
| 9 | `model_id='gemini-2.5-flash'` on result | covered in `test_gemini_contextualizer_uses_configurable_model` |
| 10 | Worker parameterization | Deferred to G4 (refactor of existing tests, no new assertion) |

---

## 4. Out-of-scope assertions (deliberate)

- **Real-API smoke** — `KB_GEMINI_API_KEY`-gated; covered in `scripts/verify_phase_3b.sh` not pytest.
- **Worker chained-defer end-to-end with Gemini** — same code path as Identity/Anthropic (worker is adapter-agnostic); covered by existing worker tests + G5 verify.
- **Cross-adapter behavioral equivalence** (Gemini vs Anthropic produce semantically-similar prefixes for the same input) — not a pytest assertion; it's a quality benchmark, deferred to a future eval-harness phase.

---

## 5. G3 exit criteria

- `pytest tests/test_contextualization_gemini_unit.py` reports 6 failed / 0 passed (RED — `GeminiContextualizer` doesn't exist yet).
- Existing `pytest tests/` count remains 232 (the mutated factory test still passes against today's binary selector — the parameterized matrix only widens it; the original two assertions are preserved).
- This spec file committed; build_tracker §5.8.1 status line updated.
