# Emerging KB

A **self-hosted RAG knowledge base** for heterogeneous documents — digital and
scanned PDFs, spreadsheets, emails, markdown. Drop in mixed files; the system
reads them, extracts and links the facts across formats, and answers
natural-language questions where **every answer either cites its exact source or
refuses** — it never guesses. Runs entirely on PostgreSQL (pgvector + ParadeDB),
domain-agnostic; an on-prem alternative to Glean / NotebookLM / Hebbia.

The demo is a **finance workspace of 55 already-processed documents** (bank
statements, KYC records, a loan amended twice, SEC-style filings, wire-transfer
emails, treasury memos, plus scanned slips, digital-PDF agreements, and
spreadsheets). It ships **pre-seeded**, so a fresh clone shows a full, working
system in minutes — no waiting on extraction.

---

## Quick start

**Prerequisites:** Docker Desktop (running), Node.js ≥ 18, Git.

```bash
# 1. Two API keys in .env (Gemini required; Anthropic optional)
cp .env.example .env
#   KB_GEMINI_API_KEY=...      # required — embeddings + scanned-PDF OCR + default generation
#   KB_ANTHROPIC_API_KEY=...   # optional — Claude for answer generation

# 2. ONE command: brings up Docker (db · minio · migrate · api · worker) AND
#    restores the committed finance seed into Postgres + MinIO.
./scripts/bootstrap.sh
#   First run builds the image (~5–10 min — downloads the Docling OCR models),
#   then restores the seed. Idempotent: re-running skips the restore.
#   Prints:  ✅ Backend up + seeded.  API → http://localhost:8000

# 3. Start the UI (Next.js 15, port 3000 — a separate process, not in compose)
cd ui && cp -n .env.local.example .env.local && npm install && npm run dev
```

Open **http://localhost:3000** — the finance workspace is already populated. Ask
on `/chat`; click any `[1]` citation to open its source; out-of-corpus or unsafe
questions are refused. Good first questions:

- *"What was the ₹6,30,00,000 wire from Acme to Vertex in March 2025 for?"*
- *"What is Acme Corp's HDFC account closing balance for April 2025?"*
- *"List Acme Corp's loan facilities and their outstanding balances."*

```bash
# --- or drive it from the command line (workspace = finance) ---
WS=f0000000-0000-0000-0000-000000000001
curl -X POST http://localhost:8000/chat \
  -H "X-Test-Workspace: $WS" -H "Content-Type: application/json" \
  -d '{"query": "What was the INR 6,30,00,000 wire from Acme to Vertex in March 2025 for?"}'

# Run the 16-pair submission eval through the live pipeline + score it (~14–15/16)
python3 scripts/run_submission_eval.py        # -> eval_out/submission_eval_results.json

# (Re)load a schema from committed YAML — the "schema as config" deliverable
docker compose exec -T api python scripts/load_schema.py --workspace $WS - \
  < demo-corpus/domains/finance/schema.yaml

# Full test suite
uv sync && PATH="$PWD/.venv/bin:$PATH" uv run pytest tests/
```

Stop everything with `docker compose down` (keeps data); wipe and reload from
scratch with `docker compose down -v && ./scripts/bootstrap.sh`. Common gotchas:
Docker Desktop must be running before step 2; if the script isn't executable run
`chmod +x scripts/bootstrap.sh`; the first answer can take ~15s (it runs several
model calls — search, rerank, generate, fact-check).

---

## How it works

**Every document is stored at four resolutions at once**, and each question is
answered from the level that fits it:

| Layer | What it holds | Answers |
|---|---|---|
| **L1** | raw page text (as parsed) | verbatim lookups |
| **L2** | overlapping chunks **+ a RAPTOR tree of summaries + named "mentions"** | needles *and* broad/summary questions |
| **L3** | extracted fields + table rows ("atomic units" — each transaction, clause, line item) | structured facts, rare-value anomalies |
| **L4** | typed schema entities + relationships (Bank, Account, Transaction…) | cross-doc joins, multi-hop |

