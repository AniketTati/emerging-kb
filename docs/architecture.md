# Emerging KB — Architecture

How the system actually works, written so any engineer who knows Python can
follow it. **Part 1** walks the real code in four areas — Ingestion, Query,
UI/UX, and Tech Stack → Deployment — with the exact steps and data flow.
**Part 2** is an honest, scale-minded review: what's right, what's missing,
what's wrong, and what it would take to run this at Glean/Hebbia scale.

> This describes the system **as it runs in the seeded demo today**. The
> design-phase formal spec (the fuller target system — extra storage layers,
> planner modes, and channels not all wired yet) is preserved at
> [`archive/architecture_design_spec.md`](archive/architecture_design_spec.md).
> Where the two differ, this doc is the source of truth for what's live.

The one mental model to hold: **every document is stored at four resolutions at
once** —

- **L1** raw pages (the text as parsed),
- **L2** chunks + RAPTOR summaries (overlapping views of the text) + surface "mentions",
- **L3** open-world extracted fields and table rows ("atomic units"),
- **L4** closed-world schema entities + relationships (typed objects like *Bank*, *Transaction*).

Retrieval pulls from all four and fuses them, so a needle ("the account number")
and a synthesis ("trace the loan chain") each resolve from the layer that fits.

---

# PART 1 — The code, in 4 parts

## 1) Ingestion — upload → "ready"

Ingestion is an **async pipeline**. The API just stores the file and drops one
job on a queue; a background **worker** (Procrastinate, a Postgres-backed job
queue) runs the rest. Each file walks a fixed set of lifecycle states, and each
stage **chains the next** by deferring another job. If a stage fails, the file
stops at `failed`; everything is restartable.

Lifecycle (the `files.lifecycle_state` column enforces this exact set):
```
queued → parsing → parsed → chunked → contextualized → embedded
       → raptor_building → mentions_extracting → fields_extracting
       → entities_extracting → identity_resolving → ready
       (or → failed / deleted)
```

**Step-by-step** (file: `src/kb/workers/tasks.py`, plus the modules each stage calls):

0. **Upload — `POST /files`** (`src/kb/api/files.py`)
   - Compute `sha256` of the bytes. **Dedup:** if that hash already exists in the
     workspace, return the existing file (HTTP 200) instead of re-ingesting.
   - Store the raw bytes in **MinIO** at key `raw_files/<sha256>`.
   - Insert a `files` row (state `queued`) and **defer `parse_file`**.

1. **`parse_file` — turn bytes into page text.** (`src/kb/parsers/`)
   - Pick a parser by file type: **Docling** for digital PDFs (keeps
     layout/tables), plus parsers for XLSX, email (.eml), and plain text/markdown.
   - **Scanned vs digital routing (the trickiest bit):** for PDFs it sniffs the
     text layer. A digital PDF goes through Docling; a scanned/image-only PDF
     scores low and **escalates to Gemini vision-OCR** — and it can escalate
     *per bad page* instead of re-OCRing the whole doc. The chosen parser is
     stamped into `raw_pages.layout_json.provenance.chose` (`docling` or
     `gemini_ocr`) so you can always audit how a page was read.
   - Writes one `raw_pages` row per page → state `parsed` → defers `chunk_file`.

2. **`chunk_file` — split text into retrievable pieces.** (`src/kb/chunking/`)
   - First it **classifies the doc type** (bank_statement, contract, …) into
     `files.inferred_doc_type`, then picks a **structure-aware chunker** by type:
     a bank statement chunks one row per transaction, a contract by clause,
     everything else uses a hierarchical splitter (paragraphs/sections with parent
     pointers). Chunk configs live in a `chunker_configs` table — data, not code.
   - Writes `chunks` (with `node_level` and `parent_chunk_id` so the tree can be
     walked) → state `chunked` → defers `contextualize_file`.

