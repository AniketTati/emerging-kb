# Roadmap — forward-looking build tasks

Concrete, pick-up-able engineering tasks toward enterprise scale. Big-picture
context lives in the repo [`README.md`](../README.md) ("Shipped vs. roadmap") and
[`scale_perf_audit.md`](scale_perf_audit.md). Each task below is written to be
started **cold**, without the conversation that produced it.

---

## T1 — Schema as a view: alias-map convergence + targeted re-extraction

**Status:** ✅ shipped (`feat/roadmap-t1-t2-t3`; in-place key convergence + display-pointer rename, NO auto re-extraction) · **Area:** ingestion / schema convergence · **Impact:** high (correctness-at-scale + cost)

### The problem

The system's stated principle is *"schema is a view, not a precondition"* — but the
**corpus-finalize** step contradicts it by **re-extracting documents** whenever the
schema converges. As written, this re-extracts the **entire workspace on every
corpus settle**, with no staleness guard, and re-fires on every new upload — so
trickle-ingesting one doc at a time re-processes all previously-ready docs again.
It's O(corpus) work per O(1) new document. (Tolerable at demo scale only because the
demo ships a pre-baked seed and the job self-gates to once-per-settle; it would
genuinely hurt a live, growing workspace.)

The key realisation: **the raw extraction is already exhaustive and immutable** —
every field the LLM ever pulled is in `proposed_fields`, and every table-row's
values are in `extracted_entities.fields` (JSONB). So when the schema converges, the
data is *already captured*; it may just be under a different **key name**
(`end_balance` vs `closing_balance`). Convergence should therefore **re-map keys**
(rename / merge / alias), **not re-read documents.** Re-extraction should be a rare,
targeted fallback — only when a needed field has **no candidate at all** in a doc's
raw layer (a genuine miss), and then only that doc + that field.

### Current behaviour (what to change) — all in `src/kb/workers/tasks.py`

- `finalize_corpus_impl` (≈ line 4913): self-gates on ingest-settle, then runs, in
  order: `converge_workspace_fields` → `reextract_workspace_schema_entities` →
  `reconcile_workspace_entities` → `renumber_workspace_chains` → `raptor_build_corpus`.
- `reextract_workspace_schema_entities_impl` (≈ line 4845): selects **ALL** ready docs
  (`lifecycle_state='ready'`, non-unknown `inferred_doc_type`) and for **each** runs
  `extract_kv_tables_file_impl(force=True)` **and** `extract_schema_entities_file_impl(force=True)`
  — two LLM-heavy calls per doc, **no per-doc staleness guard**. This is the blanket
  re-extraction to replace. (It already has a `doc_type=` filter from FIX 10 — used by
  manual schema-edit triggers — which is the right *shape*, just not applied
  automatically by staleness.)
- `converge_workspace_fields_impl` (≈ line 4507): already re-clusters `proposed_fields`
  by meaning (embedding-similarity blocking + the `field_judge` LLM merge judge via
  `converge_clusters_semantic`) and promotes. Good — but today its output feeds the
  blanket re-extraction instead of a cheap mapping. It should additionally **emit an
  alias map**.

### Design

1. **Exhaustive first pass (mostly already true).** Make sure `extract_kv_tables`
   (the open-vocab KV+Tables call) is prompted to capture *every* key/value and
   *every* column it can find, schema-agnostically. The more complete the first read,
   the more convergence is pure renaming. This is the lever that shrinks the residual
   re-extraction set toward zero.

2. **Field-alias map (new).** Add a `field_aliases` table:
   `(workspace_id, doc_type, raw_key) → canonical_field_id / canonical_key`, plus
   `method` (embedding | judge | manual) and `confidence`. `converge_workspace_fields`
   writes aliases here. Schema Studio **rename / merge / split** operations become
   writes to this map — O(1), no LLM, no document re-read.

3. **Read through the map.** The query field paths — field-filter mode, `Q`-mode SQL
   aggregation, and the doc-detail field accordion — resolve raw keys → canonical via
   `field_aliases`, so a query for `closing_balance` transparently gathers
   `end_balance`, `closing_bal`, etc. (A join / lookup in the field read path and any
   retrieval channel that touches structured fields.)

