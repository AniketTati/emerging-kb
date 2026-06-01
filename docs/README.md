# Docs index

What each document is, and whether it's **current** (describes the system as it
runs today) or **historical** (a point-in-time audit, plan, build log, design
spec, or per-domain eval run, kept under [`archive/`](archive/) for the trail).

For the project overview and one-command setup, start at the repo
[`README.md`](../README.md). The run story in one line: `./scripts/bootstrap.sh`
brings up Docker (db · minio · migrate · api · worker) and restores the committed
**finance demo seed** (55 pre-extracted docs, workspace
`f0000000-0000-0000-0000-000000000001`); the UI runs separately on `:3000`.

---

## Current — read these

| Doc | What it is |
|---|---|
| [`architecture.md`](architecture.md) | **Start here.** How the system works today — ingest · query · UI · stack — plus an honest scale review. |
| [`problem_statement.md`](problem_statement.md) | The brief: the challenge, the locked requirements, the 55-doc finance demo, and deliberate descopes. |
| [`scale_perf_audit.md`](scale_perf_audit.md) | Deep cost/latency/scale analysis (10K → 100M docs) + the enterprise upgrade paths. Forward-looking reference for the roadmap. |

**Eval working data (kept in place — scripts read/write here at runtime):**

| Path | What it is |
|---|---|
| [`eval_audit/`](eval_audit/) | Per-domain eval ground-truth + triage, written by `scripts/{audit_eval,finalize_eval,fix_eval,build_ground_truth}.py`. |
| [`eval_baselines/`](eval_baselines/) | CSV metric snapshots from eval baseline runs. |

---

## Historical — [`archive/`](archive/)

Preserved for the reasoning trail; superseded by the current docs above and by the
real code. Internal links inside these files may point to old locations.

**Design-phase specs** (the fuller target system; superseded by `architecture.md`)
- `architecture_design_spec.md` — the original ~1450-line locked formal spec (10 storage layers, planner modes, Waves A/B/C).
- `ui_design.md` — locked UI design for 10 surfaces + Doc Detail · `ui_design_v1.md` — its pre-prototype predecessor.
- `gaps_design.md` — 9 detailed gap designs (aggregation, conflicts, doc chains, feedback, citations, vocabulary, lineage, chat context, layered config).
- `api_contracts.md` — phase-gated REST API contract (the live API is `src/kb/api/` + `/docs`).

**Build log**
- `build_tracker.md` — gate-by-gate build discipline (G1 plan → G5 verify per phase), ~3000 lines.

**Decisions & teaching tours**
- `DECISIONS.md` — researched rationale for each forced choice (parsing, chunking, extract-define-canonicalize, the trust layer).
- `SYSTEM_IN_PLAIN_ENGLISH.md` · `INGESTION_WALKTHROUGH.md` · `walkthrough.md` — plain-English / teaching walkthroughs (use construction-era examples).

**Audits & adversarial reviews**
- `ENGINEERING_AUDIT.md` · `RAG_AUDIT_AND_ACTION_PLAN.md` — code / architecture audits + action plans.
- `red_team.md` — adversarial review · `scenarios.md` — 8 enterprise stress-tests.
- `citations_audit.md` — every cited paper/product verified real · `competitive_audit.md` — 2026 SOTA sweep (Hebbia, Glean, NotebookLM, …).
- `upload_flow_audit.md` — upload/ingest flow audit · `EVAL_TRUSTWORTHINESS_REPORT.md` — eval-rebuild audit.
- `M1_STAGE_EVAL.md` — per-stage measurement harness · `GROUND_TRUTH_ANALYSIS.md` — hand-verified corpus facts · `g-mode-status.md` — graph-mode status.

**Fix plans & root-cause diagnoses** (point-in-time)
- `PIPELINE_FIX_PLAN.md` · `FIX_CHECKLIST.md` · `extraction_and_citation_plan.md` — structured-layer / extraction fix plans + checklist.
- `HOLISTIC_DIAGNOSIS.md` · `RCA_AND_PATH_TO_90_PERCENT.md` · `CONSTRUCTION_QUERY_FORENSIC.md` — root-cause analyses.

**Per-domain eval artifacts** (development across non-finance domains)
- `construction_query_results*.json` · `demo-corpus-eval-construction.md` · `chat_eval_queries.md` — construction-domain eval runs.
- `entity_dedup_proposal_construction*.yaml` · `entity_merge_proposal_construction.yaml` · `field_merge_proposal_construction*.yaml` — entity / field canonicalization proposals.

**Early problem framing**
- `Problem_1.md` · `Problem_2.md` — early problem / UX expansions.
