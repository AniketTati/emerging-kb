# How the System Actually Works — A Walkthrough

**Audience:** someone new to RAG / KB systems who wants to understand the whole pipeline, not just slogans.
**Approach:** trace one real document through ingestion, then trace one real question through retrieval, with concrete numbers and storage locations at every step.

---

## 0. The Mental Model

Before any diagrams, three analogies that map onto the system.

**The system is a librarian, a detective, and a translator.**

```
LIBRARIAN     organises documents on shelves, writes catalog cards, builds the
              back-of-book index, maintains a "see also" cross-reference list.
              → Our INGESTION pipeline.

DETECTIVE     when asked a vague question, generates hypotheses, searches multiple
              evidence sources in parallel, ranks suspects, presents the case
              with citations.
              → Our RETRIEVAL pipeline.

TRANSLATOR    bridges the user's everyday vocabulary ("party", "fast") to the
              document's formal vocabulary ("offsite event", "deliver within 4
              hours of order confirmation").
              → Our QUERY REWRITING + EMBEDDING layers.
```

**Two truths that drive everything:**

1. **What's expensive to compute, we compute once.** Parsing a scanned PDF takes seconds and costs OCR money. We never redo it. Whatever uses it can rerun cheaply.
2. **Schema is a *view*, not a precondition.** The system can answer questions on day one with zero schema. As you teach it what you care about ("Vendor has a GST number"), more structured answers become available — but the raw understanding never depended on the schema.

Hold these two ideas. Everything else is implementation.

---

## 1. Ingestion — A Single Document's Journey

Let's follow one real document: a 30-page power supply contract from the Enron corpus (`enron_epe_powersupply_1999.pdf`), digital PDF, ~1.2 MB.

Time stamps are illustrative for a **digital PDF under good network conditions** with Gemini Flash at typical 1–2s/call latency and modest parallelism (~5 concurrent LLM calls). Scanned PDFs add 30–60s for Mistral OCR vendor call. Network slowness or Gemini rate-limit will stretch this — not a problem for ingest (Procrastinate queue handles backpressure) but worth noting that "T+90s" is best-case, not guaranteed.

