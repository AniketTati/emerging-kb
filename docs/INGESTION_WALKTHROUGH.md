# Ingestion Pipeline — Ground-Up Walkthrough

A plain-language tour of what happens to a file from the moment it is uploaded
until it is fully searchable. Written for an architecture write-up: every stage
lists **what goes in → what happens (the logic) → what comes out → where it is
stored**. At the end, five "imagine you are this file" journeys (PDF, Excel,
Markdown, Text, Word/docx) show how different formats diverge.

> Grounded in the real code under `src/kb/`. File references are given per stage.
> Where a recent fix changed behavior it is called out as **(FIX n)**.

---

## 0. The big picture — a conveyor belt with stations

Think of ingestion as a **conveyor belt**. A file is placed on the belt and
moves through stations one at a time. Each station does one job, stamps the
file with a new **status** (`lifecycle_state`), and hands it to the next.

The belt is driven by a background job runner (**Procrastinate**). Each station
is a "task". When a station finishes, it *defers* (schedules) the next task.
Stations talk through the database, not memory — each reads what the previous
one wrote.

The full set of statuses, in order:

```
queued → parsing → parsed → chunked (+doc_chaining) → contextualized
      → embedded → raptor_building → mentions_extracting → fields_extracting
      → entities_extracting → identity_resolving → ready
```

`ready` = the file is fully ingested and searchable. `failed` / `deleted` are
dead-ends. Once **all** files in a workspace reach `ready`, a final
**corpus-wide** phase runs once (convergence, re-extract, identity reconcile,
corpus summaries).

Two important framing ideas:

- **Per-doc vs corpus-wide.** Most stations work on one file alone. A few jobs
  ("the schema is wrong", "two docs call the same field different names") can
  only be settled by looking across *all* files of a type — those run in the
  final corpus phase.
- **Two layers come out of one file.** (1) a **vector/search layer** (chunks →
  embeddings → summaries) used for semantic retrieval, and (2) a **structured
  layer** (fields + tables → typed records) used for exact filters like "loans
  with interest rate > 9%". The pipeline builds both from the same parse.

---

## 1. Upload — the file gets on the belt

**Code:** `src/kb/api/files.py` (POST `/files`)

**What goes in:** an HTTP upload — either the raw file (multipart) or a pointer
to a file already staged in object storage (JSON mode).

**The logic:**
1. **Figure out the type.** If the caller didn't say what the file is, the
   server sniffs the first bytes ("magic bytes") to guess the MIME type.