3. **`contextualize_file` — make each chunk self-explanatory.** (`src/kb/contextualization/`)
   - **Anthropic-style Contextual Retrieval:** for each chunk an LLM writes a 1–2
     sentence prefix saying where the chunk sits ("This is the March balance row
     of Acme's HDFC statement…"). The prefix + chunk text is what gets embedded,
     so a bare "₹2.2cr" isn't ambiguous. Adapter auto-picks Gemini → Anthropic →
     Identity (no-op if no keys).
   - Writes `contextual_chunks` → state `contextualized` → defers `embed_file`.

4. **`embed_file` — turn text into vectors.** (`src/kb/embeddings/`)
   - Batch-embed each contextual chunk with **Gemini `gemini-embedding-001`
     (3072-dim)**. No key → a deterministic mock embedder (so CI runs without
     spend). Writes `chunk_embeddings` (pgvector) → `embedded` → defers
     `raptor_build_file`.

5. **`raptor_build_file` — build a summary tree over the doc.** (RAPTOR)
   - Cluster the chunk embeddings, summarize each cluster with an LLM, embed the
     summaries, then cluster *those*, up to an apex. Big-picture questions hit a
     summary node, not 50 chunks. Writes `raptor_nodes` + `raptor_edges` (per-doc
     scope) → `mentions_extracting`.

6. **`extract_mentions_file` — find every named thing (L2).**
   - LLM NER over each chunk → `extracted_mentions` (people, orgs, money, dates,
     account numbers…), with character offsets back to the source for
     highlighting. → `fields_extracting`.

7. **`extract_kv_tables_file` — fields + table rows in one call (L2b + L3).**
   - A single structured-output LLM call returns **scalars** ("total = ₹6.3cr")
     and **tables** (rows of transactions / line-items). Scalars become
     `proposed_fields`; rows become `extracted_entities` ("atomic units"). It also
     **clusters similar field names** across the doc and **auto-promotes** stable
     ones into the schema (`inferred_schema_fields` / `schema_fields`). →
     `entities_extracting`; also kicks off doc-chain detection.

8. **`extract_schema_entities_file` — fill the typed schema (L4).**
   - For each entity type the schema defines (Bank, Account, Transaction…), a
     structured LLM call pulls instances and links them with parent/child lineage.
     Writes `extracted_entities` (typed) → `identity_resolving`.

9. **`resolve_identities_file` — merge "the same thing" across docs.**
   - For each mention: **(1)** exact name+type match → **(2)** embed it and pull
     the **top-k** nearest existing entities → **(3)** ask an LLM "is this the
     same?" → **(4)** if nothing matches, create a new canonical entity. Writes
     `canonical_entities` + `mention_to_entity`. This is what makes "Acme Corp",
     "Acme Corp Pvt Ltd", and an OCR'd slip resolve to **one** entity. → `ready`,
     and defers `finalize_corpus`.

10. **Side branches:** `detect_doc_chain_file` (version chains like original →
    addendum-1 → addendum-2, via frontmatter or title heuristics → `doc_chains`),
    `extract_triples` → `build_relationships` → `build_graph_file`
    (subject-predicate-object triples → a `graph_edges` table for multi-hop), and
    **conflict detection** (`fact_conflicts`: same entity+predicate with different
    values across docs, resolved by chain / authority / recency, or left
    "unresolved").

11. **`finalize_corpus` — workspace-level cleanup.** Fires after the *last* file
    settles (self-gates: if any file is still in flight, it returns and the next
    completion re-fires). It re-clusters field names across all docs, reconciles
    duplicate entities the per-doc resolver missed, renumbers doc chains, and
    builds a **corpus-level RAPTOR** tree across documents.

**Honest notes:** corpus-RAPTOR and force-re-extract paths are partial;
triples/graph are built but not heavily exercised by the demo eval; conflict
detection works but over-fires on prose (see Part 2).

---

## 2) Query — `POST /chat` → a cited answer (or a refusal)

One question runs through ~15 steps. The spine is **plan → retrieve from 6
channels → fuse → rerank → check relevance → generate with citations → check
faithfulness → cite-or-refuse**. (Files: `src/kb/api/query.py` and
`src/kb/query/*` — orchestrator, intent, planner, channels, rrf, rerank, crag,
generate, faithfulness, citations, conflict_detector.)

1. **Request + session.** `/chat` takes the question + the `X-Test-Workspace`
   header. A chat session is created/loaded (`chat_sessions`, `chat_turns`) so
   follow-ups have memory. An `Idempotency-Key` lets a repeat call replay the
   cached answer.

2. **Intent classification.** Label the question — factoid, multi-hop,
   aggregation, temporal/chain-aware, adversarial, inventory, etc. (Gemini, or a
   keyword fallback). Some patterns ("list my documents") short-circuit.

3. **Plan + pick a mode.** The planner maps intent → one of **13 modes** and fills
   in parameters (which entities, filters, unit types, chain view…):
   **H** hybrid (default), **E** entity lookup, **F** field filter, **S**
   scoped-to-named-docs, **D** doc-metadata filter, **M** mention search, **C**
   atomic-unit filter, **A** anomaly (rarity), **T** graph traversal (multi-hop),
   **G** global/corpus summary, **K** doc-chain-aware, **Q** structured SQL
   aggregation, **I** inventory listing. If a mode can't resolve its inputs, it
   **degrades to H** rather than failing.

4. **Conversation context (follow-ups).** If the question has a pronoun /
   back-reference ("what was *that* transfer's UTR?"), an LLM rewrites it using
   the last few turns + a running summary. No back-reference → skip the call.

5. **Adversarial / safety pre-flight.** Regex + the intent label catch
   PII-exfiltration ("list everyone's PAN"), fraud/tampering, and **false
   premises** ("per clause 99…" when there is no clause 99). These **refuse up
   front** with a templated reason.

6. **Query rewriting.** Generate step-back, HyDE, and query2doc variants to
   improve recall (the hybrid channels consume these).

7. **Retrieve from 6 channels in parallel** (`src/kb/query/channels.py`), each
   capped at ~20 hits, each isolated in a SAVEPOINT so one channel's SQL error
   can't kill the request:
   1. **BM25 over chunks** (ParadeDB lexical),
   2. **BM25 over RAPTOR summaries**,
   3. **dense (vector) over chunks** (pgvector cosine),
   4. **dense over RAPTOR summaries**,
   5. **mentions-exact** (substring over extracted mentions),
   6. **atomic-unit / rarity** (typed rows, boosts rare facts).

8. **Auto-merge.** If most leaf-children of a RAPTOR parent are hit, swap them for
   the parent (you get the summary instead of 5 fragments).

9. **Fuse with RRF.** **Reciprocal Rank Fusion (k=60)** combines the 6 ranked
   lists into one — no score calibration, just ranks. Keep top ~30.

10. **Mode routing.** Apply the chosen mode's filter/boost over the fused set (e.g.
    K keeps the current chain version; C keeps only transaction rows). If a mode
    filters to zero but plain hybrid had hits, it restores hybrid.