**Ingest** (async Procrastinate pipeline, one file at a time):
`parse → chunk → contextualize → embed → RAPTOR → extract mentions →
extract fields+tables → extract schema entities → resolve identities → ready`,
plus side branches for doc chains, relationship triples/graph, and conflict
detection. PDFs route by a text-layer sniff — digital → **Docling**, scanned →
**Gemini vision-OCR** (per bad page). Schema **emerges from the data**: stable
fields auto-promote from inferred to typed, audit-logged and reversible.

**Query** (`POST /chat`): `classify intent → plan a mode → retrieve from 6
channels in parallel → fuse (RRF, k=60) → rerank (Cohere) → CRAG relevance gate
→ generate with inline citations → faithfulness gate → cite-or-refuse`. The six
channels are BM25-over-chunks, BM25-over-RAPTOR, dense-over-chunks,
dense-over-RAPTOR, mentions-exact, and atomic-unit/rarity.

Full technical walkthrough (ingest, query, UI, stack) in
[`docs/architecture.md`](docs/architecture.md).

**Stack:** PostgreSQL 17 (ParadeDB image — pgvector for dense/HNSW, pg_search for
BM25, `ltree` for hierarchy) · MinIO blob store · Procrastinate worker · FastAPI
(`:8000`) · Next.js 15 UI (`:3000`). One transactional store, no separate vector
DB. Multi-tenancy is enforced by Postgres **row-level security** (the app
connects as a non-superuser role and `SET`s the workspace per request). Models
are swappable with mock/identity fallbacks so CI runs with no keys.

---

## Shipped vs. roadmap

**Shipped (works end-to-end in the seeded demo):**

- Mixed-format ingest (markdown · email · digital PDF · scanned/OCR PDF · xlsx) to `ready`
- Four-resolution storage (L1–L4) + per-doc RAPTOR summary trees
- 6-channel hybrid retrieval → RRF → Cohere rerank → CRAG → Astute generation → faithfulness gate
- **Structured-first query head (T2)** — resolve the structured predicate first, then scope retrieval to the matching docs (confidence-weighted: hard pre-filter when trusted, else a soft rerank boost that auto-widens), carry the scope across follow-ups (relax / reset), and answer LOOKUP/LIST/existence directly from the structured layer with a source-chunk check — degrading to plain RAG whenever the signal is weak. Spec: [`docs/query_pipeline_plan.md`](docs/query_pipeline_plan.md)
- **Cite-or-refuse** with per-answer confidence, conflict detection, and a hash-chained audit log
- Cross-format / cross-doc identity resolution (one entity spanning markdown + PDF + xlsx + scan)
- Schema auto-promotion (emerging → typed), import/export schema YAML
- Next.js UI: `/chat` · `/upload` · `/explore` · `/files/[id]` (Doc Detail) · `/schema-studio` · `/dashboard` · `/audit` · `/settings` · `/playground`
- 16-pair submission eval through the live pipeline (~14–15/16)

**Roadmap — the path to enterprise scale** (the bones are right; this is the next
build phase, and the honest current limits). See the deep analysis in
[`docs/scale_perf_audit.md`](docs/scale_perf_audit.md):

