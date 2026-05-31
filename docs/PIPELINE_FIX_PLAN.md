# Pipeline Fix Plan — structured-layer rebuild (full detail)

Handoff for a fresh thread. Self-contained: every item has **why / how it
works now (code-grounded) / what changes / acceptance**. Status tracking stays
in `docs/FIX_CHECKLIST.md` (DQ1–DQ4 + this plan).

---

## 0. Root cause in one paragraph
The extractor works (a live re-run of `kv_tables` on loan-addendum-1 returns 19
rich scalars incl. `new_all_in_rate=9.4`). The DB is wrong because of a chain of
issues that mostly trace to **one design decision: the emergent schema gates
what gets stored per document.** A field only reaches a document's structured
record if it was *promoted* to the doc-type schema, and promotion requires
**≥80% prevalence** on **exact-string** field names — so a loan's interest rate
(extracted, in `proposed_fields`, named consistently across 3 of 6 loans) never
becomes a schema field and never lands on the loan. On top of that: the KV call
has **no retry** (transient miss → empty doc), a **frontmatter guard masks** the
empty result, and **lineage orphans** appear for the same docs (no doc_root
instance → no parent FK). Net: facts are retrievable from chunk text but are not
queryable structured fields.

## 1. Target pipeline (corrected order)
```
Upload → Parse → Classify → Chunk(structure-aware, table-level)
      → Embed/Contextualize → Mentions
      → KV+Tables (ONE call, hints, WITH RETRY)
      → Store EVERYTHING per-doc  ← (decoupled from promotion)
      → Coverage check (no silent success)
      → Sameness resolve (canonical: field/table/column names)
      → Promote (count-based on canonical; honor declared)
      → Identity (merge by meaning + noise filter)
      → Lineage (always set parent FK)
      → Finalize (RAPTOR, chains) → Ready
Schema edit/import/correction → Re-extract from CACHED parse+chunks (KV+Tables + schema-driven)
```
Already-correct (no change): Parse, **Classify-before-Chunk** (I1), Embed,
Retrieval, Citations/page-range, Confidence, RAPTOR, Doc-chains.

---

# THE FIXES (dependency order)

## FIX 1 — Store every extracted field per-doc (decouple from promotion) ★ the big one
**Why.** A document's own facts (the loan's 9.40% rate) must live on its own
record so structured queries ("loans with rate>9%") work. Today they don't.

**How it works now.**
- `extract_kv_tables_file_impl` (`tasks.py:1531`) makes ONE LLM call → `scalars`
  + `tables`. Scalars → `proposed_fields` (per-doc candidates, open vocab).
  Table rows → `extracted_entities` child rows (with `unit_type`,
  `parent_entity_id=NULL`).
- Promotion (`promotion.py`) decides which proposed_fields become **schema
  columns** for the doc-type (see FIX 5).
- `extract_schema_entities_file_impl` (`tasks.py:2286`) fills the **doc_root**
  record — but **only for promoted schema fields**. Its own comment
  (`tasks.py:2497-2504`): *"The LLM only runs against schema_entities that have
  promoted schema_fields."* So `read_schema_entities_with_fields` →
  `field_defs` = only promoted columns → the extractor is never asked for the
  rate → doc_root holds only the promoted (mostly frontmatter) fields.
- Result: the rate sits in `proposed_fields` (for 3 loans) but never in the
  queryable `extracted_entities` doc_root.

**What changes.**
- Write **every** extracted scalar onto the document's own structured record
  (its doc_root `extracted_entities.fields`), regardless of promotion — keyed
  by the canonical name (FIX 4) with `original_name` kept for provenance.
- Promotion (FIX 5) becomes purely about *which columns the doc-type schema
  exposes for cross-doc filtering/typing* — it no longer gates per-doc storage.
- Practically: have the per-doc write path persist the doc's own `proposed_fields`
  into its doc_root record (or a per-doc fields store the query layer reads),
  instead of only persisting promoted-schema values.

**Acceptance.** After re-extract, every loan's doc_root record contains its rate
(under canonical `interest_rate`); a structured filter "loans with
interest_rate>9" returns the right loans.