11. **Rerank.** A **cross-encoder (Cohere Rerank-3.5)** re-scores the top ~30 and
    keeps the top **10**. If Cohere errors, it falls back to passthrough (rerank
    is a quality boost, not a hard dependency).

12. **CRAG relevance gate.** An LLM scores the top snippets for "do we actually
    have evidence?" (takes the **max** — one strong snippet is enough). Below
    threshold **on hybrid mode**, the answer is refused as "insufficient
    evidence." (One light retry — *IRCoT* — can rewrite the question using the
    hits so far and search once more before giving up.)

13. **Generate (Astute-style).** The generator LLM (Gemini by default; Anthropic
    optional) writes the answer under a strict prompt: **cite every claim with a
    `[hit_id]`, enumerate all matching items, show arithmetic for sums, prefer
    refusing over guessing.** Citations come back as hit-ids; the server
    back-fills file/page/snippet.

14. **Faithfulness gate.** A second check verifies the answer is actually
    supported by the cited text. By default an LLM reads it claim-by-claim and
    asks "does the source really say this?" (`KB_FAITHFULNESS_GATE=llm`, the
    default when a key is set; without keys a token-overlap heuristic runs
    instead). Low → it can regenerate the answer (up to 2×).

15. **The two gates must agree (grounding gate).** If the answer is weakly
    grounded **and** retrieval was weak, it **refuses**. If retrieval was strong,
    it keeps the answer but shows a **low-confidence** badge. A confidence level +
    reason is derived from these signals (high / medium / low).

16. **Conflicts + citations + persistence.** Detect fact conflicts among the hits
    (tag the "losing" sources superseded), enrich each citation with a
    **modality** (pdf_span, xlsx_row, raptor_summary, email_message, entity_ref,
    chain_ref…), write the turn to `chat_turns`, and log everything to `query_log`
    for the Audit page.

