# Phase 3a — Test Spec (G3)

> **Status:** G3 open · drafted 2026-05-23 · awaiting sign-off.
> **Inputs:** Phase 3a G1 plan ([build_tracker §5.7](../../docs/build_tracker.md)) · Phase 3a G2 contract delta ([api_contracts.md §5.1 #3 + §5.2](../../docs/api_contracts.md)).
> **Outputs at G3:** this spec + two new red skeleton files (`test_chunking_unit.py`, `test_chunking_worker.py`). New imports point at `kb.chunking` + `kb.domain.chunks` + the extended `kb.workers.tasks` (all land at G4) — collection fails at G3 (expected red state).

---

## 1. Scope

Every G1 decision in [build_tracker §5.7](../../docs/build_tracker.md) gets a matching test:
- Budget enforcement (#1) · overlap (#2) · tokenizer (#3 — implicit via budget assertions) · layout-aware boundary (#4) · small-page joining (#5) · source-page tracking (#6) · chunks immutability via REVOKE (#7) · lifecycle state addition (#8) · task chaining via separate-tx defer (#9) · per-stage idempotency (#10) · empty-input failure (#11) · row-boundary preservation on huge xlsx sheets (#12).

Two files cover the surface (**16 new tests**, on top of 188 from prior phases; `pytest --collect-only` is authoritative):

| File | New tests | Covers |
|---|---|---|
| [`tests/test_chunking_unit.py`](../test_chunking_unit.py) | 9 | Pure-function `chunk_pages()` — short page → 1 chunk · over-budget page splits at paragraph break · small pages join · source_page_numbers tracks all contributors · chunk_index monotonic · overlap preserves tail · xlsx huge sheet splits on row boundary · empty input raises · content_sha invariant |
| [`tests/test_chunking_worker.py`](../test_chunking_worker.py) | 7 | `chunk_file_impl()` end-to-end against testcontainers — parsed→chunked transition · file_lifecycle event written · idempotent on already-chunked · empty raw_pages marks failed · `parse_file_impl` chains `chunk_file` via defer · chunks immutability via kb_app · RLS isolation across workspaces |

**Out of scope (Phase 3b / 3c / 4 / 5 / 9):**
- Contextual prefix LLM call → 3b.
- Embedding API call + RAPTOR build → 3c.
- HNSW + BM25 indexes on `chunks` → Phase 4.
- Force-rechunk admin endpoint → Phase 4.
- Atomic-unit-aware chunking (clause/transaction/row boundaries) → Phase 5.
- `audit_log` writes on chunking → Phase 9.

---

## 2. Fixture strategy

Reuses Phase 0+1a+1b+1c+2a+2b's testcontainers + `client` + `kb_app_conninfo` fixture pattern unchanged. No new fixture files — the chunker is exercised against in-memory `RawPage` objects (no PDF/xlsx bytes needed at the unit-test layer).

The worker tests reuse Phase 2a's pattern: POST a tiny.pdf via the HTTP client, await `parse_file_impl(fid)` directly, then await `chunk_file_impl(fid)` directly — bypassing Procrastinate's queue infrastructure so per-test runtime stays bounded.

The task-chaining test (`test_parse_file_impl_chains_chunk_file_via_defer`) asserts that `parse_file_impl()` enqueues a `chunk_file` task via `procrastinate_app.configure_task` rather than running it inline — verified by checking `procrastinate_jobs` table for the deferred row.

---

## 3. Conventions

Same as Phase 0+1a+1b+1c+2a+2b. Plus:
- **Unit tests never touch DB or MinIO** — they call `chunk_pages(raw_pages, budget=..., overlap=...)` directly on pydantic `RawPage` instances. RawPage shape kept minimal: `page_number: int`, `text: str`. The chunker doesn't read `layout_json`.
- **Worker tests use a small `KB_CHUNK_TOKENS=200` env override** (set in conftest) so 16-tokens-per-test stays under the budget — keeps unit tests fast and deterministic.

---

## 4. Test inventory

### 4.1 `tests/test_chunking_unit.py` — pure-function chunker tests (9)

| Test | Intent |
|---|---|
| `test_chunk_pages_single_short_page_returns_one_chunk` | One `RawPage` with 50 tokens → `chunk_pages([page], budget=200) → [Chunk]` with `len == 1`, `chunks[0].text == page.text`. Asserts decision #4 (layout-aware boundary preserved). |
| `test_chunk_pages_single_page_exceeds_budget_splits_at_paragraph_break` | One RawPage with 600 tokens containing `\n\n` at the ~200-token mark → `chunk_pages([page], budget=200, overlap=20)` returns ≥2 chunks; the split happens AT a `\n\n` boundary (assert via substring search). Decisions #1 + #2 + #4. |
| `test_chunk_pages_small_pages_join_until_budget` | 5 RawPages of ~50 tokens each → `chunk_pages(pages, budget=200)` returns ≤2 chunks (joined). Asserts decision #5. |
| `test_chunk_pages_source_page_numbers_tracks_all_contributing_pages` | 3 small pages joined into one chunk → `chunks[0].source_page_numbers == [1, 2, 3]`. Asserts decision #6. |
| `test_chunk_pages_chunk_index_starts_at_zero_and_increments` | Multi-chunk output → `[c.chunk_index for c in chunks] == [0, 1, 2, ...]`. Required by `chunks` UNIQUE `(file_id, chunk_index)` constraint. |
| `test_chunk_pages_overlap_preserves_tail_of_prior_chunk` | Over-budget single page split into 2 chunks with `overlap=50` → the last ~50 tokens of `chunks[0].text` appear at the start of `chunks[1].text`. Asserts decision #2. |
| `test_chunk_pages_xlsx_huge_sheet_splits_on_row_boundary` | One RawPage simulating an xlsx sheet (text = `"a\tb\nc\td\ne\tf\n..."` repeated to exceed budget) → split points fall on `\n` (row boundaries), never mid-row (no chunk ends mid-`\t`-separated cell). Asserts decision #12. |
| `test_chunk_pages_empty_pages_list_raises_chunking_error` | `chunk_pages([], budget=200)` raises `ChunkingError("empty raw_pages")`. Asserts decision #11. |
| `test_chunk_pages_content_sha_matches_sha256_of_text` | Every produced chunk: `chunks[i].content_sha == hashlib.sha256(chunks[i].text.encode("utf-8")).hexdigest()`. Phase 3c will rely on this for embedding-cache stability. |

### 4.2 `tests/test_chunking_worker.py` — worker integration tests (7)

All use the same testcontainer fixtures as Phase 2a + tiny.pdf as fixture input.

| Test | Intent |
|---|---|
| `test_chunk_file_impl_reads_raw_pages_and_writes_chunks` | POST tiny.pdf → run parse_file_impl → run `chunk_file_impl(fid)` → `SELECT count(*) FROM chunks WHERE file_id = %s` ≥ 1 AND `files.lifecycle_state == 'chunked'`. Asserts decisions #6 + #8. |
| `test_chunk_file_impl_writes_chunked_lifecycle_event` | After chunk_file_impl: `file_lifecycle` has an event with `from_state='parsed'`, `to_state='chunked'`, `event='chunking_done'`, payload includes `chunk_count` and `total_tokens`. |
| `test_chunk_file_impl_is_idempotent_on_already_chunked` | Run chunk_file_impl twice on the same file → second call is a no-op; `SELECT count(*) FROM chunks WHERE file_id = %s` unchanged; no duplicate `chunking_done` event. Asserts decision #10. |
| `test_chunk_file_impl_empty_raw_pages_marks_failed` | Pre-set a file to lifecycle_state='parsed' WITHOUT writing any raw_pages → chunk_file_impl raises ChunkingError internally → file ends in 'failed' with a `parsed→failed` event carrying `event='chunking_failed'`. Asserts decision #11. |
| `test_parse_file_impl_chains_chunk_file_via_defer` | POST tiny.pdf, run parse_file_impl → query `procrastinate_jobs` (or whichever Procrastinate-internal table holds deferred work) and confirm a `chunk_file` job with `file_id` in args is present. Asserts decision #9 (chained-defer pattern). |
| `test_chunks_table_rejects_update_via_kb_app` | Open a kb_app connection (not superuser); attempt `UPDATE chunks SET text = 'x' WHERE ...` → `psycopg.errors.InsufficientPrivilege`. Asserts decision #7 (REVOKE UPDATE). |
| `test_chunks_isolated_across_workspaces` | POST + parse + chunk a file in workspace A; switch app.workspace_id to workspace B; `SELECT * FROM chunks WHERE file_id = <A's file id>` returns zero rows. RLS day-1 invariant. |

---

## 5. What "green" means at G4

When all of the following pass, G3 is satisfied and G4 closes:
1. `uv run pytest tests/` exits 0.
2. All 188 prior tests stay green.
3. The 9 unit + 7 worker tests = 16 new tests, all green.
4. Coverage of `src/kb/chunking/__init__.py` is ≥ 90% (a pure-function module is easy to cover); `src/kb/workers/tasks.py:chunk_file_impl` is ≥ 85%.
5. Total: 188 + 16 = **204 tests**. (`pytest --collect-only` is authoritative.)

---

## 6. Sign-off

When Aniket approves this spec + the skeleton files, the Phase 3a G3 cell in [build_tracker §5](../../docs/build_tracker.md) flips 🟡 → ✅ and G4 (build) opens. Sign-off recorded in `build_tracker.md` §9.

---

## 7. Change log

| Date | Change | By |
|---|---|---|
| 2026-05-23 | Spec drafted at Phase 3a G3 open. Two buckets: unit-level pure-function chunker (9) + worker integration through testcontainers (7). 16 new tests total. Suite goes 188 → 204. Awaiting sign-off. | Aniket |