```
T+0s        File hits /upload
            ┌──────────────────────────────┐
            │ Multipart upload (resumable) │
            │ Bytes streamed to MinIO       │
            └──────────────┬───────────────┘
                           ↓
            ┌──────────────────────────────────────────────┐
            │ SHA-256 hash computed over bytes              │
            │ → 0x7af3c…  (file fingerprint)               │
            │                                              │
            │ Look up in `files` table:                    │
            │ • If hash exists → link to existing,         │
            │   do nothing else (DEDUP)                    │
            │ • If new → insert row,                       │
            │   lifecycle_state = 'received'               │
            │   Enqueue job 'parse' keyed by hash          │
            └──────────────────────────────────────────────┘

T+1s        Classifier (cheap rules + small model, ~2ms)
            ┌──────────────────────────────────────────────┐
            │ Sniffs extension, magic bytes, first 2 pages │
            │ Output:                                      │
            │   format = digital_pdf                       │
            │   doc_type = contract                        │
            │   lang = en                                  │
            │   ocr_needed = false                         │
            └──────────────────────────────────────────────┘

T+3s        Parser route → Docling (digital PDFs)
            ┌──────────────────────────────────────────────┐
            │ Docling reads layout, paragraphs, tables,    │
            │ headings, page boundaries, bounding boxes.   │
            │                                              │
            │ Output (30 rows): raw_pages table            │
            │   (file_id, page_no, text, layout_json,      │
            │    tables_json, bboxes, ocr_confidence)      │
            │                                              │
            │ This is the IMMUTABLE backbone. We never     │
            │ re-derive it. Everything downstream reads    │
            │ from here.                                   │
            └──────────────────────────────────────────────┘

T+8s        Chunking — layout-aware, ~2K tokens per chunk
            ┌──────────────────────────────────────────────┐
            │ Respect section/paragraph boundaries.        │
            │ 30 pages → 12 chunks.                        │
            │                                              │
            │ chunks table:                                │
            │   (file_id, chunk_id, page_range, text,      │
            │    char_offset_start, char_offset_end)       │
            └──────────────────────────────────────────────┘

T+10s       Contextual prefix (Anthropic 2024 technique)
            ┌──────────────────────────────────────────────┐
            │ For each chunk, ask Gemini Flash:            │
            │   "Here is the document context [cached].    │
            │    Here is one chunk. Write a 50-token       │
            │    prefix locating this chunk inside the     │
            │    document."                                │
            │                                              │
            │ The document-level context is prompt-CACHED, │
            │ so per-chunk cost is ~$0.0001.               │
            │                                              │
            │ Result for chunk #7:                         │
            │ Original chunk: "...Indemnification cap of   │
            │ $25,000,000 per occurrence shall not be      │
            │ exceeded..."                                 │
            │                                              │
            │ Contextualized:                              │
            │ "[Context: Section 12 of power supply        │
            │ agreement between Enron Energy Services      │
            │ and El Paso Electric, governing indemnity    │
            │ obligations.] ...Indemnification cap of      │
            │ $25,000,000..."                              │
            │                                              │
            │ Why this matters: the chunk's embedding now  │
            │ carries doc context. When someone asks       │
            │ "indemnity cap in our power deals", this     │
            │ chunk matches strongly. Without the prefix,  │
            │ the bare "$25M cap" floats with no anchor.   │
            └──────────────────────────────────────────────┘

T+12s       Embed + index
            ┌──────────────────────────────────────────────┐
            │ Each of 12 contextual chunks →               │
            │   • Gemini Embedding 001 (768-dim vector)    │
            │     stored in pgvector HNSW index            │
            │   • Tokenized + indexed in pg_search (BM25)  │
            │                                              │
            │ Total: 12 vectors + 12 BM25 entries.         │
            └──────────────────────────────────────────────┘

T+18s       RAPTOR tree build (hierarchical summaries)
            ┌──────────────────────────────────────────────┐
            │ Cluster the 12 chunk embeddings into ~4      │
            │ groups (by similarity).                      │
            │                                              │
            │ For each group, ask Gemini to summarise the  │
            │ chunks in that group.                        │
            │                                              │
            │ Then summarise the 4 group summaries into 1  │
            │ doc-level summary (the "catalog card").      │
            │                                              │
            │ raptor_nodes table:                          │
            │   level 0: 12 chunks (leaves)                │
            │   level 1: 4 group summaries                 │
            │   level 2: 1 doc summary card                │
            │   level 3: (built later, across all docs)    │
            │                                              │
            │ The doc-level summary reads:                 │
            │ "Power supply agreement (1999) between       │
            │ Enron Energy Services and El Paso Electric   │
            │ for 10-year term. Includes $25M indemnity    │
            │ cap, force majeure clauses, governing law    │
            │ Texas, payment terms net 30..."              │
            │                                              │
            │ Why this matters: vague queries hit the      │
            │ summary; precise queries hit the leaf chunk. │
            │ Same retrieval interface, different levels.  │
            └──────────────────────────────────────────────┘

T+25s       L2 mention extraction (UNIVERSAL types, on EVERY doc)
            ┌──────────────────────────────────────────────┐
            │ Gemini Flash with a generic prompt:          │
            │ "Extract typed mentions from the universal   │
            │ list: PERSON, ORG, MONEY, DATE, LOCATION,    │
            │ EVENT, ACTIVITY, ... and simple triples."    │
            │                                              │
            │ Output:                                      │
            │ mentions table — 47 rows, e.g.:              │
            │   "Enron Energy Services" → ORG  p1, l3      │
            │   "El Paso Electric"       → ORG  p1, l3     │
            │   "$25,000,000"            → MONEY p7, l12   │
            │   "January 1, 2000"        → DATE  p2, l1    │
            │   "Houston, Texas"         → LOC   p1, l8    │
            │   ...                                        │
            │                                              │
            │ open_triples (temp) — 22 rows, e.g.:         │
            │   (Enron Energy Services, supplies, power)   │
            │   (Enron Energy Services, indemnifies,       │
            │    El Paso Electric)                         │
            │                                              │
            │ Purpose: cross-doc entity navigation — "show │
            │ me all docs mentioning Enron Energy."        │
            └──────────────────────────────────────────────┘

T+28s       L2b emergent-field extraction (OPEN vocabulary)
            ┌──────────────────────────────────────────────┐
            │ Gemini Flash with an open-vocabulary prompt: │
            │ "Identify the structured information this    │
            │ doc contains. For each, propose field_name   │
            │ (snake_case), value, one-line description.   │
            │ Propose a doc_type_label."                   │
            │                                              │
            │ NO fixed type list. The doc proposes its     │
            │ own vocabulary.                              │
            │                                              │
            │ Output for this contract:                    │
            │ {                                            │
            │   doc_type_proposal: "power_supply_agreement"│
            │   doc_type_confidence: 0.93,                 │
            │   fields: [                                  │
            │     { name: "buyer",                         │
            │       value: "El Paso Electric",             │
            │       description: "purchasing party" },     │
            │     { name: "seller",                        │
            │       value: "Enron Energy Services",        │
            │       description: "supplying party" },      │
            │     { name: "term_years", value: 10, ... },  │
            │     { name: "indemnity_cap_usd",             │
            │       value: 25000000, ... },                │
            │     { name: "governing_law",                 │
            │       value: "Texas", ... },                 │
            │     { name: "payment_terms",                 │
            │       value: "net 30", ... }                 │
            │   ]                                          │
            │ }                                            │
            │                                              │
            │ Stored: emergent_fields table, with name +   │
            │ description + value embeddings per row.      │
            │                                              │
            │ Why this matters: as more power-supply       │
            │ agreements arrive, their fields cluster.     │
            │ After ~20 such docs with prevalence ≥ 80%,   │
            │ stability ≥ 0.9, and clear value types,      │
            │ the system AUTO-PROMOTES the inferred schema │
            │ into typed schema — no user click required.  │
            │ The promotion is audit-logged and reversible │
            │ if the user disagrees.                       │
            │                                              │
            │ This is "schema emerges from data" working,  │
            │ literally: discovery → induction → promotion │
            │ → typed access, all automatic.               │
            └──────────────────────────────────────────────┘

T+35s       L3 atomic-unit extraction (doc-type-specific plug-in)
            ┌──────────────────────────────────────────────┐
            │ Because doc_type = contract → unit = clause. │
            │ (Bank statement → transaction; drawing →     │
            │  component; xlsx → row; note → none.)        │
            │                                              │
            │ Layout cues (numbered sections) + LLM split  │
            │ → 18 clauses.                                │
            │                                              │
            │ Each clause typed against CUAD taxonomy:     │
            │   clause #1   payment_terms                  │
            │   clause #2   term_and_termination           │
            │   clause #3   delivery_obligations           │
            │   ...                                        │
            │   clause #12  indemnification                │
            │   clause #13  limitation_of_liability        │
            │   ...                                        │
            │                                              │
            │ Each clause's parameters extracted:          │
            │   #12 indemnification: { cap: 25_000_000,    │
            │                          currency: USD,      │
            │                          scope: per_event }  │
            │                                              │
            │ Each clause's rarity_score computed against  │
            │ corpus centroid for its type:                │
            │   indemnification clauses corpus centroid:   │
            │     mean_cap ≈ $5M, σ ≈ $8M                  │
            │   this clause cap = $25M → z-score 2.5       │
            │   → rarity_score = 0.91 (rare-ish)           │
            │                                              │
            │ Stored: clauses table + clause_embeddings    │
            │                                              │
            │ Why this matters: queries like "show me      │
            │ indemnity caps" filter `clauses` directly    │
            │ rather than search free text. Queries like   │
            │ "unusual indemnity terms" filter by          │
            │ rarity_score.                                │
            └──────────────────────────────────────────────┘

T+45s       Identity resolution
            ┌──────────────────────────────────────────────┐
            │ For each ORG mention, find candidate matches │
            │ in the existing entities table:              │
            │                                              │
            │ 1. Deterministic blocking — exact match on   │
            │    canonical name, normalized                │
            │ 2. Embedding blocking — top-5 cosine nearest │
            │    surface forms                             │
            │ 3. LLM judge — Gemini reads (mention_A,      │
            │    mention_B) and returns                    │
            │    { is_same: true/false, reason }           │
            │ 4. Union-find — high-confidence positives    │
            │    merged into clusters                      │
            │                                              │
            │ Outcome for this doc:                        │
            │   "Enron Energy Services" was already a      │
            │   canonical entity (from earlier ingests)    │
            │   → link to entity E-001                     │
            │   "El Paso Electric" → new canonical E-178   │
            │                                              │
            │ Stored: mention_to_entity link table         │
            └──────────────────────────────────────────────┘

T+50s       Relationship layer
            ┌──────────────────────────────────────────────┐
            │ Open triples whose args resolved to entities │
            │ become typed edges:                          │
            │                                              │
            │ relationships table:                         │
            │   (E-001, has_supply_contract_with, E-178,   │
            │    evidence: this_doc, p1, span [3,87],      │
            │    confidence: 0.93)                         │
            │                                              │
            │ Predicates are typed if the schema knows     │
            │ them, free-text otherwise.                   │
            └──────────────────────────────────────────────┘

T+55s       Schema-driven extraction (only if schema active)
            ┌──────────────────────────────────────────────┐
            │ The active schema says:                      │
            │   Contract { parties[], term_years,          │
            │              indemnity_cap, governing_law,   │
            │              payment_terms }                 │
            │                                              │
            │ Gemini Flash with JSON-schema constrained    │
            │ outputs reads the doc and emits:             │
            │                                              │
            │ extracted_entities row:                      │
            │   type: Contract                             │
            │   schema_version: v3                         │
            │   fields: {                                  │
            │     parties: [E-001, E-178],                 │
            │     term_years: 10,                          │
            │     indemnity_cap: 25_000_000,               │
            │     governing_law: "Texas",                  │
            │     payment_terms: "net 30"                  │
            │   }                                          │
            │   citations: { each field → (page, span) }   │
            │                                              │
            │ If you later add field `arbitration_clause`  │
            │ to the schema — only THIS step reruns,       │
            │ everything before is preserved.              │
            └──────────────────────────────────────────────┘

T+65s       HippoRAG graph update (incremental)
            ┌──────────────────────────────────────────────┐
            │ New entities + new edges added to the PPR    │
            │ graph index. No full rebuild.                │
            └──────────────────────────────────────────────┘

T+75s       Artifact generation (async, can finish later)
            ┌──────────────────────────────────────────────┐
            │ Briefing-doc paragraph for this doc.         │
            │ FAQ candidates ("What is the indemnity      │
            │ cap?", "Who are the parties?", ...).         │
            │ Suggested follow-up questions.               │
            │ Mind-map nodes + edges added.                │
            └──────────────────────────────────────────────┘

T+90s       Lifecycle: state = ready
            ┌──────────────────────────────────────────────┐
            │ UI shows ✓ green. The doc is now queryable. │
            │ Every step above was IDEMPOTENT — re-running │
            │ on the same file is a no-op.                 │
            └──────────────────────────────────────────────┘
```