---

## FIX 2 — Retry the KV+Tables extraction
**Why.** A transient 429/timeout currently produces a permanently empty
extraction for that doc (the 3 Acme loans show `gem_pf=0`). You asked for retry
here — it's missing.

**How it works now.** `tasks.py:1781-1796`:
```python
try:
    payload = await extractor.extract(...)
except KVTablesExtractionError:
    # "Don't block the chain on extractor failure — log + advance with empty payload."
    payload = KVTablesPayload(model_id="identity")   # ← empty, NO retry
```
`KVTablesExtractionError` covers transient causes incl. "Gemini returned no
candidates" (`kv_tables.py:683`). The retry helper `with_retry` exists and is
already used for parse (`tasks.py:283`) and schema-entities (`tasks.py:2737`) —
just not here.

**What changes.** Wrap the `extractor.extract(...)` call in `with_retry`
(retry 429/timeout/5xx with backoff). On exhaustion, fall through to the
coverage check (FIX 3) rather than silently succeeding.

**Acceptance.** A simulated 429 on first attempt → retried → succeeds; the doc
gets its fields. Unit test with a fake extractor that fails once then succeeds.

---

## FIX 3 — Frontmatter = metadata only + coverage check (no silent success)
**Why.** The frontmatter guard backfills ~9 header fields even when the body
reader returned nothing, so a failed extraction looks identical to a good one
(256 of 922 `proposed_fields` are `frontmatter:auto`). This hid the bug during
the build. Real PDFs/scans/xlsx have **no** frontmatter, so the guard is a no-op
there (the masking is demo-corpus-specific) — but those formats then have **no
safety net**, so retry+coverage matter MORE for them.

**How it works now.** `tasks.py:1871-1945` (the "Bug K" guard): reads the first
raw page, `_parse_yaml_frontmatter` it, and inserts each key as a
`proposed_fields` row with `model_id='frontmatter:auto'` (overwriting any
same-named LLM scalar — "frontmatter wins"). No-op for PDFs/emails/xlsx (no `---`
delimiter). Nothing records body-vs-header provenance or flags empty body output.

**What changes.**
- Keep frontmatter capture (it drives chains/doc_status/authority) but **tag it
  as metadata** (`source='frontmatter'`), store it separately from content
  fields, and **exclude it from content promotion and from the "extracted-OK"
  signal**.
- Record per-doc **coverage**: # body fields vs # frontmatter fields + extractor
  confidence. If a text-rich doc yields **0 body fields**, mark it
  `needs_review` (degraded), not silently `ready`.

**Acceptance.** A loan with a transient empty body extraction shows up as
`needs_review`, not `ready`. A real PDF with no frontmatter that extracts fields
shows healthy coverage.

---

## FIX 4 — Sameness resolution (canonical names) as a first-class layer
**Why.** The LLM names the same thing differently across docs. Without a
"sameness" map, the same field fragments — breaking both **querying** ("rate" vs
"interest_rate") and **promotion counting** (each name "seen once"). Three
levels: scalar field names, table names, column names. (We saw both
`transactionlisting` and `transaction_listing`.)

**How it works now.**
- Base clustering `cluster_fields_for_doctype` (`promotion.py:64`) groups by
  **exact match after snake_case normalization** (`_normalize_field_name`,
  `promotion.py:44`). Docstring: *"Wave A simplification… Phase 6 will add
  embedding-based blocking + LLM-judge."* So synonyms never merge here.
- A semantic merger DOES exist — `converge_clusters_semantic` (`promotion.py:115`):
  embed `name: description` → block by cosine ≥ sim_threshold → LLM judge → union.
  **But** it's only invoked once, at corpus finalize
  (`converge_workspace_fields_impl`, `tasks.py:4621-4636`), and **promotion does
  not use its output** — promotion still counts raw names. Table-name and
  column-name drift have no semantic merge at all.

**What changes.**
- Make canonicalization a real layer that runs (incrementally) and **feeds both
  promotion counting and the query layer**. Apply it to **scalar names, table
  names, and column names** (not just scalars).
