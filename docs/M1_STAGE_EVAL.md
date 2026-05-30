# M1 — Per-stage measurement harness

> Roadmap: `docs/FIX_CHECKLIST.md` §8 (M1) · Decision: `docs/DECISIONS.md` D9.
> Status: **delivered.** This is the Phase-0 enabler — every later fix is
> measured against it so the change can be attributed to the stage it targets.

## Why it exists

The original eval scored only **end-to-end** (right/wrong answer). It could
not say *where* accuracy was lost — did retrieval miss the doc? did rerank
drop it? did generation fail to use it? Every fix was made blind. Now that the
eval carries **verified `expected_citations`** (the rebuild), we can score each
pipeline stage separately (SOTA RAG eval, D9): score **retrieval** and
**generation** independently against grounded ground truth.

## What it measures

For each verified question (`demo-corpus/domains/<domain>/queries.yaml`), the
harness drives one `orchestrator.chat()` and captures the pipeline's internal
**fused candidate set** (pre-rerank) and **reranked top-10**, plus the answer's
actual citations. The `expected_citations` slugs are resolved to workspace
`file_id`s, then per question:

| Stage | Metric | Definition |
|---|---|---|
| Retrieval | **recall@k** (k=10, 30) | gold file present in the fused candidates within top-k |
| Retrieval | **MRR** | 1 / rank of the earliest gold file in the fused set |
| Rerank | **retention** | P(gold in reranked top-10 \| gold was in the fused set) — isolates the reranker |
| Generation | **citation accuracy** | the answer actually cited a gold file |
| Generation | **faithfulness** | HHEM/verdict grounding (reused) |
| Refusal | **refusal accuracy** | expected-refusal questions that refused |

Each question is then **localised** to the FIRST stage that lost its gold doc:
`ok` · `lost_retrieval` · `lost_rerank` · `lost_generation` ·
`refused_correct` / `refused_wrong` · `unscorable`. Reported per **stratum**
AND per **domain**.

A row is *scorable* for retrieval/rerank/citation only when it is verified,
non-refusal, has gold citations, and those citations resolved in the
workspace (so a gold doc that simply isn't ingested can't be charged as a
retrieval miss).

## Where it lives

- `src/kb/eval/scorer.py` — pure stage-metric functions + `StageObservation`,
  `StageScore`, `StageReport`, `score_stages`, `render_stage_summary`,
  `write_stage_csv`. No orchestrator/DB imports (stays import-light).
- `src/kb/eval/stage_runner.py` — in-process driver: verified-query loader,
  slug→file_id resolver, the `chat()` event-sink capture, subset selection,
  concurrency.
- `src/kb/query/orchestrator.py` — the only pipeline touch: `_retrieve_and_rerank`
  emits `fused_candidates` + `reranked_candidates` (ordered file_ids) **only**
  when an event sink is threaded in (default no-op — production unchanged; the
  plain POST `/chat` already routes every emit to `_noop_sink`).
- `scripts/run_stage_eval.py` — CLI.
- `tests/test_m1_stage_scorer.py` — 18 unit tests over the pure metrics.

## How to run

```bash
source scripts/dev_env.sh            # DB on localhost:5432, keys from .env

# Full domain (phase-gate). construction's workspace is known.
uv run python scripts/run_stage_eval.py --domain construction --concurrency 3

# Fast dev loop — ~2 questions per stratum (~18 Q, ~90s).
uv run python scripts/run_stage_eval.py --domain construction --stratified 2 --concurrency 3

# Exact subset.
uv run python scripts/run_stage_eval.py --domain construction --ids construction-q006,construction-q007
```

Outputs a per-question CSV + summary JSON under `eval_out/` (gitignored;
snapshot good baselines into `docs/eval_baselines/`).

## Testing cadence (how much to run per fix)

Don't run all 50 after every small fix — it's slow and burns the Cohere trial
budget (~10 rerank/min, ~1000/mo). Scale the test to the change:

- **Small fix → affected queries only.** Run M1 with `--ids <the questions the
  fix targets>` (or `--stratified` for one stratum). Fast, cheap.