That's the journey. For a 100K-doc corpus, this runs in parallel across many worker processes. Per-doc isolation: if one fails, others continue.

---

## 1.5 The same pipeline on seven very different document types

The contract walkthrough above is one of many possible routes. The L3 "clauses" stage I described is a doc-type-specific specialisation; the universal layer is **Atomic Units** — typed structured records inside a doc, where "what counts as a unit" varies by doc type.

| Doc | Parser | L3 atomic unit | Notable L4 behaviour |
|---|---|---|---|
| **Handwritten note** (jpg) | Mistral OCR 3, VLM fallback | (none — L2 mentions only) | Resolve referenced people/orgs |
| **Bank statement** (PDF + tables) | Docling + table extractor | TRANSACTION (per row) | Counterparty resolution across statements |
| **Invitation card** (image) | Gemini Flash VLM | (doc-as-Event record) | Event entity created/matched |
| **Employment agreement** (PDF) | Docling | CLAUSE (CUAD types) | Employee + Employer entity |
| **Plant design drawing** (PDF, image-heavy) | Docling text + ColPali images + VLM | COMPONENT (per labeled item) | Component-graph: feeds/connected_to |
| **Land record** (scanned form) | Mistral OCR 3 + form-field detection | (doc-as-Parcel) + HISTORY_ENTRY rows | Parcel entity + owner history edges |
| **ID xlsx** (5k+ rows × N cols) | openpyxl + pandas | ROW (per resident, typed by header schema) | Each row → canonical Person entity |

