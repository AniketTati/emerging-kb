# Project status — 2026-05-26

The authoritative "where we are right now" doc. Read this first when picking
the project back up. Updated each time meaningful work lands on `main` or an
open PR moves.

> For the locked design specs, see [`architecture.md`](architecture.md),
> [`ui_design.md`](ui_design.md), [`gaps_design.md`](gaps_design.md).
> For the build-discipline log, see [`build_tracker.md`](build_tracker.md).

---

## Branch state

| Branch / PR | Status | What it carries |
|---|---|---|
| `main` (HEAD `01505e3`) | merged | Phases 0–10b · Wave A (WA-1…17) · PR #31 (E1/E2/E2b/E3 + citations v2 + broader corpus + /upload pagination) |
| [PR #32](https://github.com/AniketTati/emerging-kb/pull/32) — `waveB/e2b-classifier-fallback` | **open · awaiting review** | E4 + PR8 + R1–R5 + retrieval channel-filter + R4 noise + R5 layout (8 commits, 37 files, +4083 lines) |

Two backfill scripts run-once-after-merge:
- `scripts/cleanup_noise_entities.py` — drops the ~450 noise entities from the live workspace
- `scripts/backfill_pdf_layout.py` — re-parses existing PDFs to populate `raw_pages.layout_json.elements`

Both are idempotent and have already been run against the local demo workspace
(the dev UI shows the post-fix state).

---

## Demo workspace right now

Numbers queried 2026-05-26 against `KB_DATABASE_URL` (live dev DB):

| Table | Count | Notes |
|---|---:|---|
| `files` (non-deleted) | 26 | 8 PDFs · 1 xlsx-bank · 1 xlsx-expense · 1 xlsx-pricing · 13 md · 2 eml · others |
| `atomic_units` | 475 | 26 of 26 files have non-zero units (was 7 / 26 pre-PR8) |
| `entities` | 217 | Down from 671 pre-R4 — pure signal (ORG / PRODUCT / PERSON / GPE / WORK_OF_ART / LAW / LOC / LANG / EVENT / NORP) |
| `extracted_mentions` | 1,628 | Mentions stay even when entity is a noise type (kept for chunk-context citation) |
| `doc_chains` | 1 | `contract_chain` linking `vertex-msa.pdf` ↔ `vertex-amendment.txt` (R4 / E4) |
| `extracted_triples` | ~370 | L4 relationship extraction across all formats |
| `proposed_fields` | ~410 | L3 open-world field inference |

Sanity-check query against `/chat`:

```
$ curl -X POST http://localhost:8000/chat \
    -H "Content-Type: application/json" \
    -H "X-Test-Workspace: 00000000-0000-0000-0000-000000000001" \
    -d '{"query":"Tell me about the MSA between NorthWind and Vertex including payment terms."}'

→ refused: false · crag_score: 1.0 · faithfulness: pass
→ citations:
    [1] vertex-msa.pdf       SUPERSEDED (rule=chain)   ← R1 conflict tag
    [2] vertex-amendment.txt live                       ← winner
    [3] vertex-eval-notes.md live
    [4] invoice-mar2026.pdf  live
→ conflict_resolutions:
    - payment_terms.payment_due_days · resolved 'chain' · picked '45' · loser '30'
```

The "Resolved 1 conflict via chain rules" banner renders above the answer in the
chat UI, and the `vertex-msa.pdf` citation card shows a red `SUPERSEDED` pill.
See `ui/tests/artifacts/r1-citations-panel.png` for the live screenshot.

---

## What's shipped — wave by wave

### Phases 0–10b (already on `main` before Wave A)

Core platform: schemas / parsing / chunking / RAPTOR / extraction / identity / 6-channel
retrieval / RRF / reranker / CRAG / Astute generation / orchestrator / SSE upload status /
Next.js Upload + Chat UI / eval harness. Full log in [`build_tracker.md`](build_tracker.md).

### Wave A — WA-1 … WA-17 (merged via PR #23)

Layered config · domain vocabulary · doc-chains (Design 3) · triples + HippoRAG PPR ·
conflict-detection module (Design 2) · polymorphic citations · faithfulness gate ·
intent classifier + planner + mode router · Q-mode SQL · hash-chained audit log ·
3-tier ChatContext memory · feedback / correction loop · UI page backend endpoints ·
dataset loader + eval harness.

### Wave B step 1 — `main` (PRs #24 … #31)

| PR | What |
|---|---|
| #24 | UI baseline fixes — SSE teardown bug, hydration races, Dockerfile config |
| #25, #26 | Dashboard page · Doc Detail two-pane viewer · upload-flow audit · dev-mode scripts |
| #27 | Citations v2 — worker-resolved source positions (migration 0032: `source_chunk_id` + `source_char_start/end` on mentions / fields / units / triples), `source_resolver.py` two-pass deterministic resolver, **E3** clauses-plugin gate fix |
| #28 | Broader demo corpus — 20 new docs across 8 domains, deterministic builds (reportlab `invariant=1` + xlsxwriter `_freeze_workbook`) |
| #29 | **E1** json_recovery tolerant LLM-output parser (mentions coverage 31% → 96%) |
| #30 | **E2** `KB_PROMOTION_MIN_DOCS` default 5 → 1 |
| #31 | E2b classifier prompt explicitly forbids `unknown/other/document`; `/upload` pagination |

### Wave B step 2 — [PR #32](https://github.com/AniketTati/emerging-kb/pull/32) (open)

| Commit | What |
|---|---|
| `155dd30` | **E4** — doc-chain MSA.pdf ↔ Amendment.txt via broader contract-doctype synonyms + 200-char title window |
| `243f9d0` | **PR8** — L3 atomic-unit plugins for the prose long tail: `email_messages` (regex split) + `generic_items` (LLM with per-doctype hints) + broader clauses matcher + transactions false-positive guard |
| `55b5d2e` | **R1** — wire Design 2 `conflict_detector` into chat (`conflict_resolution.py`): FactCandidates from chained-doc atomic units → `resolve_all` → prompt context block + citation tagging + `fact_conflicts` persist |
| `7839b9d` | **R2** — promote chat citations to char-range exact-snippet kind |
| `0cbbfa4` | **R3** — chat UX: file labels on citations, refusal context, txn-corruption fix |
| `42fbbb6` | **Channel filter** — `lifecycle_state <> 'deleted'` in all 6 channels (soft-deleted dupes were leaking → ghost citations + broke R1's superseded matching) |
| `c846dc8` | **R4** — skip noise mention types (`CARDINAL`/`QUANTITY`/`DATE`/`MONEY`/`ORDINAL`/`PERCENT`/`TIME`) in identity resolver; `entities` 671 → 217 |
| `1ac104e` | **R5** — Docling per-element provenance on `raw_pages.layout_json.elements`; DocDetail coloured SVG mini-map |

---

## What's pending

### Immediately actionable
1. **PR #32 review + merge** — once merged, run the two backfill scripts in any
   production workspace (`cleanup_noise_entities.py` + `backfill_pdf_layout.py`).
2. **Update `api_contracts.md`** — the spec lists 8 lifecycle events but the
   pipeline emits 19+. Missing fields on the `/files` response: `inferred_doc_type`,
   `source_authority`, `source_authority_reason`, `doc_status`. (Documented as a
   stale section here; not blocking PR #32.)
3. **Run the full chat e2e suite** — `cd ui && npx playwright test tests/chat.spec.ts`
   has 4 cases (including the R1 conflict-banner test) that depend on the live
   backend + demo corpus. Not re-run against PR #32's branch since the change.

### Deferred (explicitly out of scope this wave)
- **Wave-C PDF bbox overlay** — use R5's captured element bboxes to draw
  highlight rectangles over the actual rendered PDF when a chat citation is
  clicked. Requires PDF.js viewport math (coord transform + scale tracking).
- **Cross-type entity defragmentation** — same name + different NER types
  ("Master Services Agreement" as EVENT / LAW / WORK_OF_ART) creates 3 rows
  for one logical concept. Risky to auto-merge without contextual embeddings
  (would false-merge Kafka-ORG vs Kafka-PRODUCT). Documented as known.
- **Identity homonyms** — true homonym case (same name + same type, different
  real-world entity) isn't surfacing on the demo corpus; resolver's (name+type)
  gate + LLM judge handle the common patterns. Re-open if production data
  surfaces actual false-merge bugs.
- **Items from the original scoping doc** ([`problem_statement.md`](problem_statement.md)
  §"Out of scope") — permissions, native CAD/DICOM, real-time streams,
  bi-temporal, agentic actions, vector-store graduation, image content
  understanding, multi-tenant, cross-lingual atomic units, live source
  connectors.

---

## How to verify the current state

```bash
# 1. Stack health
make up                           # if not already running
curl -s http://localhost:8000/health

# 2. Live workspace numbers
source scripts/dev_env.sh
uv run python -c "
import asyncio, psycopg, os
async def main():
    async with await psycopg.AsyncConnection.connect(os.environ['KB_DATABASE_URL']) as conn:
        for t in ('files','atomic_units','entities','extracted_mentions',
                  'doc_chains','extracted_triples','proposed_fields'):
            cur = await conn.execute(f'SELECT count(*) FROM {t}')
            print(f'{t:<22} {(await cur.fetchone())[0]:>6}')
asyncio.run(main())
"

# 3. Test suites
source .venv/bin/activate
python -m pytest tests/ -q                # ~600 tests
cd ui && npx vitest run                   # 23 unit tests
cd ui && npx playwright test              # ~20 e2e (needs backend + demo data)

# 4. Manual smoke
open http://localhost:3000/upload         # paginated file list
open http://localhost:3000/chat           # try the MSA payment-terms query
open http://localhost:3000/files/<id>     # expand "Parsed text" → see R5 minimap
```

---

## Doc currency

Last `Explore`-driven audit: 2026-05-26.

| Doc | State | What to know |
|---|---|---|
| [`problem_statement.md`](problem_statement.md) | up-to-date | Scope + out-of-scope still accurate |
| [`architecture.md`](architecture.md) | mostly-current · missing R4 (noise filter) + R5 (layout minimap) sections | Locked design, hasn't grown the Wave-B additions yet |
| [`build_tracker.md`](build_tracker.md) | current through Wave A | Wave B PRs (#24+) summarised here instead |
| [`api_contracts.md`](api_contracts.md) | **stale** — lifecycle events + `/files` response fields | High-priority follow-up |
| [`gaps_design.md`](gaps_design.md) | up-to-date | Design 2 (conflicts) now actually wired into chat via R1 |
| [`ui_design.md`](ui_design.md) | locked for Phase 0–3 | Wave-B UI surfaces (Doc Detail two-pane, conflict banner) not yet folded in |
| [`walkthrough.md`](walkthrough.md) | aspirational | Doesn't cover the post-parse extraction phases or R1/R5 chat surfaces |
| [`extraction_and_citation_plan.md`](extraction_and_citation_plan.md) | **closed-out** | All E-items shipped; doc kept as historical record + closeout summary at top |
| [`upload_flow_audit.md`](upload_flow_audit.md) | up-to-date | Drove the Wave-B step-1 work; findings landed |
| [`scenarios.md`](scenarios.md), [`red_team.md`](red_team.md), [`competitive_audit.md`](competitive_audit.md), [`citations_audit.md`](citations_audit.md), [`scale_perf_audit.md`](scale_perf_audit.md) | up-to-date | Stable strategic content |
