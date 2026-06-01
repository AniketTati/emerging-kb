# Red Team — Battle-Testing the Locked Architecture

**Date:** 2026-05-21
**Status:** pre-build review. Findings against `docs/architecture.md`, `docs/walkthrough.md`, and `docs/scenarios.md`.
**Mode:** adversarial. The job here is to break things, not validate them. Every "✓" in `scenarios.md` is treated as a claim to be tested, not accepted.

---

## 0. Executive summary

Three things should be fixed *before* Phase 0 begins. Eight more are real and need design-level mitigation. The rest are minor or already covered.

### CRITICAL — the architecture is wrong about this today and a competent adversarial reviewer will land on it

1. ~~**Aggregation queries are silently mis-routed through the retrieval pipeline.**~~ **RESOLVED 2026-05-21 by gaps_design.md §Design 1.** New planner mode `Q` emits validated SQL plans against `extracted_entities ⨝ L3 ⨝ L5`. Aggregation answers are templated (not freeform LLM), cite the audit artifact + downloadable row list, and surface extraction-completeness caveats inline. New eval stratum + CI gate (aggregation accuracy ≥ 0.95). Industry precedent: CSR-RAG arxiv 2601.06564, Azure AI Search agentic retrieval.

2. ~~**Boolean-AND / set-intersection multi-criteria queries are wrong-routed to HippoRAG PPR.**~~ **RESOLVED 2026-05-21 by gaps_design.md §Design 1.** `Q` mode supports `set_op: intersect | union | except` over sub-plans with a declared key. PPR remains for *relevance* walks; `Q` handles *predicate* intersections.

3. **Cold-start rarity is undefined and will pollute the demo if not guarded.** L3 rarity is *corpus-relative*. The first ~200 docs in any new corpus have no centroid worth comparing against — a single 4-hour clause has rarity 1.0 because there's nothing else, *and* a typical 14-day clause also has rarity ≈ 1.0 because the corpus mean is itself. Until critical mass is reached, channel ⑥ (anomaly filter) returns garbage. The CUAD + Enron + SEC demo corpus is borderline at 80–100 docs.

### HIGH — design needs to change

4. **Schema-swap "in seconds" only holds at demo scale.** True for 80 docs (~3 min total schema re-extraction). At 100K docs it is **hours**, not seconds. The demo Moment 1 in §11.1 ("watch re-extraction kick off") works precisely because the corpus is small. We must not let a reviewer hear "seconds" and then ask "what about at 100K?"

5. **The query pipeline fires HyDE×3 + Step-Back + Query2Doc + Tree-of-Clarifications in parallel on every vague query.** Stated as "gated by intent" but the gate's logic is unspecified. Worst-case that's **6 Gemini Flash calls before retrieval even starts**, adding ~1.5s to first-token-time on the worst query type the demo deliberately includes.

6. **Multi-hop with CRAG escalation to IRCoT explodes the latency budget.** Each escalation hop = ~4–5s. Four hops = ~20s wall-clock. The demo's edge case 2 (party + fast delivery) sits right on this cliff. If the rerank top-1 score lands below threshold, the user watches a 20-second spinner.

7. ~~**Open extraction's type vocabulary is hard-coded in the Gemini Flash prompt.**~~ **RESOLVED 2026-05-21 by adding L2b (Emergent Fields).** The architecture now runs *two* open-extraction passes per doc: L2 with a universal type list for cross-doc entity navigation, and **L2b with an open-vocabulary prompt** that lets each doc propose its own fields in its own vocabulary (`stent_type`, `khasra_number`, `lab_parameter`, …). Cross-doc clustering induces a per-doc-type emergent schema; stable inferred schemas are surfaced as promotion suggestions in `/schema`. See architecture.md §1 Reality A.2 and §5 steps 12b–12d. Cost impact (+$1,500 at 100K-doc scale) absorbed into §13. *Schema-emerges-from-data is now honestly true.*

8. **L3 doc-type classifier is a single point of failure for atomic-unit retrieval.** If the classifier puts a contract into `email`, the clause-level channel (⑤) never sees that doc. Today there is no fallback — channel ⑤ misses, channels ①②③④ might still find the chunk, but the rare-clause anomaly path is dead. Need: a fallback that re-runs L3 extraction at higher cost when downstream signals (rerank score, intent) suggest a classification miss.

9. ~~**Negative-query refusal is *overconfident* whenever an L3 extractor has missed something.**~~ **RESOLVED 2026-05-21 by gaps_design.md §Design 2.** Refusal path now runs a semantically-similar fallback alongside the typed filter. Output becomes: *"No clauses typed `exclusivity` were found involving Adani entities. However, 3 clauses with semantically similar content (non-compete, lockout) involve Adani-cluster entities — please review [citations]."* Converts false negative into calibrated maybe.

