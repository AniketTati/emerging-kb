# Phase 7 — Test Spec (G3)

> **Status:** G3 open · drafted 2026-05-25 · awaiting sign-off.
> **Inputs:** Phase 7 G1 plan ([build_tracker §5.14](../../docs/build_tracker.md), 14 decisions) · G2 was a no-op (no `api_contracts.md` delta — Phase 7 has no new HTTP surface).
> **Outputs at G3:** this spec + 2 new red skeleton files + 1 mutated test. Imports point at `kb.identity.judge`, `kb.identity.resolve`, `kb.domain.entities`, `kb.workers.tasks.resolve_identities_file_impl` — modules land at G4.

---

## 1. Scope

Every §5.14 G1 decision that has assertable behavior gets matching tests. Total surface: **~22 new tests** across 2 new files (+1 mutated existing test).

| File | New tests | Covers |
|---|---|---|
| [`tests/test_identity_unit.py`](../test_identity_unit.py) | ~12 | Pure-function: threshold constants (#3) · `_parse_judgment` JSON parser edge cases (#6) · `NoopIdentityJudge` returns False always (#6) · factory matrix `KB_IDENTITY_JUDGE` (#6) · `ResolutionResult` dataclass shape · `Mention`-text-embedding integration smoke (#7). |
| [`tests/test_identity_worker.py`](../test_identity_worker.py) | ~10 | Testcontainer integration: state-guard rejects non-`identity_resolving` (#1) · 4-stage algorithm coverage (deterministic match / embedding match / LLM judge / new entity creation) (#3) · deterministic cross-doc collapse (case-insensitive lower-name match) (#11) · re-run idempotency (DELETE+INSERT, count stable) (#8) · empty-mentions edge case · lifecycle event with method counts payload (#13) · 4 method counts (deterministic/embedding/llm_judge/new) sum to mention count · entities table accumulates across files (#4). |
| [`tests/test_entities_worker.py`](../test_entities_worker.py) | mutation: 2 assertions | Updated `assert state == 'ready'` → `assert state == 'identity_resolving'` to match Phase 7 lifecycle widening (#9). |

---

## 2. Fixture strategy

**Pure-function tests** (`test_identity_unit.py`): no DB, no LLM. The `_FakeClient` pattern from Phase 5a/6 mocks `google.genai.Client.aio.models.generate_content` for any LLM-path tests.

**Worker integration tests** (`test_identity_worker.py`): use `db_url_superuser` for direct seed via SQL — fabricate `files` row in `identity_resolving` state + `extracted_mentions` rows. Faster + more isolated than driving the full upstream chain (parse → chunk → contextualize → embed → raptor → mentions → fields → units → entities → THEN identity). Per the Phase 5 fixture-strategy convention.

Mention-text embeddings: tests use the DeterministicMockEmbedder from Phase 3c (`KB_EMBEDDING_MODEL='mock-deterministic-v1'`) — every distinct text → distinct deterministic vector. Ensures embedding-blocking test cases are reproducible without a real Gemini key.

---

## 3. Decision → test mapping

| G1 # | Decision | Test(s) |
|---|---|---|
| 1 | Auto-chained trigger; lifecycle `entities_extracting → identity_resolving → ready` | `test_resolve_identities_skips_non_identity_resolving` (state guard); `test_resolve_identities_*_advances_to_ready` (terminal transition); `tests/test_entities_worker.py` mutation (Phase 6 end-state changed to `identity_resolving`). |
| 2 | Wave A scope: mentions only | Worker tests work on `extracted_mentions`; `extracted_entities` untouched (implicit assertion via row counts). |
| 3 | 4-stage algorithm + thresholds | `test_thresholds_are_sensible` (constants); `test_resolve_identities_creates_new_entities_for_unique_mentions` (stage d); `test_resolve_identities_deterministic_match_reuses_entity` (stage a); embedding + LLM-judge stages covered structurally via cross-doc tests + Noop fallback. |
| 4 | `entities` storage shape | DDL invariants in `verify_phase_7.sh`; worker tests assert row inserts + UPDATE on mention_count via `read_existing_entities`-style queries. |
| 5 | `mention_to_entity` storage shape | Worker tests SELECT count from table; `_resolved_method` is in {deterministic, embedding, llm_judge, identity}. |
| 6 | LLM-judge factory | `test_factory_selector_matrix` (8-case: auto+no-keys, auto+Gemini, auto+Anthropic, explicit gemini/anthropic/identity, gemini-without-key=ValueError, bogus=ValueError); `test_noop_judge_always_false`; `test_parse_judgment_*` (5 JSON-parser edge cases). |
| 7 | Reuses `make_embedder()` | Worker tests run with Identity LLM judge but real (mock) embedder — embeddings populate `entities.embedding`; verify_phase_7.sh asserts via SQL. |
| 8 | Idempotency via DELETE+INSERT | `test_resolve_identities_re_run_is_idempotent`. |
| 9 | Lifecycle widening | `verify_phase_7.sh` step: pg_constraint CHECK includes `identity_resolving`. Forward-compat: cross-phase sweep proves 0009/0012/0014/0017 re-apply against polluted DB without CheckViolation. |
| 10 | HNSW index | `verify_phase_7.sh` step: pg_indexes shows `entities_embedding_hnsw_idx` USING hnsw + halfvec_cosine_ops + partial WHERE embedding IS NOT NULL. |
| 11 | Case-insensitive deterministic match | `test_resolve_identities_deterministic_match_reuses_entity` uses "Acme Corp" + "ACME CORP" → 1 entity. |
| 12 | Sequential per file | Implicit — worker tests run resolver sequentially without semaphore. |
| 13 | Single `identities_resolved` lifecycle event with method-counts payload | Worker tests assert `file_lifecycle` count for `identities_resolved`=1; payload shape verified via DB read. |

---

## 4. Out-of-scope assertions (deliberate)

- **Real Gemini judge calls** — gated on `KB_GEMINI_API_KEY`; covered by `verify_phase_7.sh` E2E with-key branch (CI runs with Identity).
- **Cross-workspace identity isolation** — RLS already enforces; not re-asserted per phase.
- **Entity-merge admin endpoint** — Phase 9.
- **Re-resolution after schema change** — Phase 9.
- **Persistent union-find clustering** — Wave B.
- **`extracted_entities` typed-row resolution** — Wave B.
- **ColPali / image-mention resolution** — Wave C.

---

## 5. G3 exit criteria

- `uv run pytest tests/test_identity_unit.py tests/test_identity_worker.py tests/test_entities_worker.py` — full pass after G4 build (~22 new + 4 mutated).
- Rest of suite (370 prior) remains green; specifically `test_entities_worker.py`'s 2 assertions about Phase 6 end-state now require `identity_resolving`.
- This spec file committed; build_tracker §5.14 status updated to `G1 ✅ + G2 — + G3 ✅ + G4 🟡`.

**RED-state failure mode breakdown (pre-G4):**
- 4 × `test_identity_worker.py` integration tests fail with `ModuleNotFoundError: No module named 'kb.identity'` OR `psycopg.errors.UndefinedTable: extracted_mentions` (if migration not applied — covered by db_migrated fixture).
- ~12 × `test_identity_unit.py` tests fail with same ImportError.
- 2 × `test_entities_worker.py` assertions fail with `'ready' != 'identity_resolving'` after the lifecycle change at G4.