**The universal layers (L0, L1, L1a, L1d, L2, L4, L5, L6) are identical for all seven.** Only the L3 extractor and the optional schema projection differ.

### How a multi-doc query stitches them together

> Query: *"Did Aakash's vendor-review note flag the same parties his bank statement shows as flagged transactions?"*

The planner emits a multi-mode plan that touches *three* doc types simultaneously:

```
Modes: S (note scope) + C (Transaction units, rarity>0.9) +
       T (HippoRAG walk from Aakash) + E (entity resolve counterparties)

Channel ⑤ atomic-units:
  - In the note: action_item mentions of "vendor invoice review"
  - In the statement: transactions where rarity_score > 0.9
Channel ⑦ HippoRAG: PPR seeds [Aakash, vendor, invoice] →
  walks to flagged transactions + the note

Join: transactions.counterparty ∩ note.vendors_referenced
```

Answer cites both docs: the note for the instruction, the statement for the flagged transactions, the entity layer for the counterparty resolution.

**Same retrieval machinery; different doc types contribute different atomic-unit types as evidence.**

---

## 2. The 8 Storage Layers — Why So Many?

Each layer answers a different kind of question. Think of it as a stack of indices, each tuned for a different retrieval mode.

```
LAYER          STORES                          ANSWERS QUERIES LIKE
─────────────────────────────────────────────────────────────────────────
L0 RAW         file bytes (PDF, xlsx, …)      "show me the original PDF"
                                              (citation click-through)

L0.5 DOC       logical groupings over raw     "latest revision of drawing C7"
   CHAINS      files: email threads,          "resolution of email thread"
               contract+amendment chains,     "what was the original cap
               drawing revisions,              before amendment?"
               circulars+corrigenda,
               patient charts.

L1 PARSE       per-page text + layout         "what does page 7 say
                                              literally?"

L1a CTX CHUNK  contextualised chunks          "find chunks about indemnity"
               with embeddings + BM25         (HIGH RECALL hybrid search)

L1d RAPTOR     hierarchical summaries         "summarise this doc",
                                              "find docs about X" (vague
                                              queries hit summary level)

L2 MENTIONS    typed entity spans             "who/what is mentioned in
               (UNIVERSAL types)              this doc?", cross-doc entity
                                              navigation

L2b EMERGENT   per-doc proposed fields        "what fields does this doc
   FIELDS      in OPEN vocabulary +           have?", "what schema is
               cross-doc inferred             emerging for this doc type?"
               schema per doc-type            — the layer that makes
                                              schema-emerges-from-data
                                              honestly true

L3 ATOMIC      typed structured units per      "find indemnity caps >$10M"
   UNITS       doc type (clauses, txns,        "find unusual delivery clauses"
               components, rows,               "transactions over ₹3L to
               decisions, …)                    unknown counterparty"
               + parameters + RARITY           "rare component spec in P-101"

L4 ENTITIES    resolved canonical entities    "who is El Paso Electric?",
               + aliases                      "show all docs mentioning
                                              this entity"

L5 RELATIONS   typed edges with provenance    "who supplies to whom?",
                                              "show contracts between X
                                              and Y"

L6 HIPPORAG    PPR-ready entity graph         multi-hop reasoning:
                                              "vendors → contracts →
                                              clauses → events"

L7 COMMUNITY   (lazy, query time)             "summarise all our IP
                                              clauses across all
                                              contracts"
```