10. **Cohere Rerank is an external API on the critical path.** Latency 200–600ms, occasional spikes; vendor downtime takes the whole query path down. No documented fallback to a local reranker (`mxbai-rerank-large-v2` is listed but not wired into the failure path).

11. **Identity-resolution merges cascade through L5/L6 silently.** When two entities merge on doc N+500, every relationship row that referenced either entity needs re-aiming. The walkthrough says "no full rebuild" — true for the *graph*, not true for the *edge table*. Without an explicit re-aim job, queries against pre-merge edges return stale results.

### MEDIUM — known and budget for

12. ~~No doc versioning. Amended contracts orphan predecessors.~~ **RESOLVED 2026-05-21 by gaps_design.md §Design 3 (doc chains).** New L0.5 layer groups raw files into logical chains (email threads, contract+amendment chains, drawing revisions, circulars+corrigenda, patient charts). Chains carry ordering + `current_version_id`. Planner mode `K` retrieves chain-aware (current/all/history). Resolves "latest revision" and supersession queries.

13. ~~Citation semantics for aggregation answers are undefined.~~ **RESOLVED 2026-05-21 by gaps_design.md §Design 1.** Aggregate answers cite the audit-log artifact (Q plan + SQL + row list CSV) as a first-class citation. Top-N rows inline keep per-row citations. Result format: *"₹4,213 cr across 5,127 invoices, computed at T from query [audit#a7c2]"*.

14. Cross-lingual L3. CUAD-trained clause typer is English-only. Marathi land records have *no* L3 extraction, just L2 + RAPTOR. Govt Maharashtra scenario is partially exposed.

15. Embedding-model swap is *not* free. 5M chunks × 768d halfvec = ~3 GB to re-embed if we move off Gemini Embedding 001. The "adapter swappable" claim has a six-figure compute bill at scale.

16. Real-time freshness lag. Procrastinate queue + parser + embed + RAPTOR + extract = 60–120s per doc end-to-end on a single worker. A doc uploaded 30 seconds ago is *not* queryable.

17. Concurrent ingest + query saturates a single PG connection pool. 30 ingest workers + 50 chat sessions + vector + BM25 + audit-log writes on one DB → connection contention. Solvable with PgBouncer + read replicas, but not in the architecture today.

---

## 1. Methodology

I traced **15 representative queries** from `docs/scenarios.md` through the locked pipeline in `docs/architecture.md` §6 (query-time) and `docs/walkthrough.md` §4. For each, the columns are:

- **What the user actually wants** (often not what they typed)
- **Stated verdict** in scenarios.md
- **What the pipeline actually does** step by step (intent → rewrite → plan → channels → fuse → rerank → CRAG → generate → judge)
- **Where it goes wrong** (or right)
- **What it would take to fix**

Latency and cost are computed against the per-step assumptions stated in `architecture.md` §7–§8 and `walkthrough.md` §4 (T+ timestamps), with realistic Gemini Flash / Cohere Rerank vendor numbers as of May 2026.

---

## 2. Speed — battle-test findings

The latency budget table, the where-time-goes breakdown, and the recommended fix levers all live in **`docs/scale_perf_audit.md` §3 — single source of truth**. The red-team-specific findings:

- **IRCoT escalation is the latency cliff.** CRAG's `top-1 < τ OR top-5 disagreement → escalate` is binary; uncapped it doubles to quadruples latency. We've since capped at 2 hops (down from 4) per this finding. Scenario queries most likely to escalate: *"Where did we have a delay caused by a single supplier"*, *"Shared vendors between Retail & Jio"*, *"Top operational risks this quarter"*.
- **Five fix levers** identified by this red-team and now in the spec: gate rewriting harder (intent-selects rewrites), rerank top-50 not 200, stream generation token-by-token, cap IRCoT at 2 hops, show per-stage progress strip in chat UI.

Architecture's reaction: F8 (streaming generation) now in `architecture.md` §6 step 8; IRCoT cap now in §6 step 7. Per-query latency posture per `scale_perf_audit.md` §3.

---

## 3. Cost — battle-test findings

The per-query / per-doc / per-corpus breakdown lives in **`docs/scale_perf_audit.md` §4 — single source of truth**. The red-team-specific findings:

- **Architecture stated `$0.01–0.10/query`**; the math gives **typical ~$0.005–0.01**, worst-case ~$0.08. Lower bound revised in §13 of architecture and in scale_perf_audit.
- **L2b open-vocabulary extraction adds ~$1,500 at 100K-doc scale** (+50% over fixed-list extraction). The honest price tag of "schema emerges from data." Accepted trade.
- **Reliance scale = $600,000 one-time ingest.** State openly in the writeup; do not flinch.
- **Schema re-extraction = $300/version-change** (diff-driven, doc-type-scoped). Without those mitigations, costs balloon to $6K with normal schema iteration.
- **Cohere Rerank at 1M queries/year = $2,000.** Tractable. **Local `mxbai-rerank-large-v2` fallback was listed in §8 but not wired in §6 step 6** — open finding F7 in §0 of this doc.

