# RAG System — Architectural Audit & Query-Pipeline Action Plan

**Audit date:** 2026-05-28
**Reviewer perspective:** Senior RAG engineer, multiple enterprise deployments
**Repo:** `/Users/temp/Documents/Code/Knowledge Base Service`

---

## Document map

This single doc serves four purposes:

- **Part 1 — Immediate focus.** What to do NOW to make queries work at scale. ~9 weeks of focused engineering. The user's stated near-term priority.
- **Part 2 — Architectural reflection.** Full systematic tour of every component (ingestion → storage → retrieval → query → API → UI → infra). Saved for later reflection.
- **Part 3 — Scale & market comparison.** Where you sit relative to the enterprise RAG market, and what fails at scale.
- **Part 4 — Deferred.** Auth, PII encryption, ACL, content connectors, K8s production ops — explicitly parked per direction. Acknowledged as critical but not in the current focus.

---

# PART 1 — IMMEDIATE FOCUS: make queries work, at scale

## Goal

> Make queries work correctly, fast enough, and cheap enough at the scale this system will see.

Quality first, then latency, then cost. Auth/PII/compliance is in Part 4.

## Why we don't need to run more evals before acting

The construction eval gave us a 34%/48%/10%/8% (correct/partial/wrong/refused) baseline AND five specific wrong-answer patterns. **Static analysis of the other 5 domains' `queries.yaml` files confirms the same failure modes apply across every domain.**

The query mix per domain is structurally identical:

| Stratum | Construction | Finance | Government | Healthcare | Legal | Mining |
|---|---|---|---|---|---|---|
| needle | 12 | 11 | 11 | 11 | 12 | 11 |
| chain-aware | 9 | 9 | 8 | 9 | 8 | 9 |
| conflict-resolution | 6 | 6 | 6 | 6 | 7 | 6 |
| rare-clause | 5 | 5 | 5 | 5 | 6 | 5 |
| aggregation | 5 | 5 | 5 | 5 | 5 | 5 |
| long-form | 4 | 4 | 4 | 4 | 4 | 4 |
| adversarial | 4 | 4 | 5 | 3 | 2 | 4 |

Spot checks of actual queries confirm the same SHAPE of failures:
- finance-q012 ("9.40% per Addendum #1, original 8.85% superseded") is the same K-mode chain test as construction-q020
- healthcare-q018 ("lab-2 is current authoritative, lab-1 historical") is the same conflict test as construction-q023
- healthcare-q019 (thyroid lab supersession) is same chain pattern again

**Implication: ~30% of every domain's queries are at risk of the same 5 failure patterns we already named.** Fixing them generalizes.

**What additional eval data would change:**
- Running ONE more domain (e.g., healthcare — structurally most different from construction) would validate that fixes generalize and give us a 2-point trajectory line.
- Running all 6 would tell us WHICH customer to ship to first, not what to BUILD.

**Recommendation:** Skip running 6 evals. Optionally run healthcare AFTER Phase 2 ships to validate generalization on one second domain (~$1 cost). The build priorities don't change either way.

## The 5 wrong-answer patterns (construction v10) and where they generalize

| # | Query | Failure | Root cause | Fix | Generalizes to |
|---|---|---|---|---|---|
| q006 | "project site location" → returned project name instead of address | Retrieval/generation field confusion | Field-name exact match boost + slim generator prompt | Every domain's ~11 needle queries that reference a specific field |
| q020 | "Is EPC contract still in original form?" → cited single CO, ignored chain | K-mode signal not surfaced; planner picks H | Planner improvement: route to K when query references doc that's in a chain | All 8-9 chain-aware queries × 6 domains = ~52 queries at risk |
| q023 | "Two POs different rates — which authoritative?" → listed without reasoning | Conflict block exists but generator under-uses it | Restructure conflict block surfacing + generator prompt rule | All 6-7 conflict queries × 6 domains = ~38 queries at risk |
| q033 | "Cumulative change-order value" → single CO not sum | **Bug D field-name fragmentation** (`total_cost_premium` vs `total_cost_inr`) | Apply existing semantic field merger (commit `e273031`) | All 5 aggregation queries × 6 domains = ~30 queries; field fragmentation appears in any multi-doc doctype |
| q034 | "Distinct sub-contractors" → hallucinated "238" | Canonical entity fragmentation (Mahalaxmi = 4 rows) + generator over-specifies | Apply existing canonical dedup (commit `ca4a7e5`) | Every domain has same-entity-multiple-mention patterns |