- **Horizontal ingest.** A second concurrent extractor **deadlocks**, so ingestion is effectively single-worker today. Fixing this (then sharding by workspace, batching LLM calls, backpressure + dead-letter queue) is the #1 throughput unlock.
- **Incremental corpus maintenance.** `finalize_corpus` is O(corpus) on every file completion and the corpus-level RAPTOR rebuild is **flaky under LLM timeouts**; per-doc summaries (what answers cite) are solid. Move corpus work off the per-file path and make it incremental.
- **Get LLMs off the query hot path.** The query path is synchronous LLM calls (~15s, no token streaming); query embeddings, rerank, and CRAG verdicts aren't cached. Stream generation; cache; sample/await the heavy gates.
- **Conflict precision.** The detector is great on structured facts (it catches the loan rate-flip across amendments) but **over-fires on prose/metadata** (42k false conflicts in one messy workspace). Gate it to structured, canonicalized facts.
- **Identity robustness.** Dominant entities resolve correctly, but variants leak at the edges ("Vertex" vs "Vertexind") and the resolver can fail-open. Always compare top-k candidates; retry the embedder; never silently create a duplicate.
- **Aggregation (T3 — next phase).** Q-mode (numeric SQL aggregation) is still **allow-list-gated** — it sums only a fixed set of tables, so "sum all outstanding loans" over an arbitrary extracted table refuses (T2 *lists* them correctly). T3 makes the catalog schema-derived (grain + JSONB casts + active stated-vs-computed reconciliation + audit envelope); the T2 resolver already emits the row-filters/grain it needs. See [`docs/query_pipeline_plan.md`](docs/query_pipeline_plan.md) §6.11.
- **Real multi-tenancy + ops.** RLS is solid underneath, but the UI is wired to **one workspace** (no switcher/auth). Add workspace switching, per-tenant rate/cost limits, and tracing.
- **Simplify retrieval.** 13 planner modes, several of which are thin post-filters on the same hybrid core — collapse to a measured few so latency drops and the eval is trustworthy.
- **Large-corpus storage.** Tune HNSW, consider vector quantization, and partition tables by workspace before 100k+ docs.

Deliberately out of scope (conscious descopes, not bugs): permissions/ACL,
native CAD/DICOM/BIM geometry, real-time streaming sources, bi-temporal AS-OF
queries, agentic actions (read-only by design), and live source connectors. Full
list in [`docs/problem_statement.md`](docs/problem_statement.md).

---

## Repository layout

```
.
├── README.md                  ← you are here
├── docker-compose.yml         ← ParadeDB (pg + pgvector + pg_search) · MinIO · migrate · api · worker
├── .env.example               ← copy to .env; documents every KB_* var
├── scripts/
│   ├── bootstrap.sh           ← one-command stack-up + seed restore
│   ├── run_submission_eval.py ← 16-pair eval through the live /chat pipeline
│   └── load_schema.py         ← load a schema from YAML ("schema as config")
├── demo-corpus/
│   ├── domains/finance/       ← the 55 demo source documents + schema.yaml
│   ├── eval/submission_eval.yaml   ← the 16 ground-truth Q&A pairs
│   └── seed/kb_seed.dump      ← committed data-only pg_dump (restored by bootstrap)
├── src/kb/                    ← FastAPI service + Procrastinate worker (parsers, chunking,
│                                 contextualization, embeddings, raptor, query/*, workers/*)
├── migrations/sql/            ← versioned, idempotent Postgres DDL
├── ui/                        ← Next.js 15 app (the 9 live surfaces)
├── tests/                     ← pytest suite (testcontainers) + per-phase specs
└── docs/                      ← architecture, problem statement, scale audit + archive/ (history)
```

---

## Docs

Start with [`docs/README.md`](docs/README.md) — it indexes every doc as **current**
or **historical**. The current set:

- [`docs/architecture.md`](docs/architecture.md) — how the system actually works (ingest · query · UI · stack) + an honest scale review
- [`docs/problem_statement.md`](docs/problem_statement.md) — the brief: requirements, demo corpus, deliberate descopes
- [`docs/scale_perf_audit.md`](docs/scale_perf_audit.md) — deep cost/latency/scale analysis and the enterprise upgrade paths

Everything historical (build logs, audits, fix plans, per-domain eval runs, the
design-phase architecture/UI specs) is preserved under
[`docs/archive/`](docs/archive/). Contributor workflow in
[`CONTRIBUTING.md`](CONTRIBUTING.md); license: [MIT](LICENSE).