- **Spillover check (judgment).** If the change touches **shared / cross-cutting
  code** that could move *other* queries — e.g. the orchestrator refusal gate
  (all modes), the reranker, the generator prompt — also run the
  plausibly-affected slice, not just the target. (Heuristic: mode-local change →
  that mode's stratum; pipeline-wide change → broader slice.)
- **Full 50 → after a substantial fix or a batch of small ones** (milestone):
  confirm aggregate movement + catch regressions, then snapshot a new baseline
  into `docs/eval_baselines/`.

Worked example: **C1** (mode-Q only) → the aggregation `--ids` subset suffices.
**C2** (refusal logic across *all* modes → real spillover risk) → full 50 was
justified.

## Dev-loop policy (how we iterate without re-ingesting constantly)

- **Query-path tasks** (most of the roadmap) need **no re-ingestion** — change
  query code, re-run the eval.
- **Write-path tasks** (I1, I2, I4, …): the **"done-when" is proven by a
  targeted mechanism test** (seconds, no LLM); the eval is a no-regression
  check. Re-ingest a small `construction-dev` workspace when a real re-ingest
  is needed; full 46-doc re-ingest + full-50 eval only at **phase gates**.
- A small subset has fewer distractors → **optimistic recall**; absolute
  numbers are anchored to the full corpus at gates. The subset measures
  **mechanism + deltas**.

## Reranker note (affects every eval run)

Reranking was diagnosed as the dominant per-query cost: the local
`bge-reranker-v2-m3` cross-encoder ran **synchronously, blocking the asyncio
event loop** (and a first-call model-load stampede stalled high concurrency).
Switched to **Cohere `rerank-v3.5`** (`KB_RERANKER=cohere`) — hosted, async,
SOTA multilingual (the corpus has Marathi), ~200ms. `CohereReranker` was
hardened to **retry transient rate-limits with backoff and log loudly on
fallback**, so a trial-key 429 can never silently degrade rerank to a no-op and
corrupt the rerank-stage metrics. Trial keys cap at ~10 rerank/min and ~1000/mo
(a card lifts this); the backoff self-paces the eval under that cap.

Per-query latency dropped ~25s → ~13s; the full 50-question construction eval
runs in **~4.6 min** with 0 fallbacks.

## Phase-0 baseline — construction (50 Q, Cohere rerank-v3.5)

Snapshot: `docs/eval_baselines/construction_phase0.{summary.json,csv}`.

```
OVERALL  n=50 scor=36  r@10=0.93 r@30=0.97 mrr=0.82 rerank_ret=1.00 cite=0.75 faith=0.66 refuse=0.57
         localisation: ok=27 lost_retrieval=1 lost_rerank=0 lost_generation=8
                       refused✓=4 refused✗=3 unscorable=7
```

| stratum | n | scor | r@10 | r@30 | rerank_ret | cite | faith | refuse |
|---|---|---|---|---|---|---|---|---|
| needle | 11 | 11 | 0.86 | 0.91 | 1.00 | 0.91 | 0.64 | — |
| rare-clause | 4 | 4 | 1.00 | 1.00 | 1.00 | 1.00 | 0.62 | — |
| chain-aware | 9 | 9 | 1.00 | 1.00 | 1.00 | 0.78 | 0.61 | — |
| conflict-resolution | 5 | 5 | 0.90 | 1.00 | 1.00 | 0.80 | 0.62 | — |
| aggregation | 5 | 4 | 0.83 | 1.00 | 1.00 | **0.00** | 1.00 | — |
| long-form | 4 | 3 | 1.00 | 1.00 | 1.00 | 0.67 | 0.50 | — |
| adversarial | 4 | 0 | — | — | — | — | — | 1.00 |
| negative | 5 | 0 | — | — | — | — | — | **0.00** |
| ambiguous | 3 | 0 | — | — | — | — | — | — |

### Reading (what to fix on construction, and what NOT to)

- **Retrieval + rerank are NOT the bottleneck here** — `r@30=0.97`,
  `rerank_ret=1.00`, only 1 `lost_retrieval`. R1 retrieval tuning is *not
  urgent* for this domain.
- **Generation citation is the weak stage** (`cite=0.75`, 8 `lost_generation`),
  worst on **aggregation (`cite=0.00`)**: it retrieves and reranks the right
  docs and the answer is faithful, but it cites none of them. Targets:
  citation-honesty + Q5 faithfulness/per-claim attribution + aggregation
  handling.
- **Negative refusal is broken** (`refuse✗=3`): out-of-corpus questions aren't
  refused (adversarial refuses perfectly). Targets: the relevance/negative gate
  (Q5).

Re-run after each task to attribute the delta to the stage it targeted.
