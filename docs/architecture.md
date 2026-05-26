# Emerging KB — Locked Architecture Design

**Date:** 2026-05-20
**Status:** Locked design, awaiting user approval before Phase 0 implementation
**Audience:** the engineer or production reviewer assessing this design

---

## 0. The Frame

Every era of knowledge systems — oral tradition, Library of Alexandria, Dewey Decimal, relational, semantic web, property graphs, vector embeddings, RAG, GraphRAG — has added invariants and tried to replace the prior stack. Every era has painfully relearned that the prior invariants were not optional.

**Our architecture is not novel. It is *correct*.** It honors the nine invariants that every successful knowledge system since the Rigveda has required: provenance, identity, hierarchy, cross-reference, versioning, vocabulary, scope, multiple access paths, confidence — plus compositionality and corrigibility.

The 2022–2024 "RAG everything" wave forgot most of these. The 2024–2026 counter-reformation (GraphRAG, RAPTOR, Contextual Retrieval, HippoRAG, Astute RAG) is the industry re-discovering them under new names. We will use these new techniques where they help, but our framing is older than arxiv.

---

## 1. The Two Realities That Drive the Architecture

### Reality A: The user cannot enumerate the schema upfront

For a corpus like "100K random Reliance docs" — contracts, invoices, salary slips, HR onboarding, business proposals, plant maintenance plans, board minutes, MoUs, NDAs, financial filings, audits, technical drawings, news clippings, random PPTs, scanned letters, vendor lists — the user does not know on day one what entity types exist. They *discover* what matters by exploring.

**Implication:** the system must work *without* a schema, then improve as schema is added. Schema is a *projection* on top of richer underlying layers, not the substrate.

### Reality A.1: Schema-Emerges-From-Data Principle

This is the conceptual headline of the whole architecture.

> The system's understanding of documents is **dynamic**.
> Schemas and connections are **discovered as data arrives**, not declared upfront.
> The user can run real queries on day one with zero schema defined.

Concretely:

1. **Ingest first, classify later.** A doc enters L0–L1 (raw + parsed) before any schema interpretation.
2. **Open extraction runs always — at two levels.**
   - **L2 (entity mentions)** uses a generic universal type list (PERSON, ORG, MONEY, DATE, LOCATION, …) for cross-doc entity navigation — "find all docs mentioning Mukesh Ambani."
   - **L2b (emergent fields)** is *bottom-up*: every doc proposes its own structured fields in *its own* vocabulary. No fixed type list. A cardiac procedure note proposes `stent_type`, `placement_artery`, `complications`; an invoice proposes `vendor_gstin`, `line_items`, `due_date`. The system clusters these proposals across similar docs and induces an emergent schema per doc-type.
3. **Topics, clusters, summaries auto-organize the corpus.** RAPTOR builds the hierarchy of what's there. The user explores discovered themes — they don't have to know the structure first.
4. **Schema is a projection.** When the user says "I now care about Vendor with field gst_number", the system runs schema-driven extraction *over the existing L1/L2/L2b layers*. No re-parse. No re-embed. If the field was *already* being captured at L2b (it usually is), promotion is near-instant.
5. **Schema can evolve continuously.** Add a field, add a type, rename a type — only the schema-extraction layer reruns (cheap). Everything else is preserved. Versioned.
6. **Discovered fields and entities are surfaced for promotion.** When L2b's inferred schema for a doc-type stabilizes (prevalence + value-type consistency above threshold), `/schema` shows: *"I've seen these 9 fields across 47 cardiac reports. Promote to typed schema?"* User clicks accept → typed.

This is why the architecture has **10 main resolution layers** (L0, L0.5, L1, L2, L2b, L3, L4, L5, L6, L7) plus the L1a/L1b/L1c/L1d chunk-and-summary sub-layers, not 1. The schema layer is the *user's view* on top of the system's own (richer) representation. The system always knows more than the schema requires.

### Reality A.2: How emergent schema actually works (the L2b pipeline)

Per-doc, three stages run at ingest:

**Stage A — Per-doc open field extraction.** Gemini Flash with an open-vocabulary prompt:
*"Read this document. Identify the structured information it contains. For each piece, propose: `field_name` (snake_case), `value`, one-line `description`, and a one-line `doc_type_label` for the document. Output JSON."*

The output for a single cardiac cath report (**teaching example — NOT in the actual demo corpus**, which is CUAD + Enron + SEC. Used here because the field names are intuitively recognizable. The same pipeline runs on power-supply agreements, emails, and 10-K filings in the demo, just with domain-specific fields like `indemnity_cap_usd`, `governing_law`, `term_years`, `signer`, etc.):
```json
{
  "doc_type_proposal": "cardiac_catheterization_report",
  "doc_type_confidence": 0.91,
  "fields": [
    {"name": "procedure_date",  "value": "2024-03-15", "description": "..."},
    {"name": "stent_type",      "value": "drug-eluting", "description": "..."},
    {"name": "placement_artery", "value": "LAD", "description": "..."},
    ...
  ]
}
```

**Stage A.5 — Per-field value verification (anti-hallucination).** Open-vocabulary LLM extraction is prone to plausible-but-fabricated fields (Gemini proposing `{name: "termination_clause_count", value: "3"}` when "3" appears nowhere in the doc, or `{name: "effective_date", value: "2024-01-01"}` extrapolated from context). Before any field enters `emergent_fields`:

1. For each proposed `(field_name, value)`, check whether `value` appears in the doc's raw text (exact match OR close-paraphrase within edit-distance threshold; normalize dates/numbers/money — "$25M" vs "25 million" vs "$25,000,000" all resolve to one canonical form).
2. If found → keep, attach `provenance_span = (page, char_offsets)` for citation grounding.
3. If not found → drop the field with logged reason `value_not_grounded`; never enters cross-doc clustering or schema induction.

Cost: ~$0.0001/doc additional (mostly string match; one Flash verification call only for ambiguous paraphrase cases). Eliminates ~90% of hallucinations.

**Stage B — Cross-doc field clustering.** Each proposed (and Stage-A.5-verified) field is stored with name + description + value embeddings in the `emergent_fields` table. As docs of the same `doc_type_proposal` accumulate:

- Cluster fields by `(name_embedding, description_embedding)` — `surgeon` / `primary_surgeon` / `physician_of_record` cluster into one canonical field. Same machinery as L4 identity resolution (embedding-block → LLM-judge → union-find).
- Track frequency: field present in X% of docs of this type.
- Track value distribution: enum (≤K distinct values), date, number, free text. The field's *type* is induced from its values.

**Stage C — Schema induction + auto-promotion.** After N (default 20) docs of a `doc_type_proposed`, an `inferred_schema` row exists per (doc_type, schema_version):

```
CardiacCathReport (inferred from 47 docs, stability 0.87)
  procedure_date       date    100%
  stent_type           enum     94%   {drug-eluting, bare-metal, bioabsorbable}
  placement_artery     text     96%   common: LAD, RCA, LCX
  complications        text    100%   mostly "none"
  primary_surgeon      text    100%
  patient_id           text    100%
  contrast_volume_ml   number   68%
  fluoroscopy_time_min number   62%
```

**Promotion is automatic. No user click required.** The system uses three tiers, each decided by the system, audit-logged, and editable after the fact. **All threshold values below are *configurable defaults* — sensible starting points, not research-validated values. Per-workspace tunable. See `docs/citations_audit.md` §2.3.**

| Tier | Triggers (defaults — configurable) | What happens |
|---|---|---|
| **High** | prevalence ≥ 80% AND stability ≥ 0.9 AND value-type conf ≥ 0.9 AND n_docs ≥ `min_doc_count` | **Auto-promote silently.** Field becomes first-class typed schema. Audit log records threshold values at promotion. `/schema` shows a "recently auto-promoted" badge for 7 days. `min_doc_count` is **scale-aware via Hydra (Design 9)**: production default = 20; demo / small-corpus default = 5. Override per workspace if needed. |
| **Medium** | below either prevalence or stability threshold but stable enough to display | Listed in `/schema` as "inferred, not yet promoted". **Queryable via `emergent_fields` exactly like typed fields — below-threshold doesn't mean inaccessible, just not promoted to the *typed* surface.** Auto-promotes itself once more evidence lands. User can promote sooner with one click if they want. Real optional fields (`fluoroscopy_time_min` at ~65%, `gst_number` at ~70%, `arbitration_clause` at ~55%) live here intentionally — the 80% threshold is a *confidence signal* for the typed surface, not an accessibility gate. |
| **Low** | below display threshold | Stays in `emergent_fields`. Queryable via "show all emergent fields" drawer. Doesn't clutter `/schema`. |

**Two cases surface for disambiguation — not for confirmation:**

1. **Naming collision** with existing typed-schema field of differing semantics → marked "collision — resolve" rather than silently overwriting. User can rename, merge, or ignore.
2. **Ambiguous inferred type** (e.g., 15 distinct values — enum or open text?) → default to text (lossless), with an inline "consider enum?" option.

From the moment of promotion, schema-driven extraction (step 18 in §5) also runs, alongside open extraction, providing typed validation and faster query plans. Auto-promoted fields are fully editable, renameable, and reversible.

**Honest framing:** schema emerges *as the corpus grows*. Day 1 (1 doc) you have one doc's proposed fields. Day 20 of a type, you have a wobbly inferred schema. Day 100, stable. Emergent fields are queryable from day one (*"what fields does this doc have?"*); promotion suggestions appear once evidence accumulates.

**The five honest gotchas:**

1. Per-doc cost rises (+2–3× on this step) — open prompt is less constrained. Absorbed into the §13 cost envelope.
2. Field-name clustering is the L4 identity-resolution pattern re-used.
3. Rare-field signal vs. noise: prevalence threshold gates promotion (queryable below threshold; surfaced as suggestion above).
4. Doc-type classifier feedback loop: doc-type *proposals* are themselves clustered; classifier disagreements surface for review.
5. Schema-version churn is bounded — promotion only re-suggested on meaningful prevalence or stability changes, not every doc.

### Reality A.3: Workspace and domain — what these terms mean

We use **`workspace_id`** and **`domain_id`** throughout the schema. They are two different scopes:

| Scope | What it is | Cardinality |
|---|---|---|
| **Workspace** | The tenant / deployment boundary. One workspace = one organization's KB instance. All data (files, entities, chat sessions, audit log) carries `workspace_id`. | One per customer / deployment. For the demo: `workspace_id='default'`. |
| **Domain** | The schema scope *within* a workspace. A domain has its own typed schema, vocabulary, and L3 atomic-unit definitions. | One workspace can have multiple active domains (e.g., "legal_contracts" + "corporate_email" + "financial_filings"). Each file is tagged with its `domain_id` so the right schema applies. |

Examples:
- Demo: workspace=`default`, domains=`{legal_contracts, corporate_email, financial_filings}` — all three active on the mixed CUAD+Enron+SEC corpus. Schema-swap demo (Moment 1) is *switching the active domain on the same docs*.
- Production at "Acme Bank": workspace=`acme_bank`, domains=`{retail_loans, corporate_credit, compliance}`. Each doc tagged with its domain at ingest.
- Multi-tenant SaaS: many workspaces, each with their own domains. Cross-workspace isolation enforced by `workspace_id` in every query (RLS or app-level). **Wave C — out of MVP scope.**

This terminology is now consistent across all docs. Earlier drafts used the two terms loosely; this section is the canonical definition.

### Reality B: A serious production reviewer will throw needle-in-haystack queries with vocabulary mismatch and ambiguity

The eval set is biased toward **tricky and ambiguous** questions, not factoids. See §9.

Two test cases I refuse to fail:

1. *"What issues have we had with foundation work?"* — answer is in one internal note that uses different words ("vendor failed to deliver concrete", "three-day halt", "QC poor").
2. *"We had a party in the last 2–3 years where we needed someone to supply something very fast — who was the supplier and which party was it?"* — answer is in **one** contract among thousands, with mostly boilerplate clauses plus one unusual "deliver within 4 hours" clause.