---

## 4. Query-by-query trace — where the ✓ checkmarks are wrong

For each query I trace what the pipeline *actually* does and contrast it with what `scenarios.md` claims.

### 4.1 Reliance / CFO — "Total vendor spend across petrochem in Q2 2025"

**Scenarios.md says:** ✓ via L3 invoices + SQL aggregation.

**What actually happens in the pipeline:**

1. Intent classifier: returns `aggregation` or `factoid`. The architecture's intent set (`factoid | vague | multi-hop | global/thematic | negative | adversarial`) **has no `aggregation` class**. The planner therefore won't emit a SQL-aggregation mode.
2. Best case: intent → `factoid`, planner emits `H` (hybrid) + `F` (filter `doc_subtype=invoice AND date ∈ Q2 2025 AND vertical=petrochem`).
3. Retrieval: top-200 invoice chunks come back. But there are likely 5,000+ such invoices.
4. Rerank: top-20 → top-5 invoices.
5. Generation: Gemini Flash sees 20 invoice chunks, says *"Based on the 20 most relevant invoices, vendor spend totaled approximately ₹42 cr"*. **This number is wrong** — it's the sum of the top-20 chunks, not the actual Q2 petrochem total of ₹4,200 cr.

**Verdict:** ✗ silently. Looks confident, cites real documents, returns an answer that is 100× wrong. *This is the worst possible failure for a CFO query.*

**Fix required before Phase 0:**

- Add `aggregation` to the intent class enum.
- Add planner mode `Q` (structured query): planner emits a SQL plan against `extracted_entities` joined with `L3 atomic units`. Query runs against Postgres, not the retrieval pipeline.
- Astute RAG generation receives the *aggregated result* and the *list of contributing rows* as evidence, generates *"₹4,213 cr total across 5,127 invoices [citation: see linked report]"*.
- Citation for aggregates resolves to a generated audit attachment (the row list), not 5,127 inline footnotes.

This is a real architectural addition, not a tweak. **It is also the single thing most likely to be challenged under adversarial review.**

### 4.2 Reliance / Cross-vertical — "Shared vendors between Retail & Jio with spend > 10cr"

**Scenarios.md says:** ✓ via HippoRAG PPR.

**What actually happens:**

1. Intent: `multi-hop`. Planner emits `T` (HippoRAG) seeded with entities `[Retail, Jio]`.
2. PPR returns top-N entities most-connected to *both* Retail and Jio. **This is not the same as the intersection of vendors of Retail and vendors of Jio.** Closeness in the graph ≠ membership in both sets.
3. PPR returns: top-10 entities. Some are vendors-of-both; some are people who work at both; some are projects shared between them; some are concepts; some are noise.
4. Generation tries to filter for "vendor" and "spend > 10cr" inside the prompt. May or may not work. With small graphs, often does. With dense graphs, hallucinates set membership.

**Verdict:** ⚠ partial. PPR is the *wrong tool*. The right tool is:

```sql
SELECT vendor.id, SUM(invoice.amount)
FROM relationships r1 JOIN relationships r2 ON r1.subj = r2.subj
WHERE r1.predicate = 'supplies' AND r1.obj = 'Retail'
  AND r2.predicate = 'supplies' AND r2.obj = 'Jio'
GROUP BY vendor.id
HAVING SUM(invoice.amount) > 10_00_00_000;
```

This is identical to the aggregation case — needs planner mode `Q` against L3 + L5 tables.

**HippoRAG is correct for "vendors **relevant** to both Retail and Jio's strategic priorities"** — a fuzzy similarity question. For boolean intersection on entity attributes, it's the wrong primitive.

**Fix:** same as 4.1. Make planner mode `Q` available. Document that PPR is for *relevance*, SQL is for *predicates*.

### 4.3 D-Mart / Vendor Manager — "Vendors who missed delivery > 3 times this year"

**Scenarios.md says:** ✓ via L3 Delivery records + group-by.

**What actually happens:**

1. Intent: probably `factoid` or `multi-hop`. No `aggregation` class.
2. Planner emits `F` (filter `doc_type=delivery_log AND status=missed AND year=2026`) + `T` (HippoRAG seeded with `[vendor, delivery, missed]`).
3. Retrieval finds top-200 missed-delivery chunks across many vendors.
4. Generation sees top-20 chunks and either lists vendors with missed deliveries (incomplete) or attempts to count from chunks (wrong).

**Verdict:** ✗ silently. Same root cause as 4.1 and 4.2.

**Fix:** planner mode `Q` again. This is now the 3rd query of the first 3 scenarios where the SQL-aggregation gap appears. **It is endemic.**

### 4.4 Apollo / Treating Doctor — "Mr. Sharma's complete history"

**Scenarios.md says:** ✓ L4 Patient entity + scoped retrieval.

**What actually happens:**

