# Problem Statement — Emerging KB

A domain-agnostic enterprise knowledge base. Upload heterogeneous documents (PDFs both digital and scanned, spreadsheets, images, emails). Ask natural-language questions and get cited answers. The system auto-discovers structure as data arrives; user-defined schemas are a *view* on top, never a precondition.

This document is the public technical brief. See [`architecture.md`](architecture.md) for how the system works today, and [`README.md`](README.md) for the full docs index (current + historical).

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
12. **Demos at scale honestly** — fully working on the 55-doc demo corpus; honest about cost/latency tradeoffs at 100K / 1M / 10M / 100M (see [`scale_perf_audit.md`](scale_perf_audit.md)).

## Demo corpus

The shipped demo is a **finance workspace of 55 documents** — bank statements, KYC records, a loan amended twice, SEC-style filings, wire-transfer emails, treasury memos, plus scanned slips, digital-PDF agreements, and spreadsheets. It is committed as a pre-extracted seed (`demo-corpus/seed/`), so a fresh clone shows a full, working system in minutes; the source files live in `demo-corpus/domains/finance/`. It exercises every modality (markdown · email · digital PDF · scanned/OCR PDF · xlsx), the loan-amendment conflict case, cross-format answers, and two safety refusals.

The architecture is **domain-agnostic** — during development it was also run across legal, construction, healthcare, mining, and government corpora (those eval runs are preserved under [`archive/`](archive/) and `eval_audit/`). 55 docs is small enough to fully demo + audit, large enough to exercise the moves that only matter at scale: cross-doc identity resolution, topic clustering, and schema emergence.

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

See [`README.md`](README.md) for the full index. The current set:

| Doc | Role |
|---|---|
| [`architecture.md`](architecture.md) | How the system works today — ingest · query · UI · stack — plus an honest scale review |
| [`scale_perf_audit.md`](scale_perf_audit.md) | Deep scale/cost/latency analysis (10K → 100M docs) + enterprise upgrade paths |

The design-phase specs (`architecture_design_spec.md`, `ui_design.md`, `gaps_design.md`), the gate-by-gate build log (`build_tracker.md`), the API contract, the audits, the clickable prototype, and the per-domain eval runs are preserved under [`archive/`](archive/). Contributor workflow: [`../CONTRIBUTING.md`](../CONTRIBUTING.md).
