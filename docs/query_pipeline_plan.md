# New Query Pipeline — detailed plan v2 (T2 + T3 + KG-as-route)

**Status:** **Phase 1 (T2) + Phase 2 (T3) SHIPPED** — T2 2026-06-03, T3
2026-06-04, branch `feat/roadmap-t1-t2-t3` (commits `feat(query): T2 structured-
first query pipeline`, `feat(query): T3 Q-mode generous aggregation (Stage 5b)`).
**Phase 3 (KG) is pending.** See the §10 implementation status for exactly what's
built, verified, and outstanding. · **Owner:** query/retrieval
**v2 note:** this revision folds in two adversarial reviews — a cross-domain
query war-game (12 trace gaps) and a senior-architect "sounds-right-but-wrong"
pass (12 design gaps). Every finding is resolved in the body and tracked in the
§11 ledger. Where v2 changes a v1 rule, it says so.
**Relationship to [`ROADMAP.md`](ROADMAP.md):** this is the *authoritative*
query-side design; it supersedes the brief T2/T3 sketches there.
**Foundation:** T1 (schema-as-a-view: in-place key convergence, no auto
re-extraction, display-pointer rename) is **already shipped**.

Implementable **cold**. Every decision point has a concrete rule + a named,
defaulted, layered-config-overridable constant. Every rule that is an LLM
judgement states the *fallback for a wrong judgement* so a wrong call is safe.

---

## 1. Goal, principles, invariants

**Goal.** Stop treating the structured layer as a post-retrieval filter on a
blind whole-corpus search. Instead: **answer-or-narrow from structured data
first, then RAG only inside the narrowed docs** — *but only as far as we can
trust the structured signal* (see principles). Keep the proven
retrieve→rerank→~10-chunks tail.

