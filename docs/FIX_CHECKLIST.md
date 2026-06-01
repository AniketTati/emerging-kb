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

**▶▶ OUTPUT-VERIFIED COVERAGE + SYNTHESIS-MODE FIX + SUBSET RE-EXTRACT —
`eee6627`.** Ground-truth-verified ALL 13 modes (`scripts/verify_outputs.py`
checks answers against DB-computed values, not just "did it answer"): **8/8
hard numeric/count checks match the DB exactly** (Q debits=866,958,265.22;
I=8 bank statements; A top-rarity 4.25 / 48.24M; K 8.85→9.40; H 9.40%; E
turnover 184.2cr; T/M Northwind); C/D/S/G spot-checked correct.
- **Fix 7 (`eee6627`)** synthesis modes (G/S/T) no longer over-refused —
  `keep_low_confidence_answer_visible` ships a non-H `low_confidence` answer
  with a badge (CRAG is structurally ~0 for synthesis). G summary ships;
  adversarial/out-of-corpus still refuse. 5 tests.
- **SUBSET re-extract (`scripts/reextract_loans.py`)** — re-ran KV+Tables +
  schema-entities (force, CACHED chunks, no re-parse) on the **6 finance loans
  ONLY**. Fixed the rate inconsistency (apollo **0.0985→9.85**; all now
  8.35–9.85, **0 fraction rows**). → F-mode "rate over 9%" now filters from the
  STRUCTURED layer (resolver maps "interest rate"→stored `interest_rate_all_in`)
  and "average interest rate" returns the **correct 9.25%** (was bogus 6.03%).
  **A subset re-extract is sufficient to verify the fix** — don't need all 46.
  (Correction: my first GT check used wrong keys `interest_rate`/`principal_amount`;
  finance stores `interest_rate_all_in`/`loan_amount_usd` — the data existed,
  the real defect was the 0.0985 row, now fixed.)
- **Honest gaps still open:** (a) multi-turn **follow-ups UNVERIFIED** (in-process
  harness can't persist sessions — RLS); (b) **real-conflict** (docs that
  actually disagree) untested; (c) the OTHER finance narrative docs
  (10-K/treasury/audit) likely need the same re-extract; (d) **test-coverage
  debt** (safe-cast exec test, mode-miss, full-text grounding — live-verified,
  not CI-locked). **⚠ Stopped + restarted the worker for the re-extract.**

**▶▶▶ DEEP QUERY-PIPELINE REVIEW + 6 FIXES (live, finance ws `f0000000`) —
`0462251`,`81dd268`,`38dcd5e`,`00db02d`,`0804770`,`c2d08c3`.** User report: "a
lot of answers weren't even coming." Probed one query per flow type (probe tool
`scripts/probe_query.py`); **6 of 12 flows returned NOTHING.** Root-caused to 4
issues, each fixed + live-verified one at a time:
- **Fix 1 (`0462251`)** unit_type fragmentation broke C/A/M — planner matched
  unit_types EXACTLY, so stemmed 'transaction' ≠ stored `transaction_listing`/
  `transactionlisting`/`major_transaction`/… → C/A downgraded to H → refused.
  Added `_resolve_unit_types` (normalize + ≥5-char-substring match). → "show
  transactions over 100000" (C, 35 hits) + "unusual transactions" (A,
  rarity-scored) now answer with cites.
- **Fix 2 (`81dd268`)** Q-mode treated a doc-type as a SQL table
  (`from:'bank_statement'` → allowlist reject). Added `discover_doc_type_unit_types`
  + a doc_type→unit_types hint + prompt rule (doc-types aren't tables; scope via
  unit_type IN [variants]). → "total debits across bank statements" now plans
  from:extracted_entities.
- **Fix 3 (`38dcd5e`)** the `fields.x::numeric` cast ABORTED the whole
  aggregation on one dirty value ('USD 2.2M') → "sum of all transactions"
  refused at execution. Compiler now emits a guarded validate-then-cast
  (dirty→NULL, skipped). → "sum of all transactions" → debits 866,958,265.22 /
  credits 995,171,457.0 with **aggregate + source-file citations**.
- **Fix 4 (`00db02d`) + LLM-gate activation** the faithfulness gate fed on
  TRUNCATED `snippet_preview` → under-scored CORRECT answers (workspace summary
  REFUSED; 9.40% factoid scored ~0). Now grounds on FULL cited-hit text. Plus
  `KB_FAITHFULNESS_GATE=llm` activated in `.env` (lite gemini-2.5-flash-lite;
  API restarted). → workspace summary SHIPS (pass 0.89); factoid/entity/mention/
  multi-hop/scoped all 0.29–0.47 low_conf → **pass 1.0 / high**; fabricated
  claims still refuse.
