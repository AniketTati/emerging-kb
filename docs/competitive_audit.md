# Competitive Audit — Is There Anything Better in 2026?

**Date:** 2026-05-21
**Purpose:** the moneyball pass. For each major 2026 production system and research pattern that competes with or extends what we've designed, honestly answer: **is it better than us, and if so, why and how should we react?**
**Methodology:** web sweep across 12 production systems and 8 research patterns; cross-reference benchmark numbers; identify capability gaps; recommend deliberate adoption, deliberate rejection, or "Wave B addition with rationale."

---

## TL;DR

| Verdict | What it covers |
|---|---|
| **Genuinely behind** | Multi-agent orchestration (Hebbia pattern), agentic-loop retrieval (Search-o1 / ReAct), automatic prompt optimization (DSPy), spreadsheet-shaped batch output (Hebbia Matrix UX) |
| **Matched** | Retrieval primitives, contextual retrieval, RAPTOR, HippoRAG, faithfulness gating, conflict detection, citation grounding, hybrid Postgres stack |
| **Ahead (or on par with no public peer)** | L2b emergent-schema with auto-promotion, doc-chain L0.5 first-class layer, universal citation envelope across 10 modalities, structured feedback→regression-set→CI gate |
| **Deliberately not pursuing** | Long-context as RAG substitute, agentic-as-default, live source connectors, knowledge-graph-only ranking, multi-tenant ACL, agentic actions |

**Net assessment:** the architecture is at or near 2026 SOTA on the *primitives*. The three production patterns we are genuinely behind on (multi-agent orchestration, opt-in agentic loops, prompt optimization) are real 2026 advancements but each comes at a 3–15× token cost and require careful positioning. I recommend adding all three to **Wave B as opt-in modes**, not the default. The "we are not agentic by design" framing is defensible and intentional, but should be stated explicitly in the writeup.

---

## 1. Production systems we compete with

### 1.1 Hebbia Matrix — THE strongest competitive analog

**What it is:** AI research analyst engine for finance and law. Used by 30%+ of world's largest asset managers and elite law firms (Ropes & Gray, etc.). Funded by a16z. ([Hebbia product](https://www.hebbia.com/product), [a16z investment thesis](https://a16z.com/announcement/investing-in-hebbia/))

**What it does that we don't:**

