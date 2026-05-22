# Knowledge Base Service

> A domain-agnostic enterprise knowledge base. Upload heterogeneous documents (PDFs digital + scanned, spreadsheets, images, emails). Ask cited natural-language questions. The system auto-discovers structure as data arrives; user-defined schemas are a *view* on top, never a precondition.

**Status:** planning / pre-build (as of 2026-05-22). Architecture, UI (10-surface IA via clickable prototype at [`prototype/`](prototype/)), wiring inventory (~100 endpoints), and all nine tier-1 gap designs are locked. Phase 0 G1 ready to open.

**Public brief:** [`docs/problem_statement.md`](docs/problem_statement.md) · **Contributing:** [`CONTRIBUTING.md`](CONTRIBUTING.md)

---

## The mental picture — a "smart workbook"

The system is a giant workbook that **fills its own sheets in by reading your documents**.

```
                      YOUR KNOWLEDGE BASE
                  ╔═══════════════════════════╗
                  ║   Smart Workbook           ║
                  ╠═══════════════════════════╣
                  ║  📄 Documents              ║
                  ║  📋 Contracts              ║
                  ║  📋 Clauses                ║
                  ║  📋 Bank Statements        ║
                  ║  📋 Transactions           ║
                  ║  📋 Drawings + Components  ║
                  ║  📋 Residents (xlsx rows)  ║
                  ║  📋 Events, Notes, ...     ║
                  ║  📋 People & Orgs          ║  ← canonical directory
                  ║  📋 Relationships          ║  ← who ↔ what across docs
                  ║  💬 Chat (cited Q&A)        ║
                  ╚═══════════════════════════╝
```

Each sheet = one doc type. Each row = one **atomic unit** (clause / transaction / component / row / decision). Two cross-cutting sheets — People & Orgs, and Relationships — connect everything across all docs. The chat queries across all sheets.

---

## Three principles that drive every design choice

**1. Schema is a *view*, not a precondition.**
The system can answer questions on day one with zero schema defined. Two open-extraction passes run on every doc: **L2** captures generic entity mentions (PERSON, ORG, MONEY, …) for cross-doc navigation, and **L2b** is bottom-up — each doc proposes its own structured fields in its own vocabulary (`stent_type`, `khasra_number`, `vendor_gstin`, …) with no fixed type list. As similar docs accumulate, L2b clusters proposed fields into an **inferred schema per doc-type**. When prevalence + stability + value-type confidence cross threshold, the field is **auto-promoted to typed schema** — no user click, no confirmation gate. Promotions are audit-logged and fully reversible; you edit or rename if you disagree. Adding or modifying a field re-runs *only* the schema-projection step — never re-parses, never re-embeds. **Schema literally emerges from data, and the system promotes it itself.**

**2. Atomic units, not just clauses.**
L3 isn't a "clauses" layer — it's an "atomic typed units" layer. *What* counts as a unit is doc-type-specific:

| Doc type | Atomic unit | Anomaly means |
|---|---|---|
| Contract / Employment letter | Clause | rare clause params |
| Bank statement | Transaction | unusual amount / unknown counterparty |
| Drawing | Component | rare spec / material |
| ID xlsx | Row | duplicate IDs, impossible DOBs |
| Invitation card | doc-as-Event record | — |
| Land record | doc-as-Parcel + history entries | high-frequency owner change |
| Handwritten note | none (L2 mentions only) | — |

Adding a new doc type = registering a small plug-in (classifier + extractor + parameter schema + rarity definition). No core changes.

**3. Multi-resolution storage + parallel retrieval.**
**10 storage layers** (L0 raw → L0.5 doc chains → L1 parse → L1a contextual chunks → L1d RAPTOR summaries → L2 mentions → L2b emergent fields → L3 atomic units → L4 entities → L5 relationships → L6 HippoRAG graph → L7 communities-lazy; plus L1b late-chunk and L1c ColPali sub-layers). **10 parallel retrieval channels** (BM25, dense at every RAPTOR level, atomic-unit filter, anomaly filter, HippoRAG PPR, mention lookup, doc metadata, ColPali for visual). **12 planner modes** including the new `Q` (structured SQL/aggregation) and `K` (doc-chain aware). Naive RAG runs *one* channel; we run all 10 in parallel, fuse with RRF, rerank, and refuse to hallucinate via Astute RAG + faithfulness judges + conflict detection.