- **Fix 5 (`0804770`)** mode-miss fallback — "is there any disagreement about X
  across docs" mis-routed to A-mode → generator self-refused on off-target
  anomaly rows. Now, when a non-H/Q/I mode REFUSES, retry generation once on the
  pre-mode hybrid hits (strictly additive — can't overwrite a working answer).
  → "disagreement about Acme turnover" now answers "No disagreement — all report
  INR 184.2 crore" (pass 1.0). Anomaly still answers (fallback doesn't fire).
- **Fix 6 (`c2d08c3`)** inventory specific-count — "how many bank statements"
  dumped the 46-doc table; now headlines "You have **8 bank statements**" then
  the breakdown (`_match_query_doc_type`, plural-tolerant). Generic asks
  unchanged.
- **SCORECARD:** all 6 dead flows answer; all weak-confidence flows now high;
  conflict misroute + inventory-count fixed. **Remaining = ONE, and it's NOT a
  query bug:** finance `interest_rate_all_in` stored inconsistently (0.0985 vs
  9.65) → misleading AVG — needs an **ingestion re-extract** (percent-
  normalization predates this corpus; no query fix can reconcile mixed stored
  values). **⚠ Env: API restarted with `KB_FAITHFULNESS_GATE=llm`; native API
  on :8000.**

**▶▶ QUERY PHASE STARTED — STRUCTURED QUERIES LIT UP E2E (the query half of
FIX 4) — `a42e326`, `1798ee6`, `4ebfbe7`.** With ingestion rebuild done, the
data was ready (typed `interest_rate` 8.5/9.0/9.4 on loan doc_roots, ws
`f2b2…`) but the QUERY path never reached F-mode's filter. Three one-at-a-time
edits closed it:
- **1/3 (`a42e326`)** `_parse_plan_json` PARSES `field_filters` — it read every
  other plan field but silently DROPPED field_filters, so even a correct LLM
  emission was lost → F-mode always got `()` → unfiltered H pass-through. Adds
  `_parse_field_filters` (validates `{field,op,value}`, drops malformed/unknown-op,
  coerces stringy numerics `'9%'/'1,000'`→float for lt/le/gt/ge). 7 tests.
- **2/3 (`1798ee6`)** routing prompt now ADVERTISES field_filters (schema key +
  op set eq/ne/lt/le/gt/ge/like/in + snake_case names + a worked loan/rate
  example) and steers F-vs-C (F for DOCUMENT types, C for row types).
- **3/3 (`4ebfbe7`)** F-mode canonical name mapping (`_resolve_field_name`):
  exact → normalized (case/space/hyphen folded) → unambiguous token-subset
  (`'rate'`→`interest_rate` iff sole rate-key; ambiguous→None, never guesses).
  Stored keys are the canonical anchors. 5 tests incl. apply_mode E2E + ge/gt
  9.0 boundary. **141 planner+mode_router tests green.**
- **🎯 LIVE E2E (native API :8000, Gemini planner, ws `f2b2…`):** NL query
  "which loans have an interest rate over 9%" → **mode F**, planner emits
  `field_filters=[{interest_rate, gt, 9}]` (canonical name direct), F-mode keeps
  ONLY amendment-2 (9.4), cites it, `refused=False crag=1.0`. Boundary-inclusive
  phrasings ("9% or higher" / "at least 9%") → `ge 9` → BOTH amendment-1 (9.0) +
  amendment-2 (9.4), exclude the 8.5 original. The acceptance set {9.0,9.4} is
  produced by the inclusive phrasing; "over"=strict gt is the literal reading
  (NOT hacked to ge). Structured numeric filtering from the structured layer
  works end-to-end with correct citations. **NOTE: F-mode is a POST-retrieval
  filter** — it narrows hits retrieval already surfaced; works here because the
  3-doc loan chain is fully retrieved. **NEXT:** Q5 citation-attribution +
  faithfulness-gate lever; then the two cheap UI wins (degraded_extractions in
  Needs-Review; Schema Studio "Apply changes" button).

**▶▶ Q5/D6 — LLM FAITHFULNESS GATE built + wired + validated — `e49839d`,
`3be41f4`.** ⚠️ **Premise correction:** the brief said the gate is a "no-op
IdentityGate" — it is NOT. `make_faithfulness_gate()` already defaults
`auto→heuristic` (changed after IdentityGate let invented entities through),
`KB_FAITHFULNESS_GATE` is unset → the LIVE gate is **`heuristic-jaccard-v1`**.
The real problem is the OPPOSITE of the brief: weak Jaccard token-overlap
**under-scores grounded answers → over-refusal** (a correct "9.4%" answer scored
**0.15**, exactly on the refuse cliff). (The stale `test_b3_unit` "auto→Identity"
failures are the test asserting the *old* default.)
- **D6 fix (`e49839d`)** new `LLMFaithfulnessGate`: claim-decomposition
  (`split_sentences`) + ONE batched per-claim entailment call on a lite LLM →
  per-claim supported→1.0/not→0.0 averaged → `verdict_from_score` (HHEM bands,
  since entailment scores like NLI not Jaccard). Provider-neutral (injected
  `JsonLLMClient`); fail-safe PASSES on LLM error/unparseable verdicts (never
  refuse a good answer on a judge hiccup); refuses only real no-evidence /
  unsupported claims. 13 unit tests (fake client).
- **Wiring (`3be41f4`)** `KB_FAITHFULNESS_GATE=llm` → `_make_faithfulness_llm_client`
  (provider follows `KB_PLANNER`/keys, model defaults to LITE
  `gemini-2.5-flash-lite`, override `KB_FAITHFULNESS_MODEL`); no key → degrades
  to heuristic. `auto` stays heuristic in code (measured rollout). 3 factory
  tests (16 in file). b3 suite unchanged (same 4 pre-existing failures).
- **🎯 VALIDATED on the real lite model + full in-process pipeline (ws `f2b2…`):**
  fabricated "5.0%" vs the 9.4% evidence → **refused** (catches hallucination);
  the correct "9.4%" answer heuristic scored 0.15 → LLM **0.5 low_confidence /
  medium** — it SHIPS instead of hiding at the cliff. Per-claim judging is real
  (affirms the core rate claim, docks ungrounded add-on claims like an
  effective-date not in the cited snippet). `gemini-2.5-flash-lite` confirmed
  valid (no errors). **Per-affected-question signal (the trustworthy kind per
  the variance caveat) = clear win.**
- **TO ACTIVATE LIVE:** set `KB_FAITHFULNESS_GATE=llm` + restart the native API
  (it reads env at launch; `--reload` won't pick up a new env). **Remaining:**
  rigorous over-refusal number = full/multi-run finance eval under llm vs
  heuristic (E1-style; small single runs are swamped by ±2-Q variance) — then
  decide whether to flip the code default `auto→llm` (degrades to heuristic
  without a key, so CI stays safe). Citation-attribution (q012 sibling-source)
  is the OTHER half of row #6, still open — the per-claim gate is the
  groundwork (each claim verified vs its cited source).

**▶ FINANCE INGEST COMPLETE (46/46 ready, ws `f0000000`).** Staged ingest
(statements → chains → full corpus) surfaced + fixed 5 real bugs: I1 never
wired into chunk_file_impl (`1d4b803`); schema-bootstrap races under concurrent
same-doctype ingest (`b98a3cd`); chain `version_index` race → deterministic
renumber pass (`924bdfe`); out-of-range mention confidence parking docs
(`06e5f02`); deadlocks from duplicate workers (operational — run ONE worker).
Validated: all 9 doctypes classified, row-chunks on statements, loan chain
v0/v1/v2 (addendum-2 current), complaint v0/v1, corpus RAPTOR (L2:2+L3:1),
538 entities / 5877 mentions / 922 fields / 6901 triples.
**Quality gaps (eval-coupled → Phase C, NOT pipeline defects):** (a) identity
reconcile + field convergence UNDER-merge (HDFC vs HDFC BANK separate;
account_number vs account_no) — conservative judges, tune with eval; (b)
`resolve_identities` lacks deadlock-retry (S2/scale); (c) query path needs
`KB_RERANKER=bge`→`cohere`/`auto` (stale docker API env) + the Q-tasks.
**ARTIFACT AUDIT (per-doc × per-artifact coverage + integrity) — PASSED after
one fix.** Caught: #19 cold-start re-extraction was DESTRUCTIVE — force-mode
delete-parents-then-insert wiped doc_root entities when the re-run yielded 0
(identity extractor on a transient KB_ENTITY_EXTRACTOR=auto/Gemini miss):
complaint-005 → 0 entities, wire-005 → lost parent. Fixed `1d65e76` (guard
delete on `_has_new_parents`; +regression test) and restored both docs via
Gemini re-extract. **Re-verified: 46/46 docs have full artifact set** (raw_pages,
chunks, contextual_chunks, embeddings [0 missing], per-doc RAPTOR root, mentions,
doc_root parent), corpus RAPTOR (3 nodes), 2 chains (5 members). Ingestion data
COMPLETE + INTEGRITY-VERIFIED. (Remaining = consolidation under-merge quality,
eval-coupled — NOT a completeness gap.)
**▶ P3 DONE (schema import + loader + demo artifact).** Added the inverse of
`GET /schemas/export.yaml`: `POST /schemas/import.yaml` (`5a0930c`) parses the
export YAML (or a hand-authored equivalent) and upserts each schema's full
subtree via `restore_subtree` (the rollback engine), bumping a version per
touched schema — new names created, existing active names updated in place;
relationships accepted (export omits them). Validation runs in Python first
(field types / rel kinds / cardinalities / from-to resolvability) → clean 400,
no txn poisoning. Plus `scripts/load_schema.py` (one-step loader) + a real
inverse-importable `demo-corpus/domains/finance/schema.yaml` (11 entity types =
9 doc types + Organization + Person, 67 fields, 8 relationships) (`7537ace`).
10 tests (create / round-trip / in-place update + field-drop / 5×400 / demo-
artifact drift guard). `kind` stays `put` (schema_versions CHECK allows only
post/put/rollback — no migration). This unblocks P1b's one-step domain load.

**▶ P1b DONE (schema onboarding from the UI) — `2f24a1a`.** Schema Studio now
has two header actions, both funneling through the single atomic
`POST /schemas/import.yaml` (no per-row idempotency-keyed mutations): a 4-step
**"New schema"** wizard (schema → entities & fields → relationships → review;
builds a `SchemaImportDoc` and POSTs it as JSON — JSON ⊂ YAML) and an **"Import
YAML"** loader (paste / upload a .yaml). New FE client fns `importSchemaYaml` /
`importSchemaDoc` + types; self-contained `SchemaImportWizard.tsx` (own modal)
leaves the 2213-line `page.tsx` untouched but for header wiring. `tsc` clean;
3 vitest tests (26 FE green). **Verified live** against the native API (had to
swap the stale docker API — it predated the route, 405): both flows create
schemas end-to-end (`[created] … v2 — N entities, M fields, K rels`), no console
errors; test schemas cleaned up after. **Remaining for full P1b done-when:** the
"+config" one-click bundle (load schema *and* domain config in one step) — that
half is coupled to **P1** (config wiring, row 11, query-side still pending), not
to the schema flow.
**⚠ Env change this session:** stopped the stale docker `…-api-1` (405 on the
new route) and ran the **native** API on :8000 via `./scripts/dev_api.sh`
(reads `dev_env`, correct reranker). Native API + UI dev server (:3000) are the
live stack now. `docker start knowledgebaseservice-api-1` to revert.

**▶ P6 DONE (FE surfacing) — `2bcfb0a`.** (a) Failed-file reasons: FilesTable's
expanded detail now renders WHY a file failed — `error_class` / `message` /
collapsible traceback off the `to_state:"failed"` lifecycle payload (was a bare
"failed" badge). Pure exported `failureReasonFrom()` + 4 vitest cases (finance
has 0 failed docs → unit-tested rather than live; verified a *ready* file shows
no banner). (b) Schema version view: a "Versions" header action opens a
master-detail modal — `listSchemas` → drill into `listSchemaVersions` (version#,
kind post/put/rollback, parent, timestamp). Verified live (auto:* → v1/post),
no console errors. 30 FE tests green, `tsc` clean.

**▶ NEXT-THREAD PLAN → `docs/PIPELINE_FIX_PLAN.md`** — consolidated, code-grounded
implementation plan for the structured-layer rebuild (10 fixes in dependency
order + validation). Expands DQ1–DQ4 + chunking + retry/coverage + re-extract.
Start there.

**▶ FIX 1 DONE (decouple per-doc storage from promotion) + FIX 8 (orphans).**
Per-doc fields now reach the queryable `extracted_entities` doc_root regardless
of promotion. Written in `extract_schema_entities_file_impl` PASS 1 (not
kv_tables) — that pass already owns doc_root parents, the `_has_new_parents`
non-destructive guard, and lineage, so it dodges a kv_tables→schema-entities
clobber race and fixes FIX 8 orphans in the same spot. 3 commits:
- **Step 1/3 (`ecc4597`).** Additive domain helpers, no pipeline change:
  `read_proposed_fields_for_file` (per-doc reader keeping `value_numeric`) +
  pure `build_doc_root_fields` (prefers numeric → real numbers for F-mode range
  predicates, text fallback, drops valueless/nameless, last-write-wins). 8 unit
  tests, no DB (`tests/test_doc_root_fields_unit.py`).
- **Step 2/3 (`6a3925e`).** Wire `build_doc_root_fields` into PASS 1: merge base
  fields into the LLM-inserted doc_root (LLM promoted values win on collision →
  promoted-field behavior unchanged; non-promoted facts like a loan's rate now
  land on the queryable record).
- **Step 3/3 (`6acd7bf`).** Always create a doc_root from base fields when the
  LLM produced none (frontmatter-only / no-promotion docs — the Acme loans),
  but ONLY when no parent already exists → preserves a richer prior parent on a
  transient-empty re-extract (keeps the #19 guarantee; delete guard untouched).
  Resolves FIX 8 (0% orphans) — every doc now has a doc_root for children to FK.
- **Numeric typing:** already handled — `proposed_fields.value_numeric` is
  computed at insert via `normalize_value`; the bridge prefers it.
- **Acceptance is re-extract-gated:** the rate lands for docs whose
  proposed_fields hold it (loan-002/003/004). The 3 Acme loans have `gem_pf=0`
  (transient empty extraction) → need FIX 2 (retry) + FIX 9 (re-extract re-runs
  KV+Tables) before their rate appears. Full M1 verification deferred to FIX 9.
- **Flagged (out of scope):** `test_kv_tables_worker.py::test_extract_kv_tables_writes_scalars_and_tables`
  is pre-existing-broken (stale FakeExtractor missing `existing_scalar_hints`;
  asserts removed `atomic_units`) → spawned a separate task.

**▶ FIX 2 DONE (`55bd197`) — retry the KV+Tables extraction.**
`llm_batching.with_retry` got an additive optional `retry_on` predicate
(defaults to `is_transient` → no other caller changes). `extract_kv_tables_file_impl`
now wraps `extractor.extract` in `with_retry` with a predicate that retries
transient errors (429/timeout/5xx are embedded in the wrapped
`KVTablesExtractionError` message) AND the empty `"no candidates"` completion
— the actual cause of the 3 Acme loans landing `gem_pf=0`. On exhaustion,
falls through to the empty-payload path → FIX 3 coverage flags it. 2 new
`with_retry` tests (custom retry_on recovers after a non-transient flake;
still skips a true permanent error).

**▶ FIX 3 DONE (`fcac0f8`, `5eeb00e`) — frontmatter=metadata + coverage check.**
- `0050` migration: `files.extraction_coverage` jsonb + `files.extraction_degraded`
  bool (+ partial index); table-level GRANT already covers them. Applied to dev DB.
- `extract_kv_tables_file_impl` counts BODY scalars (captured before the
  frontmatter guard appends its synthetic status scalar) vs `frontmatter:auto`
  fields; a text-rich doc (has chunks) with 0 body fields AND 0 table rows is
  recorded `degraded`. Frontmatter is metadata → excluded from the extracted-OK
  signal. (Excluding frontmatter from *promotion* deferred to FIX 5 to avoid
  double-churn on the promotion path.)
- `GET /knowledge-map/needs-review` surfaces degraded docs (`KMDegradedDoc` +
  `degraded_extractions[_total]`, additive). 2 API tests. Doc still reaches
  `ready` (so corpus finalize settles) but is flagged for review — the
  forward-only lifecycle DAG can't host a `needs_review` state without breaking
  in-flight settling.
- **Stale tests flagged (out of scope, spawned tasks):** `test_files_crud`
  (2 stale exact-key assertions) + `test_kv_tables_worker` (stale FakeExtractor
  + atomic_units asserts).

**▶ FIX 5 DONE (`a9765c7`) — count-based promotion (not 80% prevalence).**
`should_promote` now promotes a type-stable field seen in ≥ `promote_count`
docs (default 2) OR clearing prevalence (keeps first-doc seeding);
stability/value_type_confidence still gate noise, `min_docs` is the floor.
`PromotionThresholds.promote_count` (env `KB_PROMOTION_COUNT`), wired into both
promotion sites via `extraction.l2b.auto_promotion.promote_count` + documented
in `defaults.yaml`. Tests updated to the new semantics (3/6 repeat promotes;
one-off + type-unstable rejected) + tunable-count test. 20 green. **This already
lights up same-NAME repeats** (cluster_fields groups identical names across
docs); cross-NAME synonym merge is FIX 4.
- **Remaining half of FIX 5 (defer with FIX 4):** "user-declared fields are in
  the schema by declaration" (extracted by declared name, no count needed).

**▶ FIX 4 NEXT (the big remaining item) — sameness/canonical names as a
first-class layer.** Largest fix; re-extract-coupled (FIX 9). Code-grounded plan:
- `converge_clusters_semantic` (`promotion.py:115`, embed→cosine-block→LLM-judge
  union) ALREADY exists and ALREADY feeds promotion at corpus finalize
  (`converge_workspace_fields_impl`, the ~4450 block now also resolves
  promote_count). The gaps the plan calls out:
  1. **Per-doc promotion still counts raw names** (`extract_kv_tables_file_impl`
     ~2078) — convergence only runs at finalize. Decide incremental vs
     finalize-only (finalize-only may be acceptable now that FIX 5 makes
     same-name repeats promote; the cross-name merge then lands at finalize).
  2. **Query layer doesn't map to canonical** — the planner emits raw field
     names; F-mode (`mode_router._route_f_mode`) matches `name in fields` exactly.
     Needs a query-time field-name → canonical map (anchored on user-declared
     schema names) so "rate" resolves to stored `interest_rate`.
  3. **Table-name + column-name drift have NO semantic merge** — only scalar
     names converge. `singularize_unit_type` handles plurals but not
     `transactionlisting` vs `transaction_listing`.
  4. **User-declared schema names are the anchors** (everything maps to them);
     keep `original_name` for provenance.
- Acceptance: `all_in_rate`/`interest_rate_all_in`/`post_amendment_rate` → one
  canonical `interest_rate`; `transactionlisting`/`transaction_listing` → one
  `Transaction`.

**▶ FIX 4 (table names) DONE (`6a23a49`).** ensure_sub_entity_type reuses an
existing type by normalize_unit_key (alnum-only + lowercase + singularize), so
transaction_listing / transactionlisting / Transaction Listing / Transactions
collapse onto one type (first spelling wins, non-destructive). Scalar-name
convergence already feeds promotion at finalize (converge_clusters_semantic) +
FIX 5 promotes the merged clusters. **Remaining (query-coupled, NOT ingestion):**
query-time field-name→canonical mapping in the planner/F-mode; denormalized
unit_type string canonicalization; semantic head-noun reduction.

**▶ FIX 6 DONE (`362f5e5`) — block-level table chunking.** row_per_leaf emits
one L0 leaf per block of rows_per_mid rows (parented to root), not one-per-row;
no redundant mid layer. 100-row statement → 1 root + ceil(100/N) leaves.
Auto-merge still works; rows never split; prose chunker untouched.

**▶ FIX 7 DONE (`abed921`, `7a022fb`) — identity noise gate + better merge.**
(A) is_noise_mention_text drops doc-IDs / ref numbers / URLs / email-domains /
rate-benchmarks ('HDFC MCLR') at mention creation; type-aware (numeric/temporal
types exempt — a real DATE isn't a doc-ID). (B) select_entity_match routes
short-form/full-name variants (HDFC ⊂ HDFC BANK, prefix rule) to the LLM judge
even below the low cosine threshold; judge still gates (no auto-merge on name);
mention_name=None preserves legacy behavior.

**▶ FIX 9 DONE (`62d185e`) — corpus re-extract re-runs KV+Tables.**
extract_kv_tables_file_impl(force=True) runs on a ready file from cached chunks
(re-discovers body fields under FIX 1/2/3/4/5), stays ready (no transition, no
re-defer), non-destructive on transient-empty. reextract orchestrator now runs
KV+Tables(force) THEN schema-entities(force) per file. 2 DB-backed tests
(re-discovers interest_rate + stays ready; empty re-run preserves existing).

**▶ FIX 10 DONE (`41baed9`) — schema-change / correction → re-extract triggers.**
New tasks reextract_file (per-file force) + reextract_workspace_files
(workspace/doc-type force); reextract impl gains a doc_type filter.
bump_schema_version defers a coalesced (queueing_lock), doc-type-scoped re-extract
on every schema CRUD/import. corrections scope='extraction' now defers the REAL
reextract_file (was the dead extract_fields_file). NOTE: procrastinate defers
don't land in procrastinate_jobs in the local test env (pre-existing raptor
defer test fails identically) → enqueue-landing not asserted; doc_type-scope
derivation + clean-bump tested; 50 schema + 21 correction tests green.

**▶ FIX 4 (A) DONE (`453e734`) — declared-name anchoring.** converge_clusters_semantic
takes anchor_names (user-declared field names, auto_promoted=false): emergent
variants merge INTO the declared name (all_in_rate/post_amendment_rate →
interest_rate), lone anchors dropped. Wired in converge_workspace_fields_impl.
Column-name canonicalization: DETERMINISTIC part (case/space/separator) already
done at the kv_tables write boundary (_snake_case on column names + row.values
keys); semantic cross-doc column-synonym merge grouped with query-side FIX 4.

**▶ FIX 5 (declared half) VERIFIED (`97b9851`) — no code change needed.**
User-declared fields extract by declaration: read_active_schemas_for_doctype
returns auto + user schemas, read_schema_entities_with_fields surfaces ALL
active fields (no auto_promoted filter) → declared field drives
extract_schema_entities by its name, FIX 1 merges onto doc_root. Regression
test locks it.

**▶ MAX-EFFORT CODE REVIEW (9-angle) — correctness fixes landed.** Reviewed the
session diff (150fe8f..HEAD). Must-fix set, each tested + committed:
- **#1 (`8fb9ea8`)** force re-extract replaces children PER unit_type, not a
  blanket delete (partial re-run no longer wipes unit_types it missed — data loss).
- **#2/#3 (`b78bd68`)** mention noise gate type-scoped + dominance-based — stops
  dropping real entities (PERSON 'Sonia', ORG 'Prime Rate Capital', PRODUCT
  'Boeing 747-400'/'AK-47', EVENT 'COVID-19') while still dropping doc-IDs/benchmarks.
- **#4 (`0585833`)** doc_root identified by schema_entity_id, not just
  `unit_type IS NULL LIMIT 1` (was ambiguous with user-declared parent entities).
- **#14 (`57c0bff`)** drop non-finite value_numeric (NaN/Inf) — LATENT ::jsonb
  write crash for any NaN numeric.
- **#5 + #11 (`f9c8730`)** reextract_file guarded on `ready` (no race with live
  ingest); promote_count floored at 1 (no always-promote misconfig).
- **Deferred (tracked):** #10 per-doc-field citations (feature, needs chunk-id
  translation); **#6/#7/#8 FIX 10 trigger redesign** (whole-workspace blast +
  defer-in-txn + queueing_lock lost-update → spawned task: schema→doc_type
  association + after-commit debounced enqueue); #9 chunk auto-merge granularity,
  #12 alias-judge cost, #13 doc_root 3-way merge precedence, + cleanup themes.
  143 tests green.

