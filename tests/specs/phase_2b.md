# Phase 2b — Test Spec (G3)

> **Status:** G3 open · drafted 2026-05-23 · awaiting sign-off.
> **Inputs:** Phase 2b G1 plan ([build_tracker §5.6](../../docs/build_tracker.md)) · Phase 2b G2 contract delta ([api_contracts.md §5.5](../../docs/api_contracts.md)).
> **Outputs at G3:** this spec + three new red skeleton files (`test_parse_xlsx.py`, `test_parse_email.py`, `test_parse_mistral_ocr.py`) + additions to `test_files_crud.py` + two fixture files placeholders (`tiny.xlsx`, `tiny.eml` — bytes land at G4). New imports point at `kb.parsers.{xlsx,email,mistral_ocr}_parser` modules that land at G4 — collection fails at G3 (expected red state).

---

## 1. Scope

Every G1 decision in [build_tracker §5.6](../../docs/build_tracker.md) gets a matching test:
- One `raw_pages` per xlsx sheet (#1) · per-email page model (#2) · TSV+header rendering (#3) · email rendering (#4) · attachments-as-metadata (#5) · magic-byte sniffer (#6) · Mistral self-disables when no key (#9) · `ParserRegistry` dispatch order (#7) · empty-content fallback (#13).

Four files cover the surface (**18 new tests**, on top of 170 from prior phases; pytest `--collect-only` is authoritative):

| File | New tests | Covers |
|---|---|---|
| [`tests/test_parse_xlsx.py`](../test_parse_xlsx.py) | 5 | One page per sheet · TSV + sheet header rendering · empty sheet → empty text but row · layout_json shape · ZIP-magic detection |
| [`tests/test_parse_email.py`](../test_parse_email.py) | 5 | One page · headers + body in text · attachments metadata-only in layout_json · HTML-only body fallback · header-pattern magic detection |
| [`tests/test_parse_mistral_ocr.py`](../test_parse_mistral_ocr.py) | 5 | `can_handle` gated on `KB_MISTRAL_API_KEY` · mock-driven parse · per-page split · 4xx → `ParseError` · dispatcher order with Docling |
| [`tests/test_files_crud.py`](../test_files_crud.py) — additive | 3 | POST xlsx → 201 + queued · POST eml → 201 + queued · octet-stream + ZIP magic → routes to xlsx parser |

**Out of scope (Phase 2c / 9):**
- Real Mistral OCR API calls — Phase 2c when key is procured.
- Force-parser `?parser=mistral_ocr` query param — Phase 2c.
- Attachment recursive ingestion (PDF inside email becomes child file row).
- pptx + Gemini VLM fallback — Wave B.
- `audit_log` writes on parser invocation — Phase 9.

---

## 2. Fixture strategy

Reuses Phase 0+1a+1b+1c+2a's testcontainers + client fixture pattern unchanged. Two new fixture files (committed bytes; created at G4):

- **`tests/fixtures/tiny.xlsx`** — ~1 KB. Two sheets: `Sheet1` with 3 rows × 2 cols of strings/numbers; `Sheet2` empty (one cell). Generated via openpyxl at G4-build time and committed.
- **`tests/fixtures/tiny.eml`** — ~300 B. RFC 5322 minimal email: `From: a@example.com\nTo: b@example.com\nSubject: hi\nDate: ...\n\nhello world body\n`. Plus a second fixture `tests/fixtures/tiny_with_attachment.eml` with a multipart message containing a small `text/plain` attachment for the attachment-metadata test.

Mock-HTTP-client fixture for Mistral OCR tests defined inline in `test_parse_mistral_ocr.py`.

---

## 3. Conventions

Same as Phase 0+1a+1b+1c+2a. Plus:
- **Mistral OCR tests never touch the network** — every test uses a mock `http_client` injected via the parser constructor.
- **xlsx + email parser tests are unit-level** (no DB, no MinIO) — they call `parser.parse(file_bytes, file_id='t', workspace_id='ws')` directly. The HTTP-level integration is covered by `test_files_crud.py` additions.

---

## 4. Test inventory

### 4.1 `tests/test_parse_xlsx.py` — xlsx parser unit tests (5)

| Test | Intent |
|---|---|
| `test_xlsx_parses_one_page_per_sheet` | tiny.xlsx (2 sheets) → ParsedDocument with `len(pages) == 2`; `pages[0].page_number == 1`, `pages[1].page_number == 2`. Asserts G1 decision #1. |
| `test_xlsx_text_is_tsv_with_sheet_header` | `pages[0].text` starts with `# Sheet: Sheet1\n`; rows are tab-separated, lines newline-separated. Asserts decision #3. |
| `test_xlsx_handles_empty_sheet` | tiny.xlsx's Sheet2 (one cell, no rows of data) → `pages[1].text` is empty string OR contains only the sheet header; the row exists either way. Asserts decision #13 (empty-content fallback). |
| `test_xlsx_layout_includes_rows_cols_per_sheet` | `pages[0].layout_json == {"sheet_name": "Sheet1", "rows": 3, "cols": 2}` (approximately — exact numbers from fixture). |
| `test_xlsx_can_handle_pk_zip_magic` | `XLSXParser().can_handle(mime_type="application/octet-stream", magic_bytes=b"PK\x03\x04...")` returns True. Asserts decision #6. |

### 4.2 `tests/test_parse_email.py` — email parser unit tests (5)

| Test | Intent |
|---|---|
| `test_email_parses_one_page` | tiny.eml → ParsedDocument with `len(pages) == 1`; `pages[0].page_number == 1`. Asserts decision #2. |
| `test_email_text_includes_headers_and_body` | `pages[0].text` contains `From: a@example.com`, `Subject: hi`, and the body `hello world body`. Asserts decision #4. |
| `test_email_attachments_listed_in_layout_json` | tiny_with_attachment.eml → `pages[0].layout_json["attachments"]` has one entry with `filename`, `content_type`, `size_bytes`. Decision #5. |
| `test_email_html_only_body_stripped` | An email with ONLY a `text/html` body part → `pages[0].text` contains stripped text (no `<` or `>` chars); HTML tags removed via stdlib `html.parser`. |
| `test_email_magic_detection_via_header_pattern` | `EmailParser().can_handle(mime_type="application/octet-stream", magic_bytes=b"From: a@example.com\nSubject:")` returns True. Asserts decision #6. |

### 4.3 `tests/test_parse_mistral_ocr.py` — Mistral OCR adapter (5)

All tests use a mock `http_client` — never call the real API.

| Test | Intent |
|---|---|
| `test_mistral_can_handle_when_api_key_present` | With `KB_MISTRAL_API_KEY=fake-key` in env, `MistralOCRParser().can_handle("application/pdf", b"%PDF-")` returns True. Asserts decision #9. |
| `test_mistral_cannot_handle_when_api_key_absent` | With env var unset, `can_handle("application/pdf", b"%PDF-")` returns False — parser self-disables. Decision #9. |
| `test_mistral_parses_via_mock_response` | Inject a mock HTTP client returning a canned Mistral response (2 pages with text). `parser.parse(b"%PDF-fake", ...)` returns ParsedDocument with 2 pages. Decision #8. |
| `test_mistral_returns_one_page_per_response_page` | The mock returns 3 "pages" in the response; the parser emits 3 `Page` objects with `page_number` 1, 2, 3. |
| `test_mistral_raises_parse_error_on_4xx` | Mock client returns HTTP 401 / 429 / 500 → `MistralOCRParser.parse()` raises `ParseError` with `error_class` indicating the upstream status. |

### 4.4 `tests/test_files_crud.py` — additive HTTP-level integration (3)

| Test | Intent |
|---|---|
| `test_post_xlsx_creates_file` | POST multipart with `Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` + tiny.xlsx → 201 with `mime_type='application/...'` matching. Worker is NOT run here (the post-and-queue test); subsequent `parse_file_impl()` covered by parser unit tests. |
| `test_post_email_creates_file` | POST multipart with `Content-Type: message/rfc822` + tiny.eml → 201. |
| `test_post_octet_stream_xlsx_detected_via_magic` | POST tiny.xlsx with `Content-Type: application/octet-stream` (force the magic-sniff path). After server-side sniff, mime_type should be normalized to `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` in the returned file object. Asserts decision #6. |

---

## 5. What "green" means at G4

When all of the following pass, G3 is satisfied and G4 closes:
1. `uv run pytest tests/` exits 0.
2. All 170 prior tests stay green.
3. The 15 new parser-unit tests + 3 additive HTTP tests = 18 new tests, all green.
4. Coverage of `src/kb/parsers/{xlsx,email,mistral_ocr}_parser.py` is ≥ 85% (Mistral mock paths are exhaustive on the parser side; the real API call path isn't exercised in CI by design).
5. Total: 170 + 18 = **188 tests**. (`pytest --collect-only` is authoritative.)

---

## 6. Sign-off

When Aniket approves this spec + the skeleton files, the Phase 2b G3 cell in [build_tracker §5](../../docs/build_tracker.md) flips 🟡 → ✅ and G4 (build) opens. Sign-off recorded in `build_tracker.md` §9.

---

## 7. Change log

| Date | Change | By |
|---|---|---|
| 2026-05-23 | Spec drafted at Phase 2b G3 open. Four buckets: parser-unit tests for xlsx (5) · email (5) · mistral_ocr (5) — all unit-level, no DB · HTTP-integration additions to test_files_crud (3). 18 new tests total. Suite goes 170 → 188. Awaiting sign-off. | Aniket |