---

## Locked decisions

| Decision | Choice |
|---|---|
| Demo corpus | Mixed public datasets — **CUAD** (legal contracts) + **Enron** (corporate emails) + **SEC 10-K** (financial filings) + scanned variants + one xlsx. ~80–100 docs. No domain lock-in. |
| Storage | **Postgres 17** + pgvector ≥ 0.8 + ParadeDB pg_search + MinIO + Procrastinate (Python PG-backed queue). One transactional store. |
| LLMs | **Gemini 2.5 Flash** (extraction, planning, generation); **Gemini Embedding 001** (embeddings); **Cohere Rerank 3.5** (reranker); adapter-swappable. |
| Parsers | **Docling** (digital PDF), **Mistral OCR 3** (scanned), openpyxl (xlsx), Gemini Flash VLM (last-resort). |
| Wave A (built) | Core ingest + retrieval + **8-of-10 UI surfaces** (chat front door · upload · explore · schema studio · dashboard · audit · settings + `/swagger` · doc-detail panel · basic playground for eval) + 45-question eval (5 per stratum × 9 strata, incl. aggregation, chain-aware, conflict-resolution). |
| Wave B (built, polish + 2026 SOTA parity) | NotebookLM-style artifacts + HippoRAG-2 graph + four competitive-audit-driven additions: **B1** batch query mode (Hebbia spreadsheet pattern), **B2** opt-in `deep_research` agentic loop (Search-o1 style, capped at 5 hops + cost ceiling), **B3** DSPy prompt optimization layer, **B4** multi-agent decomposition for complex Q-mode queries. See `docs/competitive_audit.md`. |
| Wave C (cited only) | HalluGraph, ColPali, LazyGraphRAG, audio overview, permissions, temporal validity. |

Full rationale in [`docs/architecture.md` §15](docs/architecture.md).

---

## What you'll see in the UI (10 surfaces, chat-first)

The sidebar groups everything into **Primary** (the 95% surface — chat is the front door), **Studio** (power-user work surfaces), and **Admin** (operations + governance). A universal **Doc Detail** slide-in opens from anywhere a doc / citation / entity / clause is referenced.

**Primary**
1. **`/chat`** — front door. ChatGPT-style streamed answers + right-side citation cards + collapsible "How I answered" plan inspector. **The 95% surface.**
2. **`/upload`** — drag-drop with **live per-doc per-stage status table** (Datadog-style)
3. **`/explore`** — Knowledge Explorer: universal search + left-rail filters (Documents · Doc Types · Atomic Units · Entities · Relationships · Topics · Anomalies); **progressive expansion** — no graph dump

**Studio**
4. **`/schema-studio`** — six tabs: Typed (auto-promoted, editable) · Inferred (emerging, with threshold bars) · Collisions (the only place you click to confirm anything) · Vocabulary (synonyms / acronyms / definitions) · Lineage (containment + revision chains) · Versions
5. **`/extraction-studio`** *(Wave C surface — prototyped for design preview)* — per-doc PDF + extracted fields, approve/edit/reject, prompt editor, test mode
6. **`/playground`** — sandbox for queries · eval suite · A/B compare configs

**Admin**
7. **`/dashboard`** — counts + sparklines · live "what the system just learned" stream · top anomalies · needs-attention · ingestion/query/cost cards
8. **`/audit`** — immutable per-query logs, filterable by user / time / status / feedback; **re-run with current config** + **add to regression set** as one-click actions
9. **`/settings`** — workspace · models & retrieval defaults · auto-discovery · ingestion · cost · API keys · webhooks · storage · `/swagger` exposure

**Universal**
10. **Doc Detail** — slide-in panel from anywhere. Hero zone shows the cited clause + PDF region (zero-scroll verification); accordions below for all extracted fields · clauses · entities · relationships · revision chain · usage · processing log.

Locked design in [`docs/ui_design.md`](docs/ui_design.md). Clickable prototype at [`prototype/`](prototype/). Wiring of every interactive element to a backend endpoint in [`prototype/wiring_inventory.md`](prototype/wiring_inventory.md).