**▶ LIVE INGEST VALIDATION (one-doc-at-a-time, real API+worker, Gemini) — PASSED,
+3 bugs found & fixed.** Built a purpose-built corpus (`demo-corpus/ingest-validation/`):
a 3-doc loan chain (original + 2 amendments, explicit chain frontmatter, interest
rate under 3 phrasings) + a cross-type invoice + the real `bank-statement.xlsx`.
Ingested each through the full pipeline; reviewed with `scripts/review_ingest.py`.
**Confirmed working on live data:** doc-chain linking (3-member versioned chain,
live/superseded); cross-doc + cross-TYPE entity resolution (Acme across 3 loans +
invoice; NorthWind across invoice + statement; **HDFC/'HDFC' short-form merge**);
per-doc-type schema evolution + promotion gate; FIX 1 per-doc store; FIX 3
coverage (xlsx no-frontmatter honest test); **FIX 6 block chunking on a real xlsx
(14 txns → root + 2 block leaves)** + 14 typed `transaction` children (closes the
real-binary xlsx E2E gap).
**3 bugs the live run surfaced (all fixed + tested):**
- **#1 (`27f3e35`)** percent/rate values weren't numeric (`'8.5%'` → text); `normalize_value`
  now strips `%`/`per annum`/`p.a.` → face-value number.