**Why this matters for the demo:** when a user asks any kind of question, we have a layer that's *right* for it. We don't force everything through "vector similarity on chunks" — that's the trap most demos fall into.

---

## 3. Where Everything Physically Lives

```
┌─────────────────────────────────────────────────────────────────┐
│                       POSTGRES 17 (one DB)                       │
│                                                                  │
│  ┌──── Extensions ────┐                                         │
│  │ pgvector 0.8       │ ← all dense embeddings (HNSW + halfvec) │
│  │ ParadeDB pg_search │ ← BM25 (Tantivy)                        │
│  │ (Apache AGE later) │ ← Cypher graph queries when needed      │
│  └────────────────────┘                                         │
│                                                                  │
│  Tables:                                                         │
│   schemas, schema_versions, schema_entities, schema_fields       │
│   files (+ source_authority, doc_status),                       │
│       file_lifecycle, jobs (Procrastinate)                      │
│   doc_chains, doc_chain_members      ← L0.5                     │
│   raw_pages                          ← L1 (immutable backbone)  │
│   chunks, contextual_chunks          ← L1a                      │
│   raptor_nodes, raptor_edges         ← L1d                      │
│   mentions, surface_forms            ← L2                       │
│   emergent_fields,                                              │
│       field_name_clusters,                                       │
│       inferred_schemas,                                          │
│       inferred_schema_fields,                                    │
│       schema_promotion_suggestions   ← L2b                      │
│   clauses, clause_types,                                         │
│       clause_parameters              ← L3                       │
│   entities, entity_aliases,                                      │
│       mention_to_entity              ← L4                       │
│   relationships,                                                 │
│       relationship_evidence          ← L5                       │
│   hipporag_edges, ppr_scores         ← L6                       │
│   extracted_entities,                                            │
│       citations (+ polymorphic envelope for                      │
│         pdf_span / xlsx_row / image_bbox / ocr_span /            │
│         email_message / raptor_summary / aggregate /             │
│         atomic_unit / entity_ref / chain_ref)  ← Design 5        │
│   fact_conflicts                     ← Design 2 (conflicts)     │
│   corrections, entity_overrides,                                 │
│       schema_field_overrides,                                    │
│       regression_set                 ← Design 4 (feedback)      │
│   artifacts (briefings, FAQs,                                    │
│       mind maps)                                                 │
│   audit_log, eval_runs, eval_judgments                          │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                       MinIO (S3-compatible)                      │
│                                                                  │
│   raw_files/         ← original bytes (L0), content-hash keyed  │
│   parse_artifacts/   ← layout JSON, tables JSON                 │
│   colpali_vectors/   ← page-image multi-vectors (Wave C)        │
│   generated/         ← briefings, mind maps, audio (later)      │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                  Procrastinate (Python PG-backed jobs)                    │
│                                                                  │
│   ingestion_jobs:  parse → contextualize → embed → extract →    │
│                    cluster → resolve → relate → graph_update    │
│   artifact_jobs:   briefing, FAQ, mind_map, audio (async)       │
└─────────────────────────────────────────────────────────────────┘
```