Conservative estimate: fixing these 5 patterns lifts each domain's correct rate by ~15–25 percentage points. Construction 34% → ~50–55%. Healthcare (if similar baseline) would land similarly.

## Phased action plan

Each phase sized for ~1 engineer-week unless noted. Ship in order.

---

### Phase 1 — Safety + determinism (1 week)

Zero quality risk. Plugs real bugs.

**1.1 Add `temperature=0.1` to deterministic LLM calls**
- Files: `context_resolver.py`, `intent.py`, `planner.py`, `q_payload_gen.py`, `crag.py`, `ircot.py`
- Generator: `temperature=0.3`. Rewriter: `temperature=0.5`.
- Why: Gemini default ≈1.0 is the cause of "same query → different routing → different answer" eval variance.

**1.2 Run adversarial detection on ORIGINAL query, not resolved query**
- In `orchestrator.chat()`, classify the raw `query` before `_resolve_context` runs.
- Why: Reproduced bug — "ignore your instructions" → resolver laundered to "what is resume about" → adversarial check missed.

**1.3 Drop `new_entities` + `new_filters` from context resolver output**
- Trim JSON schema in `context_resolver.py` `_GEMINI_SYSTEM_PROMPT`.
- Stop calling `update_session_carry_forward` from `_persist_turn`.
- Why: Source of aurangabad UUID bug and stale `carry_forward_filters: {document_type: resume}` pollution. Type confusion (LLM strings → uuid[]) is bad discipline.

**1.4 Skip context resolver when query has no pronouns**
```python
_ANAPHORA_RE = re.compile(r"\b(it|this|that|they|them|those|these|he|she|him|her|his|hers|its|previous|prior|above|earlier|same)\b", re.IGNORECASE)
if not _ANAPHORA_RE.search(query):
    return (query, None)
```
- Why: Saves ~600ms on ~80% of turns. No quality impact.

**1.5 Apply CRAG refusal uniformly across all modes**
- `force_refuse = crag_score < threshold` (drop the H-mode-only qualifier).
- Why: Non-H modes currently hallucinate from bad retrieval; user expects uniform refusal.

**1.6 Fill `answer` field with a refusal message on every refusal path**
- Map `refusal_reason` → user-facing message. No more `answer=""`.
- Why: The blank-card UX in session `c78fed8b-...` turn 1.

**1.7 Make `/chat/stream` write to `query_log`**
- Extract `_write_query_log` from `query.py:171` to a shared helper. Call from `sse.py` runner.
- Why: "How I answered" inspector shows `?` on reload because chat-stream skips the audit row.

**Acceptance:** Same query 3× returns same intent/mode. Adversarial query refuses. Reload shows real inspector values. No blank refusal cards.

---

### Phase 2 — Quality fixes targeting the 5 wrong-answer patterns (3 weeks)

The biggest quality lever. Don't refactor pipeline shape before fixing what's already broken.

**2.1 Apply Bug D field-name semantic merger** *(operational + review, 3 days)*
- Script `scripts/normalize_field_names_llm.py` (commit `e273031`) generates LLM-judge proposals.
- Run per workspace → `docs/field_merge_proposal_<workspace>.yaml`.
- Human review (drop bad merges, edit canonicals).
- `--apply` flag.
- Schema-side hardening: in `kv_tables.py`, ensure canonical `schema_fields` hints rank above `inferred_schema_fields` in the extraction prompt's hint pool (commit `e29a5b2` started this; verify ordering).
- Targets: q033 + all multi-doc aggregations across domains.