**When it refuses (cite-or-refuse):** adversarial/PII/false-premise; no hits;
CRAG below threshold (hybrid); faithfulness "refused"; LLM/parse error;
out-of-corpus questions. This is the whole point — *cited or it didn't happen*.

**Honest notes:** Q-mode (numeric aggregation) is **allow-list-gated** — it only
sums over a fixed set of tables, so "sum all outstanding loans" over an arbitrary
extracted table is refused; T/G (graph/global) modes are partial; HyDE/query2doc
are generated but lightly used. Several modes are thin post-filters on the same
hybrid core (see Part 2, "mode sprawl").

---

## 3) UI / UX — what a user actually touches

A **Next.js 15** app (React, Tailwind). It talks to the API over plain HTTP +
Server-Sent-Events (SSE), attaching the workspace via the `X-Test-Workspace`
header (`ui/lib/workspace.ts`; configured by `NEXT_PUBLIC_KB_API_URL` +
`NEXT_PUBLIC_KB_WORKSPACE_ID`). The API client is `ui/lib/api.ts`.

**Surfaces (routes under `ui/app/`):**
- **/chat** — the main surface (below).
- **/upload** — drop files; a live table shows each file marching through the
  lifecycle (SSE per file), with re-extract / re-parse / delete and a processing log.
- **/files/[id]** — **Doc detail**: a two-pane audit view. Left = the original
  rendered natively (PDF.js for PDFs, the scanned image, an .eml viewer, an XLSX
  table) from `GET /files/:id/blob`. Right = lazy accordions for every layer:
  parsed pages (L1), the chunk tree, proposed fields, sub-entities, schema entity
  instances (L4), mentions, triples, doc-chain, processing log, and "cited in
  which chats."
- **/explore** + **/explore/entity/[id]** — faceted browse of documents,
  entities, relationships, anomalies; an entity profile with aliases + related
  buckets + inline rename.
- **/schema-studio** — view schemas/entities/fields, "needs review" (promote an
  inferred field), version history, **import/export schema YAML**.
- **/dashboard** — workspace health: file counts by type, query counts, open
  conflicts, low-authority files, a "needs attention" triage list.
- **/audit** — the query log with a **hash-chained integrity badge**, filters, and
  a "replay" link back into chat.
- **/settings** — layered config (models, effective config with the layer each
  value came from, overrides, chunking).
- **/playground** — a sandbox to run one-off queries and compare two model configs
  side by side. (An `/extraction-studio` surface and the eval-suite tab are
  roadmap stubs.)

**The chat experience in detail** (`ChatExperience.tsx`, `Composer.tsx`,
`MessageBubble.tsx`, `AnswerCard.tsx`, `CitationsPanel.tsx`):
- **Composer:** Cmd/Ctrl+Enter sends (Enter = newline); an `@` doc-filter scopes
  retrieval to chosen files; attach jumps to upload.
- **Live pipeline trace:** while the answer computes, the API streams SSE events
  and the UI shows a timeline — *intent → planned mode → retrieving (6 channels) →
  fused → reranked → auto-merged → CRAG → generating → faithfulness*. "What the
  system did" is visible, not a black box.
- **Answer card:** the answer with inline `[n]` citations; a **grounded %**; a
  **confidence badge + reason**; a **conflict-resolution banner** (collapsed by
  default — "picked X over Y via chain/authority/recency"); and a **"How I
  answered"** expander (mode, intent %, the 6 channels, CRAG, latency,
  faithfulness verdict).
- **Sources panel:** one card per cited source with a relevance %, modality/kind
  chips, "superseded" chips for conflict losers, and the exact verbatim snippet
  (sliced to the cited character range when available). Clicking an inline `[n]`
  scrolls + flashes its source card.
- **Sessions:** chat history persists; reopening a session restores turns,
  citations, confidence, and conflict banners.

---

## 4) Tech stack → Deployment

**One command brings up everything in Docker.** `./scripts/bootstrap.sh` runs
`docker compose up` and then restores the committed demo seed. Compose defines
five services with health-gated ordering:

```
db (healthy) → minio (healthy) → migrate (runs once, exits 0) → api + worker
```