- **#2 (`631e6aa`)** the schema-driven LLM reinterprets numbers inconsistently
  (`8.5%`→`0.085`, `₹2.2 crore`→`2.2`); doc_root merge now makes the deterministic
  base numeric authoritative (LLM only fills keys base lacks).
- **#3 (`c035851`)** multi-segment doc-IDs typed PRODUCT/LAW (`LN-2026-001-v1/v2`)
  became entities; noise gate now drops ≥3-segment codes regardless of type.
- **🎯 PROOF:** after re-extract, `interest_rate` is numeric (8.5/9.0/9.4) and an
  `interest_rate >= 9` filter returns amendment-1 + amendment-2, excludes the
  original — the FIX 1 headline acceptance works on live data. No doc-ID entities;
  HDFC/Acme short forms merged. 127 tests green.

**▶ FIX 10 RE-DESIGNED → EXPLICIT "APPLY" (`3334307`, `47b5668`).** Architect call:
re-extraction is now user-triggered, not auto-fired per schema edit. Removed the
auto-enqueue from `bump_schema_version` (which caused the edit storm +
defer-in-request-txn + lost-update-while-running all at once). Added
`POST /schemas/{id}/re-extract` ("apply changes" → one scoped force re-extract
from cached chunks) + FE `reextractSchema()` client fn. No queueing_lock, so a
later apply always runs against the live schema. The three FIX 10 dangers are
GONE. **Remaining (much smaller, tracked):** schema→doc_type association so a
*declared* schema's apply scopes to its doc_type instead of the whole workspace;
+ the FE "Apply" button placement/visual-verify in Schema Studio.

