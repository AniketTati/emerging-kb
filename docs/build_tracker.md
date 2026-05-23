# Build Tracker

> **Single source of truth** for what is planned, in-progress, and done. Updated every time we cross a gate. If something isn't in this file, it isn't being built yet.

**Owner:** Aniket
**Started:** 2026-05-22
**Status:** Pre-build — Phase 0 gates not yet opened.

---

## 0. The build discipline (read once, follow always)

Every phase moves through **6 gates**. We do not skip gates. We do not write production logic before the gates ahead of it are green.

```
  ┌────────┐   ┌──────────────┐   ┌────────┐   ┌──────────┐   ┌────────┐   ┌────────┐
  │ G1     │ → │ G1.5         │ → │ G2     │ → │ G3       │ → │ G4     │ → │ G5     │
  │ PLAN   │   │ VISUAL       │   │ API    │   │ TESTS    │   │ BUILD  │   │ RUN    │
  │ arch,  │   │ clickable    │   │ req/   │   │ written  │   │ logic  │   │ verify │
  │ scope, │   │ HTML proto;  │   │ res    │   │ first    │   │ inside │   │ green  │
  │ tech   │   │ user reviews │   │ locked │   │ + reviewed│  │ svcs   │   │ e2e    │
  └────────┘   └──────────────┘   └────────┘   └──────────┘   └────────┘   └────────┘
```

| Gate | What lands | Where it lives | "Green" means |
|------|-----------|----------------|---------------|
| **G1 — Plan** | Architecture, scope, tech stack, data model for this phase | `docs/architecture.md`, `docs/gaps_design.md`, phase-specific section in this tracker | Reviewed + signed off in this tracker. No code yet. |
| **G1.5a — Visual prototype** | Static HTML + Tailwind clickable mock of every screen this phase touches | `prototype/*.html` | User opens it in a browser, clicks through, signs off. Locked design then back-ports into `ui_design.md`. |
| **G1.5b — Visual QA pass** | Playwright runs every prototype screen at desktop/tablet/mobile viewports, captures full-page screenshots, runs the §0.1 checklist section-by-section. Issues fixed before user reviews. | `prototype/qa/screens/<page>-<viewport>.png`, `prototype/qa/reports/<page>.md` | Every line of the checklist green for every page, every viewport. **Screen does not advance to user sign-off until QA is green.** |
| **G1.6 — Wiring inventory** | Every interactive element on every screen → mapped to its planned backend interaction (API endpoint, mutation, SSE stream) or marked client-only. Orphan UI is removed. | `prototype/wiring_inventory.md` | No interactive element exists without a documented purpose. The inventory becomes the input set for G2 — every "PLAN" row in the inventory must become an API contract in G2. |
| **G2 — API contracts** | Every endpoint's request/response, error shapes, status codes | `docs/api_contracts.md` | Reviewed, iterated, locked. Tests do not start until contracts are locked because mistakes here cascade. |
| **G3 — Test cases** | One test spec per endpoint + per service; happy path + edge + failure | `tests/specs/<phase>.md` + skeleton test files (red, not yet passing) | Every contract from G2 has a matching test. Tests fail (no logic yet) — expected. |
| **G4 — Build** | Service / handler logic | `src/kb/...` | Tests from G3 now pass. No new behavior beyond what G3 covers. |
| **G5 — Run** | End-to-end smoke against the live stack | `scripts/verify_<phase>.sh` | Service runs against docker-compose stack, smoke passes, no regressions in prior phases' tests. |

**Rules:**
1. **No backwards skips.** Don't add logic in G4 that wasn't covered by a G3 test. If we missed something, go back to G2, fix the contract, add the test, then continue.
2. **No phase advances** until G5 is green for the prior phase (with explicit exceptions noted below).
3. **Every G5 pass runs the full prior-phase test suite** — we don't let regressions hide.
4. **Tracker updates are non-optional.** When a gate turns green, this file gets a tick the same day.
5. **Plan changes update plans, not code.** If we change our minds mid-build, we go back to G1 of the affected phase, edit the plan, re-review, then re-enter G2.

### 0.1 Visual QA checklist (used at G1.5b for prototypes and G5 for production UI)

Applied **per page, per viewport** (desktop 1440×900, tablet 1024×768, mobile 390×844). The QA pass screenshots, runs through these checks, and reports findings before handing the page to user review. Source-of-truth template: [`prototype/qa_checklist.md`](../prototype/qa_checklist.md).

