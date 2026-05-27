# Construction domain — demo-corpus eval

Workspace: `c0000000-0000-0000-0000-000000000001`
Started: 2026-05-27
Domain manifest: `demo-corpus/domains/construction/manifest.yaml` (46 docs, 50 queries)

---

## Batch 1 — Drawings + BoQ (10 docs)

Uploaded in manifest order: drawing-001 A/B/C, structural-calc-001,
drawing-002-apollo A/B, drawing-003-mep, drawing-004-structural,
drawing-005-metro, boq-001.

**Wall-clock:** ~21 min (worker concurrency=1, ~50s/doc through Gemini AFC).

### What's working ✅

| Layer | Result |
|---|---|
| Upload + idempotency | 10/10 succeeded, no duplicates, no upload errors |
| L0 parsing | All 10 parsed via `textparser` (2 pages avg for the markdown docs) |
| L1 chunking (hierarchical) | Auto-merging retriever structure correct — node_levels 0/1/2 present on revA |
| L2 mentions | Working (n_mentions in details for each file > 0) |
| L3 field extraction | **31 fields on revA, all values correct** including chain_id, status (enum), wall_position, all site details, frontmatter perfectly captured |
| L4 entity extraction | KV+Tables collapse working — 47 entities on revA across 7 schemas; tables (sheets/zones/doors/finishes/distribution/approvals) decomposed cleanly |
| Doctype detection | **10/10 match manifest exactly** (architectural / structural_calc / mep / structural / bill_of_quantities) |

### Bugs found 🐛

#### Bug A — `doc_status` not propagated from L3 enum field [HIGH] — ✅ FIXED

**Status:** Fixed in `src/kb/workers/tasks.py` (`extract_kv_tables_file_impl`).
Backfill script in `scripts/backfill_doc_status.py` applies to already-ingested
docs without re-extraction. Verified: 3/3 affected batch-1 docs now show
correct `doc_status=superseded`.

**Repro (original):** All 3 chain-predecessor docs (revA, revB, apolloA) had L3
`status` field correctly extracted with value `superseded` and
`value_type=enum`, but the file row's `doc_status` column stayed at
default `live`.

| Doc | L3 `status` (proposed_field) | File `doc_status` |
|---|---|---|
| drawing-001-revA | superseded | **live** ❌ |
| drawing-001-revB | superseded | **live** ❌ |
| drawing-001-revC | live | live ✓ |
| drawing-002-apolloA | superseded | **live** ❌ |
| drawing-002-apolloB | live | live ✓ |

**Impact:** chain-aware queries q012 / q017 / q022 cannot answer
"Is Rev A still authoritative?" — the system thinks all revisions are
equally live. Knowledge Map shows no "superseded" badge.

**Fix candidate:** add an L3 → file column projection for known
canonical fields (`status` enum → `files.doc_status`, similar to how
`effective_date` is treated).

---

#### Bug B — Chain detection ignores explicit `chain_id` + `parent_doc` frontmatter [CRITICAL] — ✅ FIXED

**Status:** Fixed via two edits in `src/kb/workers/tasks.py`:
- `detect_doc_chain_file` defer **moved** from `parse_file_impl` →
  `extract_kv_tables_file_impl` (so chain detection sees L3 fields).