These are not extreme cases; they are the cases that distinguish a system from a demo. The Stanford RegLab study (2024) found that Lexis+ AI hallucinated on 17% of legal queries and Westlaw AI-Assisted Research on 33% — both production legal RAG systems. That is the bar.

---

## 2. Multi-Resolution Knowledge Representation

Every document is stored at **ten resolution levels simultaneously** (plus L1 sub-layers a/b/c/d). Each level has its own retrieval path.

```
L0   RAW                    Files (PDF, xlsx, ppt, scan, email, image)
                            Immutable. Content-hash keyed. Object store.

L0.5 DOC CHAINS             Logical groupings over raw files: email threads,
                            contract+amendment chains, drawing revisions,
                            circulars+corrigenda, patient charts. Detected at
                            ingest via headers (In-Reply-To), title similarity,
                            explicit "amends/supersedes" language. Each chain
                            has ordering + a `current_version_id` pointer.
                            Resolves "latest revision" and amendment-supersession
                            queries; gives "before/after" temporal semantics for
                            free. Design: docs/gaps_design.md §Design 3.

L1   PARSE                  Pages, chunks (~2–4K tokens), tables, layout, bboxes,
                            OCR confidences. Schema-INDEPENDENT and expensive
                            (Docling / Mistral OCR / VLM). Parse once, never redo.

L1a  CONTEXTUAL CHUNKS      Anthropic-style: each chunk gets a 50–100 token
                            LLM-generated context blurb prepended before embedding
                            and BM25 indexing.
                            Reference: Anthropic Sep 2024 — 67% retrieval failure
                            reduction (with reranking).

L1b  LATE-CHUNK EMBEDDINGS  Jina-style: encode full doc with long-context model,
                            then pool chunk-level embeddings from token outputs.
                            +2.70–3.63% over naive chunking (Jina paper).
                            **Considered but NOT primary** — L1a (Contextual
                            Retrieval) attacks the same problem (chunk
                            embedding loses doc context) with a 20× larger
                            documented effect (Anthropic: 67% failure
                            reduction with rerank). L1b is mentioned as an
                            alternative architecture; we run L1a only in MVP.
                            Re-enabling L1b in parallel adds marginal recall
                            at additional embedding-compute cost — Wave C
                            consideration.
                            Reference: arXiv 2409.04701.

L1c  COLPALI MULTI-VECTOR   For visual-heavy pages (scans, layouts, tables, PPTs):
                            multi-vector page-image embeddings, ColBERT-style.
                            Skips OCR entirely.
                            Reference: arXiv 2407.01449.

L1d  RAPTOR TREE            Recursive hierarchical cluster + summarize over L1a
                            chunks. Every level is independently retrievable:
                              - leaf: chunks themselves
                              - mid: section/topic cluster summaries
                              - top: doc-level summary card (the "catalog card")
                              - apex: corpus-level theme clusters
                            Vague queries match abstract levels; drill to chunks
                            for citation.
                            Reference: RAPTOR, ICLR 2024, arXiv 2401.18059.

L2   MENTIONS               Open extraction with a UNIVERSAL type list — PERSON, ORG,
                            MONEY, DATE, LOCATION, PROJECT, FACILITY, REGULATION,
                            PRODUCT, EVENT, ACTIVITY, … — with (surface_form, type,
                            doc, page, char_span, ner_confidence). Purpose:
                            cross-doc entity navigation ("all docs mentioning
                            Mukesh Ambani"). Schema-LIGHT, runs on EVERY doc.

L2b  EMERGENT FIELDS        Bottom-up open-vocabulary field extraction. For each
                            doc, Gemini proposes (field_name, value, description,
                            doc_type_label) in the DOC's own vocabulary — no fixed
                            type list. Stored in `emergent_fields` with name +
                            description + value embeddings. Cross-doc clustering
                            (Stage B in §1) induces a per-doc-type emergent schema
                            with prevalence + value-type per field. After
                            stability + prevalence thresholds, `/schema` surfaces
                            it as a promotion suggestion.
                            This is the layer that makes "schema emerges from
                            data" honestly true.

L3   ATOMIC UNITS          Doc-type-specific typed structured units extracted
                            from the doc. Every unit has: type, parameters
                            (jsonb), citation coordinates, rarity_score against
                            corpus-wide same-type units.

                            "Clause" is one example (for contracts); the full
                            taxonomy is doc-type-driven:

                              CONTRACT / AGREEMENT        → CLAUSE
                              EMPLOYMENT LETTER           → CLAUSE
                              BANK STATEMENT              → TRANSACTION
                              INVOICE                     → LINE_ITEM
                              INVITATION CARD             → (doc-as-Event record)
                              PLANT DESIGN DRAWING        → COMPONENT
                              LAND RECORD                 → (doc-as-Parcel)
                                                           + HISTORY_ENTRY
                              ID / RESIDENT SPREADSHEET   → ROW (per resident)
                              MEETING MINUTES             → DECISION / ACTION_ITEM
                              POLICY DOCUMENT             → PROVISION
                              EMAIL                       → MESSAGE_SEGMENT
                              COURT FILING                → HOLDING / ARGUMENT
                              PATENT                      → CLAIM
                              RESUME                      → EXPERIENCE_ENTRY
                              HANDWRITTEN NOTE / FREEFORM → (none — L2 only)

                            The L3 extractor for a doc type is a plug-in:
                            register a new doc type by registering a
                            (type_classifier, atomic_unit_extractor,
                            parameter_schema, rarity_definition) tuple.

                            Clause typing for legal: CUAD taxonomy (41 types)
                            or LEDGAR (12K labels).
                            References: CUAD arXiv 2103.06268, LEDGAR LREC 2020.

L4   ENTITIES               Identity-resolved canonical entities. Merged from L2
                            mentions via deterministic-keys → embedding blocking
                            → LLM-judge → union-find clustering.
                            Type may be schema-bound (Vendor, Contract) or "open"
                            (auto-discovered).

L5   RELATIONSHIPS          Typed edges between entities. (subj, predicate, obj,
                            evidence_doc, evidence_span, confidence). Predicates
                            typed by schema where known, free-text where not.

L6   HIPPORAG GRAPH         Entity-relation graph indexed for Personalized
                            PageRank seeded by query entities. Multi-hop in one
                            retrieval step, 10–30× cheaper than iterative.
                            Reference: HippoRAG 2, arXiv 2502.14802.

L7   COMMUNITIES (LAZY)     Derived at query time only — LazyGraphRAG pattern.
                            Community detection + map-reduce summarization, for
                            global/thematic queries. Cached per session.
                            Reference: LazyGraphRAG, Microsoft Research, Nov 2024.
```

**Schema-driven extraction does not replace these layers — it *projects* the user's defined types over L2/L2b/L3/L4/L5.** When the user adds the type `Vendor` with field `gst_number`, the system first checks whether `gst_number` is already being captured at L2b (usually yes — emergent fields will have surfaced it across vendor docs). If so, promotion is near-instant (relabel, no re-extract). Otherwise, the system re-runs *only* schema-driven extraction (Gemini 2.5 Flash with JSON-schema constrained outputs) against L1 parsed pages. **No re-parsing, no re-embedding, no re-mentioning.** That is the demo-grade architectural payoff.

**Re-extraction trigger cascade** — what user actions cascade to what re-extraction, and how concurrent jobs are handled:

| User action | What re-extracts | Concurrent-job handling |
|---|---|---|
| Manual promote one field (e.g., `arbitration_clause` in `CommercialContract`) | All docs of that doc-type only (e.g., 3,247 CommercialContracts) — sets `files.lifecycle_state='reextracting'`, emits SSE `file.reextracting → file.ready` events | Re-extract job queued *after* any in-flight ingest job for the same doc (Procrastinate priority-aware) |
| Field rename | Same as promote — re-extract affected doc-type | Same |
| Field delete | No re-extraction — removes field from typed schema; data preserved in `emergent_fields` | n/a |
| Domain swap on workspace | Schema-driven re-extraction on all docs in workspace (~$15 + ~15 min at 5K-doc scale; hours at 100K) | **Swap queued behind in-flight jobs.** UI surfaces *"Schema swap queued — will run after 153 in-flight docs finish (ETA 12 min)."* Without queueing, partial-corpus extraction state corrupts. |
| Concurrent admins edit schema | Optimistic locking via `schema_versions.updated_at`; conflict surfaces in `/schema` as *"Another admin updated this 30s ago — review changes?"* with diff view; both edits audit-logged | Last-writer-wins after explicit resolution |

---

## 2.5 Demo Corpus — Real Public Datasets (no domain lock-in)

The system is domain-agnostic; we prove it by demoing on **two genuinely different domains in one mixed corpus**, drawn entirely from public datasets. This lets us perform the schema-swap demo live on real data and removes "you cherry-picked the domain" as a reasonable objection.

