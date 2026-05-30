# Engineering audit — ingestion + query pipelines (top-down)

**Method:** read the actual code (workers/tasks.py, the query/ modules,
extraction/, chunking/), verified every load-bearing claim against
file:line. Not vibes — every ❌ below is a specific line you can open.

---

## The one-paragraph verdict

**The skeleton is right; the foundation is not grossly mis-engineered.**
The intended architecture — a lifecycle-driven ingestion DAG, hybrid
retrieval + rerank + relevance-gate + grounded generation + faithfulness
check, with schema that emerges from data — is how good RAG systems are
actually built. But the codebase has accumulated a layer of **half-built
abstractions, disabled experiments, and dead code that make the system
*present* far more capability than it *delivers*.** The biggest danger here
is not a wrong foundation — it's over-claim: 13 "retrieval modes" that are
really 1, conflict-detection that only handles half the cases, schema that
emerges but never converges, and a corpus-level retriever that's switched
off. Plus 3 genuine architectural ordering errors. Fixing this is
consolidation and completion, not a rewrite.

Are we aligned? **In intent and skeleton, yes. In several load-bearing
pieces, no — and they're concentrated, nameable, and fixable.**

---

## The intended architecture (is the shape right?)

**Ingestion (write path):** `parse → chunk → contextualize → embed →
RAPTOR → mentions → KV+tables(classify+extract) → schema-entities →
identity-resolution → graph`, driven by a `files.lifecycle_state` machine
where each stage no-ops if out of order and defers the next stage in a
*separate* transaction.

→ **This shape is correct and, in fact, well-implemented.** The
lifecycle-state machine with separate-tx chaining gives idempotency +
resumability for free (`tasks.py:326-332` pattern, repeated cleanly at
every stage). This is the strongest part of the codebase. Credit where due.

**Query (read path):** `resolve-followups → classify-intent → plan-mode →
retrieve(6 channels)+RRF → rerank → CRAG-gate → conflict-resolution →
generate → faithfulness-gate → persist`.

→ **The shape is also correct** — it's a textbook hybrid-RAG pipeline. The
problem is what's wired inside the boxes, not the boxes.

So: the architecture diagram is right. The implementation under several
boxes is where it goes wrong.

---

## INGESTION — what's correct vs mis-engineered

### ✅ Sound
- **Lifecycle DAG + separate-tx chaining** — idempotent, resumable, the
  backbone is genuinely good.
- **Parse** (`tasks.py:174`) — Docling primary + Gemini-OCR quality
  escalation. Right design.
- **Embed** (`tasks.py:805`) — clean batched embedding, mock fallback for CI.
- **Per-doc RAPTOR** (`tasks.py:944`) — correct clustering+summary tree,
  atomic write, discriminated edge FK.
- **Identity resolution 4-stage design** (`tasks.py:2488`) — deterministic →
  embedding-NN → LLM-judge → new. The *design* is right (the thresholds
  aren't — see below).
- **Explicit doc-chain detection** (`tasks.py:2747`) — deterministic
  frontmatter-driven chaining is correct and fast when metadata is present.

### ❌ Grossly mis-engineered (architectural errors)

**1. Chunking runs BEFORE classification, so the doc-type-aware chunkers are
dead for every PDF/DOCX.** (`tasks.py:519-552`, comment at `:544-546`)
The code reads `inferred_doc_type` at chunk time, but classification doesn't
happen until stage 7 (`extract_kv_tables`), so it's **NULL**. The code admits
it: *"At chunk time inferred_doc_type is usually NULL (the LLM classifies
later), so MIME-based routing carries us until then."* The clause-aware /
row-aware / message-aware chunkers (`chunking/doc_type_router.py`) therefore
only fire for `.xlsx`/`.eml` (where MIME implies type). **A PDF contract or
PDF bank statement gets the generic 2048/512/128 recursive splitter** — the
wrong retrieval granularity, baked in at the root, inherited by every
downstream stage. And chunking is **never re-run** after classification.
This is a pipeline DAG ordering bug, and it's the single most consequential
ingestion error because it poisons retrieval quality for the document types
that most need structured chunking.

**2. Field-name convergence was never built — only exact-string matching.**
(`promotion.py:44-50, 64-112`, docstring `:9-10`)
The system is *supposed* to converge emergent field names across documents.
The deterministic merger clusters by **exact normalized string only**, so
`total_cost_premium` and `total_cost_inr` never merge. The docstring openly
defers the real fix: *"Phase 6 will add embedding-based blocking + LLM-judge
for borderlines"* — **Phase 6 was never built.** Compounded by a promotion
gate requiring a field appear in ≥80% of docs of a type (`promotion.py:117`),
so rare-but-real fields never canonicalize and the LLM keeps re-inventing
them. This is the mechanical cause of **577 distinct field names across 46
documents**, and it makes cross-document aggregation structurally impossible.
The "schema emerges *and converges*" promise is half-implemented: it emerges,
it doesn't converge.