---

## Two edge cases the architecture must handle

**Edge case 1 — vocabulary mismatch needle.** *"What issues have we had with foundation work?"* — answer is one internal note that uses different words ("vendor failed to deliver concrete", "QC poor"). Solved by Contextual Retrieval (chunk embedding now carries doc context) + RAPTOR summary nodes (matches abstract query) + HyDE rewrites + cross-encoder rerank.

**Edge case 2 — rare-clause needle.** *"We had a party in the last 2–3 years where we needed someone to supply something very fast — who and which party?"* — answer is one contract among thousands, with one unusual "deliver within 4 hours" clause. Solved by clause-level atomic-unit extraction + per-type rarity scoring (4 hours = 99th percentile vs corpus centroid of 7–30 days) + multi-hop traversal contract → Event entity.

Full traces in [`docs/architecture.md` §10](docs/architecture.md) and [`docs/walkthrough.md`](docs/walkthrough.md).

---

## Repository layout

```
.
├── README.md                            ← you are here
├── CONTRIBUTING.md                      ← how to contribute · Git workflow · gate discipline
├── LICENSE                              ← project license
├── .gitignore
├── docs/
│   ├── problem_statement.md             ← public technical brief
│   ├── architecture.md                  ← locked formal spec
│   ├── build_tracker.md                 ← gate-by-gate build discipline (G1 → G5 per phase) + Git workflow
│   ├── ui_design.md                     ← locked UI design + demo flow + per-screen reference
│   ├── walkthrough.md                   ← teaching doc: ingest + retrieval traces
│   ├── scenarios.md                     ← 8 real enterprise stress-tests
│   ├── red_team.md                      ← adversarial review of the architecture vs scenarios queries
│   ├── gaps_design.md                   ← detailed designs for 9 gaps:
│   │                                      Design 1 — aggregation Q mode
│   │                                      Design 2 — conflict detection + source authority
│   │                                      Design 3 — doc chains / threads / amendments
│   │                                      Design 4 — user feedback / correction loop
│   │                                      Design 5 — universal citation envelope + modality renderers
│   │                                      Design 6 — domain vocabulary management
│   │                                      Design 7 — hierarchical containment + lineage ltree
│   │                                      Design 8 — conversational context for follow-ups
│   │                                      Design 9 — layered configuration Hydra + DB
│   ├── citations_audit.md               ← reality grounding: every paper/product/claim verified;
│   │                                      defaults vs. research-grounded values made explicit
│   ├── competitive_audit.md             ← 2026 SOTA sweep: vs Hebbia, Glean, NotebookLM, OpenAI Files,
│   │                                      Onyx, DSPy, Search-o1, Mem0; commits 4 Wave B additions
│   │                                      to close real gaps
│   ├── scale_perf_audit.md              ← honest scale/perf/cost answer at 10K/100K/1M/10M/100M
│   │                                      docs; 18 named weaknesses with mitigations
│   └── archive/                         ← historical reasoning (superseded; kept for trail)
│       ├── Problem_1.md                 ← early problem expansion
│       ├── Problem_2.md                 ← early UX expansion
│       └── ui_design_v1.md              ← pre-prototype design doc (ASCII mockups)
└── prototype/                           ← clickable HTML prototype of all 10 surfaces (G1.5)
    ├── index.html                       ← navigation landing
    ├── chat.html                        ← + the 9 other screen files
    ├── ...
    ├── qa_checklist.md                  ← visual QA checklist (per page · per viewport)
    ├── qa.mjs                           ← Playwright-driven QA runner (screenshots + auto-checks)
    ├── qa/                              ← screenshots + per-page QA reports
    │   ├── screens/
    │   └── reports/
    └── wiring_inventory.md              ← every interactive element → planned API endpoint (G1.6)
```

**Planned layout once Phase 0 starts** (deliberately not scaffolded yet — folders will be created when the code lands):