Everything that needs a transaction is in Postgres. The only thing outside Postgres is **large immutable blobs** (raw files, big parse artifacts). One DB. One backup. One thing to deploy.

---

## 4. Retrieval — A Single Query's Journey

User opens `/chat` and types:

> **"What's the indemnity cap in our license agreements?"**

```
T+0ms       Query enters /api/query
            ┌──────────────────────────────────────────────┐
            │ Session ID looked up; chat history loaded    │
            │ for follow-up context (this query is fresh)  │
            └──────────────────────────────────────────────┘

T+100ms     STEP 1 — Intent classifier (Gemini Flash)
            ┌──────────────────────────────────────────────┐
            │ Returns:                                     │
            │   intent: "field_lookup_with_filter"         │
            │   ambiguity: low                             │
            │   needs_multi_hop: no                        │
            │   probable_modes: [C, F, H]                  │
            └──────────────────────────────────────────────┘

T+500ms     STEP 2 — Query rewriting (gated by intent)
            ┌──────────────────────────────────────────────┐
            │ Original kept:                               │
            │   "What's the indemnity cap in our license   │
            │    agreements?"                              │
            │                                              │
            │ HyDE (1 hypothetical answer):                │
            │   "Indemnification cap shall not exceed $X   │
            │    million per claim. Aggregate liability    │
            │    under this Agreement is limited to $Y     │
            │    million..."                               │
            │                                              │
            │ Query2Doc keyword expansion:                 │
            │   original + "indemnity cap limitation of    │
            │    liability aggregate amount license"       │
            │                                              │
            │ Step-Back: skipped (query is concrete)       │
            └──────────────────────────────────────────────┘

T+800ms     STEP 3 — Planner emits inspectable JSON
            ┌──────────────────────────────────────────────┐
            │ {                                            │
            │   "modes": ["C", "F", "H"],                  │
            │   "C": {  ← clause-level filter              │
            │     "clause_type": "indemnification",        │
            │     "parameter": "cap"                       │
            │   },                                         │
            │   "F": {  ← schema filter                    │
            │     "entity_type": "Contract",               │
            │     "doc_subtype": "license_agreement"       │
            │   },                                         │
            │   "H": {  ← hybrid free-text                 │
            │     "queries": [<orig>, <HyDE>, <Q2D>]       │
            │   }                                          │
            │ }                                            │
            │                                              │
            │ This JSON is shown in the UI's inspector     │
            │ panel — radical transparency, no black box.  │
            └──────────────────────────────────────────────┘

T+1.5s      STEP 4 — Parallel retrieval (~10 channels)
            ┌──────────────────────────────────────────────┐
            │ All run concurrently, each returns top-200:  │
            │                                              │
            │ ① BM25 on contextual chunks                  │
            │ ② Dense on contextual chunks                 │
            │ ③ Dense on RAPTOR mid-level summaries        │
            │ ④ Dense on RAPTOR doc-level summaries        │
            │ ⑤ Clause-type filter (decisive here):         │
            │      WHERE clause_type='indemnification'     │
            │      AND parent_doc.subtype='license'        │
            │      → returns ~6 clauses across corpus      │
            │ ⑥ Anomaly filter: skipped                    │
            │ ⑦ HippoRAG PPR seeded by [indemnity, license]│
            │ ⑧ Mention table: surface ~"indemnity"        │
            │ ⑨ ColPali: skipped (no visual indicator)     │
            │ ⑩ Doc metadata filter: doc_subtype='license' │
            │                                              │
            │ Channel ⑤ returns the 6 actual clauses;      │
            │ channels ①②③④ return chunks containing       │
            │ those clauses; channels ⑦⑧⑩ reinforce.        │
            └──────────────────────────────────────────────┘

T+2.5s      STEP 5 — RRF fusion
            ┌──────────────────────────────────────────────┐
            │ Score(d) = Σ over channels of 1/(k + rank)   │
            │                                              │
            │ Documents that appear in many channels get    │
            │ boosted. The 6 license-agreement indemnity   │
            │ clauses naturally rank highest because they  │
            │ hit channels ② ③ ⑤ ⑦ ⑧ ⑩ simultaneously.    │
            │                                              │
            │ Output: unified top-200                      │
            └──────────────────────────────────────────────┘

T+3.0s      STEP 6 — Cross-encoder rerank → top-20
            ┌──────────────────────────────────────────────┐
            │ Cohere Rerank 3.5 reads (query, candidate)   │
            │ pairs and scores them jointly. This catches  │
            │ subtle relevance that RRF missed.            │
            │                                              │
            │ Top results:                                  │
            │   1. acme_techco.pdf::clause#12 (score 0.94) │
            │   2. enron_epe.pdf::clause#12 (score 0.92)   │
            │   3. contoso_dt.pdf::clause#10 (score 0.89)  │
            │   4. ...                                     │
            └──────────────────────────────────────────────┘

T+3.2s      STEP 7 — CRAG confidence gate
            ┌──────────────────────────────────────────────┐
            │ Top-1 score 0.94, top-5 agreement high       │
            │ → no escalation. Continue.                   │
            │                                              │
            │ (If top-1 were 0.5 with disagreement, we'd   │
            │ escalate to IRCoT — let Gemini reason and    │
            │ re-retrieve up to 4 hops.)                   │
            └──────────────────────────────────────────────┘

T+5.0s      STEP 8 — Astute RAG generation
            ┌──────────────────────────────────────────────┐
            │ Gemini Flash gets:                           │
            │   - Original query                           │
            │   - Top-20 reranked candidates with full     │
            │     context + clause parameters              │
            │   - System prompt: cite-or-refuse,           │
            │     surface disagreements                    │
            │                                              │
            │ Output:                                      │
            │ "Across the 4 license agreements in your     │
            │ corpus, indemnity caps vary:                 │
            │                                              │
            │ • Acme/TechCo: $10M per claim, $20M          │
            │   aggregate [¹]                              │
            │ • Enron/El Paso: $25M per event [²]          │
            │ • Contoso/DataTech: $5M per claim [³]        │
            │ • Initech/Globex: capped at 12-month fees [⁴]│
            │                                              │
            │ The Enron/EPE $25M cap is unusually high     │
            │ for our corpus (rarity score 0.91 — top 10%  │
            │ of indemnity caps in the data)."             │
            │                                              │
            │ Each [¹][²][³][⁴] points to citation IDs.     │
            └──────────────────────────────────────────────┘

T+5.5s      STEP 9 — Faithfulness gate
            ┌──────────────────────────────────────────────┐
            │ HHEM-2.1 scores each claim against its       │
            │ cited evidence. All 4 claims grounded → PASS │
            │                                              │
            │ (HalluGraph deferred to Wave C — would add   │
            │ KG-alignment check that the entities/dates/  │
            │ amounts match the source structure.)         │
            └──────────────────────────────────────────────┘

T+5.5s      STEP 10 — Audit log (immutable, append-only)
            ┌──────────────────────────────────────────────┐
            │ Writes:                                      │
            │   query, user, timestamp, session            │
            │   intent classification                      │
            │   rewrites used                              │
            │   planner JSON                               │
            │   channel scores + candidate IDs             │
            │   RRF + rerank scores                        │
            │   model + prompt + temperature + seed        │
            │   judge verdicts + thresholds                │
            │   final answer + citations                   │
            │                                              │
            │ This is what makes the system DEFENSIBLE in  │
            │ an audit. Every decision is reconstructable. │
            └──────────────────────────────────────────────┘

T+5.6s      STEP 11 — UI render
            ┌──────────────────────────────────────────────┐
            │ /chat page shows:                            │
            │                                              │
            │ ┌─ Chat area ──────────┐ ┌─ Citation cards ─┐│
            │ │ You: What's the      │ │ [¹] Acme/TechCo  ││
            │ │     indemnity cap?   │ │     Page 7        ││
            │ │                      │ │     "Indemnity   ││
            │ │ Assistant: Across    │ │      cap..."     ││
            │ │ the 4 license        │ │ ─────────────────││
            │ │ agreements...        │ │ [²] Enron/EPE    ││
            │ │ Acme [¹]... Enron[²].│ │     Page 12      ││
            │ │                      │ │     ...          ││
            │ │ ▶ How I answered     │ │ ─────────────────││
            │ │   (collapsed)        │ │ [³] Contoso/DT   ││
            │ └──────────────────────┘ └─────────────────┘│
            │                                              │
            │ Click any citation → opens the PDF at the    │
            │ right page with the clause highlighted.      │
            └──────────────────────────────────────────────┘
```