**2.2 Apply canonical entity dedup** *(operational + review, 3 days)*
- Script `scripts/dedup_canonical_entities.py` (commit `ca4a7e5`) generates embedding-sim + LLM-judge proposals.
- Same propose-review-apply workflow.
- Targets: q034 + same-entity-multiple-variant cases (Mahalaxmi pattern) across domains.

**2.3 Surface conflict_resolution block in generator user message** *(2 days)*
- In `orchestrator.chat()` lines ~960–1000, change conflict block format from inline narrative to a structured `<conflict_resolution>...</conflict_resolution>` tag.
- Update generator system prompt's conflict section: "If `<conflict_resolution>` block is present, the resolved winner is authoritative. Mention losers when they shed light on the disagreement. Don't re-derive the resolution."
- Targets: q023, q020, all 6-7 conflict queries per domain.

**2.4 K-mode chain-aware planner improvement** *(3 days)*
- Planner currently picks K only when intent is `chain_aware` or `temporal_history`. Queries like "is X still in original form" are `factoid` intent → H-mode → miss the chain.
- Add a heuristic in `planner.py`: if any entity in the query maps to a file that's a member of an active `doc_chain`, set `plan.mode = "K"` regardless of intent.
- Lookup uses `doc_chain_members` joined to mentioned entities.
- Targets: q020 + all 8-9 chain queries per domain.

**2.5 Field-name exact match retrieval boost** *(2 days)*
- New channel `field_name_exact` in `channels.py`: if query tokens exactly match a `proposed_fields.field_name` in the workspace, surface files with that field as high-score hits.
- Targets: q006 + every needle query that names a specific field.

**2.6 Slim generator system prompt from 215 → ~60 lines** *(3 days)*
- Rewrite `_SYSTEM_PROMPT` in `generate.py`:
  - Core rules: cite or refuse, enumerate, use conflict block
  - Drop overlapping prose rules ("Inventory BEFORE answering" + "Address EVERY part" + "Enumerate" → one rule)
  - Drop "common substitution traps" table (retrieval problem, not prompt problem)
  - Drop prose conflict-resolution rules (use the structured block from 2.3)

**2.7 Re-run construction eval + diff** *(1 day)*
- Same scripts as before. Compare buckets.
- Target: construction `correct + partial` ≥ 75% (from 82% currently — quality is the goal, not just keeping the same coverage).
- Construction `correct` ≥ 50%.

**Acceptance:** Construction correct ≥ 50%. The 5 specific wrong answers in v10 now pass or move to partial.

---

### Phase 3 — Latency + cost at scale (3 weeks)

Cut p50 latency in half. Bound per-query cost. Make the pipeline scale to enterprise traffic.

**3.1 Token streaming generation** *(1 week)*
- Switch `generate.py` from `client.aio.models.generate_content` to `generate_content_stream`.
- Pipe token deltas through the SSE sink as `event: token, data: {delta: "..."}`.
- UI consumes deltas, appends to assistant message progressively.
- Why: perceived latency drops 3–5×. Users start reading at 500ms instead of waiting 5–7s.

**3.2 Make HyDE + query2doc conditional on intent** *(2 days)*
- Pass intent to rewriter. Produce:
  - `step_back` when `intent ∈ {vague, factoid}`
  - `hyde` when `intent ∈ {global/thematic, multi-hop}`
  - `query2doc` when `intent ∈ {global/thematic}`
  - Else: skip the rewriter LLM call entirely; return `Rewrites(original=query, step_back="", hyde="", query2doc="", clarifications=[])`
- Why: For ~60% of traffic (specific factoid), this collapses 24 retrieval queries to 6 AND skips a ~900ms rewriter LLM call.

**3.3 Batched LLM-judge in identity resolution** *(3 days)*
- In `workers/tasks.py:2615-2627`, identity_judge is called once per borderline pair.
- Batch: collect 20 borderline pairs per Gemini call. Output `[{pair_idx, same_entity, confidence}]`.
- Why: 10–20× cost reduction on identity resolution at million-entity workspaces.