| Service | What it is | Notes |
|---|---|---|
| **db** | **PostgreSQL 17** via the **ParadeDB** image | One database does *both* searches: **pgvector** for dense vectors (HNSW) and **ParadeDB/pg_search** for **BM25** lexical. Also `ltree` for hierarchy. |
| **minio** | S3-compatible **object store** | Raw bytes at `raw_files/<sha256>`. Serves the doc-detail "view original". |
| **migrate** | one-shot | Runs `scripts/bootstrap_db.sh`: the SQL migrations via `python -m migrations.runner` (idempotent, tracked in `schema_migrations`) + the Procrastinate queue schema. |
| **api** | **FastAPI** (uvicorn) on **:8000** | Middleware: request-id, workspace resolution, access logs, CORS for the UI. Opens the Procrastinate app so `POST /files` can enqueue jobs. |
| **worker** | **Procrastinate** worker | Consumes the Postgres job queue and runs the ingestion pipeline. |

**Multi-tenancy via Row-Level Security (RLS).** Every workspace-scoped table has a
`workspace_id` and a policy `workspace_id = current_setting('app.workspace_id')`.
The app connects as a **non-superuser role `kb_app`**, and each request does
`SET app.workspace_id = <uuid>` — so a query physically cannot read another
workspace's rows. Migrations + the worker connect as superuser `kb` (which
bypasses RLS by design).

**Models / providers (all swappable, with auto-probe + safe fallbacks):**
- **Gemini** — embeddings (`gemini-embedding-001`, 3072-dim), scanned-PDF OCR,
  contextualizer, RAPTOR summarizer, and default answer generation + faithfulness.
- **Anthropic (Claude)** — optional, for contextualization / generation.
- **Cohere** — Rerank-3.5 cross-encoder.
- No keys? Each adapter falls back to a deterministic mock / identity no-op so the
  pipeline still completes (CI). The demo story is a single Gemini key + an
  optional Anthropic key.

**The image build** is multi-stage (uv for deps) and **pre-downloads the Docling
OCR models at build time** (~5 min on first build, then cached) so the first
parse isn't slow. Runs as a non-root user.

**Bootstrap + seed (why the demo is instant):** the heavy extraction is **not**
re-run on a fresh clone. `bootstrap.sh` restores a committed **data-only
`pg_dump`** (`demo-corpus/seed/kb_seed.dump` — the whole extracted knowledge
layer: chunks, embeddings, entities, schemas, RAPTOR, conflicts) and **rebuilds
the MinIO blobs from the committed source files by content-hash** (the blob key is
`sha256(bytes)`, so no separate blob artifact is needed). It's idempotent —
re-running skips the restore if the DB already has files. The demo workspace is
**finance (55 docs)** at `f0000000-0000-0000-0000-000000000001`; the UI points
there by default.

---

# PART 2 — Honest review: what's right, what's missing, what's wrong, and scaling

Reviewing this the way someone who's shipped enterprise RAG (Glean/Hebbia) would.
Short version: **the architecture is the right shape** — the gaps are *unfinished
pieces* and *things that only bite at scale*, not wrong foundations. The deep
numbers (cost/latency per corpus tier, breaking points, upgrade paths) are in
[`scale_perf_audit.md`](scale_perf_audit.md).

## What's right
- **Multi-resolution representation (L1–L4 + RAPTOR).** Needles, summaries, typed
  facts, and a graph coexist, and retrieval fuses across them. Most RAG demos only
  have "chunks + vectors"; this has four useful layers.
- **The retrieval core is the settled SOTA baseline:** hybrid (BM25 + dense) →
  **RRF (k=60)** → **cross-encoder rerank**, implemented correctly.
- **One Postgres for everything.** pgvector + ParadeDB-BM25 + RLS in a single DB —
  no separate vector DB to run, back up, and keep consistent.
- **Contextual Retrieval** (per-chunk context before embedding) — a known,
  high-impact technique.
- **The trust layer is real and is the hard part:** cite-or-refuse, an LLM
  faithfulness gate, a CRAG relevance gate, per-answer confidence, conflict
  resolution, and a **hash-chained audit log**.
- **Schema emerges from the data** (free-extract → canonicalize), so it ingests
  heterogeneous documents without someone modeling a schema first.
- **Clean engineering:** an async, restartable, idempotent pipeline on a
  Postgres-backed queue; swappable model adapters with mock fallbacks; RLS from day
  one; provenance stamped at every stage; one-command boot from a pre-baked seed.

