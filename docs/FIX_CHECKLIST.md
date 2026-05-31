# Fix checklist — work through one task at a time

> **How to use this doc:** Each task is self-contained. In a fresh chat, pick
> ONE task, read the referenced code properly, then design and apply the fix
> there. This doc only states *what is wrong and what kind of change it needs*
> — it deliberately does NOT contain the fix. The fix is decided per-task,
> with the code open, in its own chat.

---

## 1. What this codebase is

A **domain-agnostic enterprise knowledge base** (a RAG system): upload mixed
documents (PDFs, scans, spreadsheets, emails), ask natural-language
questions, get cited answers. Structure (fields, entities, document chains)
is meant to *emerge from the data* — no schema defined upfront.

**The goal.** Build something competitive with best-in-class systems
(Glean, Hebbia, Onyx). To get there we needed two things:
1. **A trustworthy eval** — a 300-question answer key (6 domains) we can
   actually trust, so "is the system getting better?" has an honest answer.
2. **A system whose numbers approach those best-in-class systems** on that
   trustworthy eval.

**Where we are now.** Step 1 is done: the eval was found to be broken
(stale citations, wrong values, unwritten placeholders) and was rebuilt —
it is now **93% corpus-grounded (280/300)**, every answer traceable to a
quoted passage. So we finally have a reliable ruler. Step 2 — fixing the
*system* so its numbers improve — is what this checklist is for.

The skeleton of the system is sound. The problems are concentrated, named,
and fixable by **consolidation + completion, not a rewrite.**

---

## 2. The architecture we should have (what each part should do)

**Ingestion (write path)** — turns a document into searchable knowledge:

| Stage | What it should do |
|---|---|
| Parse | File → clean text (digital + OCR for scans). |
| Classify | Decide the document TYPE (contract, invoice, lab report…). |
| Chunk | Split into passages using a strategy that fits the type (clauses for contracts, rows for statements). |
| Contextualize | Add a short context prefix to each passage so it's findable. |
| Embed | Turn each passage into a vector for meaning-based search. |
| Summary tree (RAPTOR) | Build per-doc AND corpus-wide summary trees for broad questions. |
| Mentions | Find named things (people, orgs, places). |
| Fields + tables | Extract structured facts; **field names must converge to a shared vocabulary across documents.** |
| Entities + chains | Build the entity hierarchy; link document revisions/amendments into chains. |
| Identity resolution | Merge spelling variants of the same entity into one. |

**Query (read path)** — turns a question into a cited answer:

| Stage | What it should do |
|---|---|
| Resolve follow-ups | Rewrite "its terms" using chat history. |
| Plan | Decide HOW to answer (lookup / aggregate / chain-walk / graph). |
| Retrieve | Search several ways, fuse, rerank to the best passages. |
| Relevance gate | Refuse if the evidence is too weak. |
| Conflict resolution | When documents disagree — **chained OR independent** — surface and resolve it. |
| Generate | Write the cited answer from the passages only. |
| Faithfulness gate | Verify the answer is grounded; refuse/regenerate if not. |

The boxes are right. The work below is fixing what's inside several of them.

---

## 3. Execution roadmap — do them in THIS order (top to bottom)

This is the order to actually work through. Each ID's full spec
(problem / change / where / done-when) is in the sections below. The `why`
(researched SOTA per decision) is in `DECISIONS.md`. **Re-run M1's per-stage
eval after each task** so you can attribute every change.

### ▸ Live status (update after every task)

**▶ CURRENT FOCUS — pre-ingest WRITE-PATH BATCH (then ingest `finance` once).**
Plan: `~/.claude/plans/lets-create-a-plan-peppy-mitten.md`. Batch ALL write-path
changes first, then run the finance ingest ONCE (finance = most tabular domain;
868 md table-rows). HEAD `87807fd`, tree clean, 138 batch tests pass.

Ingestion-batch progress (do tasks ONE-AT-A-TIME — see
`memory/editing-cadence-tooling`; never batch Edit+Bash, never `git stash pop`):
- ✅ **S1** batching (contextualize/mentions/triples via `kb/llm_batching.run_batched`)
- ✅ **I7** transient retry (`is_transient`/`with_retry`; run_batched + parse retry)
- ✅ **I1** classify-before-chunk (`kb.classification`; bank_statement→row chunker)
- ✅ **I2** field-convergence converger (`converge_clusters_semantic`) — *wiring → #19*
- ✅ **I4** identity top-k (`select_entity_match`) + embedder-fail parks file
- ✅ **S3** mentions trigram index (migration 0049, applied+verified)
- ✅ **I3** corpus RAPTOR auto-trigger (`finalize_corpus` self-gates on
  `count_inflight_files`; deferred from per-doc chain tail on file→ready).
  Incremental rebuild deferred to scale (full-teardown is correct/cheap for
  one-shot ≤50-doc ingests). 4 tests. `09fe2e9`. *finalization-phase wiring
  (I2 converge / re-extract / reconcile) lands in #19, expanding
  `finalize_corpus_impl` in place.*