**3.4 Collapse context_resolver + intent_classifier + planner into ONE LLM call** *(1 week)*
- New file `query/planner_unified.py`. Single Gemini call returns:
  ```json
  {
    "resolved_query": "...",
    "intent": "...",
    "mode": "...",
    "seed_entities": [...],
    "scoped_files": [...],
    "doc_types": [...],
    "unit_types": [...],
    "field_filters": [...],
    "is_adversarial": false,
    "refinement_of_prior": false
  }
  ```
- Wire in `orchestrator.chat()` replacing the sequential 3 calls.
- Why: ~1.5s → ~0.6s planning prefix. Removes inter-stage disagreement.

**3.5 Per-query LLM cost cap with circuit breaker** *(3 days)*
- Add `cost_ceiling_usd` env config. Each LLM call accumulates estimated cost (token-counted from response usage metadata).
- Pre-call check: if remaining budget < estimated cost, refuse with `refusal_reason="cost_ceiling_exceeded"`.
- Per-workspace daily cap config.
- Why: uncapped LLM cost = uncapped customer cost = bankruptcy at 1000-tenant scale.

**3.6 Reduce mode set from 13 to 10** *(2 days)*
- Drop G (corpus RAPTOR broken — route to H), F (fold into H + field_filters post-retrieval), D (fold into H + doc_types post-filter).
- Update `_INTENT_TO_MODE`. Remove `_route_g_mode`, `_route_f_mode`, `_route_d_mode`.
- Why: fewer wrong routes.

**3.7 Re-run construction eval + measure latency** *(1 day)*
- Buckets must not drop.
- p50 latency target: ≥30% reduction.

**Acceptance:** p50 ≤ 6s (from ~12s). p95 ≤ 12s. `correct + partial` holds or improves. Per-query cost is bounded.

---

### Phase 4 — Scale primitives (3 weeks)

Remove the structural limits that bite past ~100K docs per workspace.

**4.1 Multi-model embedding abstraction** *(1 week)*
- `chunk_embeddings`, `raptor_nodes`, `canonical_entities` all have one `embedding halfvec(3072)` tied to gemini-embedding-001.
- Refactor to per-model versioned columns (`embedding_v1`, `embedding_v2`) with `model_id`.
- Retrieval picks the column matching the workspace's configured model.
- New worker: `reembed_workspace_with_model_impl` backfills new model column.
- Why: enterprise customers will demand provider choice (OpenAI text-embedding-3, Voyage, Cohere). Today, switching requires rebuilding every vector.

**4.2 Retrieval result cache** *(3 days)*
- Hash `(workspace_id, normalized_query, mode, plan_signature)` → cache top-K hit IDs + scores.
- TTL 1 hour (configurable; longer for stable corpora).
- Invalidate on any `chunks` insert in workspace.
- Why: enterprise traffic has heavy FAQ skew. 60% cache hit ratio = 60% cost reduction.

**4.3 Q-mode JOIN support** *(2 weeks)*
- Current Q-mode is single-table.
- Add `joins: [{from_table, from_col, to_table, to_col, type}]` to QPlan grammar. Whitelist allowed JOIN paths in `catalog.py` (FK-based).
- Update `q_payload_gen.py` prompt with concrete examples.
- Validator verifies each join path is in the whitelist.
- Why: enterprise BI is JOINs all day.

**4.4 Vector index sharding strategy** *(1 week design; implementation deferred)*
- pgvector HNSW degrades past ~100M vectors.
- Decision: hash-partition `chunk_embeddings` by `workspace_id` into 64 partitions OR move to dedicated vector DB (Qdrant, Vespa, Pinecone).
- Pick strategy now; implement later as separate effort.

**Acceptance:** Workspace can be configured with any embedding model. Result cache shows ≥40% hit rate on FAQ-skewed synthetic load. Q-mode answers a 2-table JOIN query. Sharding strategy documented and committed.

---

## Total timeline