1. Intent: `factoid` with `entity_scope`.
2. Planner emits `E` (entity lookup, name=Mr. Sharma) + `S` (scope all docs containing this entity).
3. L4 entity lookup needs to handle: many "Mr. Sharma"s. First-name + last-name ambiguity is fatal in a healthcare context. There is probably no canonical entity match without a patient ID, DOB, or other discriminator.
4. The system **should** ask back: "I found 47 patients named Sharma. Which one?" — but the architecture's pipeline has no clarification-loop at this stage. Tree-of-Clarifications fires *before* retrieval; it doesn't trigger from a downstream entity-disambiguation failure.

**Verdict:** ⚠. The pipeline either returns ALL 47 Sharmas' records (privacy violation in real deployment, but legitimate KB behavior) or returns the most-recently-updated one (silent wrong-patient hazard).

**Fix:** add a `disambiguate` mode that the planner can defer to the user mid-query. UI needs a "did you mean…?" affordance on entity matches with confidence < threshold.

### 4.5 HDFC / Compliance — "Transactions flagged this week with structuring patterns"

**Scenarios.md says:** ✓ L3 Transaction + rarity + graph.

**What actually happens:**

1. Intent: `multi-hop` with `anomaly`.
2. Planner emits `C` (transaction filter `date ∈ this week`) + `A` (rarity > 0.95) + `T` (HippoRAG to detect smurfing patterns).
3. Retrieval finds rare transactions. **But "structuring" / "smurfing" is a *pattern across multiple transactions*, not a property of any single transaction.** A single ₹9.5L transaction is normal. Ten ₹9.5L transactions to ten different accounts on the same day from the same source is structuring.
4. L3 rarity scores individual atomic units. It doesn't score *patterns of units*. No layer in our architecture does pattern detection across transactions.

**Verdict:** ✗. Architecture has *no* pattern-detection layer. Structuring detection is a feature of an AML platform, not a KB.

**Honest fix:** mark this query as out-of-scope (Wave C, or domain-specific add-on). Cannot be solved by adding a planner mode; needs a new analytics layer over L3 atomic units.

### 4.6 Tata Steel / Mining — "Drilling reports Jharia coal field this month"

**Scenarios.md says:** ✓ doc filter + L1.

**What actually happens:**

1. Intent: `factoid` with `scope`.
2. Planner emits `D` (doc metadata filter: `doc_type=drilling_report AND location_facet=Jharia AND date ∈ this month`) + `H` (hybrid).
3. Doc metadata facets must have been populated at ingest time. The architecture mentions `doc_type, date, source, path` for metadata but not `location_facet`. If location wasn't extracted at ingest, this filter doesn't exist.

**Verdict:** ⚠. Works *if* location was an extracted facet. Otherwise falls through to chunk-level retrieval with location in the query text — works in practice for distinctive names like "Jharia", may fail for ambiguous ones.

**Fix:** specify the metadata facet set explicitly in §5 indexing pipeline. Currently it's implicit. Locations should be a first-class facet, populated from L2 LOCATION mentions at ingest.

### 4.7 Govt Maharashtra / Welfare — "Households with ration card but no Aadhaar"

**Scenarios.md says:** ✓ L4 set difference.

**What actually happens:**

1. Intent: `multi-hop` with set operation. No mode exists for set difference.
2. Planner emits `T` (HippoRAG seeded `[household, ration, aadhaar]`).
3. PPR returns households related to *both* topics. This is the *opposite* of what was asked.

**Verdict:** ✗. Same root cause as 4.1–4.3 (no `Q` mode for structured queries). Plus a specific set-difference semantics issue. Plus the scale issue: crore-scale rows in xlsx demands a different retrieval primitive than per-doc indexing.

**Fix:** planner mode `Q` (SQL); xlsx-row-as-entity needs an indexed projection table, not just L3 row records.

### 4.8 L&T / Subcontractor manager — "Subcontractors with both delayed delivery AND safety incident"

**Scenarios.md says:** ✓ HippoRAG.

**Same trace and verdict as 4.2 and 4.7.** This is Boolean AND, not a PPR walk. HippoRAG will return subcontractors *related to both topics*, which over-includes (someone with one safety incident and zero delayed deliveries can rank high) and may under-include (the actual AND-set is small).

### 4.9 L&T / Site Engineer — "Drawing for column C7, latest revision"

**Scenarios.md says:** ✓ + revision tracking.

**What actually happens:**

1. Intent: `factoid` with `entity_scope` + version axis.
2. Planner emits `E` (entity: drawing C7) + `D` (filter latest version).
3. **The architecture has no version axis on `files` or `extracted_entities`.** "Latest revision" requires `valid_from / valid_to` (which is Wave C) or an explicit `version, supersedes_id` link.

**Verdict:** ✗ for "latest revision". Returns all C7 drawings without ordering. The architecture's tables don't model version succession.

