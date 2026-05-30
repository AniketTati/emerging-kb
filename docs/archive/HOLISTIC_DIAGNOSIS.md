# Holistic diagnosis — what's actually wrong and where to fix it

**Date:** 2026-05-29
**Frame:** Step back from the iteration noise. Look at the whole system as
two halves — the WRITE path (upload → ingestion) and the READ path
(query → answer) — and locate each failure in the half that actually
owns it.

---

## 0. The one number that reframes everything

```
46 documents
996 extracted structured-field rows
577 DISTINCT field names
```

577 distinct field names for 46 documents means the structured-extraction
layer almost never lines up across documents. Two change orders call the
same concept `total_cost_premium` and `total_cost_inr`. Two daily reports
call it `site` and `site_name`. **The structured layer is nearly useless
for any cross-document question because nothing matches.**

This is the root cause hiding behind a third of our failures. We have been
fixing it one merge at a time on the READ side (dedup scripts, Q-mode
filters, rerank). That's backwards. The fix belongs on the WRITE side.

---

## 1. The corpus we actually have

| Property | Value |
|---|---|
| Documents | 46 |
| Doc types | 28 (most are singletons) |
| Largest doc type | architectural_drawing (6), daily_site_report (4) |
| Chunks | 801 |
| RAPTOR nodes | 133 |
| Canonical entities (active) | 673 |
| Doc chains | 4 chains, 10 members |
| Structured field rows | 996 across 577 distinct names |

**Reading of the corpus:**
- It's a *deliberately diverse* test corpus — 28 doc types, mostly one or
  two docs each. This stresses cross-doc reasoning with very little
  redundancy.
- Only 4 chains exist. Yet 9 of 50 eval queries are "chain-aware". The
  chains that matter (contract → change orders → variations; incident
  initial → investigation → corrective) are **under-linked** at ingestion.
- 673 entities, zero role tags. No way to ask "which are sub-contractors".

---

## 2. The failures, sorted by the half that owns them

v19 (best run): 18 correct / 23 partial / 6 wrong / 3 refused = 82% c+p,
36% strict-correct. The 32 non-correct queries decompose like this:

### A. WRITE-PATH failures (ingestion under-structured the data) — ~14 queries

These fail because the data needed was never extracted into a clean,
queryable shape. No amount of read-side cleverness fully fixes them.

| Query | Symptom | Root cause (write path) |
|---|---|---|
| q033 cumulative CO value | wrong | Field fragmentation + corpus gap (only 2 of ~6 COs) |
| q029 change orders >20L | partial | Field fragmentation (cost field varies per doc) |
| q032 incident count + breakdown | partial | Incidents not extracted as countable structured units |
| q034 distinct sub-contractors | wrong | No `entity_role` tag on the 673 entities |
| q006 site location | partial | Address buried in a chunk, never extracted as `site_address` field |
| q007 safety officer | refused | Officer name in a chunk, not a `safety_officer` field |
| q011 PPE clause | partial | Clause reference in prose; EPC contract not chunked clause-aware |
| q004 contract value (final) | partial | Original→CO chain not linked, so "final value" can't be derived |
| q019 variations to value | partial | Variation chain not linked |
| q020 EPC still original? | partial | Same chain-linking gap |
| q036 peak headcount | wrong | Headcount extracted per-doc but not as a clean numeric series |
| q040 Marathi contract | partial | OCR/translation quality at parse stage |
| q009 Noamundi depth | refused | Corpus gap — site doesn't exist (or eval typo) |
| q041 two Mahalaxmis | partial | Identity resolution can't distinguish Infra vs Equipment |

### B. READ-PATH failures (data is fine, the query side mishandles it) — ~12 queries

| Query | Symptom | Root cause (read path) |
|---|---|---|
| q013 drawing chain | partial | Generation drops chain members (Flash enumeration) |
| q014 incident chain | partial | Same |
| q015 final root cause | refused | Generation parse_error under heavy conflict load |
| q016 Apollo chain | partial | Generation enumeration |
| q017 is Rev A authoritative | partial | Generation didn't walk supersession |
| q018 punch-list status | partial | Generation dropped the 38/4 split |
| q022 worker error vs investigation | wrong | Conflict not surfaced (POs/incidents not chained → detector silent) |
| q023 two PO rates | partial | Conflict detector doesn't fire on non-chained docs |
| q025 two RFI fire-stop | wrong | Retrieval miss + conflict not surfaced |
| q028 incidents >1mo investigation | wrong | Q-mode can't do HAVING / per-group date math |
| q037/q038/q039 long-form | partial | Generation gives 80%, judge wants 100% |
| q042/q043 ambiguous | partial | Under-specified query; generation picks one reading |