**▶ CROSS-DOMAIN VALIDATION on FRONTMATTER-FREE PDFs (healthcare) — PASSED.**
Built 3 real PDFs (no YAML frontmatter) for one patient across 3 doc-types (lab
report / discharge summary / insurance EOB), sharing patient/hospital/physician
(`demo-corpus/ingest-validation/build_healthcare.py`). Confirmed every layer on
the honest no-metadata path: Docling parse; **classification with no frontmatter
hint** (3 distinct doc-types); **coverage frontmatter_fields=0**, none degraded;
FIX 6 row-blocking on a real PDF → **7 `test_result` children**; **numeric typing**
(EOB `Rs. 1,84,500` → 184500, Indian-comma + Rs. parsed); 3 independent emergent
schemas; **cross-TYPE entity resolution** (Rohan Mehta / Apollo Hospitals / Priya
Sharma each → ONE entity across all 3 docs, 'Dr.' normalized); **noise gate #3**
drops the real doc-IDs (patient_id/member_id/claim_number) from entities while
KEEPING them as queryable fields — the layered design exactly. Linking across
these docs is via shared entities (no explicit chain fields → no doc-chain, which
is correct).

**▶ ALL INGESTION-PIPELINE FIXES (1–10) DONE + verified.** Remaining is NOT
ingestion code:
- **Query-coupled FIX 4** — query-time field-name→canonical mapping in the
  planner/F-mode is **DONE** (`a42e326`/`1798ee6`/`4ebfbe7`, lit up E2E — see the
  QUERY-PHASE entry at the top). *Still pending:* semantic cross-doc
  column-synonym merge + its destructive fields-key rewrite (ingestion-side).
- **Validation runs** — (a) corpus re-extract over finance (FIX 9) + M1 re-run
  ("loans with rate>9%" from the structured layer; no regression on 32/50 +
  adversarial 4/4); (b) real-binary E2E (vertex-msa.pdf / scanned / xlsx) —
  never proven E2E on real binaries.