| Source | Public dataset | What it gives the demo | License |
|---|---|---|---|
| **Legal contracts** | **CUAD** (Contract Understanding Atticus Dataset) — 510 real commercial contracts annotated with 41 clause types. Hosted at [github.com/TheAtticusProject/cuad](https://github.com/TheAtticusProject/cuad) | Clause-level extraction, anomaly scoring (rare clauses), identity resolution (same parties across contracts), hierarchical schema (File → Contract → Party → Clause) | CC BY 4.0 |
| **Corporate emails** | **Enron Email Corpus** — ~500K real emails from 150 Enron employees. Hosted at [cs.cmu.edu/~enron](https://www.cs.cmu.edu/~enron/) | Vague-query needles, identity resolution across aliases/accounts, conversation threading, casual mentions of events/projects | Public domain |
| **Financial filings** | **SEC EDGAR 10-K** — long-form annual reports for select public companies (we pick 3–5). [sec.gov/edgar](https://www.sec.gov/edgar) | Long-doc handling (100+ pages), structured-extraction over financial sections, identity resolution across yearly filings | Public domain |
| **Scanned variants** | Print-and-rescan ~10 of the above to OCR-quality scans | Exercises Mistral OCR 3 + the OCR-fallback path | — |
| **Spreadsheet** | A handcrafted vendor-list xlsx derived from CUAD parties | Tests the xlsx parser; gives a structured table to join against contracts | — |

**Total seed corpus: ~80–100 documents** across 5 file types, 3 domains. Above the 10-doc assignment minimum; small enough to fully demo + audit; large enough to demonstrate cross-doc identity resolution and topic clustering.

### Why this works for the assignment

- A reviewer can pick **any** of the three domains and ask questions; the system answers.
- Mid-demo we **swap the domain schema** from `legal_contracts` → `email_correspondence` → `financial_filings` on the *same* uploaded data, no re-parse.
- The Enron emails are where vague-query needles hide (the "party-and-fast-delivery" style edge case maps cleanly to Enron's many casual references to events, vendors, deals).
- CUAD gives us a public, annotated ground truth for clause extraction — we can show numeric extraction quality (precision/recall on clause types) in the eval, not just hand-waved demo answers.
- SEC 10-Ks give us long docs and a domain where hallucination is regulated — useful framing for the "we refuse to answer when evidence is weak" demo moment.

### Eval question source

Eval questions are drawn from a mix of:
- **Hand-crafted needle queries** — single doc in corpus, vocabulary mismatch (e.g., the "foundation issues" and "party / fast delivery" style).
- **LegalBench-RAG** ([hazyresearch.stanford.edu/legalbench](https://hazyresearch.stanford.edu/legalbench/)) — public legal Q&A pairs we can run against CUAD.
- **CUAD ground-truth clause extractions** — we can ask "find me the indemnification cap in contract X" and grade against the annotation.
- **MuSiQue / HotpotQA-style** multi-hop questions adapted to our corpus.
- **Adversarial / false-premise** — handcrafted, TruthfulQA-style.
- **Negative queries** — questions where the right answer is "no such fact in the corpus".

---

## 3. The Catalog Principle

Libraries did not store books — they cataloged them. The Pinakes at Alexandria (~250 BCE) wasn't the library; it was a map *of* the library. A book without a catalog entry was effectively invisible no matter how good the shelves were.

NotebookLM (Google, late 2025–2026) explicitly markets this as **"source grounding, not RAG"** — Steven Johnson on the Hard Fork podcast, July 2025. The cataloged artifacts (briefing doc, FAQ, mind map, suggested questions, audio overview) are first-class product surfaces, not retrieval scaffolding.

Mapping library practice to our layers:

| Library practice (since 250 BCE) | Our system layer |
|---|---|
| Book on the shelf, call number | L0 raw, content-hash keyed in object store |
| Catalog card: title, author, subject headings, abstract | L1d RAPTOR doc-level summary node |
| LCSH controlled vocabulary | Normalized concept tags on cards (LLM rewrites surface forms) |
| Back-of-book index | Reverse concept→doc index derived from L2 mentions |
| Dewey topic shelves | RAPTOR top-level theme clusters |
| Bibliography / "see also" | L5 relationships + topic-to-topic edges |
| Browse / filter | Filter by classified doc_type, date range, topic |
| Card catalog search → fetch book → read | Multi-resolution retrieval: card matches first, drill to chunks, cite spans |

The catalog layer is what catches vague queries. The chunks are what citations point to.

---

## 4. The Atomic-Units and Anomaly Layer (generalised from "clauses")

For any doc that has *internally typed structured units* — clauses in a contract, transactions in a bank statement, components in a drawing, rows in a spreadsheet — L3 extracts those units as first-class typed records.

### The plug-in shape

Each doc type registers an L3 extractor:
```
(
  type_classifier,        // is this a Contract? a BankStatement? a LandRecord?
  atomic_unit_extractor,  // LLM/heuristic that yields typed units
  parameter_schema,       // jsonb shape per unit-type
  rarity_definition       // how to compute rarity vs corpus
)
```

This means adding a new doc type — say, *court_filing* with atomic unit *holding* — requires writing 4 plug-ins, no core changes.

### Examples

| Doc type | Atomic unit | Parameter shape | Anomaly criterion |
|---|---|---|---|
| Contract / Agreement | Clause | type, scope, cap, term, jurisdiction | corpus-relative param outliers |
| Employment letter | Clause | non_compete_months, notice_days, ctc | unusual notice / non-compete |
| Bank statement | Transaction | date, amount, counterparty, type | amount outliers, unknown party |
| Invoice | Line item | item, qty, unit_price, total, tax | unusual unit_price |
| Invitation card | (doc-as-Event) | event, date, venue, hosts | — |
| Plant drawing | Component | tag, type, material, spec, position | rare spec / material |
| Land record | (doc-as-Parcel) + History entries | id, owner, area, district + transfer history | high-frequency owner change, area outlier |
| ID xlsx (rows) | Row | name, dob, address, id#, ward | duplicate IDs, impossible DOBs |
| Email | Message | sender, recipient, subject, body | content/sender mismatch in thread |
| Meeting minutes | Decision / Action_Item | who, what, owner, due_date | overdue, owner unclear |
| Handwritten note | (none — L2 mentions only) | — | — |

### Rarity score (universal across unit types)

For each unit type, compute a corpus-wide centroid + variance over unit embeddings + parameter distributions (Isolation Forest, distance-from-centroid, TF-IDF rarity). Every unit gets a `rarity_score ∈ [0,1]`.

This is what makes both "deliver within 4 hours" (rare clause) **and** "₹3 lakh outflow to unknown party" (rare transaction) findable by the same anomaly machinery.

References:
- arXiv 2411.17495 (life insurance contract anomaly)
- CEUR-WS Vol-2369 (public procurement anomaly)
- Ironclad: 173 OOTB clause types, anomaly detection at 1B-contract scale

---

## 5. Indexing Pipeline (offline + incremental)

```
1.  Upload (resumable multipart)
       ↓
2.  SHA-256 dedup at receive — link to existing if seen
       ↓
3.  Classify document type:
       - Format detection (PDF vs xlsx vs jpg vs eml): magic bytes +
         extension, ~1ms
       - Subtype label (contract vs email vs 10-K vs scan vs note):
         Gemini Flash with first-page text → label, ~200–500ms.
         (A trained small classifier — DistilBERT-tiny fine-tuned on
         our doc-type taxonomy — would be ~50ms; we use Flash for the
         demo since the cost is trivial per ingest and the accuracy
         is higher; a small classifier is Wave B optimization.)
       ↓
4.  Route parser:
       ├─ Docling                (digital PDF: layout, tables, bbox)
       ├─ Mistral OCR 3          (scanned PDF, multilingual, handwriting)
       ├─ openpyxl + pandas      (xlsx)
       ├─ python-pptx + OCR      (pptx)
       ├─ email parser           (header + body + attachments)
       └─ Gemini 2.5 Flash VLM   (image-only PDF, very poor OCR)
       ↓
5.  raw_pages table — IMMUTABLE, content-hash keyed
       ↓
5.5 Doc-chain detection (cheap, ~$0.001/doc):
       - emails: parse In-Reply-To / References headers; subject normalize
       - contracts: title similarity + "amends/supersedes" language
       - drawings: filename + revision tag + project metadata
       - circulars: explicit "Corrigendum to ..." header
       - patient charts: same patient_id (L4) across encounters
       LLM-judge on borderline cases. Insert doc_chain_members rows;
       update parent chain's current_version_id when amendment detected.
       ↓
6.  Late chunking (BGE-M3 / Gemini Embedding 001) → chunks (~2–4K tokens, layout-aware)
       ↓
7.  Anthropic Contextual Retrieval prefix
       (LLM generates 50–100 token "this is from X about Y" header per chunk;
        prompt-cache the doc-level context for ~$1/M src tokens)
       ↓
8.  Embed contextualized chunks (Gemini Embedding 001) + index in pgvector HNSW
       ↓
9.  BM25 index contextualized chunks via ParadeDB pg_search (Tantivy)
       ↓
10. RAPTOR tree build: cluster chunks → summarize cluster → embed summary
       → cluster summaries → re-summarize → … (recursive)
       — every node embedded + BM25 indexed
       ↓
11. ColPali pass for visual-heavy pages (parallel, only for those pages)
       ↓
12. Mention extraction (Gemini 2.5 Flash, universal-type-list open extraction)
       → mentions table — generic types for cross-doc entity navigation
       ↓
12b-c-d-14. KV+TABLES extraction (Gemini Flash, ONE structured-output call)
       This single call collapses what used to be four separate LLM phases
       (classify, field-propose, atomic-units plugin, schema-driven extract).
       The response shape:
           {
             doc_type: "<snake_case>",
             scalars: [{name, description, value, value_type, is_pii,
                        source_chunk, ...}],
             tables:  [{name, description, cardinality, columns,
                        rows: [{values, source_chunk, source_char_*}]}]
           }
       Worker fans this out in one transaction:
         — doc_type   → files.inferred_doc_type
         — scalars[]  → proposed_fields (doc-level structured fields, with
                        PII flags for SSN/Aadhaar/PAN/DOB/phone/email/etc.)
         — tables[].rows[] → atomic_units (one row per typed unit:
                        transaction / clause / line_item / message / row)
       Then deterministic post-processing (no LLM):
         — Cross-doc clustering of proposed_fields → inferred_schema_fields
           (name+description embedding blocking, value-type induction).
         — Auto-promotion when prevalence ≥ 80% ∧ stability ≥ 0.9 ∧
           value-type conf ≥ 0.9 ∧ n_docs ≥ min_doc_count → schema_fields
           with `auto_promoted=true`.
         — Vocabulary discovery (Design 6) emits candidate synonym entries
           into `domain_vocabulary` when sibling clusters land at
           name-embedding similarity ≥ 0.85 across ≥ 5 docs.
         — Anomaly/rarity scoring on atomic_units (per-unit_type cohort).
       At render time, fields with is_pii=true display as "[PII: <type>]"
       placeholder unless workspace policy explicitly allows full display
       (full encryption + permissions-gated decryption is Wave C).
       ↓
13. Open triple extraction (light OpenIE) → temp_triples
       ↓
15. Identity resolution
       (deterministic keys → embedding blocking → LLM-judge → union-find clusters)
       → entities table, mention_to_entity link
       ↓
16. Relationship layer:
       triples + clause-party-bindings → resolve args to entity IDs
       → relationships table (typed, evidenced, confidence-scored)
       ↓
17. HippoRAG-2 graph build:
       entity + relation graph with PPR-ready edge weights
       ↓
18. Schema-driven entity instantiation + nested-entity promotion (Design 7)
       — Phase 1.5 BOOTSTRAP: for each (doc_type) seen, ensure a doc_root
         schema_entity (e.g. BankStatement) exists, plus a sub_entity type
         per distinct atomic_units.unit_type for the file (Transaction,
         Clause, LineItem, …) with parent_type_id set + a
         schema_relationships(kind='contains') edge linking root → child.
       — PASS 1: for every active doc_root schema_entity matching the
         file's inferred_doc_type, run a Gemini structured-output extract
         per entity using its promoted schema_fields. Produces parent
         extracted_entities (rows where parent_entity_id IS NULL) with
         per-field citations to contextual_chunks.
       — PASS 1.5: promote every atomic_units row for the file to a
         child extracted_entity under the matching sub_entity type.
         `parameters` jsonb becomes `fields`; rarity_score + unit_type +
         source positions copy over (chunks.id → contextual_chunks.id
         translation handles the differing FK targets).
       — PASS 2/3: topologically sort by depth, then assign each
         entity its `lineage_path` (ltree) and `parent_entity_id` by
         walking schema_relationships(kind='contains'). The full ancestor
         chain populates (e.g.
           workspace.project.client.case.contract.clause).
       — Citations record lineage_path_at_cite_time (snapshot).
       ↓
19. NotebookLM-style artifact generation (async, per workspace):
       - briefing doc        (Gemini multi-stage: outline → critique → revise)
       - FAQ
       - mind map            (entity/concept hierarchical render)
       - suggested questions
       - audio overview      (stretch goal; multi-stage script → TTS)
```

Steps 1–11 are schema-independent. Schema changes re-run only step 18 (a few cents per doc). This is the demo-grade architectural payoff.

---

## 6. Query-Time Pipeline

```
User question
     ↓
0.5 CONVERSATIONAL CONTEXT RESOLUTION (Design 8, only on follow-up turns):
       Three-tier memory — conversation is NOT capped at 6 turns:
       - Tier 1 (hot): last K=6 verbatim turns — fed to anaphora resolver
         (MTRAG-confirmed: past 6 turns adds noise to *retrieval rewriting*,
          not to *generation memory*)
       - Tier 2 (Mem0-style): rolling summary of older turns (>6 turns ago)
       - Tier 3 (structured): carry_forward_entities[], carry_forward_filters{},
         prior_result_set_id — unbounded; never expires until session does
       Gemini Flash anaphora resolver (~$0.0003, ~200ms) sees Tier 1 + Tier 3,
       outputs resolved_query + updated carry-forward state.
       Generation later sees Tier 2 + Tier 1 + Tier 3 (full history,
       log-compressed). All turns stored unbounded in chat_turns.
       Rendered in plan inspector ("Inherited from turn 2: Mr. Sharma;
       'his' → P-541; date_range carried from turn 5").
     ↓
1.  Intent classifier (Gemini Flash, ~100ms):
       factoid | vague | multi-hop | global/thematic | negative | adversarial |
       aggregation | set_operation | temporal_history | chain_aware
     ↓
2.  Query rewriting (gated by intent):
       - Step-Back Prompting        (vague queries: abstract the question)
       - HyDE × N (default N=3, configurable per Design 9):
                                    vague queries → generate N hypothetical
                                    answer docs and embed them as additional
                                    query vectors. Original paper used N=1;
                                    we default to 3 for ensemble diversity
                                    across different facets of vague queries
                                    (drop to 1 to save cost; raise to 5 for
                                    marginal recall on hardest queries).
       - Query2Doc expansion        (vague: preserves keywords for BM25)
       - Tree-of-Clarifications     (ambiguous: 2–4 disambiguation branches)
       - skip rewriting             (clear factoid)
     ↓
2.5 VOCABULARY EXPANSION (Design 6):
       - tokenize query + rewrites
       - lookup domain_vocabulary for explicit synonyms / acronym expansions
       - augment BM25 channel ① query set with synonyms (deterministic)
       - augment dense channel ② with averaged synonym embeddings
       - acronym resolution inline ("GST" → both "GST" and "Goods and Services Tax")
       - plan inspector shows: "Expanded 'GST' [vocab v_421]; synonyms for
         'indemnification' [vocab v_88]"
     ↓
3.  Schema-aware planner (Gemini Flash):
       parses query → structured plan with retrieval modes:
         E — entity lookup            (by name/identifier)
         F — field filter             (schema field predicates)
         S — scoped chunk             (within a parent: doc, contract, project)
         H — hybrid semantic          (BM25 + dense + rerank over chunks)
         T — graph traversal          (multi-hop from seed entities)
         M — mention search           (L2 surface forms)
         G — global summary           (L7, LazyGraphRAG-lazy)
         D — doc metadata filter      (type, date, source, path, authority)
         C — atomic-unit filter        (any L3 unit type + parameter predicates
                                       — clauses, transactions, line_items,
                                       components, rows, decisions, etc.
                                       "C" comes from "clause" historically;
                                       the mode works on every atomic-unit
                                       type, not just clauses)
         A — anomaly filter           (rarity_score > threshold)
         Q — STRUCTURED QUERY          (SQL plan over extracted_entities;
                                       supports aggregation (SUM/AVG/MIN/MAX/
                                       COUNT/COUNT_DISTINCT), group_by, set
                                       ops; jsonb-keyed aggregations + filters
                                       via `<col>.<key>::<cast>` syntax on
                                       audited (table, jsonb_col) pairs —
                                       e.g. SUM(fields.debit::numeric) where
                                       unit_type='transaction' AND
                                       fields.date::date BETWEEN x AND y.
                                       Validated + budgeted execution.
                                       Design: gaps_design.md §Design 1.)
         K — DOC-CHAIN AWARE           (returns chain context: current_version,
                                       all_versions, or history_only.
                                       Design: gaps_design.md §Design 3.)
       plan is JSON, inspectable in UI ("what the system did")
     ↓
4.  Parallel retrieval — **10 channels available, intent-gated** (typically
    4–8 fire per query; the rest self-skip if not applicable). Each active
    retriever returns top-200, all candidates pooled. **All channels filter
    on `files.lifecycle_state = 'ready'`** — partial-ingest docs not yet
    finalized are excluded from retrieval. Plan inspector surfaces "queried
    N ready docs; M still in flight" when partial. Plan inspector also shows
    exactly which channels fired and which were skipped + why:
       - BM25 (pg_search) on contextualized chunks
       - Dense (pgvector HNSW) on contextualized chunks
       - Dense on RAPTOR mid-level summaries
       - Dense on RAPTOR top-level summaries (doc cards)
       - Dense on RAPTOR apex (corpus themes)
       - ColPali multi-vector (if visual candidates)
       - Clause-type filter + dense within type (when intent matches)
       - Anomaly filter (rarity_score > threshold for "unusual" queries)
       - HippoRAG-2 PPR (seeded by query entities)
       - Mention table lookup (surface form ∩ type)
     ↓
5.  Reciprocal Rank Fusion across all channels → top-200 unified
     ↓
6.  Cross-encoder rerank → top-50 (configurable; was top-200, reduced per
    RankZephyr diminishing-returns finding):
       try Cohere Rerank 3.5 with 1500ms timeout
       catch (Timeout | ServiceUnavailable | RateLimitExceeded | 5xx):
          → log warning to audit
          → fall back to local mxbai-rerank-large-v2 (~250ms p50 on CPU,
            ~80ms on GPU; loaded in-process at boot)
          → annotate response with `reranker_used: "mxbai_fallback"`
            for the plan inspector
       Local fallback ALWAYS loaded — not a lazy-init; we accept the
       memory cost (~1.5GB) to ensure zero-downtime degradation.
     ↓
7.  CRAG confidence gate + CONFLICT DETECTOR:
       if top-1 score < τ_low (default 0.65 on Cohere-normalized rerank score;
            configurable per Design 9)
         OR std-dev(top-5 rerank scores) > 0.15 (top-5 disagreement signal):
         escalate to IRCoT loop. IRCoT terminates on whichever comes FIRST:
           - hops_completed ≥ max_hops (default 2 for CRAG mode, 5 for B2)
           - accumulated_cost ≥ cost_ceiling (default $0.04 for CRAG, $0.10 for B2)
           - answer confidence crosses acceptance threshold (normal stop)
         Both caps are pre-flight: before starting next hop, estimate its cost;
         if accumulated + estimated > ceiling, do not start. Partial answer
         returned with "reasoning capped at N hops / $X" indicator in plan
         inspector.
       conflict detector (Gemini Flash, cheap): on retrieved candidates that
         expose different values for the same (entity, predicate) tuple,
         emit fact_conflicts row with all candidate evidence.
         Design: gaps_design.md §Design 2.
     ↓
8.  Generation with Astute RAG defensive pattern + AUTHORITY/CHAIN RESOLUTION:
       - Gemini 2.5 Flash (Pro for complex synthesis) with Anthropic
         Citations-style sentence-level span grounding
       - cite-or-refuse: every claim points to a span
       - aggregation results (mode Q) use TEMPLATED output, not freeform —
         "X total across N rows, computed at T from query [audit#]"
       - conflict resolution applied in order:
           1. doc-chain check (Design 3) — supersession is not conflict
           2. doc_status filter (drop superseded/draft unless asked)
           3. authority dominates if gap ≥ 0.3
           4. recency tiebreaker if authority ~equal
           5. unresolvable → surface BOTH side-by-side, do not pick
       - if max retrieval score below refusal threshold: refuse with
         "no supporting evidence in corpus" + list of what was searched
         + semantically-similar near-misses (Design 2 / red-team F5)
       - generation is STREAMED to the chat UI **sentence-by-sentence**
         (not token-by-token raw): each completed sentence is checked by
         HHEM-2.1 (~200ms local inference) before being released to the
         user. If a sentence fails HHEM, abort the stream, show "let me
         re-check that…" status, retry generation up to 2 times, else
         refuse. This trades ~200ms of perceived latency per sentence for
         strict faithfulness guarantees — the user never sees an unverified
         claim. Drops perceived first-token latency vs. wait-for-full-
         generation by ~1s.
     ↓
9.  Faithfulness check (two-judge):
       - HHEM-2.1 (fast, <600MB local model) — gate A
       - HalluGraph KG-alignment (high-stakes legal/regulatory) — gate B
       - if either fails: regenerate (max 2 retries) or refuse
       References: HHEM Vectara, HalluGraph arXiv 2512.01659 (AUC 0.94 vs 0.60 BERTScore)
     ↓
10. Audit log (immutable, append-only, hash-chained, **PG-native range-
       partitioned by month on `created_at`**; partitions older than 13
       months archived to MinIO `audit_log_archive/` as compressed Parquet;
       indexes on `(workspace_id, created_at)` and `(workspace_id, query_id)`;
       construction:
       row n hash = SHA-256(prev_hash || workspace_id || timestamp ||
                            canonical_json(audit_payload));
       genesis hash_0 = SHA-256("workspace:" + workspace_id + ":init:" +
                                workspace_created_at);
       hash column indexed; tamper detection by walking the chain;
       integrity check job runs nightly, alerts on broken chain.
       PG row-level INSERT trigger computes the hash; no UPDATE/DELETE
       permitted on audit_log table at DB-role level. At 5K-doc workspace
       that's ~250K rows/year — comfortable in single month-partition.
       At 100K-doc scale: ~5M rows/year, partitioning is required to keep
       query latency on the audit log itself under 100ms):
       - query text + user + timestamp + session
       - all channel scores + candidate IDs at each stage
       - index version hash + corpus snapshot
       - cited spans + confidences + rarity scores
       - generator: model + prompt + temperature + seed
       - judge outputs + threshold + decision (accept / refuse / regen)
       - user action (accept / reject / escalate)
     ↓
11. Response to user:
       - answer with inline citation badges (polymorphic — pdf_span, xlsx_row,
         image_bbox, ocr_span, email_message, raptor_summary, aggregate,
         atomic_unit, entity_ref, chain_ref — per Design 5)
       - confidence signal + brief reason
       - "what the system did" inspector panel (plan + candidates + judges)
       - feedback affordance on every fact-bearing surface (👍/👎 + scope chip)

12. Feedback intake (async, post-response):
       - on user 👎 or "wrong" on a citation/extraction/entity/field:
           INSERT corrections row with scope + target + observed/correct values
       - severity classifier (Gemini Flash) decides routing
       - if scope='extraction' AND severity ∈ {blocker, important}:
           trigger targeted re-extraction on implicated doc(s) with
           high-effort prompt (Gemini Pro, explicit hint); overwrite L3/L2b;
           audit-log before/after; notify user 10–60s later
       - if scope='entity_*': insert entity_overrides; re-resolve affected cluster
       - if scope='schema_field': insert schema_field_overrides; revert/retype
       - if scope='doc_chain': unlink false chain member; re-detect
       - blocker/important corrections enter regression_set; CI fails on
         regression. Design: gaps_design.md §Design 4.
```

---

## 7. Storage Stack (concrete, 2026 SOTA, defensible)

```
Postgres 17                                  (single transactional store)
  ├─ Row-Level Security    Enabled day 1 on every table with `workspace_id`.
                            Policy: `workspace_id = current_setting
                            ('app.workspace_id')::uuid`. Middleware sets
                            `SET LOCAL app.workspace_id` per request. A
                            dropped `WHERE workspace_id=…` is mathematically
                            unable to leak across workspaces. MVP runs single-
                            tenant (`workspace_id='default'`) but RLS prevents
                            a retrofit pain when Wave C multi-tenant lands.
                            Admin role bypasses RLS for cross-workspace ops.
  ├─ pgvector ≥ 0.8         HNSW + halfvec — all dense embeddings.
                            **Maintenance:** HNSW graph fragments with
                            heavy INSERT load (background VACUUM doesn't
                            rebuild the graph). A weekly `REINDEX
                            CONCURRENTLY` cron restores recall to baseline.
                            At 5M-chunk scale a REINDEX CONCURRENTLY takes
                            ~hours but doesn't block reads or writes. Skip
                            on weeks with <5% new inserts.
  ├─ ParadeDB pg_search     Tantivy BM25 (self-hosted; Neon-hosted version
                            deprecated for new projects in 03/2026 but
                            self-hosted is solid)
  ├─ ltree                  Hierarchical labels for L0.5 doc-chains AND
                            extracted_entities.lineage_path (Design 7).
                            Built-in PG extension — @>, <@, ~, || operators;
                            subtree cascades natively.
  ├─ Apache AGE             Cypher graph interface (deferred — not required
                            for MVP). Recursive CTEs cover the tree/DAG
                            walks we need (lineage, doc-chains, schema
                            hierarchy). HippoRAG-2 PPR is a SEPARATE concern
                            — see below.

  HippoRAG-2 PPR implementation note: PPR is iterative matrix math, NOT a
  tree walk; recursive CTEs cannot do it efficiently. Implementation:
   • Entity-relation graph is materialized in PG as `hipporag_edges` +
     `ppr_scores` tables (already in §7 schema list).
   • At query time, the application (Python) loads the relevant subgraph
     from PG, computes PPR using NetworkX or igraph seeded by query
     entities, writes results back to `ppr_scores`, cached per session.
   • This is how HippoRAG-2's official OSU-NLP-Group implementation
     works — Apache AGE wouldn't change this (Cypher doesn't do
     PageRank either; would need GDS plugin or application code).
   • Cost: PPR on a 100K-entity graph with 1M edges converges in
     ~20–50 iterations × ~50ms each on commodity hardware (NetworkX
     `pagerank` with numpy backend). Tractable.
  └─ Tables:
       schemas, schema_versions (full JSON snapshot per version + diff
                                 from prior computed on read; rollback =
                                 clone snapshot as new current version,
                                 triggers schema-projection re-extraction
                                 on changed fields only),
       schema_entities, schema_fields,
       schema_relationships (+ kind {contains|part_of|references|associates|attribute_link},
                             cardinality, cascade_delete, single_parent
                             — Design 7),
       domain_vocabulary (canonical_term, synonyms[], acronym_of, expansion,
                          definition, embedding — Design 6),
       chat_sessions, chat_turns (Design 8),
       config_overrides (scope_kind, scope_id, config_key, config_value — Design 9),
       files (+ workspace_id, domain_id, source_authority,
              source_authority_reason, doc_status — every doc belongs to
              exactly one workspace and exactly one domain; the domain
              determines which schema, vocabulary, and chain-detection
              patterns apply at ingest. Set at upload either explicitly by
              the user or inferred from doc-type classification),
       file_lifecycle, jobs (Procrastinate — Python PG-backed queue),
       doc_chains, doc_chain_members,
       fact_conflicts,
       corrections, entity_overrides, schema_field_overrides,
       regression_set,

  Required indexes for 5K+ doc workspaces (Doc Detail and tab queries):
       files(workspace_id, lifecycle_state)            — partial-corpus filter
       files(workspace_id, domain_id)                  — domain-scoped retrieval
       mentions(doc_id)                                — Doc Detail entity lookup
       mention_to_entity(mention_id, entity_id)        — entity-graph hops
       extracted_entities(workspace_id, domain_id, doc_id) — schema projection
       extracted_entities USING gist(lineage_path)     — ancestor/descendant
       chunks(doc_id, chunk_id)                        — chunk-level retrieval
       raptor_nodes(doc_id, level)                     — RAPTOR-level dense
       clauses(doc_id) / line_items(doc_id) / ...      — L3 atomic units
       citations(audit_query_id)                       — citation render
       chat_turns(session_id, turn_index)              — chat history
       raw_pages, parse_artifacts,
       chunks, contextual_chunks, chunk_embeddings,
       raptor_nodes, raptor_edges,
       colpali_indexes,
       mentions, surface_forms,
       emergent_fields, field_name_clusters,
       inferred_schemas, inferred_schema_fields,
       schema_promotion_suggestions,
       clauses, clause_types, clause_parameters,
       entities (+ canonical_name, type, embedding — averaged from
                 member mentions; used for entity-name-as-query lookup
                 and HippoRAG PPR seeding),
       entity_aliases (entity_id, alias_surface, alias_canonical,
                       embedding, source ∈ {extracted, user_defined,
                       discovered}, confidence, first_seen_doc),
       mention_to_entity,
       relationships, relationship_evidence,
       extracted_entities (+ lineage_path ltree, parent_entity_id — Design 7),
       citations (+ type, ref jsonb, label, preview, confidence, authority,
                   doc_status, chain_id, modality, lineage_path_at_cite_time
                   — universal envelope across pdf_span, xlsx_row, image_bbox,
                     ocr_span, email_message, raptor_summary, aggregate,
                     atomic_unit, entity_ref, chain_ref;
                   per-type renderers in UI),
       artifacts (briefings, mind maps, FAQs),
       audit_log, eval_runs, eval_judgments

MinIO (S3-compatible)
  ├─ raw_files/            immutable, content-hash keyed
  ├─ parse_artifacts/      layout JSON, tables JSON, OCR confidences
  ├─ colpali_vectors/      page-image multi-vector bundles
  └─ generated_artifacts/  briefings, mind maps, audio overviews

Procrastinate (Python PG-backed job queue — async-first, used in production by
Mozilla/PeopleDoc; same SELECT FOR UPDATE SKIP LOCKED locking pattern as
Go-ecosystem River but Python-native, fits our FastAPI stack. Earlier drafts
named "River" by mistake — River is Go-only and doesn't have a stable Python
client.)
  ├─ ingestion pipeline jobs
  └─ artifact generation jobs

  Worker failure handling:
    • Each job is leased via SELECT FOR UPDATE SKIP LOCKED with a 30-min
      lease (configurable per job type — OCR-heavy gets 60 min, simple
      classifier gets 5 min).
    • Worker sends a heartbeat to PG every 60s extending the lease.
    • If heartbeat stops (worker OOM-killed, container restart, hardware
      fault), the lease expires and the job becomes available to another
      worker.
    • Per-stage idempotency (content-hash + stage-checkpointing in
      file_lifecycle) ensures replayed jobs are no-ops on already-completed
      stages — the new worker resumes from the last checkpoint, not the
      start.

Stack rationale:
- One transactional boundary across schema versioning + extraction + vectors + BM25.
- ACID across schema migrations and dependent extracted data.
- Backups are one pg_dump.
- Tested production stack: pgvector used by OpenAI/Supabase/Neon; ParadeDB
  used in production.
- Graduation path: vectors → Turbopuffer or Qdrant at ~50M chunks (single
  swap behind adapter interface, no other rewrites).
```

---

## 8. Stack Choices (LLM, embedder, OCR, reranker)

All adapter-swappable via **Hydra + OmegaConf** (Design 9). Config layering: per-user → per-doc → per-doc-type → workspace → domain YAML → global defaults. Runtime overrides via `config_overrides` table; boot YAML via `config/` folder. Effective-config inspector lives in **Settings › Auto-discovery** (`ui_design.md` §6.10) and shows which layer produced each value.



> **Shipped reality vs. this table (2026-05-24):** This stack table is the **full target**. What Wave A actually ships is narrower — see [`docs/build_tracker.md`](build_tracker.md) §5 "Build phases" for the canonical shipped/planned split. Per-row status footnoted below.

| Stage | Default (API-first, laptop-friendly) | Offline fallback | Shipped? |
|---|---|---|---|
| Extraction LLM | Gemini 2.5 Flash (structured outputs, 1M context, cheap) | Llama-3.3-70B / Qwen3-32B | ⬜ Planned (Phase 5+) |
| Hard-query LLM | Gemini 2.5 Pro (planner for complex, ER judge on edge cases) | Qwen3-72B | ⬜ Planned (Phase 5+) |
| Embeddings | Gemini Embedding 001 (#1 commercial MTEB, 68.32) | bge-m3 or Qwen3-Embedding-8B (70.58 MTEB, open) | ✅ Phase 3c (GeminiEmbedder + DeterministicMockEmbedder fallback) |
| Reranker | Cohere Rerank 3.5 | mxbai-rerank-large-v2 | ⬜ Planned (Phase 4 retrieval) — no `cohere` dep yet in pyproject |
| Digital PDF parser | Docling (IBM, MIT, layout-aware, tables) | — | ✅ Phase 2a |
| Scanned PDF parser | **Gemini 2.5 Flash VLM** (Phase 2c — strategy-aware dispatch via text-layer sniff; primary for scanned). Mistral OCR 3 adapter present + tested, but inert without `KB_MISTRAL_API_KEY`. Original target was Mistral OCR 3 first; Gemini won the demo path for the single-API-key story. | Tesseract via Docling's RapidOCR fallback (also already active) | ✅ Phase 2c (`KB_PARSER_STRATEGY ∈ {auto, docling_first, gemini_first, gemini_only}`) |
| OCR fallback (visual) | Gemini 2.5 Flash VLM (folded into Scanned PDF parser row above) | — | ✅ Phase 2c |
| Contextualization (RAPTOR per-chunk prefix + cluster summaries) | Anthropic Claude Opus 4.7 (default for contextualization) OR Gemini 2.5 Flash (default for summarization) + Identity fallback. Adapters: `KB_CONTEXTUALIZER` (3b/3b-bis) + `KB_SUMMARIZER` (3d/3e). | Identity = no-key smoke path | ✅ Phases 3b + 3b-bis + 3d |
| Clause typer | LLaMA-3.3-70B few-shot OR DeBERTa-v3 fine-tune on CUAD | — | ⬜ Planned (Phase 5+ atomic units) |
| Faithfulness gate | HHEM-2.1 + HalluGraph | RAGAS faithfulness | ⬜ Planned (Phase 6+ judges) |

**On the Gemini Flash question — directly:** the architecture decisions in §2–§7 matter ~10× more than the LLM choice. Gemini 2.5 Flash is excellent for extraction, planning, and generation; 1M context is genuinely usable; structured outputs are reliable. We make every model call go through an `LLMAdapter` and can A/B with Sonnet/GPT-5 per-stage if needed.

---

## 9. Evaluation Design

A 300-query stratified golden set, 3 expert annotators, Cohen's κ ≥ 0.7. (For the demo, we ship **45 = 5 questions × 9 strata** — PDF asks ≥15. The full 300 is designed in the writeup.)

**Strata — 9 ship in the 45-question demo eval (#1–#9); #10–#11 are production-only additions that need full instrumentation:**

1. Needle queries — single doc hidden in corpus.
2. Rare-clause queries — rarest 10% of clause types.
3. Adversarial / false-premise queries — TruthfulQA-style.
4. Long-form synthesis queries — scored by FactScore / SAFE.
5. Ambiguous / multi-document queries.
6. Negative queries — correct answer is "no such fact in corpus" (tests refusal).
7. **Aggregation queries** (NEW per gaps_design.md §Design 1) — total/group-by/set
   operations. Pass: aggregate within ±1% of ground truth AND row count exact.
8. **Chain-aware queries** (NEW per gaps_design.md §Design 3) — "latest revision",
   "original before amendment", "evolution of policy". Pass: cite chain members
   with correct roles + ordering.
9. **Conflict-resolution queries** (NEW per gaps_design.md §Design 2) — two
   sources disagree. Pass: correct authority/recency resolution OR surface both
   when unresolvable.

**Production-only additions (live in the full 300-question set; not in the 45-question demo eval):**

10. **Feedback responsiveness** (NEW per gaps_design.md §Design 4) — inject
    synthetic extraction errors, file corrections, verify targeted re-extraction
    fires and answer updates within 60s. Plus: regression_set runs on every
    deploy and must 100% pass (any previously-fixed correction that regresses
    blocks deploy).
11. **Citation modality coverage** (NEW per gaps_design.md §Design 5) — for
    queries grounded in xlsx, OCR, image, RAPTOR, aggregate, atomic-unit,
    entity-ref, chain-ref: the citation type must match the source modality.

**Metrics dashboard:**
- Recall@20, Recall@200 (per stratum)
- nDCG@20
- Context precision (RAGAS)
- Context recall (RAGAS)
- Faithfulness (FaithJudge)
- HalluGraph AUC (legal stratum)
- Refusal precision (correct refusals / all refusals)
- FactScore on long-form
- Citation correctness (% of claims with valid span)

**Demo eval (45 questions, 5 per stratum × 9 strata)** — qualitative pass/fail per question; gives *directional* signal but not statistical confidence at fractional-percent thresholds (need n≥20/stratum for that). The 45-question demo eval is sufficient for the design submission (problem brief asks ≥15); production CI gates below apply once the eval set grows.

**Annotation process** — committed in repo as `eval/golden.yaml`. Each question hand-verified by reading the source docs before commit; gold answers reviewed against the demo corpus contents. Per-stratum gold-answer shape:

| Stratum | Gold-answer shape | Source |
|---|---|---|
| Needle (vocab-mismatch) | `{correct_doc_ids: [...], correct_span_text: "..."}` | Hand-verified against known Enron emails (California energy pushback, Mexico deal) |
| Rare-clause | `{correct_clause_ids: [...], rarity_threshold: 0.95}` | CUAD published annotations |
| Aggregation | `{expected_value: $237M, expected_row_count: 18, tolerance: ±1%}` | Computed live against demo corpus |
| Multi-hop | `{entities_required: [...], hop_chain: [...]}` | Hand-traced |
| Conflict-resolution | `{surfaced_both: true, primary: "amendment_value"}` | Known Enron/EPE original vs amendment |
| Chain-aware | `{chain_id: ..., current_version: ...}` | Known Enron thread structure |
| Negative / refusal | `{should_refuse: true}` | Questions whose answer is NOT in corpus |
| Long-form synthesis | `{required_topics: [...], factscore_min: 0.85}` | Hand-crafted with required-topic coverage list |
| Citation-modality | `{expected_citation_type: pdf_span | xlsx_row | ...}` | Per-modality grounding question |

For production CI growth (n≥20/stratum target): 3-annotator labeling with Cohen's κ ≥ 0.7 inter-annotator agreement.

**Production CI gates (require n ≥ 20 per stratum to be statistically meaningful; block deploy when triggered):**
- Recall@200 < 0.97 on needle stratum
- HalluGraph AUC < 0.90 on legal stratum
- Refusal precision < 0.95
- Aggregation accuracy < 0.95 (Design 1)
- Chain-aware accuracy < 0.90 (Design 3)
- Conflict-handling accuracy < 0.90 (Design 2)
- Regression set < 100% pass (Design 4: any previously-fixed correction that regresses is a deploy blocker — n=1 sufficient because each is a specific known failure)
- Citation modality match < 0.95 (Design 5)

The regression-set gate is the only one that's statistically meaningful at low n, because each row is a specific known-failure case (binary: did it regress or not). The rest need the eval set to grow to ~20 per stratum before the percent thresholds carry real signal.

References:
- RAGAS docs.ragas.io
- ARES arXiv 2311.09476
- FactScore arXiv 2305.14251
- SAFE arXiv 2403.18802
- HHEM github.com/vectara/hallucination-leaderboard
- FaithJudge arXiv 2505.04847
- HalluGraph arXiv 2512.01659
- BRIGHT arXiv 2407.12883 (reasoning-intensive retrieval; top score ~24 nDCG@10; sample for needle stratum)
- FRAMES arXiv 2409.12941 (factuality + retrieval + reasoning, 800 multi-hop Wikipedia questions; sample for multi-hop stratum)
- EnterpriseRAG-Bench arXiv 2605.05253 (May 2026; closest external benchmark to our use case)
- RAGBench arXiv 2407.11005 (100K examples, 5 industry domains; cross-domain validation)
- LegalBench arXiv 2308.11462

---

## 10. Edge Case Traces

**Note on examples:** the original "foundation work issues" and "party + fast delivery" needles are *Reliance-scale architectural illustrations* — what the system is built to solve at enterprise scale. They are NOT demoable on the 80–100-doc CUAD + Enron + SEC corpus because no such docs exist in it. Two demo-corpus-runnable equivalents follow each Reliance illustration.

### Edge case 1 — vocabulary-mismatch needle

**Reliance-scale illustration:** *"What issues have we had with foundation work?"* — needle is one internal text note. *"Vendor XYZ failed to deliver concrete at the Maharashtra plant for foundation pouring on Mar 15. Three-day halt. QC was poor."* — no word "issue", no word "foundation work" qua phrase.

**Demo-corpus equivalent (Enron):** *"Did anyone push back on the California energy strategy?"* — needle is one or two emails using different vocabulary ("the West Coast plan", "PJM markets", "FERC capacity exposure"). Tests the same machinery: Contextual Retrieval + RAPTOR mid-level summaries + HyDE query rewriting into source vocabulary + cross-encoder rerank.

**Trace through the locked pipeline:**

1. Intent: vague.
2. Rewriting:
   - Step-Back: "What general categories of issues affect construction at Reliance plants?"
   - HyDE×3 generates hypothetical docs about vendor performance failures, delivery delays, QC findings.
   - Query2Doc: appends keywords "delays failures vendor concrete pour footings QC."
3. Planner emits modes: `H (hybrid) + D (metadata, doc_type ∈ {note, report, incident}) + M (ACTIVITY mention)`.
4. Parallel retrieval:
   - BM25 picks up "foundation pouring", "vendor", "halt", "QC" in the note.
   - Dense on **contextualized chunk** (Anthropic prefix): *"[Context: internal note documenting vendor performance issue at Maharashtra plant during March 2024 foundation work…] Vendor XYZ failed to deliver concrete…"* — strong semantic match.
   - Dense on **RAPTOR mid-level node**: the note's auto-generated summary card says *"Internal note about vendor performance issue at Maharashtra plant during foundation pouring; failed concrete delivery caused 3-day halt; quality control flagged poor."* — direct match.
   - Dense on **RAPTOR top-level theme**: if "Site execution issues" is an apex theme, this note is a member.
   - HippoRAG-2 PPR: query entities = `[foundation_work, issue]`. Walk to `[Maharashtra plant, Vendor XYZ, concrete supply]`. Note in seed neighborhood.
   - Mention table: `type=ACTIVITY ∩ surface~foundation` hits.
5. RRF fusion: note ranks top-10 from multiple converging channels.
6. Rerank: cross-encoder reads query + contextualized chunk together → top-3.
7. CRAG gate passes.
8. Generation with Astute RAG: *"Based on internal note [citation: foundation_issues_note.txt:1], Vendor XYZ failed to deliver concrete at the Maharashtra plant on Mar 15, causing a 3-day halt to foundation pouring. QC was flagged as poor."*
9. FaithJudge: every claim grounded. HalluGraph alignment: entities match source. Passes.

**Result: designed to retrieve reliably; empirical confirmation pending eval-set run.** The decisive layers are (a) Contextual Retrieval injecting doc context into the chunk embedding [Anthropic Sep 2024: 49% retrieval-failure reduction alone, 67% with rerank], (b) RAPTOR mid-level summary matching abstract vocabulary [Sarthi et al. ICLR 2024], (c) cross-encoder rerank confirming [Cohere Rerank 3.5: 23.4% better than hybrid on internal eval].

### Edge case 2 — rare-clause needle

**Reliance-scale illustration:** *"We had a party in the last 2–3 years where we needed someone to supply something very fast — who?"* — needle is ONE contract among thousands. Local drink supplier for an offsite event. Boilerplate clauses + one unusual *"deliver within 4 hours"* clause.

**Demo-corpus equivalent (CUAD):** *"Find contracts with unusually short termination-notice clauses."* — CUAD has a long tail of `termination_for_convenience` clause parameters; rare cases include "1-business-day notice" or "termination without cause within 24 hours" amongst contracts with typical 30–60-day notice. Tests the same machinery: L3 atomic-unit extraction with `notice_period_days` parameter + per-clause-type rarity scoring (z-score against `notice_period_days` distribution) + multi-hop traversal to contract metadata + plan-inspector showing `C(clause_type=termination_for_convenience, notice_period_days < 5) ∩ A(rarity > 0.95)`.

**Trace through the locked pipeline:**

1. Intent: vague + multi-hop (need Event AND Vendor AND clause type).
2. Rewriting:
   - Step-Back: "Find vendor contracts with unusually strict delivery time requirements within the last 3 years."
   - HyDE×3 generates contracts for fast-delivery vendor at corporate event.
   - Query2Doc: "vendor agreement supply event offsite party function delivery hours rapid."
   - Tree-of-Clarifications: ["party = corporate offsite", "supply fast = SLA in hours"].
3. Planner emits modes:
   - `C` (clause-level: `type=delivery_timing AND parameters.hours < 24`)
   - `A` (anomaly: `rarity_score > 0.95`)
   - `F` (field filter: `signed_date ∈ [today-3y, today]`)
   - `T` (graph traversal: contract → references → Event entity)
   - `H` (hybrid confirmation)
4. Parallel retrieval:
   - **Clause-level filter is decisive:** across 100K contracts, `delivery_timing.hours < 24` returns ~3–5 clauses. Our drink contract is one.
   - **Anomaly filter:** `rarity_score > 0.95` over delivery clauses returns the same handful (corpus centroid is "7–30 days"; "4 hours" is 99th percentile).
   - BM25 + dense on contextualized chunks reinforce.
   - **RAPTOR mid-level:** drink contract's doc card reads *"Contract with Mumbai Beverage Co. for soft drink and water supply for Q3 2023 offsite event. Includes 4-hour delivery clause."* — direct match.
   - HippoRAG-2 PPR: seeds `[event, vendor, fast_delivery]`. Walks `fast_delivery → clauses with hours-level params → contract → Event entity`.
5. RRF fusion: drink contract ranks #1 from multiple converging channels.
6. Rerank locks in.
7. CRAG passes.
8. Generation: *"The Q3 2023 offsite event was supplied by Mumbai Beverage Co., contracted to deliver within 4 hours of order [citation: contract_2023_mumbev.pdf:p7:clause_8.2]. The 4-hour delivery clause is the strictest delivery timing in the entire vendor contract corpus — rarity score 0.99 vs. corpus median of 14 days."*
9. FaithJudge + HalluGraph: all entities and parameters align with source. Passes.

**Result: designed for reliable retrieval with full multi-hop binding (event ↔ vendor ↔ clause) AND the unusual-clause framing surfaced naturally; empirical confirmation pending eval-set run on CUAD subset.** The decisive layer is **clause-level extraction + anomaly scoring** — without it, this would be a coin flip. Every other layer is reinforcing. (Conditional on L3 clause-typer accuracy on rare delivery-timing clauses — needs an explicit eval slice per `docs/red_team.md` finding #4.14.)

---

## 11. UI surface (web app, not just API)

**Ten surfaces grouped Primary · Studio · Admin, plus a universal Doc Detail slide-in.** Chat is the front door; everything else is reachable from the sidebar but lives behind it. Full per-screen breakdown in [`docs/ui_design.md`](ui_design.md); clickable prototype at [`prototype/`](../prototype/); every interactive element wired to a backend endpoint in [`prototype/wiring_inventory.md`](../prototype/wiring_inventory.md).

```
🏠 PRIMARY                          🧪 STUDIO                          📊 ADMIN
  💬 Chat       (front door)         🧠 Schema Studio (6 tabs)          📊 Dashboard
  📤 Upload     (SSE live status)    ⚗️  Extraction Studio              📋 Audit
  🔍 Explore    (progressive)        🎛️  Playground (query / eval / A/B) ⚙️  Settings (+ /swagger)
```

### Cross-cutting design rules (enforced at QA · `prototype/qa_checklist.md` §12)

| Rule | What it means |
|---|---|
| **Schema visible everywhere** | Wherever a field value is shown, its typed/inferred badge appears + field name links to Schema Studio. |
| **Schema editable everywhere** | Every field has inline edit or one-click jump to Schema Studio. |
| **Doc Detail universal** | Any doc / citation / entity / clause → one click opens the same slide-in panel. |
| **⌘K reachable** | Global palette on every page. |
| **Streaming over spinners** | Ingest stages, chat answers, learning events all stream via SSE. No centered spinners. |
| **Trust signals on every derived value** | Answers, fields, anomaly scores, promotions all show confidence + source. |

### SSE channels (one-way server→client streams)

| Stream | Purpose |
|---|---|
| `/events/ingestion` | Per-stage doc progress for Upload + Dashboard |
| `/events/ingestion-counts` | Aggregate counts in Upload top bar + Dashboard |
| `/events/learning` | Dashboard "What the system just learned" feed (auto-promotions, doc-type proposals, prevalence crossings, entity merges, anomalies, doc chains, synonyms, corrections) |
| `/chats/{id}/messages/{mid}/stream` | Token-by-token chat response generation |

SSE chosen over WebSocket because all streams are server→client one-way; SSE gets auto-reconnect via the browser EventSource API for free, works through HTTP/1.1 proxies without protocol upgrades, and is simpler to debug.

### Stack

- **Next.js 15 + Tailwind + lucide icons** — light theme default · monochrome accent · restrained palette · internal-tool aesthetic
- **React Query** for API state + **EventSource (SSE)** for the four streams above
- Server-side rendering kept minimal

### Schema swap demo

Switch from one schema (e.g., legal-contract) to another (e.g., corporate-email) on the same uploaded data. The L1 parse + L2 mentions + L2b emergent fields stay; only the L3/L4 schema-projection rerun. **Demo affordance:** Schema Studio header has "Switch schema" with impact preview (docs re-projected · cost · time · data-loss). Live progress shown on the Upload page. Architectural payoff (parse-once / extract-many) holds at any scale; only speed varies with corpus size.

### Phasing note (wave-by-wave)

| Wave | Surfaces in scope |
|---|---|
| **Wave A (built)** | Chat · Upload · Explore · Schema Studio (all 6 tabs: Typed / Inferred / Collisions / Vocabulary / Lineage / Versions — covered by Phase 1 schema versioning + Phase 5/6 extraction + Design 6 vocabulary + Design 7 lineage + Phase 10d UI) · Dashboard · Audit · Settings (+ `/swagger`) · Doc Detail panel |
| **Wave B (build if time)** | Playground deeper sandbox + Compare configs · `/batch` page (B1, Hebbia-style spreadsheet matrix) |
| **Wave C (cited, not built)** | Extraction Studio (per-doc human review · Phase 23) · Schema Studio *visual* editor (graph-rendering on top of the table tabs · Phase 22) · audio overview (Phase 18) · ColPali index (Phase 16) · HalluGraph gate (Phase 15) · permissions / multi-tenant / temporal validity (Phases 19–21) |

The prototype renders **all 10 surfaces** to lock the IA + interactions before the first line of code. Wave-C surfaces are prototyped but not built in MVP. See §12 for the per-phase build plan.

---

## 11.1 Demo "Blow Their Mind" Moments

Three moments that should land in the live demo:

### Moment 1 — Schema swap on the same files

Load **legal_contracts** schema. Watch the system extract Contracts, Parties, Indemnity caps. Live mid-demo, switch to **corporate_email** schema (or any second domain) on the *same* uploaded data. Re-extraction runs in **~10–60 seconds wall-clock at the 80-doc demo scale** depending on Gemini Flash concurrency (best case ~10s with 50 concurrent calls; realistic ~30s with paid tier; up to 2 min if rate-limited on free tier). No re-OCR, no re-embedding, no re-mentioning. The L1 parse layer + L2 mentions + L2b emergent fields stay; only the schema-driven extraction layer reruns. Live progress shown in `/upload`.

**Honest about scale:** at 100K docs, this becomes ~30 min to several hours wall-clock — *not* seconds. The demo claim of "seconds" holds only because demo scale is 80 docs. The architectural payoff (parse-once / extract-many) holds at any scale; only the *speed* changes with corpus size.

**Proves:** parse-once / extract-many separation. 90% of RAG demos cannot do this.

### Moment 2 — The party-fast-delivery query

Run edge case 2 live. Show the inspectable plan with `C(clause_type=delivery_timing, hours<24) ∩ A(rarity>0.95)`. Show the 3 candidate clauses across 100K contracts. Show the multi-hop traversal to the Event entity. Show the answer with rarity scores in the answer text. Show the click-through to the PDF page with the highlighted clause.

**Proves:** clause-level extraction + anomaly scoring + multi-hop reasoning. NotebookLM cannot do this; Hebbia tries; Harvey does it in legal.

### Moment 3 — Live faithfulness refusal

Ask a question whose answer is *not* in the corpus ("What was our Q4 2026 revenue?" when only Q1–Q3 docs are loaded). System refuses with *"No supporting evidence found in the corpus. I searched: revenue chunks, finance entity field, financial-report doc type. Closest evidence is Q3 2026 revenue [citation]."* Then ask a similar question whose answer IS in the corpus — same UI, supplied with citation.

**Proves:** Astute RAG defensive generation + FaithJudge gate. Stanford RegLab showed Lexis+/Westlaw fail this in 17–33% of queries; we don't.

---

## 12. Build Phasing

**Wave A — MVP slice for the live demo (build):**

```
Phase 0   ┃ Repo + docker-compose (Postgres+pgvector+pg_search+MinIO+Procrastinate)
Phase 1   ┃ Schema service: CRUD, versioning, NL field descriptions, hierarchy
Phase 2   ┃ Parse layer: Docling + Mistral OCR + xlsx + email → raw_pages
Phase 3   ┃ Chunking + Contextual Retrieval + RAPTOR tree build
Phase 4   ┃ Indexing: pgvector HNSW + pg_search BM25 on all RAPTOR levels
Phase 5   ┃ Open extraction → mentions; clause split + typing + anomaly score
Phase 6   ┃ Schema-driven extraction (Gemini structured outputs)
Phase 7   ┃ Identity resolution (deterministic→embedding→LLM judge→union-find)
Phase 8   ┃ Query planner + rewriting (Step-Back + HyDE + Query2Doc)
          ┃ + parallel retrieval + RRF + rerank + CRAG gate + Astute generation
Phase 9   ┃ Audit log + lifecycle visibility + idempotency
Phase 10a ┃ UI — Upload page with live per-doc, per-stage status (Server-Sent Events)
Phase 10b ┃ UI — Chat page (front door · streamed answers · right-side citation cards · inspector)
            ┃ + Doc Detail universal slide-in panel (reused from every surface)
Phase 10c ┃ UI — Explore (Knowledge Explorer: universal search + left-rail facets ·
            ┃ progressive expansion · entity profile via Doc Detail)
Phase 10d ┃ UI — Schema Studio (6 tabs: Typed · Inferred · Collisions · Vocabulary
            ┃ [Design 6] · Lineage [Design 7] · Versions · schema-swap affordance)
Phase 10e ┃ UI — Dashboard (counts · live "what just learned" SSE feed · top anomalies
            ┃ · needs-attention · per-doc-type breakdown · ingestion/query/cost cards)
Phase 10f ┃ UI — Audit (immutable per-query log · re-run with current config ·
            ┃ add-to-regression-set one-click action)
Phase 10g ┃ UI — Settings (workspace · models & retrieval defaults · auto-discovery ·
            ┃ ingestion · cost · API keys · webhooks · storage · /swagger exposure ·
            ┃ Effective Config [Design 9] resolved view under Auto-discovery)
Phase 11  ┃ Public-dataset loader: CUAD + Enron subset + SEC 10-K subset
Phase 12  ┃ Eval harness — 45 questions (5 per stratum × 9 strata) + RAGAS + HHEM
            ┃ + basic Playground UI for query sandbox
```

**Wave B — polish + 2026 SOTA parity (build if time):**

```
Phase 13  ┃ NotebookLM-style artifacts: briefing doc, FAQ, mind map, suggested Qs
Phase 14  ┃ HippoRAG-2 graph index for richer multi-hop
Phase 14b ┃ Playground depth: full A/B Compare configs UI + advanced retrieval controls
          ┃   (basic Playground query sandbox + eval matrix ship in Wave A Phase 12)

  COMPETITIVE-AUDIT-DRIVEN ADDITIONS (closes 2026 SOTA gaps; see docs/competitive_audit.md):

Phase B1  ┃ Batch query mode (Hebbia spreadsheet pattern):
          ┃   /batch page — question × doc-type cohort matrix, cell-level citations
          ┃   ~$0.005/cell, ~$2 per 400-doc batch; reuses extraction pipeline
Phase B2  ┃ Opt-in `deep_research` agentic mode (Search-o1 / ReAct):
          ┃   plan → retrieve → reflect → re-retrieve loop. Terminates on
          ┃   FIRST of: max_hops=5, cost_ceiling=$0.10, confidence accept.
          ┃   Default OFF, intent-class-triggered.
Phase B3  ┃ DSPy prompt optimization layer:
          ┃   refactor extraction + planner + generation prompts as DSPy modules
          ┃   compile against eval set with BootstrapFewShotWithRandomSearch
Phase B4  ┃ Multi-agent decomposition for complex Q-mode:
          ┃   planner emits list of sub-plans for set_op queries / many group_bys
          ┃   parallel execution + join on declared key; sub-plan cap 5
```

**Wave C — future work in the writeup (not built; cited):**

```
Phase 15  ┃ HalluGraph gate for high-stakes queries (cite arXiv 2512.01659)
Phase 16  ┃ ColPali index for visual-heavy docs (cite arXiv 2407.01449)
Phase 17  ┃ LazyGraphRAG community summarization (L7)
Phase 18  ┃ Audio overview (NotebookLM-style)
Phase 19  ┃ Permissions: row/field/entity-level ACL, retrieval-time enforcement
Phase 20  ┃ Temporal validity (valid_from / valid_to, bi-temporal)
Phase 21  ┃ Multi-tenant isolation
Phase 22  ┃ Schema Studio — graph-based visual editor on top of the Wave A table tabs
          ┃   (per archive/Problem_2 UX vision: entities + relationships as nodes/edges)
Phase 23  ┃ Extraction Studio — per-doc PDF + extracted-fields review surface
          ┃   (prototype at prototype/extraction-studio.html locks the design;
          ┃    feedback affordances [Design 4] ship in Wave A everywhere else)
Phase 24  ┃ Multi-agent KB Agent (Legal / Finance / Procurement personas)
```

---

## 13. Cost & Scale Posture

Headline numbers at the architecture target (100K docs):

| Metric | Value |
|---|---|
| Total Postgres footprint | **~35 GB** (single instance fits) |
| One-time ingestion cost | **~$6,000** (of which ~$1,500 is the L2b "schema emerges from data" premium) |
| Schema re-extraction per version | **~$300** (diff-driven; ~$0 if field already in L2b) |
| Per-query cost typical | **~$0.005–0.01** |
| Per-query cost worst case (IRCoT 2-hop or B2 agentic) | **~$0.04–0.08** |

**Full breakdown — including storage detail at all 5 corpus tiers (10K → 100M), latency budget per query class, throughput posture, and the 18 named weaknesses with mitigations — is the single source of truth in `docs/scale_perf_audit.md`.** This section is the executive summary.

---

## 14. Honest Residual Risks

| Risk | Mitigation |
|---|---|
| LLM hallucinates an entity or fact | Span-grounded citations + FaithJudge + HalluGraph; refuse-or-cite generation. |
| Vocabulary mismatch beyond LLM normalization | HyDE×3 + Step-Back; user-taught aliases propagate. |
| Two docs disagree on a fact | Both stored with citations; answer surfaces disagreement, doesn't pick. |
| Ambiguous query | Tree-of-Clarifications + ranked candidate list + ask back. |
| Doc not yet indexed | Lifecycle table shows `pending`; query indicates partial corpus. |
| OCR garbled (handwriting, stamps) | ColPali (visual retrieval) + LLM-tolerant card generation. |
| Rare entity, no canonical cluster | Falls back to L2 mentions with confidence; surfaces uncertain merges for review. |
| Schema doesn't match a doc's content | L0–L2 still work; user can promote discovered concepts into schema. |
| 500+ page contract | Section-aware chunking + hierarchical extraction; 1M-context single-pass when needed. |
| Demo-time pipeline failure | Per-stage idempotency, content-hash keying, isolated failure per doc. |
| MinIO disk full mid-ingest | Pre-flight check on each upload: reject with HTTP 507 (Insufficient Storage) + clear UI error. New uploads pause until capacity restored or another bucket added. Already-ingested docs unaffected (we never overwrite raw_files/<sha>). |
| Procrastinate worker dies mid-job | Job lease expires after 30 min (configurable per job type); job auto-resumes via another worker; per-stage checkpoints in `file_lifecycle` ensure no-op on already-completed stages. |
| pgvector HNSW recall degrades over time | Weekly REINDEX CONCURRENTLY cron (auto-skipped if <5% new chunks since last reindex). |
| Disaster recovery | Daily pg_dump to MinIO `backups/pg/<date>.sql.gz` (~5 min for a 35GB cluster); MinIO replicates daily to a second region via mc mirror. RPO = 24h, RTO = ~1 hr from pg_restore. WAL archiving optional for tighter RPO. |

---

## 15. Locked Decisions (as of 2026-05-21)

1. **Demo corpus.** No fixed domain. **Mixed public datasets:** CUAD (legal contracts) + Enron emails (corporate correspondence) + SEC EDGAR 10-K (financial filings) + scanned variants + one xlsx. ~80–100 docs. See §2.5. Proves domain-agnosticism live.

2. **Wave B scope (planned, not yet shipped):** NotebookLM-style artifacts (briefing/FAQ/mind map/suggested Qs) + HippoRAG-2 graph for richer multi-hop (basic PPR via the `T` planner mode already ships in Wave A) + four competitive-audit commitments: **B1** batch query mode (Hebbia spreadsheet pattern), **B2** opt-in `deep_research` agentic loop (5-hop cap), **B3** DSPy prompt-optimization layer, **B4** multi-agent decomposition for Q-mode. HalluGraph + audio overview deferred to Wave C (cited as future work). See `docs/competitive_audit.md` §5.

3. **Eval:** 45 questions = 5 per stratum × 9 strata (needle, rare-clause, adversarial, long-form, ambiguous, negative, aggregation, chain-aware, conflict-resolution). Biased toward **tricky and ambiguous** — heavier weighting on needle-in-haystack, multi-hop, adversarial/false-premise, and negative (no-answer) strata. Lighter on easy factoids.

4. **UI:** real Next.js web app — **10 surfaces** grouped Primary (Chat front-door · Upload · Explore) · Studio (Schema Studio · Extraction Studio · Playground) · Admin (Dashboard · Audit · Settings + `/swagger`) — plus a universal Doc Detail slide-in panel. **Wave A ships 9 of 10** as functional surfaces (Extraction Studio ships as a Wave-C roadmap page). Playground depth (full A/B compare matrix + eval-suite UI) extends in Wave B (Phase 14b). Locked design + clickable prototype at [`prototype/`](../prototype/) and [`docs/ui_design.md`](ui_design.md). Wiring inventory (~100 endpoints across 16 groups) at [`prototype/wiring_inventory.md`](../prototype/wiring_inventory.md).

5. **HalluGraph:** cite in the writeup as future work, do not build (saves ~2 days; HHEM gate is sufficient for the demo).

### Underlying principle confirmed (user-validated)

> Schema and connections are **dynamic** — built and refined as data arrives. No schema is required to ingest. The system understands documents at multiple resolutions; the user's schema is a *view* on top, not a precondition.

This is the headline framing for the writeup.

---

## 16. References

### Foundational papers and techniques
- HyDE — Hypothetical Document Embeddings: [arXiv 2212.10496](https://arxiv.org/abs/2212.10496)
- Query2Doc: [arXiv 2303.07678](https://arxiv.org/abs/2303.07678)
- Step-Back Prompting: [arXiv 2310.06117](https://arxiv.org/abs/2310.06117)
- RAG-Fusion: [arXiv 2402.03367](https://arxiv.org/abs/2402.03367)
- IRCoT: [arXiv 2212.10509](https://arxiv.org/abs/2212.10509)
- RAPTOR: [arXiv 2401.18059](https://arxiv.org/abs/2401.18059)
- LongRAG: [arXiv 2406.15319](https://arxiv.org/abs/2406.15319)
- Anthropic Contextual Retrieval: [anthropic.com/news/contextual-retrieval](https://www.anthropic.com/news/contextual-retrieval)
- Late Chunking (Jina): [arXiv 2409.04701](https://arxiv.org/abs/2409.04701)
- ColBERTv2 / PLAID: [arXiv 2205.09707](https://arxiv.org/abs/2205.09707)
- ColPali: [arXiv 2407.01449](https://arxiv.org/abs/2407.01449)
- HippoRAG: [arXiv 2405.14831](https://arxiv.org/abs/2405.14831)
- HippoRAG 2: [arXiv 2502.14802](https://arxiv.org/abs/2502.14802)
- GraphRAG (Microsoft): [arXiv 2404.16130](https://arxiv.org/abs/2404.16130)
- LazyGraphRAG: [microsoft.com/en-us/research/blog/lazygraphrag](https://www.microsoft.com/en-us/research/blog/lazygraphrag-setting-a-new-standard-for-quality-and-cost/)
- LightRAG: [arXiv 2410.05779](https://arxiv.org/abs/2410.05779)
- Self-RAG: [arXiv 2310.11511](https://arxiv.org/abs/2310.11511)
- CRAG (Corrective RAG): [arXiv 2401.15884](https://arxiv.org/abs/2401.15884)
- Astute RAG: [arXiv 2410.07176](https://arxiv.org/abs/2410.07176)
- Tree of Clarifications: [arXiv 2310.14696](https://arxiv.org/abs/2310.14696)
- Search-o1: [arXiv 2501.05366](https://arxiv.org/abs/2501.05366)
- RankZephyr: [arXiv 2312.02724](https://arxiv.org/abs/2312.02724)

### L2b emergent schema (open-vocabulary, bottom-up)
- LLM-empowered KG construction survey: [arXiv 2510.20345](https://arxiv.org/pdf/2510.20345)
- PARSE — LLM-driven schema optimization for reliable entity extraction: [arXiv 2510.08623](https://arxiv.org/html/2510.08623v1)
- LMDX — language-model-based document information extraction & localization: [arXiv 2309.10952](https://arxiv.org/pdf/2309.10952)
- Open-domain hierarchical event schema induction by incremental prompting: [arXiv 2307.01972](https://arxiv.org/pdf/2307.01972)
- Schema-driven extraction from heterogeneous tables: [arXiv 2305.14336](https://arxiv.org/pdf/2305.14336)

### Clause-level / legal
- CUAD: [arXiv 2103.06268](https://arxiv.org/abs/2103.06268)
- LEDGAR: [ACL Anthology LREC 2020](https://aclanthology.org/2020.lrec-1.155/)
- LegalBench: [arXiv 2308.11462](https://arxiv.org/abs/2308.11462)
- SaulLM family: [arXiv 2403.03883](https://arxiv.org/abs/2403.03883), [NeurIPS 2024](https://proceedings.neurips.cc/paper_files/paper/2024/file/ea3f85a33f9ba072058e3df233cf6cca-Paper-Conference.pdf)
- LegalPro-BERT: [arXiv 2404.10097](https://arxiv.org/abs/2404.10097)
- ClauseMiner: [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5507977)
- ContractEval: [arXiv 2508.03080](https://arxiv.org/abs/2508.03080)
- Stanford RegLab "Hallucination-Free?": [arXiv 2405.20362](https://arxiv.org/abs/2405.20362)
- Atomic units for enterprise RAG: [arXiv 2405.12363](https://arxiv.org/abs/2405.12363)

### Faithfulness / hallucination eval
- RAGAS: [docs.ragas.io](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/)
- ARES: [arXiv 2311.09476](https://arxiv.org/abs/2311.09476)
- FactScore: [arXiv 2305.14251](https://arxiv.org/abs/2305.14251)
- SAFE / LongFact: [arXiv 2403.18802](https://arxiv.org/abs/2403.18802)
- HaluEval: [arXiv 2305.11747](https://arxiv.org/abs/2305.11747)
- HalluLens: [arXiv 2504.17550](https://arxiv.org/abs/2504.17550)
- FACTOID: [arXiv 2403.19113](https://arxiv.org/abs/2403.19113)
- FaithJudge / Vectara Leaderboard: [arXiv 2505.04847](https://arxiv.org/abs/2505.04847), [github.com/vectara/hallucination-leaderboard](https://github.com/vectara/hallucination-leaderboard)
- HalluGraph: [arXiv 2512.01659](https://arxiv.org/abs/2512.01659)

### Rare-event / anomaly retrieval
- Life insurance contract anomaly: [arXiv 2411.17495](https://arxiv.org/abs/2411.17495)
- Public procurement anomaly (CEUR-WS): [ceur-ws.org/Vol-2369/short09.pdf](https://ceur-ws.org/Vol-2369/short09.pdf)
- Rarity-aware retrieval eval: [arXiv 2511.09545](https://arxiv.org/abs/2511.09545)
- FVA-RAG (anti-context retrieval): [arXiv 2512.07015](https://arxiv.org/abs/2512.07015)

### Benchmarks
- BRIGHT (reasoning-intensive retrieval): [arXiv 2407.12883](https://arxiv.org/abs/2407.12883)
- MuSiQue: [TACL 2022](https://aclanthology.org/2022.tacl-1.31.pdf)
- MIRAGE / MedRAG: [arXiv 2402.13178](https://arxiv.org/abs/2402.13178)

### Production system writeups + competitive references
- NotebookLM on Latent Space: [latent.space/p/notebooklm](https://www.latent.space/p/notebooklm)
- Hebbia "Goodbye RAG": [hebbia.com/blog/goodbye-rag](https://www.hebbia.com/blog/goodbye-rag-how-hebbia-solved-information-retrieval-for-llms)
- Hebbia Matrix Multi-Agent (the closest production competitive analog; informs our Wave B B1/B4): [hebbia.com/blog/divide-and-conquer](https://www.hebbia.com/blog/divide-and-conquer-hebbias-multi-agent-redesign)
- Hebbia Matrix product page: [hebbia.com/product](https://www.hebbia.com/product)
- Harvey BigLaw Bench Retrieval: [harvey.ai/blog/biglaw-bench-retrieval](https://www.harvey.ai/blog/biglaw-bench-retrieval)
- Glean Knowledge Graph (permissions-first KG architecture; informs our deferred ACL roadmap): [glean.com/resources/guides/glean-knowledge-graph](https://www.glean.com/resources/guides/glean-knowledge-graph)
- Glean Enterprise AI Assistant: [glean.com/blog/how-to-build-an-ai-assistant-for-the-enterprise](https://www.glean.com/blog/how-to-build-an-ai-assistant-for-the-enterprise)
- Onyx (open-source enterprise RAG; 64–76% win vs ChatGPT/Claude/Notion on 220K-doc workplace QA): [onyx.app/insights/enterprise-rag-platforms-2026](https://onyx.app/insights/enterprise-rag-platforms-2026)
- Cohere Command R+ grounded generation: [docs.cohere.com/docs/command-r-plus](https://docs.cohere.com/docs/command-r-plus)
- Anthropic Citations API: [anthropic.com/news/introducing-citations-api](https://www.anthropic.com/news/introducing-citations-api)
- LinkedIn customer service RAG+KG: [arXiv 2404.17723](https://arxiv.org/abs/2404.17723)
- BloombergGPT: [arXiv 2303.17564](https://arxiv.org/abs/2303.17564)
- USPTO PE2E: [uspto.gov fact sheet](https://www.uspto.gov/sites/default/files/documents/ai-sim-search.pdf)

### 2026 SOTA patterns we audited against (see docs/competitive_audit.md)
- Search-o1 / agentic search-enhanced reasoning (informs Wave B B2): [arXiv 2501.05366](https://arxiv.org/pdf/2501.05366)
- Agentic RAG production patterns 2026: [digitalapplied.com agentic-rag-patterns](https://www.digitalapplied.com/blog/agentic-rag-patterns-multi-step-reasoning-guide)
- Multi-agent orchestration patterns 2026: [beam.ai multi-agent-orchestration](https://beam.ai/agentic-insights/multi-agent-orchestration-patterns-production)
- DSPy (programmatic prompt optimization; informs Wave B B3): [dspy.ai](https://dspy.ai/)
- Mem0 (agentic memory layer; complementary to RAPTOR + L2b): [mem0.ai/blog/state-of-ai-agent-memory-2026](https://mem0.ai/blog/state-of-ai-agent-memory-2026), [arXiv 2504.19413](https://arxiv.org/pdf/2504.19413)
- Long-context vs RAG production decision framework: [tianpan.co](https://tianpan.co/blog/2026-04-09-long-context-vs-rag-production-decision-framework)
- 2026 RAG performance paradox (simpler chunking wins; informs A/B test): [ragaboutit.com](https://ragaboutit.com/the-2026-rag-performance-paradox-why-simpler-chunking-strategies-are-outperforming-complex-ai-driven-methods/)

---

**End of locked architecture. Awaiting user approval on the five open decisions in §15 before Phase 0 implementation.**