### C. NEITHER — the measurement is harsh — ~6 queries

The long-form and ambiguous partials (q037-q043) are answers a human would
accept. The judge marks them partial because it demands every named
sub-detail. This is an eval-strictness artifact, not a system defect.

---

## 3. The higher-order conclusion

**We have been over-investing in the read path to compensate for an
under-built write path.**

The read path is genuinely sophisticated: 6 retrieval channels, RRF,
cross-encoder rerank, CRAG gate, IRCoT, conflict resolution, faithfulness
gate. That machinery is doing heroic work to dig answers out of messy
structured data.

But look at the write-path failures above. If ingestion had:
- a **canonical field vocabulary** per doc type (so `total_cost_inr` is
  always `total_cost_inr`), the aggregation failures evaporate;
- **needle facts promoted to fields** (`site_address`, `safety_officer`,
  `contract_value`), the needle failures become one-row lookups;
- **entity role tags**, q034 works;
- **proper chain linking** (contract→CO→variation, incident
  initial→investigation→corrective), the chain and "final value" queries
  resolve deterministically instead of hoping Flash enumerates well.

That's ~14 of the 32 failures addressed at the source, and it makes the
read path's job dramatically easier for the rest.

**This is the classic RAG maturity lesson: extraction quality beats
retrieval cleverness.** A clean, canonical, well-linked knowledge layer is
worth more than another reranker.

---

## 4. What to change — WRITE path (upload → ingestion)

Ordered by leverage. Stages refer to `workers/tasks.py`.

### W1. Canonical field vocabulary at extraction time *(highest leverage)*
**Stage 7 — KV+Tables extraction.**
Today the extractor sees each doc fresh and invents field names. Fix: before
extracting a doc of type T, load the existing canonical `schema_fields` for
type T and pass them as an **authoritative vocabulary** — "use these exact
names when the concept matches; only invent a name for genuinely new
concepts." This makes the Bug-D dedup self-healing instead of a recurring
cleanup chore.
- Effort: ~half day (prompt + hint wiring; partial scaffolding exists at
  commit e29a5b2).
- Fixes at source: q033, q029, q032 and prevents regression on every
  future upload.

### W2. Promote needle facts to first-class fields
**Stage 7 — extraction schema.**
Add a per-doc-type "key facts" extraction that always pulls the obvious
queryable attributes: site address, parties, key personnel (safety
officer, PM, architect), headline values, effective dates. These become
`proposed_fields` rows, so a needle query is a field lookup, not a chunk
hunt.
- Effort: ~1 day (extend extraction prompt with per-doctype field
  templates).
- Fixes at source: q006, q007, q011, q004.

### W3. Entity role classification
**New step between Stage 8 (schema entities) and Stage 9 (identity).**
After entities are resolved, run one LLM pass tagging each ORG/PERSON with
a role drawn from the project ontology (sub_contractor, vendor, client,
consultant, gov_body, bank, utility, person_role). Store `entity_role` on
`canonical_entities`.
- Effort: ~1 day (migration + classification pass + backfill).
- Fixes at source: q034, improves q041.

### W4. Stronger chain detection
**Stage 9 area / a dedicated chain-builder.**
Today: 4 chains. Needed: link change orders to their parent contract, link
contract variations into a value chain, link incident
initial→investigation→corrective. Use the structured fields
(`parent_contract`, `change_order_number`, incident IDs) to build these
deterministically rather than relying on doc-similarity.
- Effort: ~1 day.
- Fixes at source: q004, q019, q020; makes q013-q018 deterministic for the
  read path.

### W5. Tighten identity resolution
**Stage 9 — identity resolution.**
Lower the embedding auto-merge threshold (0.92 → ~0.86) and widen the
LLM-judge budget so Mahalaxmi-style splits are caught at insertion. Pair
with a "distinct legal entity" guard so Infra vs Equipment stay separate.
- Effort: ~half day (config + judge prompt).
- Makes W1/W3 durable; improves q041.