```
.
├── README.md                            ← will be rewritten as build/run instructions
├── docker-compose.yml                   ← Postgres + pgvector + pg_search + MinIO + Procrastinate
├── Makefile
├── pyproject.toml                       ← uv-managed Python project
├── config/                              ← layered YAML config + per-domain schemas
├── db/
│   ├── migrations/                      ← Postgres DDL (versioned)
│   └── seed/                            ← demo corpus loader
├── docker/                              ← any non-trivial Dockerfile customizations
├── src/kb/                              ← FastAPI service
│   ├── api/                             ← routes
│   ├── core/                            ← business logic (planner, retrievers, judges)
│   └── adapters/                        ← parsers, LLM, storage, embeddings
├── web/                                 ← Next.js 15 UI
├── scripts/                             ← bootstrap, seed, eval, demo-cache warmer
├── eval/                                ← 45 stratified Q&A + RAGAS + regression set
├── tests/
└── docs/                                ← planning docs above + future build docs
```

---

## Reading order

The docs are grouped by purpose. Read top-to-bottom within a group; pick groups by what you need.

### Group 1 — Mental model (30 min, read in order)
1. **This README** — mental model, locked decisions, what's explicitly out of scope — 5 min
2. **[`docs/walkthrough.md`](docs/walkthrough.md)** — teaching doc: one doc's journey through ingest + one query's journey through retrieval, with concrete numbers — 15 min
3. **[`docs/ui_design.md`](docs/ui_design.md)** — locked design for all 10 surfaces + Doc Detail panel + end-to-end demo flow — 10 min (clickable prototype at [`prototype/`](prototype/))

### Group 2 — Full spec (canonical reference, 75 min)
4. **[`docs/architecture.md`](docs/architecture.md)** — locked formal spec: 16 sections covering layers, indexing/query pipelines, storage stack, stack choices, eval, edge cases, UI, phasing, cost, risks, references — 30 min
5. **[`docs/gaps_design.md`](docs/gaps_design.md)** — 9 detailed gap designs (Q-mode, conflicts, doc chains, feedback, citations, vocabulary, lineage, chat context, layered config) — 45 min reference

### Group 3 — Stress-tests & audits (review at your pace)
6. **[`docs/scenarios.md`](docs/scenarios.md)** — 8 enterprise stress-tests with verdicts (80/10/5/5 coverage analysis) — 10 min
7. **[`docs/red_team.md`](docs/red_team.md)** — adversarial battle-test against scenario queries; resolved + open findings — 15 min
8. **[`docs/citations_audit.md`](docs/citations_audit.md)** — every paper/product verified real; our defaults vs. research-grounded split — 10 min
9. **[`docs/competitive_audit.md`](docs/competitive_audit.md)** — 2026 SOTA sweep (Hebbia / Glean / NotebookLM / OpenAI Files / Onyx / DSPy / Search-o1) + the 4 Wave B commitments — 15 min
10. **[`docs/scale_perf_audit.md`](docs/scale_perf_audit.md)** — single source of truth on scale/perf/cost at 5 corpus tiers; 18 named weaknesses with mitigations — 15 min

**Single sources of truth (no duplication elsewhere):**
- 9 detailed designs → `gaps_design.md`
- All cited papers/products → `architecture.md` §16 references (+ `citations_audit.md` verification)
- Cost / latency / throughput tables → `scale_perf_audit.md`
- 2026 SOTA comparison → `competitive_audit.md`
- 18 named weaknesses → `scale_perf_audit.md` §5

---

## Open design choices — flag what you want changed; defaults stand otherwise

These are decisions the architecture has *made*, not questions awaiting your approval. If anything looks wrong, push back and I'll change it. Silence = the default stands.