**Fix:** add `superseded_by_doc_id` to `files`. Plus a doc-similarity check at ingest to detect revisions automatically (same title + ~80% chunk overlap = probable revision). Mark this as a Wave-B promotion; it's needed for the L&T scenario *and* the legal/banking scenarios.

### 4.10 L&T / Civil engineer — "Show me all bolted joints rated for 50 tonne in plant X drawings"

**Scenarios.md says:** ✗ requires CAD-aware tool.

**Verdict honest.** Out of scope, acknowledged.

### 4.11 Cyril Amarchand / Conflict Check — "Are we conflicted on representing Y vs Z?"

**Scenarios.md says:** ✓ cross-doc set check.

**What actually happens:**

1. Intent: `multi-hop` with relationship check.
2. Planner emits `T` (HippoRAG seeded `[Y, Z]`) + `H`.
3. PPR finds entities related to both Y and Z. If the firm has *ever represented* Y or Z, those representations are likely entity-graph edges (`represented_by`, `client_of`).
4. Generation: needs to synthesize "Y is our client of record" + "Z appears in adverse position in case A" + "this creates a conflict".

**Verdict:** ⚠. This *might* work because the relevant entities are likely well-connected in the graph and PPR will surface both. But the reasoning step "this creates a conflict" is on generation, which can hallucinate the conflict semantics.

**Fix:** for high-stakes Boolean conflict checks, the architecture should refuse to *infer* and instead *enumerate*: "Y has these 4 representation relationships in our corpus [list]; Z has these 7 [list]; manual review required." The system isn't a lawyer.

### 4.12 Cyril Amarchand / Knowledge Mgmt — "Find similar fact patterns to current case"

**Scenarios.md says:** ✓ HyDE + RAPTOR.

**What actually happens (this is the GOOD case):**

1. Intent: `vague` (similarity).
2. Rewriting: HyDE produces 3 hypothetical case summaries.
3. Planner emits `H` over RAPTOR mid + top levels.
4. Retrieval pulls top-200 doc-card summaries from RAPTOR L2/L3.
5. Rerank gets top-20.
6. Generation cites 5 similar cases.

**Verdict:** ✓ genuine. This is exactly what RAPTOR + HyDE were designed for. *Note that this is the only ✓ in this trace section that survives scrutiny without fix.*

### 4.13 The edge case 1 trace — "What issues have we had with foundation work?"

**Architecture.md §10 walks through this and concludes ✓.**

**Battle-test verdict:** ✓ — the walkthrough is honest. Contextual Retrieval + RAPTOR mid-level summary + cross-encoder rerank genuinely solve the vocabulary-mismatch needle problem. The only fragility is: **if the note never made it through OCR cleanly** (handwritten note photographed at angle, glare on key words), neither the chunk nor the RAPTOR summary will contain the right vocabulary, and the needle stays buried. Out-of-scope-of-retrieval failure, not pipeline failure.

### 4.14 The edge case 2 trace — "Party + fast delivery"

**Architecture.md §10 walks through this and concludes ✓.**

**Battle-test verdict on the trace itself:** ✓ provided that **(a)** the 100,000-contract corpus has enough delivery_timing clauses with hour-scale parameters to establish a stable centroid (~100+ should be enough; we will have to assume this for the demo), and **(b)** the L3 extractor recognizes "4 hours" as a `delivery_timing` parameter rather than misclassifying it as `term_duration` or `penalty_window`.

**Failure modes if (b) breaks:** the clause-level channel ⑤ goes silent; channels ①②③④ might still surface the contract via chunk-level "deliver within 4 hours" string matches; channel ⑦ HippoRAG might surface the Event entity. But the *decisive* layer for this query is L3, and L3 has a single point of failure on extractor accuracy. Without an evaluation of L3 extractor precision/recall on rare clauses, the demo claim is unfalsifiable.

**Fix:** add an evaluation slice that specifically targets L3 extraction precision on rare clauses, on CUAD annotations. Make this an explicit eval CI gate (e.g., "L3 clause-type recall on rarest-decile clauses ≥ 0.85").

### 4.15 Reliance / Board member — "Have we ever signed exclusivity with any Adani entity?"

**Scenarios.md says:** ✓ L4 entity filter; system refuses if no evidence.

**The over-confident refusal problem in detail:**

1. Intent: `negative-query`.
2. Planner emits `E` (entity lookup `Adani*`) + `C` (clause filter `clause_type=exclusivity`) + `T` (HippoRAG path from Reliance entity to any Adani entity through exclusivity relationships).
3. If channel ⑤ returns 0 clauses typed `exclusivity` involving an Adani-cluster entity → CRAG flags no evidence → Astute RAG generates *"No exclusivity agreement with any Adani entity is supported by the corpus. Searched: 12,847 contracts, 0 typed as exclusivity, 0 with Adani-cluster parties in exclusivity clauses."*
4. The user trusts this.

**Failure mode:**

