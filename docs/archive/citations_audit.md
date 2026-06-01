# Citations Audit — Grounding Every Claim

**Date:** 2026-05-21
**Purpose:** verify every cited paper, product, and pattern in the architecture is real and accurately characterized; flag every numerical default that is *our choice* rather than a research-grounded value.
**Methodology:** for each citation in `architecture.md`, `gaps_design.md`, `red_team.md`, and `walkthrough.md`, run a targeted web search and confirm (a) the paper/product exists, (b) the claim we attached to it is accurate, (c) the link resolves.
**Conclusion in advance:** 17 of 17 papers/products checked are real and the claims attached to them are accurate. A handful of numerical defaults (auto-promotion thresholds, source-authority scale, doc-chain detection heuristics) are *our choices* and are now explicitly labeled as such — not invented citations, just operating defaults.

---

## 1. Papers and products verified (with attached claim accuracy)

| Citation | Status | Claim we attached | Accurate? | Source |
|---|---|---|---|---|
| **HippoRAG 2** (arxiv 2502.14802) | ✅ REAL | PPR-based multi-hop, dual-node KG with passage+phrase nodes, 7-pt F1 gain over embedding retrievers | ✅ exactly matches abstract | [arxiv](https://arxiv.org/abs/2502.14802) · [github](https://github.com/OSU-NLP-Group/HippoRAG) |
| **ConflictRAG** (arxiv 2605.17301) | ✅ REAL — submitted May 17, 2026 | Conflict detect-classify-resolve; authority + recency dominate | ✅ paper introduces two-stage detection (88.7% F1) + Entropy-TOPSIS source credibility + CARS metric; we adopted the same resolution principle | [arxiv](https://arxiv.org/abs/2605.17301) |
| **CSR-RAG** (arxiv 2601.06564) | ✅ REAL — submitted Jan 10, 2026 (Singh, Boškov, Drabeck, Gudal, Khan) | Hybrid retrieval for enterprise text-to-SQL; 40% precision, 80% recall, 30ms latency | ✅ exact figures match | [arxiv](https://arxiv.org/abs/2601.06564) |
| **HalluGraph** (arxiv 2512.01659) | ✅ REAL | KG-alignment hallucination detection, AUC 0.94 on Legal Contract QA vs. 0.60 BERTScore baseline | ✅ paper reports AUC 0.94 / 0.84 / ~0.89 on three benchmarks | [arxiv](https://arxiv.org/abs/2512.01659) |
| **Mistral OCR 3** | ✅ REAL — released Dec 2025 | Top-quality scanned-doc parser; multilingual + handwriting; ~$2/1K pages | ✅ 88.9% handwriting, 96.6% tables (double-digit lead vs Azure/Textract); $2/1K with 50% batch discount | [Mistral news](https://mistral.ai/news/mistral-ocr-3) · [InfoQ Jan 2026](https://www.infoq.com/news/2026/01/mistral-ocr3/) |
| **Gemini Embedding 001** | ✅ REAL | #1 commercial MTEB English at 68.32; multimodal 3072d | ✅ confirmed; lead has narrowed to Voyage-3.1 (high 67s) but still #1 | [Google Developers](https://developers.googleblog.com/en/gemini-embedding-available-gemini-api/) · [Awesome Agents MTEB April 2026](https://awesomeagents.ai/leaderboards/embedding-model-leaderboard-mteb-april-2026/) · [arxiv 2503.07891](https://arxiv.org/pdf/2503.07891) |
| **DARE** (Springer 2026) | ✅ REAL — March 2026 | Dialectical adversarial framework for evidence-aware RAG conflict resolution | ✅ paper proposes structured cross-examination of claims; integrates reliability into reasoning, not just final weighting | [Springer chapter](https://link.springer.com/chapter/10.1007/978-3-032-21300-6_27) |
| **Cohere Rerank 3.5** | ✅ REAL | Top cross-encoder reranker for production RAG | ✅ confirmed; 80–150 ms p50 on chunks <2K tok, 200ms+ p99 on chunks >3K tok; 23.4% better than hybrid on internal eval | [Cohere docs](https://docs.cohere.com/docs/rerank) · [VentureBeat](https://venturebeat.com/ai/cohere-rerank-3-5-is-here-and-its-about-to-change-enterprise-search-forever) |
| **Anthropic Contextual Retrieval** (Sep 2024) | ✅ REAL | 67% retrieval-failure reduction *with rerank*; 49% reduction without | ✅ exact numbers match; we correctly stated "67% with rerank" | [Anthropic news](https://www.anthropic.com/news/contextual-retrieval) · [InfoQ Sep 2024](https://www.infoq.com/news/2024/09/anthropic-contextual-retrieval/) |
| **RAPTOR** (arxiv 2401.18059, ICLR 2024) | ✅ REAL | Recursive hierarchical summarization tree; abstract-level retrieval; ICLR 2024 | ✅ Stanford team (Sarthi, Abdullah, Tuli, Khanna, Goldie, Manning); 20% improvement on QuALITY with GPT-4 | [arxiv](https://arxiv.org/abs/2401.18059) · [github](https://github.com/parthsarthi03/raptor) |
| **Docling** (IBM) | ✅ REAL | Layout-aware digital PDF parser, open source, MIT | ✅ IBM Research Zurich, now Linux Foundation AI & Data; TableFormer trained on 1M+ tables | [IBM Research](https://research.ibm.com/blog/docling-generative-AI) · [Docling tech report](https://arxiv.org/pdf/2408.09869) |
| **CRAG** (arxiv 2401.15884) | ✅ REAL | Confidence-gated corrective retrieval with three actions (correct / incorrect / ambiguous) | ✅ matches paper's three-action design | [arxiv](https://arxiv.org/abs/2401.15884) |
| **Astute RAG** (arxiv 2410.07176) | ✅ REAL | Defensive generation that surfaces conflicts; iterative source-aware consolidation | ✅ Astute RAG adaptively elicits LLM internal knowledge + iteratively consolidates with external evidence + finalizes per reliability | [arxiv](https://arxiv.org/abs/2410.07176) |
| **ColPali** (arxiv 2407.01449) | ✅ REAL | Visual document retrieval via VLM multi-vector embeddings + late interaction | ✅ matches paper's contribution; ViDoRe benchmark introduced | [arxiv](https://arxiv.org/abs/2407.01449) |
| **Late chunking (Jina)** (arxiv 2409.04701) | ✅ REAL | Encode full doc with long-context model then pool chunk embeddings from token outputs | ✅ paper shows 2.70%–3.63% relative improvement vs. naive chunking | [arxiv](https://arxiv.org/pdf/2409.04701) |
| **CUAD** (Hendrycks et al., arxiv 2103.06268) | ✅ REAL | Public legal corpus: 13K+ labels, 510 commercial contracts, 41 clause types, expert-annotated | ✅ exact; CC BY 4.0; maintained by The Atticus Project | [Atticus Project](https://www.atticusprojectai.org/cuad/) · [arxiv](https://arxiv.org/pdf/2103.06268) |
| **ParadeDB pg_search** | ✅ REAL | Tantivy-based BM25 inside Postgres; production-ready | ✅ 265–500× faster than native PG FTS at 10M–100M rows; Tantivy is the same engine used by Quickwit | [ParadeDB docs](https://www.paradedb.com/learn/search-in-postgresql/bm25) · [Tiger Data benchmark](https://www.tigerdata.com/blog/pg-textsearch-bm25-full-text-search-postgres) |

**Summary:** every paper and product cited in our architecture is real, current (most published 2024–2026), and the claim attached to each citation is accurate to the source. **No fabricated citations. No exaggerated claims.**

---

## 2. Concept precedents — what we composed vs. what we invented

This section is the honest one. The architecture isn't a single new paper; it's a *composition* of many published patterns. Some compositions are standard; some are our adaptation. I'm explicit about which is which.

### 2.1 Established patterns we adopted directly

| Architecture element | Established precedent | Our role |
|---|---|---|
| Multi-resolution storage + parallel retrieval channels | RAG-Fusion (arxiv 2402.03367), Glean hybrid retrieval, LongRAG (arxiv 2406.15319) | Compose 10 channels and fuse with RRF; standard pattern |
| HyDE + Step-Back + Query2Doc + Tree-of-Clarifications | Each is a separate published technique | Gate by intent classifier; standard |
| Contextual Retrieval (chunk-context prefix) | Anthropic Sep 2024 | Adopt as-is |
| RAPTOR tree | Sarthi et al. ICLR 2024 | Adopt as-is at L1d |
| HippoRAG-2 PPR | OSU NLP Group | Adopt at L6 |
| ColPali visual retrieval | Faysse et al. 2024 | Adopt at L1c (Wave C) |
| CRAG confidence gate + IRCoT escalation | Yan et al. + Trivedi et al. | Compose; we cap IRCoT at 2 hops (red-team finding) |
| Astute RAG defensive generation | Wang et al. Oct 2024 | Adopt at generation step |
| HHEM-2.1 + HalluGraph as faithfulness gate | Vectara + HalluGraph paper | Two-judge ensemble; HHEM is standard; HalluGraph is Wave C |
| Identity resolution: deterministic → embedding → LLM-judge → union-find | Classic ER literature (Christen 2012); modern LLM-judge versions in 2024–2025 papers | Standard pattern at L4 |
| Doc chain via In-Reply-To headers | [RFC 5322](https://www.rfc-editor.org/rfc/rfc5322) (Internet Message Format) | Standard; not a research claim |
| Aggregation `Q` mode (text-to-SQL hybrid retrieval) | CSR-RAG (arxiv 2601.06564), Azure AI Search agentic retrieval, Hebbia Matrix output | Compose grammar + execution path; pattern is established |
| Conflict detection + authority/recency resolution | ConflictRAG (arxiv 2605.17301), DARE (Springer 2026), κ-RRSS (arxiv 2410.22954) | "Authority and recency dominate" is the literature consensus; we adopt directly |
| Postgres-everything (pgvector + pg_search) | OpenAI, Supabase, Neon, ParadeDB customer references | Standard production stack |
| Span-grounded citations | Anthropic Citations API (Jan 2025); NotebookLM source-grounding | Standard product pattern |

### 2.2 Patterns we *composed* — research-grounded but our specific arrangement

| Architecture element | Existing precedents we composed from | What's ours |
|---|---|---|
| **L2b emergent schema** (per-doc open extraction → cross-doc clustering → auto-promotion) | LKD-KGC schema induction from doc summaries; EDC open-IE; NeOn-GPT prompt-driven ontology induction; QueryForm zero-shot schema-aware extraction; LLMs4Life | The composition — running open extraction *with auto-promotion thresholds* and integrating into a typed schema view — is our adaptation. **The concept is research-grounded; the pipeline specifics are ours.** ([survey](https://arxiv.org/pdf/2510.20345), [PARSE](https://arxiv.org/html/2510.08623v1)) |
| **Q-mode grammar** (JSON plan → SQL → templated answer + audit-artifact citation) | CSR-RAG (the architecture), Azure agentic retrieval (the parallel sub-query pattern), Hebbia Matrix (the spreadsheet-shaped output) | Our specific grammar shape (`from / join / filter / group_by / aggregate / set_op`) and "templated answer cites audit artifact" rendering is our composition |
| **Universal citation envelope** (10 modality types under one schema) | Anthropic Citations API (PDF spans only), NotebookLM source grounding (UX shape only) | The 10-modality polymorphic envelope is our design; no single paper defines all 10 types under one schema |
| **Severity-classifier feedback routing** | Standard product feedback patterns (Glean, NotebookLM, customer-support ML systems) | Our specific routing logic (`scope='extraction' → high-effort re-extraction with Gemini Pro`) is composed from standard practice; no specific citation |
| **Doc-chain L0.5 as first-class layer** | Email threading is RFC 5322 (standard); contract amendments are legal practice; drawing revisions are engineering practice; LinkedIn customer-service RAG-KG uses thread-aware retrieval (arxiv 2404.17723) | Treating "chain" as a first-class storage layer with `current_version_id` + role enum is our design; standard pattern but not specifically cited |

### 2.3 Our numerical defaults — choices, not citations

These are the values I chose. They are **operational defaults**, not research-grounded numbers. Should be flagged in the docs as "configurable defaults, picked as sensible starting points."

| Default | Value | Source |
|---|---|---|
| Auto-promotion: prevalence threshold | **80%** | Our choice. Literature uses prevalence/frequency thresholds, but no canonical value. 80% is a sensible "most docs of this type have this field" cut-off. Configurable. |
| Auto-promotion: stability threshold | **0.9** | Our choice. Measures how much the inferred schema has stopped changing across the last K updates. No published value; reasonable starting point. |
| Auto-promotion: value-type confidence | **0.9** | Our choice. Combined enum/text/number/date classifier confidence. |
| Auto-promotion: doc-type sample size | **n ≥ 20** | Our choice. Below 20, inferences are too noisy. Standard "rule of thumb" not from a paper. |
| Source-authority scale | **1.0 (audited) → 0.2 (handwritten note)** | Our choice. Literature establishes "authority dominates" (ConflictRAG, κ-RRSS) but no canonical numerical scale. Our scale + per-doc-type defaults are *defaults*; user/admin override per-doc. |
| Authority gap to dominate | **≥ 0.3** | Our choice. "Authority dominates if gap is meaningful" is from the literature; the 0.3 cut-off is our default. |
| Doc-chain: title similarity threshold | **≥ 0.7** | Our choice. Heuristic. Same vibe as document-clustering thresholds in classic IR but no specific paper for amendment detection at exactly 0.7. |
| Doc-chain: sender/recipient overlap | **≥ 0.5** | Our choice. Heuristic for email-thread fallback when In-Reply-To is broken. |
| Doc-chain: detection LLM-judge cost | **~$0.001/doc** | Our estimate based on Gemini Flash pricing. |
| Anomaly rarity threshold | **0.95** | Our choice. P99-ish cut-off for "unusually rare." Configurable per query. |
| Cardinality budget for Q mode | **10M rows** | Our choice. Pre-flight EXPLAIN refuses queries that would scan more. |
| Q-mode SQL timeout | **30s** | Our choice. Standard analytics timeout. |
| Join depth cap in Q mode | **≤ 3 default, warn at 4, refuse at 5** | Our choice. Standard query-builder hygiene. |
| Severity classes for corrections | `blocker / important / minor / enhancement` | Our choice. Standard product severity taxonomy. |
| Feedback regression-set CI gate | **100% pass on previously-fixed corrections** | Our choice. Strict by design; configurable. |
| IRCoT escalation cap | **2 hops** (was 4) | Our choice based on red-team latency analysis. CRAG paper does not specify a hop cap. |
| Rerank top-K | **50** (was 200) | Our choice. RankZephyr (arxiv 2312.02724) and MS MARCO replications show diminishing returns past ~50; our choice is consistent with literature but not a specific citation. |
| 10-channel parallel retrieval | **10** | Specific count is our composition. Each channel maps to a published technique; the choice of "these 10 in parallel" is our composition. |
| 12 planner modes (E/F/S/H/T/M/G/D/C/A/Q/K) | **12** | Our composition of established retrieval primitives + two new modes (Q, K). |

**Implication:** in the writeup, every one of these should appear with a parenthetical *"default; configurable"* — not presented as research-validated values. The architecture inherits its *patterns* from literature; the *specific operating points* are calibrated defaults that we (and any user) will tune from observed eval performance.

---

## 3. Pieces I checked for honesty — and they hold up

### 3.1 The "schema emerges from data" claim

I worried this might be overclaim. **It is not.** The pattern exists in published research under multiple names:

- **LKD-KGC** (LLM-driven knowledge graph construction with schema induction from doc summaries)
- **EDC** (Extract-Define-Canonicalize framework for open IE)
- **NeOn-GPT** (end-to-end prompt-driven ontology induction with adaptive refinement)
- **QueryForm** (zero-shot extraction via prompts encoding schema + entity types)
- **PARSE** (LLM-driven schema optimization for reliable entity extraction; arxiv 2510.08623)
- **LLMs4Life** (adaptive schema refinement)

What's ours: the *composition* of (a) per-doc open extraction with values + descriptions + doc-type proposal, (b) cross-doc clustering by name+description embeddings, (c) value-type induction from observed values, (d) **auto-promotion with explicit thresholds**, (e) integration into a *typed schema view* that the user edits or overrides. This is research-grounded *and* a real design contribution. We can present it as such without overclaiming.

Source: [LLM-empowered knowledge graph construction survey, arxiv 2510.20345](https://arxiv.org/pdf/2510.20345); [PARSE, arxiv 2510.08623](https://arxiv.org/html/2510.08623v1).

### 3.2 The "70% of enterprise RAG fails before production" claim in red-team intro

This is widely cited in 2026 industry reviews. Verified in [DEV.to article](https://dev.to/gabrielanhaia/70-of-enterprise-rag-deployments-fail-before-production-heres-what-kills-them-26ml). It's a soft number (the source itself doesn't trace to a primary study) but it's the consensus framing in the industry. We can cite it as "industry framing" not as a hard study.

### 3.3 The "Lexis+ 17%, Westlaw 33% hallucination" Stanford RegLab claim

Real. [Magesh et al., arxiv 2405.20362](https://arxiv.org/abs/2405.20362), "Hallucination-Free? Assessing the Reliability of Leading AI Legal Research Tools". Numbers we cited match the abstract.

### 3.4 The "edge case 1" and "edge case 2" retrievability claims

These are *design claims* — we say "the architecture can solve this" but we haven't *empirically verified* it on a corpus. **For the demo, this is fine** (we'll run the actual eval). For the writeup, we should be honest: *"we have not yet empirically tested these on the demo corpus; this is the pipeline that should solve them based on the literature on Contextual Retrieval + RAPTOR + cross-encoder rerank for case 1, and clause-level extraction + rarity scoring for case 2."*

---

## 4. Things we *don't* cite but should consider citing

A few good references I'd add to the writeup if asked:

- **Hebbia Matrix** (multi-agent spreadsheet-shaped output): a real production system that's spiritually close to our Q-mode templated-answer-with-row-list output. Public blog post: [hebbia.com Matrix](https://www.hebbia.com/blog/divide-and-conquer-hebbias-multi-agent-redesign).
- **Harvey BigLaw Bench Retrieval**: the legal-RAG production system that does what we'd do for the CUAD half of the demo. [harvey.ai BigLaw Bench](https://www.harvey.ai/blog/biglaw-bench-retrieval).
- **Glean Knowledge Graph**: production reference for L4 + L5 + cross-app threading.
- **PARSE (arxiv 2510.08623)**: directly relevant to L2b — LLM-driven schema optimization for reliable entity extraction. Worth adding to the L2b citation list.
- **κ-RRSS / source-reliability RAG (arxiv 2410.22954)**: Reliable RAG. Worth adding to Design 2 citation list.

---

## 5. Things I am explicitly *not* claiming, despite the architecture having them

To preempt reviewer pushback, the following are *deliberate descopes*. Stated as scope, not as solved problems:

1. **Permissions / ACL** — Wave C. We say so. ([scenarios.md cross-cutting patterns](scenarios.md))
2. **Native CAD / DWG / DICOM / BIM geometry queries** — out of MVP. ColPali handles visual layout, not geometry.
3. **Real-time streaming sources** — KB ≠ OLAP, by design.
4. **Bi-temporal validity (AS-OF queries on facts)** — Wave B/C; doc chains in Design 3 handle *some* temporal questions (latest revision, supersession) but not arbitrary `AS OF '2023-06-15'`.
5. **Agentic actions** — read-only by design. We retrieve and reason; we do not send emails, place trades, or mutate external systems.
6. **Vector-store graduation past ~50M chunks** — Postgres is the MVP store; Turbopuffer/Qdrant graduation cited as future work.
7. **Image content understanding beyond layout** — not in scope. We can OCR scanned documents and embed page images; we cannot say "this photo shows unbraced formwork."
8. **Multi-tenant isolation** — Wave C.
9. **Multilingual L3 atomic-unit extraction** — Wave C. L2 mentions are multilingual via Gemini Embedding; L3 clause/transaction typing is English-only for demo.

---

## 6. Action items from this audit

| Action | What to change | Where |
|---|---|---|
| 1 | Mark numerical defaults as "configurable" | architecture.md §1 A.2 (auto-promotion thresholds), Design 2 (authority scale + gap), Design 3 (doc-chain heuristics), Design 4 (severity classes), §9 (CI gate values) |
| 2 | Add PARSE (arxiv 2510.08623) to L2b citation list | architecture.md §2 L2b reference list |
| 3 | Add κ-RRSS (arxiv 2410.22954) to Design 2 reference list | gaps_design.md §Design 2 References |
| 4 | Soften edge-case traces from "is solved" to "is designed to solve, awaiting eval" | architecture.md §10 |
| 5 | Add the "things we explicitly don't claim" list to the writeup-facing README | README.md "out of scope by design" |
| 6 | Verify all link URLs in architecture.md References resolve | one-shot check |

These are small surgical edits; do them in the next pass.

---

## 7. The honest one-liner

**The architecture is not novel.** It is a composition of 17+ established research patterns and production systems, plus our specific operating defaults. Every cited paper and product is real and accurately characterized. The places where we composed or chose defaults are flagged in §2.2 and §2.3 of this audit, and will be flagged in the architecture docs themselves in the next edit pass.

If asked *"what's actually new here?"* the truthful answer is:

> *"The composition is mine; the parts are not. The L2b emergent-schema pipeline with auto-promotion, the Q-mode planner with templated aggregation answers, the universal citation envelope, and the L0.5 doc-chain layer are specific designs I built on top of established research. The 10-layer storage, 10-channel parallel retrieval, conflict detection, and feedback loop follow well-known patterns from CSR-RAG, ConflictRAG, RAPTOR, HippoRAG, Contextual Retrieval, CRAG, and Astute RAG. Every numerical threshold in the architecture is a calibrated default — not a research-validated value — and is configurable per workspace. I prioritized correctness over novelty: this design is what the 2026 literature says works, composed coherently."*

That's the answer. It's defensible. It is grounded in truth.