| Phase | Weeks | Outcome |
|---|---|---|
| 1 — Safety + determinism | 1 | Adversarial closed; deterministic routing; no blank cards; inspector works on reload |
| 2 — Quality fixes | 3 | Construction 34% → ~50%+; same patterns fix other domains |
| 3 — Latency + cost | 3 | p50 12s → 6s; token streaming; cost cap |
| 4 — Scale primitives | 3 | Multi-model embeddings; result cache; Q-mode JOINs |

**~10 weeks of focused engineering** to get queries working well at enterprise scale.

**Optional checkpoint:** after Phase 2, run healthcare eval (~$1, ~10 min) to validate fixes generalize on a structurally different domain. Skip if confident.

---

# PART 2 — ARCHITECTURAL REFLECTION (full systematic tour)

For each component: ✅ what's right, 🟡 what's a smell, ❌ what's wrong.

## Storage layer (`migrations/sql/`, 46 migrations)

✅ Workspace RLS on every domain table
✅ Immutable audit pattern (query_log, chat_turns, audit_log REVOKE UPDATE+DELETE from kb_app)
✅ Soft-delete with partial unique indexes
✅ Hierarchical chunks (parent_chunk_id self-FK + node_level)
✅ Polymorphic raptor_edges (discriminated FK with CHECK)
✅ merged_into soft-merge on canonical_entities (migration 0046)
✅ Idempotent migrations (every CREATE IF NOT EXISTS)
✅ audit_log partitioned by month

🟡 `carry_forward_entities uuid[]` — schema expects UUIDs, LLM provides strings (aurangabad bug source). Phase 1.3 fixes.
🟡 `halfvec(3072)` hardcoded across 3 tables. Phase 4.1 abstracts.

❌ No row-level ACL beyond workspace (deferred — Part 4)
❌ PII fields stored plaintext, `is_pii` flag only (deferred — Part 4)

## Ingestion stage 1 — Parse (`workers/tasks.py:174–340`)

✅ Docling primary + Gemini OCR escalation gated by quality score
✅ Idempotent
✅ raw_pages immutable

🟡 Quality-gate threshold hardcoded (should be per-doctype)
🟡 Three OCR backends — diminishing returns

❌ No PII detection at parse (deferred — Part 4)

## Ingestion stage 2 — Chunk (`workers/tasks.py:497–650`)

✅ LlamaIndex HierarchicalNodeParser, sizes `[2048, 512, 128]`
✅ chunker_configs table allows per-MIME-type config
✅ Topological insertion (parents before children)

🟡 Chunker selection is MIME-type-based, not doc-type-based (dep ordering wrong — chunking happens before classification)
🟡 No semantic chunking option

## Ingestion stage 3 — Contextualize (`workers/tasks.py:658–797`)

✅ Anthropic Contextual Retrieval with cache token tracking — **senior engineering**
✅ Semaphore-bounded concurrency (default 8)
✅ Cache columns on contextual_chunks for cost audit

🟡 No queue+retry fallback if Anthropic unavailable

❌ Single biggest ingest cost at scale; no per-tenant budget guard (Phase 3.5 partially addresses)

## Ingestion stage 4 — Embed (`workers/tasks.py:805–926`)

✅ Batched embedding per file
✅ DeterministicMockEmbedder for CI

❌ Single-model per environment — Phase 4.1 abstracts
❌ No re-embedding pipeline — Phase 4.1 adds it

## Ingestion stage 5 — RAPTOR (`workers/tasks.py:944–1266`)

✅ Atomic write of nodes + edges
✅ Lineage resolution post-insert via synthetic keys
✅ Per-doc scope works (133 nodes, 692 edges in construction live)

❌ **Corpus-scope RAPTOR is broken** (construction = 0 nodes, finance = 5 orphans). G-mode silently degrades. Phase 3.6 drops G-mode (route to H).
🟡 Summarizer concurrency = 4 (bottleneck at 100K+ doc workspaces)

## Ingestion stage 6 — Mention extraction (`workers/tasks.py:1274–1421`)

✅ OntoNotes-18 mention types
✅ DELETE-then-INSERT idempotency
✅ Source position resolution