- An old Reliance-Adani vendor agreement contains an exclusivity clause that the L3 typer labeled as `non_compete` instead of `exclusivity`. The query refuses falsely. A user (or adversarial reviewer) points at the 2018 doc and says "but look — it's right here". *Trust lost.*

**Fix:** on negative-queries specifically, channel ⑤ should retrieve via *semantic similarity to "exclusivity" clauses*, not just `clause_type=exclusivity` filter. Even if typed wrong, an exclusivity clause's embedding will be near other exclusivity clauses. Generation then says: *"No clauses typed `exclusivity` with Adani parties were found. However, 3 clauses with semantically similar content (non-compete, lockout) involve Adani-cluster entities — please review [citations]."* This converts a false negative into a calibrated maybe.

---

## 5. Structural findings — patterns that span scenarios

### F1. The aggregation gap is endemic

**Where it appears:** Reliance CFO (4.1), Reliance cross-vertical (4.2), D-Mart vendor manager (4.3), D-Mart audit, HDFC NPA, HDFC AML, Govt CM scheme tracking, Govt PWD audit, Govt welfare (4.7), L&T MD cost overrun, L&T subcontractor (4.8), Cyril workload, Cyril tax — **at least 14 scenario queries** that are graded ✓ in scenarios.md.

The architecture has no `Q` planner mode and no `aggregation` intent. The query pipeline is purely retrieval-then-generate. Aggregation queries don't fail loudly; they fail *silently and confidently*.

**Required fix before Phase 0:**

1. Add intent class `aggregation | computation`.
2. Add planner mode `Q`: SQL projected against `extracted_entities` ⨝ L3 ⨝ L5.
3. Generation path branches: aggregation results are templated, not generated freeform. *("₹4,213 cr across 5,127 invoices, computed at 2026-05-21 14:32:01 IST from query <link to audit row>.")*
4. Citations for aggregates link to a generated row-list artifact, not inline footnotes.
5. Eval set gets an `aggregation` stratum (5 questions, alongside the existing 5 needle / 5 multi-hop / 5 rare-unit / 5 entity-scoped / 5 negative / 5 long-form synthesis).

This is the most important pre-build change in this entire document.

### F2. Boolean / set / join queries need the same `Q` mode

**Where it appears:** Reliance cross-vertical, L&T subcontractor, Govt welfare, Cyril conflict-check (4.11), D-Mart anomalous-invoice detection.

PPR is the wrong primitive. The same `Q` mode that fixes F1 also fixes F2, *provided* the planner is taught to emit set-algebra plans (`UNION`, `INTERSECT`, `EXCEPT`).

### F3. ~~The "schema emerges from data" framing is partially marketing~~ — RESOLVED by adding L2b

**Resolution (2026-05-21):** the architecture now includes a dedicated **L2b — Emergent Fields** layer that runs an *open-vocabulary* extraction prompt on every doc (no fixed type list). Each doc proposes its own fields with values and descriptions; cross-doc clustering induces a per-doc-type emergent schema; stable schemas are surfaced as promotion suggestions in `/schema`.

This means schema-emerges-from-data is honestly true:

- Day 1 (1 doc): you can query *that doc's* proposed fields.
- Day 20 of a doc-type: you have a wobbly inferred schema with prevalence.
- Day 100: stable inferred schema; promotion suggestion appears.
- Domain-specific fields (`stent_type`, `khasra_number`, `lab_parameter`, `steel_grade`) are captured without anyone naming them.

See architecture.md §1 Reality A.2 and §5 indexing pipeline steps 12b–12d for the per-doc pipeline. Cost impact: per-doc ingest rises from ~$0.04 → ~$0.06; per 100K-doc corpus +$1,500. Worth it.

**Reusable framing for the writeup:**

> "Schema emerges from data. Every doc proposes its own structured fields in its own vocabulary as it arrives. Across similar docs the system clusters those proposals into an inferred schema that grows in confidence with the corpus. The user can already query emergent fields on day one; once the inferred schema stabilizes, the system suggests promoting it to typed schema. There is no day-zero schema requirement; there is no 'extract these N pre-named types' bottleneck."

### F4. Cold-start statistics

Rarity scoring, identity-resolution thresholds, and entity-graph PPR weights are all functions of corpus statistics. None of these work below a critical mass. For the 80-doc demo:

- Rarity scores: ~stable for clause types present in 30+ examples; noisy below that. CUAD has 41 clause types; ~510 contracts; ~12 contracts per type average → barely sufficient. Mitigation: in the demo, default the anomaly filter to **OFF** unless query intent is explicitly "unusual". Don't let cold-start rarity pollute typical queries.
- Identity-resolution embedding-blocking thresholds: stable after ~100 entities. Below that, LLM-judge runs on every pair → cost spike.
- PPR: works at any size, but seeds matter more on small graphs.

**Fix:** add a corpus-size check in the pipeline. Below thresholds, log a warning and route around the channel.