- New explicit-chain branch at the top of `detect_doc_chain_file_impl`:
  if this file's proposed_fields contain a `chain_id`, build the chain
  deterministically (`chain_key=explicit:<id>`, chain_type from doctype,
  parent_doc_id resolved by matching siblings' `doc_id` field).
- Heuristic detect_chain() still fires for docs without an explicit
  chain_id.

One-shot retro fix-up: `scripts/rerun_chain_detection.py` wipes chains
in a workspace and re-runs detection per ready file. Applied to
construction batch 1: 4 fuzzy chains → 2 ground-truth chains:
- `explicit:chain_datacentre_drawing_revisions` (3 members, was 2 with revA dropped)
- `explicit:chain_apollo_maternity_drawings` (2 members, roles fixed)
- False positive (structural-calc + drawing-004) eliminated.

**Repro (original):** L3 extraction captures `chain_id` and `parent_doc` from
frontmatter perfectly for revA/B/C and apolloA/B. But chain detection
was using fuzzy title-similarity instead of these explicit fields.

Detected chains (`GET /chains` on construction workspace):

| Real chain (manifest) | Detected | Issue |
|---|---|---|
| drawing-001 (A→B→C, 3 members) | revB + revC only (2 members) | **revA missing from chain** |
| drawing-002-apollo (A→B, 2 members) | A + B (2 members) | Both labeled `role=original` (B should be `amendment`) |
| (none — they are unrelated) | structural-calc + drawing-004 grouped | **False positive** |

Chain types are all `contract_chain` regardless of doctype. Chain titles
are gibberish (raw frontmatter text chopped at ~200 chars).

**Impact:** chain-aware queries q012, q013, q016, q017, q018, q019, q020
all break. "Walk the architectural drawing revision chain" returns
incomplete chain. The whole drawing-chain stressor pattern is gutted.

**Fix candidate:** in chain detection, if a doc has L3 field `chain_id`
set, use that as the canonical `chain_key` (skip fuzzy title path
entirely). If a doc has L3 field `parent_doc`, map it to `parent_doc_id`
via the doc_id field on other docs in the same workspace.

---

#### Bug C — Authority not assessed when only `inferred_doc_type` is set [MEDIUM]

**Repro:** All 10 docs show `source_authority=0.5` with reason
`"authority not assessed (no doc-type classification)"`. `inferred_doc_type`
is populated correctly, but `doc_type` (the approved/promoted column) is
null, so the authority assessor skips.

**Impact:** chain-aware "newest wins" / "highest authority wins" tie-breakers
don't fire — all docs in a chain compare equal at 0.5. Conflict resolution
queries (q021, q022) lose a key signal.

**Fix candidate (either-or):**
- Option 1: auto-promote `inferred_doc_type` → `doc_type` when detection
  confidence is high (e.g. ≥0.85), so authority assessment runs in the
  zero-touch path
- Option 2: have the authority assessor read `inferred_doc_type` if
  `doc_type` is null

Option 1 is closer to the original Schema Studio "promote" flow but
needs a confidence gate. Option 2 is the minimal change.

---

#### Bug D — Field-schema divergence across same-doctype docs [HIGH long-term]

**Repro:** Three docs with `inferred_doc_type=architectural_drawing` have
divergent field schemas:

| Field meaning | revA | revB | revC |
|---|---|---|---|
| Drawing number | `drawing_number` | `drawing_number` | `drawing_no` |
| Revision label | `revision` | `revision_id` | `revision` |
| Wall position | `main_load_bearing_wall_position` | (missing) | `wall_position_confirmation` |
| Sheet number | `sheet_number` | `sheet_number` | `sheet` |

**Impact:**
- Query "compare wall position across revisions" (q012, q021, q039) cannot
  find a common field
- Knowledge Map Schema view shows three near-duplicate field rows per
  schema (noise)
- Aggregation queries that try to roll up "wall_position" across docs
  return null for revB/revC

**Same root cause** as the deferred "synonymous-duplicate fields" issue
from finance domain (`opening_balance` vs `opening_balance_usd`).

**Fix candidate:** when extracting fields for an existing doctype, feed
the LLM the existing field schema for that doctype and instruct it to
reuse field names where the meaning matches. Field-name convergence pass
or post-hoc canonical mapping.

---

## Priority for fix-cycle 1 (before batch 2 upload)

1. **Bug A** — small, surgical fix; unblocks 3+ chain queries; high user-trust impact ✅ DONE
2. **Bug B** — medium fix; unblocks 7+ chain queries; biggest single impact ✅ DONE
3. Defer Bug C — needs design call (auto-promote vs read-inferred)
4. Defer Bug D — biggest fix, biggest blast radius; better tackled after
   we see how many domains exhibit it

---

## Final results — full construction domain (50 queries)

**Ingest:** 46/46 docs reached `ready`. 252 frontmatter fields written
across all docs. 3 ground-truth chains formed correctly (drawing-001
A→B→C; apollo A→B; safety initial→investigation+corrective).

**Query suite (50 queries, see `construction_query_results.json`):**

| Verdict | Count | % |
|---|---|---|
| pass | 23 | 46% |
| partial (some citations matched) | 4 | 8% |
| fail-empty | 10 | 20% |
| fail-no-cit-match (answer right, citations didn't match expected) | 8 | 16% |
| fail-should-have-refused | 4 | 8% |
| error (timeout) | 1 | 2% |

**By stratum:**

| Stratum | Pass+partial | Total | % |
|---|---|---|---|
| needle | 7+0 | 12 | 58% |
| chain-aware | 3+2 | 9 | 56% |
| conflict-resolution | 2+1 | 6 | 50% |
| **rare-clause** | **4+1** | **5** | **100%** |
| aggregation | 1+0 | 5 | 20% |
| long-form | 2+0 | 3 | 67% |
| ambiguous | 2+0 | 3 | 67% |
| **negative** | **2+0** | **2** | **100%** |
| **adversarial** | **0** | **4** | **0%** |

**Bugs fixed during this domain:**

| Bug | Description | Status |
|---|---|---|
| A | L3 `status` → file.doc_status propagation | ✅ FIXED + backfill |
| B | Chain detection ignored L3 chain_id | ✅ FIXED + backfill |
| E | `_cosine` TypeError (vocab discovery silently broken) | ✅ FIXED |
| F | Gemini embed_batch >100 limit | ✅ FIXED |
| H | `ensure_sub_entity_type` not idempotent | ✅ FIXED |
| I | `_depth` RecursionError on self-referential schema_entities | ✅ FIXED |
| K | LLM misses YAML frontmatter — added deterministic parser | ✅ FIXED + backfill |

**Bugs deferred (tracked):**

| Bug | Description | Why deferred |
|---|---|---|
| C | Authority not assessed when only `inferred_doc_type` set (all docs at 0.5) | Needs design call (auto-promote vs read-inferred) |
| D | Field-schema divergence across same-doctype docs (`drawing_no` vs `drawing_number`) | Bigger fix — needs cross-domain coverage to scope |
| G | `graph_edges` FK race in `build_graph_file` — non-blocking | Will be addressed during Phase 2 G-mode build |
| L | False-positive `contract_chain` chain bundles labour-contract + EPC-contract via fuzzy title | Heuristic threshold needs raising; affects 0 query results |

**Top failure modes to address next:**

1. **Adversarial refusal: 0/4** — the system should refuse PPE-bypass /
   Aadhaar-exfiltration / backdating / Clause-99 false-premise queries
   but returns answers. Needs guard-rail prompts before generator runs.
2. **Aggregation (Q-mode): 1/5** — q032 returns "5 safety incidents"
   (should be 1 LTI), q033 returns "$80.0" total change-order value
   (should be ~INR 1.28 crore), q034 returns "1 sub-contractor" (5+).
   Q-mode SQL gen has fundamental issues against construction data.
3. **fail-empty (10 queries)** — investigate why these return empty
   answers (likely planner picked wrong mode or hit a CRAG refusal).

These should drive Phase 2 priorities along with the G-mode build.