- Resolution = embed `name + description + sample values` → shortlist by cosine
  → cheap LLM yes/no judge → canonical name. **User-declared schema names are
  the anchors** (everything maps to them). Keep `original_name` for provenance.
- Scope **per-doc-type** for promotion (cheaper, avoids cross-context mis-merge);
  add a thin **global** field vocabulary later for cross-type fields
  (`account_number`, party names, dates).

**Acceptance.** `all_in_rate`, `interest_rate_all_in`, `post_amendment_rate` all
resolve to one canonical `interest_rate`; `transactionlisting` and
`transaction_listing` resolve to one `Transaction` sub-entity.

---

## FIX 5 — Promotion: count-based on canonical concepts (not 80% prevalence)
**Why.** You designed "repeats 2–3 times → it's a real field." The code requires
**80% prevalence** instead — meaningless at small N. Concrete: `interest_rate_all_in`
is in **3/6** loans (prevalence 0.50) → **rejected**, though it genuinely repeats.

**How it works now.** `promotion.py`:
```python
class PromotionThresholds:
    prevalence: float = 0.80      # fraction of docs of the type
    stability: float = 0.90
    value_type_confidence: float = 0.90
    min_docs: int = 1             # absolute count — your knob, but...
def should_promote(c, t):         # ...ANDed with prevalence, which dominates
    return (c.n_docs_observed >= t.min_docs and c.prevalence >= t.prevalence
            and c.stability >= t.stability and c.value_type_confidence >= t.value_type_confidence)
```
Now configurable via `extraction.l2b.auto_promotion.*` (P1 wired it). First doc
of a type promotes everything (prevalence 1/1=1.0), so the schema seeds from
doc 1 — but if doc 1 was an empty Acme loan, the schema seeds thin and later
fields can't reach 80%.

**What changes.**
- Promote a **canonical** field/table/column when **seen ≥ N times** (N=2–3),
  type-stable — drop or sharply lower the prevalence gate. Count is on canonical
  concepts (FIX 4), so synonyms add up.
- **User-declared fields are in the schema by declaration** — extracted by their
  declared name (no drift, no count needed). This is the user-defined-schema
  half the spec requires.
- Promotion only **adds/labels/types columns**; it never deletes per-doc data
  (which now lives independently per FIX 1).

**Acceptance.** With canonicalization on, `interest_rate` (seen 3×) promotes to a
typed `loan_agreement` column; the emergent `auto:loan_agreement` schema shows a
real rate field, not just frontmatter.

---

## FIX 6 — Chunk tables at table-level, not per-row
**Why.** One embedded vector per transaction = bloat + weak embeddings +
retrieval noise, for little gain once transactions are reliable structured rows
(FIX 1). Per-transaction precision belongs in the structured layer, not vectors.

**How it works now.**
- Routing: `select_chunker` (`chunking/doc_type_router.py:87`) →
  `_DEFAULT_BY_DOC_TYPE` maps `bank_statement`/`invoice` → `row_per_leaf`
  (with `rows_per_mid` 15/10); else `hierarchical`. Configurable per-doctype via
  the `chunker_configs` table (`extra.rows_per_mid`). **Classification runs
  before this** (I1, `tasks.py:589-610`) — correct.
- Row chunker `chunk_pages_row_per_leaf` (`chunking/__init__.py:266`) already
  builds **root → mids → leaves**: 1 root (whole doc), mids = buckets of
  `rows_per_mid` consecutive rows, **leaves = ONE PER ROW** (`__init__.py:337-349`).
  So a 100-row statement = 1 + 5 + 100 = 106 chunks.

**What changes.**
- Make **leaves = the mid-window** (a block of rows kept intact), i.e. stop
  emitting one leaf per row. Net structure: root → table/row-block chunks (no
  per-row leaf explosion). Tune `rows_per_mid` (block size) per doctype via
  `chunker_configs`. Keep each row intact (never split a row).
- Prose hierarchical chunker (`chunk_pages_hierarchical`, `__init__.py:108`)
  stays as-is (it builds the parent/child tree RAPTOR/auto-merge need).