**Principle P1 — confidence-weighted narrowing (NOT default-on).** Narrowing
precision tracks extraction quality, which is good on clean tables and poor on
narrative. So the pre-filter is strongest where it's least needed. Therefore a
resolved predicate carries a **confidence**, and:
- **high confidence** → *hard* scope (true pre-filter; can remove docs);
- **low/medium confidence** → *soft* scope (boost in-scope docs at rerank, but
  STILL search unscoped — can broaden, can't hide).
This is the v2 answer to "structured-first inverted utility."

**Principle P2 — every structured answer carries an independent check.** The
trust stack must not be circular (we cannot use extraction to vouch for
extraction). So:
- a structured **lookup** answer must be **confirmable against its source
  chunk** (the value appears in / normalizes to the cited chunk) before it ships;
- a computed **aggregate** (which no chunk contains) must be **auditable**: it
  ships with `N contributing rows + the exact SQL`, plus a sanity check — never
  "trust the SQL."

**Invariant I1 — honest "never worse than today".** v1 claimed scoping is never
worse; that was false (a *wrong-but-populated* scope returns hits and dodged the
count-based fallback). v2 makes it true by construction:
> A narrow may **remove** a doc from search ONLY when predicate confidence is
> high (P1). In all other cases the scope is a *rerank boost over unscoped
> retrieval*, so the right doc is always reachable. Additionally, a hard-scoped
> pass whose **top reranked relevance is weak** auto-widens (relevance trigger,
> §6.10) — the fallback now watches **relevance, not count**.

**Invariant I2 — plain RAG is always reachable.** Every smart path (answer-
direct, narrow, KG, aggregate) degrades to unscoped RAG on failure/ambiguity.

**Non-goals.** Full always-on GraphRAG (KG is an on-demand route, §6.9);
dbt-style named-metric semantic layer (T3 catalog is the right size); external
memory frameworks; re-architecting RAPTOR or the reranker.

---

## 2. Glossary

- **doc / file** — `file_id` (uuid).
- **structured layer** — `proposed_fields` (raw scalars, immutable),
  `extracted_entities.fields` (canonical doc_root scalars + sub_entity/table
  columns, jsonb), `canonical_entities` + `extracted_mentions` +
  `mention_to_entity`, `schema_fields`/`schema_entities`.
- **field** — scalar or table column, keyed by **canonical key** (post-T1).
- **display label** — user rename pointer (T1); display→canonical at query time.
- **entity** — `canonical_entities` row; resolved from a surface form (fuzzy).
- **predicate** — the structured constraint a question implies. v2: a
  first-class **predicate object** (§6.12), not just a doc set.
- **clause** — one part of a predicate (a field test / entity / doc-type /
  unit-type / date-window), each with its own resolved value, matched file_ids,
  **coverage**, and **confidence**.
- **scope** — the file_ids retrieval is restricted/boosted to (or `ALL`).
- **confidence** — how much we trust a resolved clause/predicate (drives P1).
- **coverage** — fraction of docs of a type where a field is *present* (≠
  correctness — see §6.3).
- **grain** — the row level a number lives at: doc_root (one per doc) vs a
  unit_type row (many per doc). Mixing grains double-counts.
- **stated figure** — a number written verbatim in a doc. **computed figure** —
  a number we produced by aggregating rows.
- **schema epoch** — a monotonically-increasing token that bumps on ANY schema
  change incl. T1 display-rename; used to invalidate caches (§6.1).

---

## 3. End-to-end flow

Stages list **In / Action / Decision rules / Out / On-failure**.

### Stage 0 — Request
`query`, `workspace_id`, optional `session_id`, optional `file_ids` (@-doc
picker), optional `requested_mode`. Ensure a session row exists.

### Stage 0.5 — Non-retrieval gate (NEW, fixes "thanks" → full RAG)
**Action:** cheap classifier (regex + the intent label) detects a
**non-retrieval** turn — greeting / thanks / acknowledgement / meta ("what can
you do"). If so → answer conversationally (or no-op) with **no resolver, no
retrieval, no faithfulness gate**. Otherwise continue.
**On-failure:** if unsure, treat as a retrieval query (safe — just does the
normal pipeline).

### Stage 1 — Context resolution + scope state machine
**In:** `query`, `ChatContext` (last K=6 verbatim turns, Tier-2 summary, and the
**carried predicate** — §6.7, v2 stores the predicate, not just file_ids).
**Action:** resolve anaphora → `effective_query`; decide this turn's scope
operation via the **scope state machine** (§6.7): clear / intersect / replace /
relax / inherit / none.
**Out:** `effective_query`, `inherited_predicate` (or none), `scope_decision`.
**On-failure:** → no inherited scope (fresh).

### Stage 2 — Intent + schema-aware planning
**In:** `effective_query`, intent, and the **linked schema** (§6.1 — the top-k
schema elements *retrieved* for this query, NOT the whole schema).
**Action:** planner (one LLM call) emits the `Plan`: `field_filters`,
`seed_entities`, `doc_types`, `unit_types`, **date/relative-date expressions**,
and a *provisional* `answer_mode` ∈ {LOOKUP, AGGREGATE, EXISTENCE, LIST,
NARRATIVE, HYBRID, RELATIONSHIP} (§6.4).
**Out:** `Plan` (+ provisional `answer_mode`).
**On-failure (LLM error):** identity routing + `answer_mode = NARRATIVE`.
**Cost rule:** this is the ONLY planning LLM call; the resolver (Stage 3) is SQL.

### Stage 3 — Resolve the predicate (SQL) → `ResolvedPredicate`
**In:** `Plan`, `inherited_predicate`, `workspace_id`.
**Action:** resolve each clause to file_ids **with per-clause coverage +
confidence**; build the **`ResolvedPredicate`** object (§6.12) carrying:
`clauses[]`, `file_scope` (AND of clauses, with the **AND-salvage** rule §6.2),
`row_filters` (date windows / value thresholds to push into SQL),
`overall_confidence`, `selectivity`. Expansive, fuzzy matching (§6.2). Combine
with `inherited_predicate` per §6.7.
**Out:** `ResolvedPredicate`.
**On-failure / 0-match / over-broad:** §6.10 matrix.

### Stage 4 — Answer-mode decision (REVISABLE — fixes mode-before-data)
**In:** provisional `answer_mode`, `ResolvedPredicate`, gate signals (§6.3).
**Revise the mode using resolver facts, then route:**
- `LOOKUP` resolving to **>1 doc/entity** → **revise to LIST** (never a bare
  single value across many docs — fixes HDFC/salary).
- `AGGREGATE` whose grouping/filter depends on an **entity clause** or a
  **low-confidence/low-coverage** field → **revise to LIST** ("show the docs"),
  with a caveat (fixes count-by-entity undercount + low-cov group-by).
- Route: `AGGREGATE`→5b (iff aggregatable + gate §6.3); `LOOKUP`/`EXISTENCE`→5a
  (iff gate §6.3 passes); `LIST`→5c; `NARRATIVE`→6; `HYBRID`→5+6; `RELATIONSHIP`
  →7.
**On-failure:** unknown mode → NARRATIVE → Stage 6.

### Stage 5 — Structured answer paths
**5a Lookup / existence.** Read field value(s) for the resolved docs. **P2
check:** the returned value must be **confirmable in its source chunk**
(`source_chunk_id`) — if it isn't, do NOT answer-direct; degrade to Stage 6.
Ambiguous field (>1 canonical key) or >1 entity → handled at Stage 4 (LIST) or
disambiguate. **"None/0" rule:** never a bare "No"; require coverage ≥
`COVERAGE_COMPLETE` *and* the weakest clause high-confidence to assert "none",
else hedge.
**5b Aggregate (Q-mode, T3).** §6.11 governs correctness: **row-filter handoff**,
**grain selection + dedup**, **group-by canonicalization**, **active
reconciliation** vs any stated total, and the **P2 audit envelope** (N rows +
SQL + sanity check). Validated, parameterized, read-only, timeout/row-cap, with
a **group-by cardinality cap**; audited (`audit_queries` + CSV).
**5c List (NEW).** Return the resolved **row/doc set** from the structured layer
(paged), not a 10-chunk RAG sample. Each row cited.
**Out:** structured answer + citations (+ source-doc hits kept).
**On-failure:** coverage/P2 fails → Stage 6; aggregate validate/exec error →
self-repair ≤2 (T3) → typed refusal.

### Stage 6 — Retrieval (confidence-weighted scope)
**In:** `ResolvedPredicate`, `effective_query` rewrites.
**Action (P1):**
- `overall_confidence ≥ SCOPE_CONF_HARD` → **hard** scope: channels filtered to
  `file_scope` (§6.8).
- else → **soft** scope: run unscoped, but **boost** in-scope docs at rerank.
Fuse (RRF) → rerank → top `K_FINAL`.
**Relevance widen (fixes I1):** if the *hard*-scoped pass's **top reranked
relevance is weak** (< `RERANK_FLOOR`, or far below a tiny unscoped probe), or
yields `< MIN_SCOPED_HITS`, **supplement** (union) with unscoped + re-rerank.
Widen is by **relevance/union, never replacing the scoped set**.
**Out:** top-`K_FINAL` hits.
**On-failure:** channel/SQL error → unscoped retrieval.

### Stage 7 — Knowledge-graph route (on-demand)
> Numbered 7 but in the RELATIONSHIP path runs **before** Stage 6 — it produces
> the scope Stage 6 searches. It is a route chosen at Stage 4, not a tail step.
**Action:** resolve seeds → PPR over `graph_edges` → top entities. **Constrain
(fixes filtered multi-hop):** restrict PPR to the **context subgraph** (the 2nd
seed / project), and **filter output by requested entity type / edge role** (e.g.
subcontractor), not raw proximity. → docs → `file_scope` for Stage 6.
**On-failure / no seed / empty graph:** degrade to Stage 6 narrow+RAG. KG
answers flagged lower-confidence until the agentic-extraction verify pass (§10).

### Stage 8 — Fusion + rerank
RRF (k=60) → top-30 → cross-encoder rerank → top-`K_FINAL` (10). Unchanged.

### Stage 9 — Generate + verify + reconcile
**Action:** generate. Apply **P2** (lookup→text-confirm; aggregate→audit
envelope). Apply **value reconciliation** (§6.5): stated-vs-computed (active
fetch), stated-vs-stated/amendment, HYBRID cross-leg **entity consistency**, and
**false-premise / locator-existence** gate (§6.13). Disagreements are
**surfaced with provenance**, not silently resolved.
**On-failure:** faithfulness refuse → CRAG/abstain.

### Stage 10 — Persist + carry predicate + surface
Persist the turn; write the **`ResolvedPredicate`** (not just file_ids) to
session carry-forward (§6.7, §7). Surface in the Plan Inspector:
`scoped_doc_count`, `scope_source`, `scope_decision`, `scope_mode`
(hard/soft), `answered_from_structured`, `confidence`, and *"scoped to N docs
from your previous question — say 'across all documents' to reset."*

---

## 4. Diagram

```
query
 └ S0.5 non-retrieval? ─yes→ conversational reply (no retrieval)
 └ S1 context + scope-state-machine ── inherited_predicate
   └ S2 planner (linked schema) ── Plan + provisional answer_mode
     └ S3 resolver (SQL) ── ResolvedPredicate {clauses, file_scope, row_filters, confidence}
       └ S4 revise mode (using resolver facts), route:
          ├ AGGREGATE ─► S5b Q-mode (grain+dedup+row-filters+active-reconcile+audit)
          ├ LOOKUP/EXISTENCE +gate ─► S5a lookup (P2 text-confirm; hedge "none")
          ├ LIST ─► S5c structured row/doc list
          ├ RELATIONSHIP ─► S7 KG (context-constrained, type-filtered) ─► scope ─┐
          ├ NARRATIVE / gate-fail / low-conf ─► S6 retrieval ◄──────────────────┘
          └ HYBRID ─► S5 (definite) + S6 (narrative) ─► entity-consistency check
            └ S6 retrieval: confidence-weighted (hard vs soft scope) + relevance-widen
              └ S8 fuse+rerank ─► S9 generate + P2 verify + reconcile + premise-gate
                └ S10 persist + carry PREDICATE + surface
   (any failure / over-broad / low-confidence ─► unscoped RAG)
```

---

## 5. Constants (defaults; layered-config overridable)

| Constant | Default | Meaning |
|---|---|---|
| `K_FINAL` | 10 | chunks to the LLM (post-rerank) |
| `SCOPE_CONF_HARD` | 0.75 | predicate confidence ≥ → hard pre-filter; below → soft boost (P1) |
| `MIN_SCOPED_HITS` | 5 | below this pre-rerank → supplement unscoped |
| `RERANK_FLOOR` | (calibrate) | scoped top reranked score below → relevance-widen (I1) |
| `SMALL_SCOPE_DOCS` | 200 | ≤ → exact vector over scoped rows; > → HNSW iterative scan |
| `COVERAGE_HIGH` | 0.90 | field present in ≥ this fraction — necessary, not sufficient (§6.3) |
| `COVERAGE_COMPLETE` | 0.98 | required (with high clause-confidence) to assert "none/no" |
| `MIN_DOCS_FOR_COVERAGE` | 3 | min docs of a type before coverage is trusted |
| `ENTITY_TRGM_MIN` | 0.45 | trigram similarity floor for entity fuzzy match |
| `OVERBROAD_FRAC` | 0.80 | predicate matching > this fraction → treat as no narrowing |
| `GROUPBY_CARDINALITY_CAP` | 200 | max distinct group-by keys (sanity + DoS) |
| `RECON_TOLERANCE` | 0.01 | stated-vs-computed relative gap before surfacing a conflict |
| `HNSW_EF_SEARCH_SCOPED` | 100 | ef_search for large-scope vector queries |

---

## 6. Cross-cutting specifications

### 6.1 Schema introspection: linking-as-retrieval + epoch — `src/kb/domain/structured_schema.py` (new)
`live_schema(conn, workspace_id) -> LiveSchema`: doc_types; per-doc_type fields
(`canonical_key, value_type, display_name, coverage, grain`); unit_types +
columns (+ grain); a fuzzy `resolve_entity(name)`; `field_display_map` (T1).
- **Schema-linking, not schema-dumping (fixes "doesn't scale").** At real schema
  size you cannot put every field in the planner prompt. The planner receives the
  **top-k schema elements *retrieved* for this query** (embed the query against
  field/unit names+descriptions; deterministic ranking). Low-coverage fields are
  retrievable too — coverage gates *answering*, not *visibility*.
- **Cache keyed on `schema_epoch` (fixes T1-rename-invisible).** T1 display-rename
  and convergence MUST bump `schema_epoch` so a cached `live_schema` /
  `field_display_map` is invalidated immediately (v1 keyed on `schema_version`,
  which a label-only rename didn't bump).

### 6.2 Resolver — `src/kb/query/structured_prefilter.py` (new)
`resolve(conn, ws, plan, inherited_predicate) -> ResolvedPredicate` (§6.12).
- **Field clause** → map surface form to canonical key via `_resolve_field_name`
  + display map. **Ambiguity (fixes "what's the date"):** if it maps to **>1
  canonical key** across doc-types with comparable scores, do NOT auto-pick — mark
  the clause `ambiguous` (Stage 4 → disambiguate / LIST per doc-type / low
  confidence). Translate to **JSONB SQL** (eq/ne/lt/le/gt/ge/like/in; numerics via
  `value_numeric`), run set-wise; never load-all-then-Python. **Single engine:**
  the SQL translation is the source of truth; the F-mode Python post-filter is a
  *fallback only* and is documented as possibly-divergent on messy values (it is
  never used to "confirm" the SQL pre-filter).
- **Entity clause** → `resolve_entity` (exact → fold → trigram ≥ `ENTITY_TRGM_MIN`
  / prefix). Returns **per-candidate confidence**. **Single-value safety (fixes
  same-name conflation):** if a DEFINITE_LOOKUP entity resolves to **>1 distinct
  canonical entity**, do not silently pick → Stage 4 LIST/disambiguate.
- **Doc-type / unit-type clause** → `inferred_doc_type` / `unit_type = ANY(...)`.
- **Dates (fixes relative + fiscal):** normalize absolute ("March 2026"→`2026-03`),
  **relative** ("last quarter/month/YTD", anchored on request time), and
  **fiscal/calendar quarters** (config fiscal-year start). Produces a `row_filter`
  range (applied at row level, §6.11), not just a doc filter.
- **AND-salvage (fixes cross-clause zero-trap):** `file_scope` = AND of clause
  file-id sets, BUT if the AND is empty while individual clauses are non-empty,
  fall back to the **largest non-empty clause subset** (drop the zero clauses),
  lower `overall_confidence`, and record which clause was dropped. A spurious
  clause must not collapse a good entity scope to whole-corpus.
- **Per-clause coverage + confidence** are attached to each clause (drives §6.3,
  §6.4, P1).

### 6.3 Trust gate (presence AND correctness; per-clause; all answer-direct paths)
Two-part, because **coverage = presence ≠ correctness** (fixes the proxy bug):
1. **Presence:** `coverage(doc_type, key) = docs_with_field / docs_of_type ≥
   COVERAGE_HIGH AND docs_of_type ≥ MIN_DOCS_FOR_COVERAGE`. Source: aggregate
   per-file `files.extraction_coverage` at finalize, cached per
   `(ws, doc_type, key, schema_epoch)`.
2. **Correctness (P2):** the specific value returned must be **confirmable in its
   source chunk** (lookup) or **auditable** (aggregate). No correctness check →
   no answer-direct.
**Per-clause, weakest-link (fixes per-field 0-match bug):** for a multi-clause
predicate, the gate uses the **weakest resolved clause**. **Entity clauses are
always treated as low-coverage** (mention-resolution ~47%): an entity-link miss
therefore HEDGES / goes to RAG — it can never produce a confident "no documents
match" just because the *field* was high-coverage.
**Applies to AGGREGATE too (fixes aggregate-bypasses-gate):** a low-coverage or
entity-dependent aggregate is demoted to LIST + caveat (Stage 4), or annotated
("computed over the 15% of invoices that have a GST number"). The gate is no
longer wired only to LOOKUP/EXISTENCE.

### 6.4 answer_mode (provisional at S2, revised at S4)
- **LOOKUP** — one field value of one doc/entity.
- **AGGREGATE** — sum/count/avg/min/max/group-by.
- **EXISTENCE** — yes/no.
- **LIST** *(new)* — enumerate the rows/docs matching a predicate ("show all X
  over Y", "list all contracts"). Returns the set (S5c), not a chunk sample.
- **NARRATIVE** — explain/why/how/summary.
- **HYBRID** — definite part + narrative part.
- **RELATIONSHIP** — connection/multi-hop.
- **CONVERSATIONAL** *(new)* — non-retrieval turn (handled at S0.5).
**Revisable (fixes mode-before-data):** S4 may change the mode using resolver
facts (LOOKUP→LIST on >1 match; AGGREGATE→LIST on entity/low-conf). Wrong label
is safe: default degrade is NARRATIVE → S6.

### 6.5 Value reconciliation (generalized)
Trust order **stated > computed > inferred**, but stated is an *estimate, not
gospel* (fixes "stated always wins"):
1. **Stated vs computed.** Reconciliation is **active (fixes passive bug):** when
   an AGGREGATE targets a field that ALSO exists as a stated total on the scoped
   doc(s), S5b **fetches the stated figure** and compares. Prefer stated **only**
   if the **same-quantity guard** passes (metric+entity+period+**scope**: a
   stated "total" that excludes tax is NOT the same quantity as a tax-inclusive
   computed total — flag, don't assume).
2. **Stated vs stated / amendment (fixes Net-30-vs-Net-45).** When a field has >1
   distinct value across related docs, prefer **amends/supersedes lineage → 
   doc_status → effective_date/version → source_authority**. Works for
   **non-numeric** fields too (governing law, etc.). Needs an amendment→parent
   edge (doc_type=amendment referencing the MSA); derive if absent.
3. **HYBRID entity consistency (fixes Acme-Corp-vs-Acme-Industries).** Assert the
   aggregate-leg entity == the narrative-leg's cited-chunk entity; if not, surface
   "which Acme?" instead of stitching two subjects into one answer.
4. **On material disagreement (> `RECON_TOLERANCE`):** do **not** silently pick —
   **surface both with provenance** + route to the conflict-detector.

### 6.6 Grounding / verification (P2)
- **Lookup / narrative → text grounding:** faithfulness gate vs cited chunk(s).
- **Computed aggregate → audit envelope (fixes "exemption removes the last
  guard"):** the number is exempt from chunk-text faithfulness (it's in no
  chunk), BUT it is NOT unguarded. It MUST ship with: (a) `N contributing rows`
  cited, (b) the exact audited SQL (`audit_queries`), (c) a **sanity check**
  (non-absurd magnitude; row count > 0; grain consistent — §6.11), and (d) it is
  surfaced as "computed from N rows" so it is user-auditable. Self-repair catches
  *erroring* SQL; the sanity check + surfaced provenance catch *plausible-wrong*
  SQL.

### 6.7 Scope state machine — carry the PREDICATE (fixes relax + file_id-only)
State persisted as the **`ResolvedPredicate`** (clauses + file_scope), NOT just
file_ids (v1 stored file_ids → could never relax a filter). Per turn, first
match wins:
1. **CLEAR** → scope = ALL. Trigger: **schema-driven reset** — match against
   `all|every|each` + the live `doc_types` vocabulary (and plurals/synonyms) AND a
   generic list (`docs|documents|files|everything|the whole corpus`). "across all
   **contracts**" now resets (v1 regex missed domain nouns). Aggregates also
   default to ALL unless explicitly scoped (an aggregate is a fresh global
   question by nature).
2. **RELAX** *(new)* → re-resolve the SAME dimension from the **base predicate**.
   Trigger: a follow-up that **loosens an existing clause** ("make it 8% not 9%",
   "drop the date filter"). Because we kept the predicate, we widen the bound on
   the original clause — set-intersection cannot do this.
3. **INTERSECT** → `prior_scope ∩ resolve(new clause)`. Trigger: refinement
   (`refinement_of_prior` OR regex `of those|of these|which of (them|those)|from
   the (prior|previous|above)|just the|only the|narrow`) that **adds** (not
   loosens) a clause.
4. **REPLACE** → resolve the new predicate fresh. Trigger: new predicate, not a
   refinement (topic change).
5. **INHERIT** → reuse prior predicate. Trigger: anaphoric / no new clause.
6. **NONE** → fresh.
**Ambiguity default:** uncertain → do NOT silently hard-filter; prefer
REPLACE/NONE with a *soft* scope (P1) and **surface** it.
**Empty-after-intersect:** answer the new clause across ALL + surface "nothing in
your previous focus matched; showing across all documents."

### 6.8 Channel scoping + pgvector — `src/kb/query/channels.py`
Add `file_scope: set[str] | None` to all 6 channels (applied only when P1 says
*hard* scope).
- chunk / mention / sub_entity channels: `AND <file_id col> = ANY(%(scope)s::uuid[])`.
- **RAPTOR channels:** corpus nodes are `file_id IS NULL`. Under a doc-scope,
  **EXCLUDE** corpus nodes (`AND rn.file_id = ANY(scope)`) — a whole-corpus
  summary isn't in a narrowed doc set. Corpus nodes serve unscoped / G-mode only.
- **pgvector recall:** small scope (≤ `SMALL_SCOPE_DOCS`, few rows) → exact scan
  over filtered rows (`SET LOCAL enable_indexscan = off`), 100% ANN-recall —
  *note: that is recall of the embedding-nearest, BM25+rerank still carry
  lexical-mismatch cases; in a tiny scope prefer feeding the reranker all chunks
  of the scoped docs.* Large scope → `SET LOCAL hnsw.iterative_scan='relaxed_order';
  SET LOCAL hnsw.ef_search=HNSW_EF_SEARCH_SCOPED;` + over-fetch then trim. Test
  small-scope recall.

### 6.9 KG route — `ppr.py` + `graph_edges` (context-constrained)
Chosen at S4 for RELATIONSHIP. Resolve seeds → PPR. **Constrain (fixes filtered
multi-hop):** restrict the walk to the **context subgraph** (2nd seed / project)
and **filter output entities by requested type / edge role** (e.g. subcontractor)
— not raw proximity. → docs → S6 scope. Falls back to S6 narrow+RAG if no seed
resolves. Lower-confidence flagged until §10 verify pass.

### 6.10 Degrade / fallback matrix
| Situation | Behavior |
|---|---|
| Predicate **low confidence** (P1) | **soft** scope (boost, not filter) — never removes the answer |
| Hard-scoped pass: **weak top relevance** (< `RERANK_FLOOR`) | **relevance-widen** (union unscoped, re-rerank) ← fixes I1 |
| Scoped retrieval `< MIN_SCOPED_HITS` | supplement unscoped (union) |
| 0 docs, weakest clause **high-coverage + high-confidence** | clean *"no documents match `<predicate>`"* |
| 0 docs, weakest clause low-coverage **or entity-based** | unscoped RAG; never claim "none" |
| Predicate over-broad (selectivity > `OVERBROAD_FRAC`) | no narrowing → unscoped + post-filter |
| AND empty but clauses non-empty | largest non-empty subset (§6.2), lower confidence |
| answer-direct P2 check fails | degrade to S6 |
| AGGREGATE entity-dependent / low-coverage | revise to LIST + caveat (§6.4) |
| Aggregate validate/exec error | self-repair ≤2 → typed refusal |
| Aggregate sanity check fails (§6.6) | surface + do not assert the number |
| KG no seed / empty graph | narrow+RAG |
| INTERSECT → 0 docs | answer new clause across ALL + surface note |
| field surface form ambiguous (>1 key) | disambiguate / LIST per doc-type |
| any resolver/LLM/channel error | unscoped RAG |

### 6.11 Aggregate correctness (Q-mode / T3)
- **Row-filter handoff (fixes "MAX over all time"):** the `ResolvedPredicate.row_filters`
  (date windows, value thresholds) are compiled into the aggregate **SQL WHERE at
  row level** — not just used to pick docs. (A statement doc straddles a date
  window; the filter must apply per transaction row.)
- **Grain selection + dedup (fixes double-count):** the catalog tags each
  aggregatable field with its **grain** (doc_root vs unit_type row). The planner
  picks one grain; the compiler **dedups** on a natural key (loan/account id, or
  supersedes-lineage) so a loan in original+amendment isn't counted twice.
- **Group-by canonicalization (fixes HDFC/HDFC-Bank/HDFC-Ltd):** GROUP BY keys
  that are entities/strings are mapped through the canonical entity/value
  normalizer before grouping; cap distinct keys at `GROUPBY_CARDINALITY_CAP`.
- **Active reconciliation:** §6.5(1). **Audit envelope:** §6.6.
- **Trust boundary note (fixes "still a whitelist = safe"):** the catalog is now
  schema-derived from *document-extracted* content; safety rests on
  parameterization + read-only role + timeout + row cap + the cardinality cap, and
  on extraction not minting pathological catalog entries — it is a *wider* surface
  than a hand-fixed whitelist, mitigated by those limits, not by the whitelist
  alone.

### 6.12 The `ResolvedPredicate` object (the seam — fixes resolver↔Q-mode info loss)
One object flows Plan → resolver → {Q-mode, scope machine, fallback}:
```
ResolvedPredicate {
  clauses: [ {kind: field|entity|doctype|unit|date,
              canonical_key?, op?, value?, file_ids,
              coverage, confidence, ambiguous?} ],
  file_scope: set[file_id] | ALL,        # AND (with salvage)
  row_filters: [ {column, op, value/range, grain} ],  # pushed into SQL
  overall_confidence: float,             # min/weighted over clauses (P1)
  selectivity: float,
  dropped_clauses: [...],                # from AND-salvage
}
```
This is what gets **persisted** for carry-forward (§6.7) — so RELAX works — and
what Q-mode reads for **row-level WHERE** (§6.11) — so date windows / thresholds
apply correctly. v1's "only `file_ids` cross the seam" is the bug this closes.

### 6.13 False-premise / locator-existence gate (fixes "Clause 99")
When a query asserts a **structural locator** (clause/section/exhibit/article +
number/name), verify it **exists** in the scoped docs (extracted structure or a
locator-anchored retrieval) **before** answering. If absent → refuse/redirect:
*"Clause 99 was not found in <doc>; the penalty is in Clause 12 — did you mean
that?"* The faithfulness gate checks value↔chunk, not premise↔document, so this
is a separate, required check.

---

## 7. Data / schema & module changes

**Migrations**
- `0054_chat_session_predicate.sql` — store the carried **predicate**:
  `carry_forward_predicate jsonb NOT NULL DEFAULT '{}'::jsonb` (the
  `ResolvedPredicate`; supersedes the v1 `file_scope uuid[]` idea — file_ids are
  *inside* it). Keep a denormalized `carry_forward_file_scope uuid[]` only if
  convenient for surfacing.
- **Schema epoch:** bump a `schema_epoch` (per workspace, or on the auto-schema
  row) on T1 convergence **and** display-rename (§6.1). Small column/trigger.
- (T3) no new tables; catalog from live schema; reuse `audit_queries`. Add
  `grain` to the field catalog (derived, not necessarily stored).

**New modules**
- `src/kb/domain/structured_schema.py` — live schema + linking + epoch (§6.1).
- `src/kb/query/structured_prefilter.py` — resolver → `ResolvedPredicate`,
  answer-mode revision, scope state machine (§6.2/6.4/6.7/6.12).

**Changed**
- `channels.py` — `file_scope` (§6.8).
- `orchestrator.py` — S0.5 non-retrieval gate; thread `ResolvedPredicate`;
  confidence-weighted scope; relevance-widen; degrade matrix; surface; carry
  predicate.
- `planner.py` — linked-schema input; provisional `answer_mode` incl. LIST /
  CONVERSATIONAL; relative-date expressions.
- `mode_router.py` — reuse resolver; S5c LIST; KG constraints; F-mode post-filter
  stays as fallback only.
- `context_resolver.py` + `chat_memory.py` — read/write `carry_forward_predicate`;
  expose `refinement_of_prior` + relax detection.
- `q_payload_gen.py` + `q_planner/*` (T3) — dynamic catalog w/ grain + value_type
  casts; row-filter compile from `ResolvedPredicate`; group-by canonicalization;
  active reconciliation; cardinality cap; self-repair; audit envelope.
- `faithfulness.py` — aggregate audit-envelope path (§6.6); premise gate hook
  (§6.13).

---

## 8. Acceptance criteria (testable)

1. **Broaden:** a high-confidence field/entity scope surfaces the right chunk
   that wouldn't rank globally.
2. **Honest never-worse (I1):** a *deliberately wrong* high-selectivity predicate
   does NOT lose the answer — relevance-widen recovers it; a low-confidence
   predicate uses soft scope and the unscoped answer still appears.
3. **Coverage≠correctness:** a high-coverage-but-mis-extracted field does NOT get
   answered-direct (P2 source-confirm fails → RAG).
4. **Aggregate guarded:** a wrong-grain / wrong-filter aggregate is caught by the
   sanity check or surfaced with N-rows+SQL, not shipped as a bare number.
5. **Relax follow-up:** "over 9%" then "make it 8%" returns the 8–9% docs too
   (predicate carried, RELAX path).
6. **Reset by domain noun:** "across all contracts …" clears the scope.
7. **Reconciliation active:** "total contract value" with a stated $5.2M and
   computed $5.0M surfaces/prefers the stated figure (same-quantity), not a silent
   computed $5.0M.
8. **Amendment:** "payment term" returns the amendment's value (Net 45), noting
   supersession.
9. **No bare "No":** entity-existence over weak entity-resolution hedges.
10. **0-match per-clause:** an entity-link miss (field high-coverage) hedges /
    RAGs — never "no documents match".
11. **Modes:** "show all change orders over $50k" → LIST (full set), not 10
    chunks; "thanks" → conversational, no retrieval; "what's the date?" (ambiguous)
    → disambiguate, not arbitrary.
12. **Row-level filter:** "highest transaction last quarter" applies the date at
    row level (not all-time), with relative-date resolved.
13. **Never worse, full suite:** with the head disabled/failing, behavior = today;
    `uv run pytest tests/` green (modulo 38 pre-existing); eval unchanged-or-better.

---

## 9. Test plan
- **Unit:** resolver → `ResolvedPredicate` (per-clause coverage/confidence, fuzzy
  entity w/ trigram floor, ambiguous-field, AND-salvage, relative/fiscal dates,
  row_filters); scope machine (clear/relax/intersect/replace/inherit/none +
  schema-driven reset + empty-after-intersect); trust gate (presence×correctness,
  weakest-clause, entity-always-low); answer-mode revision; reconciliation
  (active stated-vs-computed, stated-vs-stated/amendment, hybrid-entity,
  surface-both); aggregate correctness (grain/dedup, group-by canon, row-filter
  WHERE, sanity); non-retrieval gate; premise/locator gate; channel `file_scope`
  + RAPTOR-NULL; relevance-widen.
- **Integration (testcontainers):** broaden; honest-never-worse (wrong predicate
  recovered); relax follow-up; reset-by-domain-noun; active reconciliation;
  amendment supersession; LIST; HYBRID entity-consistency; KG constrained + fallback.
- **Regression:** full `uv run pytest tests/`; `scripts/run_submission_eval.py`.

---

## 10. Phasing & out-of-scope
- **Phase 1 — T2** (Stages 0.5–10 minus 5b internals). ✅ **SHIPPED** (2026-06-03).
- **Phase 2 — T3** (5b internals: schema-derived catalog + grain + value-type
  casts + row-filter compile + group-by canon + active reconcile + self-repair +
  audit envelope). ✅ **SHIPPED** (2026-06-04). See §10.1.
- **Phase 3 — KG, agentic.** 3a query the existing graph on-demand (Stage 7,
  context-constrained). 3b agentic-extraction verify pass (propose→critic→resolve)
  to raise entity/relationship quality before the graph is trusted. 3c measure.
  ⏳ **pending.**
- **Noted:** faithfulness LLM gate tuning; whether per-doc RAPTOR earns its cost.

### 10.1 Implementation status (T2 2026-06-03; T3 2026-06-04)

**Phase 1 / T2 — shipped on `feat/roadmap-t1-t2-t3`.** New modules:
`domain/schema_epoch.py`, `domain/structured_schema.py` (§6.1),
`query/structured_prefilter.py` (`ResolvedPredicate` + resolver §6.2/6.12 +
scope state machine §6.7), `query/structured_answer.py` (answer_mode §6.4 +
trust gate §6.3 + S5a/S5c + §6.13 locator gate). Changed: `channels.py`
(`file_scope` §6.8), `orchestrator.py` (S0.5 gate, confidence-weighted scope +
relevance-widen §6.10, structured-answer branch, scope surfacing §S10),
`chat_memory.py` (`carry_forward_predicate`), `workers/tasks.py` + `api/schemas.py`
(epoch bump §6.1). Migration `0054`.

**Verified:** full test suite green (zero new failures; ~53 new T2 tests across
`tests/test_t2_*`). Live finance eval unchanged-or-better (14/16, within range;
misses are base-pipeline nondeterminism, not T2). Adversarial runtime trace on
the seeded finance corpus confirmed §8 #1 (broaden), #2 (honest-never-worse /
relevance-widen), #3 (coverage≠correctness / P2), #5 (relax), #6 (reset by domain
noun), #9/#10 (no-bare-No / entity-miss hedge), #11 (LIST + conversational),
#12 (row-level date), #13 (suite green).

**Done differently / via existing code:** §6.5(2) stated-vs-stated / amendment
reconciliation is handled by the existing R1 conflict-resolution path (not
re-implemented).

**Phase 2 / T3 — shipped on `feat/roadmap-t1-t2-t3`.** New modules:
`q_planner/dynamic_catalog.py` (per-workspace catalog DERIVED from
`structured_schema.live_schema()` + a value-castability probe; spelling-variant
unit_types unioned; §6.11), `q_planner/group_by.py` (group-key canonicalization +
`GROUPBY_CARDINALITY_CAP`). Changed: `q_planner/validator.py` (`value_type` gate,
opt-in via `live_catalog=`), `q_planner/compiler.py` (`compile_row_filters`
row-level WHERE + guarded ISO-date cast + supersedes-dedup `exclude_file_ids`),
`query/q_payload_gen.py` + `query/planner.py` (feed the catalog to the planner +
generation-time validate), `query/mode_router.py` (`_route_q_mode` envelope:
self-repair → canon → reconcile → sanity → gated return), `query/orchestrator.py`
(thread `predicate`), `query/citations.py` (envelope on the aggregate citation).
No migration (reuses `audit_queries`).

**T3 verified:** §8 #4 (wrong-grain/all-NULL caught by the sanity check → typed
refusal, never a bare number), #7 (stated-vs-computed surfaced), #12 (row-level
date filter compiles into the WHERE — temp-table execution proof). 23 new tests
(`tests/test_t3_q_mode.py`); full suite zero new failures (38 pre-existing); live
eval 15/16 (within variance). Live adversarial trace: the silent-`None` and bare
606M aggregates now ship an audit envelope ("computed from N rows") or a typed
`q-mode-refusal`. **The §6.6 sanity check (contributing-rows) is the load-bearing
guard, NOT the value_type gate** — string-stored numerics ("INR 18,400/year")
can't be pre-classified, so the gate stays conservative and sanity catches the
all-NULL.

**T3 follow-ups (hardening, shipped 2026-06-04).** Closed the worst planner-
dependent gaps without trusting the planner:
- **Deterministic date window** (`_derive_date_row_filter`): when the planner
  emits no `date_filter`, Q-mode now lifts a date phrase from the query +
  the catalog's date key on the target unit_type and applies it at row level
  ("highest transaction last quarter" no longer silently runs all-time). The
  §6.6 sanity check backstops an over-narrow to zero rows.
- **Grain / overlap caveats** (surfaced in the envelope, never silently change
  the number): a SUM over ≥2 distinct period-variant `field_name`s
  (`_heterogeneous_sum_caveat` — the live "606M" mar-31 + jan-1 + original trap)
  and an additive aggregate unioning ≥2 semantically-distinct unit_types
  (`_multi_unit_type_caveat` — `major_transaction` within `transaction_listing`).
  Live-confirmed: the 606M sum now ships a "different points in time" warning and
  the answer reports a coherent as-of-date figure instead.

**Remaining T3 gaps (lower priority):** date interpretation is calendar-relative
(not data-/fiscal-relative); active *reconciliation* still covers only the
single-SUM-over-unit-rows case (the caveats cover the `proposed_fields` SUM case
instead); dedup is doc-version-level + caveat (not silent row-overlap dedup, which
needs a reliable natural key); "avg by lender"-style grouping is still chosen by
the planner. The universal guard on every aggregate is the audit envelope + sanity.

**NOT built (the honest gaps):**
- **Stage 7 KG route (§6.9)** — unchanged from pre-T2 (Phase 3). RELATIONSHIP
  queries use the old PPR path; the context-constrained / type-filtered walk and
  the agentic verify pass are not built.
- **Quality ceiling (not a pipeline gap):** T2 *uses* the structured layer well
  but can only narrow on what extraction captured — strong on tables, weak on
  narrative (~0/13 narrative docs captured key terms), ~47% mention resolution.
  Where there's no structured signal it correctly degrades to plain RAG.

**Known rough edges (logged, non-blocking):** S5c LIST shows an arbitrary field
value for a file with multiple doc_root rows (e.g. an xlsx) — the doc-set is
correct, the displayed value may not be; EXISTENCE-absent on an entity returns
an empty refusal rather than a friendly hedge; the live trace was finance-only
(no legal/construction runtime trace).

---

## 11. Decisions ledger (every finding → resolution → section)

**v1 review — pass 1 (decision-point holes):** fuzzy answer gate/"0"→§6.3,§6.5;
wrong-but-nonempty narrow→§6.2,§6.10,P1; replace/refine→§6.7; hybrid→§6.4;
KG-tail→S7; structured-no-chunks→§6.6; counts-weak-entity→§6.3,§6.4.
**v1 review — pass 2 (second-order):** aggregate text-grounding→§6.6;
hybrid-self-contradict→§6.5; auto-widen-noise→§6.10 (relevance+union);
multi-turn drift→§6.7; routing invisible→§6.7,S10; one-weak-link→§6.3,P1;
coverage source→§6.3. **Correction:** stated>computed→§6.5 (with v2 caveats).
**Subsystem audit:** dead carry-forward→§6.7/§7 (now the predicate);
RAPTOR-NULL→§6.8; triples/relationships unused→S7/§10.

**Cross-domain war-game (12) →**
W1 aggregate bypasses coverage→§6.3 (gate on aggregate too) ·
W2 count-by-entity undercount→§6.3/§6.4 (demote to LIST+caveat) ·
W3 0-match per-field→§6.3 (weakest-clause; entity always low) ·
W4 reconciliation passive→§6.5(1) (active fetch) ·
W5 row-filters/relative dates not in SQL→§6.2 dates + §6.11 row-filter handoff + §6.12 ·
W6 grain/dedup double-count→§6.11 ·
W7 GROUP BY value fragmentation→§6.11 ·
W8 HYBRID entity mismatch→§6.5(3) ·
W9 no LIST mode→§6.4/S5c ·
W10 no conversational mode→§6.4/S0.5 ·
W11 ambiguous field→§6.2/§6.10 ·
W12 lookup multi-doc / same-name conflation→§6.2/§6.4 ·
W13 empty entity/doctype scope silent→§6.10 + `ENTITY_TRGM_MIN` + S10 surfacing ·
W14 cross-clause AND zero-trap→§6.2 AND-salvage ·
W15 KG filtered multi-hop/type→§6.9 ·
W16 quarter/fiscal dates→§6.2.

**Senior-architect pass (12) →**
A1 "never worse" false (count signal)→I1 + §6.10 relevance-widen ·
A2 carry-forward file_ids can't relax→§6.7 RELAX + §6.12 predicate + §7 ·
A3 coverage=presence≠correctness→§6.3 (presence×correctness P2) ·
A4 aggregate faithfulness exemption removes guard→§6.6 audit envelope + §6.11 sanity ·
A5 structured-first inverted utility→P1 confidence-weighted scope ·
A6 circular trust→P2 independent verification on every structured answer ·
A7 mode-before-data→§6.4 revisable at S4 ·
A8 stated>computed assumes stated authoritative→§6.5 (same-quantity incl. scope; surface-both) ·
A9 schema-to-planner doesn't scale→§6.1 linking-as-retrieval ·
A10 T1 rename invisible to cache→§6.1 schema_epoch + §7 ·
A11 dynamic whitelist trust boundary→§6.11 trust-boundary note + cardinality cap ·
A12 two predicate engines diverge→§6.2 (SQL is source of truth; Python = fallback only).

---

## 12. Build order
`structured_schema` (linking + epoch) → `ResolvedPredicate` + resolver
(per-clause confidence, salvage, dates, ambiguity) → channel `file_scope` +
pgvector + confidence-weighted scope + relevance-widen → scope state machine
(predicate-carry + RELAX + schema reset) → answer-mode (revise + LIST +
CONVERSATIONAL) → trust gate (presence×correctness, weakest-clause) → S5a/S5c +
P2 + premise gate → reconciliation (active + stated-vs-stated + hybrid-entity) →
surface → tests. (T3 §6.11 internals and Phase-3 KG follow as their own
branch-commit cycles.)
