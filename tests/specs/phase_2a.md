# Phase 2a — Test Spec (G3)

> **Status:** G3 open · drafted 2026-05-23 · awaiting sign-off.
> **Inputs:** Phase 2a G1 plan ([build_tracker §5.5](../../docs/archive/build_tracker.md)) · Phase 2a G2 contracts ([api_contracts.md §5](../../docs/archive/api_contracts.md)).
> **Outputs at G3:** this spec + 5 new red skeleton files. New imports point at `kb.api.files`, `kb.domain.files`, `kb.domain.raw_pages`, `kb.parsers`, `kb.workers.tasks` that land at G4 — collection fails at G3 (expected "red" state).

---

## 1. Scope

Every endpoint in api_contracts §5 gets coverage. Every G1 decision in [build_tracker §5.5](../../docs/archive/build_tracker.md) (MinIO/PG split, content-hash dedup, state machine, append-only audit, raw_pages immutability, Procrastinate worker, workspace context in worker, two-mode upload, two-layer idempotency, Parser Protocol + dispatcher, Docling integration, RLS, upload validation, failure mode) has at least one test asserting it.

Five files cover the surface (**28 new tests**, on top of 142 from Phase 0+1a+1b+1c; pytest `--collect-only` is authoritative):

| File | New tests | Covers |
|---|---|---|
| [`tests/test_files_crud.py`](../test_files_crud.py) | 10 | POST upload (both modes) · content-hash dedup · 413/415 · GET list · GET one with lifecycle · DELETE soft · RLS. |
| [`tests/test_parse_dispatch.py`](../test_parse_dispatch.py) | 5 | Parser Protocol + registration + MIME/magic-bytes routing. Pure unit tests; no DB. |
| [`tests/test_parse_pdf_docling.py`](../test_parse_pdf_docling.py) | 3 | Docling against a tiny PDF fixture; output shape; invalid-PDF error path. |
| [`tests/test_raw_pages.py`](../test_raw_pages.py) | 5 | `GET /files/:id/pages` paginated · empty while queued · 404 for unknown · pagination · DB-layer immutability (kb_app UPDATE rejected). |
| [`tests/test_files_lifecycle.py`](../test_files_lifecycle.py) | 5 | Initial lifecycle event on POST · worker state transitions · failure event · idempotency on replay · DB-layer immutability. |

**Out of scope (Phase 2b / 5+ / 9):**
- xlsx + email + Mistral OCR parser tests — Phase 2b.
- doc_type classifier tests — when the classifier lands.
- `audit_log` write tests — Phase 9.
- SSE lifecycle endpoint tests — Phase 9.
- `POST /files/:id/retry` — Phase 2b.

---

## 2. Fixture strategy

Reuses Phase 0's testcontainers + Phase 1a's per-test workspace fixture pattern. New conventions for 2a:

- **Tiny PDF fixture.** `tests/fixtures/tiny.pdf` — a minimal valid digital PDF (≤ 2 KB) committed to the repo. Used by Docling tests + integration tests. **Lands at G4** (not G3 — the spec describes the file but doesn't ship it yet).
- **Worker-in-process helper.** `run_pending_jobs(timeout=10)` async helper that drains the Procrastinate queue once, invoking `parse_file` synchronously in the test process. Avoids needing a real worker container during pytest.
- **Lower max-upload-size for tests.** Via `KB_MAX_UPLOAD_BYTES` env var, tests can set the limit to a few KB to exercise the 413 path without uploading 100 MB.

Migrations now include `0008_parse_layer.sql` (lands at G4). The session-scoped `db_migrated` fixture picks it up.

---

## 3. Conventions

Same as Phase 0+1a+1b+1c. Plus:

- **Worker tests use the in-process helper**, not a real Procrastinate worker container. The pattern: POST /files → call `run_pending_jobs()` → assert state. This keeps tests fast (no container start) but exercises the full task definition.
- **MinIO interactions** use the same testcontainer MinIO from Phase 0. The `client` fixture wires up MinIO credentials via env.
- **Fixture PDFs** stay tiny (< 2 KB each) to keep the repo lean.

---

## 4. Test inventory

### 4.1 `tests/test_files_crud.py` — upload + dedup + read + delete + RLS (10 tests)

| Test | Intent |
|---|---|
| `test_post_creates_file_via_multipart` | §5.5 Mode A: POST multipart with a PDF → 201 with file object; `lifecycle_state='queued'`; `mime_type='application/pdf'`; `size_bytes` matches. |
| `test_post_creates_file_via_json_minio_key` | §5.5 Mode B: pre-stage a file in MinIO, POST JSON `{minio_object_key, name}` → 201 with same shape. |
| `test_post_requires_idempotency_key` | Missing header → 400 slug `missing-idempotency-key`. |
| `test_post_rejects_payload_too_large` | With `KB_MAX_UPLOAD_BYTES=1024`, POST 2 KB → 413 slug `payload-too-large`. |
| `test_post_rejects_unsupported_mime` | POST a `.txt` file (text/plain) → 415 slug `unsupported-media-type` (Phase 2a only whitelists application/pdf). |
| `test_post_content_hash_dedup_returns_existing` | POST same content twice → second call returns 200 (NOT 201) with same `id`; `X-Dedup-Reason: content-hash` header present. |
| `test_get_list_returns_workspace_files` | Create 3 files; GET → `total=3`. |
| `test_get_one_includes_lifecycle_history` | POST → GET /files/:id → response includes `lifecycle: [{from_state: null, to_state: "queued", event: "upload", …}]`. |
| `test_delete_soft_deletes` | POST → DELETE → 204; subsequent GET → 404; row in DB has `lifecycle_state='deleted'`. |
| `test_files_isolated_across_workspaces` | POST in A; GET as B → 404 (NOT 403, same §2.4 leak-avoidance). |

### 4.2 `tests/test_parse_dispatch.py` — Parser Protocol + routing (5 tests)

Pure unit tests (no DB). Tests `kb.parsers.dispatch(...)` against a small set of fake parsers.

| Test | Intent |
|---|---|
| `test_register_and_dispatch_for_pdf_magic` | Register a fake PDFParser whose `can_handle` matches `mime='application/pdf'`. Dispatch with `mime='application/pdf'` → returns that parser. |
| `test_dispatch_falls_through_to_first_match` | Register multiple parsers; dispatch picks the first whose `can_handle` returns True. |
| `test_dispatch_raises_when_no_parser_matches` | Dispatch with mime that no registered parser handles → raises `NoParserForMime`. |
| `test_dispatch_uses_mime_type_when_provided` | Mock parser's `can_handle` signature: when mime is given, dispatcher passes it; magic_bytes only used as a tiebreak. |
| `test_dispatch_uses_magic_bytes_when_mime_missing` | When `mime=None` is passed (rare), dispatcher uses magic_bytes for routing. |

### 4.3 `tests/test_parse_pdf_docling.py` — Docling against a fixture PDF (3 tests)

| Test | Intent |
|---|---|
| `test_docling_parses_tiny_pdf_into_pages` | Call `DoclingParser.parse(pdf_bytes, file_id='test')`. Returns `ParsedDocument` with ≥ 1 page; `pages[0].text` non-empty; `pages[0].page_number == 1`. |
| `test_docling_returns_text_and_layout` | `pages[0].layout_json` is a dict with at least a `blocks` key (or whatever Docling outputs). |
| `test_docling_raises_on_invalid_pdf` | `DoclingParser.parse(b"not a pdf", file_id='x')` raises a domain exception (e.g., `ParseError`). |

### 4.4 `tests/test_raw_pages.py` — `/files/:id/pages` endpoint + DB immutability (5 tests)

| Test | Intent |
|---|---|
| `test_get_pages_after_parse_returns_text` | POST PDF + run worker → GET /files/:id/pages → items each have `page_number`, `text`, `layout_json`, `content_sha`. |
| `test_get_pages_returns_empty_while_queued` | POST PDF, don't run worker → GET pages → `total=0`. |
| `test_get_pages_404_for_unknown_file` | GET pages on random UUID → 404. |
| `test_get_pages_pagination` | POST a 3-page PDF + run worker → GET ?limit=2&offset=1 → 1 item, `total=3`. |
| `test_raw_pages_table_rejects_update_via_kb_app` | DB-layer immutability: as `kb_app` connection (RLS-enabled), attempting `UPDATE raw_pages SET text='hacked'` raises `psycopg.errors.InsufficientPrivilege`. As superuser, the same UPDATE succeeds (proving the table itself isn't read-only). |

### 4.5 `tests/test_files_lifecycle.py` — state machine + audit + idempotency + DB immutability (5 tests)

| Test | Intent |
|---|---|
| `test_post_creates_initial_lifecycle_event` | POST → GET /files/:id → lifecycle has one event `{from_state: null, to_state: "queued", event: "upload"}`. |
| `test_parse_task_transitions_queued_to_parsing_to_parsed` | POST + `run_pending_jobs()` → GET /files/:id → lifecycle has 3 events in order: `null→queued`, `queued→parsing`, `parsing→parsed`. |
| `test_parse_task_failure_writes_failed_lifecycle_event` | POST an invalid PDF (e.g., `.txt` masquerading as PDF via Mode B) + run worker → lifecycle includes `parsing→failed`; payload contains `error_class`, `message`. |
| `test_parse_task_idempotent_when_already_parsed` | POST PDF + run worker → lifecycle has 3 events. Run worker AGAIN (simulating replay) → lifecycle still has 3 events; no new transitions. |
| `test_file_lifecycle_table_rejects_update_via_kb_app` | DB immutability: `UPDATE file_lifecycle SET ...` as kb_app → InsufficientPrivilege. As superuser → succeeds. |

---

## 5. What "green" means at G4

When all of the following pass, G3 is satisfied and G4 closes:
1. `uv run pytest tests/` exits 0.
2. Phases 0+1a+1b+1c (142 tests) stay green — no regressions.
3. The 28 new tests across 5 new files are green.
4. Coverage of `src/kb/api/files.py`, `src/kb/domain/files.py`, `src/kb/domain/raw_pages.py`, `src/kb/parsers/__init__.py`, `src/kb/parsers/docling_parser.py`, `src/kb/workers/tasks.py` is ≥ 90%.
5. Total: 142 + 28 = **170 tests**. (`pytest --collect-only` is authoritative.)

---

## 6. Sign-off

When Aniket approves this spec + the skeleton files, the Phase 2a G3 cell in [build_tracker §5](../../docs/archive/build_tracker.md) flips 🟡 → ✅ and G4 (build) opens. Sign-off recorded in `build_tracker.md` §9.

---

## 7. Change log

| Date | Change | By |
|---|---|---|
| 2026-05-23 | Spec drafted at Phase 2a G3 open. Five new files: files_crud (10) + parse_dispatch (5) + parse_pdf_docling (3) + raw_pages (5) + files_lifecycle (5) = **28 new tests**. Total after Phase 2a: 142 + 28 = 170. Awaiting sign-off. | Aniket |