## What's missing (built thin or deferred)
- **Streaming generation.** Answers are produced in one shot, then shown; you wait
  ~15s (the pipeline trace softens it, but it isn't token streaming).
- **Claim-level grounding.** Faithfulness scores the *whole answer*; the stronger
  design decomposes it into atomic claims and verifies each against its cited span.
- **Incremental RAPTOR / corpus finalize.** `finalize_corpus` re-does corpus-wide
  work; no true incremental update of the corpus summary tree or converged schema
  as single docs trickle in.
- **Real multi-tenancy in the UI.** RLS is solid underneath, but the UI is wired to
  **one hardwired workspace** (no switcher, no login).
- **Eval rigor.** 16 grounded Q&A + a scorer is good, but it's a single run with
  ±1-pair stochasticity, no held-out split, no separate retrieval-vs-generation
  scoring in CI.
- **Cost / rate-limit controls.** Nothing budgets or backpressures the LLM calls.
- **Caching.** Query embeddings, rerank results, and CRAG verdicts aren't cached.

## What's done wrong (the real bugs/risks)
- **Conflict detection over-fires on prose/metadata.** Keyed correctly on (entity,
  predicate), and great on *structured* facts (the loan-rate-flip chain is detected
  and shown). But on narrative and routing metadata it generates huge volumes of
  false "conflicts" — one non-demo workspace had **42,811**. We pruned the demo and
  collapsed the UI banner; the underlying detector is unfixed.
- **The worker can't be safely scaled out yet.** A second concurrent extractor
  **deadlocks** — exactly the component you most need to parallelize. Ingestion is
  effectively single-worker today.
- **`finalize_corpus` is O(corpus) on every file completion.** It self-gates, but
  during a bulk load it can thrash and stalls on transient LLM timeouts — an
  O(n²)-flavored pattern at scale.
- **Identity resolution under-merges at the edges and can fail-open.** Dominant
  entities are right, but variants leak ("Vertex" vs "Vertexind"), and an embedder
  hiccup historically created *new* entities instead of retrying.
- **The whole query path is synchronous LLM calls.** Intent → (rewrite) → CRAG →
  rerank → generate → faithfulness are each a blocking network call, which is why a
  query takes ~15s.
- **Mode sprawl.** 13 planner modes, several cosmetic post-filters on the same
  hybrid core — surface area that hides bugs and complicates the eval.

## What it would take to run at scale (100k+ docs, many tenants)
1. **Make ingestion horizontally scalable.** Fix the second-worker deadlock; run
   many workers; **shard by workspace**; batch the LLM calls; add backpressure,
   retries, and a dead-letter queue. The #1 unlock.
2. **Make corpus maintenance incremental.** Update RAPTOR and the converged schema
   per new doc (or small batches), off the per-file completion path.
3. **Get LLMs off the query hot path.** Stream generation; cache query embeddings +
   rerank + CRAG; make the heavy gates async or sampled; consider a small local
   reranker/judge for the common case.
4. **Fix conflict detection before it scales.** Gate to *structured* fields on
   *canonicalized* entities; suppress prose/metadata predicates; cap and dedupe.
5. **Harden identity resolution.** Top-k blocking everywhere, never fail-open
   (retry the embedder), and a periodic reconcile job — the entity graph is the
   asset; don't let it fragment.
6. **Index + storage strategy for large corpora.** Tune HNSW (`m`,
   `ef_construction`, `ef_search`); consider vector quantization (int8/binary);
   **partition tables by workspace**; plan `pg_dump`/PITR backups + object-store
   replication.
7. **Real multi-tenancy + ops.** Workspace switching + auth in the UI; per-tenant
   rate/cost limits; connection pooling per tenant; per-stage tracing + cost
   metering; an eval suite in CI with a held-out set and multi-run averaging.
8. **Simplify retrieval.** Collapse the mode facade to a small, measured set so
   latency drops and the eval becomes trustworthy.

**Bottom line:** the bones are right — multi-resolution retrieval, a correct
hybrid+rerank core, and a serious trust layer. Making it Glean/Hebbia-grade is
about *throughput* (parallel ingest, incremental corpus), *latency* (streaming +
caching + fewer hot-path LLM calls), and *correctness-at-scale* (conflict
precision + identity robustness) — not redesigning the architecture.