1. **Multi-agent orchestration with model routing.** Matrix breaks down complex queries into structured steps and runs *multiple agents in parallel*, routing each subtask to the most suitable model. One agent does semantic search, another does tabular extraction, a third does legal terminology, etc. ([Matrix multi-agent redesign](https://www.hebbia.com/blog/divide-and-conquer-hebbias-multi-agent-redesign))

2. **Spreadsheet-as-primary-interface.** Rows = documents (e.g., 10,000 contracts), columns = questions (e.g., "Does this contain a non-compete?"). Every cell is one LLM evaluation. Each cell has a "Verifiable Fact Layer" with clickable citation to source PDF.

3. **Batch evaluation as default UX.** You don't ask one question at a time — you batch-evaluate a question across N docs in one operation.

**Why this matters:** Hebbia is the *legal/finance industry standard* for the exact use case our CUAD half of the demo addresses. Anyone who knows the space will ask "how is this different from Hebbia?"

**Honest answer:**

- We chose **single-question deep retrieval** as the primary UX (chat); Hebbia chose **batch question-across-many-docs** (spreadsheet) as the primary UX. Both are valid; we make different tradeoffs.
- Our Explore › Atomic Units page is *spiritually* the same as Hebbia's matrix (one row per atomic unit, columns are parameters), but it's a *read* view of pre-extracted data, not a live batch-LLM-evaluation interface.
- We are **not multi-agent**; we use parallel retrieval channels (10) under a single planner. Cheaper and more deterministic, less flexible.

**Recommendation:** add to **Wave B**:
- **Batch query mode** in Explore — pick a doc-type cohort + a question, run the question across all docs in parallel, render as a spreadsheet with cell-level citations. This is a small UX addition that closes the most visible Hebbia gap.
- **Multi-agent decomposition for Q-mode complex queries** — when intent classifier flags "multi-criteria + multi-step", planner spawns parallel sub-plans rather than single Q. Cost cap configurable.

### 1.2 Glean — closest production analog at our architectural shape

**What it is:** Enterprise search with a permissions-aware knowledge graph. $7.2B valuation, $200M ARR (doubled in 9 months). 100+ connectors (live sync from SaaS apps). Agentic Engine 2 + Canvas co-authoring UI. ([Glean KG](https://www.glean.com/resources/guides/glean-knowledge-graph), [Glean RAG perspectives](https://www.glean.com/perspectives/best-rag-features-in-enterprise-search))

**What it does that we don't:**

1. **100+ live connectors.** Slack, SharePoint, Google Drive, Salesforce, Jira, Confluence, Notion, GitHub, etc. The system continuously ingests the org's *live* data, not file uploads.

2. **Permissions-aware throughout.** Row/field/entity-level ACL is integrated into retrieval — users only see what they're allowed to see.

3. **Graph-informed ranking** with recency, popularity, organizational proximity as signals beyond pure semantic similarity.

4. **Agentic Engine 2 + Canvas** — built-in agentic workflows + collaborative document authoring.

**Honest gap:** Glean is a **production enterprise product**; this is a **design submission with a 100-doc demo corpus**. Apples-to-oranges. Their connectors and permissions are deployment-engineering, not architectural advantages.

**What we share:** knowledge graph (our L4 + L5 + L6), hybrid retrieval (chunks + entity-graph), citation grounding.

**What we do that they don't (publicly):** schema-emerges-from-data with auto-promotion (Glean talks about KG but not about *inferring the schema itself* bottom-up). Universal citation envelope across 10 modalities (Glean cites docs, not modality-specific).

**Recommendation:** **no change to the architecture.** Position deliberately: "we are file-ingestion KB; permissions + connectors are deployment integrations, not architectural changes." Cite Glean as the production exemplar for the connector/permissions deployment path.

### 1.3 NotebookLM — the source-grounding pattern at consumer scale

**What it is:** Google's research/thinking partner. Source-grounding pattern (not RAG; explicit framing). Generates briefing docs, FAQs, mind maps, audio overviews. ([NotebookLM evolution 2023-2026](https://medium.com/@jimmisound/the-cognitive-engine-a-comprehensive-analysis-of-notebooklms-evolution-2023-2026-90b7a7c2df36))

**What it does that we don't:**

1. **Audio overview** (NotebookLM-style podcast generation). We have this as Wave C, not built.
2. **Tight Google Workspace integration.** We're standalone.

**What we share:** source grounding, generated artifacts (briefing/FAQ/mind map), refusal-on-no-evidence.

**Honest gap:** none architectural. NotebookLM is **per-source-collection** (small, curated); we are **per-corpus** (large, heterogeneous). Different scale.

**Recommendation:** **no change.** Cite NotebookLM as the source-grounding inspiration. Audio overview stays Wave C.

### 1.4 OpenAI File Search / Anthropic File Search — built-in RAG, zero infrastructure

**What it is:** Vector-search-as-a-service from the LLM providers. Upload a file → ask questions → cited answers. Zero infrastructure.

**OpenAI specifics:** Assistants API File Search; will move to Responses API by Aug 26, 2026 ([OpenAI docs](https://developers.openai.com/api/docs/assistants/tools/file-search)).

**What they do that we don't:**

1. **Effectively zero engineering to set up.**
2. **Continuous improvements as the vendor upgrades the underlying retriever.**

**What we do that they don't:**

1. **Domain-agnostic schema emergence.** They give you Q&A; they don't extract typed entities, identity-resolve, build relationship graphs, etc.
2. **Multi-resolution storage with 10 layers.** Theirs is chunk-and-embed; we have L0 raw → L7 communities.
3. **Aggregation Q-mode.** They can't compute "total spend by vendor in Q2".
4. **Conflict detection, source authority, doc chains, feedback loop, multi-modal citations.** None of these are in vendor RAG.
5. **Transparency** — we expose the planner JSON, plan inspector, audit log. Vendor RAG is a black box.
6. **Control over the stack** — we own the parser choice, embedder choice, reranker, faithfulness gate. Vendor RAG locks you into their choices.

**Honest gap:** they win on time-to-first-answer for prototype usage. We win on every dimension that matters for an enterprise KB.

**Recommendation:** **no change.** Address this directly in the writeup: *"if you only need Q&A over a small fixed set of files, use OpenAI/Anthropic file search. The system we built is for the case where you also need extraction, identity resolution, schema evolution, aggregation, audit, and control over the stack."*

### 1.5 Onyx — open-source enterprise search

**What it is:** Open-source enterprise RAG platform. Reports 64–76% win rate vs ChatGPT/Claude/Notion AI on 220K-document workplace-question quality. ([Onyx insights](https://onyx.app/insights/enterprise-rag-platforms-2026))

**Why it's relevant:** it's an open-source production reference for what we're building. Their architecture choices map closely to ours (Postgres + vector + reranking + connectors).

**What they do that we don't:** connectors (same as Glean).

**Recommendation:** **add Onyx to architecture.md §16 References** as a production reference. Their benchmarks are informative for our eval target setting.

### 1.6 LlamaIndex — top RAG framework 2026

**What it is:** Open-source RAG framework. 2026 enterprise comparison reports 35% better retrieval accuracy and 40% faster document retrieval than LangChain. ([alphacorp.ai 2026](https://alphacorp.ai/blog/rag-frameworks-top-5-picks-in-2026))

**Why it's relevant:** the 2026 enterprise pattern is "LlamaIndex for ingestion + retrieval, LangChain/LangGraph for orchestration + agents, evaluation layer on top." We're not using either.

**Why we chose not to:** we're building from primitives because we want control + audit trail + a small, defensible dependency surface. LlamaIndex would abstract away half of what makes our architecture defensible (the per-step transparency). LangChain has well-documented issues with magic, breaking changes, and reliability in production.

**Recommendation:** **no change.** Stay framework-light. Explicitly state in writeup: *"we considered LlamaIndex and chose primitives; rationale: transparency, audit, dependency control. The trade-off is more code, which we accept."*

---

## 2. Research patterns we should consider

### 2.1 Agentic RAG / Search-o1 / ReAct (the real gap)

**What it is:** Retrieval-as-a-tool. Model plans → retrieves → reflects → re-retrieves → answers. Plan-and-execute or ReAct or Search-o1 patterns. ([Agentic RAG patterns 2026](https://www.digitalapplied.com/blog/agentic-rag-patterns-multi-step-reasoning-guide), [Search-o1 arxiv 2501.05366](https://arxiv.org/pdf/2501.05366))

**What we do today:** CRAG confidence gate with IRCoT escalation, capped at 2 hops. This is a *reactive* simplification — escalate only if top-1 is weak.

**What the 2026 SOTA does:** model **proactively** decides whether to retrieve, what to retrieve, when to stop. Three to ten times the tokens of classic RAG, but 5–15% quality lift on complex queries.

**Cost reality:** "multi-step reflection loops typically consume three to ten times the tokens of classic RAG. The quality lift only justifies that spend on specific workload shapes. Budget for 15× token overhead before you start." ([Agentic RAG 2026 patterns](https://www.digitalapplied.com/blog/agentic-rag-patterns-multi-step-reasoning-guide))

**Recommendation:** **Wave B addition — opt-in `deep_research` mode.**

- Intent classifier gains an output: `complex_research` (long, multi-criteria, exploratory query like *"What patterns of vendor failure correlate with regulatory complaints across our last 3 years?"*)
- When this intent fires AND the user has not toggled off "extended cost", run an agentic loop: plan → retrieve → reflect → re-retrieve, capped at 5 hops or a cost ceiling (e.g., $0.10/query default).
- Audit log records every step (we already do this for IRCoT; just extend).
- Default OFF for the demo. Show as a feature in the writeup with cost rationale.

This closes the agentic gap without sacrificing the cost/latency/determinism story for the typical query.

### 2.2 Multi-agent orchestration (the Hebbia gap)

**What it is:** orchestrator agent owns conversation context, spawns parallel sub-agents per sub-task, each sub-agent uses a task-specific model. Orchestrator uses capable model; workers use cheaper ones. **Cost savings 40–60% reported.** ([Multi-agent orchestration patterns 2026](https://beam.ai/agentic-insights/multi-agent-orchestration-patterns-production))

**What we do today:** single planner emits a multi-mode plan, retrieval channels run in parallel (10 channels). This is **parallel retrieval**, not **multi-agent**. The distinction:

- Our 10 channels are *the same query* hitting different indexes simultaneously.
- A multi-agent system **decomposes the query** into sub-questions, dispatches each to a specialized agent (which may invoke retrieval), recomposes.

**Recommendation:** **Wave B addition — multi-agent decomposition mode.**

- Triggered for queries the planner identifies as decomposable (`set_operation`, `aggregation` with multiple group_bys, multi-hop with `>` 3 entity hops).
- Planner emits a *list of sub-plans* instead of a single plan.
- Each sub-plan runs in parallel; results are joined per the declared join key.
- Each sub-agent uses Gemini Flash (cheap); orchestrator uses Gemini Flash (or Pro for tricky synthesis).
- Cost still bounded — multi-agent of 4 sub-plans at Flash is ~$0.005 each = $0.02 total. Same envelope as IRCoT escalation.

This closes the multi-agent gap while keeping cost predictable.

### 2.3 DSPy — automatic prompt optimization

**What it is:** Stanford NLP framework that replaces hand-written prompts with programmatic optimization. v2.5 supports joint optimization across multiple modules. Production users report measurable quality improvements. ([DSPy v2.5 guide](https://myengineeringpath.dev/tools/dspy-guide/), [DSPy.ai docs](https://dspy.ai/))

**What we do today:** hand-written prompts everywhere — extraction, planner, generation, conflict detector, severity classifier.

**Why this matters:** the 2026 RAG performance landscape says **prompts are now the dominant tuning surface** after rerank. DSPy mechanizes this. Without it, our prompts will degrade as Gemini Flash updates and corpus shifts.

**Recommendation:** **Wave B addition — DSPy as the prompt optimization layer.**

- Phase ~13: refactor extraction + planner + generation prompts into DSPy signatures + modules.
- Use the eval set (45 questions = 5 × 9 strata; expanding) as the optimization target.
- Joint-optimize the planner + generation modules with BootstrapFewShotWithRandomSearch.
- Re-run optimization on schema changes or eval-set additions.

This is a real engineering investment but pays for itself in 6 months at any non-trivial scale.

### 2.4 Long-context as alternative to RAG (we should NOT pursue)

**The narrative:** Gemini 2.5 Pro 1M context, Claude extended context — RAG is dead.

**The reality, per FloTorch Feb 2026 and others:**
- 99.7% needle-in-a-haystack recall in 1M, BUT
- 60% recall on realistic multi-fact retrieval (40% miss rate, silent)
- 30–60× slower than RAG
- 1,250× cost per query
- A Fortune 500 legal archive of 50M tokens — only 4% fits in 2M context
([RAG vs Long Context production decision framework](https://tianpan.co/blog/2026-04-09-long-context-vs-rag-production-decision-framework), [SitePoint](https://www.sitepoint.com/long-context-vs-rag-1m-token-windows/))

**Verdict:** at our 100K-doc scale, long-context is the wrong tool. Even at single-doc scale (500-page contract), our RAPTOR + chunked retrieval is more cost-effective.

**One useful exception:** for **single-doc deep analysis** (e.g., legal contract review looking for inconsistencies across distant clauses), long-context can win because "the signal lives in the interaction between parts, not in any individual chunk." We could add an opt-in "deep doc analysis" mode that loads a doc into a 1M context and runs the inspection there. Wave C — useful but not blocking.

**Recommendation:** **explicitly position in writeup as "we are RAG; long-context is complementary."** Long-context for single-doc deep dive; RAG for everything else.

### 2.5 Mem0 / agentic memory

**What it is:** persistent memory layer for AI agents — distills conversation history into compact natural-language memories. 41K GitHub stars, 14M downloads, exclusive AWS Agent SDK memory provider. ([Mem0](https://mem0.ai/blog/state-of-ai-agent-memory-2026), [arxiv 2504.19413](https://arxiv.org/pdf/2504.19413))

**Is this relevant to us?** Partially:
- Mem0 is about *user memory across conversations*. We're about *KB memory across documents*. Different domains.
- But Mem0's "hierarchical extraction → compact memories" is spiritually identical to our RAPTOR (L1d).
- And Mem0's "salient fact extraction" overlaps with our L2b emergent fields.

**Recommendation:** **no change to architecture.** Cite Mem0 in references as the contemporaneous "memory layer for agents" pattern; note we do the same at ingest-time rather than conversation-time. Architectures are complementary.

### 2.6 The "simpler chunking wins" finding

**What it is:** FloTorch Feb 2026 study — recursive character splitting at 512 tokens beat sophisticated semantic chunking on academic papers. ([2026 RAG paradox](https://ragaboutit.com/the-2026-rag-performance-paradox-why-simpler-chunking-strategies-are-outperforming-complex-ai-driven-methods/))

**Our current choice:** Late chunking (Jina arxiv 2409.04701) with 2–4K token chunks, layout-aware.

**Why we chose this:** Late chunking shows +2.70–3.63% over naive chunking per the Jina paper. Layout-aware preserves table/section integrity.

**Recommendation:** **A/B test in Phase 11 eval.** Add a config flag for `chunking_strategy ∈ {late_chunking, recursive_512, semantic_2k}`. Run eval set against each; pick the winner empirically. The Jina paper's 2.70–3.63% lead may or may not hold on our corpus.

### 2.7 Re-ranking is the dominant performance lever (we are aligned)

**What it is:** 2026 consensus — *"Re-ranking has overtaken LLM size as the dominant performance accelerator."* ([Onyx 2026](https://onyx.app/insights/enterprise-rag-platforms-2026))

**What we do:** Cohere Rerank 3.5 (internal eval: 23.4% better than hybrid; 80–150ms p50 latency on chunks <2K tok).

**Verdict:** aligned with SOTA. We have the right reranker in the right place.

---

## 3. Benchmark landscape

What we should be testing against in our eval, beyond CUAD + Enron + SEC + hand-crafted needle queries:

| Benchmark | What it tests | Top score (2026) | Use for | Notes |
|---|---|---|---|---|
| **BRIGHT** ([arxiv 2407.12883](https://arxiv.org/abs/2407.12883)) | Reasoning-intensive retrieval | ~24 nDCG@10 | Hard needle stratum | Even MTEB-leading models only hit ~18 here — *hard benchmark*. Sample from this for the 5 "needle" stratum questions in our 30-question demo eval. |
| **FRAMES** ([arxiv 2409.12941](https://arxiv.org/pdf/2409.12941)) | Factuality + retrieval + reasoning, multi-hop | varies | Multi-hop stratum | 800 multi-hop questions across 2–15 Wikipedia articles. Good for our 5 "multi-hop" eval questions. |
| **EnterpriseRAG-Bench** ([arxiv 2605.05253](https://arxiv.org/abs/2605.05253)) | RAG over company internal knowledge | varies | Calibration | Released May 2026. Closest external benchmark to our actual use case. |
| **RAGBench** ([arxiv 2407.11005](https://arxiv.org/abs/2407.11005)) | 100K examples, 5 industry domains | varies | Cross-domain validation | Useful for domain-agnosticism claim. |
| **CRAG benchmark** | Corrective RAG eval | varies | Refusal stratum | Aligned with our Astute RAG + refusal design. |
| **LegalBench-RAG** | Legal Q&A (CUAD subset) | varies | CUAD half of demo | Already cited. |
| **HHEM-2.1** (Vectara leaderboard) | Hallucination detection | varies | Faithfulness CI gate | Already cited. |
| **HalluGraph** ([arxiv 2512.01659](https://arxiv.org/abs/2512.01659)) | Legal hallucination via KG alignment | AUC 0.94 | Wave C gate B | Already cited. |
| **MuSiQue** | Multi-hop (decomposable) | varies | Multi-hop stratum | Already cited. |
| **MTEB** | Embedding quality | Gemini #1 at 68.32 | Embedding choice rationale | Already cited. |

**Recommendation:** **add BRIGHT, FRAMES, EnterpriseRAG-Bench, RAGBench to architecture.md §16 References.** Sample 1–2 questions from each for the demo eval set.

---

## 4. The honest competitive map

```
                     ON CITATIONS / FAITHFULNESS / TRANSPARENCY
                                    │
                            us  ●   │   ● Glean
                                    │       (with permissions)
                                    │
              Anthropic  ●          │
              Files                 │   ● Onyx
                                    │
   OpenAI Files  ●                  │
                                    │
                                    │
        ──────────────────────────────────────────────  ON DEPTH OF EXTRACTION / SCHEMA
                                    │
                                    │   ● Hebbia Matrix
                                    │     (multi-agent, batch)
              ● NotebookLM          │
                (single-source)     │
                                    │
                                    │   ● Harvey
                                    │     (legal-specialized)
                                    │
                              SIMPLER ─────────────────── MORE AGENTIC
```

**Where we sit:** **top-left quadrant** — high on citations/transparency, mid-high on extraction depth, low-mid on agentic-ness. This is **deliberately positioned**. The architecture trades agentic flexibility for deterministic, audit-friendly, cost-controlled retrieval.

**The Wave B additions in §2.1 and §2.2** move us up-right (more agentic) without losing our left-top properties. **The Wave B additions in §2.3** (DSPy) move us *vertically up* (better extraction quality at the same agentic level).

---

## 5. The four Wave B additions to commit to

Based on this audit, four concrete additions for Wave B. Each closes a real SOTA gap; none breaks the cost/latency/transparency story.

### B1. Batch query mode (the Hebbia spreadsheet pattern)

**What:** new page or Explore tab. User picks a doc-type cohort + a column header (a question) + optional filter. Question is run across all docs in parallel. Renders as a spreadsheet: rows=docs, columns=question answers, every cell has citation.

**Why:** closes the most visible Hebbia gap. Demo moment: *"ask 'does this contract have a non-compete clause?' across 412 contracts in one operation."*

**Cost:** 412 × ~$0.005 = $2 per batch. Configurable cell-level concurrency.

**Implementation surface:** ~Phase 13. New `/batch` page, new `batch_query` job type in Procrastinate, spreadsheet render. Reuses existing extraction + retrieval pipeline per cell.

### B2. Opt-in `deep_research` agentic mode

**What:** intent classifier output `complex_research` triggers a plan→retrieve→reflect→re-retrieve loop, capped at 5 hops or a per-query cost ceiling.

**Why:** closes the Search-o1 / agentic-loop gap. Real 2026 SOTA for complex queries.

**Cost:** 3–10× typical query (~$0.03–$0.10 per agentic query). Default OFF for demo. Opt-in for users who toggle "extended thinking" or for query classes the planner flags.

**Implementation surface:** ~Phase 14. Extension to the planner + a new orchestration loop. Reuses retrieval channels.

### B3. DSPy prompt optimization layer

**What:** refactor extraction + planner + generation prompts into DSPy signatures + modules. Compile against the eval set using `BootstrapFewShotWithRandomSearch`.

**Why:** automatic quality improvement; reduces hand-tuning labor; makes prompts trackable + versionable as code.

**Cost:** one-time engineering investment of ~3–5 days. Ongoing: re-optimize on eval-set growth.

**Implementation surface:** Phase 13. Refactor `src/kb/core/prompts.py` to DSPy modules. Add `dspy_compile.py` script.

### B4. Multi-agent decomposition for complex Q-mode

**What:** planner emits a *list* of sub-plans (not a single plan) for `set_operation` queries, queries with multiple group_bys, queries with > 3 entity hops. Each sub-plan runs in parallel; results joined per declared key.

**Why:** closes the Hebbia multi-agent gap for the specific class of queries where it actually helps.

**Cost:** parallel = same wall-clock as single plan; total tokens 1–4× single plan. Bounded by sub-plan count cap (default 5).

**Implementation surface:** Phase 14. Extension to Q-mode grammar (already supports `set_op`). Joining layer in generation.

---

## 6. Five positions to state explicitly in the writeup

These aren't gaps — they are deliberate stances that should be defended on-the-record.

| Position | Defense |
|---|---|
| **We are not "agentic by default."** | Cost, determinism, transparency, audit. Agentic is an opt-in mode (B2), not the default loop. |
| **We use Postgres-everything, not knowledge-graph-only or LlamaIndex framework.** | Transactional simplicity, single backup, dependency control, audit trail. |
| **We do not use long-context as a substitute for RAG.** | Cost (1250× per query), latency (30–60×), recall degradation (~40% miss on realistic multi-fact at 1M). Long-context as complementary "single-doc deep dive" mode only. |
| **We do file ingestion, not live connectors.** | Connectors are deployment integration. Architecture has no concept of "source not in our DB"; adding connectors is engineering, not architecture. |
| **We are read-only by design.** | Agentic actions (send email, place order, mutate external system) are a separate layer on top. KB is the substrate. |

---

## 7. References to add to architecture.md §16

New citations identified by this audit:

- **Hebbia Matrix multi-agent**: [hebbia.com/blog/divide-and-conquer](https://www.hebbia.com/blog/divide-and-conquer-hebbias-multi-agent-redesign)
- **Hebbia Matrix product**: [hebbia.com/product](https://www.hebbia.com/product)
- **Glean Knowledge Graph**: [glean.com/resources/guides/glean-knowledge-graph](https://www.glean.com/resources/guides/glean-knowledge-graph)
- **Onyx open-source enterprise RAG**: [onyx.app](https://onyx.app/insights/enterprise-rag-platforms-2026)
- **DSPy framework**: [dspy.ai](https://dspy.ai/)
- **Mem0 agentic memory**: [mem0.ai](https://mem0.ai/) + [arxiv 2504.19413](https://arxiv.org/pdf/2504.19413)
- **Search-o1 agentic search**: [arxiv 2501.05366](https://arxiv.org/pdf/2501.05366)
- **BRIGHT reasoning-retrieval benchmark**: [arxiv 2407.12883](https://arxiv.org/abs/2407.12883)
- **FRAMES (Fact, Fetch, Reason)**: [arxiv 2409.12941](https://arxiv.org/pdf/2409.12941)
- **EnterpriseRAG-Bench**: [arxiv 2605.05253](https://arxiv.org/abs/2605.05253)
- **RAGBench**: [arxiv 2407.11005](https://arxiv.org/abs/2407.11005)
- **FloTorch 2026 RAG paradox study** (simpler chunking wins): [ragaboutit.com](https://ragaboutit.com/the-2026-rag-performance-paradox-why-simpler-chunking-strategies-are-outperforming-complex-ai-driven-methods/)
- **Long-context vs RAG production decision framework**: [tianpan.co](https://tianpan.co/blog/2026-04-09-long-context-vs-rag-production-decision-framework)
- **Agentic RAG 2026 patterns**: [digitalapplied.com](https://www.digitalapplied.com/blog/agentic-rag-patterns-multi-step-reasoning-guide)

---

## 8. Final verdict

**Is there a system in 2026 that is better than what we have built?**

- **For the same use case (domain-agnostic enterprise KB with schema emergence, multi-resolution retrieval, conflict detection, feedback, citation grounding):** no published peer matches the full envelope. Glean comes closest but is permissions-first + connector-first, not schema-emergence-first.

- **For specific dimensions:**
  - **Multi-agent + batch UX:** Hebbia is ahead. Closing in Wave B (B1, B4).
  - **Agentic loops on complex queries:** Search-o1 / ReAct patterns are ahead. Closing in Wave B (B2).
  - **Prompt optimization:** DSPy is ahead. Closing in Wave B (B3).
  - **Live source integration:** Glean has 100+ connectors. Deferred as deployment integration.
  - **Permissions/multi-tenant:** Glean/everyone has it. Wave C.

- **For deliberate non-pursuit:** long-context as RAG substitute (don't), agentic-as-default (don't), live connectors as architecture (don't — deployment), agentic actions (don't — read-only by design).

**The architecture is solid 2026 SOTA on primitives, slightly behind on three specific patterns (B1–B4 fixes), and deliberately positioned on the five "we don't claim" stances from README.**

If asked *"why didn't you build like Hebbia / Glean / NotebookLM?"*, the answer is:

> *"We made different trade-offs. Hebbia is multi-agent + batch — we're starting single-question deep-retrieval, adding their batch pattern in Wave B (Design B1). Glean is permissions-first + connector-first — those are deployment integrations on a similar architecture, scoped to Wave C. NotebookLM is single-source thinking partner — different scale than enterprise KB. Where the SOTA is genuinely ahead on architecture (multi-agent decomposition, agentic loops, prompt optimization) we have explicit Wave B paths to close those gaps. The composition we built today is what 2026 says works for our scope, and the four committed Wave B additions land us at full parity."*

That answer is defensible. It is grounded in this audit.
