# Pipeline Fix Plan — structured-layer rebuild

Handoff for a fresh thread. Consolidates the design + fixes agreed in the
data-quality root-cause sessions. Source of truth for *status* stays
`docs/FIX_CHECKLIST.md` (items DQ1–DQ4 + this plan). Everything here is
grounded in the actual code (file:line cited).

## Root cause (one paragraph)
The extractor works (live re-run on loan-addendum-1 → 19 rich scalars incl.
`new_all_in_rate=9.4`). The DB is wrong because: (a) the **frontmatter guard**
backfills header fields even when body extraction returns 0 → masks failures;
(b) the KV+Tables call has **no retry** and swallows errors into an empty
payload; (c) per-doc rich fields **never reach `extracted_entities`** — the
doc_root only gets *promoted* schema fields; (d) promotion gate is **≥0.80
prevalence** (meaningless at N=6) on **exact-string** field names, so a field
seen 3/6 times under varying names never promotes. Net: the loan rate is
retrievable from text but is **not a queryable structured field**.

## Target pipeline (corrected order)
Upload → Parse → **Classify** → **Chunk (structure-aware, table-level)** →
Embed/contextualize → Mentions → **KV+Tables (one call, hints, with retry)** →
**Store everything per-doc** → **Coverage check** → **Sameness resolve
(canonical)** → **Promote (count-based on canonical)** → Identity → Lineage →
Finalize (RAPTOR, chains) → Ready. Schema edit → **re-extract from cached
parse+chunks**.

---

## The fix list (dependency order)

### 1. Decouple per-doc storage from promotion  *(THE BIG ONE — DQ1)*
- **What:** write every extracted scalar/table-row onto the document's own
  `extracted_entities` record, regardless of whether the field is promoted.
- **Why:** today doc_root only gets promoted schema fields, so the loan rate
  (extracted, in `proposed_fields`) never lands on the loan.
- **Code:** `extract_schema_entities_file_impl` (`tasks.py:2286`); the comment at
  `tasks.py:2497-2504` confirms "LLM only runs against schema_entities that have
  promoted schema_fields." Doc_root fields come from here, not from the KV
  scalars.

### 2. Retry on the KV+Tables extraction  *(part of DQ1 / I7)*
- **What:** wrap the extractor call in `with_retry` (429/timeout/5xx).
- **Why:** currently `tasks.py:1781-1796` catches `KVTablesExtractionError` and
  advances with an **empty payload — no retry**. A rate-limit blip = permanently
  empty extraction (almost certainly what hit the 3 Acme loans, `gem_pf=0`).
- **Code:** `tasks.py:1782` (the `await extractor.extract(...)`). `with_retry`
  already exists + used at `tasks.py:283` (parse) and `:2737` (schema entities).

### 3. Coverage check + frontmatter = metadata only  *(DQ1)*
- **What:** (a) tag frontmatter-derived fields as `source='frontmatter'`
  metadata, keep them for chains/status/authority, but **exclude from content
  promotion and from the "extracted OK" signal**; (b) record fields-from-body
  vs from-frontmatter + confidence; (c) a text-rich doc with **0 body fields →
  flag "needs review"**, don't mark clean-ready.
- **Why:** the frontmatter guard masks body-extraction failure (256/922 fields
  are `frontmatter:auto`). Note: real PDFs/scans/xlsx have NO frontmatter
  (guard is a no-op there) — so retry+coverage matter MORE for real binaries.
- **Code:** frontmatter guard at `tasks.py:1871-1945`.

### 4. Sameness resolution (canonicalization) as a first-class layer  *(DQ4 + DQ1)*
- **What:** embed `name + description + sample values` → block by cosine sim →
  cheap LLM yes/no judge → collapse synonyms to one **canonical** name. Apply to
  **scalar field names, table names, AND column names**. Use canonical name in
  the queryable/promotion layer; keep the raw extracted name as `original_name`
  for provenance/citation. Declared schema names are the anchors. Group
  **per-doc-type** for promotion (cheaper + avoids cross-context mis-merges);
  add a thin **global** layer later for cross-type fields (`account_number`,
  party names, dates).
- **Why:** stops naming drift (`all_in_rate`≈`interest_rate`;
  `transactionlisting`≈`transaction_listing`→`Transaction`) from fragmenting the
  same field at query time and at promotion counting.