1. **Mental model.** "Smart workbook" — sheets per doc-type, cross-cutting People/Orgs and Relationships sheets, chat queries across all of them.
2. **Atomic units.** L3 unit per doc-type via plug-in: clause / transaction / component / row / decision / message_segment / etc. Handwritten notes have no L3.
3. **Schema promotion is automatic.** Auto-promotes when prevalence ≥ 80%, stability ≥ 0.9, value-type confidence ≥ 0.9, doc-type sample ≥ `min_doc_count` (production default 20; demo / small-corpus default 5 — scale-aware via Design 9 Hydra config). Audit-logged, reversible, editable. No user click anywhere in the flow. (Naming collisions and ambiguous types surface for disambiguation, not confirmation.)
4. **Impact preview** on schema *edits* (rename / delete / split) — *"will re-run schema-projection on 412 contracts, ~$4 cost, ~3 min"* — shown before destructive ops, dismissible. Not shown for auto-promotions (those are non-destructive).
5. **Demo corpus.** CUAD + Enron + SEC 10-K + scans + xlsx, ~80–100 docs.
6. **Pre-Phase-0 architecture changes already designed** (in `docs/gaps_design.md`):
   - Design 1 — aggregation `Q` planner mode (templated answers, audit-artifact citation)
   - Design 2 — conflict detection + source authority + recency resolution
   - Design 3 — L0.5 doc chains (email threads, contract amendments, drawing revisions)
   - Design 4 — user feedback / correction loop (corrections table, targeted re-extraction, regression CI)
   - Design 5 — universal citation envelope across 10 modalities (xlsx, OCR, image, RAPTOR, aggregate, atomic-unit, entity, chain, email, PDF)
   - Design 6 — domain vocabulary (synonyms, acronyms, definitions; query expansion + L2b discovery; closes the "vocabulary used in the domain" requirement)
   - Design 7 — hierarchical containment + lineage chains (schema_relationships.kind + extracted_entities.lineage_path ltree; closes the hierarchical containment + parent/container-chain requirements)
   - Design 8 — conversational context (ChatContext + LLM anaphora resolver + 3-tier memory: hot K=6 turns for the retrieval rewriter per MTRAG, Mem0-style rolling summary for older turns, unbounded structured carry-forward; conversation itself is unbounded like ChatGPT/Claude; closes the conversational follow-up requirement)
   - Design 9 — layered configuration (Hydra/OmegaConf YAML for boot + config_overrides DB for runtime; 6-layer resolution; closes the layerable-configuration requirement)
   - Plus: cold-start guards on rarity, negative-query semantic fallback, IRCoT capped at 2 hops, streaming generation, rerank fallback wired

Phase 0 (repo skeleton + docker-compose + storage + lifecycle DDL) starts when you say "go."

---

## What we explicitly *don't* claim (scope, not solved problems)

Each of these is a deliberate descope, not an oversight — open items on the public roadmap:

1. **Permissions / row-field-entity ACL** — Wave C. Architecture has `domain_id` everywhere; enforcement is future work.
2. **Native CAD / DWG / DICOM / BIM geometry queries** — out of scope. ColPali (Wave C) handles visual layout, not geometry.
3. **Real-time streaming sources (POS, SCADA, ATM, EMR vitals)** — KB ≠ OLAP, by design.
4. **Bi-temporal validity (AS-OF queries on facts)** — Wave B/C. Doc chains (Design 3) handle *some* temporal questions (latest revision, supersession) but not arbitrary `AS OF '2023-06-15'` against fact-level history.
5. **Agentic actions** — read-only by design. The system retrieves and reasons; it does not send emails, place trades, mutate external systems, or take downstream actions.
6. **Vector-store graduation past ~50M chunks** — Postgres-everything is the MVP store; Turbopuffer/Qdrant graduation cited as future work in `docs/architecture.md` §7.
7. **Image *content* understanding** — we OCR scanned documents and embed page images (ColPali). We do *not* claim to recognize photographic content like "this site photo shows unbraced formwork." That requires a specialized vision pipeline.
8. **Multi-tenant isolation** — Wave C. Same shape as permissions.
9. **Cross-lingual L3 atomic-unit extraction** — L2 mentions are multilingual via Gemini Embedding; L3 clause/transaction typing is English-only for the demo.
10. **Live source connectors (Slack, SharePoint, Gmail sync)** — file-ingestion only; connectors are deployment integration, not architecture.

All nine tier-1 designs (1: aggregation, 2: conflicts, 3: doc chains, 4: feedback, 5: multi-modal citations, 6: vocabulary, 7: hierarchical containment + lineage, 8: conversational context, 9: layered config) **are** designed and integrated. Full audit of citations and the defaults-vs-research-grounded split in [`docs/citations_audit.md`](docs/citations_audit.md).