**3. Corpus-scope RAPTOR is implemented but never triggered during
ingestion.** (`tasks.py:3688` impl; only caller is `POST
/corpus/raptor/rebuild`)
Cross-document tree retrieval (the thing that answers "summarize everything
about X across the corpus") silently **does not exist** unless an operator
manually hits an endpoint — and that endpoint is a full O(corpus) teardown +
rebuild with no incremental path. So G-mode / corpus-summary queries run on
an empty corpus tree in normal operation.

### 🟡 Smells (fragile / won't scale, not architecturally wrong)
- **Contextualization resends the entire uncapped document on every chunk**,
  and the default Gemini path has **no prompt caching** (Anthropic path does,
  `contextualization/__init__.py:147`). Cost is O(chunks × doc_size) per
  doc — the dominant cost driver, blows input quota on large docs.
- **Per-chunk LLM calls** for mentions (`tasks.py:1337`) and triples
  (`:3205`) — linear in total chunks, won't reach 100k docs.
- **Identity resolution is top-1-NN only** (`tasks.py:2605`, `limit=1`) with
  **hardcoded 0.92/0.85 thresholds** (`identity/resolve.py:16-17`) and runs
  per-mention inside ingestion. Top-1 misses the true match when it's the 2nd
  neighbor → duplicate entities. And on an embedder hiccup, **every mention in
  the file resolves as a brand-new entity** (`:2550-2552`) — silent graph
  pollution.
- **detect_doc_chain does an O(N²) sibling scan** (one `raw_pages` query per
  sibling, `LIMIT 200` window, `:2976-2991`) — breaks well before 100k docs,
  and a true predecessor older than the 200 most-recent files is invisible.
- **Silent data-loss on transient failures**: parse failure parks a doc with
  no retry (`:270-284`); per-chunk extraction failures are swallowed
  (`:1344-1348`).

---

## QUERY — what's correct vs mis-engineered

### ✅ Sound
- **The overall pipeline order** is a correct hybrid-RAG flow.
- **RRF fusion** (`rrf.py`) — standard, correct, k=60.
- **Q-mode SQL** — whitelist-catalog compiler, injection-proof
  (`q_planner/compiler.py`), refusal envelope. Sound (just single-table).
- **Pre-resolution adversarial check** (`orchestrator.py:541`) — correctly
  runs on the *raw* query before context-resolution can launder it (this
  fixed a real pen-test finding).
- **Persist-turn on a fresh connection** (`:1773`) — survives txn-abort, the
  right call after the silent-turn-loss bug.
- **Conflict resolution 5-rule cascade** (`conflict_detector.py`) — sound
  *design* (chain→status→authority→recency→unresolved), when it fires.

### ❌ Grossly mis-engineered

**1. The "13 retrieval modes" are 1 real retriever + 1 SQL path + 11
cosmetic post-filters — several dead, several redundant, all neutralized by a
blanket fallback.** (`mode_router.py` 1540 lines; `orchestrator.py:867-884`)
Only **H** runs the 6 channels. **Q** ignores `hits` and runs SQL. The other
11 modes *filter/boost/prepend* to the **same H-mode candidate set** — none
run distinct retrieval. On top of that:
- K-mode auto-routing is **commented out** (`planner.py:511-513`).
- `field_name_exact` channel is **disabled** (`channels.py:605-627`) — so
  "6 channels" is really 5.
- E and M are **weaker clones of T** (resolve-a-name-and-boost).
- A is a **clone of C** with a hardcoded rarity threshold.
- And the orchestrator **restores pre-mode hits whenever a specialized mode
  filters to zero** (`:867-884`) — so for a large fraction of queries the
  "mode" contributes nothing but a metadata tag.
This is ~1500 lines implementing an abstraction that mostly doesn't do what
its name claims. It's simultaneously over-engineered (too many modes) and
mis-engineered (they don't retrieve). The eval-driven reverts
(`channels.py:605`, `planner.py:511`) show the team knows — it's just never
been consolidated.

**2. Conflict detection structurally cannot see independent-document
disagreements.** (`conflict_resolution.py:336-340, 412-414`)
The hard gate `if meta is None or not meta.chain_id: continue` means the
entity-key for conflict grouping **is the chain_id itself** — so only
doc-chain siblings (MSA↔Amendment, Rev A↔Rev B) are ever comparable. **Two
independent POs with different steel rates, two RFIs, two invoices that
disagree are never flagged.** The system's headline differentiator ("surfaces
conflicts, doesn't pick arbitrarily") only covers the *chained* half. The
non-chained half is pushed entirely into a **prose instruction in the
generator prompt** (`generate.py:257-287`) — no structural detection, no
`fact_conflicts` row, no UI surfacing. For a knowledge base whose value prop
includes contradiction-surfacing, this is the biggest correctness gap.

**3. The generator prompt is doing retrieval's and conflict-detection's job.**
(`generate.py:163-328`, ~165 lines)
Entire prompt sections compensate for upstream gaps: "Inventory FIRST"
(`:183` — a recall problem patched in the prompt), hardcoded **corpus-
specific** substitution traps ("Survey No. 184/2A is a site location",
"don't drop the firm Deshpande Architects", `:191-214`), and the
independent-vs-chained conflict logic the detector can't do (`:257-287`).
When the prompt encodes facts about *this specific corpus*, the system has
stopped being domain-agnostic and is memorizing the eval. That's a smell that
the layers below aren't doing their job.

### 🟡 Smells
- **6 retrieval channels share one DB connection**, correctness held together
  by 4 nested layers of interleaved SAVEPOINTs (`orchestrator.py:740-798`).
  The code comment itself says the right fix is a per-channel connection pool
  (`:751-754`). Latent source of the "citation says 'document' not filename"
  class of bugs.
- **IRCoT (the only iterative element) is effectively dead** — its
  reformulator reads `GEMINI_API_KEY`/`GOOGLE_API_KEY` (`ircot.py:206`) while
  the whole system uses **`KB_GEMINI_API_KEY`** (verified: never mapped). So
  in any normal deploy IRCoT degrades to a no-op stub, wrapped in a bare
  `except: pass` (`orchestrator.py:960-963`) that hides it. The "escalate
  instead of refuse" path doesn't run.
- **Default faithfulness gate is Jaccard token-overlap** (`faithfulness.py:
  221-278, 390`), which its own docstring concedes is "NOT a real
  hallucination detector" — and when it *does* refuse, CRAG≥0.7 **overrides
  it and ships the answer anyway** (`orchestrator.py:1159-1188`). Two weak
  gates wired to cancel each other.
- **No per-turn LLM-call cap / cost ceiling** anywhere — a single H-mode +
  low-CRAG + 2-regen turn can fan out to a dozen-plus model calls.
- **CRAG is decorative for 12 of 13 modes** — computed (costs a call) but
  `force_refuse` is H-mode-only (`orchestrator.py:1010-1012`).
- **The pipeline is single-pass** — no query decomposition. (This is a
  ceiling, not a bug; the agentic move is a deliberate future choice.)

---

## The pattern behind all of it

Two forces produced this state, and naming them matters more than any single
bug:

1. **"Wave A" MVP shortcuts that were never completed.** Field convergence
   (Phase 6), corpus-RAPTOR auto-trigger, HHEM faithfulness, conflict for
   non-chained docs, JOINs in Q-mode — all explicitly deferred in docstrings
   and never built. The architecture was *designed* fuller than it was
   *built*, and the gap is invisible from the outside.

2. **Eval-driven reverts that left dead code in place.** K-mode routing,
   `field_name_exact` channel, uniform CRAG — all tried, regressed the eval,
   got commented out but not removed. So the codebase is littered with
   disabled experiments that read as capability.

Net effect: **the system's surface area over-claims relative to what's wired
and working.** That's the honest top-down finding — not "the foundation is
wrong," but "the foundation is sound and half the house above it is a
facade."

---

## What I'd actually do (priority order)

These are *consolidation + completion*, not a rewrite:

1. **Move classification before chunking (or re-chunk after).** Highest-
   leverage ingestion fix — unlocks the doc-type-aware chunkers that already
   exist. Either run a fast classifier at parse time, or add a re-chunk step
   after stage 7.
2. **Build the field-name converger that was deferred** (embedding-blocking +
   LLM-judge merge), and drop the 80%-prevalence promotion gate. Kills the
   577-names problem at the source; makes aggregation possible.
3. **Un-gate conflict detection from chain_id** — detect on resolved-entity +
   predicate for any two docs, then classify (chain-supersession vs
   independent-transaction vs contradiction). Delivers the headline feature.
4. **Collapse the 13 modes to ~4 honest ones** (hybrid-H, SQL-Q, chain-K,
   graph-T) and delete the cosmetic filters + the zero-result fallback. Less
   code, fewer wrong routes, no facade.
5. **Auto-trigger corpus RAPTOR** (incremental) so cross-doc retrieval exists.
6. **Fix the cheap real bugs:** IRCoT env var (`ircot.py:206`), per-turn cost
   cap, per-channel connection pool, remove the `/tmp` parse-error dump.

The eval is now a trustworthy ruler (93% grounded), so each of these can
finally be measured honestly. Do them in this order; re-eval after each.