| Section | Checks |
|---|---|
| **Sidebar / left nav** | Collapsed-state icons all render · Hover-expand reveals labels cleanly · Active section visually distinct · Section dividers labelled · No overflow at any viewport · Keyboard focus visible |
| **Top bar / header** | Breadcrumb readable · Right-side actions don't overlap title at narrow widths · ⌘K hint present and aligned · Theme toggle present · No vertical misalignment |
| **Primary content area** | Max-width sane (text isn't a wide ribbon on big monitors) · Scroll behaves (sticky composer / header stays put) · Typography hierarchy clear (h1 → h2 → body) · Line-length 60–80ch for prose · Inline images/figures don't blow out the column |
| **Right panel (when present)** | Width fixed and reasonable (350–400px) · Header sticky · Inner scrolling independent of main column · Cards don't horizontal-scroll · Doesn't collapse content below readable threshold |
| **Interactive elements** | All buttons have visible hover state · All buttons have ≥36px touch target on mobile · Inputs show focus ring · Links underline on hover or have other affordance · Disabled states clearly muted |
| **Icons & imagery** | Every icon renders (no broken/missing) · Icon stroke widths consistent · Icons aligned with their labels (vertical baseline) · Logo / brand mark renders correctly |
| **Typography & color** | Body contrast ≥ 4.5:1 against background · No text below 12px except mono technical metadata · Mono font reserved for IDs/timings/snippets · Accent color used sparingly (≤ 3 instances per screen) |
| **Empty / loading / error states** | Each list/feed/table has an explicit empty state · Loading states are progressive (skeleton/stream, not centered spinner) · Errors are inline and recoverable |
| **Information density** | Whitespace appropriate for the surface (admin = denser, chat = airy) · No "wall of text" without visual breaks · Related elements grouped, unrelated separated |
| **Responsive** | At tablet: sidebar collapses by default · At mobile: right panel collapses to a tab or drawer · Tap targets respected · No horizontal page scroll |
| **Cross-page consistency** | Sidebar identical on every page · Top-bar height identical · Hover/focus patterns identical · Spacing scale identical |

**Each check has one of three states per page+viewport: ✓ pass · ⚠ minor (note, fix in production) · ✗ fail (block sign-off).**

### 0.15 Git workflow (every phase lives on its own branch)

Public repository · ongoing development. Branch model + commit conventions are non-optional.

```
            main  (protected · only fast-forward merges via PR)
              │
              ├─ phase-0/repo-skeleton ─────────────┐
              │     ├ commit per gate (G1, G1.5, G2, G3, G4, G5)
              │     └ PR opens at G5; review + merge
              │
              ├─ phase-1/schema-service ────────────┤
              ├─ phase-2/parse-layer ───────────────┤
              ├─ phase-N/<short-name> ──────────────┘
              │
              └─ feature/<descriptive-name>          (out-of-band fixes, docs)
```

**Branch naming:**
- `phase-N/<short-name>` — one branch per build-tracker phase (e.g., `phase-0/repo-skeleton`, `phase-10b/ui-chat`)
- `feature/<short-name>` — for cross-phase work (docs polish, dependency bump, tooling)
- `fix/<short-name>` — for bug fixes against `main`

**Commit conventions** (Conventional Commits, lowercase):
- `feat(phase-N): <gate> — <what>` for new functionality at a gate
- `test(phase-N): <gate> — <what>` for test work
- `chore(phase-N): <gate> — <what>` for non-functional changes
- `docs: <what>` for documentation-only changes
- `fix(phase-N): <what>` for bug fixes

Examples:
- `feat(phase-0): G4 build — docker-compose with postgres, pgvector, pg_search, minio`
- `test(phase-1): G3 specs — schema CRUD test scaffolds (red)`
- `feat(phase-1): G4 build — schema service CRUD endpoints; G3 tests now pass`
- `docs: back-port locked UI design into ui_design.md`

**Per-phase Git ritual** (interlocks with the 6 build gates):

| Gate | Git action |
|------|------------|
| **G1 Plan** | `git switch -c phase-N/<name>` from `main`. First commit on the branch: `docs(phase-N): G1 plan — <summary>`. |
| **G1.5 Visual prototype** (UI phases only) | Commits to the same branch as prototype HTML lands. Each screen sign-off: `feat(phase-N): G1.5 — <screen> prototype signed off`. |
| **G1.6 Wiring inventory** (UI phases only) | One commit per inventory pass. |
| **G2 API contracts** | One commit per contract or contract group: `docs(phase-N): G2 — API contracts for <endpoints>`. |
| **G3 Test cases** | One commit landing all G3 test skeletons (red): `test(phase-N): G3 specs — <what>`. |
| **G4 Build** | Many commits as logic lands; each makes some G3 test pass: `feat(phase-N): G4 — <what>`. |
| **G5 Run / verify** | `scripts/verify_phase_N.sh` lands + passes: `chore(phase-N): G5 — verify script + green run`. **Open PR**: title `Phase N: <name>` linking the relevant tracker rows. |
| **PR merge** | Squash-merge after review. Delete the phase branch. Tag if it's a phase boundary: `git tag phase-N-complete`. |

**Wave boundaries:** tag `wave-a-mvp` after Phase 12 ships green. Same for `wave-b-polish`, etc.

**Protected `main`:**
- No direct commits.
- Force-push disabled.
- PRs require: green CI, build-tracker gate row updated, no new failing tests.

**What never gets committed:**
- `prototype/qa/screens/` and `prototype/qa/reports/` — regenerable by running `node qa.mjs` (now in `.gitignore`)
- `prototype/node_modules/`, `prototype/package-lock.json` — regenerable
- `docs/Build a Knowledge Base Service.pdf` — the original problem brief, kept locally only; the public version is `docs/problem_statement.md`
- Anything in `.env*`, `.claude/`, `data/`, `pg-data/`, `minio-data/`

**Contributor entry point:** [`CONTRIBUTING.md`](../CONTRIBUTING.md) for the short version; this section is the long version.

### 0.2 Cross-cutting design rules (applied on every page)

These are invariants — every screen must satisfy them. Violations block sign-off the same way QA fails do.

| Rule | What it means | Where it manifests |
|---|---|---|
| **Schema visible everywhere** | Wherever a field value is shown, its schema metadata (typed/inferred/collision · field name · type) is visible or one click away. The system must never display a value without letting the user see what schema produced it. | Doc Detail, Explore entity/doc/atomic-unit cards, Upload expanded rows, Chat citation cards, Extraction Studio. |
| **Schema editable everywhere** | Wherever a field value is shown, the user can edit it (with impact preview) or jump to Schema Studio to edit the definition. No "view-only" surfaces for schema. | Same as above. |
| **Doc Detail is universal** | Any doc / citation / entity / clause / atomic-unit → single click opens the same Doc Detail slide-in panel. No alternative drill-downs. | Every page. |
| **⌘K is global** | Global command palette reachable from every page. Jump to doc / entity / Studio tool / setting. | Every page. |
| **Streaming, not spinners** | Long-running things stream (ingest stages, chat responses, learning events). No centered spinners. | Upload, Chat, Dashboard. |
| **Trust signals on every answer/extraction** | Whenever the system shows a derived value (answer, extracted field, anomaly score, promoted field), it shows confidence + source. | Chat answers, Extraction Studio fields, Schema Studio promotions, Anomaly cards. |
| **Sidebar + top-bar identical** | Same components, same height, same hover/active behavior on every page. | Every page. |

These rules are checked at G1.5b QA in [`prototype/qa_checklist.md`](../prototype/qa_checklist.md) §12.

### 0.3 User-facing copy discipline

Engineering-roadmap and internal-design references **do not appear in user-facing UI**. They live in `docs/` and the tracker, not in the product.

**Forbidden in production UI copy:** Wave labels (A/B/C), phase numbers (Phase 0–23), internal design names (Design 1–9), library names (Hydra, OmegaConf, Procrastinate, RAPTOR, HippoRAG, ColPali), the corrections table by name, any `gaps_design.md §X` style citation.

**Allowed:** plain-English explanations of behavior. Example — instead of *"Logged to corrections (Design 4)"*, write *"Reason logged."* Instead of *"YAML rules resolved by Hydra/OmegaConf · DB overrides apply at runtime (Design 9)"*, write *"YAML rules per doc-type. Saving creates a new version."*

QA gates this at G1.5b — every prototype page is grep'd for the forbidden vocabulary before sign-off.

---

## 1. Now / Next / Blocked

**Now:** Phase 1c G4 — building (`0007_schema_hierarchy.sql` + new domain module + new router + extended schemas/schema_versions domain + main wiring). Branch: `phase-1c/schema-hierarchy`.
**Next:** Phase 1c G5 — `verify_phase_1c.sh` + run all 4 verify scripts + end-of-phase cross-phase sweep.
**Blocked on:** nothing.

---

## 2. Planning artifacts — completion checklist (pre-Phase-0)

These exist *before* any phase opens. They define the system as a whole. Each must be reviewed and confirmed before we open Phase 0 G1.

| Artifact | File | Status | Review needed? |
|---|---|---|---|
| Mental model + locked decisions | [README.md](../README.md) | ✅ Done | Confirm scope + locked tech stack |
| Architecture spec (16 sections) | [docs/architecture.md](architecture.md) | ✅ Done | Confirm: layers, storage, query pipeline, phasing |
| UI design (10 surfaces, locked) | [docs/ui_design.md](ui_design.md) + [`prototype/`](../prototype/) | ✅ Done | **Reviewed via clickable prototype (G1.5)** |
| 9 gap designs | [docs/gaps_design.md](gaps_design.md) | ✅ Done | Confirm each design is well-formed |
| Walkthrough (ingest + retrieval traces) | [docs/walkthrough.md](walkthrough.md) | ✅ Done | Reference doc, no review gate |
| Scenarios (8 enterprise stress-tests) | [docs/scenarios.md](scenarios.md) | ✅ Done | Reference doc |
| Red team | [docs/red_team.md](red_team.md) | ✅ Done | Open findings tracked in source doc |
| Citations audit | [docs/citations_audit.md](citations_audit.md) | ✅ Done | Reference doc |
| Competitive audit (2026 SOTA) | [docs/competitive_audit.md](competitive_audit.md) | ✅ Done | Wave B additions confirmed |
| Scale/perf audit | [docs/scale_perf_audit.md](scale_perf_audit.md) | ✅ Done | 18 weaknesses named — accepted |
| **Build Tracker (this file)** | docs/build_tracker.md | 🟡 In review | **You sign off** |
| API contracts | [docs/api_contracts.md](api_contracts.md) | ✅ Phase 0 contracts signed off 2026-05-23 | Phase 1 contracts land at Phase 1 G2 |
| Test specs (per-phase) | tests/specs/ | ⬜ Not started | Created per phase at G3 |

---

## 3. Tech stack — locked (no change without re-opening G1 globally)

| Layer | Choice | Why |
|---|---|---|
| **Runtime** | Python 3.12, uv-managed | Modern toolchain, fast resolver, lockfile reproducible |
| **API framework** | FastAPI | Async, OpenAPI built-in, ecosystem maturity |
| **DB** | Postgres 17 + pgvector ≥ 0.8 + ParadeDB pg_search + ltree (built-in) | One transactional store; vector + BM25 + hierarchical labels in same place. Apache AGE deferred (MVP doesn't need Cypher; recursive CTEs cover lineage/chains). |
| **Test fixtures** | `testcontainers-python[postgres,minio]` ≥ 4.7 + `freezegun` (dev-only) | Hermetic per-session Postgres + MinIO; tests run without a pre-existing docker-compose stack. Freezegun for assertions on timestamps. |
| **Object store** | MinIO | S3-compatible; runs in docker-compose |
| **Queue** | Procrastinate | Postgres-backed; one fewer service |
| **LLM (extraction/plan/gen)** | Gemini 2.5 Flash | Cost/latency target; adapter pattern so swappable |
| **Embeddings** | Gemini Embedding 001 | Same provider; high quality multilingual |
| **Reranker** | Cohere Rerank 3.5 | Best-in-class cross-encoder |
| **Parsers** | Docling (digital PDF), Mistral OCR 3 (scanned), openpyxl (xlsx), Gemini VLM (fallback) | Each is best-of-class for its modality |
| **UI** | Next.js 15 + Tailwind | SSR + streaming + ecosystem |
| **Streaming** | Server-Sent Events for ingest status, streaming responses for chat | Simpler than WebSockets, fits our flows |
| **Container** | docker-compose for local; container-per-service | Standard, portable |
| **Test runner** | pytest + pytest-asyncio + httpx | Standard FastAPI test stack |
| **Eval** | RAGAS + HHEM + custom 45-question stratified set | Multi-method confidence |

---

## 4. UI screen walkthrough — REVIEW REQUIRED before Phase 0 (via clickable prototype, G1.5)

**Information architecture — locked 2026-05-22, problem-driven:** chat is the front door (95% of users), Studio holds the power-user surfaces, Admin holds dashboards and logs. Universal **Doc Detail** slide-in opens from any citation/doc/entity anywhere. Global **Cmd-K** palette jumps anywhere.

```
LEFT SIDEBAR (collapsed icons, expand on hover)

🏠 PRIMARY
  💬 Chat               ← home / front door
  📤 Upload
  🔍 Explore            Knowledge Explorer (progressive expansion)

🧪 STUDIO
  🧠 Schema Studio      Typed · Inferred · Collisions · Vocabulary · Lineage · Versions · Impact preview
  ⚗️  Extraction Studio  per-doc review · approve/edit/reject · prompt editor · test mode
  🎛️  Playground         run-the-pipeline-on-anything sandbox

📊 ADMIN
  📊 Dashboard          counts + "what the system just learned" + top anomalies
  📋 Audit              immutable per-query logs
  ⚙️  Settings + /swagger
```

The clickable prototype is what we review, not ASCII mockups. Each row below corresponds to a single `.html` file in `prototype/`.

| # | Screen | File | Status |
|---|--------|------|--------|
| 1 | 💬 Chat (home — the 95% surface) | `prototype/chat.html` | ✅ signed off |
| 2 | 📤 Upload (drag-drop + live SSE ingestion) | `prototype/upload.html` | ✅ signed off |
| 3 | 🔍 Explore (progressive expansion, search-first) | `prototype/explore.html` | ✅ signed off |
| 4 | 🧠 Schema Studio (Typed · Inferred · Collisions · Vocabulary · Lineage · Versions) | `prototype/schema-studio.html` | ✅ signed off |
| 5 | ⚗️ Extraction Studio (per-doc PDF + extracted fields, approve/edit/reject, prompt editor, test mode) | `prototype/extraction-studio.html` | ✅ signed off (rebuilt against docs) |
| 6 | 🎛️ Playground (run pipeline on anything, eval-style) | `prototype/playground.html` | ✅ signed off |
| 7 | 📊 Dashboard (counts + learning stream + anomalies) | `prototype/dashboard.html` | ✅ signed off |
| 8 | 📋 Audit (per-query logs) | `prototype/audit.html` | ✅ signed off |
| 9 | 📑 Doc Detail (universal slide-in panel, reused everywhere) | `prototype/doc-detail.html` | ✅ signed off (rebuilt around JTBD) |
| 10 | ⚙️ Settings + Swagger exposure | `prototype/settings.html` | ✅ signed off |

**Process for each screen:**
(a) I build the static HTML+Tailwind page with realistic dummy data.
(b) I post the file path; you open in browser, click around.
(c) You push back: anything off, missing, unclear, or wrong from a KB-user perspective.
(d) I iterate.
(e) Row ticked when you sign off.
(f) Once all rows ticked, I back-port the locked design into `docs/ui_design.md` and Phase 0 G1 opens.

---

## 5. Build phases — Wave A (MVP slice)

> Source of phase list: `architecture.md` §12. Each row tracks all 5 gates.

Legend: ⬜ not started · 🟡 in progress · ✅ done · ⛔ blocked

| Phase | Description | G1 Plan | G2 API | G3 Tests | G4 Build | G5 Run | Notes |
|---|---|---|---|---|---|---|---|
| **0** | Repo + docker-compose (Postgres+pgvector+pg_search+MinIO+Procrastinate) + lifecycle DDL | ✅ | ✅ | ✅ | ✅ | ✅ | All 5 gates green 2026-05-23. `scripts/verify_phase_0.sh` 16/16 checks pass. Ready to merge. |
| **1a** | Schema service — **CRUD foundation**: `schemas` table + 5 endpoints (POST/GET-list/GET/PUT/DELETE) | ✅ | ✅ | ✅ | ✅ | ✅ | All 5 gates green 2026-05-23. verify_phase_1a.sh 17/17. verify_phase_0.sh still 16/16. Ready to merge. |
| **1b** | Schema service — **versioning**: `schema_versions` table; every PUT creates a new version; version list/read/rollback endpoints | ✅ | ✅ | ✅ | ✅ | ✅ | All 5 gates green 2026-05-23. verify_phase_1b.sh 21/21. verify_phase_1a.sh still 17/17. verify_phase_0.sh still 16/16. pytest 106/106. Ready to merge. |
| **1c** | Schema service — **hierarchy**: `schema_entities`, `schema_fields`, `schema_relationships` tables; nested CRUD; NL field descriptions; single_parent + cascade_delete constraints | ✅ | ✅ | ✅ | 🟡 | ⬜ | G1+G2+G3 ✅ signed off 2026-05-23. 36 new tests collected (entities 10 + fields 8 + relationships 8 + hierarchy_versions 10); pytest --collect-only confirms 142 total. G4 building now. |
| **2** | Parse layer: Docling + Mistral OCR + xlsx + email → raw_pages | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | Internal service; API exposed via upload (phase 10a) |
| **3** | Chunking + Contextual Retrieval + RAPTOR tree build | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | Internal worker |
| **4** | Indexing: pgvector HNSW + pg_search BM25 on all RAPTOR levels | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | Internal worker |
| **5** | Open extraction → mentions; clause split + typing + anomaly score | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | L2 + L2b + L3 |
| **6** | Schema-driven extraction (Gemini structured outputs) | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | L4/L5 projection |
| **7** | Identity resolution (deterministic→embedding→LLM judge→union-find) | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | Entity merge worker + admin endpoint |
| **8** | Query planner + rewriting (Step-Back + HyDE + Query2Doc) + parallel retrieval + RRF + rerank + CRAG gate + Astute generation | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | The big one — split into sub-phases at G1 |
| **9** | Audit log + lifecycle visibility + idempotency | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | SSE endpoint for upload-page status |
| **10a** | UI — Upload (drag-drop · live per-doc per-stage status via SSE) | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | Next.js page + tests · matches `prototype/upload.html` |
| **10b** | UI — Chat (front door · streamed answers · right-side citation cards · plan inspector) + universal Doc Detail slide-in panel | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | `chat.html` + `doc-detail.html` |
| **10c** | UI — Explore (Knowledge Explorer: search + left-rail facets · progressive expansion) | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | `explore.html` |
| **10d** | UI — Schema Studio (6 tabs: Typed · Inferred · Collisions · Vocabulary · Lineage · Versions · schema-swap affordance) | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | `schema-studio.html` · covers Designs 6 / 7 / 9 UI surfaces |
| **10e** | UI — Dashboard (counts + sparklines · live "what just learned" SSE feed · needs-attention · ingestion/query/cost cards) | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | `dashboard.html` |
| **10f** | UI — Audit (immutable per-query log · re-run with current config · add-to-regression-set) | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | `audit.html` · pairs with Phase 9 backend |
| **10g** | UI — Settings (workspace · models & retrieval defaults · auto-discovery · ingestion · cost · API keys · `/swagger` exposure · Effective Config view) | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | `settings.html` |
| **11** | Public-dataset loader: CUAD + Enron + SEC 10-K subset + scans + xlsx | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | Scripts, not service endpoints |
| **12** | Eval harness — 45 stratified Q&A (5 × 9 strata) + RAGAS + HHEM + basic Playground sandbox UI | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | `playground.html` (basic single-query + eval matrix) · regression CI |

### 5.1 Phase 0 plan — Repo skeleton + docker-compose (G1 ✅ SIGNED OFF)

> **Status:** G1 ✅ signed off (corrected version) 2026-05-23 by Aniket. Plan locked. Branch: `phase-0/repo-skeleton`.
>
> **History:** initial sign-off 2026-05-22 (commit `d50c1c7`) → re-opened 2026-05-23 after gate-transition consistency review surfaced six drift findings against architecture §6/§7/§12 (commit `1ee9738`) → second sign-off this date. The corrections below are the canonical Phase 0 plan.
>
> **What changed in the re-open:** workspace-scoped tables now carry `workspace_id` + RLS policies day 1 per architecture §7; `audit_log` ships in its full partitioned shape per architecture §6 (hash trigger deferred to Phase 9); `processing_status` removed (lands at Phase 2 as `file_lifecycle`); column renames to match architecture's canonical names (`ts` → `created_at`); FastAPI middleware added for workspace context + request-id; Phase 0 ↔ Phase 9 split made explicit.

#### Scope

Phase 0 produces the runnable infrastructure that every later phase builds on.

**In scope:**
- Single-package Python repo layout under `src/kb/`.
- `docker-compose.yml` bringing up Postgres (pgvector + pg_search), MinIO, a Procrastinate worker container, and the FastAPI app — in one command.
- Cross-cutting tables that phases 1–8 will write to: `audit_log` (full partitioned shape, hash trigger deferred to Phase 9), `idempotency_keys` (workspace-scoped), `schema_migrations` (infrastructure).
- RLS policies on every workspace-scoped table from day 1, plus the FastAPI middleware that sets `app.workspace_id` per request.
- Migration runner — raw SQL files + a thin Python applier.
- Python project tooling (`uv`, `ruff`, `pyright`, `pytest`).
- FastAPI app skeleton with middleware mounted (no routes yet — `/health` + `/ready` open at Phase 0 G2).

**Out of scope (deferred):**
- Any application logic (schema service, parsers, chunkers, indexers, retrieval, extraction, identity, query, UI). Each owns its phase.
- Phase-specific DDL (schemas, raw_pages, chunks, embeddings, mentions, entities, queries, raptor_nodes). Each phase ships its own `migrations/sql/NNNN_*.sql` at its own G4.
- Next.js `web/` project — Phase 10a.
- CI workflows beyond a single smoke check.

#### Decisions (locked at G1; changes require re-opening G1)

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | Repo layout | **Single Python package** at `src/kb/` with internal modules (`kb.api`, `kb.workers`, `kb.db`, `kb.storage`). API and worker share one image; differ only by entrypoint. | All later phases share schema/retrieval/eval primitives. Splitting now invents internal API surface that isn't needed. Process separation already happens via different entrypoints + Procrastinate queue, not packages. |
| 2 | Postgres image | **`paradedb/paradedb:latest-pg17`** | `pg_search` is a ParadeDB extension; the image bundles it with `pgvector`. Stock `postgres:17` + manual install is fragile. |
| 3 | Migration tool | **Raw SQL files + thin Python runner** (`migrations/runner.py`) tracking applied files in `schema_migrations`. | Architecture is DDL-heavy (extensions, partitions, HNSW, BM25, materialized views). Alembic autogenerate doesn't help with any of that; every migration would be hand-written. Avoids ORM coupling — multiple services use raw SQL. |
| 4 | Python tooling | **`uv`** (deps + lockfile), **`ruff`** (lint + format), **`pyright`** basic mode (types), **`pytest` + `pytest-asyncio` + `httpx`** (tests). | Modern, fast, no exotic choices. |
| 5 | Lifecycle DDL scope | **Narrow** — extensions + cross-cutting tables only. Each phase ships its own DDL at its own G4. | Lets table shapes evolve as the code using them gets written. Phase tables aren't pre-locked. |
| 6 | Row-Level Security (RLS) | **Enabled day 1** on every table that carries `workspace_id`. Policy: `workspace_id = current_setting('app.workspace_id')::uuid`. Set per request via `SET LOCAL` in a FastAPI middleware. MVP runs `workspace_id='default'` but the policies are real from day 1. | Per architecture §7. A dropped `WHERE workspace_id=...` is mathematically unable to leak across workspaces. Retrofitting RLS later is painful — every existing query needs auditing. Free now, expensive later. |
| 7 | Audit log table shape | **Ship the full partitioned shape at Phase 0**: range-partitioned by month on `created_at`, `workspace_id`+`query_id` indexes, `prev_hash`/`hash` columns. Defer the **hash-chain INSERT trigger + nightly integrity job** to Phase 9 (per architecture §12). | Partitioning is hard to add later without downtime; ship now. Hash trigger is a small additive at Phase 9 that doesn't change the table shape. |
| 8 | Phase 0 ↔ Phase 9 split | Phase 0 ships **stubs** of `audit_log` and `idempotency_keys` (full table shape, no enrichment). Phases 1–8 write to them. Phase 9 layers on: audit-log hash-chain trigger + integrity job + `GET /audit` read API + SSE lifecycle visibility endpoint. | Reconciles architecture §12 (Phase 9 owns "audit log + lifecycle + idempotency") with build_tracker §5 Phase 0 ("lifecycle DDL"). Lets phases 1–8 actually audit-log as they ship, without blocking on Phase 9. |

#### Repo layout (target after Phase 0 G4)

```
emerging-kb/
├── pyproject.toml              ← single uv project
├── uv.lock
├── .env.example                ← all env vars documented; real .env gitignored
├── docker-compose.yml
├── docker-compose.override.yml ← gitignored; local overrides
├── Dockerfile                  ← single image; api/worker/migrate = different entrypoints
├── src/kb/
│   ├── api/                    ← FastAPI app; entrypoint `kb.api.main:app`
│   │   ├── main.py             ← app factory; mounts /health, /ready at Phase 0 G2
│   │   ├── middleware.py       ← workspace context (SET LOCAL app.workspace_id) + X-Request-Id + access log
│   │   └── deps.py             ← db session, settings, current_workspace_id
│   ├── workers/                ← Procrastinate worker; entrypoint `kb.workers.run`
│   │   └── run.py
│   ├── db/                     ← psycopg async pool; transactions
│   │   └── pool.py             ← per-request connection; SET LOCAL app.workspace_id before any query
│   ├── storage/                ← MinIO client
│   ├── config.py               ← pydantic-settings (env-var-driven; Hydra/OmegaConf lands at Phase 5 when first LLM call arrives)
│   └── logging.py              ← structlog config (binds request_id, workspace_id)
├── migrations/
│   ├── runner.py               ← applies .sql files in lexical order; tracks in schema_migrations; runs as superuser (bypasses RLS for DDL)
│   └── sql/
│       ├── 0001_extensions.sql           ← CREATE EXTENSION vector, pg_search, ltree + CREATE ROLE kb_app
│       ├── 0002_schema_migrations.sql    ← bootstrap migration tracker (no workspace_id — infrastructure)
│       ├── 0003_audit_log.sql            ← partitioned by month on created_at + workspace_id + hash columns + RLS (hash trigger lands Phase 9)
│       └── 0004_idempotency_keys.sql     ← (workspace_id, key) primary key + RLS
├── scripts/
│   ├── bootstrap_db.sh         ← docker compose up + run migrations
│   └── verify_phase_0.sh       ← G5 smoke (lands at G5)
├── tests/
│   ├── conftest.py             ← lands at G3
│   └── specs/phase_0.md        ← lands at G3
└── docs/, prototype/           ← existing
```

**Reversibility note:** if any module under `src/kb/` later needs its own package (e.g. shared lib, separate deploy target), that's a mechanical extract — cheaper than carrying multi-package scaffolding through 12 phases that may never need it.

#### docker-compose service plan

| Service | Image | Ports | Volumes | Depends on |
|---|---|---|---|---|
| `db` | `paradedb/paradedb:latest-pg17` | `5432:5432` | `pg-data:/var/lib/postgresql/data` | — |
| `minio` | `minio/minio:latest` | `9000:9000` (S3), `9001:9001` (console) | `minio-data:/data` | — |
| `migrate` | built from `Dockerfile`; entrypoint `python -m migrations.runner` | — | — | `db` (healthy) |
| `api` | built from `Dockerfile`; entrypoint `uvicorn kb.api.main:app --host 0.0.0.0 --port 8000` | `8000:8000` | — | `migrate` (completed_successfully) |
| `worker` | built from `Dockerfile`; entrypoint `python -m kb.workers.run` | — | — | `migrate` (completed_successfully) |

**Notes:**
- Healthchecks: `db` → `pg_isready`; `minio` → HTTP `/minio/health/live`; `api` → `GET /health` once routes land at G2.
- Single `Dockerfile` for `api`, `worker`, `migrate` — different entrypoints over the same image. Keeps build cache tight.
- `migrate` runs as a short-lived one-shot init container (Compose `service_completed_successfully` condition gates `api` + `worker`).
- `.env.example` committed with placeholders. `pg-data/` and `minio-data/` gitignored.
- No separate broker — Procrastinate uses Postgres directly.

#### Lifecycle DDL — Phase 0 migrations (corrected scope, RLS day-1)

Phase 0 ships **four** migration files. Three carry `workspace_id` + an RLS policy from day 1 per architecture §7. The fourth (`schema_migrations`) is global infrastructure and has no workspace scope.

##### `0001_extensions.sql`

```sql
CREATE EXTENSION IF NOT EXISTS vector;     -- pgvector ≥ 0.8 (HNSW + halfvec)
CREATE EXTENSION IF NOT EXISTS pg_search;  -- ParadeDB BM25
CREATE EXTENSION IF NOT EXISTS ltree;      -- hierarchical labels (Phase 3 doc-chains, Phase 7 lineage_path per architecture §7 / Design 7)

CREATE ROLE kb_app NOLOGIN;                -- application role; RLS applies. Login + password set by env at G4.
GRANT CONNECT ON DATABASE current_database() TO kb_app;
GRANT USAGE ON SCHEMA public TO kb_app;
```

No workspace scope. Runs first; everything else depends on these.

##### `0002_schema_migrations.sql` (no workspace_id — infrastructure)

```sql
CREATE TABLE schema_migrations (
  id          text        PRIMARY KEY,           -- filename, e.g. '0003_audit_log.sql'
  applied_at  timestamptz NOT NULL DEFAULT now()
);
```

Used by `migrations/runner.py` to track which files have been applied. No RLS — this is global infrastructure, not workspace data.

##### `0003_audit_log.sql` (full architecture shape, hash trigger deferred)

Architecture §6 lines 691–706 + §7 lines 850. Partitioned by month on `created_at` from day 1 (cannot retrofit cheaply). Hash chain columns present; the **INSERT trigger that fills them, plus the nightly integrity job**, lands at Phase 9.

```sql
CREATE TABLE audit_log (
  id            uuid         NOT NULL DEFAULT gen_random_uuid(),
  workspace_id  uuid         NOT NULL,
  created_at    timestamptz  NOT NULL DEFAULT now(),
  actor         text         NOT NULL,           -- user_id or 'system:<service>'
  action        text         NOT NULL,           -- e.g. 'schema.create', 'query.run', 'extraction.update'
  entity_type   text,                            -- e.g. 'schema', 'doc', 'entity'
  entity_id     text,
  query_id      uuid,                            -- set on query-time audit rows (Phase 8+)
  payload       jsonb        NOT NULL,
  prev_hash     bytea,                           -- Phase 9 fills via INSERT trigger
  hash          bytea,                           -- Phase 9 fills via INSERT trigger
  PRIMARY KEY (id, created_at)                   -- partition key must be in PK
) PARTITION BY RANGE (created_at);

-- Initial partitions: current month + next month. A cron creates future months at Phase 9.
CREATE TABLE audit_log_2026_05 PARTITION OF audit_log
  FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE audit_log_2026_06 PARTITION OF audit_log
  FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');

CREATE INDEX audit_log_ws_created_idx ON audit_log (workspace_id, created_at DESC);
CREATE INDEX audit_log_ws_query_idx   ON audit_log (workspace_id, query_id);

ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;
CREATE POLICY audit_log_workspace_isolation ON audit_log
  USING (workspace_id = current_setting('app.workspace_id')::uuid);

-- Append-only at the DB-role level. Application role (kb_app) can INSERT/SELECT only.
-- Migration runs as superuser so this doesn't block DDL.
REVOKE UPDATE, DELETE ON audit_log FROM PUBLIC;
```

**Deferred to Phase 9** (do not ship in Phase 0):
- `INSERT` trigger that computes `prev_hash` and `hash`.
- Nightly integrity walker job.
- Partition-rotation cron (creates next month's partition).
- `GET /audit` API + SSE lifecycle endpoint.

##### `0004_idempotency_keys.sql` (workspace-scoped)

```sql
CREATE TABLE idempotency_keys (
  workspace_id  uuid         NOT NULL,
  key           text         NOT NULL,           -- value from Idempotency-Key header
  response      jsonb        NOT NULL,
  status_code   int          NOT NULL,
  created_at    timestamptz  NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, key)
);

CREATE INDEX idempotency_keys_created_idx ON idempotency_keys (created_at);

ALTER TABLE idempotency_keys ENABLE ROW LEVEL SECURITY;
CREATE POLICY idempotency_keys_workspace_isolation ON idempotency_keys
  USING (workspace_id = current_setting('app.workspace_id')::uuid);
```

Phase 1 (schema service) is the first phase to write here. Phase 9 may add a TTL cleanup job.

##### What is **not** shipped at Phase 0

- **`processing_status` / `file_lifecycle`** — removed from Phase 0. Architecture's canonical name is `file_lifecycle`. No `files` exist until Phase 2, so this table lands at Phase 2 (or Phase 9 per architecture §12's reading). Phase 2 G1 makes the call.
- **`corrections`, `entity_overrides`, `schema_field_overrides`, `regression_set`** — Phase 4 / Phase 9.
- **`config_overrides`** — Phase 5 when Hydra + OmegaConf land.
- **Procrastinate's `jobs` table** — Procrastinate's own migrations create this at first worker startup; we don't author its DDL.

Each later phase appends its own `NNNN_<purpose>.sql` files at its own G4. Numbering is global (linear apply order).

#### Migration runner behaviour

`python -m migrations.runner`:
1. Connect to the configured Postgres **as superuser** (DDL needs it; superuser also bypasses RLS so policies don't block table creation).
2. Bootstrap: if `schema_migrations` doesn't exist, apply `0002_schema_migrations.sql` and record it. Then proceed.
3. List `migrations/sql/*.sql` in lexical order.
4. For each file not yet recorded: run it inside a transaction; on success record `(id=filename, applied_at=now())`.
5. Idempotent: re-running with no new files does nothing.

**App vs migration role:** the application uses a non-superuser `kb_app` role created at first migration. RLS applies to `kb_app`; superuser (migrations + admin tasks) bypasses RLS. This split is created in `0001_extensions.sql`.

No rollback DSL — for DDL we write forward fixes. Standard in DDL-heavy systems.

#### Phase 0 G5 — what "green" means

`scripts/verify_phase_0.sh` lands at G5 and runs end-to-end:

1. `cp .env.example .env && docker compose up -d --build`
2. Wait for `db`, `minio`, `api`, `worker` healthy; `migrate` exited 0.
3. `psql` into `db` as superuser:
   - `\dx` includes `vector`, `pg_search`, and `ltree`.
   - `\dt` includes `schema_migrations`, `audit_log`, `idempotency_keys` (only these — no `file_lifecycle`, no `processing_status`).
   - `audit_log` is partitioned: `\d+ audit_log` shows partitioned table with `audit_log_2026_05` and `audit_log_2026_06` partitions.
   - RLS enabled on `audit_log` and `idempotency_keys`: `SELECT relname, relrowsecurity FROM pg_class WHERE relname IN ('audit_log', 'idempotency_keys')` shows `relrowsecurity = t` for both.
   - `\du` includes the `kb_app` role.
4. As `kb_app` role with `SET app.workspace_id = '<some-uuid>'`: insert into `audit_log` succeeds; SELECT only returns rows matching the set workspace.
5. `curl http://localhost:8000/openapi.json` returns 200; `paths` **contains** `/health` and `/ready` (G2 contracts implemented by G4). (Phase-0 era only checked exact equality; per Phase 1a's consistency sweep, later phases will add `/schemas` etc., so the assertion is "contains", not "equals".)
6. `curl -i http://localhost:8000/openapi.json` response includes an `X-Request-Id` header (middleware proof).
7. `pytest tests/` is green (45 tests across health, ready, migrations, RLS, middleware).

#### Sign-off

- Initial G1 signed off 2026-05-22 (commit `d50c1c7`).
- Re-opened 2026-05-23 after gate-transition consistency review; corrections in commit `1ee9738`.
- Second sign-off 2026-05-23 by Aniket. Plan locked. G2 contracts re-validated and also signed off. G3 opens.
- Phase 0 fully closed 2026-05-23; PR #1 merged; tag `phase-0-complete`. Local branch deleted.

---

### 5.2 Phase 1a plan — Schema CRUD foundation (G1 ✅ SIGNED OFF)

> **Status:** G1 ✅ signed off 2026-05-23 by Aniket. Plan locked. Branch: `phase-1a/schemas-crud`.

#### Scope

Phase 1a is the smallest sub-phase of the Phase 1 split: just `schemas` table + 5 bare CRUD endpoints. Phase 1b layers versioning on top; Phase 1c layers hierarchy. This phase establishes the patterns every later API phase will copy.

**In scope:**
- One DDL file: `migrations/sql/0005_schemas.sql`. Workspace-scoped, RLS day-1.
- Five endpoints: `POST /schemas`, `GET /schemas`, `GET /schemas/:id`, `PUT /schemas/:id`, `DELETE /schemas/:id`.
- Soft delete via `lifecycle_state` column (architecture §7 pattern for the `files` table generalized).
- Idempotency-Key honored on POST/PUT/DELETE (backed by Phase 0's `idempotency_keys` table).
- Workspace-scoped uniqueness on `name` (only among `lifecycle_state='active'` rows).
- The patterns are the deliverable as much as the endpoints — they get copied into every Phase-1b/1c/2/3/… endpoint.

**Out of scope (deferred):**
- Versioning. PUT is full-replace at 1a; 1b adds the `schema_versions` table and the "every PUT creates a version" trigger.
- Hierarchy. No `schema_entities` / `schema_fields` / `schema_relationships` writes; those land at 1c.
- NL field descriptions. Phase 1c.
- domain_vocabulary. Phase 5.
- Writing to `audit_log` on each mutation. The append pattern is established at Phase 9 when the hash-chain trigger + read API land; phases 1–8 leave the table un-touched for now.
- Cursor pagination. Offset+limit is fine for `n < 1000`; cursor lands when a workspace breaks that.

#### Decisions (locked at G1)

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | Soft delete vs hard delete | **Soft** — `lifecycle_state text NOT NULL DEFAULT 'active' CHECK (lifecycle_state IN ('active', 'deleted'))` | Architecture §7's `files.lifecycle_state` pattern generalized. Preserves history; audit-log integration at Phase 9 can reference deleted rows. GET endpoints filter to `active` by default. |
| 2 | Schema name uniqueness | **Per-workspace, among active rows only** — partial unique index on `(workspace_id, name) WHERE lifecycle_state = 'active'` | Lets a deleted schema be re-created with the same name. Cross-workspace name collisions are fine (each workspace is its own namespace). |
| 3 | Endpoint URL shape | **Flat** — `/schemas`, not `/workspaces/:wid/schemas` | Workspace is resolved by middleware (Phase 0). Putting it in the URL is redundant and would invite the bug of "URL says A, middleware says B". |
| 4 | PUT semantics | **Full replace** of name + description | Phase 1b will wrap PUT in an "always create a new version" trigger; for 1a it's plain replace. PATCH not exposed; clients send full body. |
| 5 | Pagination | **Offset + limit** — query `?limit=50&offset=0`, max limit 200 | Cursor pagination is overkill for the schema list (workspaces will have 10s, not 1000s). Phase 8+ can swap to cursor when query endpoints need it. |
| 6 | Idempotency | **`Idempotency-Key` header required on POST, optional on PUT/DELETE** | POST creates a new resource — without idempotency, a network retry could create duplicates. PUT/DELETE are naturally idempotent at the resource level but the header is still respected (returns cached body) when present. |
| 7 | Audit-log writes from 1a | **None** | Phase 9 owns the audit-log machinery (hash chain, integrity job, /audit API). Phases 1–8 don't write to it until Phase 9's design pass decides whether to backfill or leave forward-only. Keeps the 1a surface tight. |
| 8 | UUID flavor for `schemas.id` | **UUIDv4 from `gen_random_uuid()`** per api_contracts §0.2 (broadened 2026-05-23 to allow v4 for PKs where time-sortability isn't a query pattern). UUIDv7 stays reserved for `X-Request-Id` and the future `query_id` where monotonic ordering matters. | Postgres has `gen_random_uuid()` built-in. Schemas aren't queried "most recently created"; they're queried by id/name. PK round-trip via app-generated v7 buys nothing here. Same rationale Phase 0 silently applied to `audit_log.id`; the §0.2 wording is now honest about this. |

#### Repo layout delta after Phase 1a G4

```
emerging-kb/
├── migrations/sql/
│   └── 0005_schemas.sql                ← NEW
├── src/kb/
│   ├── api/
│   │   ├── main.py                     ← include schemas_router
│   │   ├── schemas.py                  ← NEW (router; 5 endpoints)
│   │   └── idempotency.py              ← NEW (Idempotency-Key dependency)
│   ├── domain/                         ← NEW package
│   │   ├── __init__.py
│   │   └── schemas.py                  ← NEW (pydantic models + repo functions)
└── tests/
    ├── test_schemas_crud.py            ← NEW (~12 tests)
    ├── test_schemas_rls.py             ← NEW (~4 tests)
    └── test_idempotency.py             ← NEW (~4 tests)
```

#### `0005_schemas.sql` shape (locked at G1)

```sql
CREATE TABLE schemas (
    id              uuid         NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    workspace_id    uuid         NOT NULL,
    name            text         NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    description     text         NOT NULL DEFAULT '',
    lifecycle_state text         NOT NULL DEFAULT 'active'
                                 CHECK (lifecycle_state IN ('active', 'deleted')),
    created_at      timestamptz  NOT NULL DEFAULT now(),
    updated_at      timestamptz  NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX schemas_workspace_name_active_idx
    ON schemas (workspace_id, name)
    WHERE lifecycle_state = 'active';

CREATE INDEX schemas_workspace_lifecycle_idx
    ON schemas (workspace_id, lifecycle_state);

ALTER TABLE schemas ENABLE ROW LEVEL SECURITY;
ALTER TABLE schemas FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS schemas_workspace_isolation ON schemas;
CREATE POLICY schemas_workspace_isolation
    ON schemas
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

GRANT SELECT, INSERT, UPDATE, DELETE ON schemas TO kb_app;
```

#### Endpoint preview (locked at G1; full contracts land at G2)

| Method | Path | Body | Response (success) | Errors |
|---|---|---|---|---|
| `POST` | `/schemas` | `{name, description?}` | `201 Created` `{id, name, description, lifecycle_state, created_at, updated_at}` | `409` duplicate name · `422` validation · `400` malformed |
| `GET` | `/schemas` | — | `200 OK` `{items: [...], total, limit, offset}` | `400` bad query |
| `GET` | `/schemas/:id` | — | `200 OK` schema object | `404` not found (incl. soft-deleted) |
| `PUT` | `/schemas/:id` | `{name, description?}` | `200 OK` updated object | `404` · `409` name collision · `422` validation |
| `DELETE` | `/schemas/:id` | — | `204 No Content` | `404` already deleted |

Every endpoint mounts under the same workspace + request-id + access-log middleware from Phase 0. `Idempotency-Key` header behavior consolidated into a single FastAPI dependency at `kb.api.idempotency`.

#### Phase 1a G5 — what "green" means

`scripts/verify_phase_1a.sh` lands at G5 and adds to the Phase 0 verify checks:
1. After migrate exits 0: `\dt` includes `schemas`; RLS enabled; partial unique index `schemas_workspace_name_active_idx` exists.
2. `curl POST /schemas` with body returns 201 with the full object.
3. `curl GET /schemas` returns the created schema (workspace context is the default sentinel).
4. `curl POST /schemas` with the same name returns 409.
5. `curl POST /schemas` with the same `Idempotency-Key` returns the cached 201 body, not a new row.
6. `curl DELETE /schemas/:id` → 204; subsequent `GET /schemas/:id` → 404; row still in DB with `lifecycle_state='deleted'`.
7. RLS isolation: insert as workspace A, set workspace to B, list returns 0 schemas (verified via `docker compose exec db psql` with explicit `SELECT set_config('app.workspace_id', ...)`).
8. `pytest tests/` green (49 from Phase 0 + ~20 new = ~69 total).

#### Pre-G2 consistency review checklist

Before G2 opens (after this plan is signed off), verify:
- [ ] Architecture §7 lists `schemas` table (no specific column shape mandated) — no conflict.
- [ ] api_contracts §0 conventions all apply unchanged.
- [ ] Phase 1a doesn't preempt Phase 1b decisions: no `current_version_id` column on `schemas` yet (1b adds it as a nullable FK).
- [ ] Phase 1a doesn't preempt Phase 1c decisions: no `schema_entities` references.
- [ ] No `audit_log` writes (Phase 9 owns).

#### Sign-off

When Aniket approves this plan, the Phase 1a G1 cell in §5 flips ⬜ → ✅ and Phase 1a G2 opens (5 endpoint contracts landing in `docs/api_contracts.md` §2). Sign-off recorded in §9.

---

### 5.3 Phase 1b plan — Schema versioning (G1 ✅ SIGNED OFF)

> **Status:** G1 ✅ signed off 2026-05-23 by Aniket. Plan locked. Branch: `phase-1b/schema-versioning` off `main` (Phase 1a merged as PR #2; tag `phase-1a-complete`).

#### Scope

Phase 1b layers an **immutable version history** on top of the Phase 1a CRUD surface. Every PUT becomes a new version; nothing is ever overwritten. A rollback is also a new version (it clones an old snapshot forward) — so the version log is append-only and the schema's "current" pointer is always the head of its own log.

**In scope:**
- One DDL file: `migrations/sql/0006_schema_versions.sql`. Workspace-scoped, RLS day-1.
  - New table `schema_versions` (id, schema_id, workspace_id, version_number, body jsonb, parent_version_number, created_at, kind).
  - Add nullable column `current_version_id uuid` to `schemas` (FK → `schema_versions.id`).
- Three new endpoints: `GET /schemas/:id/versions`, `GET /schemas/:id/versions/:v`, `POST /schemas/:id/versions/:v/rollback`.
- Wrap Phase 1a `POST /schemas` and `PUT /schemas/:id` so each one writes a new `schema_versions` row in the same transaction and updates `schemas.current_version_id`.
- Schema response shape grows one field: `current_version` (the integer version number). The full snapshot body stays at `/versions/:v`.
- Diff computation on read: `GET /schemas/:id/versions/:v` returns `{version, body, diff_from_prior, created_at}` where `diff_from_prior` is computed at read time from `body` and `parent_version`'s `body`. Phase 1b's diff format covers `name` + `description`; Phase 1c extends the same diff machinery to entities/fields/relationships.

**Out of scope (deferred):**
- Optimistic-lock `If-Match` header on PUT (architecture §7 "Concurrent admins edit schema" — last-writer-wins after explicit resolution). Wires up at Phase 10d when Schema Studio surfaces the diff view; until then, two concurrent PUTs both succeed (each becomes its own version — no data loss, just an ordering question the UI resolves).
- `created_by` on versions (no auth yet — column ships nullable; lands when auth phase opens).
- Entity / field / relationship snapshots inside `body` (Phase 1c — the `body jsonb` column shape is forward-compatible, so 1c only changes what 1b writes into it, not the DDL).
- Re-extraction trigger on rollback ("triggers schema-projection re-extraction on changed fields only" per architecture line 791). Phase 6 wires this; 1b just stamps an audit-friendly `kind` value (`'put' | 'rollback'`) so the worker can find them later.
- `audit_log` writes on mutation (Phase 9 owns the hash-chain trigger + read API).
- Cursor pagination on the version list — offset+limit follows the Phase 1a precedent.

#### Decisions (locked at G1)

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | Snapshot vs delta storage | **Full JSON snapshot per version** in `body jsonb`. Diffs computed at read time. | Architecture §7 (line 788) locks this. Storage cost is trivial for a schema body (few KB); read-time diffing is O(1) per pair and matches the Schema Studio "Versions" tab's UX (compare any two versions, not just adjacent). Delta storage would force a re-walk of the chain on every read. |
| 2 | Version identifier shape | **Monotonic integer per schema** (1, 2, 3, …), allocated by the database via `max(version_number)+1 WHERE schema_id=...` inside the PUT transaction. The UUID `id` stays as the row PK (cross-table FKs need it); `version_number` is for humans and URLs. | `/schemas/:id/versions/3` reads better than `/schemas/:id/versions/0193abcd-…`. Integer is unique *per schema*, not globally — collisions across schemas are fine. The `(schema_id, version_number)` unique index gives O(1) lookup by URL. |
| 3 | First-version creation | **POST /schemas creates v1 atomically** in the same transaction as the schema row. `current_version_id` is `NOT NULL` after the POST commits. | A schema with no version is a transient state we'd have to defend against in every read. Doing both inserts in one tx makes "schema exists ⇒ at least one version exists" a hard invariant. The new column is added nullable in DDL (so the migration can run on a schema-less DB), but app code never inserts a schema without immediately inserting v1. |
| 4 | PUT semantics | **Full replace + new version row + bump `current_version_id`** — all in one tx. Response is the updated schema with the new `current_version` number. The new version's `parent_version_number` points at the prior. | The version is the side-effect; the API still feels like a CRUD PUT. Phase 1a's `Idempotency-Key`-replay path bypasses the version write (a replayed PUT returns the cached body without creating a duplicate version), preserving "same logical op = same outcome." |
| 5 | Rollback semantics | **Clone-forward**: `POST /schemas/:id/versions/:v/rollback` reads v's snapshot, inserts a new row at `version_number = current+1` with `body = v.body`, `parent_version_number = current`, `kind = 'rollback'`, bumps `current_version_id`. The OLD `schemas.name`/`description` columns get overwritten with the cloned snapshot. | Append-only history. The original v is never mutated; rolling back to v3 from v7 produces v8 whose `body` equals v3's. Lets you "rollback the rollback" by rolling back to v7. Matches architecture line 789-792 exactly. |
| 6 | What `body jsonb` holds at 1b | **`{"name": "...", "description": "..."}`** — just the fields the Phase 1a schema has. | Forward-compatible: Phase 1c extends this to include entities/fields/relationships *without changing the column shape*. The `jsonb` column is the contract; the value's shape evolves with the phases. We do NOT freeze a Pydantic model for the body — it's literally "snapshot of the schema row + its 1c subtree." |
| 7 | Diff format on read | `{added: [{path, value}], removed: [{path, value}], changed: [{path, old, new}]}` — JSON-Patch-like, *not* RFC 6902 strict (no operation array, no escaping rules). Paths are dotted strings (e.g., `"description"`). At 1b only top-level keys; 1c walks nested entities/fields. | Renders naturally in the Schema Studio UI without client-side post-processing. Strict JSON-Patch would force the client to interpret op order; our format is purely declarative. v1's `diff_from_prior` is `null`. |
| 8 | Idempotency keys | `POST /schemas` still required; `PUT /schemas/:id` optional; **rollback required** (it creates a new version, same shape as POST). PUT/rollback replay returns the cached body — does NOT create a duplicate version. | Replay must be cheap and side-effect-free; the version-write side effect would violate Idempotency-Key semantics. The cached body is the source of truth for the response. |
| 9 | DELETE behaviour | **Unchanged from 1a** — soft-delete the schema; versions stay in the DB but become unreachable via the API (GET on the parent or any version → 404). No per-version delete endpoint. | Versions are an audit trail. Deleting one would defeat the purpose. The parent schema's `lifecycle_state='deleted'` is the cascade gate. |
| 10 | RLS on `schema_versions` | **Own `workspace_id` column + day-1 RLS policy**, mirroring 1a. NOT relying on the parent schema's RLS via FK. | Cross-phase invariant: every workspace-scoped table has its own `workspace_id` and its own policy. Belt-and-braces — a join-bug that crosses workspaces is impossible at the policy layer, not just the app layer. |
| 11 | `schemas.current_version_id` FK constraint | **`ON DELETE SET NULL`**, NOT `CASCADE`. | A version is never hard-deleted in 1b (see #9), so the ON DELETE clause is defensive. Phase 9's eventual purge job (if any) would need the FK to relax, not cascade — preserving the schema row matters more than the pointer integrity. |
| 12 | Concurrent PUT handling | **Last-writer-wins, serialized per-schema** — both PUTs commit, each becomes its own version. Server uses `SELECT ... FOR UPDATE` on the `schemas` row inside the mutation tx so two concurrent PUTs serialize cleanly (no `UNIQUE (schema_id, version_number)` constraint violation on the second one — it sees `max+1` after the first commits). From the client's view: both succeed, version_numbers are contiguous, no "diff conflict" surfaced backend-side. | Optimistic locking via `If-Match` is a UX feature, not a data-integrity feature. The version log loses no data; the only thing at risk is "did the user mean to overwrite the change they didn't see?" — and that's a UI decision (Phase 10d). The row-level lock keeps the allocation race out of the contract entirely. |
| 13 | Rollback no-op handling | **409 `rollback-noop`** — `POST /schemas/:id/versions/:v/rollback` where `v == current_version` returns `409` (not 200 with a fresh-but-identical version row). | A no-op rollback creates pure log noise. Misclicks shouldn't pollute the audit trail. The slug name is opt-in: clients that *want* to clone the head forward can simply `PUT` the current body. |

#### Repo layout delta after Phase 1b G4

```
emerging-kb/
├── migrations/sql/
│   └── 0006_schema_versions.sql       ← NEW: schema_versions table + ALTER schemas ADD current_version_id
├── src/kb/
│   ├── api/
│   │   ├── main.py                    ← include versions_router; register new exception handlers if any
│   │   └── schema_versions.py         ← NEW (router; 3 endpoints)
│   └── domain/
│       ├── schemas.py                 ← MUTATED (POST + PUT now write a version row in-tx; response includes current_version)
│       └── schema_versions.py         ← NEW (snapshot model, version repo functions, diff function)
└── tests/
    ├── test_schema_versions.py        ← NEW (~10 tests: PUT creates version · list · read · diff_from_prior · rollback semantics)
    ├── test_schemas_crud.py           ← MUTATED (POST returns current_version=1; PUT bumps current_version; deletion still works)
    └── specs/phase_1b.md              ← NEW
```

#### `0006_schema_versions.sql` shape (locked at G1)

```sql
CREATE TABLE schema_versions (
    id                       uuid         NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    schema_id                uuid         NOT NULL REFERENCES schemas(id) ON DELETE CASCADE,
    workspace_id             uuid         NOT NULL,
    version_number           int          NOT NULL CHECK (version_number >= 1),
    body                     jsonb        NOT NULL,
    parent_version_number    int          NULL CHECK (parent_version_number IS NULL OR parent_version_number >= 1),
    kind                     text         NOT NULL DEFAULT 'put'
                                          CHECK (kind IN ('post', 'put', 'rollback')),
    created_at               timestamptz  NOT NULL DEFAULT now(),
    UNIQUE (schema_id, version_number)
);

CREATE INDEX schema_versions_workspace_idx ON schema_versions (workspace_id);
CREATE INDEX schema_versions_schema_created_idx ON schema_versions (schema_id, created_at DESC);

ALTER TABLE schema_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE schema_versions FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS schema_versions_workspace_isolation ON schema_versions;
CREATE POLICY schema_versions_workspace_isolation
    ON schema_versions
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

GRANT SELECT, INSERT ON schema_versions TO kb_app;
-- No UPDATE / DELETE: versions are immutable. Soft-delete the parent schema instead.

ALTER TABLE schemas
    ADD COLUMN current_version_id uuid NULL
    REFERENCES schema_versions(id) ON DELETE SET NULL;

CREATE INDEX schemas_current_version_idx ON schemas (current_version_id);
```

#### Endpoint preview (locked at G1; full contracts land at G2)

| Method | Path | Body | Response (success) | Errors |
|---|---|---|---|---|
| `POST` | `/schemas` (mutated from 1a) | `{name, description?}` | `201` `{id, name, description, lifecycle_state, current_version: 1, created_at, updated_at}` | unchanged from 1a |
| `PUT` | `/schemas/:id` (mutated from 1a) | `{name, description?}` | `200` schema with bumped `current_version` | unchanged from 1a |
| `GET` | `/schemas/:id/versions` | — | `200` `{items: [{version, kind, created_at}], total, limit, offset}` | `404` schema not found · `400` bad query |
| `GET` | `/schemas/:id/versions/:v` | — | `200` `{version, kind, body, parent_version, diff_from_prior, created_at}` | `404` schema OR version not found |
| `POST` | `/schemas/:id/versions/:v/rollback` | `{}` (empty or absent) | `200` updated schema (now points at the cloned-forward version) | `404` schema or v not found · `409` if v is already the current version (no-op rollback rejected) |

Every endpoint reuses Phase 0+1a middleware (workspace, request-id, access log) and the same problem+json error shape from `kb.api.errors`.

#### Phase 1b G5 — what "green" means

`scripts/verify_phase_1b.sh` lands at G5 and adds to the Phase 0 + 1a verify checks:
1. After migrate exits 0: `\dt` includes `schema_versions`; RLS enabled+forced; `schemas.current_version_id` column exists with FK to `schema_versions.id`.
2. `curl POST /schemas` returns 201 with `current_version: 1`; `SELECT count(*) FROM schema_versions` shows 1 row.
3. `curl PUT /schemas/:id` returns 200 with `current_version: 2`; row count is 2; the v2 row's `parent_version_number = 1`.
4. `curl GET /schemas/:id/versions` returns both, newest-first.
5. `curl GET /schemas/:id/versions/2` returns `{version: 2, body: {...}, parent_version: 1, diff_from_prior: {changed: [{path:"description", old:..., new:...}], ...}}`.
6. `curl POST /schemas/:id/versions/1/rollback` returns 200; `current_version` is now 3; v3's `body` equals v1's `body`; v3's `kind='rollback'`.
7. RLS: insert as workspace A, list versions as workspace B → 404 (NOT 403 — same existence-leak avoidance as 1a).
8. `pytest tests/` green: 49 (Phase 0) + 29 (Phase 1a) + ~10 (Phase 1b) = ~88 total.

#### Pre-G2 consistency review checklist

Before G2 opens (after this plan is signed off), verify:
- [ ] Architecture §7 line 788 storage strategy honoured (full snapshot + diff on read + rollback as clone-forward).
- [ ] Architecture line 164 `current_version_id` pointer name matches our column name (yes — same name).
- [ ] Architecture line 292 optimistic locking via `schema_versions.updated_at` — explicitly deferred (decision #12) with rationale; need to confirm there's no other architecture passage that requires it day-1.
- [ ] api_contracts §0 conventions all apply unchanged.
- [ ] Phase 1b doesn't preempt Phase 1c decisions: `body jsonb` is the column; what we *write* into it stays scalar in 1b.
- [ ] Phase 1b doesn't preempt Phase 6 decisions: `kind='rollback'` is just a marker — no worker dispatch wired.
- [ ] No `audit_log` writes (Phase 9 owns).

#### Sign-off

When Aniket approves this plan, the Phase 1b G1 cell in §5 flips 🟡 → ✅ and Phase 1b G2 opens (3 new endpoint contracts + 2 mutated contracts landing in `docs/api_contracts.md` §3 — note: §3 is currently a placeholder index; Phase 1b G2 fills it in). Sign-off recorded in §9.

---

### 5.4 Phase 1c plan — Schema hierarchy (G1 ✅ SIGNED OFF)

> **Status:** G1 ✅ signed off 2026-05-23 by Aniket. Plan locked. Branch: `phase-1c/schema-hierarchy` off `main` (Phase 1b merged as PR #3; tag `phase-1b-complete`).

#### Scope

Phase 1c layers the **entity-type tree** on top of Phase 1b's versioning. Adds three new tables, 11 nested endpoints, and extends Phase 1b's version-snapshot `body` from `{name, description}` to the full subtree. Every nested CRUD writes a new `schema_versions` row — rollback now restores the entire hierarchy. Foundations for Phase 5 (open extraction) + Phase 6 (schema-driven extraction).

**In scope:**
- `migrations/sql/0007_schema_hierarchy.sql` — three new workspace-scoped + RLS-day-1 tables: `schema_entities`, `schema_fields`, `schema_relationships`.
- 11 endpoints under `/schemas/:id/{entities, entities/:eid/fields, relationships}` (entity + field: full CRUD = 4 each; relationship: POST/GET-list/DELETE = 3 — PUT deferred since soft-delete + re-create suffices for typed edges).
- `nl_description` field on `schema_fields` — the prompt that Phase 6's Gemini extractor will consume.
- `kind ∈ {contains, part_of, references, associates, attribute_link}` enum on relationships (architecture line 794).
- `cardinality`, `cascade_delete`, `single_parent` columns — recorded only; Phase 6 enforces during extraction-time row writes.
- 1b's `schema_versions.body` JSON shape grows to include the full subtree; no DDL change needed (jsonb forward-compatible per 1b decision #6).
- Rollback path extended: replays entity/field/relationship rows from the snapshot in a single tx.
- Same patterns (soft-delete via `lifecycle_state`, Idempotency-Key required on POSTs + rollback, RLS day-1 with own `workspace_id` column on every new table).

**Out of scope (deferred):**
- `extracted_entities` table + `lineage_path` ltree column — Phase 5/6 (the runtime tree, populated by the extraction worker).
- Helper endpoints `/entities/:id/{descendants, ancestors, siblings, breadcrumb}` — Phase 8 (query phase).
- DB-level `single_parent` trigger — Phase 6 if extraction-time conflicts arise; 1c records the intent only.
- Re-extraction trigger on schema change — Phase 6.
- domain_vocabulary (synonyms, acronyms) — Phase 5.
- `audit_log` writes on hierarchy mutation — Phase 9.
- Field-type validation beyond a small core set — Phase 6 when extraction needs richer typing.

#### Decisions (locked at G1)

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | New tables vs jsonb columns | **Three new tables** — `schema_entities`, `schema_fields`, `schema_relationships`. Each with own `workspace_id` + own RLS policy. | Foreign keys + UNIQUE constraints + indexed lookup are required for Phase 6 extraction (which joins entities + fields by name + type). Nested jsonb on `schemas` would be cheap to write but slow + brittle to query. Worth one extra migration to do it right. |
| 2 | Field type enum at 1c | `string / number / boolean / date / datetime` — small core set; CHECK constraint enforces. | Phase 6 may add richer types (currency, email, list_X). Locking 5 now beats inventing 12; expansion is additive. |
| 3 | NL field description shape | `nl_description text NOT NULL DEFAULT ''`. The prompt Phase 6 sends to Gemini. | Required column makes "empty prompt" explicit (you have to opt out). Not nullable so a corpus-wide grep can identify under-prompted fields. |
| 4 | Relationship `kind` enum | `contains / part_of / references / associates / attribute_link` per architecture line 794. CHECK constraint. | Verbatim from the locked architecture. `contains` and `part_of` drive Phase 6's lineage_path computation; the others are typed edges in the entity graph (Phase 7 identity resolution + Phase 8 query). |
| 5 | `single_parent` + `cascade_delete` enforcement | **Recorded only at 1c**; Phase 6 enforces during extraction-time row writes. | Schema-edit-time enforcement would require walking the existing extracted_entities tree, which doesn't exist yet. Recording intent now lets Phase 6 enforce when it has data to enforce against. |
| 6 | Soft delete on entities/fields/relationships | Same `lifecycle_state` pattern as 1a's `schemas`. Partial unique on `(parent_id, name) WHERE lifecycle_state='active'`. | Consistent with Phase 1a/1b precedent. Deleted rows preserved for audit. |
| 7 | Every nested CRUD writes a new `schema_versions` row | Yes — the whole-subtree snapshot is the version's `body`. Versions are coarse-grained: one row per schema change, no matter which sub-resource changed. | A 5-version history showing "added entity X · added field Y · added relationship Z · changed field Y's type · rolled back" is what Schema Studio's Versions tab needs. Per-sub-resource version logs would be operationally noisy. |
| 8 | Rollback restores the full hierarchy | Rollback reads the target version's `body`, soft-deletes all current children entities/fields/relationships, INSERTs fresh rows from the snapshot, writes the new `kind='rollback'` version row. All in one tx. | Maintains the 1b invariant "rollback = clone-forward" — the new version is fully self-contained. Cost: rollback is O(subtree size); for our scale (a few hundred entities + fields per schema) trivial. |
| 9 | Endpoint URL shape | Nested under `/schemas/:id/`: `/entities`, `/entities/:eid/fields`, `/relationships`. | Resource ownership matches DB FK; no separate "which schema does this entity belong to?" round-trip. |
| 10 | Idempotency-Key | Required on all POSTs + rollback (unchanged from 1b); optional on PUT/DELETE. | Consistent rule across the API. |
| 11 | RLS on all 3 new tables | Each carries own `workspace_id` + own `CREATE POLICY` (same `current_setting('app.workspace_id')` predicate). | Belt-and-braces (1a/1b decision #10 continued — invariant grows to 7 workspace-scoped tables). |
| 12 | Snapshot body shape at 1c | `{name, description, entities: [{name, description, fields: [{name, type, nl_description, is_required}]}], relationships: [{name, kind, from, to, cardinality, cascade_delete, single_parent}]}`. References between entities use entity `name` (not UUID) so the snapshot is portable across rollbacks (a re-created entity gets a new UUID; name stays). | Self-contained + readable + diffable. Phase 6 reads it for extraction prompts. |
| 13 | Diff format extension | Same `{added, removed, changed}` shape; paths become dotted like `entities.File.fields.line_total.nl_description`. `compute_diff` recurses into the new keys. | The format is unchanged from 1b; only the path depth grows. |

#### Repo layout delta after Phase 1c G4

```
emerging-kb/
├── migrations/sql/
│   └── 0007_schema_hierarchy.sql      ← NEW
├── src/kb/
│   ├── api/
│   │   ├── main.py                    ← include hierarchy_router
│   │   └── schema_hierarchy.py        ← NEW (9 endpoints; entities · fields · relationships)
│   └── domain/
│       ├── schema_hierarchy.py        ← NEW (pydantic + repo functions + snapshot builder)
│       ├── schema_versions.py         ← MUTATED (compute_diff recurses; snapshot builder accepts subtree)
│       └── schemas.py                 ← MUTATED (POST + PUT + rollback now write full-subtree snapshot)
└── tests/
    ├── test_schema_entities.py        ← NEW (~7 tests)
    ├── test_schema_fields.py          ← NEW (~7 tests)
    ├── test_schema_relationships.py   ← NEW (~6 tests)
    ├── test_schema_hierarchy_versions.py ← NEW (~7 tests — snapshot/rollback/diff on subtree)
    └── specs/phase_1c.md              ← NEW
```

#### `0007_schema_hierarchy.sql` shape (locked at G1)

```sql
-- schema_entities — entity types declared within a schema.
CREATE TABLE schema_entities (
    id              uuid         NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    schema_id       uuid         NOT NULL REFERENCES schemas(id) ON DELETE CASCADE,
    workspace_id    uuid         NOT NULL,
    name            text         NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    description     text         NOT NULL DEFAULT '',
    lifecycle_state text         NOT NULL DEFAULT 'active'
                                 CHECK (lifecycle_state IN ('active', 'deleted')),
    created_at      timestamptz  NOT NULL DEFAULT now(),
    updated_at      timestamptz  NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX schema_entities_schema_name_active_idx
    ON schema_entities (schema_id, name) WHERE lifecycle_state = 'active';
CREATE INDEX schema_entities_workspace_idx ON schema_entities (workspace_id);
ALTER TABLE schema_entities ENABLE ROW LEVEL SECURITY;
ALTER TABLE schema_entities FORCE ROW LEVEL SECURITY;
CREATE POLICY schema_entities_workspace_isolation ON schema_entities
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON schema_entities TO kb_app;

-- schema_fields — fields on each entity, with NL extraction prompts.
CREATE TABLE schema_fields (
    id              uuid         NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    entity_id       uuid         NOT NULL REFERENCES schema_entities(id) ON DELETE CASCADE,
    workspace_id    uuid         NOT NULL,
    name            text         NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    type            text         NOT NULL
                                 CHECK (type IN ('string','number','boolean','date','datetime')),
    nl_description  text         NOT NULL DEFAULT '',
    is_required     boolean      NOT NULL DEFAULT false,
    lifecycle_state text         NOT NULL DEFAULT 'active'
                                 CHECK (lifecycle_state IN ('active', 'deleted')),
    created_at      timestamptz  NOT NULL DEFAULT now(),
    updated_at      timestamptz  NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX schema_fields_entity_name_active_idx
    ON schema_fields (entity_id, name) WHERE lifecycle_state = 'active';
CREATE INDEX schema_fields_workspace_idx ON schema_fields (workspace_id);
ALTER TABLE schema_fields ENABLE ROW LEVEL SECURITY;
ALTER TABLE schema_fields FORCE ROW LEVEL SECURITY;
CREATE POLICY schema_fields_workspace_isolation ON schema_fields
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON schema_fields TO kb_app;

-- schema_relationships — typed edges between entity types within a schema.
CREATE TABLE schema_relationships (
    id              uuid         NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    schema_id       uuid         NOT NULL REFERENCES schemas(id) ON DELETE CASCADE,
    workspace_id    uuid         NOT NULL,
    name            text         NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    from_entity_id  uuid         NOT NULL REFERENCES schema_entities(id) ON DELETE CASCADE,
    to_entity_id    uuid         NOT NULL REFERENCES schema_entities(id) ON DELETE CASCADE,
    kind            text         NOT NULL
                                 CHECK (kind IN ('contains','part_of','references','associates','attribute_link')),
    cardinality     text         NOT NULL DEFAULT 'one_to_many'
                                 CHECK (cardinality IN ('one_to_one','one_to_many','many_to_many')),
    cascade_delete  boolean      NOT NULL DEFAULT false,
    single_parent   boolean      NOT NULL DEFAULT true,
    lifecycle_state text         NOT NULL DEFAULT 'active'
                                 CHECK (lifecycle_state IN ('active', 'deleted')),
    created_at      timestamptz  NOT NULL DEFAULT now(),
    updated_at      timestamptz  NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX schema_relationships_schema_name_active_idx
    ON schema_relationships (schema_id, name) WHERE lifecycle_state = 'active';
CREATE INDEX schema_relationships_workspace_idx ON schema_relationships (workspace_id);
CREATE INDEX schema_relationships_from_idx ON schema_relationships (from_entity_id);
CREATE INDEX schema_relationships_to_idx ON schema_relationships (to_entity_id);
ALTER TABLE schema_relationships ENABLE ROW LEVEL SECURITY;
ALTER TABLE schema_relationships FORCE ROW LEVEL SECURITY;
CREATE POLICY schema_relationships_workspace_isolation ON schema_relationships
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON schema_relationships TO kb_app;
```

#### Endpoint preview (locked at G1; full contracts at G2)

| Method | Path | Notes |
|---|---|---|
| `POST` | `/schemas/:id/entities` | Create entity type. Writes new schema version. |
| `GET` | `/schemas/:id/entities` | List active entity types in schema. |
| `PUT` | `/schemas/:id/entities/:eid` | Replace entity name + description. |
| `DELETE` | `/schemas/:id/entities/:eid` | Soft-delete entity type. |
| `POST` | `/schemas/:id/entities/:eid/fields` | Create field on entity. |
| `GET` | `/schemas/:id/entities/:eid/fields` | List active fields on entity. |
| `PUT` | `/schemas/:id/entities/:eid/fields/:fid` | Replace field name+type+nl_description+is_required. |
| `DELETE` | `/schemas/:id/entities/:eid/fields/:fid` | Soft-delete field. |
| `POST` | `/schemas/:id/relationships` | Create typed edge. |
| `GET` | `/schemas/:id/relationships` | List active relationships. |
| `DELETE` | `/schemas/:id/relationships/:rid` | Soft-delete relationship. |

(Listing endpoints kept lightweight; PUT on relationships deferred to a later phase if needed — soft-delete + recreate is the easier path here.)

#### Phase 1c G5 — what "green" means

`scripts/verify_phase_1c.sh` lands at G5 and adds to the Phase 0+1a+1b verify checks:
1. After migrate exits 0: `\dt` includes `schema_entities`, `schema_fields`, `schema_relationships`; RLS forced; partial unique indexes on `(parent_id, name) WHERE lifecycle_state='active'`.
2. `curl POST /schemas/:id/entities` → 201; subsequent `GET /schemas/:id/versions/:current` body includes the new entity in `entities[]`.
3. `curl POST .../fields` → 201; version body shows the field nested under its entity.
4. `curl POST .../relationships` with `kind=contains` → 201; version body shows the edge with `cardinality + cascade_delete + single_parent`.
5. `curl POST /schemas/:id/versions/:v/rollback` to a pre-hierarchy version → schema's entities/fields/relationships are restored to the snapshot state; row count assertions via superuser psql.
6. RLS isolation: every new resource 404s for workspace B when created in workspace A.
7. `pytest tests/` green: 106 (Phase 0+1a+1b) + ~30 new = ~136 total.

#### Pre-G2 consistency review checklist

Before G2 opens (after this plan is signed off), verify:
- [ ] Architecture line 793–796 listing — verbatim coverage of `schema_entities`, `schema_fields`, `schema_relationships` + kind/cardinality/cascade_delete/single_parent.
- [ ] No Phase 5 leak: no `domain_vocabulary` references in 1c code.
- [ ] No Phase 6 leak: no extraction-time enforcement of `cascade_delete` / `single_parent`.
- [ ] No Phase 8 leak: no lineage helper endpoints.
- [ ] Phase 1b invariants intact: `schema_versions` table still GRANT SELECT+INSERT only (after the REVOKE from 1b G5).
- [ ] No `audit_log` writes (Phase 9 owns).
- [ ] api_contracts §0 conventions all apply unchanged.

#### Sign-off

When Aniket approves this plan, the Phase 1c G1 cell in §5 flips 🟡 → ✅ and Phase 1c G2 opens (9 endpoint contracts landing in `docs/api_contracts.md` §4 — the current §4 placeholder index shifts to §5, changelog to §6). Sign-off recorded in §9.

---

### Wave B (build if time)

| Phase | Description | G1 | G2 | G3 | G4 | G5 |
|---|---|---|---|---|---|---|
| **13** | NotebookLM-style artifacts (briefing doc, FAQ, mind map, suggested Qs) | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |
| **14** | HippoRAG-2 graph index for multi-hop | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |
| **14b** | UI — Playground depth (Compare configs A/B + advanced retrieval controls; basic ships Wave A Phase 12) | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |
| **B1** | Batch query mode (Hebbia spreadsheet pattern) | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |
| **B2** | Opt-in `deep_research` agentic mode (Search-o1 / ReAct, capped) | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |
| **B3** | DSPy prompt optimization layer | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |
| **B4** | Multi-agent decomposition for complex Q-mode | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ |

### Wave C — cited as future work, not built

Phases 15–24 per `architecture.md` §12. Tracked here only as a reminder of intentional descope.

---

## 6. API contracts — index

> Filled in as each phase enters G2. Authoritative file: [`docs/api_contracts.md`](api_contracts.md).

| Phase | Endpoints planned | Contract status |
|---|---|---|
| 0 | `GET /health`, `GET /ready` | ✅ signed off 2026-05-23 |
| 1 | `GET/POST/PUT/DELETE /schema`, `GET /schema/versions`, hierarchy endpoints | ⬜ |
| 2–7 | Mostly internal workers; admin endpoints TBD at G1 | ⬜ |
| 8 | `POST /query`, `POST /chat`, `GET /chat/:id/stream` (SSE) | ⬜ |
| 9 | `GET /upload/:id/status` (SSE), `GET /audit` | ⬜ |

---

## 7. Test plan — index

> One file per phase under `tests/specs/`. Each phase's G3 produces:
> - A spec markdown (test names, intent, fixtures)
> - Failing test skeletons in `tests/`
> - A note in this tracker linking spec → test files

| Phase | Spec | Tests | G3 status |
|---|---|---|---|
| 0 | [tests/specs/phase_0.md](../tests/specs/phase_0.md) | [test_health.py](../tests/test_health.py) · [test_ready.py](../tests/test_ready.py) · [test_migrations.py](../tests/test_migrations.py) · [test_rls.py](../tests/test_rls.py) · [test_middleware.py](../tests/test_middleware.py) | ✅ signed off 2026-05-23 (49 tests, all green) |
| 1a | [tests/specs/phase_1a.md](../tests/specs/phase_1a.md) | [test_schemas_crud.py](../tests/test_schemas_crud.py) · [test_schemas_rls.py](../tests/test_schemas_rls.py) · [test_idempotency.py](../tests/test_idempotency.py) | ✅ 29 tests green (post-G4); pytest authoritative |
| 1b | [tests/specs/phase_1b.md](../tests/specs/phase_1b.md) | [test_schema_versions.py](../tests/test_schema_versions.py) · [test_schemas_crud.py](../tests/test_schemas_crud.py) (additive) · [test_idempotency.py](../tests/test_idempotency.py) (additive) | ✅ 28 new tests green (post-G5); suite total 106 |
| 1c | tests/specs/phase_1c.md | tests/test_schema_hierarchy.py · tests/test_schema_entities_*.py | ⬜ |
| ... | | | |

---

## 8. Run / verify — index

> Each phase's G5 produces a script (`scripts/verify_<phase>.sh`) or a manual checklist appended to this tracker. Outputs are summarized here.

| Phase | Verify script | Last run | Result |
|---|---|---|---|
| 0 | [scripts/verify_phase_0.sh](../scripts/verify_phase_0.sh) | 2026-05-23 (post Phase 1a code) | ✅ 16/16 (still green after Phase 1a's code landed) |
| 1a | [scripts/verify_phase_1a.sh](../scripts/verify_phase_1a.sh) | 2026-05-23 (post Phase 1b code) | ✅ 17/17 (compose smoke + 9 schemas assertions + 29 pytest) — still green after Phase 1b code landed |
| 1b | [scripts/verify_phase_1b.sh](../scripts/verify_phase_1b.sh) | 2026-05-23 | ✅ 21/21 (compose smoke + 5 DDL assertions on schema_versions + 11 HTTP/rollback/RLS curl checks + openapi check + Phase-1b pytest 52) |
| 1c | scripts/verify_phase_1c.sh | — | — |
| ... | | | |

---

## 9. Change log

> Append-only. Every gate transition, scope change, or plan revision lands here.

| Date | Change | By |
|---|---|---|
| 2026-05-22 | Build Tracker created. Pre-Phase-0 review opened. | Aniket |
| 2026-05-22 | Added G1.5 (Visual prototype) gate. Re-IA'd UI: chat-first home + Studio (Schema/Extraction/Playground) + Admin (Dashboard/Audit) sidebar. Studio vision per `archive/Problem_2.md` integrated. | Aniket |
| 2026-05-22 | Added G1.5b (Visual QA / Playwright) sub-gate + reusable `prototype/qa_checklist.md` template. Discipline: every prototype screen runs through Playwright screenshots + auto-checks at desktop/tablet/mobile + section-by-section manual review before sign-off. | Aniket |
| 2026-05-22 | Added §0.2 cross-cutting design rules (schema-everywhere, Doc Detail universal, ⌘K reachable, streaming over spinners, trust signals, sidebar/top-bar identical). | Aniket |
| 2026-05-22 | Added §0.3 user-facing copy discipline. No Wave labels, phase numbers, internal design names (Design 1–9), library names (Hydra, OmegaConf, RAPTOR, HippoRAG, ColPali, Procrastinate), or `gaps_design.md §X` citations in production UI copy. QA gates this at G1.5b. | Aniket |
| 2026-05-22 | Added G1.6 (Wiring inventory) gate. Every interactive element on every screen → planned backend endpoint or marked client-only. `prototype/wiring_inventory.md` produced — ~210 elements audited, ~100 unique endpoints across 16 groups. Becomes the input set for G2. | Aniket |
| 2026-05-22 | All 10 prototype screens built, QA-passed, signed off. Polish pass applied: doc names → Doc Detail · field pills → Schema Studio · doc-type badges → Schema Studio · query IDs → Audit · cited sources → Doc Detail. Cross-cutting §0.2 rules verified on every screen. | Aniket |
| 2026-05-22 | Locked design back-ported into `docs/ui_design.md`. Prior version preserved at `docs/archive/ui_design_v1.md`. **Pre-Phase-0 review complete. Phase 0 G1 ready to open.** | Aniket |
| 2026-05-22 | **Phase 0 G1 OPEN.** Branched `phase-0/repo-skeleton`. Plan section §5.1 drafted: single-package `src/kb/` layout, ParadeDB image (bundles pgvector + pg_search), raw-SQL migration runner, narrow lifecycle DDL (extensions + `schema_migrations`, `audit_log`, `processing_status`, `idempotency_keys`), `uv`/`ruff`/`pyright`/`pytest` tooling, FastAPI skeleton (routes open at G2). Awaiting sign-off. | Aniket |
| 2026-05-22 | **Phase 0 G1 ✅ SIGNED OFF.** Plan locked. Phase 0 G2 opens — API contracts for `/health` + `/ready` to land in `docs/api_contracts.md`. | Aniket |
| 2026-05-22 | **Phase 0 G2 drafted.** Created `docs/api_contracts.md` with §0 conventions (RFC 9457 errors, UUIDv7 IDs, ISO-8601 timestamps, idempotency headers, status code map) and §1 Phase 0 contracts: `GET /health` (liveness — process up, no dependency checks) and `GET /ready` (readiness — db + minio + migrations check, 503 with `application/problem+json` on fail, parallel checks with 5s budget). Awaiting sign-off. | Aniket |
| 2026-05-23 | **Gate-transition consistency review (G1+G2) ran before opening G3.** Six drifts surfaced against `docs/architecture.md`: (A) lifecycle tables had no `workspace_id` + RLS — architecture §7 mandates RLS day 1; (B) no FastAPI workspace middleware; (C) no X-Request-Id middleware (G2 §0.8 promised, G1 omitted); (D) `audit_log` shape under-specified vs architecture §6 (partitioning, hash columns, role grants); (E) `processing_status` was a fabrication — canonical name is `file_lifecycle`, belongs to Phase 2+; (F) Phase 0 ↔ Phase 9 split implicit — needed explicit reconciliation. Tech stack, gate discipline, branch+commit conventions all clean. | Aniket |
| 2026-05-23 | **Phase 0 G1 re-opened** to apply consistency fixes. §5.1 rewritten: lifecycle DDL shrinks to four files (`0001_extensions`, `0002_schema_migrations`, `0003_audit_log` full partitioned shape, `0004_idempotency_keys` workspace-scoped); RLS day-1 added as decision #6; audit-log shape as #7; Phase 0↔9 split as #8; `src/kb/api/middleware.py` added to layout (workspace context + X-Request-Id); G5 acceptance updated to verify partitions + RLS + request-id header. G2 contracts unchanged (re-validated against revised G1). Awaiting second sign-off. | Aniket |
| 2026-05-23 | **Phase 0 G1 ✅ and G2 ✅ both signed off.** Corrected §5.1 plan locked. G2 contracts in `docs/api_contracts.md` locked. G3 opens: test specs + red skeletons for `/health`, `/ready`, migration runner, RLS isolation, middleware. | Aniket |
| 2026-05-23 | **Phase 0 G3 drafted.** Created `tests/specs/phase_0.md` (test spec — 5 buckets, 41 test functions, testcontainers fixture strategy) + 6 skeleton files (`conftest.py`, `test_health.py`, `test_ready.py`, `test_migrations.py`, `test_rls.py`, `test_middleware.py`). Skeletons are RED — they import from `kb.*` modules that land at G4. Every G2 contract has a matching test; every G1 decision (RLS day-1, partitioning, middleware) has a matching test. Awaiting sign-off. | Aniket |
| 2026-05-23 | **Post-G3 cross-gate consistency sweep (G1↔G2↔G3↔architecture).** Five drifts fixed in one commit: (A) G1 plan §5.1 G5 acceptance #5 was stale post-G2 — said `/openapi.json` returns empty paths, but G4 will mount `/health` + `/ready`; corrected. (B) Spec test count was 41 (claimed) vs 45 (actual recount of first draft); corrected. (C) §3 missing `testcontainers-python` + `freezegun` (test fixtures); added as new row. (D) `ltree` extension missing from `0001_extensions.sql` per architecture §7 (required for Phase 3 doc-chains + Phase 7 lineage_path); added, also added `kb_app` role creation in 0001. §3 DB row updated to include ltree. (E) Unused fixtures (`set_workspace`, `frozen_time`) removed from `conftest.py`. Plus 4 new tests landed: `test_health_returns_json_content_type` (api_contracts §0.1) + 3 per-check timeout tests on `/ready` (api_contracts §1.2 check table). Final test count: 49 (was 45 at G3 first draft). | Aniket |
| 2026-05-23 | **Phase 0 G3 ✅ signed off.** Spec + 49 red skeletons locked. **G4 opens.** Order of build commits planned: (1) project bootstrap (pyproject.toml, .env.example, .gitignore, Dockerfile); (2) migrations (runner + 4 SQL files); (3) shared modules (config, db pool, logging, storage); (4) FastAPI app + middleware; (5) /health + /ready endpoints + readiness checks; (6) Procrastinate worker entrypoint; (7) docker-compose.yml + scripts/bootstrap_db.sh. Each commit makes some red tests green. | Aniket |
| 2026-05-23 | **Phase 0 G4 — code landed (5 commits, not yet run).** Commits on `phase-0/repo-skeleton`: `1dec6f5` bootstrap (pyproject, Dockerfile, .env, src/kb stub) · `c0d020c` migrations (runner + 4 SQL) · `18b6ea8` shared modules (config, logging, db pool, storage) · `944c61f` FastAPI app + middleware + /health + /ready · `1dbd08e` worker + compose + bootstrap script + kb_app password wiring. **Tests not yet verified** — local env lacks uv + Python 3.12; will run at G5 (`scripts/verify_phase_0.sh`). G4 cell stays 🟡 until that suite goes green. | Aniket |
| 2026-05-23 | **Phase 0 G4 debugging pass — all 49 tests pass.** Installed `uv` via Homebrew, ran pytest against fresh paradedb + minio testcontainers, fixed issues surfaced (commit `17baa1c`): PG utility commands (ALTER ROLE, SET LOCAL) don't accept bind parameters → use `psycopg.sql.Literal` + `SELECT set_config(...)`; testcontainer SQLAlchemy-style URLs need stripping for psycopg3; container `.stop()/.start()` in tests reassigns ports and breaks subsequent tests → replaced with monkey-patched check functions; configure_logging now called in build_app (ASGITransport doesn't trigger lifespan); structlog `cache_logger_on_first_use=False` so test-time processor swaps take effect; conftest now sets full MinIO creds + clears lru_caches; 0003/0004 made idempotent (IF NOT EXISTS, DROP POLICY IF EXISTS) so bootstrap test re-application works. Suite runtime ~19s. | Aniket |
| 2026-05-23 | **Phase 0 G5 ✅ GREEN — full stack verified.** Authored `scripts/verify_phase_0.sh` (commit `f9a73fa`); 16 checks pass end-to-end: docker compose build + up, migrate one-shot exits 0, db/minio/api healthy, vector+pg_search+ltree installed, lifecycle tables + partitions + RLS state correct, kb_app role exists, /health + /ready + /openapi.json + X-Request-Id all behave per contract, pytest 49/49. Additional bug fixes landed in the same commit: Dockerfile missing README+LICENSE COPY (hatchling needs them); base compose was binding db/minio host ports → conflicts with developers' other infra → moved to `docker-compose.override.yml`; Procrastinate v3 PsycopgConnector import was under non-existent `contrib.psycopg` → use `procrastinate.PsycopgConnector` directly; 0002 missing explicit GRANT SELECT on schema_migrations for kb_app (ALTER DEFAULT PRIVILEGES in 0001 doesn't retroactively cover bootstrap-created tables). **Phase 0 complete; ready to open PR.** | Aniket |
| 2026-05-23 | **Phase 1 split into 1a/1b/1c.** Architecture §12 lists "CRUD, versioning, NL field descriptions, hierarchy" as Phase 1's scope — four distinct deliverables. Per the discipline (sub-phase splits memory entry), each becomes its own G1→G5 cycle with its own branch + PR. 1a = `schemas` CRUD foundation (5 endpoints). 1b = `schema_versions` + versioning endpoints. 1c = `schema_entities` + `schema_fields` + `schema_relationships` hierarchy + NL descriptions. domain_vocabulary deferred to Phase 5; re-extraction trigger on rollback stubbed for Phase 6. | Aniket |
| 2026-05-23 | **Phase 0 merged.** PR #1 squash-merged into `main`. Tag `phase-0-complete` pushed. Local `phase-0/repo-skeleton` branch deleted. | Aniket |
| 2026-05-23 | **Phase 1a G1 OPEN.** Branched `phase-1a/schemas-crud` from `main`. Plan section §5.2 drafted: `0005_schemas.sql` (workspace-scoped, RLS day-1, partial unique index on (workspace_id, name) WHERE lifecycle_state='active'); 5 endpoints (POST/GET-list/GET/PUT/DELETE) with offset+limit pagination; Idempotency-Key required on POST, optional on PUT/DELETE; soft delete via lifecycle_state; UUIDv4 for `schemas.id` (UUIDv7 reserved for X-Request-Id). Audit-log writes explicitly deferred to Phase 9. Awaiting sign-off. | Aniket |
| 2026-05-23 | **Phase 1a G1 ✅ signed off. G2 drafted.** `api_contracts.md` §2 added: schema resource shape (no workspace_id field on responses — clients know their own); 5 endpoints with per-endpoint error tables using RFC 9457 `type` slugs (`schema-name-conflict`, `not-found`, `validation-error`, `bad-request`, `missing-idempotency-key`); §2.8 explicit out-of-scope list to prevent 1b/1c leak. Placeholder index in api_contracts §3 split Phase 1 row → 1a/1b/1c. | Aniket |
| 2026-05-23 | **Phase 1a G2 ✅ signed off. G3 drafted.** Created `tests/specs/phase_1a.md` (3 buckets: CRUD 17 · RLS 4 · idempotency 4 = ~25 tests) + 3 red skeleton files (`test_schemas_crud.py`, `test_schemas_rls.py`, `test_idempotency.py`). Per-test workspace UUID fixture pattern (isolated via `X-Test-Workspace` header instead of transaction rollback at HTTP boundary). Imports from `kb.api.schemas` / `kb.api.idempotency` / `kb.domain.schemas` fail at G3 (red, expected). Coverage: every G2 endpoint contract, every error slug, RLS 404-not-403 design, idempotency-key replay semantics including the DELETE replay vs second-call-state distinction. Awaiting sign-off. | Aniket |
| 2026-05-23 | **Phase 1a G3 ✅ signed off. Post-G3 consistency sweep.** Four drifts fixed: (A) `api_contracts.md` §0.2 broadened — entity IDs are UUIDv4 by default for primary keys (where time-sortability isn't a query pattern), UUIDv7 reserved for transactional event IDs (X-Request-Id, future query_id). Reflects what Phase 0 silently shipped for `audit_log.id` and what Phase 1a chose for `schemas.id` — convention now honest about it. (B) `scripts/verify_phase_0.sh` openapi paths assertion relaxed from `==` to "contains" so later phases mounting routes don't break the Phase 0 verify. `build_tracker.md` §5.1 G5 #5 text updated to match. (C) `scripts/verify_phase_0.sh` pytest step now runs **only Phase 0 test files** explicitly — running `pytest tests/` picked up Phase 1a's red skeletons and falsely failed Phase 0's invariant check. Each phase's verify owns its own scope. (D) Spec test count corrected: claimed ~25, actual 31 (21 + 5 + 5). G1 decision #8 narrative updated to reflect the §0.2 carve-out (no actual value change). Phase 0 verify re-run: 16/16 GREEN. | Aniket |
| 2026-05-23 | **Phase 1a G4 ✅ — API layer committed.** Commits `bebb102` (0005_schemas.sql + domain layer with pydantic models + repo functions) · `44ec1f0` (errors.py with RFC 9457 helper + 5 custom exceptions, idempotency.py with Header deps + cache helpers, schemas.py router with 5 endpoints, deps.py kb_app_connection async-generator, main.py exception handlers + router mount). One Phase 0 cross-phase drift fixed in the same commit: `test_runner_applies_all_files_in_lexical_order` was asserting exactly 4 migration files; relaxed to "first 4 are Phase 0's, in order" since later phases append files. All 78 tests pass (49 Phase 0 + 29 Phase 1a). | Aniket |
| 2026-05-23 | **Phase 1a G5 ✅ + end-of-phase cross-phase sweep.** Authored `scripts/verify_phase_1a.sh`; 17/17 checks pass — compose smoke + 9 schemas-surface assertions (table + partial unique index + RLS state, CRUD via curl, 409 on dup, RLS isolation A↔B, idempotency replay, soft delete + DB row remains, openapi paths include /schemas) + 29 pytest. **Cross-phase sweep** per memory entry `feedback_end_of_phase_cross_phase_check.md`: (a) verify_phase_0.sh re-run after Phase 1a code → still 16/16 GREEN; (b) scope-leak grep clean — no `schema_versions` / `current_version_id` / `schema_entities*` / `nl_description` / `INSERT INTO audit_log` in Phase 1a code; (c) verify script for Phase 1a needed one fix during the run (replay byte-comparison → semantic JSON comparison, since PG jsonb doesn't preserve key order); (d) spec test count corrected (31 → 29 — earlier grep counted the `test_workspace` fixture as a test; pytest is the authoritative count source). **Phase 1a complete; ready to open PR.** | Aniket |
| 2026-05-23 | **Phase 1a merged.** PR #2 merged into `main` (merge commit `c5cfc08`). Tag `phase-1a-complete` pushed. Local `phase-1a/schemas-crud` branch deleted. | Aniket |
| 2026-05-23 | **Phase 1b G1 OPEN.** Branched `phase-1b/schema-versioning` from `main`. Plan section §5.3 drafted: `0006_schema_versions.sql` adds `schema_versions` table (workspace-scoped, RLS day-1, immutable — GRANT SELECT+INSERT only) and a nullable `current_version_id` FK on `schemas`; 3 new endpoints (list, read with diff, rollback) + 2 mutated 1a endpoints (POST/PUT now write a version row in-tx and return `current_version`); full JSON snapshots per architecture §7 line 788; rollback = clone-forward as new current version; monotonic integer `version_number` per schema. Out of scope: optimistic locking via If-Match (Phase 10d UI), `created_by` (auth phase), re-extraction trigger (Phase 6), entity/field/relationship snapshots (Phase 1c — `body jsonb` is forward-compatible). 12 decisions locked. Awaiting sign-off. | Aniket |
| 2026-05-23 | **Phase 1b G1 ✅ signed off. G2 drafted.** `api_contracts.md` §3 added (renumbered: old §3 placeholders → §4, old §4 changelog → §5). §3.1 versioning-model invariants (append-only, atomic with mutation, schema-exists ⇒ ≥1 version, monotonic int per schema, rollback = clone-forward, replay never duplicates, workspace-isolated). §3.2 mutated schema object adds `current_version: int`. §3.3/§3.4 POST/PUT behavioural deltas. §3.5 version object shape. §3.6 declarative diff format (not strict RFC 6902). §3.7 list versions (newest-first, lightweight summary). §3.8 read one with computed `diff_from_prior`. §3.9 rollback with `409 rollback-noop` for same-as-current target + Idempotency-Key required. §3.10 out-of-scope list. New slug introduced: `rollback-noop` (joins 1a's 4 slugs). | Aniket |
| 2026-05-23 | **Phase 1b post-G2 cross-gate review (G1↔G2).** All 12 G1 decisions traced into §3 cleanly. Two tightenings landed: (A) decision #12 expanded — "last-writer-wins" was correct but didn't address the `version_number` allocation race. Locked: server serializes per-schema via `SELECT ... FOR UPDATE` on the parent row inside the mutation tx, so contiguous numbers always assigned and the UNIQUE `(schema_id, version_number)` constraint is never raced. §3.4 contract amended to match. (B) new decision #13 — `409 rollback-noop` for `v == current_version` (derived from §3.9 contract; G1 hadn't preempted this UX call; locked at G2). | Aniket |
| 2026-05-23 | **Phase 1b G2 ✅ signed off.** §3 contracts locked. **G3 opens** — drafting `tests/specs/phase_1b.md` + 3 red skeleton files: `test_schema_versions.py` (list + read-with-diff + rollback + concurrency), `test_schemas_crud.py` mutations (POST returns current_version=1; PUT bumps it), `test_idempotency.py` mutations (rollback Idempotency-Key replay). | Aniket |
| 2026-05-23 | **Phase 1b G3 drafted.** `tests/specs/phase_1b.md` covers the §3 surface with 25 tests in new `test_schema_versions.py` (list 8 · read 9 · rollback 7 · concurrent-PUT 1), 2 additive in `test_schemas_crud.py` (current_version on POST + PUT), 1 additive in `test_idempotency.py` (rollback replay verified via superuser row count). 28 new tests total. pytest --collect-only confirms 106 total (49 Phase 0 + 29 Phase 1a + 28 Phase 1b). Awaiting sign-off. | Aniket |
| 2026-05-23 | **Phase 1b post-G3 cross-gate review (G1↔G2↔G3).** All 13 G1 decisions traced to ≥1 G3 test (snapshot via body-equality assertions in §3.5 tests; monotonic int via concurrency test; v1-atomic via shape test; PUT-creates-version via current_version assertions; clone-forward via body-equality after rollback; declarative diff via expected-dict comparison; required Idempotency-Key on rollback via 400-missing-key test; rollback-noop 409 via direct assertion). New slug `rollback-noop` reaches §3 contract + spec + test code. No cross-phase scope leak (grep: no entities/fields/relationships/nl_description/audit_log writes in test sources beyond the spec's deliberate out-of-scope notice). | Aniket |
| 2026-05-23 | **Phase 1b G3 ✅ signed off. G4 opens.** Build order planned: (1) `0006_schema_versions.sql` (schema_versions table + ALTER schemas ADD current_version_id) + idempotent re-application guards (DROP POLICY IF EXISTS, etc.); (2) `kb/domain/schema_versions.py` (snapshot pydantic models + diff helper + repo functions list/get/rollback); (3) extend `kb/domain/schemas.py` (POST + PUT now write version in-tx via SELECT FOR UPDATE); (4) `kb/api/schema_versions.py` router + new `rollback-noop` slug helper; (5) wire into `kb/api/main.py`; (6) extend the `SchemaResponse` pydantic model with `current_version: int`. | Aniket |
| 2026-05-23 | **Phase 1b G4 ✅ — code landed (single commit `6a5f896`).** Files: `0006_schema_versions.sql` (workspace-scoped + RLS + UNIQUE (schema_id, version_number) + ALTER schemas ADD current_version_id ON DELETE SET NULL); `kb/domain/schema_versions.py` (VersionSummary/VersionRead pydantic + RollbackNoopError/VersionNotFoundError + compute_diff + list_versions/get_version/insert_version); `kb/domain/schemas.py` mutated (SchemaResponse + current_version; INNER JOIN on schema_versions in reads; create_schema writes v1 atomically; update_schema uses SELECT FOR UPDATE + allocates max(v_n)+1; rollback_to_version new); `kb/api/schema_versions.py` router (3 endpoints, Path ge=1, parent 404-gate); `kb/api/main.py` + handlers for both new exceptions. All 13 G1 decisions traced in commit body. pytest -q tests/ → **106 passed** (49 Phase 0 + 29 Phase 1a kept + 28 Phase 1b new) in 24.87s. | Aniket |
| 2026-05-23 | **Phase 1b G4 cross-gate sweep — 1 issue found and fixed.** verify_phase_1b.sh step 7 (kb_app GRANTs on schema_versions) caught that 0001's `ALTER DEFAULT PRIVILEGES ... GRANT SELECT, INSERT, UPDATE, DELETE` grants the full CRUD set on every NEW table in `public`, overriding 0006's narrow `GRANT SELECT, INSERT`. Without an explicit REVOKE, an application bug or future maintainer could UPDATE/DELETE a version row and silently mutate audit history — violating invariant §3.1 #1. Fix: added `REVOKE UPDATE, DELETE ON schema_versions FROM kb_app;` to 0006 with a comment explaining the default-privileges interaction. Re-run verify_phase_1b.sh: 21/21 GREEN. | Aniket |
| 2026-05-23 | **Phase 1b G5 ✅ + end-of-phase cross-phase sweep.** Authored `scripts/verify_phase_1b.sh` (21 checks): compose smoke + 5 DDL invariants (table+UNIQUE constraint+RLS forced+kb_app grants restricted+ON DELETE SET NULL) + 11 HTTP/rollback/RLS/idempotency curl checks + openapi exposure + Phase-1b pytest. **Cross-phase sweep** per memory entry `feedback_end_of_phase_cross_phase_check.md`: (a) verify_phase_0.sh re-run → 16/16 GREEN; verify_phase_1a.sh re-run → 17/17 GREEN; verify_phase_1b.sh → 21/21 GREEN. (b) Scope-leak grep clean — no `schema_entities` / `schema_fields` / `schema_relationships` / `nl_description` / `domain_vocabulary` / rogue `INSERT INTO audit_log` in Phase 1b code (the audit_log INSERTs in test_rls.py are Phase 0's RLS tests on the audit_log table itself, by design). (c) RLS invariant holds — all 4 workspace-scoped tables (audit_log, idempotency_keys, schemas, schema_versions) have their own `workspace_id` column + their own `CREATE POLICY` (belt-and-braces per decision #10). (d) pytest --collect-only confirms 106 total tests, matching the spec. **Phase 1b complete; ready to open PR.** | Aniket |
| 2026-05-23 | **Phase 1b merged.** PR #3 merged into `main` (merge commit `95f5a4f`). Tag `phase-1b-complete` pushed. Local `phase-1b/schema-versioning` branch deleted. | Aniket |
| 2026-05-23 | **Phase 1c G1 OPEN.** Branched `phase-1c/schema-hierarchy` from `main`. Plan section §5.4 drafted: `0007_schema_hierarchy.sql` adds three workspace-scoped + RLS-day-1 tables (`schema_entities`, `schema_fields`, `schema_relationships`) per architecture line 793–796 (kind/cardinality/cascade_delete/single_parent). 9 nested CRUD endpoints under `/schemas/:id/{entities, entities/:eid/fields, relationships}`. `nl_description` on fields (Phase 6 extraction prompts). 1b's `schema_versions.body` jsonb expands to include the full subtree; rollback restores entities + fields + relationships in one tx. 13 decisions locked. Out of scope: `extracted_entities` table (Phase 5/6), lineage helper endpoints (Phase 8), `single_parent` enforcement (Phase 6 enforces at extraction time), domain_vocabulary (Phase 5), re-extraction trigger (Phase 6), audit_log writes (Phase 9). Awaiting sign-off. | Aniket |
| 2026-05-23 | **Phase 1c G1 ✅ signed off. G2 drafted.** Endpoint count corrected 9 → 11 (entity + field full CRUD = 4 each; relationship = POST/GET-list/DELETE = 3; no PUT on relationships — soft-delete + re-create suffices). `api_contracts.md` §4 added with 18 sub-sections: hierarchy invariants (§4.1 — workspace-isolated · parent-scoped soft delete · coarse-grained versioning · atomic mutations · name-resolved cross-refs in snapshots · replay never duplicates), extended schema_versions.body snapshot with entities/fields/relationships (§4.2), diff format extension with nested dotted paths (§4.3), entity surface 4 endpoints (§4.4–§4.8; DELETE cascades to fields + relationships), field surface 4 endpoints (§4.9–§4.13; type enum string/number/boolean/date/datetime), relationship surface 3 endpoints (§4.14–§4.17; kind enum verbatim architecture line 794), out-of-scope §4.18. Old §4 placeholder → §5; old §5 changelog → §6. | Aniket |
| 2026-05-23 | **Phase 1c post-G2 cross-gate review (G1↔G2).** All 13 G1 decisions traced into §4 cleanly. One nuance tightened: §4.14 now explicit that live relationship objects on the wire reference entities by UUID (`from_entity_id`/`to_entity_id`) while `schema_versions.body` snapshots reference them by name (per invariant §4.1 #5 — rollback that re-creates entities binds relationships by name to the new UUIDs). 3 new error slugs introduced — `entity-name-conflict`, `field-name-conflict`, `relationship-name-conflict` (join 1a/1b's 5; same `<resource>-name-conflict` precedent). | Aniket |
| 2026-05-23 | **Phase 1c G2 ✅ signed off. G3 opens.** Drafting `tests/specs/phase_1c.md` + new red skeleton files: `test_schema_entities.py`, `test_schema_fields.py`, `test_schema_relationships.py`, `test_schema_hierarchy_versions.py` (subtree snapshot + rollback restoration + nested diff). | Aniket |
| 2026-05-23 | **Phase 1c G3 drafted.** `tests/specs/phase_1c.md` covers the §4 surface with 36 tests across 4 new files: entities 10 (CRUD + cascade-delete to fields + relationships + RLS), fields 8 (CRUD + type enum + RLS), relationships 8 (CRUD + kind enum + cross-schema FK rejection + RLS), hierarchy_versions 10 (coarse-grained versioning + subtree snapshot shape + rollback restores entities/fields/relationships + nested-path diff). pytest --collect-only confirms 142 total (49 Phase 0 + 29 Phase 1a + 28 Phase 1b + 36 Phase 1c new). Awaiting sign-off. | Aniket |
| 2026-05-23 | **Phase 1c post-G3 cross-gate review (G1↔G2↔G3).** All 13 G1 decisions traced via tests: type enum CHECK (test_post_validation_rejects_invalid_type), kind enum CHECK (test_post_validation_rejects_invalid_kind), NL description in shape test, recorded-only metadata (cardinality/cascade_delete/single_parent surfaced in test_post_creates_relationship_with_documented_shape), soft-delete via superuser row count, coarse-grained versioning (test_entity_post_bumps_schemas_current_version + 2 siblings), name-resolved cross-refs (test_snapshot_body_includes_relationships_with_names + test_rollback_restores_relationships_by_name_resolution), nested URLs (verbatim), Idempotency-Key (test_post_requires_idempotency_key x3), RLS day-1 (one per file), snapshot body (test_snapshot_body_includes_entities_with_fields), nested diff paths (test_diff_for_added_entity + test_diff_for_changed_field_type). All 3 new slugs asserted. No cross-phase scope leak (grep clean for lineage_path / extracted_entities / domain_vocabulary / audit_log writes). | Aniket |
| 2026-05-23 | **Phase 1c G3 ✅ signed off. G4 opens.** Build order: (1) `0007_schema_hierarchy.sql` (3 new workspace-scoped + RLS-day-1 tables); (2) `kb/domain/schema_hierarchy.py` (pydantic models + repo functions + subtree builder + name-resolved rollback restorer); (3) extend `kb/domain/schema_versions.py` (recursive compute_diff; subtree snapshot builder); (4) extend `kb/domain/schemas.py` (rollback now uses subtree restorer); (5) `kb/api/schema_hierarchy.py` router with 11 endpoints; (6) wire into `kb/api/main.py` (mount router + 3 new exception handlers for *-name-conflict slugs); (7) extend errors.py with the 3 new domain exceptions. | Aniket |

---

## 10. Reading order for a fresh reviewer

1. This file → understand discipline + current state.
2. [`README.md`](../README.md) → mental model.
3. [`docs/architecture.md`](architecture.md) → full system spec.
4. [`docs/ui_design.md`](ui_design.md) → screen-by-screen UX.
5. [`docs/gaps_design.md`](gaps_design.md) → 9 detailed designs.
6. Stress-tests & audits as needed (`scenarios.md`, `red_team.md`, `citations_audit.md`, `competitive_audit.md`, `scale_perf_audit.md`).