- **Code:** exists as `converge_clusters_semantic` (`promotion.py:115`) but only
  runs once at finalize (`tasks.py:4626`) and promotion ignores its output; base
  clustering is exact-string (`cluster_fields_for_doctype` / `_normalize_field_name`,
  `promotion.py:44,64`). Make it feed promotion + query, run incrementally.

### 5. Promotion = count-based on canonical concepts  *(DQ1)*
- **What:** promote a canonical field/table/column when **seen ≥ N times** (2–3),
  type-stable — not ≥80% prevalence. Honor **user-declared** fields by name (no
  count needed). First doc seeds the schema (already works). Promotion only
  adds/labels columns, never deletes per-doc data.
- **Why:** 80% prevalence is meaningless at small N; `interest_rate_all_in`
  appears in 3/6 loans (0.50) → rejected though it genuinely repeats.
- **Code:** `PromotionThresholds.prevalence=0.80` + `should_promote`
  (`promotion.py`). Now configurable via `extraction.l2b.auto_promotion.*` (P1).

### 6. Chunking — table-level, not per-row  *(DQ-chunking)*
- **What:** for tabular docs, emit **one chunk per table** (rows kept intact,
  windowed if very large), not one chunk per row. Prose stays hierarchical.
  Classify-before-chunk is already correct (I1).
- **Why:** per-row vectors = bloat + weak embeddings + retrieval noise; per-row
  precision belongs in the structured layer (fix #1). Coarsening shrinks the
  vector index. Granularity is coupled to structured-layer reliability.
- **Code:** the row chunker selected by `select_chunker` for tabular doctypes
  (chunking module / I1 wiring).

### 7. Identity — merge by meaning + noise filter  *(DQ2 + DQ3)*
- **What:** fix under-merge (`HDFC BANK`≠`HDFC`; `Apollo Hospitals`≠`…Pune`);
  add a noise/type gate so doc-IDs, ref-numbers, email domains, rate-benchmarks
  are NOT entities; raise mention-resolution coverage (currently 47%).
- **Code:** `resolve_identities_file_impl` (`tasks.py:~2710`),
  `kb.identity.merge`; mention extraction in `kb.extraction.mentions`.

### 8. Lineage — always set the parent FK  *(DQ — small)*
- **What:** 18% of sub-entities (90/491) have `lineage_path` but null
  `parent_entity_id`. Always set the FK.
- **Code:** lineage pass in `extract_schema_entities_file_impl`
  (`assign_lineage_for_entity`, `kb.extraction.lineage`).

### 9. Re-extract the corpus to repopulate  *(after 1–5)*
- **What:** re-run extraction (from KV+Tables) on the frontmatter-only narrative
  docs — start with the 3 Acme loan-chain docs (`gem_pf=0`).
- **Code:** existing admin re-extract / `reextract_workspace_schema_entities`;
  ensure it re-runs KV+Tables (open-vocab), not just schema-driven.

### 10. Schema-change → re-extract without re-parse  *(P4+F1 / #15)*
- **What:** close the loop — schema edit re-runs from extraction on cached
  parse+chunks. Honors newly-declared fields by name.

---

## Validation (do alongside)
- **End-to-end proof on real binaries:** ingest `vertex-msa.pdf` (digital),
  a scanned PDF, and `bank-statement.xlsx` into a fresh workspace with retry +
  coverage in place → verify parse→extract→query→cite. Tests real-PDF/scan/xlsx
  support AND the fix on frontmatter-free docs (the honest test). Parsers exist
  + unit-tested (`tiny.pdf`/`tiny_scanned.pdf`/`tiny.xlsx`) but never proven
  end-to-end on the real binaries.
- **Re-run M1 finance** after the re-extract; structured-filter questions
  (e.g. "loans with rate>9%") should now be answerable from the structured layer.

## Dependency order
**1 → 2 → 3 → 4 → 5** (makes structured queries real + fixes root cause) →
**6** (chunking) → **7 → 8** (identity/lineage) → **9** (re-extract corpus) →
**10** (re-extract loop) → validation.

## Out of scope here (separate PDF-requirement gaps, tracked in FIX_CHECKLIST)
- §2.5 **prompts** configurable without code (only thresholds done via P1).
- Write-up consolidation to ≤4 pages.

## Editing discipline
`tasks.py` is ~4k lines and rollback-prone (see
`memory/editing-cadence-tooling`). One change at a time: Edit → `ast.parse` →
import-check → targeted pytest → commit. Never batch Edit+Bash. Run ONE worker.