- **Flagged pre-existing test-rot** (spawned tasks): test_files_crud exact-keys;
  test_kv_tables_worker FakeExtractor/atomic_units; procrastinate-defer
  test-harness gap (defers don't land in local test DB).

**▶ DATA-QUALITY AUDIT (actual DB rows, not coverage) — finance ws.** Reviewed
every layer with samples. **Good:** chunking (0 garbage; bank statements
row-chunked ~59/doc; hierarchical elsewhere), **tabular field extraction**
(transaction rows = `{date,debit,credit,balance,description}`), RAPTOR (194
nodes), chains (2/5 members, order correct), lineage paths (100%). **WEAK — real
gaps (newly surfaced; prior audit only checked artifact *coverage*, not
*quality*):**
- **Narrative field extraction THIN — 0/13** loan/10-K/treasury docs captured a
  key numeric term (interest_rate/principal/revenue/EBITDA) as a STRUCTURED
  field; loan doc_root holds only frontmatter (doc_id/parties/chain_role). The
  9.40% rate lives only in text → the §2.2 "structured data conforming to
  schema" promise is half-delivered (strong for tables, weak for prose). A
  structured filter like "loans with rate>9%" can't be served today.
- **Emergent schema reflects it:** `auto:loan_agreement` = frontmatter fields +
  an empty `Financialaction` sub-entity.
- **unit_type fragmentation:** `transactionlisting`(52) vs `transaction_listing`
  (18) + overlapping `*_by_category` buckets → query-splitting risk.
- **Identity weak:** under-merge (`HDFC BANK`135 ≠ `HDFC`80; `Apollo Hospitals` ≠
  `Apollo Hospitals Pune`); **noise entities** (doc-IDs, ref-numbers, email
  domains `hdfcbank.com`, rate benchmark `HDFC MCLR` all as ORG/PRODUCT);
  **only 47%** of 5877 mentions resolve to a canonical entity.
- **Triples mixed:** some real (`X has CIF Y`), some noise (`columns for balance`,
  field-name subjects).
- **Lineage:** 18% of sub-entities (90/491) lack a parent FK (path set, FK null).
**Implication:** retrieval/answers work (text layer clean); the STRUCTURED
knowledge layer needs a quality pass — narrative-doc field extraction (read the
body, not the frontmatter), identity merge + noise filter, mention resolution,
unit_type canonicalization. These were partially flagged (identity/field
under-merge as "eval-coupled") but the **narrative-extraction gap is the
headline** and was understated. New work items to add to the master table.

**▶ PDF-CONSISTENCY AUDIT + FULL STACK LIVE (for hands-on testing).**
Re-read the assignment PDF (§1–§5) and audited every requirement against the
repo. **Verdict: the checklist is faithful — the functional reqs §2.1–§2.4 are
BUILT; the long pending list is RAG-quality + scale polish, not missing
capability.** Confirmed in code: schema define/version/evolve (P1b wizard + P6
versions), scanned+digital PDF + xlsx parsers (Docling/GeminiOCR/xlsx routed by
text-layer sniff), scoped "X within Y" (S-mode) + filtered by date/metadata/id
(D/F/C-modes), citations file→page-range→excerpt + confidence, 1-cmd bootstrap,
seed data (8 PDF/3 xlsx/eml), loadable demo schema (P3), eval set (52 Q +
scorer). **Stack brought up + verified live:** API :8000 · native worker · UI
:3000 · DB :5432 · MinIO :9000. Live chat on ws `f0000000`: "current HDFC rate?"
→ "9.40% per annum" + 7 citations + confidence=medium; UI chat history + schema
studio (9 emergent `auto:*` schemas + New-schema wizard + Versions) all render.
**TRUE remaining gaps vs the PDF (distinct from quality polish):** (1) §2.2
schema-change→re-extract-without-reparse (P4+F1 #15, FUNCTIONAL); (2) §2.5
**prompts** configurable without code (thresholds done via P1, but generator/CRAG
prompts are still hardcoded constants); (3) §3 domain-agnostic confirming sweep
(Q3 fixed the generator prompt; verify no OTHER hardcoded domain values); (4)
E2E *proof* on the real mixed-format binaries (unit-tested, not demonstrated
end-to-end); (5) write-up consolidation to ≤4 pages. Everything else pending =
quality (Q5/Q1/Q2) + scale (S-series) + agentic/eval (A1/E1).

**▶ Q-BATCH MEASURED WIN (finance, snapshot `docs/eval_baselines/finance_after_qbatch.*`).**
Cumulative effect of this session's query fixes — CRAG best-snippet relevance
(`7bd2073`), IRCoT env-var fix so it actually reformulates (`7510cd2`),
auto_merge config (`8bda0fa`), Q3 neutral generator prompt (`1527db4`) — vs the
phase0 baseline: **ok 29→32, lost_generation 8→5, cite 0.78→0.86**; adversarial
refusal held 4/4; **3 questions fixed (q028 rare-clause, q035 aggregation, q039
long-form), 0 regressed.** The remaining 5 generation losses are the
citation-attribution cases needing **Q5** span/claim-verification (prefer the
authoritative source). Net session query progress is real and regression-free.

**▶ CRAG OVER-REFUSAL FIX + Q5 CITATION DIAGNOSIS (`7bd2073`).** Diagnosed the 8
finance generation losses live: **5 were over-refusals** (gold retrieved, CRAG
force-refused) — root cause CRAG **averaged** relevance over top-3, so one
correct-but-singular snippet in a long doc was diluted <0.5. Fixed → CRAG now
scores **best-snippet (max) relevance over top-5**. Measured (n=50, single run):
over-refusals **12→8**, adversarial refusal held 4/4; scored flat 37→37 because
the now-answered questions hit the **next** bug. Snapshot
`docs/eval_baselines/finance_after_crag.*`. **Q5 roadmap (the remaining loss):**
citation **attribution** — e.g. q012 answers "9.40%" correctly with gold chunks
IN context, but the generator cites a *sibling* hit that also states 9.40%
(non-gold file) instead of the canonical source (the addendum that SETS the
rate). The LLM cites `[hit_id]` markers → enriched to file_ids; when multiple
hits support a claim it doesn't prefer the authoritative source. This is genuine
**Q5 span/claim-verification** (prefer authoritative source for each claim),
partly eval-gold strictness — NOT a cheap bug. (q009-class refusals are
stochastic CRAG borderline on sparse mentions; lower priority.)

**▶ FINANCE M1 BASELINE (first ever) — snapshot `docs/eval_baselines/finance_phase0.*`.**
50 verified Qs, in-process orchestrator, Cohere rerank. **Headline: retrieval is
NOT the bottleneck on finance either — the loss is generation+citation, same as
construction.** OVERALL scor=37/50 · r@10=0.95 r@30=0.99 rerank_ret=1.00 (→ 0
retrieval losses, 0 rerank losses) · mrr=0.78 · cite=0.78 · faith=0.57 ·
refuse=1.00. Localisation: ok=29, **lost_generation=8**, refused_correct=4,
unscorable=9. **All 8 generation losses had good retrieval (r@30=1.00 except one
0.5) but cite=0** — the answer didn't ground/cite correctly. Faithfulness on
those 8 was mostly `skipped` (the default IdentityFaithfulnessGate is a no-op →
it can't catch wrong-but-confident answers; ties to the b3 factory-default and
to Q5). **Refusal works on finance** (adversarial 4/4 refuse✓, refuse=1.00 —
unlike construction's negative-refusal gap; C2's relevance gate is paying off).
Weakest strata: **aggregation** (numeric Q-mode: r@10=0.75, cite=0.50) and
**long-form/negative** (cite 0.50/0.00). **Architect read → prioritise:** (1) Q5
citation/span-verification + the generator (the cite=0 generation losses, the
"cited or it didn't happen" NFR); (2) consider defaulting the faithfulness gate
off Identity so wrong answers get caught; (3) aggregation Q-mode quality. R1
(retrieval) stays LOW priority — finance r@30=0.99.

**▶ P1 QUERY-SIDE (core) DONE — `f51a87f`/`9ddf557`.** The query pipeline now
reads its two highest-value thresholds through layered config instead of
hardcoded constants: the **CRAG refuse gate** (`retrieval.crag.threshold`, the
M1-identified loss area) and the **conflict authority-dominance gap**
(`conflicts.authority_dominance_gap`, Design 2). New `kb.query.config_thresholds.
resolve_query_threshold` (mirrors the write-path helper) resolves both once per
`chat()` request from conn+workspace_id; CRAG value flows to all 6 in-request
decision sites (emit/IRCoT/force_refuse/grounding gate), the gap threads through
`resolve_conflicts_for_hits`→`resolve_all`. SAFE: hardcoded default passed +
returned on any error; `conn=None` still reads defaults.yaml. **End-to-end loop
closed:** `POST /settings/overrides`→`insert_override`→`config_overrides`→
`resolve_config`→orchestrator — so an override set via the Settings API now
genuinely changes the refuse/conflict behavior (P1 done-when, for these
thresholds). 5 new tests; CRAG + b2 conflict + write-path P1 all green.
**Optional remaining for P1:** auto_merge (0.5) + faithfulness (0.80/0.50) —
need new defaults.yaml keys; lower value than the refuse gate.

This also **unblocks P1b's "+config"** thread (config is now read at runtime).

**▶ P1b/P3/P6 PHASE COMPLETE.** UI discovery + user-schema ability delivered:
schema import endpoint+loader+demo artifact (P3 ✅), define-from-scratch wizard +
one-step import (P1b ◑ — "+config" bundle coupled to P1), failure-reason +
version-history FE surfaces (P6 ✅). **Suggested NEXT (sr-architect):** the
cheapest high-value move is now **P1 query-side config wiring** (unblocks P1b's
"+config" and the domain-swap promise) AND/OR **reranker + M1 on finance** —
finance has never been eval'd (`finance/queries.yaml` 52 Qs ready, zero
baseline); native API is already up with a correct reranker env this session.

---

**▶ (prior) pre-ingest WRITE-PATH BATCH (then ingest `finance` once).**
Plan: `~/.claude/plans/lets-create-a-plan-peppy-mitten.md`. Batch ALL write-path
changes first, then run the finance ingest ONCE (finance = most tabular domain;
868 md table-rows). HEAD `0712736`, tree clean, 140+ batch tests pass.

Ingestion-batch progress (do tasks ONE-AT-A-TIME — see
`memory/editing-cadence-tooling`; never batch Edit+Bash, never `git stash pop`):
- ✅ **S1** batching (contextualize/mentions/triples via `kb/llm_batching.run_batched`)
- ✅ **I7** transient retry (`is_transient`/`with_retry`; run_batched + parse retry)
- ✅ **I1** classify-before-chunk — **NOW ACTUALLY WIRED** (`1d4b803`). The
  classifier existed + was unit-tested but `chunk_file_impl` never called it
  (read NULL `inferred_doc_type` → MIME/hierarchical fallback → markdown
  bank_statements chunked across transaction rows). A live finance smoke caught
  it. Fixed: classify before `select_chunker` + persist doc_type. Verified live
  (statement-003: 42 row-leaves vs 9 hierarchical windows). +2 regression tests
  (`0712736`).
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
  **Live smoke DONE (3 finance bank_statements into ws `f0000000`):** full
  pipeline reaches `ready` (~85s/doc); classify→`bank_statement`; kv_tables +
  schema_entities + triples + chains + graph all fire; finalize_corpus fires.
  **Smoke caught the I1 not-wired bug (now fixed `1d4b803`).** Decisions this
  session: finance gets its **own** workspace `f0000000` (co-locating with
  construction would make #19's whole-workspace finalize re-extract all 46
  construction docs every settle — cost); view via `ui/.env.local` FE pointer.
  **Env state:** stale docker worker `knowledgebaseservice-worker-1` STOPPED;
  native worker (this branch) is the active consumer of the `localhost:5432`
  queue. **NEXT:** statement-001/002 were ingested BEFORE the I1 fix (bad
  hierarchical chunks) — wipe ws `f0000000` (`DELETE FROM files WHERE
  workspace_id='f0000000-…001'` cascades) and re-run the **staged** ingest
  (statements → loan/complaint chains → widen) on the fixed worker → M1 finance
  baseline. Restart docker worker (`docker start knowledgebaseservice-worker-1`)
  only if reverting to the docker stack.

> Pre-existing (NOT my regression): `tests/test_b4b_api.py` 2 failures
> (StubPlanner `.plan()` missing `conn` kwarg) — fail identically at `ff0ceea`.
> Also `tests/test_b3_unit.py` 4 failures (faithfulness factory default/auto
> → Identity; 2 PDF span-ref) — `faithfulness.py`/`citations.py`/the test are
> byte-identical to session-start `f24431a` and import none of the P1
> query-side files, so they predate this session's query work.

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
| 2 | **I1** classify-before-chunk + clause/row chunker | 1 | ✅ | **Wiring fixed `1d4b803`** — classifier was built+tested but `chunk_file_impl` never called it (smoke caught it); now classifies→persists doc_type before `select_chunker`. Verified live (42 row-leaves). 6 + 2 regression tests. Clause chunking still hier-backed (markdown-limited). D2 |
| 3 | **I2** field convergence (EDC) | 1 | ✅ | converger `converge_clusters_semantic` + `field_judge` wired as corpus pass `converge_workspace_fields_impl` (#19, `79fee41`). 5+3 tests. D3 |
| 4 | **I4** identity resolution (top-k) | 1 | ✅ | top-k `select_entity_match` (per-doc) + post-ingest `reconcile_workspace_entities_impl` sweep on shared `merge_entity_group` (#19, `e4afbac`). 6+3 tests. D4 |
| 5 | **Q1** conflict across independent docs | 2 | ⏳ | depends I2+I4. D8 |
| 6 | **Q5** faithfulness (claim-decomp + span verify) | 2 | ◑ | **C2** relevance-gate/override (`6ae2571`). **D6 claim-decomp + entailment DONE** — `LLMFaithfulnessGate` on a lite LLM (`e49839d`/`3be41f4`), replaces the heuristic-Jaccard gate that was over-refusing (correct 9.4% scored 0.15→cliff; LLM→0.5 ships, catches fabricated 5.0%). Activate `KB_FAITHFULNESS_GATE=llm`. **Remaining:** full-eval over-refusal number + flip default; **citation-attribution** (q012 sibling-source); **C2b** q009. |
| 7 | **P2** answer confidence signal + reason | 2 | ✅ | `derive_answer_confidence` (high/med/low + reason from faithfulness+CRAG); auto-set on every ChatResult via validator → exposed on `/chat`; FE confidence badge in `AnswerCard.tsx`. 5 tests. (FE wired, not browser-verified.) D6 |
| 8 | **Citation honesty** (kill fake-cite fallback) + **P5** page-range | 2 | ✅ | **C1** grounded aggregate citations (0.00→1.00; `0079118`); **fake-citation fallback killed** (`45d7da3`); **P5** page-range (citation reports `pp. X–Y`; mechanism-tested — not construction-visible, markdown corpus). D6 |
| 9 | **Q3** strip corpus-specific facts from generator prompt | 2 | ✅ | `1527db4`. Replaced construction-corpus few-shot facts in the generator system prompt (Acme Whitefield / Survey No. 184/2A; Deshpande Architects; Grid C→D; headcount 201/140; 8 Mar 2025) with domain-neutral schematic placeholders — NFR §3 (no domain values hardcoded). D5/NFR |
| 10 | **Q2** collapse 13-mode facade → ~4 honest modes | 2 | ⏳ | D5 |
| 11 | **P1** wire pipeline to read layered config | 3 | ◑ | **Extraction-side DONE** (`91824f3`/`03dcdb1`/`40becc9`). **Query-side CRAG + authority-gap + auto_merge DONE** (`f51a87f`/`9ddf557`/`8bda0fa`): `resolve_query_threshold` resolves `retrieval.crag.threshold` + `conflicts.authority_dominance_gap` + `retrieval.auto_merge.threshold` per chat() request; end-to-end closed (`POST /settings/overrides`→`config_overrides`→`resolve_config`→orchestrator). 6 tests. **Remaining (optional):** faithfulness pass/refuse thresholds — coupled to the gate-default question (gate is Identity no-op, so its thresholds don't even fire; the real lever is the gate default, not its thresholds). D7 |
| 12 | **P3** committed, loadable demo-schema artifact | 3 | ✅ | D7 |
| 13 | **P1b** define-from-scratch schema + onboarding | 3 | ◑ | `2f24a1a`. Schema-studio "New schema" 4-step wizard (entities+fields+relationships) + "Import YAML" loader, both via atomic POST /schemas/import.yaml; verified live (both create schemas end-to-end). **Remaining:** "+config" one-click bundle — coupled to P1 config wiring (row 11). D7 |
| 14 | **P6** FE: failure reasons + schema version view | 3 | ✅ | `2bcfb0a`. (a) FilesTable expanded detail surfaces failed-file `error_class`/`message`/traceback from the failed lifecycle event (pure `failureReasonFrom`, 4 tests; no live failed files in finance). (b) "Versions" modal in Schema Studio: list schemas → drill into immutable version history (verified live, auto:* show v1/post). |
| 15 | **P4 + F1** schema-change re-extraction loop | 3 | ⏳ | |
| 16 | **I5** contextualization cost cap/cache | 4 | ⏳ | |
| 17 | **I7** transient-failure retry + visibility | 4 | ◑ | retry done: is_transient+with_retry in llm_batching; run_batched + parse_file_impl retry transient 429/timeout/5xx. 5 tests. Part 2 (degraded-rate visibility event) optional, not blocking. |
| 18 | **OCR per-page escalation** | 4 | ⏳ | D1 |
| 19 | **Q4** per-channel DB connections | 4 | ⏳ | |
| 20 | **Q6** IRCoT: fix env-var or delete | 4 | ✅ | `7510cd2`. 'Fix and keep': `make_default_reformulator` read GEMINI_API_KEY/GOOGLE_API_KEY (both unset) → fell to Identity no-op; now reads canonical `KB_GEMINI_API_KEY` (matches crag/rewriter), verified → GeminiReformulator. |
| 21 | **Q7** per-turn cost cap | 4 | ⏳ | |
| 22 | **Cheap bugs** (`/tmp` dump, etc.) | 4 | ✅ | `7510cd2`. Removed the hard-coded `/tmp/kb-parse-errors.log` per-request dump in `generate.py` (logger.warning already covers it). Q6 env-var + citation-fallback (#8) were the other listed cheap bugs; OCR-escalation tracked separately as #18. |
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
| 34 | **DQ1** narrative fields stranded before the queryable layer | 1 | ⏳ | **NEW — ROOT-CAUSED (the extractor WORKS; data is wrong).** Live re-run of `kv_tables` on loan-addendum-1 yields 19 rich scalars (`new_all_in_rate=9.4`, `post_amendment_interest_rate=9.4`, principal, EMI, MCLR). But the DB has frontmatter-only at doc_root because of **3 compounding faults: (a)** the frontmatter guard (`model_id='frontmatter:auto'`) backfills 9 clean fields even when body extraction returns 0 → **masks failures**, doc reaches `ready` looking healthy (this is why the build missed it; 256/922 fields are frontmatter:auto); **(b)** rich fields that ARE extracted (`interest_rate_all_in` in loan-002/003/004) sit in `proposed_fields` but never reach `extracted_entities` — promotion is **prevalence-gated (~0.80)** and the LLM names the rate differently per doc, so no name clears the bar; I2 synonym-convergence isn't merging them; **(c)** the 3 Acme/HDFC loan-chain docs got `gem_pf=0` — a transient empty extraction during the #19 staged re-extract. **Fix:** (1) add a degradation guard (0 body scalars on a text-rich doc = flag, don't silently succeed); (2) surface per-doc proposed_fields into the doc's own `extracted_entities` (a doc's record shouldn't be gated on cross-doc prevalence); (3) strengthen field-name convergence so key terms promote; (4) re-extract the frontmatter-only narrative docs. Highest-value structured-layer gap. |
| 35 | **DQ2** identity merge + noise filter | 5 | ⏳ | **NEW.** Under-merge (`HDFC BANK`≠`HDFC`; `Apollo Hospitals`≠`…Pune`); noise entities (doc-IDs/ref-numbers/email-domains/rate-benchmarks as ORG/PRODUCT). Tune merge thresholds + add an entity-type/noise gate at mention extraction. Overlaps I4/S2. |
| 36 | **DQ3** mention resolution coverage | 5 | ⏳ | **NEW.** Only 47% of 5877 mentions resolve to a canonical entity → half the entity graph is disconnected; weakens scoped/entity queries. |
| 37 | **DQ4** unit_type canonicalization | 1 | ⏳ | **NEW.** `transactionlisting` vs `transaction_listing` + overlapping `*_by_category` buckets fragment the same concept → query splitting. Extend `singularize_unit_type`/normalization to merge case+underscore variants. |

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