Total time: ~5.6 seconds. Most of it is the LLM generation step. Retrieval itself is <1 second.

---

## 5. The Two Pipelines, Side by Side

```
                  INGEST                              QUERY
       ─────────────────────────────       ──────────────────────────────
        Upload                              User types question
            ↓                                       ↓
        Hash + dedup                         Intent classify
            ↓                                       ↓
        Classify                             Rewrite (HyDE, Step-Back)
            ↓                                       ↓
        Parse (Docling/OCR)                  Plan (JSON modes)
            ↓                                       ↓
        Chunk                                Parallel retrieve (10 chans)
            ↓                                       ↓
        Contextualize                        RRF fuse
            ↓                                       ↓
        Embed + BM25 index                   Rerank
            ↓                                       ↓
        RAPTOR build                         CRAG gate
            ↓                                       ↓
        Open extract (mentions)              Generate (Astute RAG)
            ↓                                       ↓
        Atomic-unit extract + rarity         Faithfulness judge
            ↓                                       ↓
        Identity resolve                     Audit log
            ↓                                       ↓
        Relate                               Render w/ citations
            ↓
        Schema project (optional)
            ↓
        Artifact generate (async)
            ↓
        READY
```

The ingest pipeline **writes into the layers**. The query pipeline **reads from the layers**. The catalog principle in one line: *what ingest does for the corpus, retrieval undoes for the question*.

