# Problem Statement — Emerging KB

A domain-agnostic enterprise knowledge base. Upload heterogeneous documents (PDFs both digital and scanned, spreadsheets, images, emails). Ask natural-language questions and get cited answers. The system auto-discovers structure as data arrives; user-defined schemas are a *view* on top, never a precondition.

This document is the public technical brief. The locked architecture lives in [`architecture.md`](architecture.md); the locked UI design lives in [`ui_design.md`](ui_design.md); the build discipline lives in [`build_tracker.md`](build_tracker.md).

> **Coming back to this project?** Read [`STATUS.md`](STATUS.md) first — it's the
> rolling snapshot of what's on `main`, what's in the PR queue, and what the
> demo workspace looks like right now.

---

## The challenge

Build a knowledge base service that solves the *real* enterprise problem:

> "I have thousands of documents — contracts, statements, drawings, notes, emails, spreadsheets, scans. I want to ask questions across all of them. I want to trust the answer. I want to see where each fact came from."

Most "chat with your PDFs" tools answer this with naive RAG: chunk, embed, retrieve nearest, prompt. That fails on:

- **Heterogeneous modalities.** A digital PDF, a scanned land record, a photo of a handwritten note, a 8,000-row xlsx, an email thread — each needs different parsing.
- **Vocabulary mismatch.** The user asks *"foundation issues"*; the relevant note says *"vendor failed to deliver concrete; QC poor."* Pure dense retrieval misses this.
- **Needle-in-haystack.** One unusual clause in one contract among thousands. Naive top-K retrieval drowns it.
- **Aggregation.** *"Total indemnity exposure across all contracts"* — RAG can't sum.
- **Conflict.** Two docs disagree on the same fact. Naive RAG picks one arbitrarily.
- **Multi-hop.** *"Which contracts share an arbitration venue with the EPE deal?"* — needs entity linking + graph traversal.
- **Trust.** When the system is wrong, users have no path back to correct it.
- **Schema reality.** Real enterprise data has hundreds of doc-types with overlapping but distinct schemas. No one defines them upfront.

## Requirements (the locked set)

A working system that:

1. **Ingests** heterogeneous documents (PDF digital + scanned, xlsx, csv, jpg, png, eml, zip archives, folders).
2. **Discovers schema from data** — fields, doc-types, entities emerge from the corpus. No schema upfront.
3. **Auto-promotes** stable fields from "emerging" to "typed" without user clicks — prevalence + stability + value-type confidence thresholds. Reversible, audit-logged.
4. **Answers** natural-language questions with **cited** responses (per-claim provenance, modality-aware citations).
5. **Refuses** when evidence is insufficient. Refusal is correct behavior, not a failure mode.
6. **Surfaces conflicts** when two sources disagree. Doesn't pick arbitrarily.
7. **Aggregates** (totals, group-bys, set operations) with verifiable audit artifacts.
8. **Resolves identity** across spelling variants ("Aakash Cons." ≡ "Aakash Constructions Pvt Ltd").
9. **Tracks doc chains** — contract amendments, email threads, drawing revisions.
10. **Captures feedback** — when an answer is wrong, the correction routes back to targeted re-extraction; the system learns.
11. **Is auditable** — every query reproducible from immutable logs.
12. **Demos at scale honestly** — perfect at 80–100 docs (the demo corpus); honest about cost/latency tradeoffs at 100K / 1M / 10M / 100M.

## Demo corpus

Mixed public datasets to prove domain-agnosticism:

| Domain | Dataset | What it tests |
|---|---|---|
| Legal | **CUAD** — 510 commercial contracts annotated with 41 clause types | Clause-level extraction · rare-clause anomaly · identity resolution across parties · hierarchical schema |
| Communications | **Enron Email Corpus** — ~500K real emails from 150 employees | Vague-query needles · identity resolution across aliases · conversation threading · casual mentions of events |
| Financial | **SEC EDGAR 10-K filings** — public, well-structured | Aggregation queries · structured-table extraction · cross-doc joins |
| Scanned | Variants of the above, re-rendered as low-quality scans | OCR robustness · scan-vs-digital fallback chain |
| Spreadsheet | Vendor-list xlsx derived from CUAD parties | xlsx row-level extraction · join across modalities |

**Total: ~80–100 documents.** Small enough to fully demo + audit; large enough to demonstrate the architectural moves that fail at small scale (cross-doc identity resolution, topic clustering, schema emergence).

## Out of scope (deliberate descopes)

The system is **scoped** here. Each item below is a conscious choice, not an oversight. They are open challenges for a public roadmap:

1. **Permissions / row-field-entity ACL** — architecture has `domain_id` everywhere; enforcement is future work.
2. **Native CAD / DWG / DICOM / BIM geometry queries** — out of scope. ColPali (future work) handles visual layout, not geometry.
3. **Real-time streaming sources** (POS, SCADA, ATM, EMR vitals) — KB ≠ OLAP, by design.
4. **Bi-temporal validity (AS-OF queries on facts)** — doc chains handle *some* temporal questions (latest revision, supersession) but not arbitrary `AS OF '2023-06-15'` against fact-level history.
5. **Agentic actions** — read-only by design. The system retrieves and reasons; it does not send emails, place trades, or mutate external systems.
6. **Vector-store graduation past ~50M chunks** — Postgres-everything is the MVP store; Turbopuffer/Qdrant graduation is future work.
7. **Image *content* understanding** — we OCR scanned documents and embed page images. We do *not* claim to recognize photographic content like "this site photo shows unbraced formwork."
8. **Multi-tenant isolation** — same shape as permissions; future work.
9. **Cross-lingual atomic-unit extraction** — entity mentions are multilingual; clause/transaction typing is English-only initially.
10. **Live source connectors** (Slack, SharePoint, Gmail sync) — file-ingestion only; connectors are deployment integration, not architecture.

## How to read the rest of these docs

| Doc | Role |
|---|---|
| [`architecture.md`](architecture.md) | Locked formal spec — layers, indexing/query pipelines, storage stack, eval design |
| [`ui_design.md`](ui_design.md) | Locked UI design + demo flow + per-screen reference |
| [`gaps_design.md`](gaps_design.md) | 9 detailed designs (aggregation · conflicts · doc chains · feedback · citations · vocabulary · lineage · conversational context · layered config) |
| [`build_tracker.md`](build_tracker.md) | Gate-by-gate build discipline (G1 plan → G5 verify per phase) + Git workflow |
| [`walkthrough.md`](walkthrough.md) | Teaching doc — one doc's journey through ingest, one query's journey through retrieval |
| [`scenarios.md`](scenarios.md) | 8 enterprise stress-tests with verdicts |
| [`red_team.md`](red_team.md) | Adversarial review of the architecture |
| [`citations_audit.md`](citations_audit.md) | Every cited paper/product verified real |
| [`competitive_audit.md`](competitive_audit.md) | 2026 SOTA sweep — Hebbia, Glean, NotebookLM, OpenAI Files, Onyx, DSPy, Search-o1, Mem0 |
| [`scale_perf_audit.md`](scale_perf_audit.md) | Honest scale/cost/latency at 10K / 100K / 1M / 10M / 100M docs |
| [`../prototype/`](../prototype/) | Clickable HTML prototype of all 10 UI surfaces |
| [`../CONTRIBUTING.md`](../CONTRIBUTING.md) | How to contribute · Git workflow · gate discipline |