- ✅ **P1 (extraction-side)** config wiring. Shared `_resolve_threshold`
  helper (safe-default fallback, domain from `KB_DEFAULT_DOMAIN`) routes
  identity (0.92/0.85), auto-promotion (0.80/0.90/0.90), field-convergence
  similarity (0.85, new `extraction.l2b.vocabulary.similarity_threshold` key),
  and doc-chain title-sim (0.7/0.8, new `corrigendum_similarity_threshold`
  key) through `layered_config.resolve_config`. 4 tests. `91824f3`/`03dcdb1`/
  `40becc9`. *Query-side P1 + FE (table #11, Phase 3) still pending.*
- ✅ **#19** corpus-finalization pass — `finalize_corpus_impl` now runs the
  full ordered sequence after settle: **(1) field convergence**
  (`converge_workspace_fields_impl` + `kb.extraction.field_judge`, EDC merge,
  injectable embed/judge) → **(2) cold-start re-extraction**
  (`reextract_workspace_schema_entities_impl` + `extract_schema_entities_file_impl(force=True)`
  — stays `ready`, no identity re-chain) → **(3) identity reconcile**
  (`reconcile_workspace_entities_impl` + shared `kb.identity.merge.merge_entity_group`,
  also now used by the dedup script) → **(4) corpus RAPTOR**. 4 slices,
  `a4bec8d`/`79fee41`/`e4afbac`/`e41e5cd`. 13 new tests (fake embed/judge/
  extractor); 135 batch tests green. *Live validation at the finance ingest (#20).*
- ◑ **I6** chain detection — **correctness DONE** (`87807fd`): distinct
  `version_index` via `next_version_index` (loan original+2 addenda → 0/1/2,
  not 0/1/1); explicit path accepts `chain` as alias of `chain_id` (finance
  loan declares `chain:`, complaint declares `chain_id:`). 3 tests. *O(N²)/
  200-doc-window perf still deferred to scale (no-op at ~50 docs).*
- ⏳ **#20** pre-ingest gate → ingest finance once → M1 finance baseline.
  **Key-free pre-ingest verification DONE** (architect pass): (a) I5 large-doc
  risk — none (finance docs ≤~2k tokens vs Gemini ~1M window; I5 stays a
  deferred *cost* optimization); (b) classifier→router alignment — `bank_statement`
  → row chunker fires; other doc_types classify `unknown` pre-chunk (→
  hierarchical, fine for markdown) and correct to manifest doc_types at
  `extract_kv_tables` via frontmatter; (c) **row chunker handles markdown
  pipe-tables** (line-based → one chunk per transaction row, no garbage).
  **Remaining = DYNAMIC (needs keys/worker):** 1-doc smoke (classify+row-chunk+
  extract end-to-end) → staged ingest (statements → loan/complaint chains →
  widen) → M1 finance baseline.

> Pre-existing (NOT my regression): `tests/test_b4b_api.py` 2 failures
> (StubPlanner `.plan()` missing `conn` kwarg) — fail identically at `ff0ceea`.

**Operating mode:** construction only (`c0000000-…001`, 46 docs ingested);
other 5 domains not ingested yet — deferred until construction build is done.
Measurement-driven: re-run the M1 per-stage eval after each task and record the
delta here. **Testing cadence:** small fix → only the **affected queries**
(`--ids`), plus a spillover slice if the change is cross-cutting; **full 50 only
after a substantial fix or a batch** (then snapshot a new baseline). See
`docs/M1_STAGE_EVAL.md` → "Testing cadence". Reranker = Cohere `rerank-v3.5`.

> **Eval is stochastic** (LLM intent/planner/rewriter + Cohere rerank vary
> run-to-run). A ±1–2-question wobble on a stratum is **noise** — only
> attribute deltas above that to a change, and prefer the per-question CSV
> (which stage moved on *which* question) over headline averages. Multiple-run
> averaging / a held-out set is E1 (later).

**Commitment:** the **whole list below (all 33 tasks, Phase 0→6, each task's
full done-when, no shortcuts) is the definition of done.** Nothing here is
dropped. Completing it includes **ingesting the other 5 domains** at the point
the plan needs them (Phase 1 I1/I2/I4 validation is best measured on tabular
domains; Phase 5 scale needs the full corpus) — that is the "go to other
domains after the current work" stage, not a skip.

**Sequencing (judgment, measurement-led — order only, not scope):** the order
below was authored *before* per-stage measurement. M1 shows construction's
losses are **not** in retrieval/chunking (r@30=0.97, rerank_ret=1.00) but in
**generation-citation** and **negative-refusal**. So we work the **Phase-2
items construction can measure now first** (the only domain ingested), then the
write-path foundation (Phase 1) + Phases 3–6, which need the other domains.
Done out of numeric order, but **every task is tracked to completion** in the
master table below.

#### Master status — all 33 tasks (legend: ✅ done · ◑ partial · ⏳ pending)

| # | Task | Phase | Status | Note / measured effect |
|---|---|---|---|---|
| 1 | **M1** per-stage harness (+Cohere reranker) | 0 | ✅ | `0fef654`. Phase-0 baseline; query ~25s→13s. `docs/M1_STAGE_EVAL.md`, D9 |
| 2 | **I1** classify-before-chunk + clause/row chunker | 1 | ✅ | `kb.classification` pre-chunk classifier (vocab-constrained) wired into chunk_file_impl → bank_statement chunks row-aware. 6 tests. Clause chunking still hier-backed (markdown-limited; PDF follow-up). D2 |
| 3 | **I2** field convergence (EDC) | 1 | ✅ | converger `converge_clusters_semantic` + `field_judge` wired as corpus pass `converge_workspace_fields_impl` (#19, `79fee41`). 5+3 tests. D3 |
| 4 | **I4** identity resolution (top-k) | 1 | ✅ | top-k `select_entity_match` (per-doc) + post-ingest `reconcile_workspace_entities_impl` sweep on shared `merge_entity_group` (#19, `e4afbac`). 6+3 tests. D4 |
| 5 | **Q1** conflict across independent docs | 2 | ⏳ | depends I2+I4. D8 |
| 6 | **Q5** faithfulness (claim-decomp + span verify) | 2 | ◑ | **C2** did the relevance-gate/override slice (negative refuse 0.00→0.67, no over-refusal; `6ae2571`). **Remaining:** claim-decomposition + span verification (D6); **C2b** entity-grounded q009 case. |
| 7 | **P2** answer confidence signal + reason | 2 | ✅ | `derive_answer_confidence` (high/med/low + reason from faithfulness+CRAG); auto-set on every ChatResult via validator → exposed on `/chat`; FE confidence badge in `AnswerCard.tsx`. 5 tests. (FE wired, not browser-verified.) D6 |
| 8 | **Citation honesty** (kill fake-cite fallback) + **P5** page-range | 2 | ✅ | **C1** grounded aggregate citations (0.00→1.00; `0079118`); **fake-citation fallback killed** (`45d7da3`); **P5** page-range (citation reports `pp. X–Y`; mechanism-tested — not construction-visible, markdown corpus). D6 |
| 9 | **Q3** strip corpus-specific facts from generator prompt | 2 | ⏳ | after I1/I2/Q1. D5/NFR |
| 10 | **Q2** collapse 13-mode facade → ~4 honest modes | 2 | ⏳ | D5 |
| 11 | **P1** wire pipeline to read layered config | 3 | ◑ | **Extraction-side DONE** (`91824f3`/`03dcdb1`/`40becc9`): identity, promotion, field-sim, doc-chain thresholds via `_resolve_threshold`→`resolve_config`, safe defaults. 4 tests. **Query-side (CRAG 0.5 etc.) + FE Settings still ⏳.** D7 |
| 12 | **P3** committed, loadable demo-schema artifact | 3 | ⏳ | D7 |
| 13 | **P1b** define-from-scratch schema + onboarding | 3 | ⏳ | D7 |
| 14 | **P6** FE: failure reasons + schema version view | 3 | ⏳ | |
| 15 | **P4 + F1** schema-change re-extraction loop | 3 | ⏳ | |
| 16 | **I5** contextualization cost cap/cache | 4 | ⏳ | |
| 17 | **I7** transient-failure retry + visibility | 4 | ◑ | retry done: is_transient+with_retry in llm_batching; run_batched + parse_file_impl retry transient 429/timeout/5xx. 5 tests. Part 2 (degraded-rate visibility event) optional, not blocking. |
| 18 | **OCR per-page escalation** | 4 | ⏳ | D1 |
| 19 | **Q4** per-channel DB connections | 4 | ⏳ | |
| 20 | **Q6** IRCoT: fix env-var or delete | 4 | ⏳ | |
| 21 | **Q7** per-turn cost cap | 4 | ⏳ | |
| 22 | **Cheap bugs** (`/tmp` dump, etc.) | 4 | ⏳ | |
| 23 | **S1** batch per-chunk/entity LLM calls | 5 | ✅ | `kb/llm_batching.run_batched` + batched contextualize/mentions/triples; I2/I4 judges use the same primitive. Tests in `test_s1_batching.py`. (#1 100k blocker — cleared.) |
| 24 | **S3** `mentions_exact` trigram index | 5 | ✅ | migration 0049: `CREATE EXTENSION pg_trgm` + GIN `gin_trgm_ops` on `lower(mention_text)`. Applied + verified on running DB. |
| 25 | **S2** identity-resolution throughput | 5 | ⏳ | |
| 26 | **I6** chain detection O(N²)→bounded | 5 | ◑ | **Correctness DONE** `87807fd`: distinct `version_index` + `chain` alias for `chain_id`. 3 tests. O(N²)/200-window perf deferred to scale. |
| 27 | **I3** corpus RAPTOR incremental + auto-trigger | 5 | ◑ | `09fe2e9`. Auto-trigger DONE (`finalize_corpus` self-gates on `count_inflight_files`; deferred from chain tail). Incremental rebuild deferred to scale. 4 tests |
| 28 | **S4** vector memory/recall at ~3M vectors | 5 | ⏳ | |
| 29 | **S5** retrieval result cache | 5 | ⏳ | |
| 30 | **S6** bulk-ingest + docs/hour benchmark | 5 | ⏳ | |
| 31 | **R1** retrieval recall — measure (M1) then improve | 6 | ⏳ | M1 ready; construction retrieval already strong |
| 32 | **A1** query decomposition (agentic) | 6 | ⏳ | |
| 33 | **E1** grow + hold-out eval | 6 | ⏳ | (variance/averaging caveat noted above) |

> **Sub-items spun off** (tracked so they're not lost):
> - **C2b** — entity-grounded relevance for the q009-class (asked entity/premise
>   absent from retrieved docs; CRAG sits at the neutral 0.5 default so a
>   threshold can't separate it). Folds into #6 Q5 / #32 A1.
> - **C3 — mode-K chain_view** ◑ (`24d365f`; folds into **#10 Q2**). Real root
>   cause found: the Gemini planner emits an **invalid** `chain_view` (a topic
>   string like "safety incident-fall"); `_route_k_mode` coerced that to
>   `current_version`, whose filter DROPPED the chain members → chain-walk
>   questions cited the wrong, non-chain docs. Fixed at source: `_parse_plan_json`
>   infers the view from the query when chain_view is absent or invalid.
>   Affected-query check: q014+q038 cite the full safety-001 chain. **BUT** the
>   full-50 aggregate stayed flat (cite 0.86) — run-to-run variance (~±2 Q:
>   q014 flipped back, q033 aggregation flipped out) swamps the gain. q017
>   (cites revB not revA/revC) + intermittent q014 remain — ranking nuance,
>   folds into #31 R1. *(version_index mis-set: revB&revC=1, investigation&
>   corrective=1 — minor backfill.)*
>
> **⚠ Measurement note (important):** single full-50 runs CANNOT reliably
> detect 1–2-question deltas — the pipeline is stochastic (Cohere rerank +
> Gemini planner/rewriter/generation) with ~±2-question run-to-run noise.
> **Trust the per-affected-question check** (deterministic improvement on the
> target) over the full-run aggregate for small fixes; use multi-run averaging
> for headline numbers (**E1 #33**).
> - **q025** (conflict) — borderline: mode-H CRAG `force_refuse` at crag≈0.5
>   flips refuse↔ship run-to-run; when it ships it cites the wrong doc. Hard
>   case; folds into #5 Q1 / #6 Q5. Not a C2 regression.

> **Phase-0 baseline** (50 Q, `construction_phase0.*`): `r@10=0.93 r@30=0.97
> mrr=0.82 rerank_ret=1.00 cite=0.75 faith=0.66 refuse=0.57` ·
> `ok=27 lost_retrieval=1 lost_rerank=0 lost_generation=8 refused✗=3`.
>
> **Current** (after Phase-2 batch C1/C2/#8/P2/C3, `construction_after_phase2batch.*`):
> `cite≈0.86–0.92 refuse=0.86 ok≈31–33/36` (range = run-to-run variance).
> Retrieval/rerank unchanged (not the bottleneck). C3 confirmed on
> affected-query checks (q014/q038 chain citations) but flat in the noisy
> aggregate. Remaining construction losses → **C2b** (q009), **q017**
> (revB vs revA/revC ranking), **q025** (borderline conflict/CRAG); these
> fold into R1/Q2/Q1, not quick wins.

> **DEFINITION OF DONE: every task on this list must be completed — no
> shortcuts.** The phases are an *order*, not a menu. We may *submit* an early
> snapshot, but the system must be fully built (through Phase 6) before the
> call. The tags below indicate *priority/sequence*, NOT optionality — nothing
> here is "skippable." A task is done only when its **Done when** criterion is
> met; "mostly working" does not count (that's how the facades got here in the
> first place).

Tags (priority/sequence only — **all are required**): **[SUBMIT]** earliest /
makes a claim true · **[QUALITY]** robustness/honesty · **[SCALE]** 100k ·
**[STANDOUT]** raises the ceiling.

**Phase 0 — Measurement (unblocks everything; do first)**
1. **M1** [SUBMIT-enabler] ✅ **DONE** — per-stage measurement harness. Cheap
   now that the eval is trustworthy; lets you localise every later change
   (retrieval vs rerank vs generation). Without this you fix blind — the trap
   that cost a week. → see `docs/M1_STAGE_EVAL.md` (harness, how-to, dev-loop
   policy, and the Phase-0 construction baseline). Construction baseline:
   retrieval/rerank are solid (r@30=0.97, rerank_ret=1.00); the losses are in
   generation-**citation** (aggregation cite=0.00) and **negative refusal**.

**Phase 1 — Write-path foundation (everything downstream inherits this)**
2. **I1** [SUBMIT] ✅ — classify before chunk. Highest leverage (SOTA: structure-
   aware chunking is "the single biggest, easiest win"). *Done: pre-chunk
   classifier wired; clause chunker still hier-backed (PDF follow-up).*
3. **I2** [SUBMIT] ◑ — field convergence (EDC: embedding-block + LLM-judge). Makes
   "structured outputs matching schema" + cross-doc aggregation real. *Converger
   done + tested; corpus-pass wiring → #19.*
4. **I4** [SUBMIT] ✅ — identity resolution (semantic blocking, top-k not top-1).
   Entities must resolve before conflict can work. *Done: top-k + park-on-fail.*

**Phase 2 — Read-path correctness + the brief's promises**
5. **Q1** [SUBMIT] — conflict across independent docs. *Depends on I2 + I4.*
6. **Q5** [SUBMIT] ◑ **partial** — faithfulness via claim-decomposition + span
   verification (replace the Jaccard default). *Done:* C2 relevance-gate /
   override (`6ae2571`). *Remaining:* claim-decomposition + span verify; C2b.
7. **P2** [SUBMIT] — answer confidence signal + reason, derived from Q5's
   per-claim verification.
8. **Citation honesty** [SUBMIT] ◑ **partial** — kill the fake-citation fallback
   (§5 bug) + **P5** page-range. *Done:* C1 grounded aggregate citations
   (`0079118`). *Remaining:* fake-citation fallback + P5.
9. **Q3** [SUBMIT] — strip corpus-specific facts from the generator prompt.
   *Do after I1/I2/Q1 so quality holds.*
10. **Q2** [QUALITY] — collapse the 13-mode facade to ~4 honest modes.

**Phase 3 — Domain-agnostic: config + schema + onboarding (the central claim)**
11. **P1** [SUBMIT] — wire the pipeline to *read* the layered config. Makes the
    "swap config per domain, no code" promise actually work.
12. **P3** [SUBMIT] — committed, loadable demo-schema artifact + loader
    (explicit deliverable §5.3).
13. **P1b** [SUBMIT] — define-from-scratch schema + one-step domain onboarding.
14. **P6** [SUBMIT] — FE surfacing: show failure reasons (§2.2) + schema version
    view (§2.1). (The rest of the FE is already built.)
15. **P4 + F1** [QUALITY] — close the schema-change re-extraction loop (no re-parse).

> **— Earliest-submittable checkpoint (NOT a stopping point) —** After Phase 3
> the system meets the brief end-to-end and *could* be submitted as a snapshot.
> **Do not stop here.** Phases 4–6 are required to complete the build before the
> call — they are what make it stand out and are not optional.

**Phase 4 — Robustness & cost [QUALITY]**
16. **I5** — contextualization cost (cap/cache, or evaluate late chunking).
17. **I7** — transient-failure retry + visibility.
18. **OCR per-page escalation** — stop re-OCRing the whole doc for one bad page
    (DECISIONS D1).
19. **Q4** — per-channel DB connections.
20. **Q6** — IRCoT: fix the env-var and keep, or delete. Not a hidden no-op.
21. **Q7** — per-turn cost cap.
22. **Cheap bugs** — remove the `/tmp` parse dump, etc.

**Phase 5 — Scale to 100k [SCALE]**
23. **S1** — ingestion throughput: batch the per-chunk/per-entity LLM calls (the
    #1 blocker; folds in **I8**).
24. **S3** — `mentions_exact` trigram index (kill the full table scan).
25. **S2** — identity-resolution throughput (batch the judge).
26. **I6** — chain-detection O(N²) → bounded/index-backed.
27. **I3** — corpus RAPTOR incremental + auto-trigger.
28. **S4** — vector memory/recall tuning at ~3M vectors.
29. **S5** — retrieval result cache.
30. **S6** — bulk-ingest path + docs/hour benchmark.

**Phase 6 — Stand-out accuracy [STANDOUT]**
31. **R1** — retrieval recall: measure (M1) then improve the weakest lever.
32. **A1** — query decomposition (agentic) for hard multi-hop / aggregation.
33. **E1** — grow + hold-out eval (anti-overfitting).

**Deferred:** ACL / PII / live connectors (later, role-based track — §9 below).

**Final — the write-up (§5.4 deliverable):** architecture diagram · the 3
trickiest decisions (DECISIONS.md D2/D3/D5 are strong candidates) · what you'd
do with more time (Phases 4–6) · where it breaks today, honestly (this
checklist). The honesty here is rewarded — don't hide the gaps, frame them with
the researched fixes.

---

## 4. INGESTION tasks (do these first — the write path feeds everything)

### I1 — Classification happens AFTER chunking, so type-aware chunkers are dead
**Problem.** Chunking reads the document type, but the type isn't decided
until a later stage — so it's always empty at chunk time. The clause-aware /
row-aware / message-aware chunkers therefore never run for PDFs or DOCX;
those get a generic splitter. Chunking is never redone after the type is
known. Wrong retrieval granularity is baked in at the root and inherited by
every downstream stage.
**Change type.** Reorganize pipeline order (classify earlier) OR add a
re-chunk step after classification.
**Where.** `src/kb/workers/tasks.py` (chunk stage ~`:497`, classify stage
inside `extract_kv_tables` ~`:1428`), `src/kb/chunking/doc_type_router.py`.
**Done when.** A PDF contract is chunked clause-aware and a PDF statement
row-aware (not the generic splitter).

### I2 — Field names never converge across documents
**Problem.** Extracted field names are merged by exact-string match only,
so the same concept under two names (e.g. two spellings of "total cost")
stays split forever. The real merger (meaning-based) was deferred in a
docstring and never built. A separate 80%-prevalence gate blocks rare fields
from ever canonicalizing. Result: ~577 distinct field names across 46
documents, and cross-document aggregation is structurally impossible.
**Change type.** Build the missing converger; delete/replace the
exact-match-only clusterer; revisit the prevalence gate.
**Where.** `src/kb/extraction/promotion.py`, `src/kb/extraction/kv_tables.py`.
**Done when.** Two documents expressing the same field under different names
resolve to one canonical field; aggregation across them returns one number.

### I3 — Corpus-wide summary tree (RAPTOR) is never built during ingestion
**Problem.** The corpus-level summary tree is implemented but only runs from
a manual endpoint that does a full teardown-and-rebuild. In normal ingestion
it's never triggered, so "summarize everything about X across the corpus"
runs against an empty corpus tree.
**Change type.** Add an automatic trigger; make the rebuild incremental
rather than full-teardown.
**Where.** `src/kb/workers/tasks.py` (corpus RAPTOR ~`:3688`), the ingestion
chain wiring.
**Done when.** Corpus-scope retrieval works after normal ingestion with no
manual step.

### I4 — Identity resolution is fragile and can silently create duplicates
**Problem.** Entity-merge uses a top-1 nearest-neighbour only (misses a true
match that's the 2nd neighbour), with hardcoded similarity thresholds. Worse:
if the embedder hiccups for a file, **every** entity in that file is created
as brand-new, silently polluting the entity graph with duplicates.
**Change type.** Redo the matching logic (consider top-k candidates, handle
embedder failure as retry-not-create, revisit thresholds).
**Where.** `src/kb/workers/tasks.py` (`resolve_identities_file` ~`:2488`),
`src/kb/identity/resolve.py`, `src/kb/domain/entities.py`.
**Done when.** A transient embedder error never creates duplicate entities;
true matches that aren't the single nearest neighbour are still merged.

### I5 — Contextualization re-sends the whole document on every chunk
**Problem.** Each chunk's context call includes the entire (uncapped)
document text, and the default model path has no prompt caching — so a
50-chunk document re-sends its full body 50 times. This is the dominant
ingestion cost and can blow the input limit on large documents.
**Change type.** Rework to cap/cache the document context (or batch).
**Where.** `src/kb/workers/tasks.py` (`contextualize_file` ~`:658`),
`src/kb/contextualization/__init__.py`, `src/kb/domain/contextual_chunks.py`.
**Done when.** Per-document context cost is roughly flat in document size,
not multiplied by chunk count.

### I6 — Document-chain detection does an O(N²) scan with a 200-doc window
**Problem.** Detecting whether a new doc is a revision/amendment of an
existing one scans up to 200 sibling files and issues one DB read per
sibling, every time a doc is ingested. It breaks well before large corpora,
and a true predecessor older than the 200 most-recent files is invisible.
**Change type.** Redo the sibling-matching to be index-backed / bounded, not
a per-sibling scan.
**Where.** `src/kb/workers/tasks.py` (`detect_doc_chain_file` ~`:2686`),
`src/kb/extraction/doc_chains.py`.
**Done when.** Chain detection cost is sub-linear per doc and finds
predecessors regardless of corpus size.

### I7 — Transient failures silently drop data or park documents
**Problem.** A parse failure marks a doc failed with no retry; per-chunk
extraction failures are swallowed and skipped. A momentary network/model
blip permanently loses data with no visible signal.
**Change type.** Add retry/back-off and surfacing for transient failures;
distinguish transient from permanent.
**Where.** `src/kb/workers/tasks.py` (parse `:270-284`, mentions
`:1344-1348`, triples `:3206-3208`).
**Done when.** A transient failure retries and recovers; only genuine
failures park a doc, and they're visible.

### I8 — Per-chunk LLM calls won't scale (lower priority)
**Problem.** Mention extraction and triple extraction make one model call
per chunk — linear in total chunks across the corpus.
**Change type.** Reorganize to batch multiple chunks per call.
**Where.** `src/kb/workers/tasks.py` (mentions ~`:1274`, triples ~`:3132`),
`src/kb/extraction/mentions.py`.
**Done when.** Calls scale per-document, not per-chunk.

---

## 5. QUERY tasks (after the write path is solid)

### Q1 — Conflict resolution only works for chained documents
**Problem.** Conflict detection only compares documents that already belong
to the same revision chain. Two independent documents that disagree (two POs
with different rates, two RFIs, two invoices) are never flagged — that case
is shoved into a prompt instruction with no real detection, no record, no UI
surfacing. This is the headline feature only half-delivered.
**Change type.** Rewrite the gating so conflicts are detected on
resolved-entity + predicate for any two documents, then classified
(chain-supersession vs independent-transaction vs contradiction).
**Where.** `src/kb/query/conflict_resolution.py` (gate ~`:337`, `:412`),
`src/kb/query/conflict_detector.py`.
**Done when.** Two independent disagreeing documents produce a surfaced,
resolved conflict.

### Q2 — The "13 retrieval modes" are 1 real retriever + a facade
**Problem.** Only one mode actually runs the search; one runs SQL; the other
eleven just filter/boost the same candidate set, several are dead (commented
out), several are redundant clones, and a blanket fallback restores the
default hits whenever a mode filters to zero — so most "modes" do nothing but
add a tag. ~1500 lines implementing an abstraction that mostly doesn't do
what its name claims.
**Change type.** Delete the dead/redundant modes; reorganize down to a small
honest set (hybrid / SQL / chain / graph); remove the zero-result fallback.
**Where.** `src/kb/query/mode_router.py`, `src/kb/query/planner.py`,
`src/kb/query/channels.py` (disabled channel ~`:605`).
**Done when.** Every remaining mode does distinct, real work; no dead code;
the planner's choices map to genuinely different behaviour.

### Q3 — The generator prompt is doing retrieval's and conflict's job
**Problem.** The answer-writer's prompt contains corpus-specific facts and
compensation rules (count items, don't confuse these two fields, handle
conflicts the detector can't). When the prompt memorizes *this* corpus, the
system has stopped being domain-agnostic — it's papering over upstream gaps.
**Change type.** After I2/Q1 land, strip the prompt back to general rules;
delete the corpus-specific and compensation sections.
**Where.** `src/kb/query/generate.py` (system prompt ~`:163-328`).
**Done when.** The prompt has no corpus-specific facts; quality holds because
the upstream stages now do their job.

### Q4 — All retrieval channels share one DB connection
**Problem.** The parallel retrieval channels run on a single database
connection, and correctness is held together by nested transaction
savepoints. One channel's failure can cascade into downstream stages
(citations, conflict, audit). The code itself notes the right fix is a
per-channel connection pool.
**Change type.** Redo retrieval to use independent connections per channel.
**Where.** `src/kb/query/orchestrator.py` (~`:740-798`),
`src/kb/query/channels.py` (~`:64-102`).
**Done when.** A single channel failure can't corrupt other channels or
downstream stages.

### Q5 — The faithfulness gate is weak and can be overridden
**Problem.** The default "is the answer grounded?" check is simple word-
overlap (its own docstring says it's not a real hallucination detector), and
even when it refuses, the relevance score can override it and ship the answer
anyway. Two weak gates wired to cancel each other.
**Change type.** Redo the gate (stronger grounding check; remove or rethink
the override).
**Where.** `src/kb/query/faithfulness.py`, `src/kb/query/orchestrator.py`
(override ~`:1159-1188`).
**Done when.** A non-grounded answer is reliably caught and not shipped.

### Q6 — The iterative-reasoning path (IRCoT) is silently dead
**Problem.** The one iterative element reads the wrong environment variable
name for its model key, so in any normal deploy it degrades to a no-op and
the whole "escalate instead of refuse" path never runs — hidden behind a
bare catch-all.
**Change type.** Decide: fix the env-var (cheap) and keep it, OR delete it if
not pursuing iterative retrieval now. Don't leave it dead.
**Where.** `src/kb/query/ircot.py` (~`:206`), `src/kb/query/orchestrator.py`
(~`:893-963`).
**Done when.** The iterative path either works or is removed — not a hidden
no-op.

### Q7 — No cost ceiling per question
**Problem.** A single question can fan out to a dozen-plus model calls with
nothing stopping it. No per-turn cap or budget exists.
**Change type.** Add a per-turn call/cost cap with a clean refusal when
exceeded.
**Where.** `src/kb/query/orchestrator.py` (the `chat()` flow).
**Done when.** A pathological question is bounded, not unbounded.

---

## 6. Cheap real bugs (quick, low-risk — can be batched)
- **Q6 env-var fix** if keeping IRCoT (`ircot.py:206`).
- **Remove the hard-coded `/tmp/kb-parse-errors.log` dump** in the request
  path (`generate.py` ~`:516`).
- **Citation fallback** synthesizes 3 citations even when the model cited
  nothing — decide whether an un-grounded answer should ship with fake
  citations (`generate.py` ~`:629`). (This is the "citation honesty" item in
  the roadmap — a hard requirement, not just cleanup.)
- **OCR per-page escalation** (DECISIONS D1) — the parse quality-gate re-OCRs
  the **whole document** to fix one bad page (`workers/tasks.py` ~`:418`).
  Escalate only the failing pages. Efficiency, not correctness.

---

## 7. Requirements gaps vs the original assignment (P-series — these are in the brief)

The original assignment requires specific things that are **built as
infrastructure but not actually wired**, or missing as deliverables. These
aren't "nice to have" — they're stated requirements. (Deployment is fine:
the brief says "docker-compose preferred" and we have one-command bootstrap.)

### P1 — Config UI exists but the pipeline ignores it (a *false affordance*) (§2.5, §3 domain-swap)
**Problem.** The brief's central promise is "swap config + schema per domain,
no code change." The **surfaces all exist**: a Settings → Overrides UI with a
`defaults → global → domain → workspace → doc_type → doc → user` scope chain
(including a **domain** scope), the `config_overrides` table, and a
layered-config resolver. **But the pipeline reads none of it** — grep confirms
**zero** files in `src/kb/query/`, `src/kb/extraction/`, `src/kb/workers/`
import `layered_config`. Every threshold/prompt is a hardcoded constant
(identity 0.92/0.85, CRAG 0.5, promotion 0.80, rarity 1.0, chain 0.7/0.8,
prompts inline). So a user can open the UI, set a per-domain override, save it
— and the engine runs the hardcoded value anyway. The control *looks*
functional but has no effect. The domain-swap promise is scaffolded, not wired.
**Change type.** Reorganize — make the pipeline **read** config through
`layered_config.resolver` at runtime, replacing the hardcoded constants. No
new infra; connect the engine to the config surface that already exists.
**Where.** the constants across `src/kb/query/` + `src/kb/extraction/` +
`src/kb/workers/`; `src/kb/layered_config/resolver.py`; `src/kb/api/settings.py`.
**Done when.** changing a threshold/prompt in the Settings UI at the **domain**
scope actually changes pipeline behavior for that domain, with no code edit.

### P1b — No "define a domain schema from scratch + swap/onboard" flow (§2.1, §3)
**Problem.** Schema Studio lets a user edit *fields/columns* of an
**auto-discovered** schema, but there's no clean flow to **define a whole new
domain** upfront (entities + containment relationships + NL field descriptions)
and **load/swap it in one step** to onboard a new domain — which §3 requires
("swapping schema + data should be enough to onboard a new domain"). Ties to P3
(no committed, loadable demo schema artifact).
**Change type.** Add — a define-from-scratch schema path in the FE/API + a
one-step "load this domain's schema (+config)" onboarding flow.
**Where.** `ui/app/schema-studio/`, `src/kb/api/schemas.py` +
`schema_hierarchy.py`, the loader from P3.
**Done when.** a new domain can be onboarded by defining/loading its schema +
config from the UI, then uploading data — no code change.

### P2 — No answer-level confidence signal with a reason (§2.4)
**Problem.** The brief requires every answer to carry a **confidence signal
with a brief reason**. Internally we have a CRAG score, a faithfulness verdict,
and intent confidence — but none is surfaced as one answer-level confidence +
reason to the user.
**Change type.** Add — derive and surface a single confidence signal + a
one-line reason on each answer, AND a FE component to display it (no
confidence widget exists in `ui/components`).
**Where.** `src/kb/query/orchestrator.py` (assemble), `src/kb/api/query.py`
(expose), the generation result shape, `ui/components/AnswerCard.tsx` (display).
**Done when.** each answer returns a confidence value + a short why, and the
chat UI shows it.

### P3 — Demo schema isn't a committed, loadable artifact (§5.3 deliverable)
**Problem.** The deliverable requires a **demo schema committed in the repo,
loadable via a script**. We auto-discover schema from data but ship no
user-defined schema artifact + loader (no schema file exists in
`demo-corpus/`).
**Change type.** Add — commit a demo schema (the user-defined view: things,
fields, NL descriptions, relationships) + a one-command loader; or export the
auto-discovered schema as a loadable snapshot.
**Where.** `demo-corpus/`, `scripts/`.
**Done when.** a fresh setup loads the demo schema with one script.

### P4 — Schema-change re-extraction loop is half-built (§2.2) — overlaps F1
**Problem.** The brief requires that changing the schema and re-running the
schema-driven part of ingestion must **NOT redo expensive parsing**. The
plumbing exists (corrections defer `scope='extraction'`), but the code says
the actual re-extraction "is wired in a follow-up" — it's not closed, and
there's no general "schema changed → re-extract affected docs (skip
parse/chunk/embed)" path.
**Change type.** Complete — close the re-extraction loop and expose a
schema-change → re-extract path that reuses parsed/chunked/embedded data.
**Where.** `src/kb/domain/corrections.py`, the worker re-extraction tasks,
schema-edit endpoints. (Do with F1.)
**Done when.** editing the schema re-derives structured data for affected docs
without re-parsing.

### P5 — Verify page-RANGE provenance (§2.4) — minor
**Problem.** The brief requires source file → **page range** → exact excerpt.
Citations carry a single page (first page of the chunk), not necessarily a
multi-page range.
**Change type.** Verify; extend to a range if it's single-page only.
**Where.** `src/kb/query/citations.py`.
**Done when.** a citation spanning pages reports the page range.

### P6 — Frontend surfacing gaps (§2.2 failures, §2.1 schema-evolve) — verify/complete
**Problem.** The FE is otherwise comprehensive (12 pages: upload, dashboard,
chat+citations, files, schema-studio, extraction-studio, explore, audit,
settings, playground). Two surfaces look thin against the brief:
(a) **Failure visibility** — §2.2 wants "clear visibility into status AND
**failures**". The UI shows a status/stage badge but appears NOT to surface the
failure *reason* (no `error_message`/`error_class` rendered in `ui/`); a file
that failed shows "failed" with no why.
(b) **Schema evolve/version view** — §2.1 wants view/manage/**evolve**. Editing
exists; a version-history / diff / rollback view is thin.
**Change type.** Verify each in the running UI; complete the thin one(s) — show
failure reasons on a failed file; surface schema version history.
**Where.** `ui/components/FilesTable.tsx`, `ui/app/files/[id]/`,
`ui/app/dashboard/`, `ui/app/schema-studio/`; the failure data is already on
`file_lifecycle` / `files` (error_class, message, traceback).
**Done when.** a failed file shows WHY it failed in the UI; schema versions are
viewable.

> **FE completeness note.** The other UI requirements are already built —
> citations (`CitationsPanel`/`SourceViewer`: file→excerpt, page is P5), the
> "what the system did" inspector (audit page), lifecycle status
> (`FilesTable`/`StageBadge`), config editor (settings — wiring is P1), chat +
> conversational context. Remaining FE work is mostly *making existing screens
> functional* (P1 config, P2 confidence, P6 above), not building new ones.

> **Two requirements that overlap tasks already listed — but note they are
> hard requirements, not cleanup:**
> - **Domain-agnostic (§3 NFR: "no domain values hardcoded in source").** The
>   corpus-specific facts in the generator prompt (**Q3**) directly violate
>   this. Q3 is a compliance requirement.
> - **"Cited or it didn't happen" (§3 NFR).** The fake-citation fallback (in
>   §5 cheap bugs) ships synthesized citations for an uncited answer — it
>   violates this. Fix it, don't leave it.

---

## 8. Will the tasks above reach Glean's accuracy? (Honest answer + what's still missing)

**No — the tasks in §3–§5 are necessary but not sufficient.** They remove the
facade and complete what was half-built, taking the system from "claims more
than it delivers" to "a strong, honest document-QA system." But they are
mostly *bug-and-gap fixes*. Matching Glean's **accuracy** needs a few things
that are **ceilings, not bugs** — listed below. And matching Glean as a
*product* needs an enterprise track that is deliberately out of scope here
(noted at the end).

These were missing from the first list. Add them so this is done once.

### M1 — Per-stage measurement harness (do this FIRST of this group)
**Problem.** The eval only measures end-to-end (right/wrong answer). There is
no measurement of *where* accuracy is lost: did retrieval surface the correct
document in the top-k? did rerank keep it? did generation use it? Every fix
is currently made blind. The eval now has **verified expected-citations +
evidence quotes** (from the rebuild), so this is suddenly cheap.
**Change type.** Build a stage-level scorer: retrieval recall@k (did the
verified citation appear in the fused candidates / top-10), rerank precision,
generation faithfulness — reported per stratum and per domain.
**Where.** `src/kb/eval/scorer.py` (extend), plus the verified
`queries.yaml` citations.
**Why it's load-bearing.** You cannot close an accuracy gap you can't
localise. This makes every other task measurable in isolation.

### R1 — Retrieval recall (the input to everything)
**Problem.** Several known failures are "the right document was never in the
candidate set" — no rerank or prompt can recover that. Retrieval recall has
never been measured or tuned (hybrid BM25/dense weighting, query expansion,
the BM25 sanitizer that strips tokens, chunk granularity from I1).
**Change type.** Measure recall@k (via M1), then improve the weakest lever
(weighting / expansion / chunking / embedding choice). Possibly add a
stronger retrieval method than single-vector dense.
**Where.** `src/kb/query/channels.py`, `src/kb/query/rrf.py`,
`src/kb/query/rewriter.py`, embedding config.
**Why.** Retrieval is the ceiling on everything downstream — generation can
only be as good as what reaches it.

### A1 — Query decomposition for hard multi-hop + aggregation (the agentic move)
**Problem.** The pipeline is single-pass: retrieve once, answer once. Hard
questions ("compare X across all contracts", "which deals share an
arbitration venue") cannot be answered in one shot. This is the real
architectural ceiling vs Glean/Hebbia, which decompose a question into
sub-questions, retrieve/extract per sub-question, then compose.
**Change type.** Add an iterative path: decompose → retrieve per sub-question
→ compose → verify. (The dead IRCoT in Q6 is a weak seed of this — replace,
don't patch.) Gate it to hard queries to control cost.
**Where.** `src/kb/query/orchestrator.py` (control flow), a new decomposition
module.
**Note.** This is bigger than the others — treat it as its own phase. It is
the single largest accuracy lever beyond the bug-fixes.

### F1 — Complete the feedback / correction loop
**Problem.** The system is supposed to learn: when an answer is wrong, the
correction routes back to targeted re-extraction and the system improves. The
plumbing exists (corrections are recorded and routed) but the actual
re-extraction loop is deferred and not closed — so the system does not learn
from corrections.
**Change type.** Complete the wiring: a correction triggers re-extraction of
the implicated docs and the improvement persists.
**Where.** `src/kb/domain/corrections.py` (route ~`:501`, defer ~`:614-639`),
the worker re-extraction tasks.
**Why.** "Improves with use" is a Glean-class property and a stated goal.

### E1 — Grow and diversify the eval (anti-overfitting)
**Problem.** The eval is 300 questions across 6 domains. It is now
trustworthy, but it is *small*. A system can hit 90% on 300 questions and
still fail the long tail Glean handles. Tuning hard against 300 risks
overfitting to them.
**Change type.** Over time, expand the question set and hold out a portion as
a blind test set never used for tuning.
**Where.** `demo-corpus/domains/*/queries.yaml`, the eval harness.
**Why.** Accuracy that generalises ≠ accuracy on a fixed 300.

---

## 9. SCALE to 100k documents (in scope — do alongside accuracy)

Goal: the pipeline works correctly AND performs at ~100k documents in a
workspace (≈3M chunk vectors, millions of mentions). The index *foundation*
is actually sound — HNSW (`m=16, ef_construction=200`) on halfvec(3072)
embeddings + real ParadeDB BM25, not naive search. The scale gaps are in
**ingestion throughput**, **a few un-indexed query paths**, and **memory
planning** — not the retrieval core.

**Already on the list and scale-relevant** (don't duplicate — just keep the
scale lens when doing them): **I3** (corpus RAPTOR must be incremental, not
full-rebuild), **I5** (contextualization cost per doc), **I6** (chain
detection O(N²) → bounded), **I8** (per-chunk LLM calls → batched), **Q4**
(per-channel connections under concurrent load).

The genuinely-uncovered scale gaps:

### S1 — Ingestion throughput is gated by per-unit LLM calls (the #1 100k blocker)
**Problem.** Ingestion makes LLM calls **per chunk** (contextualize, mentions,
triples) and **per entity** (schema entities, identity judge). At 100k docs ×
~30 chunks that's *millions* of model calls, bounded by the provider's
requests-per-minute ceiling — ingesting 100k docs would take days, not hours.
This is the dominant scale wall.
**Change type.** Reorganize the per-unit stages to **batch** many
chunks/mentions/entities per call; measure docs/hour end-to-end. (Subsumes
I5/I8 into one throughput strategy.)
**Where.** `src/kb/workers/tasks.py` (contextualize, mentions, triples,
schema-entities, identity), the extraction modules.
**Done when.** Ingestion throughput is measured and bounded by batch calls,
not per-chunk calls; 100k docs is hours-scale.

### S2 — Identity resolution runs per-mention inside ingestion
**Problem.** Each mention does a vector nearest-neighbour query plus a possible
LLM judge call, inline in the ingest path. At millions of mentions this both
slows ingestion and floods the judge.
**Change type.** Batch the judge calls; bulk the nearest-neighbour lookups;
consider moving resolution to a post-ingest pass that can be parallelised.
(This is the *throughput* angle — I4 is the *correctness* angle; do them
together.)
**Where.** `src/kb/workers/tasks.py` (`resolve_identities_file` ~`:2488`),
`src/kb/identity/`.
**Done when.** Identity resolution cost scales sub-linearly with batching and
doesn't dominate ingest time.

### S3 — `mentions_exact` retrieval channel is a full table scan
**Problem.** The mentions channel matches with `lower(mention_text) LIKE
'%query%'` (`channels.py:357`). A leading-wildcard LIKE **cannot use an
index**, so it scans the entire `extracted_mentions` table (millions of rows
at 100k docs) on every query. Confirmed.
**Change type.** Redo with a trigram GIN index (`pg_trgm`) or replace the
substring match with a tokenised/indexed lookup; or fold mention matching
into the BM25 path.
**Where.** `src/kb/query/channels.py` (`mentions_exact_channel` ~`:326`), a
new migration for the index.
**Done when.** The channel uses an index; query latency is flat in corpus
size.

### S4 — Vector store memory + recall at ~3M vectors
**Problem.** halfvec(3072) is ~6KB/vector → ~18GB of chunk embeddings + HNSW
graph (~25-30GB resident) at 100k docs. HNSW wants to live in RAM for latency;
index *build* needs `maintenance_work_mem`; runtime recall depends on
`ef_search`, which is never tuned. None of this is measured. (Single shared
index also means workspace-filtered search post-filters — fine for one big
workspace, a recall problem if many small workspaces share it.)
**Change type.** Measure index size + recall@k at scale; tune `ef_search` /
build memory; document the RAM requirement; decide partition strategy if
multi-workspace.
**Where.** `migrations/sql/0013_indexes.sql`, retrieval config,
`src/kb/query/channels.py` (dense channels).
**Done when.** Recall@k and p95 latency are measured at ~3M vectors and meet a
target; RAM requirement is documented.

### S5 — No retrieval result cache
**Problem.** Every query recomputes all channels + rerank from scratch.
Enterprise traffic is FAQ-skewed; the same questions repeat. Nothing is
cached.
**Change type.** Add a result cache keyed on (workspace, normalised query,
mode), invalidated on ingest into the workspace.
**Where.** `src/kb/query/orchestrator.py` (retrieval entry), a cache layer.
**Done when.** Repeated queries hit the cache; measurable hit-rate on
repeated load.

### S6 — No bulk-ingest path / throughput measurement
**Problem.** Ingestion is one-file-at-a-time via the worker chain, with no
bulk-load path and no end-to-end throughput measurement. You can't improve
docs/hour you don't measure.
**Change type.** Add a throughput benchmark (docs/hour to `ready`) and a
bulk-ingest entry that batches where possible; scale workers horizontally
(Procrastinate supports it — the bottleneck is LLM RPM, see S1).
**Where.** worker entry, `scripts/`, a benchmark harness.
**Done when.** docs/hour-to-ready is a tracked number; 100k docs has a known,
acceptable ingest time.

---

## 10. Explicitly deferred (NOT now — later / role-based track)
- **Permissions / ACL / role-based access** and **PII redaction at rest** —
  deferred to the role-based work, out of scope for this Q&A + scale push.
- **Live source connectors** (SharePoint, Drive, Slack) — file-upload only
  for now.

These do not block the accuracy or scale work.

---

## 11. Before you touch any task — read the code first

For every task above: **open the referenced files and read them properly
before changing anything.** These stages are interdependent (the lifecycle
state machine, the shared connection, the savepoints), and a change that
looks local can break a neighbouring stage. Specifically:
- Understand what the stage reads/writes and what triggers the next one
  before reordering anything (I1, I3).
- Confirm the current behaviour with a quick trace before deleting "dead"
  code (Q2, Q6) — make sure it's actually dead.
- Re-run the eval (now trustworthy at 93%) after each task, in isolation, so
  you can attribute any change in the numbers to that one task.

Follow the §3 roadmap order (Phase 0 → Phase 6). One task per chat. Read, then
fix. **Complete the whole list — every task, through Phase 6 — before the
call. No shortcuts.** A task counts as done only when its **Done when**
criterion is genuinely met and the per-stage eval confirms it; "mostly works"
is not done. The point of this whole exercise was that "mostly works" hiding
behind a facade is what we're fixing — don't recreate it.