### F5. Negative-query overconfidence

Channel ⑤'s strict typed filters convert misclassified L3 units into false negatives. Always do a semantic-similarity check alongside the typed filter, and report disagreement: "*No typed exclusivity clauses found; 3 semantically similar clauses surfaced — review.*"

### F6. Verisioning is not Wave C; it's blocking for half the scenarios

Banks (loan amendments), law firms (contract revisions), construction (drawing revisions), government (gazette amendments) all need version-aware retrieval. The architecture defers `valid_from / valid_to` to Wave C. **Promote to Wave B at minimum**, ideally Wave A.

### F7. The reranker is on the critical path; the fallback isn't wired

`mxbai-rerank-large-v2` is listed in §8 as the fallback but the failure path in §6 step 6 says only `Cohere Rerank 3.5 / mxbai-rerank-large-v2`. Implementation must actually:

```
try:
    rerank = cohere.rerank(query, top200)
except (Timeout, ServiceUnavailable, BudgetExceeded):
    rerank = local_mxbai.rerank(query, top200)
```

…not as an afterthought. Cohere outages are real; rerank is in every query.

### F8. Streaming generation is missing from the spec

`§6 step 8` says "generate". The chat UX wants streaming. This is a real implementation choice and a real demo-feel improvement. Add to spec: generation is streamed by default; HHEM judge runs at the end on the full output, can retract; UI shows "checking sources…" between stream-end and judge-pass.

### F9. Concurrent load shape

The architecture says "one Postgres". For demo this is fine. For production we need:

- PgBouncer for connection pooling
- Read replicas for query-time reads (vector + BM25 + audit log writes are heavy on the primary)
- Separate audit-log write path (logical replication or an outbox table)

This belongs in Wave A as a stretch, certainly Wave B. Mention in writeup.

### F10. Doc-type classifier accuracy bounds the L3 surface area

If the classifier is 95% accurate, 5% of docs are mis-routed at L3. For 100K docs, that's 5,000 documents where rare-clause retrieval is permanently broken. Mitigations:

- Multi-label classifier: docs can be `contract + memo`, both extractors run.
- "Unknown" route: when classifier confidence is low, fall back to *generic* atomic-unit extraction (split by section, type as "unit", parameters as freeform jsonb).
- Reclassification on rerank disagreement: if the chat-time evidence suggests the doc is a contract but it was indexed as memo, re-extract L3 in the background and refresh.

---

## 6. Severity matrix

| # | Finding | Severity | Resolution | Phase to land |
|---|---|---|---|---|
| F1 | ~~Aggregation gap~~ | ~~CRITICAL~~ | **RESOLVED** gaps_design.md §Design 1 (Q mode) | Phase 8 |
| F2 | ~~Boolean / set / join gap~~ | ~~CRITICAL~~ | **RESOLVED** rides on Design 1 (set_op) | Phase 8 |
| F4 | Cold-start statistics | CRITICAL for demo | low (guard rails) | Phase 0 ingest setup |
| #2 latency | IRCoT 4-hop cliff at 20s | HIGH | RESOLVED — capped at 2 hops + streaming | Phase 8 |
| F5 | ~~Negative-query overconfidence~~ | ~~HIGH~~ | **RESOLVED** gaps_design.md §Design 2 (semantic fallback) | Phase 8 |
| F6 | ~~Versioning~~ | ~~HIGH~~ | **RESOLVED** gaps_design.md §Design 3 (doc chains) | Phase 5 |
| F3 | ~~Schema-emerges framing~~ | — | **RESOLVED** L2b layer | done |
| F7 | ~~Cohere Rerank fallback unwired~~ | ~~HIGH~~ | **RESOLVED** — try/catch with 1500ms timeout, fall back to local mxbai-rerank-large-v2 always-loaded; annotated in plan inspector | architecture.md §6 step 6 |
| F8 | ~~Streaming generation~~ | ~~MEDIUM~~ | RESOLVED — added to §6 step 8 | Phase 8 |
| F9 | Concurrent load shape | MEDIUM | medium (PgBouncer + replicas) | Wave B |
| F10 | Doc-type classifier blast radius | MEDIUM | medium (multi-label + unknown route) | Phase 5 |
| 4.4 | Entity disambiguation mid-query | MEDIUM | medium (planner emit "disambiguate" mode + UI) | Wave B |
| 4.5 | AML pattern detection | DOCUMENTED OUT-OF-SCOPE | n/a | writeup |
| 4.9 | ~~Doc revisions ("latest C7")~~ | ~~MEDIUM~~ | **RESOLVED** rides on Design 3 | Phase 5 |
| 13 | ~~Aggregate citation semantics~~ | ~~MEDIUM~~ | **RESOLVED** Design 1 (audit-artifact citation) | Phase 8 |
| 14 | Cross-lingual L3 | LOW (scoped out of demo) | medium (per-language extractor) | Wave C |
| 15 | Embedding-model lock-in | LOW (cost not blocker) | high (re-embed) | writeup risk section |
| 16 | Real-time freshness lag | LOW (designed) | n/a | writeup expectation |
| — | **Two docs disagree (silent pick)** | NEW finding from gap-thinking | **RESOLVED** gaps_design.md §Design 2 (conflict detection + authority + recency) | Phase 8 |
| — | **Doc chains / threads / amendments** | NEW finding | **RESOLVED** gaps_design.md §Design 3 (L0.5 doc chains) | Phase 5 |
| — | **User feedback / correction loop** ("what if I tell you the answer is wrong?") | NEW finding | **RESOLVED** gaps_design.md §Design 4 (corrections table + targeted re-extraction + regression CI) | Phase 9 |
| — | **Citation across modalities** (xlsx/OCR/image/RAPTOR/aggregate/atomic-unit/entity/chain) | NEW finding | **RESOLVED** gaps_design.md §Design 5 (universal envelope + per-type renderers) | Phase 8/10b |