**Acceptance.** A 100-row statement produces a handful of chunks (root + row
blocks), not 100+; "show me transactions about X" still retrieves; per-row
lookups come from the structured rows.

---

## FIX 7 — Identity: merge by meaning + noise filter
**Why.** Under-merge (`HDFC BANK`135 ≠ `HDFC`80; `Apollo Hospitals` ≠ `…Pune`)
and **noise entities** (doc-IDs, ref-numbers, email domains, rate benchmarks
stored as ORG/PRODUCT). *(Note: the "47% mention resolution" is largely fine —
~43% are noise TYPES (DATE/MONEY/CARDINAL) deliberately skipped by design;
DQ3 is less severe than first stated.)*

**How it works now.**
- Mentions (`extraction/mentions.py`): LLM NER constrained to OntoNotes-18 types.
  `_parse_mentions_json` accepts any valid-typed mention — **no content gate**
  (a doc-ID typed ORG is kept; `hdfcbank.com` typed ORG is kept).
- Resolution `resolve_identities_file_impl` (`tasks.py:2672`): Stage 0 skips
  noise *types* (CARDINAL/DATE/MONEY/…). Stage 1 deterministic = exact
  lowercased name + exact type (`find_entity_deterministic`,
  `domain/entities.py:15`). Stage 2 embedding NN **within the same type**
  (`find_entity_by_embedding`) with thresholds **high 0.92 / low 0.85** (config
  `extraction.identity.embedding_*`). Stage 3 `select_entity_match`
  (`identity/resolve.py:62`): sim≥0.92 auto-match; 0.85≤sim<0.92 → LLM judge;
  sim<0.85 → stop. Stage 4 creates a new canonical entity with
  `canonical_name = mention_text` as-is (no validity gate).
- Under-merge: `HDFC BANK` vs `HDFC` differ on exact name (Stage 1 miss); their
  embeddings likely sit ~0.85–0.91 → below the 0.92 auto-match; the LLM judge
  only fires in that band and may answer "no"; and a **type mismatch**
  (ORG vs PRODUCT) makes them never even candidates.