---

## 6. What Makes This Better Than "Naive RAG"

Naive RAG = chunk → embed → cosine-similarity search → stuff into LLM. It loses on:

| Failure mode | Why naive RAG fails | What our system does |
|---|---|---|
| Vague query, vocabulary mismatch | Embeddings don't bridge "party" ↔ "offsite" | HyDE rewrites the query into source-vocabulary |
| Vague query, abstract level | Chunk embeddings are too local | RAPTOR summary levels match abstract queries |
| Rare-unit query (clause, txn, component…) | Cosine sim swamped by typical units | Atomic-unit extraction + per-type rarity scoring (L3) |
| Multi-hop reasoning | One retrieval pass can't traverse | HippoRAG PPR graph + IRCoT escalation |
| Schema change | Re-extract everything | Re-extract only the schema layer; parse stays |
| "No answer in corpus" | LLM hallucinates anyway | Astute RAG refusal + HHEM gate |
| Government audit | Black box | Audit log + plan JSON + citation spans |

Each row maps to a paper + a layer in our system. None of this is novel; all of it is necessary.

---

## 7. Things You Should Push Back On Before We Build

These are the assumptions worth stress-testing:

1. **"Per-clause anomaly score is corpus-relative."** Right for "unusual *for us*" queries, wrong if the user wants industry comparison. Configurable.
2. **"Open extraction runs on every doc."** Doubles extraction cost vs schema-only. Justified by needle-query recall; want to confirm we're OK paying for it.
3. **"RAPTOR builds at ingest, not query time."** Cheaper than LazyGraphRAG style, but means schema changes don't refresh summaries. Live with it for MVP.
4. **"Postgres for everything."** Caps us at ~50M chunks before vectors need graduation. Fine for demo + writeup discussion.
5. **"Identity resolution uses LLM judge."** Costs scale O(n × k) where k is blocking candidates. ~$500 at 100K-doc scale; cheap.

---

## 8. Suggested Next Discussion Topics

Pick one and we'll go deeper:

- **Identity resolution mechanics** — how does the LLM judge decide "Mukesh Ambani" = "M. Ambani" reliably?
- **Schema versioning** — what happens when the user renames a field, deletes a field, splits a type?
- **The rerank step** — why cross-encoder beats LLM-as-reranker on cost AND quality.
- **The audit log shape** — exactly what gets written and how it satisfies government-style review.
- **Failure modes & graceful degradation** — when each layer is wrong, what happens?
- **Scale story** — what changes at 10M docs? At 100M?