2. **De-duplicate by content.** It computes a SHA-256 hash of the bytes. If a
   file with the same hash already exists (and isn't deleted), it returns the
   existing one (HTTP 200) instead of ingesting a duplicate.
3. **Store the bytes.** New files are written to object storage (MinIO) under a
   key derived from the hash.
4. **Create the record.** A row in the `files` table is created with
   `lifecycle_state = 'queued'`, plus a first audit row in `file_lifecycle`
   (event = `upload`).
5. **Idempotency-Key.** If the caller sends one and retries, the same response
   is replayed (no double-create).
6. **Optional `?parser=` override** (`auto` / `docling` / `gemini`) is saved so
   the parse stage can force a specific PDF parser.
7. **Kick off the belt.** It schedules the `parse_file` task with the file id.

**What comes out:** a `files` row (`queued`) + the bytes in storage.
**Stored in:** `files`, `file_lifecycle`, object storage.

---

## 2. Parse — turn bytes into page text

**Code:** `parse_file_impl` in `src/kb/workers/tasks.py`; parsers in
`src/kb/parsers/`.

**What goes in:** the file id (status `queued`).

**The logic:**
1. Move status to `parsing`. (If already `parsed`, stop — idempotent.)
2. Fetch the bytes from storage.
3. **Pick a parser** (`select_parser_for`). Non-PDF types use a simple registry
   ("can you handle this MIME?"). PDFs use a *strategy*:
   - `auto` (default): peek at the PDF's text layer. If it has real text
     (≥ ~50 chars/page) → use **Docling** (fast, digital). If it's basically
     images (a scan) → use **Gemini OCR**.
   - `docling_first` / `gemini_first` / `gemini_only` / a caller override force
     a specific path.
4. **Parse** with a retry wrapper (transient 429/timeout/5xx are retried).
5. **Quality escalation (PDF only).** After Docling runs, the output is scored
   (how much text is printable, how many pages are non-empty). If it's empty or
   garbled, it **escalates to Gemini OCR** — either the whole doc or just the
   bad pages. A record of what was tried and chosen ("provenance") is kept.
6. **Write pages.** Each page becomes a `raw_pages` row: `page_number`, `text`,
   and `layout_json` (format-specific extras + the provenance stamp).
7. Move status to `parsed`, then schedule `chunk_file`.

**What comes out:** one or more `raw_pages` rows (page text).
**Stored in:** `raw_pages` (append-only).

**Who parses what** (`can_handle`):

| Format | Parser | Page model |
|---|---|---|
| PDF (digital) | **Docling** | one page per PDF page, with layout boxes |
| PDF (scanned/garbled) | **Gemini OCR** (escalation) | renders each page to an image, OCRs to markdown text |
| Excel `.xlsx/.xls` | **XLSXParser** (openpyxl) | one page per **sheet**, rows rendered tab-separated |
| Email `.eml` | **EmailParser** | one page; headers + body, attachments as metadata |
| Markdown `.md` / Text `.txt` | **TextParser** | split into ~3000-char pages on paragraph breaks |
| **Word `.docx`** | **none** → see note | — |

> **⚠ `.docx` is not supported today.** No parser's `can_handle` matches the
> Word MIME (`…wordprocessingml.document`). With the correct MIME it fails with
> `NoParserForMime`; with a missing/generic MIME its ZIP magic bytes
> (`PK\x03\x04`) get caught by the Excel parser's fallback, and `openpyxl` then
> rejects it — also a parse failure. **To support Word, a docx parser must be
> registered** (Docling can do docx, but its `can_handle` is currently
> PDF-only). This is the one gap to flag in your architecture doc.

---

## 3. Classify — decide what kind of document this is (runs *before* chunking)

**Code:** inside `chunk_file_impl` in `src/kb/workers/tasks.py`; classifier in
`src/kb/classification/`. (Internally called **I1**.)

**What goes in:** the file's `raw_pages` text.

**The logic:** if the file doesn't yet have a `doc_type`, an LLM (Gemini Flash)
reads the first ~4000 characters and labels it from a known list
(`bank_statement`, `invoice`, `master_services_agreement`, …) or `unknown`. The
label is saved on the `files` row.

**Why it matters:** the doc type chooses the **chunker** in the next step. A
bank statement should be chunked as rows; a contract as prose. Classifying
*first* is what makes that possible.

**What comes out:** `files.inferred_doc_type` set.
**Stored in:** `files`.

---

## 4. Chunk — break the document into retrievable pieces

**Code:** `chunk_file_impl`; chunkers in `src/kb/chunking/__init__.py`; routing
in `src/kb/chunking/doc_type_router.py`.

**What goes in:** `raw_pages` + the doc type.

**The logic — pick a chunker (`select_chunker`):**
1. Workspace-specific override (`chunker_configs`), else
2. Built-in per-doc-type default, else
3. MIME-based default, else
4. fall back to **hierarchical**.

**Two main chunkers:**

- **Hierarchical (prose: contracts, markdown, text, most PDFs).** Builds a
  3-level tree: **root (whole doc) → mid (~512-token sections) → leaf
  (~128-token pieces)**. Only the leaves are searched; parents are pulled in at
  query time ("auto-merging") when many leaves under the same parent match.
- **Row-per-leaf (tabular: bank statements, invoices, spreadsheets).** Builds
  **root → row-block leaves**. **(FIX 6)** each leaf is now a *block* of
  `rows_per_mid` consecutive rows kept intact (e.g. 15 rows), **not one leaf per
  row**. So a 100-row statement makes ~7 leaves, not 100+. Rows are never split
  mid-row. Per-row precision comes from the structured layer (step 8), not from
  vectors.

Each chunk stores `node_level` (0=leaf, 1=mid, 2=root) and a `parent_chunk_id`
link. Move status to `chunked` and schedule `contextualize_file`. A separate
**doc-chain detection** branch is also scheduled later (step 8) once fields
exist.

**What comes out:** `chunks` rows forming a tree.
**Stored in:** `chunks`.

---

## 5. Contextualize — give each piece a one-line "you are here"

**Code:** `contextualize_file_impl`; `src/kb/contextualization/`.

**What goes in:** the **leaf** chunks (level 0) + the full document text.

**The logic:** for each leaf, an LLM writes a short (50–100 token) prefix that
says where the chunk sits in the document (e.g. *"This section is the warranty
clause of the MSA between Acme and Vertex…"*). The stored text becomes
`prefix + "\n\n" + original chunk`. This is Anthropic's "contextual retrieval"
trick — it makes a lonely chunk searchable even when the query words aren't in
the chunk itself. (No API key → identity mode: prefix is empty, text unchanged.)

**What comes out:** `contextual_chunks` rows (one per leaf).
**Stored in:** `contextual_chunks`. Then schedule `embed_file`.

---

## 6. Embed — turn text into vectors

**Code:** `embed_file_impl`; `src/kb/embeddings/`.

**What goes in:** the `contextual_chunks` text.

**The logic:** each contextual chunk is turned into a 3072-dimension vector by
the Gemini embedding model (or a deterministic mock when no key is set, so tests
are reproducible). Vectors are what semantic search compares.

**What comes out:** `chunk_embeddings` rows (vectors).
**Stored in:** `chunk_embeddings`. Then schedule `raptor_build_file`.

---

## 7. RAPTOR (per-doc) — build a summary tree of the document

**Code:** `raptor_build_file_impl`; `src/kb/domain/raptor.py`.

**What goes in:** the leaf embeddings + texts.

**The logic:** cluster similar leaves into groups, **summarize each group** with
an LLM, embed those summaries, then cluster *those* into higher groups, and so
on up the tree. This gives the document a pyramid of summaries (level 2, 3, …)
so a broad question ("what is this doc about?") can hit a summary node instead
of many tiny leaves.

**What comes out:** `raptor_nodes` (summaries) + `raptor_edges` (parent→child).
**Stored in:** `raptor_nodes`, `raptor_edges` (scope = per-doc). Then status
moves to `mentions_extracting` and `extract_mentions_file` is scheduled.

---

## 8. The structured layer (the heart of "exact answers")

This is where the file stops being "text to search" and becomes "facts you can
filter and aggregate". Three sub-steps run in order.

### 8a. Mentions — find named things

**Code:** `extract_mentions_file_impl`; `src/kb/extraction/mentions.py`.

**The logic:** an LLM does Named-Entity Recognition over each contextual chunk,
labeling spans with OntoNotes-18 types (PERSON, ORG, GPE, DATE, MONEY, …).
**(FIX 7)** a **noise gate** (`is_noise_mention_text`) drops junk before it can
become an "entity": doc-IDs (`INV-2024-001`), reference numbers, URLs/email
domains, and rate benchmarks (`HDFC MCLR`). It's *type-aware* — a real `DATE`
like `2024-01-15` is kept; the same string mislabeled `ORG` is dropped.

**Stored in:** `extracted_mentions`. Status → `fields_extracting`; schedule
`extract_kv_tables_file`.

### 8b. KV+Tables — the one big extraction call

**Code:** `extract_kv_tables_file_impl`.

This single LLM call does two things at once: pull **scalars** (key→value facts,
like `interest_rate = 9.4`) and **tables** (repeating rows, like a list of
transactions).

**The logic, in order:**
1. **Hints.** Before calling, it gathers existing field/column names (from the
   schema and from what other docs of this type used) and passes them as hints,
   so the LLM reuses names instead of inventing new ones each time.
2. **The call, with retry. (FIX 2)** wrapped in `with_retry` — a transient
   429/timeout/5xx *or* an empty "no candidates" response is retried with
   backoff (this was the cause of some docs silently coming back empty).
3. **Scalars → `proposed_fields`.** Each scalar is stored per-doc. The value is
   also parsed into a real number where possible (`value_numeric`, via
   `value_normalize` — handles `9.40%`, `₹22 lakh`, `(500)`), so range filters
   work later.
4. **Frontmatter guard. (FIX 3)** if the file top has YAML frontmatter
   (`--- … ---`), those keys are captured and **tagged as metadata**
   (`model_id = 'frontmatter:auto'`) — they don't count as real "body" content.
5. **Tables → `extracted_entities` children.** Each table row becomes a child
   record (`unit_type` = table name, `fields` = the row's columns). Column names
   and row keys are snake-cased here, so `Txn Date`/`txn_date` already line up.
6. **Bootstrap the schema.** `ensure_auto_schema_entity` makes a doc-type schema
   (`auto:<doc_type>`) with a **doc_root** type; `ensure_sub_entity_type` makes a
   child type per table. **(FIX 4)** `normalize_unit_key` reuses an existing type
   when names are spelling variants (`transaction_listing` ≈ `transactionlisting`
   ≈ `Transactions`) so the schema doesn't fragment.
7. **Coverage check. (FIX 3)** record per-doc coverage (how many body fields vs
   frontmatter fields vs table rows). If a text-rich doc produced **0** body
   fields and **0** rows, mark it `extraction_degraded` so it surfaces in the
   "needs review" view instead of looking healthy at `ready`.
8. Status → `entities_extracting`; schedule `extract_schema_entities_file`,
   `extract_triples_file`, `detect_doc_chain_file`. (**Doc-chain** detection runs
   here because it needs the just-written fields like `chain_id`/`parent_doc` to
   link revisions of the same document.)

**Stored in:** `proposed_fields`, `schema_fields`/`inferred_schema_fields`,
`schema_entities`, `extracted_entities` (children).

### 8c. Doc-root + lineage — make the doc's own facts queryable

**Code:** `extract_schema_entities_file_impl`.

**The logic:**
1. An LLM fills in the doc-type's **promoted** schema fields for this doc
   (parent record).
2. **(FIX 1 — the big one)** the doc's **own** fields are merged onto its
   queryable record. `build_doc_root_fields` takes *every* `proposed_field`
   (promoted or not, numeric where possible) and writes them onto the doc_root
   `extracted_entities` row (the one with `unit_type IS NULL`). So a loan's rate
   lands on the loan even if "interest_rate" was never promoted to a column.
   This is what makes the F-mode filter "loans with interest_rate > 9" work.
3. **(FIX 8)** if the LLM produced no parent record (e.g. a frontmatter-only
   doc), one is **still created** from the doc's own fields — but only when none
   exists, so a transient-empty re-run never wipes a good record.
4. **Lineage.** Children (table rows) are linked to their parent doc_root:
   `parent_entity_id` is set and an `lineage_path` (ltree) is computed, after a
   topological sort so parents are processed before children. This is why every
   transaction "knows" which statement it belongs to.

**Stored in:** `extracted_entities` (parent doc_root + updated child links).
Status → `identity_resolving`; schedule `resolve_identities_file`.

---

## 9. Identity resolution — merge mentions into canonical entities

**Code:** `resolve_identities_file_impl`; `src/kb/identity/resolve.py`.

**What goes in:** the file's `extracted_mentions`.

**The logic** (per mention):
- **Stage 0:** skip noise *types* (numbers/dates/money never become entities).
- **Stage 1:** exact name+type match → reuse that entity.
- **Stage 2/3:** find nearest existing entities by embedding; ≥ 0.92 similarity
  auto-matches; the 0.85–0.92 "maybe" band asks an LLM judge yes/no.
- **(FIX 7)** **alias routing:** if the mention is an obvious short-form of a
  candidate (`HDFC` ⊂ `HDFC BANK`, prefix rule), it's sent to the judge **even
  below 0.85** — the judge still decides, so nothing is auto-merged on name
  alone, but `HDFC` and `HDFC BANK` now get the chance to merge.
- **Stage 4:** no match → create a new canonical entity.

**What comes out:** `entities` + `mention_to_entity` links. Status → `ready`.
Then graph-build tasks run, and the **corpus finalize** is nudged.

---

## 10. Corpus finalize — the once-per-workspace cleanup

**Code:** `finalize_corpus_impl`.

**The logic:** this only runs when **no file is still in-flight** (a cheap count
gates it; during a batch it's a no-op until the last file lands). Then, in order:

1. **Field convergence.** Re-cluster `proposed_fields` across **all** docs of a
   type and merge same-meaning names with embeddings + an LLM judge
   (`total_cost` ≈ `total_amount`). **(FIX 4)** **user-declared** field names act
   as *anchors* — emergent variants (`all_in_rate`, `post_amendment_rate`) fold
   into the declared `interest_rate`.
2. **Promotion. (FIX 5)** a field becomes a real typed column when it is seen in
   **≥ N docs** (count-based, default **2**) *or* clears the old prevalence bar —
   not the old "must be in 80% of docs" rule that rejected fields appearing in
   3 of 6 loans. User-declared fields skip this entirely (they're columns by
   declaration — verified in the schema-driven extractor, which reads *all*
   active fields, no promotion filter).
3. **Cold-start re-extract. (FIX 9)** re-run extraction for every ready doc
   **using cached chunks (no re-parse)** so the now-stable schema/rules apply.
   Crucially this now re-runs **KV+Tables**, not just the schema-driven pass, so
   newly-understood body fields are actually discovered. Non-destructive: a
   transient-empty re-run keeps existing data.
4. **Identity reconcile** (merge duplicates the per-doc pass missed) → **chain
   renumber** → **corpus RAPTOR** (a summary tree spanning the whole corpus).

**Schema edits later? (FIX 10)** when a user edits or imports a schema (or files
a "extraction" correction), the system **auto-schedules a re-extract** of the
affected doc type's ready files from cached chunks — so a corrected schema reaches
already-ingested docs without re-parsing. The job is coalesced (a queue lock)
so rapid edits don't pile up.

---

## 11. "Imagine you are this file" — five journeys

### 📄 A digital PDF contract (`vertex-msa.pdf`)
Upload → MIME `application/pdf` → **Docling** (text layer present) → pages with
layout → classify = `master_services_agreement` → **hierarchical** chunks
(root→mid→leaf) → contextual prefixes ("warranty clause of the MSA…") →
embeddings → per-doc summary tree → mentions (Acme, Vertex, dates) → KV+Tables
pulls scalars (effective_date, term, governing_law) and maybe a payment table →
doc_root gets all those fields → entities (Acme≡Acme Corporation) → `ready`.

### 🖼️ A scanned PDF (image-only)
Same start, but the text-layer peek finds almost no text → routed to **Gemini
OCR** (or Docling runs, scores badly, and **escalates** to OCR). OCR renders each
page to an image and reads it back as markdown text. From `raw_pages` onward it's
identical to the digital PDF. (This path is real-code but **not yet proven E2E on
real scanned binaries** — a known validation gap.)

### 📊 An Excel bank statement (`statement.xlsx`)
Upload → MIME = spreadsheet → **XLSXParser** → **one page per sheet**, rows as
tab-separated text → classify = `bank_statement` → **row-per-leaf** chunks: root
+ one leaf per **block of ~15 rows** (FIX 6) → each block gets a context prefix +
embedding (good for "show me transactions about rent") → mentions →
**KV+Tables** is where the real value is: each row becomes a structured
`transaction` child record (date, amount, description as typed columns) under the
statement's doc_root → per-row precision lives here, not in vectors → `ready`.
Querying "transactions > ₹1cr" filters these child records.

### 📝 A Markdown / plain-text note (`README.md`, `notes.txt`)
Upload → MIME `text/markdown` or `text/plain` → **TextParser** → split into
~3000-char pages on paragraph boundaries (markdown is kept as raw source, not
rendered) → classify (often `unknown` for free-form notes) → **hierarchical**
chunks → contextual prefixes → embeddings → summary tree → mentions → KV+Tables
(may find few/no structured fields; if it's text-rich but yields nothing, FIX 3
flags it degraded) → `ready`. Mostly lands in the **search layer**, light on the
structured layer.

### 📃 A Word document (`contract.docx`) — **fails today**
Upload → MIME `…wordprocessingml.document` → **no parser matches** →
`NoParserForMime` → status `failed` (the failure reason is visible in the file's
lifecycle detail). If the MIME was missing, its ZIP magic bytes get caught by the
Excel parser and `openpyxl` rejects it — still a parse failure. **Action item for
the architecture:** register a Word parser (Docling supports docx; its
`can_handle` just needs to accept the Word MIME) and it would then flow exactly
like a prose PDF.

---

## 12. Where everything is stored (quick map)

| Layer | Table | Holds |
|---|---|---|
| Source | `files`, `file_lifecycle` | file record + status history |
| Source | `raw_pages` | parsed page text + layout/provenance |
| Search | `chunks` | tree of text pieces (root/mid/leaf) |
| Search | `contextual_chunks` | leaf text + "you are here" prefix |
| Search | `chunk_embeddings` | 3072-d vectors |
| Search | `raptor_nodes` / `raptor_edges` | summary tree (per-doc + corpus) |
| Structured | `proposed_fields` | per-doc scalars (with numeric value) |
| Structured | `inferred_schema_fields` / `schema_fields` | candidate + promoted columns |
| Structured | `schema_entities` / `schema_relationships` | doc-type schema (doc_root + tables) |
| Structured | `extracted_entities` | the queryable records (doc_root + child rows) |
| Identity | `extracted_mentions` | raw named-thing spans |
| Identity | `entities` / `mention_to_entity` | canonical entities + links |
| Quality | `files.extraction_coverage` / `extraction_degraded` | per-doc health flag (FIX 3) |

---

## 13. Design decisions worth putting in your arch doc

1. **One file → two layers** (search + structured) from a single parse.
2. **Classify before chunk** so tabular vs prose get the right chunker.
3. **Tables: rows are structured records, blocks are vectors** (FIX 6) — keeps
   the vector index small and pushes per-row precision into SQL-like filters.
4. **A doc's own facts live on its own record** (FIX 1), decoupled from whether a
   field was "promoted" corpus-wide — this is what makes structured filters real.
5. **Promotion is count-based** (FIX 5): repeats ≥ 2 → real column; declared
   fields are columns by declaration.
6. **Canonical names** (FIX 4): tables/fields with spelling variants collapse;
   user-declared names anchor the merge.
7. **No silent success** (FIX 2 retry + FIX 3 coverage): empty extractions are
   retried, and a text-rich-but-empty doc is flagged, not quietly marked done.
8. **Re-derive without re-parse** (FIX 9/10): schema changes and corpus
   convergence re-run extraction from cached chunks; schema edits auto-trigger it.
9. **Stations are decoupled via the DB + a job queue**, each defers the next, so
   a queue hiccup never rolls back completed work.

### Known gaps (honest list)
- **`.docx` is unsupported** — needs a Word parser registered (§2 / §11).
- **Real-binary E2E** (digital PDF / scanned PDF / xlsx) is coded but not yet
  proven on real files end-to-end.
- **Query-side canonical mapping** (matching a user's query word to the stored
  canonical field name) and **semantic column-synonym merge** are the remaining
  parts of FIX 4, on the query side.