### W6. (Corpus, not code) Fill the gaps
- Add the missing change-order docs so q033's "1.28 cr" is real.
- Fix or remove the "Noamundi" eval query (it names a site not in the corpus).
- Effort: ~2 hours of corpus authoring.

**Upload flow itself is fine** — file → MinIO → file row → job chain is
sound. The problem is purely what the job chain *produces*, addressed by
W1–W5.

---

## 5. What to change — READ path (query → answer)

These matter most for the queries that AREN'T fixable upstream, and for
when the corpus grows.

### R1. Broaden conflict detection beyond doc-chains *(highest read-path leverage)*
`conflict_resolution.py` only builds fact candidates for files that are in
a `doc_chain`. So two POs or two RFIs (not chained) never trigger conflict
surfacing — the generator sees both and picks one blindly.
Fix: build candidates for any two docs asserting the same predicate about
the same entity, then classify: chain-supersession vs independent-
transaction vs genuine-contradiction.
- Fixes: q022, q023, q025.

### R2. Generation: stronger synthesis for chain + long-form
The chain partials (q013-q020) and long-form partials (q037-q039) are Flash
giving 80%-complete answers. Two options:
- (a) Route chain-aware + long-form intents to **Gemini 2.5 Pro** (better
  multi-item synthesis). ~5× cost on ~20 of 50 query types.
- (b) Use **structured generation** — ask for an explicit JSON list of
  chain members / sub-points, then render. Keeps Flash, forces completeness.
Recommendation: try (b) first (cheaper, no model change); fall back to (a)
if (b) plateaus.

### R3. Q-mode: HAVING / per-group computation
q028 ("incidents with >1 month investigation") needs per-incident date
math then a threshold filter. Extend the Q-mode grammar with a HAVING
clause over computed expressions.
- Fixes: q028.

### R4. Rerank: stabilize
BGE+MPS works but regressed 4 queries (q007, q010, q022, q025) by pushing
previously-good chunks out. Either tune (rerank only when initial top-3
confidence is low) or move to Cohere Rerank (more stable, cloud, cheap).
- Net: removes the regressions BGE introduced.

### R5. Q-mode result rendering (already largely done)
Snippet now carries query context + currency + column meanings. Keep.

---

## 6. Anything unusual I'd flag

1. **577 field names / 46 docs** — the structured layer is fragmented to
   near-uselessness. This is the headline anomaly. Everything in §4 W1
   flows from it.
2. **4 chains for a corpus whose eval is 20% chain-aware** — chain
   detection is under-powered relative to how much the questions lean on it.
3. **Conflict detection gated on chains** — a design choice that silently
   disables conflict surfacing for the most common conflict shape
   (independent transactions like POs/RFIs). Surprising and load-bearing.
4. **The eval judge is strict to a fault** — ~6 "partial" verdicts are
   production-acceptable answers. The 36%-correct headline understates real
   quality; 82% correct+partial is the truer number.
5. **We did all our data fixes as post-hoc cleanup scripts, not pipeline
   changes** — so they don't survive re-ingestion. W1–W5 make them durable.

---

## 7. The path to target, honestly

| Phase | Work | Lift (strict-correct) | Durable? |
|---|---|---|---|
| Today (v19) | — | 18/50 (36%), 82% c+p | data fixes are NOT durable |
| WRITE W1-W2 | canonical fields + needle-as-fields | +4-6 | yes |
| WRITE W3-W4 | entity roles + chain linking | +3-4 | yes |
| READ R1 | broaden conflict detection | +2-3 | yes |
| READ R2 | structured/Pro synthesis | +3-5 | yes |
| READ R3 | Q-mode HAVING | +1 | yes |
| WRITE W6 | corpus gap fill | +1-2 | n/a |

Realistic landing after WRITE W1-W4 + READ R1-R2: **~30-34 correct
(60-68%), ~90%+ c+p — and durable across re-ingestion.**

The sequence matters: **do the WRITE-path work first.** It's the larger
lever, it's durable, and it shrinks the read-path problem before you spend
on Pro or Cohere.
