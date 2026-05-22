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

**Now:** Phase 0 G2 — API contracts for `/health` + `/ready` (open). Branch: `phase-0/repo-skeleton`.
**Next:** Phase 0 G3 — test specs for `/health` + `/ready` + migration runner.
**Blocked on:** nothing. Phase 0 G1 signed off 2026-05-22 · plan locked in §5.1.

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
| API contracts | [docs/api_contracts.md](api_contracts.md) | 🟡 Phase 0 contracts drafted | Awaiting sign-off on Phase 0 §1 (`/health`, `/ready`) |
| Test specs (per-phase) | tests/specs/ | ⬜ Not started | Created per phase at G3 |

---

## 3. Tech stack — locked (no change without re-opening G1 globally)

| Layer | Choice | Why |
|---|---|---|
| **Runtime** | Python 3.12, uv-managed | Modern toolchain, fast resolver, lockfile reproducible |
| **API framework** | FastAPI | Async, OpenAPI built-in, ecosystem maturity |
| **DB** | Postgres 17 + pgvector ≥ 0.8 + ParadeDB pg_search | One transactional store; vector + BM25 in same place |
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
| **0** | Repo + docker-compose (Postgres+pgvector+pg_search+MinIO+Procrastinate) + lifecycle DDL | ✅ | 🟡 | ⬜ | ⬜ | ⬜ | G1 signed off 2026-05-22 (plan §5.1). G2 open: `/health` + `/ready`. |
| **1** | Schema service: CRUD, versioning, NL field descriptions, hierarchy | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | First "real" API phase |
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

### 5.1 Phase 0 plan — Repo skeleton + docker-compose (G1 SIGNED OFF)

> **Status:** G1 ✅ signed off 2026-05-22 by Aniket. Plan below is locked — changes require re-opening G1 per §0 rule 5. Branch: `phase-0/repo-skeleton`.

#### Scope

Phase 0 produces the runnable infrastructure that every later phase builds on.

**In scope:**
- Single-package Python repo layout under `src/kb/`.
- `docker-compose.yml` bringing up Postgres (pgvector + pg_search), MinIO, a Procrastinate worker container, and the FastAPI app — in one command.
- Cross-cutting "lifecycle" tables that every later phase reads or writes (audit log, processing status, idempotency keys, migration tracker).
- Migration runner — raw SQL files + a thin Python applier.
- Python project tooling (`uv`, `ruff`, `pyright`, `pytest`).
- FastAPI app skeleton (no routes yet — `/health` + `/ready` open at Phase 0 G2).

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
│   │   └── deps.py
│   ├── workers/                ← Procrastinate worker; entrypoint `kb.workers.run`
│   │   └── run.py
│   ├── db/                     ← psycopg async pool; transactions
│   │   └── pool.py
│   ├── storage/                ← MinIO client
│   ├── config.py               ← pydantic-settings
│   └── logging.py              ← structlog config
├── migrations/
│   ├── runner.py               ← applies .sql files in lexical order; tracks in schema_migrations
│   └── sql/
│       ├── 0001_extensions.sql           ← CREATE EXTENSION vector, pg_search
│       ├── 0002_schema_migrations.sql    ← bootstrap migration tracker
│       ├── 0003_audit_log.sql            ← append-only, monthly partitioned
│       ├── 0004_processing_status.sql    ← per-(doc_id, stage) lifecycle
│       └── 0005_idempotency_keys.sql     ← cross-cutting idempotency
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

#### Lifecycle DDL — Phase 0 migrations (narrow scope)

1. **`0001_extensions.sql`** — `CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_search;`
2. **`0002_schema_migrations.sql`** — `(id text primary key, applied_at timestamptz default now())`. Bootstrap tracker the runner writes to.
3. **`0003_audit_log.sql`** — append-only, partitioned by `ts` per month. Columns: `id uuid pk · ts timestamptz · actor text · action text · entity_type text · entity_id text · payload jsonb`. Phase 9 builds the read surface over this; phases 1–8 write to it.
4. **`0004_processing_status.sql`** — per-`(doc_id, stage)` lifecycle row. Columns: `doc_id uuid · stage text · status text · started_at timestamptz · finished_at timestamptz · error jsonb`. Phases 2–7 write; Phase 9 exposes the SSE feed.
5. **`0005_idempotency_keys.sql`** — `key text pk · response jsonb · created_at timestamptz`. Cross-cutting idempotent request handling.

Each later phase appends its own `NNNN_<purpose>.sql` files at its own G4. Numbering is global (linear apply order).

#### Migration runner behaviour

`python -m migrations.runner`:
1. Connect to the configured Postgres.
2. Ensure `schema_migrations` table exists (bootstrap if missing — runs `0002_*.sql` separately).
3. List `migrations/sql/*.sql` in lexical order.
4. For each file not yet recorded: run it inside a transaction; on success record `(id=filename, applied_at=now())`.
5. Idempotent: re-running with no new files does nothing.

No rollback DSL — for DDL we write forward fixes. Standard in DDL-heavy systems.

#### Phase 0 G5 — what "green" means

`scripts/verify_phase_0.sh` lands at G5 and runs end-to-end:

1. `cp .env.example .env && docker compose up -d --build`
2. Wait for `db`, `minio`, `api`, `worker` healthy; `migrate` exited 0.
3. `psql` into `db`: `\dx` includes `vector` and `pg_search`; `\dt` includes `schema_migrations`, `audit_log`, `processing_status`, `idempotency_keys`.
4. `curl http://localhost:8000/openapi.json` returns 200 with an empty `paths` object (routes open at Phase 0 G2).
5. `pytest tests/test_phase_0_*.py` is green.

#### Sign-off

When Aniket approves this plan, the Phase 0 G1 cell in §5 flips 🟡 → ✅ and Phase 0 G2 opens (API contracts for `/health` + `/ready`). Sign-off recorded in §9 (Change log).

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
| 0 | `GET /health`, `GET /ready` | 🟡 drafted · awaiting sign-off |
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
| 0 | tests/specs/phase_0.md | tests/test_phase_0_*.py | ⬜ |
| 1 | tests/specs/phase_1.md | tests/test_phase_1_*.py | ⬜ |
| ... | | | |

---

## 8. Run / verify — index

> Each phase's G5 produces a script (`scripts/verify_<phase>.sh`) or a manual checklist appended to this tracker. Outputs are summarized here.

| Phase | Verify script | Last run | Result |
|---|---|---|---|
| 0 | scripts/verify_phase_0.sh | — | — |
| 1 | scripts/verify_phase_1.sh | — | — |
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

---

## 10. Reading order for a fresh reviewer

1. This file → understand discipline + current state.
2. [`README.md`](../README.md) → mental model.
3. [`docs/architecture.md`](architecture.md) → full system spec.
4. [`docs/ui_design.md`](ui_design.md) → screen-by-screen UX.
5. [`docs/gaps_design.md`](gaps_design.md) → 9 detailed designs.
6. Stress-tests & audits as needed (`scenarios.md`, `red_team.md`, `citations_audit.md`, `competitive_audit.md`, `scale_perf_audit.md`).