4. **Schema versioning + targeted re-extraction.** Add a `schema_version` per
   `(workspace_id, doc_type)` that bumps on promotion/convergence, and stamp each doc
   with the version it was extracted against (e.g. `files.schema_version_at_extract`).
   Replace `reextract_workspace_schema_entities`'s "all ready docs" selection with
   **only docs that (a) are behind the current `schema_version` AND (b) have a
   canonical field with no raw candidate (a true miss)** — re-extract just those docs,
   ideally just the missing field. Everything else is handled by the alias map.

### What still needs (targeted) re-extraction — keep this fallback

Pure remapping covers ~90%. Two cases genuinely require re-reading the document, and
they're the *only* reasons re-extraction should ever fire:

- **True miss:** a needed value isn't in the raw layer under *any* key.
- **Structural transform not derivable from stored data:** e.g. a fact that lived in
  prose and was never captured as a field/column.

Both are rare if the first pass is exhaustive. Handle them **per-doc / per-field**,
never as a blanket corpus re-do. (An LLM first pass is never perfectly exhaustive, so
this fallback can't be deleted — only made rare and targeted.)

### Files / tables to touch

- **Change** `src/kb/workers/tasks.py`:
  - `reextract_workspace_schema_entities_impl` — blanket → targeted (schema-version +
    missing-field gap).
  - `converge_workspace_fields_impl` — also emit `field_aliases` rows.
  - `finalize_corpus_impl` — gate the re-extract step on "schema actually changed since
    last run"; consider debouncing so it doesn't fire on every single upload.
- **New:** `field_aliases` table (+ migration under `migrations/sql/`); a resolver in
  `kb.domain.fields`; `schema_version` columns on `schemas`/`schema_entities` and a
  per-doc stamp on `files`.
- **Read path:** wherever structured fields are read for query (field-filter, Q-mode
  SQL builder, doc-detail) — resolve through `field_aliases`.
- **Reuse:** `kb.extraction.promotion` (`cluster_fields_for_doctype`,
  `converge_clusters_semantic`, `should_promote`, `promote_field`),
  `kb.extraction.field_judge` (`same_field`).

### Acceptance criteria

- Rename / merge a field in Schema Studio → **O(1)**, no LLM call, no document
  re-read; queries reflect it immediately.
- Uploading one new doc into a settled workspace re-extracts **zero** previously-ready
  docs (only the new doc ingests).
- `finalize_corpus` re-extraction count == number of docs with a genuine missing-field
  gap (**0** when the schema was already stable), not "all ready docs".
- Cold-start docs still gain genuinely-missed fields (via the targeted fallback).
- 16-pair submission eval (`scripts/run_submission_eval.py`) unchanged or better; no
  field that was previously queryable disappears (verify alias coverage).

### Notes / guardrails

- Keep `proposed_fields` / `extracted_entities.fields` as the **immutable source of
  truth** — never overwrite the raw layer during convergence.
- Keep force-mode re-extraction **non-destructive** (preserve existing data if a re-run
  returns empty — the current FIX 9 behaviour).
- Follow the repo's gate discipline; branch before committing; **no `Co-Authored-By`
  trailer**.

---

## T2 — Structured pre-filter for retrieval (narrow doc-ids first, then search)

**Status:** ✅ shipped as Phase 1 (`feat/roadmap-t1-t2-t3`, 2026-06-03) — the
structured-first head went well beyond this sketch (confidence-weighted hard/soft
scope + relevance-widen, predicate carry-forward + relax/reset, answer-direct
LOOKUP/LIST/EXISTENCE with P2, locator gate). **The authoritative spec + exact
implementation status is [`query_pipeline_plan.md`](query_pipeline_plan.md) §10.1.**
· **Area:** query / retrieval · **Impact:** high (precision + recall on structured questions)

> **Detailed plan:** [`query_pipeline_plan.md`](query_pipeline_plan.md) is the
> authoritative, build-ready design for the new query pipeline (T2 + T3 +
> KG-as-a-route). The sketch below is kept for context; where they differ, the
> detailed plan wins.

### The problem

Today the structured layer narrows results only as a **post-retrieval filter**:
`_route_f_mode` (`src/kb/query/mode_router.py`) and the chat `@`-doc filter prune the
hits that hybrid retrieval *already found*, keyed by `file_id`. The retrieval channels
(`src/kb/query/channels.py`) search the **whole workspace**; structured attributes never
scope the vector / BM25 search itself. Consequence: it can **prune but not broaden** —
if the chunk that answers a field-scoped question didn't make the global hybrid top-K,
the filter can't recover it.

### The fix

Resolve the structured predicate → a set of matching **`file_id`s FIRST**, then run the
vector + BM25 channels **scoped to those file_ids** (`WHERE <file_id col> = ANY(:scope)`),
so retrieval searches only within the matching docs. This turns narrowing into a true
**pre-filter** that surfaces the right chunk even when it wouldn't rank globally — and it's
faster (smaller search space).

### Design

1. **Doc-id resolver.** A function that takes `plan.field_filters` (+ entity / doc-type /
   metadata constraints) and returns the matching `file_id` set — a cheap SQL over
   `extracted_entities.fields` / `schema_fields` / `files`. Reuse F-mode's predicate logic
   (`_field_predicate_holds`, `_resolve_field_name` — incl. the canonical-name mapping)
   but run it **before** retrieval as a doc-id query.
2. **Thread `file_scope` into the channels.** Add an optional `file_scope: set[str] | None`
   param to each channel in `channels.py` (`bm25_chunks`, `dense_chunks`, `bm25_raptor`,
   `dense_raptor`, `mentions_exact`, `atomic_units`) → `AND <file_id col> = ANY(%s)`.
3. **Orchestrator.** When the plan has structured constraints, resolve scope → pass into
   `_retrieve_and_rerank` so every channel is scoped.
4. **Degrade rules (don't lose recall).** 0 matching docs → return a clean *"no documents
   match `<predicate>`"* answer (a meaningful result), not a silent empty refusal. If the
   predicate is ambiguous/over-broad, fall back to **unscoped retrieval + the current
   F-mode post-filter** so scoped retrieval can never answer *worse* than today.

### pgvector + scope caveat (must handle + test)

Filtering an HNSW ANN search by `file_id` can **under-return** — the ANN graph walk is
filter-blind, so a small matching set may be missed. Pick one:
- pgvector ≥ 0.8 **iterative index scans** (`SET hnsw.iterative_scan = relaxed_order`), or
- for small scopes, **exact/bruteforce** vector search within the scoped rows (skip HNSW), or
- **over-fetch** (raise `ef_search` / candidate K) then filter.
BM25 (ParadeDB) with a `file_id` WHERE is straightforward — the asymmetry is the vector path.

### Follow-up queries (explicit requirement)

The active doc-id scope must **persist across turns**. *"…and which of those were above 9%?"*
should narrow within the previously-resolved scope.
- Store the resolved `file_id` scope in the **session carry-forward state**
  (`kb.domain.chat_memory` / the `ChatContext` the context resolver reads).
- Rule (make it explicit + surface in the Plan Inspector): a follow-up **inherits** the prior
  scope by default; an **explicit new filter replaces** it; *"across all docs"* **clears** it.
  Show *"scoped to 12 docs from your previous question"* in the inspector.

### Files / tables

- `src/kb/query/channels.py` — add `file_scope` to each channel's SQL.
- `src/kb/query/orchestrator.py` (`_retrieve_and_rerank`, `chat`) — resolve scope pre-retrieval; thread through; degrade rules.
- `src/kb/query/mode_router.py` (`_route_f_mode`, `_resolve_field_name`, `_field_predicate_holds`) — extract the predicate logic into the pre-filter resolver; keep post-filter as fallback.
- `src/kb/query/context_resolver.py` + session carry-forward — persist + inherit the scope across turns.
- Possibly a migration / index to keep `file_id`-scoped vector search efficient.

### Acceptance criteria

- A field-scoped question surfaces the right chunk even when it wouldn't rank in the global top-K (**broaden**, not just prune).
- 0-match predicate → a clear *"no docs match"* answer, never a silent refusal.
- A follow-up narrows **within** the prior scope; *"across all docs"* resets it; the scope shows in the Plan Inspector.
- Vector recall under scope verified — a small matching set is NOT lost to HNSW under-return.
- 16-pair eval (`scripts/run_submission_eval.py`) unchanged or better.

### Guardrails

- Scoped retrieval must fall back to unscoped + post-filter — never return empty when unscoped would have answered.
- Branch before committing; **no `Co-Authored-By` trailer**.

---

## T3 — Q-mode: schema-derived catalog for generous aggregation

**Status:** proposed — **NEXT UP** (Phase 2; T2 shipped, this is the immediate
follow-on). The T2 resolver already produces `ResolvedPredicate.row_filters` +
grain hints + an active-reconcile seam waiting for this. See
[`query_pipeline_plan.md`](query_pipeline_plan.md) §6.11/§10.1. · **Area:** query / Q-mode · **Impact:** medium-high (fewer aggregation refusals)

### The problem

Q-mode (`_route_q_mode` + `src/kb/q_planner/`) runs a safe 10-layer SQL pipeline, but its
`validate` step checks a **fixed catalog whitelist** — so it "only sums over a fixed set of
tables" and **refuses many legitimate aggregations** over the structured data the system
actually extracted (`extracted_entities` rows / promoted `schema_fields`). The schema
**emerges** from the data, but the catalog is hand-fixed — a mismatch that makes Q-mode
feel stingy.

### The fix

Make the catalog **derive from the live, emerged schema** so aggregation is generous:
**any** promoted/canonical field (`schema_fields`) and **any** `unit_type` table
(`extracted_entities` grouped by `unit_type`, with their JSONB `fields` columns) is
aggregatable — while keeping **every** safety layer (parameterized SQL, read-only, timeout,
row cap, type checks, append-only audit).

### Design

1. **Dynamic catalog.** Build the Q-planner catalog **per-workspace** from `schema_entities`
   / `schema_fields` (canonical fields + their `value_type`) + the distinct
   `extracted_entities.unit_type`s + observed `fields` JSONB keys. The `value_type` tells you
   what's numerically summable.
2. **Feed the live catalog to the planner LLM** (`q_payload_gen.py`) so it generates valid
   plans for the data that actually exists — directly cutting "could not build a safe plan"
   refusals.
3. **JSONB-aware compile.** Aggregating a column stored in `extracted_entities.fields` needs
   a typed cast (e.g. `(fields->>'amount')::numeric`), **gated by the field's `value_type`**
   so a non-numeric field refuses cleanly with a clear message instead of erroring.
4. **Keep all safety layers.** The catalog is now schema-derived but it is **still a
   whitelist** — not arbitrary SQL. Still parameterized, read-only, timeout-bounded,
   row-capped, and audited (`audit_queries` + CSV artifact).

### Files / tables

- `src/kb/q_planner/` (catalog, validate, compile, grammar) — schema-derived catalog; JSONB typed-cast compile path.
- `src/kb/query/q_payload_gen.py` — pass the live catalog into the planner prompt.
- Reuse `schema_fields.value_type` for type-safety; `extracted_entities.unit_type` + `fields` for the row tables.

### Acceptance criteria

- *"Sum Acme's outstanding loans"*, *"total April transactions"*, *"count contracts by type"*,
  *"average interest rate by bank"* all work **iff** the underlying structured field / unit_type
  exists — no longer refused for being "off-catalog".
- Non-numeric aggregation → a clean typed refusal (*"field X isn't numeric"*), not a crash.
- Still safe + auditable: parameterized, read-only, timeout, row cap, `audit_queries` + CSV.
- Q-mode refusal rate on the eval's aggregation questions drops; 16-pair eval unchanged or better.

### Guardrails

- "Generous" = a **wider, schema-derived whitelist** — NOT raw/arbitrary SQL. Keep every safety layer.
- Branch before committing; **no `Co-Authored-By` trailer**.

> **Note:** T2 and T3 both introspect the structured schema (`schema_fields`,
> `extracted_entities`) — consider a shared "live structured-schema" helper that the
> doc-id resolver (T2) and the dynamic catalog (T3) both reuse.

---

*(Other roadmap items — horizontal ingest / second-worker deadlock, incremental
corpus RAPTOR, streaming + caching on the query path, conflict-detection precision,
identity robustness, real multi-tenant UI — are summarised in the repo README's
"Shipped vs. roadmap" and analysed in `scale_perf_audit.md`. Add them here as
detailed task specs when they're picked up.)*