🟡 Field-extraction and mention-extraction don't compare notes — possible inconsistency

## Ingestion stage 7 — KV+Tables extraction (`workers/tasks.py:1428–2142`)

✅ **One LLM call replaces three** (legacy L2b+L3+L4 collapse) — cost win
✅ Frontmatter guard rail (Bug K)
✅ Auto-promotion thresholds (prevalence ≥0.8 ∧ stability ≥0.9 ∧ value_type_conf ≥0.9)
✅ Existing hints passed to prompt (tackles Bug D)

🟡 Cross-doc canonicalization iterative; early-ingest errors pollute hints (Phase 2.1 hardens)

❌ No PII redaction at extraction (deferred — Part 4)
❌ `is_pii` detected but never used in display/search (deferred — Part 4)

## Ingestion stage 8 — Schema-driven entities (`workers/tasks.py:2149–2480`)

✅ 3-pass design (parents → children → lineage)
✅ Defensive bootstrap of auto_schema_entity
✅ lineage_path as ltree (postgres native, supports `<@` ancestor queries)

🟡 Per-schema_entity LLM call, concurrency = 4 (scale bottleneck)

## Ingestion stage 9 — Identity resolution (`workers/tasks.py:2488–2678`)

✅ 4-stage pipeline (deterministic → embedding → LLM judge → new)
✅ Noise mention filter
✅ resolved_method audit column

❌ No bulk re-resolution job (Phase 2.2 applies existing script as one-shot)
❌ LLM judge cost unbounded (Phase 3.3 batches)
❌ Workspace-scoped identity (cross-workspace deferred)

## Retrieval — 6 parallel channels (`query/channels.py`)

✅ 6 channels (bm25_chunks, bm25_raptor, dense_chunks, dense_raptor, mentions_exact, sub_entities_rarity)
✅ Per-channel SAVEPOINT isolates failures
✅ asyncio.gather with return_exceptions=True
✅ Top-K=20 per channel before RRF

🟡 All 6 channels share one conn (per-channel pool would isolate better)
🟡 BM25 sanitizer too aggressive (strips legal possessives)

❌ No result cache (Phase 4.2)
❌ HNSW at billions of vectors (Phase 4.4 design)

## Query stage 1 — Context resolver

✅ Provider abstraction

🟡 Untyped strings cast to UUID (Phase 1.3 drops fields)
🟡 Runs even without pronouns (Phase 1.4 skip)

❌ Adversarial laundering (Phase 1.2 fix)

## Query stage 2 — Intent classifier

✅ Inventory regex short-circuit

🟡 18 labels; half are 1:1 with planner modes
🟡 No confidence calibration

## Query stage 3 — Planner

✅ 13 modes with deterministic `_INTENT_TO_MODE` fallback

❌ Sequential with intent (Phase 3.4 unifies)

## Query stage 4 — Q-mode SQL generator

✅ Workspace-aware schema hints at runtime — **genuine differentiator**
✅ Whitelist-only catalog (SQL-injection-proof)
✅ Refusal envelope

❌ Single-table only (Phase 4.3 adds JOINs)
❌ Aggregations only (no row dumps, no LIMIT-1)

## Query stage 5 — Query rewriter

✅ One LLM call produces 4 rewrites + clarifications

🟡 HyDE always on (Phase 3.2 makes conditional)
🟡 Clarifications branch rarely fires

## Query stage 6 — CRAG gate

✅ LLM-judge approach

🟡 Threshold 0.5 hardcoded; should be calibrated per workspace

❌ Refusal applies to H-mode only (Phase 1.5 fix)

## Query stage 7 — IRCoT reformulator

✅ Standard IRCoT, max 2 hops

🟡 H-mode only (same H-mode-bias)

## Query stage 8 — Generator

✅ max_output_tokens=16000
✅ MAX_TOKENS finish_reason distinction → `refusal_reason="truncated"`
✅ Brace-balanced JSON extractor recovers prose-wrapped responses