**What changes.**
- **Noise gate** at mention/entity creation: reject doc-IDs, reference numbers,
  URLs/email-domains, and benchmark strings (pattern + a cheap "is this a real
  named entity?" check) before they become canonical entities.
- **Better merge:** lower/auto-tune the high threshold or add a fuzzy/alias
  pre-block so `HDFC`⊂`HDFC BANK` get judged; consider cross-type candidates for
  obvious org variants; let the LLM judge see more borderline pairs.

**Acceptance.** `HDFC BANK` and `HDFC` collapse to one canonical entity; doc-IDs
/ URLs / `HDFC MCLR` are no longer canonical entities.

---

## FIX 8 — Lineage: always set the parent FK
**Why.** 18% of sub-entities (90/491) have a `lineage_path` but a NULL
`parent_entity_id`. **Same root cause as FIX 1:** when no doc_root *instance* is
created (empty/frontmatter-only extraction), children have no parent to point to.

**How it works now.**
- KV+Tables writes child rows with `parent_entity_id=NULL`
  (`tasks.py:2112-2124`, by design — "set later").
- `extract_schema_entities_file_impl` PASS 3 (`tasks.py:2603-2617`) calls
  `assign_lineage_for_entity` (`extraction/lineage.py:94`) →
  `find_parent_extracted_entity_id`. If the doc_root **instance** doesn't exist
  (because PASS 1 inserted no parent — empty extraction or no promoted fields),
  it returns None → `parent_entity_id` stays NULL. But `compute_lineage_path`
  (`lineage.py:79`) **always returns a path** (self-label for "roots"), so
  `lineage_path` gets set anyway → the orphan signature.

**What changes.** Mostly falls out of FIX 1 (always create a per-doc doc_root
record → children always have a parent to FK to). Belt-and-suspenders: in the
lineage pass, if a child's parent schema-type exists but no instance does,
create/repair the doc_root instance rather than leaving NULL.

**Acceptance.** 0% orphan sub-entities after re-extract; every transaction FKs to
its statement's doc_root.

---

## FIX 9 — Re-extract the corpus (and make re-extract re-run KV+Tables)
**Why.** After FIX 1–5 land, the existing data must be repopulated — especially
the frontmatter-only narrative docs (the 3 Acme loans).

**How it works now.**
- `reextract_workspace_schema_entities_impl` (`tasks.py:4533`) → per file
  `extract_schema_entities_file_impl(file_id, force=True)`. **Reuses cached
  parse+chunks+contextual-chunks (`tasks.py:2436-2438`) — does NOT re-parse /
  re-chunk / re-embed.** Non-destructive (deletes only doc_root parents,
  `tasks.py:2492-2495`).
- **Gap:** it re-runs only the **schema-driven** extraction (promoted fields) —
  NOT the **KV+Tables open-vocab** pass. So it can't discover NEW body fields;
  it only re-fills already-promoted columns.

**What changes.** The corpus re-extract must re-run **KV+Tables** (with retry +
canonicalization + per-doc storage) and then schema-driven — both from cached
chunks. Run it once over finance after FIX 1–5.

**Acceptance.** Post-run, all narrative docs carry their body fields; M1 finance
re-run answers structured-filter questions.

---

## FIX 10 — Schema-change → re-extract without re-parse (P4+F1)
**Why.** §2.2 requires editing the schema to re-derive structured data without
re-parsing. The schema *will* be wrong on v1 for every new domain.

**How it works now.**
- Machinery exists and is cheap (FIX 9: cached reuse, non-destructive, force).
- **But no trigger from user actions:** schema CRUD (`api/schema_hierarchy.py`
  POST/PUT field/entity) and import (`POST /schemas/import.yaml`,
  `domain/schemas.py`) call `bump_schema_version()` but **never enqueue
  re-extraction**.
- The correction path (`domain/corrections.py:613-671`, scope='extraction')
  defers `extract_fields_file` — a task that **no longer exists** (replaced by
  the KV+Tables collapse, `tasks.py:1514`) → it throws, leaving status='fixing'.

**What changes.**
- On schema edit/import (and on a scope='extraction' correction), **enqueue
  re-extraction** for the affected doc-type's `ready` files (force=True, cached
  chunks). Fix the correction path to defer the real task (KV+Tables +
  schema-entities), not the dead `extract_fields_file`.

**Acceptance.** User declares `interest_rate` on the loan schema → affected loans
re-extract from cached chunks → the field populates, no re-parse.

---

# Validation (do alongside)
- **Real-binary E2E proof:** ingest `vertex-msa.pdf` (digital), a scanned PDF,
  and `bank-statement.xlsx` into a fresh workspace with FIX 2+3 in place →
  verify parse→extract→query→cite. Tests real-PDF/scan/xlsx support AND the fix
  on **frontmatter-free** docs (the honest test). Parsers exist + unit-tested
  (`tiny.pdf`/`tiny_scanned.pdf`/`tiny.xlsx`), never proven E2E on real binaries.
- **Re-run M1 finance** after FIX 9; structured-filter Qs (e.g. "loans with
  rate>9%") should now resolve from the structured layer; check no regression on
  the existing 32/50 ok + adversarial 4/4.

# Dependency order
**1 → 2 → 3 → 4 → 5** (makes structured queries real + fixes root cause) →
**6** (chunking) → **7 → 8** (identity/lineage; 8 mostly falls out of 1) →
**9** (re-extract corpus) → **10** (re-extract triggers) → validation.

# Out of scope here (separate gaps, tracked in FIX_CHECKLIST)
- §2.5 **prompts** configurable without code (only thresholds done via P1).
- Write-up consolidation to ≤4 pages.

# Editing discipline
`tasks.py` is ~4k lines and rollback-prone (`memory/editing-cadence-tooling`).
ONE change at a time: Edit → `ast.parse` → import-check → targeted pytest →
commit. Never batch Edit+Bash. Run ONE worker. `python3` not `python`; tests
need `source scripts/dev_env.sh`.
