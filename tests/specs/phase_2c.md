# Phase 2c — Test Spec (G3)

> **Status:** G3 open · drafted 2026-05-24 · awaiting sign-off.
> **Inputs:** Phase 2c G1 plan ([build_tracker §5.6.1](../../docs/build_tracker.md)) · Phase 2c G2 contract delta ([api_contracts.md §5.5 Query parameters + §5.3 parser-enum widening](../../docs/api_contracts.md)).
> **Outputs at G3:** this spec + 4 new red skeleton files + 1 mutated test file. Imports point at `kb.parsers.gemini_ocr_parser.GeminiOCRParser`, `kb.parsers.text_layer_sniff.sniff_pdf_text_layer`, the widened `kb.parsers.select_parser_for(...)`, and the `kb.workers.tasks.parse_file_impl` quality-escalation branch (all land at G4).

---

## 1. Scope

Every §5.6.1 G1 decision gets matching tests. Total surface: **~18 new tests + 2 mutated tests** in `test_files_crud.py`. On top of 238 from prior phases (256 expected at G5 green).

| File | New tests | Covers |
|---|---|---|
| [`tests/test_parse_gemini_ocr.py`](../test_parse_gemini_ocr.py) | 6 | `GeminiOCRParser` with mocked `google.genai.Client` — per-page render + prompt shape (#5) · response parsing · model literal `'gemini-2.5-flash'` (#1) · `asyncio.Semaphore(4)` concurrency (#6) · empty-key startup error (#13) · API error → `ParseError` |
| [`tests/test_text_layer_sniff.py`](../test_text_layer_sniff.py) | 3 | `sniff_pdf_text_layer(bytes)` — digital PDF returns `avg_chars_per_page >= 50` · scanned PDF returns `~0` chars · `max_pages_sniffed=10` bound respected for large docs (#8, #9) |
| [`tests/test_parser_dispatcher_strategy.py`](../test_parser_dispatcher_strategy.py) | 5 | `select_parser_for(...)` matrix — `auto`+digital→Docling, `auto`+scanned→Gemini OCR, `docling_first`→Docling always, `gemini_only`+no-key→`OCRConfigError`, unknown strategy→`ValueError` (#7, #13) |
| [`tests/test_parse_quality_escalation.py`](../test_parse_quality_escalation.py) | 4 | `parse_file_impl` escalation matrix — empty Docling output → re-parse w/ Gemini · `printable_ratio<0.7` → escalate · hybrid (1 bad page among many) → per-page rerun · provenance JSON shape (#10, #12) |
| [`tests/test_files_crud.py`](../test_files_crud.py) | +2 mutated | `POST /files?parser=gemini` accepted → 201 + `forced_parser='gemini'` persisted · `?parser=bogus` → 400 `invalid-parser-override` (#11) |

---

## 2. Fixture strategy

**Mock the LLM client; use real PDFs for the sniff.**

- `GeminiOCRParser` tests mock `google.genai.Client.aio.models.generate_content` — same `_MockGeminiClient` pattern from `test_contextualization_gemini_unit.py` (3b-bis), adapted to return per-page text. No `KB_GEMINI_API_KEY` required in CI.
- Text-layer sniff tests use the existing `tests/fixtures/tiny.pdf` (digital → high text density) and a new `tests/fixtures/tiny_scanned.pdf` (image-only — generated synthetically from `tiny.pdf` by rendering to PNG and re-encoding into a PDF with no text layer; the generator script `tests/fixtures/scripts/make_tiny_scanned.py` is checked in but not run in CI). The fixture lands at G4 alongside the production code.
- Dispatcher strategy tests use both fixtures + the mock Gemini parser to keep the matrix hermetic.
- Quality-escalation tests inject a fake Docling result + the mocked Gemini OCR parser to test the worker branch without touching either real parser.
- `test_files_crud.py` mutations just thread the `?parser=` query param through the existing httpx `client` fixture; assertions live on the HTTP response + lifecycle event payload (no DB-level reads needed).

---

## 3. Decision → test mapping

| G1 # | Decision | Test(s) |
|---|---|---|
| 1 | `gemini-2.5-flash` model literal + `KB_OCR_MODEL` override | `test_gemini_ocr_uses_configurable_model` |
| 2 | `pypdfium2` for PDF→PNG render | covered implicitly by render-call side effect in `test_gemini_ocr_renders_per_page` |
| 5 | OCR prompt (verbatim from §5.6.1 #5) | `test_gemini_ocr_sends_image_with_correct_prompt` |
| 6 | `asyncio.Semaphore(4)` concurrency | `test_gemini_ocr_caps_concurrent_calls_at_4` |
| 7 | `KB_PARSER_STRATEGY` 4-value selector | `test_select_parser_for_strategy_matrix` (covers all 4 + invalid) |
| 8 | Sniff threshold = 50 chars/page | `test_sniff_digital_pdf_returns_high_density` + `test_sniff_scanned_pdf_returns_zero_density` |
| 9 | Sniff bounded to first 10 pages | `test_sniff_caps_at_10_pages_for_large_docs` |
| 10 | Quality-escalation signals (3 of them) | `test_escalate_on_empty_docling_output` + `test_escalate_on_garbled_output` + `test_escalate_per_page_for_hybrid_pdf` |
| 11 | Caller override `?parser=<docling\|gemini\|auto>` + `400 invalid-parser-override` | `test_post_files_accepts_parser_gemini_query_param` + `test_post_files_rejects_bogus_parser_value` |
| 12 | Provenance JSON shape | `test_escalation_writes_provenance_json` |
| 13 | `gemini_only` + no key → `OCRConfigError` | `test_select_parser_for_gemini_only_without_key_raises` + `test_gemini_ocr_parser_no_key_raises` |

Decisions #3 (DPI), #4 (PNG), #14 (fixture synthesis), #15 (Mistral untouched) need no dedicated test — covered by code-shape inspection at G4 sign-off (DPI + PNG are constants in the parser; Mistral adapter is left alone).

---

## 4. Out-of-scope assertions (deliberate)

- **Real Gemini API smoke** — `KB_GEMINI_API_KEY`-gated; covered in `scripts/verify_phase_2c.sh` not pytest.
- **OCR quality benchmark** (Gemini vs Docling+RapidOCR vs Mistral on a labeled corpus) — separate eval harness; out of scope for this phase.
- **`pypdfium2` rendering fidelity** (anti-aliasing, color accuracy, DPI scaling) — trust the upstream library; we assert it produces *some* PIL image, not pixel-perfect output.
- **Workspace-level OCR policy** (§5.6.1 out-of-scope #1) — Phase 5 territory.
- **Batched multi-page OCR** (§5.6.1 out-of-scope #2) — cost optimization for later.

---

## 5. G3 exit criteria

- `pytest tests/test_parse_gemini_ocr.py tests/test_text_layer_sniff.py tests/test_parser_dispatcher_strategy.py tests/test_parse_quality_escalation.py` → all RED (ImportError or assertion failure — the new modules don't exist yet).
- `pytest tests/test_files_crud.py` → 2 fails (the mutated `?parser=` tests), all other tests still pass (no collateral damage).
- Rest of suite (238 prior) remains green.
- This spec file committed; build_tracker §5.6.1 status updated.