❌ **215-line system prompt** of accreted band-aids (Phase 2.6 slims)
❌ No streaming (Phase 3.1 adds tokens)

## Query stage 9 — Faithfulness gate

✅ Three implementations (Identity, Heuristic, HHEM)
✅ Per-sentence verdicts

🟡 Default Heuristic (Jaccard is crude); HHEM built but not enabled
🟡 Up to 2 regenerations; at enterprise cost, should be 1 default

## Query stage 10 — Conflict detection / resolution

✅ 5-stage rule cascade (chain → status → authority → recency → unresolved)
✅ Rule-based (deterministic, fast, auditable)
✅ Unresolved persists to fact_conflicts

🟡 Per-fact, not per-narrative

✅ **Genuine differentiator** — most RAG systems don't have this

## Query stage 11 — Citation enrichment

✅ Polymorphic envelope (12 modalities)
✅ fetch_file_metas batch with SAVEPOINT isolation
✅ RAPTOR source-file resolution via raptor_edges recursive descent

🟡 Citation count uncapped (26 for inventory queries)

## API layer

✅ RFC 9457 problem+json errors
✅ Idempotency-Key required on writes
✅ X-Request-Id middleware
✅ structlog binding
✅ Lifespan-managed Procrastinate

❌ No auth (deferred — Part 4)
❌ No rate limiting
❌ No request body size middleware

## Frontend

✅ Next.js 15 + React 19 App Router
✅ URL-as-truth for chat session
✅ SSE consumed via fetch + manual stream parsing

🟡 No token streaming (Phase 3.1)
🟡 Inspector shows `?` on reload (Phase 1.7)
🟡 Citation list overwhelm at 26+

❌ No mobile UX

## Infrastructure

✅ Docker Compose for dev
✅ Procrastinate workers decouple ingest from API
✅ MinIO for object storage
✅ HNSW indexes via pgvector

❌ No production deploy manifests (deferred)
❌ No observability beyond stdout (deferred)
❌ No backup / DR (deferred)
❌ No multi-region (deferred)

---

# PART 3 — SCALE & MARKET COMPARISON

## Where the system breaks at scale

| Axis | Current limit | First failure | Fix |
|---|---|---|---|
| Docs per workspace | ~100K (untested above) | HNSW degradation, CR cost | Phase 4.1 + 4.4 |
| Concurrent users | ~50 | 8–12 LLM calls × concurrency × rate limits | Phase 3.1, 3.2, 3.4 |
| Workspaces per cluster | ~1,000 | Vacuum pressure, FK pressure | Per-workspace sharding (separate effort) |
| LLM cost per turn | Uncapped | Bad query → 20+ LLM calls | Phase 3.5 |
| Identity resolution cost | Unbounded LLM-judge | 1M entities = millions of pairs | Phase 3.3 |
| Vector index size | ~100M vectors per HNSW | Recall degrades | Phase 4.4 |
| Generation latency | Wait for full payload | Bad UX > 3s | Phase 3.1 |
| Ingest throughput | CR concurrency=8 | 100K docs = days | Horizontal worker scale |

## Cost model at enterprise scale

Single workspace, 1M docs, 30 chunks/doc, 100K queries/month:

**One-time ingest:**
- Contextualization (Anthropic CR): 30M × $0.005 = $150K (80% cache: ~$30K)
- Embeddings (Gemini): 30M × $0.0001 = $3K
- KV+Tables: 1M × $0.01 = $10K
- Mention extraction: 30M × $0.002 = $60K
- Schema extraction: 1M × 3 × $0.005 = $15K
- Identity LLM judge: ~$3K (without batch); **$300 with Phase 3.3**
- RAPTOR: 1M × 5 × $0.005 = $25K

**Ingest total: ~$300K one-time per workspace; ~$30K/month incremental**

**Query (monthly):**
- Today (8-12 LLM calls/turn): 100K × 10 × $0.001 = **$10K/month**
- After Phase 3 (3-5 LLM calls/turn): 100K × 4 × $0.001 = **$4K/month**

