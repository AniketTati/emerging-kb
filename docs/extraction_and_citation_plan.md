# Extraction + citation plan — Wave B step 2

Date: 2026-05-25 · branch: `waveB/demo-corpus-and-pages` (post PR #26)

PR #26 shipped the doc-detail two-pane UI. The citation system in that
PR is best-effort text-search; this doc lays out the plan to make
citations work *properly* and to fix the extraction-quality gaps the
audit surfaced in the same pass.

---

## 1. Extraction audit — what the demo corpus actually produces

Counts per layer per file as of 2026-05-25 (queried via
`/files/:id/details` against the live workspace):

| File | mime | inferred_doc_type | pages | chunks | mentions | atomic_units | entities_linked | triples | proposed_fields |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| vertex-msa.pdf | application/pdf | master_services_agreement | 2 | 1 | **0** ⚠ | 5 ✓ | **0** ⚠ | 19 ✓ | 23 ✓ |
| vertex-pricing-tiers.xlsx | xlsx | price_sheet | 2 | 1 | 44 ✓ | 13 ✓ | 27 ✓ | 39 ✓ | 8 ✓ |
| vertex-sales-thread.eml | message/rfc822 | email_thread | 1 | 1 | **0** ⚠ | **0** ⚠ | **0** ⚠ | 20 ✓ | 22 ✓ |
| vertex-eval-notes.md | text/markdown | vendor_evaluation | 1 | 1 | 44 ✓ | **0** ⚠ | 28 ✓ | 16 ✓ | — |
| vertex-amendment.txt | text/plain | legal_contract | 1 | 1 | **0** ⚠ | **0** ⚠ | **0** ⚠ | 18 ✓ | 23 ✓ |

Plus two findings against `schema_entities` + `extracted_entities`:

- `extracted_entities` table is **empty for every file** (0 / 0 / 0 / 0 / 0 / 0).
- `schema_entities` table is **empty** — there are no schema-entity definitions for the L4 closed-world extractor to instantiate against.

Plus `doc_chains`: **empty**. The MSA + Amendment ("Amendment No. 1
TO Master Services Agreement … amends the Master Services Agreement
between the parties dated January 15, 2026") were NOT linked.

Plus `parse_artifacts`: **empty for every file** — Docling's layout
(per-element bboxes), OCR confidence, etc. are being computed and
thrown away at parse time.

### 1.1 Quality issues to fix (ranked)

| # | Issue | Root cause hypothesis | Impact | Effort |
|---|---|---|---|---|
| **E1** | **L2 mentions = 0 on PDF / TXT / EML** (works on .md + .xlsx) | Gemini mentions extractor prompt or chunking variant differs by parser path — Docling/TextParser/EmailParser chunks confuse the extractor that .md/.xlsx chunks don't. | Cascading: no mentions → no entities linked → no per-mention citations. Half the corpus has no L2 layer at all. | M |
| **E2** | **`extracted_entities` empty for everyone** | `schema_entities` is also empty — the closed-world extractor has no schemas to instantiate against. Either the seed schemas weren't migrated in or schema_entities are created lazily on first auto-promotion (Phase 6 design). | The doc-detail "Schema entity instances (L4 closed-world)" accordion is always empty. Audit shows it as a feature gap; reality is upstream data is missing. | M |
| **E3** | **`atomic_units` = 0 on legal_contract .txt + .eml + .md** | The clause-extractor plugin (`extraction/plugins/clauses.py`) probably gates on file format (PDF only?) instead of doc_type. Amendment .txt has `inferred_doc_type=legal_contract` and should produce clause units, same as the MSA PDF. | The doc-detail "Atomic units" accordion is empty for the amendment; the user can't see "this amendment has 3 clauses" the way they see MSA's 5. | S |
| **E4** | **Doc-chain not detected for MSA ↔ Amendment** | `extraction/doc_chains.py` likely uses filename-pattern matching within an extension family (e.g. `*.pdf` only); the Amendment is `.txt`. The Amendment's body explicitly references the MSA — a content-based hint would catch this. | Revision history accordion is empty for both docs; "click MSA → see amendment chain" doesn't work. | M |
| **E5** | **`parse_artifacts` empty for everyone** | Parsers compute layout/OCR-confidence/etc. but don't persist them. This is the Wave-C blocker — without it, no per-element bbox for pixel-precise PDF highlighting. | Wave-B citations work without it (text-based); only Wave-C bbox precision needs it. Deferred. | L |

Legend: S ≈ ½ day, M ≈ 1 day, L ≈ multi-day.

### 1.2 What *is* working well

- L1 chunking + page-mapping (`chunks.source_page_numbers`) — uniform across all formats
- L3 proposed_fields — Gemini open-world inference produces 22–23 rich fields per non-xlsx doc with descriptions + value types
- L3 atomic_units on xlsx — every row becomes a unit with sheet_name/row_index/cells (the citation gold standard)
- L4 triples — works across **all** formats including the ones where L2 mentions fail (different code path; uses chunks directly)
- L2 mentions on .md + .xlsx — produces rich offsets with canonical-entity linkage

---

## 2. Citation plan — make it actually work

The original design (per `gaps_design.md` Design 5, `citations_audit.md`)
defined a polymorphic citation envelope (`pdf_span`, `pdf_bbox`,
`xlsx_row`, `email_message`, etc.) but **deferred the extraction
mechanism to "offset-aware LLM" — never specified**. We don't need an
offset-aware LLM: the LLM already returns the **exact text** of what it
extracted; the worker can just re-search that text in the source.

### 2.1 The fix in one sentence

**Worker-side `resolve_source_position()` step that runs after every LLM
extraction stage, finds the extracted text in the chunk that was sent to
the LLM, and persists (`source_chunk_id`, `source_char_start`,
`source_char_end`).**

Deterministic. No LLM changes. No new dependencies. Works for every
text-based format. PDF/PDF.js text-layer highlighting is then exact
within the cited page (no fuzzy first-match).

### 2.2 Schema changes (additive, one migration)

```sql
-- migrations/sql/0032_source_positions.sql
ALTER TABLE extracted_mentions
    ADD COLUMN source_chunk_id UUID REFERENCES chunks(id),
    ADD COLUMN source_char_start INT,
    ADD COLUMN source_char_end INT;

ALTER TABLE proposed_fields
    ADD COLUMN source_chunk_id UUID REFERENCES chunks(id),
    ADD COLUMN source_char_start INT,
    ADD COLUMN source_char_end INT;

ALTER TABLE extracted_triples
    ADD COLUMN subject_char_start INT,
    ADD COLUMN subject_char_end INT,
    ADD COLUMN object_char_start INT,
    ADD COLUMN object_char_end INT;

-- atomic_units already has parameters.sheet_name + row_index for xlsx
-- (perfect). For non-xlsx units (clauses etc.), reuse the same resolver:
ALTER TABLE atomic_units
    ADD COLUMN source_chunk_id UUID REFERENCES chunks(id),
    ADD COLUMN source_char_start INT,
    ADD COLUMN source_char_end INT;

CREATE INDEX extracted_mentions_source_chunk_idx
    ON extracted_mentions(source_chunk_id);
CREATE INDEX proposed_fields_source_chunk_idx
    ON proposed_fields(source_chunk_id);
```

All columns nullable — backfill is opportunistic, no data migration needed.

### 2.3 Worker resolver

One shared helper:

```python
# src/kb/extraction/source_resolver.py
def resolve_source_position(
    extracted_text: str,
    chunk_text: str,
    chunk_id: UUID,
) -> tuple[UUID, int, int] | None:
    """Find extracted_text in chunk_text. Returns (chunk_id, start, end)
    or None if not found. Prefers longest contiguous match if multiple."""
    # Try exact match first
    idx = chunk_text.find(extracted_text)
    if idx >= 0:
        return (chunk_id, idx, idx + len(extracted_text))
    # Fall back to whitespace-normalized search
    norm_chunk = " ".join(chunk_text.split())
    norm_target = " ".join(extracted_text.split())
    idx = norm_chunk.find(norm_target)
    if idx >= 0:
        # Map normalized offset back to original (slow path)
        return _map_normalized_offset(chunk_text, norm_chunk, idx, len(norm_target), chunk_id)
    return None
```

Wired into each extractor's post-processing:
- `extraction/mentions.py` — for each mention, resolve against original `chunks.text` (not `contextual_chunks.contextual_text` — we want source offsets, not prefix-shifted offsets)
- `extraction/fields.py` — for each proposed field, resolve `value_text` against the chunk(s) sent to the LLM
- `extraction/triples.py` — resolve `subject_text` and `object_text` against the chunk
- `extraction/plugins/clauses.py` — resolve `parameters.summary` against the chunk (for clause units)

### 2.4 API surface

Extend the existing `/files/:id/{mentions,proposed-fields,triples,atomic-units}` endpoints to include the new fields. Pydantic models gain optional `source_chunk_id` / `source_char_start` / `source_char_end`. Already-stored data returns null until backfill.

### 2.5 UI rewrite

Replace `SourceViewer`'s text-search hacks with a single deterministic path:

```ts
// On citation publish:
//   For text formats: jump to source_chunk → wrap source_char_start..end in <mark>
//   For xlsx: use atomic_unit.parameters.row_index (already works) or
//             search for source_chunk's text in the SheetJS HTML
//   For PDF: jump to chunks.source_page_numbers[0] → in PDF.js text layer,
//            wrap the span whose textContent contains chunk_text[source_char_start..end]
```

No more numeric-normalization hacks, no more "first match" fallbacks, no
more "not in source body" banner (because if the resolver returns null at
extraction time, the row gets `source_char_start = NULL` and the UI just
shows "no source location" honestly).

### 2.6 Backfill

One-off worker task (`scripts/backfill_source_positions.py`) that walks
existing mentions / proposed_fields / triples / atomic_units and runs
the resolver. For the demo corpus that's ~250 rows, ~1 minute.

---

## 3. PR2 scope (this is the next branch)

**Lands in one PR:**

1. Migration `0032_source_positions.sql` (additive columns + indexes)
2. `src/kb/extraction/source_resolver.py` (shared helper)
3. Wire resolver into 4 extractors (mentions, fields, triples, clauses)
4. Extend API + Pydantic models with new fields
5. UI rewrite of `SourceViewer` highlight paths (deterministic)
6. Backfill script + run it against the demo corpus
7. Fix **E3** (atomic_units on non-xlsx legal_contract): make `clauses.py` gate on doc_type, not file format. ~30 lines.
8. Update Playwright spec: citation tests now assert pixel-precise highlight (real source offset), not best-effort

**Out of scope for PR2** (separate PRs):

- **E1** (mentions extractor broken on PDF/TXT/EML): root-cause investigation needed. Possibly different chunking shape sent to Gemini per parser. Standalone PR.
- **E2** (`schema_entities` empty): standalone PR or part of Phase 6 work — need to either seed schema_entities at startup or ship the auto-promotion logic that creates them. Architectural.
- **E4** (doc-chain detector): standalone PR. Add content-based hint (Amendment body referencing MSA filename / title) to complement filename-pattern matching.
- **E5** (parse_artifacts persistence for Docling layout): Wave C / pixel-precise PDF bbox. Separate effort.

---

## 4. Acceptance criteria for PR2

On the demo corpus:
- Clicking any `mention` on the right pane highlights the **exact characters** in the left pane (no fuzzy match, no first-occurrence bug)
- Clicking any `proposed_field` on the right pane highlights the **exact value text** in the left pane (was completely broken in PR #26)
- Clicking any `triple` on the right pane highlights the subject AND object in the chunk
- xlsx cell-precise highlight continues to work (`atomic_units.row_index`)
- PDF page-jump + span-level highlight uses `chunks.source_page_numbers` + new source offsets, no random first-text-match
- Amendment .txt shows 3 atomic_units (the three clauses in the doc) — was 0 in PR #26
- New Playwright assertions: `expect(highlightedText).toBe(citation.text)` instead of "highlight exists somewhere"

---

## 4b. Broader-corpus audit (PR3 — 2026-05-25 evening)

Added 20 new docs across 8 domains to surface gaps the narrow NorthWind /
Vertex corpus hid. Also fixed two corpus-generation bugs:
deterministic PDF (`invariant=1`) + xlsx (`_freeze_workbook` helper) so
content-sha dedup actually catches re-uploads.

### 4b.1 Corpus layout (25 unique docs)

| Format | Count | Files |
|---|---:|---|
| PDF | 8 | vertex-msa, mutual-nda, saas-subscription, employment-offer, invoice-mar2026, lab-blood-panel, resume, insurance-eob |
| .md | 9 | vertex-eval-notes, quarterly-summary, jd-data-scientist, performance-review, postmortem, standup, api-design-rfc, press-release, case-study |
| .xlsx | 3 | vertex-pricing-tiers, bank-statement, expense-report |
| .txt | 3 | vertex-amendment, discharge-summary, bug-report |
| .eml | 2 | vertex-sales-thread, it-incident-thread |

Personas covered: legal/contracts, financial, healthcare, HR, engineering,
operations, marketing, insurance — vs the prior single "enterprise SaaS
contract" persona.

### 4b.2 Per-format extraction audit results

| Format | Docs | zero-mentions | zero-units | zero-entities | zero-fields |
|---|---:|---:|---:|---:|---:|
| markdown | 9 | **8** | 9 | 8 | 0 |
| pdf | 9 | **5** | 5 | 5 | 2 |
| plain (txt) | 3 | **3** | 2 | 3 | 0 |
| rfc822 (eml) | 2 | **2** | 2 | 2 | 0 |
| xlsx | 3 | **3** | 0 | 3 | 1 |

### 4b.3 Refined E1 — mentions extractor is broken on MORE than PDF/TXT/EML

The narrow corpus suggested "works on .md + .xlsx, broken on PDF/TXT/EML".
The broader corpus shows the actual pattern is **content-shape dependent,
not format dependent**:

- **PDFs that work** (4 of 9): dense legal/insurance prose — vertex-msa
  (42 mentions), saas-subscription (40), mutual-nda (29), insurance-eob (34)
- **PDFs that fail** (5 of 9): structured / list / form layouts — resume,
  invoice, employment-offer-letter, lab-blood-panel, tiny.pdf
- **Only 1 of 9 markdown files produces mentions** (vertex-eval-notes
  with 44). Press releases, performance reviews, meeting notes, financial
  summaries, postmortems, RFCs — all 0 mentions despite being entity-rich.
- **All 3 xlsx files: 0 mentions**. (Previous "44 mentions on xlsx" was a
  pre-PR3 run; re-upload with deterministic SHA produced 0.)
- **All 3 .txt + 2 .eml: 0 mentions**.

**Re-stated hypothesis for E1**: the mentions extractor's `_USER_TEMPLATE`
asks for char offsets into "the chunk", but the contextualizer prepends
a prefix BEFORE the chunk body in `contextual_text` (which is what the
extractor sees). Many chunks contain non-prose layouts (lists, tables,
headers). The Gemini structured-output schema may be returning `[]`
when it can't confidently produce char offsets — and our code accepts
that empty list as the answer.

Other plausible contributors:
- The L0 chunker emits ONE chunk per file for short docs (n_chunks = 1
  on every doc in the audit). Big chunks → Gemini may truncate output.
- The contextualizer prefix often LOOKS like the doc itself, so the
  extractor may see the prefix as "the chunk" and refuse to point at
  offsets in the unseen prose body.
- Response truncation: `_MAX_OUTPUT_TOKENS = 2000` may cap structured
  output for long lists.

Pass 2 for E1 should:
1. Log the raw Gemini response for a failing chunk to see whether
   it's returning `{"mentions": []}` deliberately or whether parsing
   is dropping rows.
2. Test the prompt with + without the contextual prefix to see which
   variant produces mentions on the failing files.
3. Test smaller chunks for the long docs.

### 4b.4 Open-world fields (proposed_fields) — surprising winners

Fields ARE being extracted on most docs (only 2 with zero):
- lab-blood-panel.pdf (0 fields) — also `doc_type=unknown`, classifier
  likely refused due to medical content
- bank-statement.xlsx (0 fields) — also `doc_type=unknown`

When classification fails, the field-proposer presumably skips. That's
a separate bug — classifier should at least return a generic type so
field inference can proceed.

### 4b.5 Updated priorities

| # | Issue | New severity | Notes |
|---|---|---|---|
| E1 | Mentions extractor fails on most non-dense-prose docs | **CRITICAL** | Bigger than initially scoped — affects 22 of 26 docs (85%) |
| E2 | schema_entities empty | high | still 0 across all 26 |
| E2b | classifier returns `unknown` on healthcare/financial → no proposed_fields | medium | new gap surfaced by broader corpus |
| E4 | doc-chain doesn't link MSA ↔ Amendment | medium | unchanged |
| E5 | parse_artifacts empty (Docling layout dropped) | Wave C | unchanged |

## 5. Wave C (separate effort, not blocking)

| Item | What it unlocks |
|---|---|
| Persist Docling per-element layout (bboxes) in `raw_pages.layout_elements` jsonb | Pixel-precise PDF bbox highlighting (overlay on rendered page, not text-layer span) |
| Same for OCR confidence per region | Visual confidence overlay on scanned PDFs |
| Triple span-offsets within chunk | Hover-to-highlight subject vs predicate vs object |