---

## 7. What to change before Phase 0

This is the short list. Everything else can be added later or documented as known.

1. **Add intent class `aggregation`** to the architecture's intent enum (§6 step 1).
2. **Add planner mode `Q`** for structured SQL plans (§6 step 3). Specify its grammar: which tables, which predicates, what aggregations, what joins.
3. **Specify the metadata facet set explicitly** in §5 indexing (doc_type, date, source_path, language, **location_facet from L2**, **doc_status (live / superseded)**).
4. **Add a versioning concept**: `superseded_by_doc_id` on `files` + a content-similarity check at ingest to auto-detect revisions. Promote to Wave A or B (not Wave C).
5. ~~Honest restatement of "schema-emerges-from-data"~~ — **done**: architecture now has the L2b emergent-fields layer that makes the principle literally true. Per-doc cost +50%; absorbed.
6. **Cold-start guard rails**: corpus-size check before enabling channels ⑥ (anomaly) and ⑦ (HippoRAG) below thresholds.
7. **Negative-query fix**: typed filters always run with semantic-similarity fallback; generation must report disagreement, not refuse.
8. **Cap IRCoT at 2 hops** with a streaming progress UI.
9. **Aggregation stratum (5 questions) added to the 30-question demo eval.** Without it, F1 is hidden in the demo and surfaces in production.
10. **Wire the Cohere Rerank fallback** to `mxbai-rerank-large-v2` in the adapter, not just listed in §8.

Items 1–3 and 9 are mandatory. The rest are strongly recommended.

---

## 8. What survives the battle test cleanly

To balance the criticism: these claims hold up under scrutiny.

- **Multi-resolution storage (L0–L7) and parallel retrieval channels.** The diagram correctly addresses different query types via different layers. The vocabulary-mismatch needle (edge case 1) genuinely is solved by Contextual Retrieval + RAPTOR + rerank.
- **L3 atomic-unit + rarity for *unusual single unit* queries.** Edge case 2 (party + fast delivery) works *if* the L3 extractor is accurate on rare clauses (eval gate covers this).
- **Identity resolution** as deterministic → embedding → LLM-judge → union-find. Robust pattern, well-evidenced in literature.
- **Schema-projection-not-substrate** for the cases where it applies (generic types). The 2-step re-extraction is genuinely a competitive advantage over naive RAG demos.
- **Audit log immutability + per-query reconstructability.** Defensible in front of a reviewer who tests with "show me exactly what the system did at 14:32:17."
- **Cost envelope** at all three scales. The numbers are honest.
- **Refuse-or-cite generation with HHEM gate.** When extraction *is* correct, the refusal path is genuinely better than Lexis+/Westlaw.
- **The 7 UI pages including the Datadog-style upload status table.** Right product surface for this kind of system.

---

## 9. Closing — what would *still* face-palm us under adversarial review

If I were the adversarial reviewer, I would ask:

1. *"Sum total of all our vendor spend last year, broken down by vertical."* — catches F1.
2. *"Subcontractors who delivered late AND had safety incidents in 2025."* — catches F2.
3. *"Latest revision of drawing C7 for plant X."* — catches F6.
4. *"Have we ever signed exclusivity with Adani?"* — catches F5 if their corpus has a misclassified clause.
5. *"What is the most unusual clause across all our contracts?"* — catches F4 (cold-start) if their corpus is below threshold.
6. *"What were the operational risks our board discussed last quarter?"* — catches the latency cliff if it routes to IRCoT.

Of these, the first three should be fixed in the architecture *before* initial deployment. The remaining three should have honest answers prepared in the writeup ("yes, here's the calibrated fallback / cold-start behavior / latency budget").

The architecture is genuinely strong — better than 90% of RAG demos. It is also genuinely vulnerable to specific question shapes that a thoughtful adversarial reviewer will choose. This document is the punch list for closing those gaps before Phase 0.