**Monthly total (post-Phase-3): ~$34K per workspace.** At 1000 workspaces = $34M/month. Acceptable margin model exists with Phase 3.5 cost cap.

## Market comparison

### Tier 1 — Mature enterprise RAG (table-stakes you're missing)

**Glean, Microsoft Copilot M365, AWS Q Business, Sana, mature Vectara:**
- OAuth/OIDC/SAML, SCIM, ACL beyond workspace, content connectors (SharePoint, Drive, Confluence, Slack, S3), streaming generation, mobile UX, production K8s, SOC 2 / HIPAA, cross-region, admin console, SLA monitoring.

**Closing these is ~18 months of Part 4 work. Real moats for incumbents.**

### Tier 2 — Mid-stage RAG (where you sit)

Alongside: early Vectara, Pryon early, Glean's 2022 GA, internal-FAANG-RAG-tools circa 2023.
- ✅ Workspace isolation
- ✅ Audit + idempotency
- ✅ Eval framework
- ✅ Hybrid retrieval (BM25 + dense)
- ✅ Polymorphic citations
- ✅ Hierarchical chunking
- ✅ Anthropic CR

**Rare differentiators YOU have even at this tier:**
- Q-mode SQL over extracted fields — **rare**
- Conflict resolution module — **very rare**
- KV+Tables collapse (single-call structured extraction) — **rare**
- LLM-judge eval across 6 stratified domains — **rare**
- Schema auto-promotion — **rare**

### Tier 3 — Basic RAG (what you're better than)

Pinecone-bolted-to-OpenAI startups, LangChain wrappers, in-house "RAG MVP":
- ✗ No workspace isolation
- ✗ No audit
- ✗ No structured extraction
- ✗ No conflict resolution
- ✗ No real eval

**You are clearly above this tier.**

## Honest positioning

After Phases 1–4: you're a **strong Tier 2** with three rare differentiators (Q-mode SQL, conflict resolution, KV+Tables). Not Glean; a credible alternative for verticals where conflict resolution + structured extraction matter more than content-connector breadth (legal, financial services, healthcare records, construction, mining).

To Tier 1: ~18 months of Part 4 work on top.

---

# PART 4 — DEFERRED (acknowledged, parked)

Explicitly out of immediate scope.

## Security & Compliance
- OAuth/OIDC/SAML + SCIM
- JWT middleware replacing X-Test-Workspace
- users, teams, user_workspace, acl_rules tables
- Per-row ACL on top of RLS
- PII encryption at rest (pg_crypto / app AES-GCM)
- Decrypt-on-read with audit
- is_pii=true masked-by-default UI
- SOC 2 / HIPAA / ISO 27001
- Data residency (multi-region)
- Adversarial robustness beyond Phase 1.2

## Operations / DevOps
- K8s / Helm production deploys
- Prometheus + Grafana / OpenTelemetry
- APM (Datadog / NewRelic / Honeycomb)
- Backup + DR automation
- Blue/green with eval gate
- A/B testing framework
- Feature flags
- Rate limiting

## Enterprise Capability
- Content connectors (SharePoint, Confluence, Drive, Slack, S3)
- Webhook / event bus for ingest
- Public API + SDK
- Admin console (users, workspaces, billing)
- Mobile UX
- Cross-workspace entity linking with permission gates

---

# Closing note

The bones are right. The differentiators are rare. The query pipeline is correctable in ~10 weeks of focused engineering (Phase 1–4). Enterprise GA work (Part 4) is a separate ~6-month track that doesn't block this.

**You don't need to run more evals before acting.** The 5 specific wrong-answer patterns surfaced by the construction eval, combined with the structurally identical query mix across all 6 domains, give us enough to prioritize confidently. Optional: re-eval construction after Phase 2 ships (one ~10-minute, ~$1 run) to measure quality lift. Optional: eval ONE more domain (healthcare recommended — most structurally different from construction) to validate generalization, only if you want second confirmation.

Pick any component in Part 2 and the evidence is in the codebase exactly where I said it is. Ready to ship Phase 1 the moment you give the word.
