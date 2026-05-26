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

> **For the current "where we are" snapshot — what's on `main`, what's in the
> open PR queue, and what the demo workspace looks like right now — read
> [`STATUS.md`](STATUS.md).** That doc is refreshed each time meaningful
> work lands; this section captures the latest gate-level state only.

**Now:** **Wave B step 2 — [PR #32](https://github.com/AniketTati/emerging-kb/pull/32) open against `main`.**
Carries 8 commits across E4 (doc-chain cross-format) · PR8 (L3 long-tail plugins:
`email_messages` + `generic_items`) · R1 (Design 2 conflict resolution wired into
chat) · R2 (char-range citations) · R3 (chat UX polish) · retrieval channel
lifecycle filter · R4 (noise mention skip — 671→217 entities) · R5 (Docling
per-element layout + DocDetail mini-map). 37 files, +4083 lines. Two run-once
backfill scripts ship with it (`cleanup_noise_entities.py` + `backfill_pdf_layout.py`).
Wave B step 1 (PR #31, merged 2026-05-25) carried E1/E2/E2b/E3 + citations v2 +
broader corpus + `/upload` pagination.

**Next:** Awaiting PR #32 review + merge. Deferred follow-ups: Wave-C PDF bbox
overlay (use R5 layout to draw highlight rectangles on rendered PDF when a
citation is clicked) · cross-type entity defragmentation · `api_contracts.md`
refresh to cover the 19+ lifecycle events (only 8 are currently documented).

**Blocked on:** nothing.

<details>
<summary>Historical "Now/Next" snapshots (pre-Wave-B)</summary>

> 🎉 **Wave A FULLY COMPLETE.** Phase 3e ✅ shipped — corpus-level RAPTOR (plan at §5.10.1, **15 decisions**). Phase 3d also ✅ — per-doc RAPTOR (plan at §5.10, **16 decisions** revised post-deliberation; the deliberation flips that earned their keep: (1) discriminated edge FK + L1 stays in contextual_chunks — saves 30 GB at 100K-doc scale; (2) `raptor_building` intermediate lifecycle state — observability for the multi-stage build; (3) `MAX_LEVELS` bumped 4→6 to cover corpus-tree depth `log₈(100K)≈5.5`; (4) forward-compat `raptor_nodes.scope` enum + nullable `file_id` locked at 3d's 0012 migration — Phase 3e needed no separate migration). **286/286 pytest** in 81s.
>
> **Phase 10a ✅ FULLY GREEN — Next.js 15 Upload UI shipped.** `ui/` Next.js 15 (App Router) + Tailwind v4 + lucide-react app delivered 2026-05-25 on `phase-10a/upload-ui`. `/upload` page: drag-drop zone + live status table with 5-pip stage indicator + SSE per-file subscription. **10/10 vitest** + **2/2 Playwright** E2E. Backend 541/541 pytest GREEN.

</details>

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
| **1c** | Schema service — **hierarchy**: `schema_entities`, `schema_fields`, `schema_relationships` tables; nested CRUD; NL field descriptions; single_parent + cascade_delete constraints | ✅ | ✅ | ✅ | ✅ | ✅ | All 5 gates green 2026-05-23. verify_phase_1c.sh 20/20. verify_phase_1b.sh still 21/21. verify_phase_1a.sh still 17/17. verify_phase_0.sh still 16/16. pytest 142/142. Ready to merge. |
| **2a** | Parse layer — **scaffold + Docling**: `files` + `file_lifecycle` + `raw_pages` + `parse_artifacts` tables; Procrastinate `parse_file` task; MIME-based dispatcher; Docling (digital PDF) parser; admin `POST /files` upload endpoint | ✅ | ✅ | ✅ | ✅ | ✅ | All 5 gates green 2026-05-23. pytest 170/170. First worker phase complete. Ready to merge. |
| **2b** | Parse layer — **additional parsers**: xlsx (openpyxl) + email (stdlib) + Mistral OCR (external API adapter class + mock-tested; real-API gated on `KB_MISTRAL_API_KEY`) | ✅ | ✅ | ✅ | ✅ | ✅ | All 5 gates green 2026-05-23. verify_phase_2b.sh 15/15. pytest 188/188. xlsx + email E2E pipeline verified in Docker stack (xlsx → 2 sheets → 2 raw_pages; email → 1 page with headers + body). Mistral OCR adapter ready, self-disabled without API key. Ready to merge. |
| **2c** | Gemini OCR + strategy-driven dispatch — `GeminiOCRParser` (pypdfium2 PDF→PNG + Gemini 2.5 Flash VLM, per-page) + pre-flight text-layer sniff + 4-value `KB_PARSER_STRATEGY ∈ {auto,docling_first,gemini_first,gemini_only}` + caller override `?parser=...` + quality escalation + provenance JSON in `raw_pages.layout_json`. | ✅ | ✅ | ✅ | ✅ | ✅ | Shipped 2026-05-24. 258/258 pytest. verify_phase_2c.sh 15/15 (compose smoke + pypdfium2 worker probe + KB_PARSER_STRATEGY env probe + digital→Docling E2E + provenance JSON in raw_pages + provenance in lifecycle parse_done + scanned→soft-Docling-fallback when no Gemini key + caller override `?parser=docling` + 400 invalid-parser-override + Phase-2c pytest 18). Cross-phase sweep: 9/10 GREEN first-run; 2c flaked at step 7 under host memory pressure after 9 prior stacks ran sequentially — confirmed transient (standalone re-run = GREEN). Bumped step 7 polling 180→240 iters (6→8 min buffer for Docling first-run model download under load) + added worker-log capture on failure for future debugging. |
| **3a** | Chunking — late chunking of `raw_pages` → `chunks` table (layout-aware, token-bounded, cross-page joining); worker stage `chunk_file`; new lifecycle state `chunked` | ✅ | ✅ | ✅ | ✅ | ✅ | All 5 gates green 2026-05-23. verify_phase_3a.sh 18/18. Cross-phase sweep: 0/1a/1b/1c/2a/2b all still green (124/124 cumulative checks). pytest 204/204. Ready to merge. |
| **3b** | Contextual Retrieval — Anthropic Claude per-chunk prefix with prompt-cached doc context; `contextual_chunks` table; worker stage `contextualize_file` | ✅ | ✅ | ✅ | ✅ | ✅ | All 5 gates green 2026-05-23. verify_phase_3b.sh 15/15. Cross-phase sweep: 0/1a/1b/1c/2a/2b/3a/3b all green (139/139 cumulative checks). pytest 219/219. Ready to merge. |
| **3c** | Embedding — Gemini Embedding 001 on contextual chunks → `chunk_embeddings` (`halfvec(3072)`); worker stage `embed_file`; new lifecycle state `embedded` | ✅ | ✅ | ✅ | ✅ | ✅ | First embedding call; gated on `KB_GEMINI_API_KEY` with DeterministicMockEmbedder for CI. 13/13 new tests green; suite 232/232. verify_phase_3c.sh 15/15 + cross-phase sweep 0/1a/1b/1c/2a/2b/3a/3b/3c all GREEN. One sweep fix: 3a's accept-set widened to also accept `embedded` (Phase 3c chained-defer races past `chunked` before the script polls — same forward-compat pattern handled at 3b). |
| **3b-bis** | Gemini Contextualizer adapter — `GeminiContextualizer` alongside `AnthropicContextualizer` + factory selector `KB_CONTEXTUALIZER ∈ {gemini,anthropic,identity,auto}`. No schema/lifecycle/API delta. | ✅ | — | ✅ | ✅ | ✅ | Shipped 2026-05-24. 238/238 pytest. verify_phase_3b.sh widened 15→16 checks (adapter env probe + conditional Gemini/Anthropic/Identity branch on `model_id`/`cache_creation_input_tokens`/`cache_read_input_tokens`). Cross-phase sweep 0/1a/1b/1c/2a/2b/3a/3b/3c all GREEN (158 checks total). `.env.example` consistency gap closed at G5 (all 3 LLM keys + KB_CONTEXTUALIZER documented). |
| **3d** | RAPTOR tree build, **per-doc** — recursive cluster→summarize→re-embed → `raptor_nodes` (L2..6) + `raptor_edges` (discriminated child FK); intermediate lifecycle state `raptor_building` between `embedded` → `ready` | ✅ | ✅ | ✅ | ✅ | ✅ | Shipped 2026-05-24. 275/275 pytest. verify_phase_3d.sh 22/22 standalone. Cross-phase sweep across all 11 verify scripts: 10/11 first-pass GREEN; 3c regressed at step 10 with `last state: ready` instead of `embedded` — same forward-compat race that 3a/3b already handled. Fix: widened 3c's accept-set to `embedded \| raptor_building \| ready` (matches the 0009 CHECK convention from 3b G4 fix #2). Re-ran 3c → 15/15. Final sweep: **0:16 · 1a:17 · 1b:21 · 1c:20 · 2a:17 · 2b:15 · 2c:15 · 3a:18 · 3b:16 · 3c:15 · 3d:22 — 192 total, all GREEN**. L2-node assertion in 3d verify gated on `leaf_count >= 2` (tiny.xlsx is singleton; pytest worker tests cover multi-leaf with fabricated data). |
| **3e** | RAPTOR tree build, **corpus-level** — cluster doc-roots across workspace → summarize themes → write `scope='corpus'` rows. Explicit `POST /corpus/raptor/rebuild` trigger (not auto). UMAP+GMM swap-in for the N=100K case where AC is infeasible. | ✅ | ✅ | ✅ | ✅ | ✅ | Shipped 2026-05-24. **286/286 pytest**. verify_phase_3e.sh **13/13 GREEN first-pass standalone**. Cross-phase sweep across ALL **12 verify scripts** (0/1a/1b/1c/2a/2b/2c/3a/3b/3c/3d/3e): **12/12 GREEN on first pass, no regressions**. Final sweep totals: **205 checks total**. **Wave A FULLY COMPLETE** — ingestion + per-doc RAPTOR + corpus-level RAPTOR all shipped on `phase-3/chunking-raptor` branch (7 commit-sets). |
| **4** | Indexing: pgvector HNSW + pg_search BM25 on all RAPTOR levels | ✅ | — | ✅ | ✅ | ✅ | All gates green 2026-05-25 (§5.11, 16 decisions). G2 was a no-op per decision #16. **296/296 pytest in 89.84s.** verify_phase_4.sh **16/16 standalone GREEN** (DDL ×5 + tiny.pdf E2E + ANALYZE + planner-usage EXPLAIN ×3 + smoke helper ×2 + pytest). **Cross-phase sweep across all 13 verify scripts: 13/13 GREEN in 14:56**. Branch `phase-4/retrieval`. Ready to merge. |
| **5** | Open extraction — parent (split into 5a/5b/5c) | ✅ | — | ✅ | ✅ | ✅ | All gates green 2026-05-25. 346/346 pytest. verify_phase_5.sh 16/16. Cross-phase sweep 14/14. Branch `phase-5/extraction`. |
| **5a** | Mention extraction — NER over contextual_chunks → `extracted_mentions` | ✅ | — | ✅ | ✅ | ✅ | §5.12.1 (11 decisions). 13/13 5a pytest. Lifecycle adds `mentions_extracting`. |
| **5b** | Emergent fields + doc-type classifier + auto-promotion to typed schema | ✅ | — | ✅ | ✅ | ✅ | §5.12.2 (11 decisions). 18/18 5b pytest. Lifecycle adds `fields_extracting`. Auto-promotion writes to existing `schema_fields` with `auto_promoted=true`. |
| **5c** | Atomic units + per-type rarity / anomaly scoring (clauses + transactions + rows plugins) | ✅ | — | ✅ | ✅ | ✅ | §5.12.3 (10 decisions). 19/19 5c pytest. Lifecycle adds `units_extracting`; final transitions to `ready`. |
| **6** | Schema-driven extraction (Gemini structured outputs) + lineage paths | ✅ | — | ✅ | ✅ | ✅ | All gates green 2026-05-25. 370/370 pytest. verify_phase_6.sh 10/10. Cross-phase sweep 15/15 GREEN. Branch `phase-6/schema-extraction`. |
| **7** | Identity resolution (deterministic→embedding→LLM judge→union-find) | ✅ | — | ✅ | ✅ | ✅ | All gates green 2026-05-25 (§5.14, 14 decisions). 407/407 pytest. verify_phase_7.sh 16/16. Cross-phase sweep 16/16 GREEN. Branch `phase-7/identity-resolution`. |
| **8** | Query planner + rewriting + parallel retrieval + RRF + rerank + CRAG + Astute generation (parent — split into 8a-f) | ✅ | — | ⬜ | ⬜ | ⬜ | G1 ✅ split-locked at §5.15. Each sub-phase has its own G1→G5 cycle. |
| **8a** | Query rewriting (Step-Back + HyDE + Query2Doc) | ✅ | — | ✅ | ✅ | ✅ | All gates green 2026-05-25 (§5.15.1, 10 decisions). 421/421 pytest. verify_phase_8a.sh 8/8. Cross-phase sweep 17/17. Branch `phase-8a/query-rewriter`. |
| **8b** | 6-channel parallel retrieval (BM25 chunks/raptor + dense chunks/raptor + mentions exact + atomic units rarity) + RRF fusion | ✅ | — | ✅ | ✅ | ✅ | All gates green 2026-05-25 (§5.15.2, 12 decisions). 441/441 pytest. verify_phase_8b.sh 9/9. Cross-phase sweep 18/18. Branch `phase-8b/retrieval-channels`. |
| **8c** | Reranker (Cohere Rerank 3.5 default · mxbai-rerank-large-v2 local fallback · Identity passthrough) | ✅ | — | ✅ | ✅ | ✅ | All gates green 2026-05-25 (§5.15.3, 12 decisions). 456/456 pytest. Cross-phase sweep 19/19 GREEN. Branch `phase-8c/rerank`. |
| **8d** | CRAG (Corrective RAG) relevance gate — judges top-K rerank confidence + refuses below threshold | ✅ | — | ✅ | ✅ | ✅ | All gates green 2026-05-25 (§5.15.4, 10 decisions). 472/472 pytest. Branch `phase-8d/crag`. |
| **8e** | Astute generation — Gemini answer with citations + cite-or-refuse over reranked top-10 | ✅ | — | ✅ | ✅ | ✅ | All gates green 2026-05-25 (§5.15.5, 15 decisions). 491/491 pytest. Branch `phase-8e/generate`. |
| **8f** | HTTP surface — `POST /search` + `POST /chat` + `query_log` audit table | ✅ | ✅ | ✅ | ✅ | ✅ | All gates green 2026-05-25 (§5.15.6, 17 decisions). 518/518 pytest. verify_phase_8f.sh 13/13. Cross-phase sweep 22/22 (one bash-syntax fix mid-G5, re-verified). Branch `phase-8f/orchestrator`. |
| **9** | Audit log + lifecycle visibility + chat replay SSE | ✅ | — | ✅ | ✅ | ✅ | All gates green 2026-05-25 (§5.16, 12 decisions). 541/541 pytest. verify_phase_9.sh 14/14 (real-stack E2E SSE 13 lifecycle events). Cross-phase sweep 23/23. Branch `phase-9/sse-audit`. |
| **10a** | UI — Upload (drag-drop · live per-doc per-stage status via SSE) | ✅ | — | ✅ | ✅ | ✅ | All gates green 2026-05-25 (§5.17, 15 decisions). Next.js 15 + Tailwind v4 + lucide-react. 10/10 vitest + 2/2 Playwright (screenshot artifact). Backend 541/541 still GREEN after CORS middleware. Branch `phase-10a/upload-ui`. |
| **10b** | UI — Chat (front door · streamed answers · right-side citation cards · plan inspector) + universal Doc Detail slide-in panel | 🟡 | — | ⬜ | ⬜ | ⬜ | G1 DRAFT at §5.18. |
| **10c** | UI — Explore (Knowledge Explorer: search + left-rail facets · progressive expansion) | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | `explore.html` |
| **10d** | UI — Schema Studio (6 tabs: Typed · Inferred · Collisions · Vocabulary · Lineage · Versions · schema-swap affordance) | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | `schema-studio.html` · covers Designs 6 / 7 / 9 UI surfaces |
| **10e** | UI — Dashboard (counts + sparklines · live "what just learned" SSE feed · needs-attention · ingestion/query/cost cards) | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | `dashboard.html` |
| **10f** | UI — Audit (immutable per-query log · re-run with current config · add-to-regression-set) | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | `audit.html` · pairs with Phase 9 backend |
| **10g** | UI — Settings (workspace · models & retrieval defaults · auto-discovery · ingestion · cost · API keys · `/swagger` exposure · Effective Config view) | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | `settings.html` |
| **11** | Public-dataset loader: CUAD + Enron + SEC 10-K subset + scans + xlsx | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | Scripts, not service endpoints |
| **12** | Eval harness — 45 stratified Q&A (5 × 9 strata) + RAGAS + HHEM + basic Playground sandbox UI | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | `playground.html` (basic single-query + eval matrix) · regression CI |

### 5.1 Phase 0 plan — Repo skeleton + docker-compose (G1 ✅ + G2 ✅ + G3 ✅ + G4 ✅ + G5 ✅ SIGNED OFF)

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

### 5.2 Phase 1a plan — Schema CRUD foundation (G1 ✅ + G2 ✅ + G3 ✅ + G4 ✅ + G5 ✅ SIGNED OFF)

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

### 5.3 Phase 1b plan — Schema versioning (G1 ✅ + G2 ✅ + G3 ✅ + G4 ✅ + G5 ✅ SIGNED OFF)

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

### 5.4 Phase 1c plan — Schema hierarchy (G1 ✅ + G2 ✅ + G3 ✅ + G4 ✅ + G5 ✅ SIGNED OFF)

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

### 5.5 Phase 2a plan — Parse-layer scaffold + Docling (G1 ✅ + G2 ✅ + G3 ✅ + G4 ✅ + G5 ✅ SIGNED OFF)

> **Status:** G1 ✅ signed off 2026-05-23 by Aniket. Plan locked. Branch: `phase-2a/parse-scaffold` off `main` (Phase 1c merged as PR #4; tag `phase-1c-complete`).
>
> **Why split Phase 2 into 2a + 2b:** the §5 description ("Docling + Mistral OCR + xlsx + email → raw_pages") is four comma-separated parsers — the [`feedback_sub_phase_splits`](../../.claude/memory/feedback_sub_phase_splits.md) rule applies. 2a builds the **scaffold** (tables + Procrastinate worker + dispatcher + admin upload endpoint + ONE parser to prove the pipeline). 2b layers the remaining parsers on top via the same `Parser` Protocol.

#### Scope

Phase 2a builds the **runnable end-to-end ingestion path**: upload a PDF → MinIO → `files` row → Procrastinate task → Docling parser → `raw_pages` rows → readable via API. This is the first worker phase; the pattern (Procrastinate task + per-stage idempotency via `file_lifecycle`) gets copied by every later worker phase (3 chunking, 4 indexing, 5 mention extraction, 6 schema extraction).

**In scope:**
- One DDL file: `migrations/sql/0008_parse_layer.sql`. Four new workspace-scoped + RLS-day-1 tables:
  - `files` — display name + MinIO `object_key` + content `sha256` + `mime_type` + `size_bytes` + `lifecycle_state` enum (`queued/parsing/parsed/failed/deleted`).
  - `file_lifecycle` — append-only audit trail of state transitions (GRANT SELECT+INSERT only; same immutability pattern as `schema_versions`).
  - `raw_pages` — immutable per-page output (`file_id` + 1-indexed `page_number` + `text` + `layout_json` + `content_sha`).
  - `parse_artifacts` — secondary parser output stored in MinIO via `object_key` reference (`kind` enum: `layout`, `tables`, `ocr_confidence`).
- Admin upload endpoint: `POST /files` accepts multipart upload OR a `{ minio_object_key }` body for pre-uploaded content; computes `sha256`, dedupes by `(workspace_id, content_sha)`, creates the `files` row, enqueues the parse task.
- Read endpoints: `GET /files` (paginated list) · `GET /files/:id` (file + lifecycle history) · `GET /files/:id/pages` (paginated raw pages) · `DELETE /files/:id` (soft delete; MinIO blob retained).
- Procrastinate task: `parse_file(file_id: str)` — workspace_id resolved from the row inside the worker; 30-min lease; per-stage idempotency.
- `Parser` Protocol: `can_handle(mime_type, magic_bytes) -> bool` + `async def parse(file_bytes, file_id) -> ParsedDocument`.
- Docling parser implementation (Wave A's main use case: digital PDFs from CUAD, SEC 10-K, etc.).
- MIME + magic-bytes-based dispatcher that picks the right `Parser` (only Docling registered in 2a; 2b adds more).
- Failure mode: parser raises → worker writes `file_lifecycle` row with `to_state='failed'`, `payload={error: ...}`, leaves `files.lifecycle_state='failed'`. Caller can `POST /files/:id/retry` (rare — keep for 2b).

**Out of scope (deferred):**
- xlsx parser (openpyxl) — **Phase 2b**.
- email parser (stdlib `email`) — **Phase 2b**.
- Mistral OCR parser (external API) — **Phase 2b**.
- pptx parser — Wave B (architecture line 421 lists it but Wave A's eval corpus is PDF + xlsx + email).
- Gemini VLM fallback for image-only PDFs — Wave B (architecture line 423).
- Doc-chain detection (step 5.5 in architecture) — own phase or rolled into Phase 5.
- ColPali for visual-heavy pages (step 11) — Phase 4.
- Chunking, embedding, indexing — Phases 3 + 4.
- Upload UI (drag-drop SSE) — **Phase 10a**.
- Authentication on `POST /files` — same `X-Test-Workspace` header as 1a/1b/1c until an auth phase opens.
- `audit_log` writes on file mutations — **Phase 9**.

#### Decisions (locked at G1)

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | Files-vs-blob storage split | **Postgres holds metadata**; **MinIO holds bytes** under `raw_files/<sha256>` (architecture line 853). `files.object_key` is the MinIO key; we never store file bytes in PG. | Postgres rows stay tiny; MinIO handles blob storage efficiently. Standard split. |
| 2 | Content-hash keying | **sha256 over raw bytes** stored as `content_sha` (lower-hex). Used as both the MinIO object key and a `(workspace_id, content_sha)` partial unique constraint among `lifecycle_state != 'deleted'`. | Per-stage idempotency invariant (architecture line 874): a duplicate upload returns the existing `files` row instead of creating a second one. Cross-workspace duplicates are independent (each workspace owns its own copy of bytes — workspace isolation invariant). |
| 3 | `files.lifecycle_state` machine | `queued → parsing → parsed` (happy path) · `* → failed` · `* → deleted` (soft delete). Transitions enforced at the application layer via the `file_lifecycle` append-only log. | Matches the architecture §6 lifecycle pattern. Adding states later (e.g., `reextracting` per architecture line 288) is additive. |
| 4 | `file_lifecycle` shape | Append-only audit trail; one row per state transition: `(id, file_id, workspace_id, from_state, to_state, event, payload jsonb, created_at)`. `GRANT SELECT, INSERT` only; `REVOKE UPDATE, DELETE` mirroring Phase 1b's `schema_versions` immutability. | Per-stage checkpointing for replay safety (architecture line 875). Worker reading `lifecycle_state='parsed'` knows to no-op. |
| 5 | `raw_pages` immutability | `GRANT SELECT, INSERT` only; `REVOKE UPDATE, DELETE`. Each page is content-hash keyed (`raw_pages.content_sha`) so a re-parse of the same file produces identical rows (idempotent re-runs). | Architecture line 425: "raw_pages table — IMMUTABLE, content-hash keyed". |
| 6 | Procrastinate task naming + interface | One task: `parse_file(file_id: str)` registered on the `kb` queue. Lease: 30 min (Docling parses can take minutes for large PDFs). Per-stage idempotency: if `files.lifecycle_state == 'parsed'` at task start, return immediately. | Architecture line 867. The task takes only `file_id` so a serialized task arg stays tiny — the worker reads everything else from PG. |
| 7 | Worker → workspace context | Worker reads `files.workspace_id`, calls `SET LOCAL app.workspace_id = <uuid>` before any subsequent query. Same pattern as the FastAPI `WorkspaceMiddleware` but driven by the task arg. | RLS still applies to worker queries (decision #6 of Phase 0). A worker bug that drops the SET LOCAL would be unable to leak across workspaces. |
| 8 | Admin upload endpoint shape | `POST /files` accepts EITHER multipart/form-data (the file content + display name) OR JSON `{minio_object_key, name}` (for pre-uploaded content used by tests + Phase 10a's streaming-upload UI). Response: `201 Created` with `files` object including `lifecycle_state='queued'`. Idempotency-Key required. | Both modes are useful: multipart for casual use, JSON for streamed/pre-uploaded use. The two-mode endpoint covers both without a separate URL. |
| 9 | Idempotency on upload | Two layers: (a) the `Idempotency-Key` header (Phase 0's mechanism) — replay returns cached response. (b) content-hash dedup — same content_sha in same workspace + still active → return the existing `files` row with `lifecycle_state` as-is (could be `queued`, `parsing`, `parsed`). | Stripe-style dedup at the HTTP layer + content-addressed dedup at the storage layer. Both required for safe upload retries. |
| 10 | Parser interface | `class Parser(Protocol)`: `can_handle(mime_type: str, magic_bytes: bytes) -> bool` and `async def parse(self, file_bytes: bytes, *, file_id: str, workspace_id: str) -> ParsedDocument`. Returns `{pages: [{page_number, text, layout_json}]}`. The dispatcher iterates registered parsers, picks the first that `can_handle`. | Protocol (PEP 544) gives us static-typed pluggability without an ABC's runtime overhead. The dispatcher pattern lets Phase 2b add parsers via registration, not branching. |
| 11 | Docling integration | Use `docling` PyPI package. Run synchronously in a worker thread (Docling is CPU/IO-bound, not async). Pin to `>= 2.0` (released 2026-01). | Docling 2.x is the supported line. Synchronous in a thread keeps Procrastinate's async loop healthy. |
| 12 | RLS on 4 new tables | Each carries own `workspace_id` + own `CREATE POLICY` (same predicate). Cross-phase invariant grows from 7 → 11 workspace-scoped tables. | Belt-and-braces (Phase 1c #11 continued). |
| 13 | Upload-time validation | Hard limits at upload: `≤ 100 MB` per file (rejected with `413 Payload Too Large`); `mime_type` from request's `Content-Type` (or sniffed from magic bytes if multipart); `name` 1–500 chars. | Hard limits keep one bad upload from DoS'ing the worker. 100 MB covers the largest CUAD/SEC documents we need for the demo. |
| 14 | `parse_artifacts` shape | `(id, file_id, workspace_id, kind text CHECK IN ('layout','tables','ocr_confidence'), object_key text, created_at)`. The artifact JSON lives in MinIO under `parse_artifacts/<file_id>/<kind>.json`. | Architecture line 854. Heavy JSON (layout per page) keeps PG row sizes small while letting clients fetch the artifact directly from MinIO if needed. Phase 2a only writes `kind='layout'` (Docling's layout output); 2b will add `tables` and `ocr_confidence`. |
| 15 | Failure handling | Parser exception → worker writes `file_lifecycle` row `from='parsing' to='failed'` with `payload={error_class, message, traceback_head}`; updates `files.lifecycle_state='failed'`. Procrastinate retries handled by lease expiry (auto-retry on death); explicit retries by re-enqueueing via `POST /files/:id/retry` — **deferred to 2b** (rare in practice; demo can re-upload). | Failures are visible (lifecycle history) without auto-retry-loops on permanent failures (corrupt PDF). |

#### Repo layout delta after Phase 2a G4

```
emerging-kb/
├── migrations/sql/
│   └── 0008_parse_layer.sql              ← NEW (4 tables)
├── pyproject.toml                        ← MUTATED (add docling, python-magic, aiofiles)
├── src/kb/
│   ├── api/
│   │   ├── main.py                       ← include files_router
│   │   └── files.py                      ← NEW (5 endpoints)
│   ├── domain/
│   │   ├── files.py                      ← NEW (pydantic + repo functions)
│   │   └── raw_pages.py                  ← NEW (repo for raw_pages reads)
│   ├── parsers/                          ← NEW package
│   │   ├── __init__.py                   ← Parser Protocol + register() + dispatch()
│   │   └── docling_parser.py             ← Docling implementation
│   ├── storage/
│   │   └── files.py                      ← NEW (MinIO helpers: upload + read + key derivation)
│   └── workers/
│       └── tasks.py                      ← NEW (parse_file Procrastinate task)
└── tests/
    ├── test_files_crud.py                ← NEW (~10 tests)
    ├── test_parse_dispatch.py            ← NEW (~5 tests — parser registration + routing)
    ├── test_parse_pdf_docling.py         ← NEW (~5 tests — Docling against a fixture PDF)
    ├── test_raw_pages.py                 ← NEW (~5 tests — read endpoints + immutability)
    ├── test_files_lifecycle.py           ← NEW (~5 tests — state machine + audit trail)
    └── specs/phase_2a.md                 ← NEW
```

#### `0008_parse_layer.sql` shape (locked at G1)

(Full DDL drops at G4; here's the head — same RLS pattern as schemas/schema_versions.)

```sql
CREATE TABLE files (
    id              uuid         NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    workspace_id    uuid         NOT NULL,
    name            text         NOT NULL CHECK (length(name) BETWEEN 1 AND 500),
    content_sha     text         NOT NULL CHECK (length(content_sha) = 64),
    object_key      text         NOT NULL,        -- MinIO key (raw_files/<sha>)
    mime_type       text         NOT NULL,
    size_bytes      bigint       NOT NULL CHECK (size_bytes >= 0),
    doc_type        text         NULL,            -- classified type, nullable until classifier runs (later phase)
    lifecycle_state text         NOT NULL DEFAULT 'queued'
                                 CHECK (lifecycle_state IN ('queued','parsing','parsed','failed','deleted')),
    created_at      timestamptz  NOT NULL DEFAULT now(),
    updated_at      timestamptz  NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX files_workspace_sha_active_idx
    ON files (workspace_id, content_sha) WHERE lifecycle_state <> 'deleted';

CREATE INDEX files_workspace_lifecycle_idx
    ON files (workspace_id, lifecycle_state);

-- RLS + GRANT same pattern as schemas.
```

Plus `file_lifecycle` (GRANT SELECT+INSERT + REVOKE UPDATE+DELETE), `raw_pages` (same immutability), `parse_artifacts`.

#### Endpoint preview (locked at G1; full contracts at G2)

| Method | Path | Notes |
|---|---|---|
| `POST` | `/files` | multipart OR JSON `{minio_object_key, name}`; computes sha256; dedupes; enqueues `parse_file`. Idempotency-Key required. |
| `GET` | `/files` | List active files in workspace; pagination same as 1a. |
| `GET` | `/files/:id` | File metadata + lifecycle history. |
| `GET` | `/files/:id/pages` | Paginated raw_pages list (text + layout). |
| `DELETE` | `/files/:id` | Soft delete; MinIO blob retained. |

#### Phase 2a G5 — what "green" means

`scripts/verify_phase_2a.sh` adds to Phase 0+1a+1b+1c verify checks:
1. After migrate exits 0: `\dt` includes `files`, `file_lifecycle`, `raw_pages`, `parse_artifacts`; RLS forced on all 4; immutability GRANTs on `file_lifecycle` and `raw_pages` (SELECT+INSERT only).
2. `curl POST /files` with a tiny test PDF → 201 with `lifecycle_state='queued'`.
3. Within ~10s (worker polls), `files.lifecycle_state == 'parsed'`; `raw_pages` has ≥ 1 row.
4. `curl GET /files/:id` returns lifecycle history showing `queued → parsing → parsed`.
5. Duplicate POST same content_sha → returns existing file (no second row).
6. `GET /files` as workspace B doesn't see workspace A's files (RLS isolation).
7. `pytest tests/` green: 142 (prior phases) + ~30 new = ~172 total.

#### Pre-G2 consistency review checklist

Before G2 opens (after this plan is signed off), verify:
- [ ] Architecture line 417–425 routing list — Phase 2a covers Docling (digital PDF) only; 2b will add Mistral OCR + xlsx + email.
- [ ] Architecture line 425 — `raw_pages` immutable + content-hash keyed (decision #5).
- [ ] Architecture line 874 — per-stage idempotency via `file_lifecycle` (decision #4).
- [ ] api_contracts §0 conventions all apply unchanged.
- [ ] No `audit_log` writes (Phase 9 owns).
- [ ] No chunking / embedding / indexing references (Phase 3+).

#### Sign-off

When Aniket approves this plan, the Phase 2a G1 cell in §5 flips 🟡 → ✅ and Phase 2a G2 opens (5 endpoint contracts landing in `docs/api_contracts.md` §5 — the current §5 placeholder index shifts to §6, changelog to §7). Sign-off recorded in §9.

---

### 5.6 Phase 2b plan — Additional parsers (G1 ✅ + G2 ✅ + G3 ✅ + G4 ✅ + G5 ✅ SIGNED OFF)

> **Status:** G1 ✅ signed off 2026-05-23 by Aniket. Plan locked. Branch: `phase-2b/parse-formats` off `main` (Phase 2a merged as PR #5; tag `phase-2a-complete`).

#### Scope

Phase 2b adds three parsers behind the same `kb.parsers.Parser` Protocol that Phase 2a established. **No new HTTP endpoints** — the existing `POST /files` widens its mime-type whitelist to accept xlsx + email; the dispatcher (with first-match-wins ordering) picks the right parser per upload. Phase 2a's E2E pipeline (`upload → MinIO → Procrastinate → parse → raw_pages`) carries each new parser transparently.

**In scope:**
- **xlsx parser** (`kb/parsers/xlsx_parser.py`): handles `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` (and the ZIP-magic `PK\x03\x04` fallback). One `raw_pages` row per sheet — `page_number = sheet index (1-based)`; `text = TSV-rendered cells`; `layout_json = {sheet_name, rows, cols}`. Uses `openpyxl` (already in lock via Docling).
- **email parser** (`kb/parsers/email_parser.py`): handles `message/rfc822` (and the header-sniff fallback: `^[A-Z][a-zA-Z-]+:\s` in the first line). One `raw_pages` row — `page_number = 1`; `text = "From: …\nTo: …\nSubject: …\n\n<body>"`; `layout_json = {headers, attachments: [{filename, content_type, size_bytes}]}`. Body extraction prefers `text/plain` body parts; falls back to `text/html` stripped via stdlib `html.parser`. Attachments **not** recursively parsed in 2b (a PDF attachment doesn't auto-trigger Docling on its bytes — that's recursive ingestion, deferred).
- **Mistral OCR parser** (`kb/parsers/mistral_ocr_parser.py`): adapter class for the Mistral OCR 3 HTTP API per architecture line 419. Constructor takes an optional `http_client` for test injection. Test suite uses a mock client returning a pre-canned per-page response. Real API integration gated on `KB_MISTRAL_API_KEY` env var (if unset, `MistralOCRParser.can_handle()` returns False — the parser is effectively disabled). Registration order: Docling FIRST for `application/pdf` (which already includes RapidOCR fallback via docling); Mistral OCR registered AFTER (currently inert because it'd never win the dispatch with Docling registered first, but ready to swap in when a force-route mechanism lands).
- **Mime whitelist expansion** in `kb/api/files.py`: the `_PHASE_2A_WHITELIST` set widens to include the xlsx + email mimes. (415 errors stay for genuinely unsupported types like text/plain.)
- **Magic-byte sniffing** when `Content-Type` is missing or `application/octet-stream` — dispatcher uses the first 8 bytes to pick a parser.

**Out of scope (deferred):**
- **Real Mistral OCR API integration** — adapter class ships; real API call works the instant `KB_MISTRAL_API_KEY` is set, but Phase 2b's CI/tests never call the real API. Phase 2c (or whenever a key is procured) flips this on.
- **Force-parser route mechanism** (`?parser=mistral_ocr` query param on POST /files) — would let callers force Mistral OCR over Docling for a known-scanned PDF. Deferred to Phase 2c if needed; current scanned-PDF fallback uses Docling+RapidOCR.
- **Attachment recursive ingestion** (email with a PDF attachment auto-creating a child file row) — a useful future feature but its own design decision (audit trail of the parent/child, doc-chain detection). Out of scope.
- **pptx** (architecture line 421 lists it but Wave A's eval corpus doesn't need it) — Wave B.
- **Gemini VLM fallback for image-only PDFs** (architecture line 423) — Wave B.
- **`audit_log` writes** on new parser invocations — Phase 9.

#### Decisions (locked at G1)

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | xlsx page model | **One `raw_pages` row per sheet** (page_number = sheet index 1-based; text = TSV-rendered cells). Empty sheets still emit a row with empty text (preserves sheet-count fidelity for the citation layer). | Spreadsheets don't have pages; sheets are the closest analog. TSV keeps it parseable without a structured representation that we don't yet have a place for. Phase 3 chunking + Phase 6 schema extraction can read TSV. |
| 2 | email page model | **One `raw_pages` row** with combined headers + body. Layout JSON stores parsed headers + attachment metadata. | Emails don't have pages either; one row matches their "single conceptual document" nature. Headers in the text body let Phase 3 chunkers include them in retrieval context. |
| 3 | xlsx text rendering | `\t`-separated cells per row, `\n`-separated rows, `\n\n` between sheets. Includes the sheet name as a header line: `# Sheet: <name>\n<rows>`. | Simple, deterministic, diff-friendly. No pandas-styling. Cell values stringified via `str(v)` (preserves dates as their ISO form via openpyxl's default). |
| 4 | email text rendering | `From: <addr>\nTo: <addrs>\nCc: <addrs>\nSubject: <subj>\nDate: <date>\n\n<body>`. Body: prefer `text/plain` parts joined by `\n\n`; if none, fall back to `text/html` stripped via stdlib `html.parser`. | Mirrors how a user sees an email. Stdlib-only — no `beautifulsoup4` or other HTML libs. |
| 5 | Email attachments | Recorded in `layout_json.attachments[]` as `{filename, content_type, size_bytes}`. Bytes NOT extracted into a child `files` row. | Recursive ingestion is a separate design decision (parent/child file relationships, doc-chains). Deferred. |
| 6 | Magic-byte sniffer at upload | `kb/api/files.py` reads the first 8 bytes after `await upload.read()` and overrides `mime_type` if Content-Type is `application/octet-stream` or empty. PDF (`%PDF-`), ZIP/xlsx (`PK\x03\x04`), email (`^[A-Z][a-zA-Z-]+:`). | Many file uploads ship without a meaningful Content-Type. Robustness over relying on the caller. |
| 7 | Mistral OCR routing | **Registered after Docling** so it never wins dispatch-by-mime today. Adapter class proves the Protocol works; real activation comes when a force-parser mechanism lands (Phase 2c or later). | Avoids a routing-strategy decision that's premature without a key + cost story. |
| 8 | Mistral OCR mock | Test suite uses `MistralOCRParser(http_client=MockHttpClient(...))`. The mock returns a fixture JSON shaped like Mistral's real per-page response — proves we parse the API response shape correctly. | Standard pattern for external-API adapters. No live API calls in CI. |
| 9 | Mistral OCR `can_handle` | Returns `False` if `KB_MISTRAL_API_KEY` is unset (parser disables itself when no key). Returns `True` for `application/pdf` magic when the env var is present. | Self-disabling parser — registry can include it without breaking anything. |
| 10 | xlsx mime whitelist | Two strings: `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` (.xlsx) and `application/vnd.ms-excel` (.xls). `.xls` is forwarded to the parser; `openpyxl` may raise; if so, surface as `ParseError` → `parsing→failed` lifecycle event. | Generous accept on the mime; let the parser handle the actual format check. |
| 11 | Email mime whitelist | `message/rfc822`. Magic sniff: first 200 bytes contain a header-like `^[A-Z][a-zA-Z-]+:\s`. | Tightly RFC-anchored; the magic sniff covers `.eml` files uploaded as `application/octet-stream`. |
| 12 | Cross-parser body-bytes contract | Every parser receives `bytes`. Workers fetch the bytes from MinIO once and pass to `parser.parse(file_bytes, ...)`. No per-parser MinIO-key handling. | Phase 2a's worker contract is unchanged. |
| 13 | Empty-content handling | Each parser ensures at least one `raw_pages` row is emitted (even if the text is empty). `ParserRegistry.dispatch` raises `NoParserForMime` (not `ParseError`) if no parser matches → worker writes `parsing→failed` with payload `error_class='NoParserForMime'`. | Distinguishes "no parser for this format" (likely should have been rejected at upload, but defensive) from "parser failed on this content." |

#### Repo layout delta after Phase 2b G4

```
emerging-kb/
├── src/kb/
│   ├── api/
│   │   └── files.py                     ← MUTATED (mime whitelist + magic sniff)
│   └── parsers/
│       ├── __init__.py                  ← MUTATED (register_default_parsers adds 3 new)
│       ├── xlsx_parser.py               ← NEW
│       ├── email_parser.py              ← NEW
│       └── mistral_ocr_parser.py        ← NEW
└── tests/
    ├── fixtures/
    │   ├── tiny.xlsx                    ← NEW (~1 KB; 1 sheet, ~5 cells)
    │   └── tiny.eml                     ← NEW (~300 B; minimal RFC822 with one text/plain body)
    ├── test_parse_xlsx.py               ← NEW (~5 tests)
    ├── test_parse_email.py              ← NEW (~5 tests)
    ├── test_parse_mistral_ocr.py        ← NEW (~5 tests; all against mock)
    ├── test_files_crud.py               ← MUTATED (+2-3 tests: POST xlsx → 201 → page count matches sheets; POST email → 201 → headers in raw_page.text)
    └── specs/phase_2b.md                ← NEW
```

#### Endpoint contract delta (api_contracts.md §5.5)

The §5.5 description of `POST /files` already covers multipart + JSON modes. Phase 2b's only change: the §5.5 415 row's narrative widens to read "Phase 2a + 2b accept: `application/pdf`, `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`, `application/vnd.ms-excel`, `message/rfc822`." No new sub-section needed.

#### Phase 2b G5 — what "green" means

`scripts/verify_phase_2b.sh` adds to Phase 0+1a+1b+1c+2a verify checks:
1. `curl POST /files` with `tiny.xlsx` (multipart, `Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`) → 201; worker parses; `raw_pages` count == sheet count of `tiny.xlsx`.
2. `curl POST /files` with `tiny.eml` (multipart) → 201; worker parses; `raw_pages.text` includes `From:`, `Subject:`, body text.
3. Octet-stream upload of `tiny.xlsx` with content-type stripped → magic-sniff detects ZIP magic → routes to xlsx parser → 201.
4. Octet-stream upload of `tiny.eml` with content-type stripped → magic-sniff detects header pattern → routes to email parser → 201.
5. Mistral OCR adapter: pytest covers via mock — round-trip a fake API response into a `ParsedDocument`.
6. POST a text file (text/plain) still returns 415 — only the new whitelisted types are accepted.
7. `pytest tests/` green: 170 (existing) + ~15 new = ~185.

#### Pre-G2 consistency review checklist

Before G2 opens:
- [ ] Architecture line 417–425 routing — Phase 2b covers xlsx + email; Mistral OCR shipped as adapter (real activation deferred); pptx + Gemini VLM still Wave B.
- [ ] api_contracts §5.5 415 narrative widens (single contract delta).
- [ ] No new endpoints — verified by `grep '^router\.' kb/api/files.py` returns the same 5 endpoints.
- [ ] No `audit_log` writes (Phase 9 owns).
- [ ] Phase 2a's E2E pipeline still serves PDF correctly (cross-phase sweep at G5).

#### Sign-off

When Aniket approves this plan, the Phase 2b G1 cell in §5 flips 🟡 → ✅ and Phase 2b G2 opens (single contract delta in `docs/api_contracts.md` §5.5 mime whitelist narrative). Sign-off recorded in §9.

---

### 5.6.1 Phase 2c plan — Gemini OCR + strategy-driven parser dispatch (G1 ✅ + G2 ✅ + G3 ✅ + G4 ✅ + G5 ✅ SIGNED OFF)

> **Status:** G1 opens 2026-05-24. Physically slotted as §5.6.1 (adjacent to 2b in this doc); functionally a new top-level phase 2c — introduces multiple new system surfaces (new parser + sniff library + strategy-aware dispatcher + caller override + quality escalation + provenance metadata) on top of what 2b shipped. Same `phase-3/chunking-raptor` branch (5th commit-set after 3a/3b/3c/3b-bis). Naming convention matches §5.8.1 (Phase 3b-bis).
>
> **Motivation:** Build_tracker §5.6 #7 + #1166 documented "force-parser routing as Phase 2c-or-later" and "current scanned-PDF fallback uses Docling+RapidOCR." The user has concluded that OCR quality directly determines KB retrieval quality (garbage-in-garbage-out applies twice — to chunks AND to RAPTOR summaries that compound on chunks) and that Docling+RapidOCR's quality is insufficient for hard inputs (multilingual, handwriting, complex tables, mixed-layout). Phase 2c brings Gemini 2.5 Flash VLM as the OCR adapter + a cheapest-first routing strategy so the system uses Gemini only when the input actually needs it.

#### Scope

Phase 2c keeps Phase 2b's `Parser` Protocol untouched. It widens the **dispatcher** (currently first-match-wins) into a strategy-aware selector that consults a pre-flight text-layer sniff before routing PDF uploads, with a post-parse quality-escalation safety net and a caller-side override.

**In scope:**
- **`GeminiOCRParser`** (`src/kb/parsers/gemini_ocr_parser.py`) — renders each PDF page to a PIL image via `pypdfium2` at 150 DPI, calls `google.genai.Client.aio.models.generate_content` with the image + an OCR prompt (markdown output, table-preserving), returns one `ParsedDocument.pages[*]` entry per page. Per-page concurrency cap via `asyncio.Semaphore(4)`. Reuses the `google-genai` SDK already added at Phase 3c.
- **Pre-flight text-layer sniff** (`src/kb/parsers/text_layer_sniff.py`) — `def sniff_pdf_text_layer(buffer: bytes) -> SniffResult` returns `{avg_chars_per_page, page_count, has_text_layer}`. Uses `pypdfium2.PdfDocument(buffer).get_page(i).get_textpage().get_text_range()` per page. Bounded by `max_pages=10` for the heuristic (large docs sniff only the first N pages — cost vs. accuracy tradeoff).
- **Strategy-aware dispatcher** (mutate `src/kb/parsers/__init__.py`) — new `select_parser_for(*, mime_type, magic_bytes, file_bytes, strategy)` function. Reads `KB_PARSER_STRATEGY` env (default `auto`). Strategies:
  - `auto`: for PDFs, run sniff. If `avg_chars_per_page ≥ KB_PDF_TEXT_LAYER_THRESHOLD` (default 50) → Docling. Else → Gemini OCR. For non-PDF mimes, behavior unchanged (first-match wins per Phase 2b).
  - `docling_first`: always Docling for PDFs; escalate on bad quality (see below).
  - `gemini_first`: always Gemini OCR for PDFs (no sniff, no Docling).
  - `gemini_only`: Gemini OCR for PDFs, fail if `KB_GEMINI_API_KEY` is unset (no Docling fallback).
- **Quality escalation** (mutate `src/kb/workers/tasks.py::parse_file_impl`) — after Docling parses, score the result:
  - Total chars across all pages == 0 → escalate
  - `(printable_chars / total_chars) < 0.7` → escalate (garbled output)
  - Any individual page with `chars < 5` while others have `chars > 100` → escalate that page only (hybrid PDF: digital pages + 1 scanned page)
  - Escalation: re-parse via Gemini OCR (full doc or per-page depending on scope of failure). Both attempts recorded in `raw_pages.layout_json.provenance`.
- **Caller override** (mutate `src/kb/api/files.py::create_file`) — `POST /files?parser=<docling|gemini|auto>` query param. Defaults to `auto`. Passed through to the worker via task arg `forced_parser: str | None`. Worker bypasses dispatcher when set.
- **Provenance JSON in `raw_pages.layout_json`** — every parse writes:
  ```json
  {
    "provenance": {
      "strategy": "auto",
      "forced_parser": null,
      "tried": ["docling"],
      "chose": "docling",
      "reason": "text_layer_present (avg=2730 chars/page over 3 pages)",
      "quality_score": 0.94
    }
  }
  ```
  On escalation:
  ```json
  {
    "provenance": {
      "strategy": "auto",
      "tried": ["docling", "gemini_ocr"],
      "chose": "gemini_ocr",
      "reason": "docling output failed quality check: printable_ratio=0.42",
      "quality_score": 0.42
    }
  }
  ```
- **`.env.example` updates** — `KB_PARSER_STRATEGY` (default `auto`), `KB_PDF_TEXT_LAYER_THRESHOLD` (default 50, commented), `KB_OCR_MODEL` (default `gemini-2.5-flash`, commented), `KB_OCR_CONCURRENCY` (default 4, commented).

**Out of scope (deferred):**
- **Workspace-level OCR policy** (`workspaces.default_ocr_strategy` column + per-workspace override) — needs workspace_settings infrastructure; lands at Phase 5 / workspace mgmt.
- **PNG vs JPEG image format toggle** — defaults to PNG (lossless, slightly larger payloads). JPEG quality knob deferred.
- **Multi-page batched OCR call** (one API call covering N pages) — defaults to per-page calls (simpler, parallelizable). Batched mode is a cost optimization for later.
- **Mistral OCR adapter activation** — stays registered + inert. Phase 2c's strategy slots Gemini OCR ahead; Mistral is a 4th option deferred to "when force-route covers it" (i.e., now, but we don't wire it in since Gemini is the chosen path).
- **Recursive ingestion of email attachments** — same deferral as Phase 2b #5.
- **`audit_log` writes** on dispatcher decisions — Phase 9.

#### Decisions (locked at G1; changes require re-opening G1)

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | OCR model | **`gemini-2.5-flash`** (configurable via `KB_OCR_MODEL`). | Flash is the multimodal Gemini for image input + text output. Pro reserved for downstream reasoning (RAPTOR L3 in later phases). Architecture line 423 already references "Gemini 2.5 Flash VLM" as the planned OCR path. |
| 2 | PDF → image library | **`pypdfium2`** (Apache-2.0; pure-binary wheel, no system deps). | Modern, fast, permissive license. `pdf2image` requires system Poppler; `pymupdf` is AGPL. `pypdfium2` is also what `pdfminer.six` is being replaced by. |
| 3 | Render DPI | 150 DPI per page (configurable via `KB_OCR_RENDER_DPI`; not exposed in .env.example until tuned). | 150 DPI is the sweet spot for OCR quality vs. image size (~1.5MB per A4 page as PNG). 300 DPI doubles latency + cost without measurable quality gain for typed text. |
| 4 | Image format to Gemini | **PNG** (lossless). | Tables + thin lines suffer with JPEG compression. PNG is ~30% larger but Gemini Flash's per-input-token cost is independent of image bytes (it's normalized internally). |
| 5 | OCR prompt | Fixed prompt: `"Extract ALL text from this document page. Preserve tables as markdown tables, headings as # / ## / ###, lists as - bullets. Return only the extracted text, no preamble or commentary."` | Markdown is what Docling produces too — keeps downstream chunkers + contextualizers indifferent to which parser ran. |
| 6 | Per-page concurrency | `asyncio.Semaphore(4)` (configurable via `KB_OCR_CONCURRENCY`). | Gemini Flash free tier is 15 RPM. 4-way concurrency stays under for typical 5-10 page docs while parallelizing the slow part (vision inference is ~1-3s per page). |
| 7 | Dispatcher strategy enum | `KB_PARSER_STRATEGY ∈ {auto, docling_first, gemini_first, gemini_only}`, default `auto`. | Four explicit modes cover the demo's needs (auto for most users), CI determinism (`docling_first` skips Gemini API costs in tests), force-Gemini for benchmarking (`gemini_first`), and operator opt-out of Docling for known-bad-input corpora (`gemini_only`). |
| 8 | Pre-flight sniff threshold | `KB_PDF_TEXT_LAYER_THRESHOLD = 50` chars/page (averaged over first 10 pages). | A typed 1-page A4 is ~3000 chars. A scanned PDF page returns ~0-20 chars from the text layer (often just a stray header). 50 is a generous floor that won't trip on edge cases. |
| 9 | Sniff bounding | `max_pages_sniffed = 10`. Large docs (>10 pages) sniff only the first 10. | Sniff is cheap (~10ms/page) but bounded so a 1000-page PDF doesn't cost 10s before parsing starts. First 10 pages are representative of the doc's text-layer-ness. |
| 10 | Quality-escalation criteria | Total chars == 0 → escalate. `printable_chars/total_chars < 0.7` → escalate (garbled). Per-page: `chars < 5` while peers have `> 100` → escalate that page only. | Three signals catch the realistic failure modes: empty (everything's scanned), garbled (OCR ran but on bad input), hybrid (most pages digital, one page scanned). |
| 11 | Caller override | `POST /files?parser=<docling\|gemini\|auto>` query param. Defaults to `auto`. Worker accepts via `forced_parser` task arg. Invalid values → 400. | Three explicit values (no "anthropic" since this is the parser layer, not contextualizer). Per-call override defeats the strategy env var. Useful for `/files?parser=gemini` to force-run on edge-case demos. |
| 12 | Provenance JSON shape | `raw_pages.layout_json.provenance = {strategy, forced_parser, tried[], chose, reason, quality_score}`. | Audit trail without a new column. `layout_json` is already free-form JSON per Phase 2a #6. Dashboards filter rows by `provenance->>'chose'`. |
| 13 | Failure semantics | If `auto`/`gemini_first` picks Gemini but `KB_GEMINI_API_KEY` is unset → worker writes `parsing→failed` with `error_class='OCRConfigError'`. Strategy `gemini_only` + no key → same. `docling_first` is always safe (no key needed). | Loud-fail on misconfig at parse time, not at dispatch (need bytes to sniff). Surfacing the wrong-config message in the lifecycle event keeps debug-loop short. |
| 14 | Test fixture for scanned PDFs | `tests/fixtures/tiny_scanned.pdf` — synthetic: render `tiny.pdf` to PNG via pypdfium2, then re-encode as an image-only PDF (no text layer). Test assertion is "routes to Gemini path", not "Gemini extracts perfectly" (mocked). | Avoids the licensing/provenance question of using a real scanned PDF. Synthetic fixture is reproducible from `tiny.pdf` + a one-shot generator script. |
| 15 | Mistral OCR adapter | **Untouched.** Still registered after Docling; still inert. Phase 2c slots Gemini OCR via the strategy + sniff, not via parser-registration order. | Keeps 2b's Mistral adapter as a "drop-in replacement if Gemini hits cost ceiling" without rewiring routing. |

#### Repo layout delta after Phase 2c G4

```
emerging-kb/
├── pyproject.toml + uv.lock                   ← MUTATED (add pypdfium2)
├── src/kb/
│   ├── api/
│   │   └── files.py                           ← MUTATED (?parser= query param + 400 on invalid)
│   ├── parsers/
│   │   ├── __init__.py                        ← MUTATED (select_parser_for + strategy enum + sniff invocation)
│   │   ├── gemini_ocr_parser.py               ← NEW (~150 LOC: PDF→PNG via pypdfium2 + Gemini Flash VLM call + concurrency cap)
│   │   └── text_layer_sniff.py                ← NEW (~50 LOC: pypdfium2 text-extraction sniff with max_pages bound)
│   └── workers/
│       └── tasks.py                           ← MUTATED (quality_score + escalation re-parse + provenance JSON write + forced_parser arg)
├── tests/
│   ├── fixtures/
│   │   ├── tiny_scanned.pdf                   ← NEW (synthetic; generated from tiny.pdf)
│   │   └── scripts/make_tiny_scanned.py       ← NEW (one-shot generator script, not run in CI)
│   ├── test_parse_gemini_ocr.py               ← NEW (~6 tests: prompt shape, response parsing, model literal, per-page concurrency, error handling, missing-key error)
│   ├── test_text_layer_sniff.py               ← NEW (~3 tests: digital PDF returns avg > 50, scanned returns ~0, page-count bounded)
│   ├── test_parser_dispatcher_strategy.py     ← NEW (~5 tests: auto+digital→docling, auto+scanned→gemini, docling_first, gemini_only_no_key→err, invalid strategy→err)
│   ├── test_parse_quality_escalation.py       ← NEW (~4 tests: empty docling→escalate, garbled→escalate, hybrid per-page escalation, provenance JSON shape)
│   ├── test_files_crud.py                     ← MUTATED (+2 tests: ?parser=gemini override + invalid value → 400)
│   └── specs/phase_2c.md                      ← NEW
└── scripts/
    └── verify_phase_2c.sh                     ← NEW (separate from verify_phase_2b.sh — adds compose stack invocation with KB_GEMINI_API_KEY check + scanned-PDF E2E + dispatcher provenance assertions)
```

No SQL migration. No new domain module. No lifecycle change (parse_file is still `queued → parsing → parsed | failed`).

#### Endpoint contract delta (api_contracts.md §5.5)

Two single-line deltas to §5.5 `POST /files`:
1. Add the `?parser=<docling|gemini|auto>` query param under the "Query parameters" subsection. Default `auto`. Invalid → 400 with `error_class='InvalidParserOverride'`.
2. The 200/201 response body's `parser` field (already part of the file resource per §5.5) gains a new possible value: `'gemini_ocr'`. The accepted set widens to `'docling' | 'xlsx' | 'email' | 'gemini_ocr' | 'mistral_ocr'`.

#### Phase 2c G5 — what "green" means

`scripts/verify_phase_2c.sh` (new):
1. Compose smoke — same shape as 2b, plus a worker-env probe for `KB_PARSER_STRATEGY` + `KB_OCR_MODEL` + `KB_GEMINI_API_KEY` presence.
2. `psql` confirms `raw_pages.layout_json` is JSONB (no schema change but documented invariant).
3. **Auto-strategy digital path:** `POST tiny.pdf` → routes to Docling → `provenance.chose='docling'` + `provenance.tried=['docling']` + `quality_score > 0.7`.
4. **Auto-strategy scanned path:** `POST tiny_scanned.pdf` → sniff says scanned → routes to Gemini OCR (gated on `KB_GEMINI_API_KEY` presence; verify-skip with `[skip]` if unset) → `provenance.chose='gemini_ocr'` + `provenance.reason` includes `'text_layer_absent'`.
5. **Caller override:** `POST tiny.pdf?parser=gemini` → routes to Gemini OCR even though sniff says digital → `provenance.forced_parser='gemini'`.
6. **Invalid override:** `POST tiny.pdf?parser=bogus` → 400 with `error_class='InvalidParserOverride'`.
7. **Quality escalation:** `POST` a fixture PDF where Docling extracts garbled output → escalates → `provenance.tried=['docling', 'gemini_ocr']` + `provenance.chose='gemini_ocr'`. (Skipped if no `KB_GEMINI_API_KEY`.)
8. **`gemini_only` no-key failure:** with `KB_PARSER_STRATEGY=gemini_only` + `KB_GEMINI_API_KEY` unset → lifecycle event `parsing_failed` with `error_class='OCRConfigError'`.
9. `pytest tests/` green: 238 (existing) + ~18 new = ~256.
10. Cross-phase sweep `verify_phase_{0,1a,1b,1c,2a,2b,2c,3a,3b,3c}.sh` all green (10 scripts now).

#### Pre-G3 consistency review checklist

Before G3 opens:
- [ ] Architecture line 417–425 routing — line 423's "Gemini 2.5 Flash VLM (image-only PDF, very poor OCR)" stays accurate (we run it on scanned PDFs broadly, not just "very poor OCR" cases — the line predates the sniff design).
- [ ] api_contracts §5.5 deltas drafted (two single-line changes).
- [ ] `.env.example` widens with `KB_PARSER_STRATEGY` + commented overrides.
- [ ] No regression to Phase 2b's Mistral adapter (untouched, still registered).
- [ ] Phase 2a's worker contract (`parser.parse(file_bytes, ...)`) unchanged — strategy + sniff happen at dispatcher level, parsers stay simple.
- [ ] Provenance JSON shape documented in `raw_pages.layout_json` invariants (Phase 2a #6).
- [ ] `layout_json` is already JSONB per Phase 2a; no migration needed.
- [ ] Phase 3b/3b-bis/3c untouched — they read `raw_pages.text` regardless of which parser produced it (provenance is metadata only).

#### Sign-off

When Aniket approves this plan, §5 gains a new row for Phase 2c, G1 flips 🟡 → ✅, and G2 opens (two single-line deltas to `docs/api_contracts.md` §5.5 — query param + parser enum widening). Estimated wall-clock: ~6-8 hr across G3 + G4 + G5 combined.

---

### 5.7 Phase 3a plan — Chunking (G1 ✅ + G2 ✅ + G3 ✅ + G4 ✅ + G5 ✅ SIGNED OFF)

> **Status:** All 5 gates green 2026-05-23. Plan + contract delta + 16 red skeletons + working implementation + verify_phase_3a.sh 18/18 + cross-phase sweep (124/124 cumulative). Branch: `phase-3/chunking-raptor` off `main`. **Ready to merge** as the FIRST commit-set on the Phase 3 branch (3b + 3c follow on the same branch as additional commit-sets per the split-decision §9 entry).

#### Scope

Phase 3a takes a `parsed` file (raw_pages already populated by Phase 2a/2b parsers) and produces the layout-aware **`chunks`** table that all downstream retrieval channels consume (BM25, dense embedding, RAPTOR clustering input). This is the first internal worker stage that doesn't add an HTTP endpoint — the chunker is automatic post-parse, driven by Procrastinate task chaining.

**In scope:**
- **`0009_chunks.sql` migration** — `chunks` table (workspace-scoped, RLS day-1, immutable: REVOKE UPDATE/DELETE on kb_app). Columns: `id uuid PK`, `file_id uuid FK`, `workspace_id uuid`, `chunk_index int` (0-based ordering within file), `text text`, `source_page_numbers int[]` (every raw_page that contributed bytes), `token_count int`, `content_sha text` (sha256 of `text`), `created_at timestamptz`. Indexes: `(workspace_id)`, `(file_id, chunk_index)`. UNIQUE `(file_id, chunk_index)`.
- **Lifecycle state extension** — `files.lifecycle_state` CHECK widens to include `chunked`. Transitions allowed: `parsed → chunked` (success), `parsed → failed` (chunker error). The terminal `'ready'` state lands in Phase 3c after RAPTOR build.
- **`kb/chunking/__init__.py`** — `Chunker` module with one function `chunk_pages(raw_pages: list[RawPage], *, budget_tokens: int = 2500, overlap_tokens: int = 250) -> list[Chunk]`. Pure: no DB, no I/O. Token counting via `tiktoken.get_encoding("cl100k_base")`. Layout-aware: respects raw_page boundaries; joins small pages (< budget/4) with their neighbours; never splits a single page mid-stream unless it exceeds the budget on its own.
- **Worker stage `chunk_file_impl(file_id)`** in `kb/workers/tasks.py` — reads file row + raw_pages, sets workspace context, calls `chunk_pages()`, INSERTs `chunks` rows, transitions lifecycle to `chunked` with event `chunking_done` carrying `{chunk_count, total_tokens}`. Idempotent: returns immediately if lifecycle is already `chunked`. Wrapped in a `@procrastinate_app.task(name="chunk_file", queue="kb")`.
- **Task chaining** — `parse_file_impl()`'s success path defers `chunk_file(file_id)` after writing the `parsed` lifecycle event. Done inside the same task as a Procrastinate `defer` — cheap and matches architecture's "chained worker stage" pattern.
- **Failure mode** — chunker exceptions → `parsed→failed` lifecycle event with `event='chunking_failed'`. Same `_mark_failed` shape Phase 2a uses, just with `from_state='parsed'`.
- **Empty input handling** — file with `raw_pages.count() == 0` shouldn't happen post-2a (every parser emits ≥1 row per decision #13), but defensive: raises `ChunkingError("empty raw_pages")` → fails the file rather than silently producing zero chunks.
- **Cross-parser uniformity** — chunker doesn't know the source parser. xlsx files arrive as multiple raw_pages (one per sheet); the chunker treats each sheet as a layout unit, joining small sheets, splitting huge ones at row boundaries (`\n` separator). Email files arrive as a single raw_page that may or may not need splitting — most emails fit in one chunk.

**Out of scope (deferred):**
- Contextual Retrieval prefix LLM call → **Phase 3b**.
- Embedding calls + `chunk_embeddings` table → **Phase 3c**.
- RAPTOR tree build → **Phase 3c**.
- HNSW + BM25 indexes on `chunks.text` → **Phase 4** (architecture §5 step 9; Phase 3a stops at producing chunk rows without search-time indexes).
- Force-rechunk admin endpoint (`POST /files/:id/rechunk`) → Phase 4 (paired with re-index endpoint).
- Custom per-doc-type chunking (legal contracts get clause-aware chunks, xlsx gets row-aware chunks) → Wave B / Phase 5 (atomic-unit extraction owns clause/transaction boundaries; Phase 3a is the generic token-budgeted chunker).
- True Jina-style late chunking (token-level embeddings aggregated to chunk vectors) → Wave B optimization. Architecture §5 step 6 uses "late chunking" terminology loosely; BGE-M3 and Gemini Embedding 001 don't expose per-token outputs. Phase 3a implements **layout-aware token-bounded chunking**, the practical approximation.
- `audit_log` writes on chunk_file completion → Phase 9.

#### Decisions (locked at G1; changes require re-opening G1)

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | Chunk budget | **2500 tokens default**, configurable via `KB_CHUNK_TOKENS` env (test fixture overrides to 200). | Mid-range of architecture §5 step 6's "~2–4K tokens" guidance. Leaves headroom for Phase 3b's contextual prefix (~75 tokens) plus retrieval-time concatenation of neighbours without bumping the embedder's 8K-token context limit. Lower defaults (256–512) hurt recall on multi-hop queries per the [Anthropic Contextual Retrieval write-up](https://www.anthropic.com/news/contextual-retrieval). |
| 2 | Overlap policy | **Rolling 10% (250 tokens) overlap** between consecutive chunks within the same file. No overlap across files. | RAG papers consistently show non-zero overlap improves recall on queries that span chunk boundaries. 10% is the LangChain/LlamaIndex industry default; large enough to recover boundary context, small enough not to inflate the chunk count beyond ~1.1×. |
| 3 | Tokenizer | **`tiktoken` `cl100k_base`** (gpt-4 / gpt-3.5-turbo encoder). | Stable, fast, hard dep already pulled by other Anthropic-ecosystem libraries. Token counts are *approximate* for non-OpenAI embedders but within ±5% of BGE-M3 / Gemini Embedding tokenizers — adequate for budgeting. Anthropic's tokenizer isn't public; cl100k_base is the standard proxy. |
| 4 | Layout-aware boundary rule | The chunker treats each `raw_pages` row as a **layout unit** with hard "do-not-split" preference. Splitting WITHIN a page is allowed only when the page on its own exceeds the budget; in that case, split on the largest paragraph-break (`\n\n`) closest to the budget point. | Respects parser output. For PDF parsed by Docling, one raw_page == one printed page (already paragraph-segmented). For xlsx, one raw_page == one sheet (sheet boundary preserved). For email, one raw_page == the whole message (small enough to never split). |
| 5 | Small-page joining | If a page's token count is `< budget // 4` (default: 625 tokens), and the next page exists, **join** it with subsequent pages until the budget is reached or the file ends. | Tiny standalone chunks (~1 page of cover-letter or signature) hurt retrieval recall. Joining recovers context. Boundary doesn't cross files. |
| 6 | Cross-page chunks track all source pages | `chunks.source_page_numbers int[]` records EVERY raw_page that contributed at least one byte (e.g., a chunk spanning pages 5-6-7 stores `{5,6,7}`). | Phase 8 citation rendering needs this for "this answer came from pages 5–7." Storing the array now avoids a Phase 8 migration. |
| 7 | Chunks table immutability | `REVOKE UPDATE, DELETE ON chunks FROM kb_app;` — same pattern as `raw_pages` (Phase 2a decision #5) and `schema_versions` (Phase 1b decision #10). Re-chunking deletes-via-superuser + re-inserts via a future admin path. | Chunks are an immutable derived artifact; downstream embeddings reference them by id. In-place mutation would silently invalidate Phase 3b's contextual prefix + Phase 3c's embeddings. |
| 8 | Lifecycle state addition | `files.lifecycle_state` CHECK widens to `('queued','parsing','parsed','chunked','failed','deleted')`. Phase 3b will add `contextualized`; Phase 3c will add `ready`. | Each sub-phase appends ONE new state. Easier to reason about than retrofitting all four at once. |
| 9 | Task chaining mechanism | `parse_file_impl()` calls `await procrastinate_app.configure_task(name="chunk_file").defer_async(file_id=file_id)` after the `parsed` lifecycle event is written. Done in a SEPARATE PG transaction (not the parse's tx) so a Procrastinate defer failure doesn't roll back the parse. | Procrastinate's defer is itself an INSERT into its task table; nesting it inside our parse tx couples two concerns. Worst case (defer fails): file stays at `parsed`; an out-of-band `chunk_file` invocation by an admin path recovers it. |
| 10 | Idempotency | `chunk_file_impl(file_id)` returns immediately when `files.lifecycle_state` is already `chunked` (or downstream: `contextualized` / `ready`). Idempotency key on UNIQUE `(file_id, chunk_index)` prevents duplicate rows on replay. | Matches Phase 2a's per-stage idempotency pattern. `SELECT FOR UPDATE` on the files row inside the lifecycle tx serializes concurrent invocations. |
| 11 | Empty-file handling | If `SELECT count(*) FROM raw_pages WHERE file_id = %s` returns 0, raise `ChunkingError("empty raw_pages for file=…")` → write `parsed→failed` lifecycle event. Don't emit zero chunks (would silently break downstream). | Shouldn't happen post-2a/2b but defensive. Failing loud beats failing silent. |
| 12 | xlsx row-boundary respect within a sheet | When a single xlsx sheet exceeds the chunk budget, the split point is the last `\n` before the budget — preserving row boundaries (Phase 2b decision #3 made sheet text `\t`-separated cells, `\n`-separated rows). | Mid-row splits break the columnar grid that Phase 5 atomic-unit extraction relies on. Row boundaries are a free, natural breakpoint. |

#### Repo layout delta after Phase 3a G4

```
emerging-kb/
├── migrations/sql/
│   └── 0009_chunks.sql                       ← NEW (table + RLS + REVOKE UPDATE/DELETE)
├── src/kb/
│   ├── chunking/
│   │   └── __init__.py                       ← NEW (`Chunker` Protocol + `chunk_pages()` function + tokenizer cache)
│   ├── domain/
│   │   └── chunks.py                         ← NEW (pydantic `Chunk` + `insert_chunk()` + `list_chunks_for_file()`)
│   └── workers/
│       └── tasks.py                          ← MUTATED (`chunk_file_impl` + Procrastinate `chunk_file` task + defer at end of parse)
└── tests/
    ├── test_chunking_unit.py                 ← NEW (~10 pure-fn tests on `chunk_pages` — budget/overlap/joining/splitting)
    ├── test_chunking_worker.py               ← NEW (~6 worker-tests: end-to-end parsed→chunked, idempotency, failure mode, task chaining)
    └── specs/phase_3a.md                     ← NEW
```

No `kb/api/` mutations. Phase 3a is pure-internal — no new endpoints, no contract deltas in `api_contracts.md` (Phase 3a docs append a single sentence to §5.2 noting the new `'chunked'` lifecycle state value on the wire).

#### Endpoint contract delta (api_contracts.md §5.2)

The §5.2 file-resource description lists the `lifecycle_state` enum on the wire. Phase 3a's only contract change: enum widens from `queued | parsing | parsed | failed | deleted` to `queued | parsing | parsed | chunked | failed | deleted`. Phase 3b adds `contextualized`; 3c adds `ready`. No new endpoints, no new error slugs.

#### Phase 3a G5 — what "green" means

`scripts/verify_phase_3a.sh` adds to Phase 0+1a+1b+1c+2a+2b verify checks:
1. `psql` confirms `0009_chunks.sql` applied: `chunks` table exists with workspace_id + RLS forced + UPDATE/DELETE revoked from kb_app + UNIQUE `(file_id, chunk_index)` constraint present.
2. `psql` confirms `files.lifecycle_state` CHECK includes `chunked`.
3. Compose smoke: `curl POST /files (tiny.pdf, multipart)` → 201 → worker parses (Docling) → worker chunks → `files.lifecycle_state = 'chunked'` within 4 min.
4. `psql` confirms ≥1 chunk row exists for the file with `source_page_numbers` populated and `token_count > 0`.
5. `curl POST /files (tiny.xlsx, multipart)` → parse → chunk; `psql` confirms chunks for each non-empty sheet.
6. `curl POST /files (tiny.eml, multipart)` → parse → chunk; `psql` confirms ≥1 chunk row.
7. Re-deferring `chunk_file(file_id)` on an already-`chunked` file → no duplicate chunk rows.
8. `pytest tests/` green: 188 (existing) + ~16 new = ~204.

#### Pre-G2 consistency review checklist

Before G2 opens:
- [ ] Architecture §5 step 6 traceability — Phase 3a covers the layout-aware token-bounded chunker only; "late chunking" terminology kept in docs (architecture's phrasing) with a code comment noting the practical implementation.
- [ ] No leak into Phase 3b territory (no `contextual_chunks` table, no LLM call, no `KB_ANTHROPIC_API_KEY` reference).
- [ ] No leak into Phase 3c territory (no `chunk_embeddings`, no `raptor_nodes`, no embedding API call).
- [ ] No `audit_log` writes (Phase 9).
- [ ] Phase 2a/2b's E2E pipeline still serves every supported mime correctly after the chained defer is added (cross-phase sweep at G5).
- [ ] RLS invariant grows from 11 → 12 workspace-scoped tables (chunks joins the list).

#### Sign-off

When Aniket approves this plan, the Phase 3a G1 cell in §5 flips 🟡 → ✅ and Phase 3a G2 opens (single contract delta in `docs/api_contracts.md` §5.2 lifecycle enum). Sign-off recorded in §9.

---

### 5.8 Phase 3b plan — Contextual Retrieval (G1 ✅ + G2 ✅ + G3 ✅ + G4 ✅ + G5 ✅ SIGNED OFF)

> **Status:** All 5 gates green 2026-05-23. Plan + contract delta + 15 red skeletons + working implementation + verify_phase_3b.sh 15/15 + cross-phase sweep (139/139 cumulative across Phase 0/1a/1b/1c/2a/2b/3a/3b). Branch: `phase-3/chunking-raptor` (second commit-set on the same branch as 3a). **Ready to merge** as a single PR carrying both Phase 3a and Phase 3b — split via commit-set per the §9 split decision.

#### Scope

Phase 3b takes a `chunked` file and produces a **`contextual_chunks`** row per `chunks` row: each contextual chunk carries a 50–100 token LLM-generated "this is from X about Y" prefix prepended to the chunk text. The prefix is what BM25 + dense embedding will actually index in Phase 3c + Phase 4 — the rationale (per Anthropic's [eval](https://www.anthropic.com/news/contextual-retrieval)) is that a chunk "Q3 revenue grew 12%" is hard to retrieve without knowing it's from ACME Corp's 2024 10-K. The prefix supplies that missing context. **This is the first LLM call in the pipeline.**

**In scope:**
- **`0010_contextual_chunks.sql` migration** — `contextual_chunks` table (workspace-scoped, RLS day-1, immutable: REVOKE UPDATE/DELETE on kb_app). Columns: `id uuid PK`, `chunk_id uuid FK to chunks ON DELETE CASCADE`, `file_id uuid FK`, `workspace_id uuid`, `contextual_prefix text` (the LLM-generated header), `contextual_text text` (`= prefix + "\n\n" + chunks.text`, denormalized for index efficiency), `model_id text` (e.g., `'claude-opus-4-7'` — records which LLM produced the prefix), `prefix_token_count int`, `cache_creation_input_tokens int`, `cache_read_input_tokens int` (Anthropic-reported cache metrics for cost auditing), `created_at timestamptz`. UNIQUE `(chunk_id)`. Indexes: `(workspace_id)`, `(file_id)`.
- **Lifecycle state extension** — `files.lifecycle_state` CHECK widens to include `contextualized`. Transition `chunked → contextualized` (success) or `chunked → failed` (error).
- **`kb/contextualization/__init__.py`** — `Contextualizer` Protocol with one method: `async contextualize(*, doc_text: str, chunk_text: str) -> ContextualizedChunk`. Real impl `AnthropicContextualizer(api_key=..., client=None, concurrency=8)` uses `anthropic.AsyncAnthropic`. **All-or-nothing self-disable**: `KB_ANTHROPIC_API_KEY` unset → `IdentityContextualizer` swaps in (returns `contextual_prefix=""` so `contextual_text == chunk_text`); downstream pipeline keeps moving, retrieval recall degrades to "no contextual retrieval" baseline. This means Phase 3c + Phase 4 + Phase 8 work without an API key, just less accurately.
- **Prompt-caching strategy** (per `claude-api` skill + architecture §5 step 7):
  - **System block carries the full doc context** + `cache_control: {type: "ephemeral"}`. Render order is `tools → system → messages`; the doc text sits early in the prefix where caching matters.
  - **User message carries the chunk** + "Provide a 50–100 token contextual prefix" instruction.
  - `max_tokens=200` (prefix target is 50–100 tokens; budget = ~2× that for safety margin).
  - No thinking (`thinking: {type: "disabled"}` — short description task; thinking would burn tokens for no benefit). Note: Opus 4.7 default IS thinking-disabled, but explicit is clearer.
  - Cache-hit verification at ingest time via `response.usage.cache_read_input_tokens` — recorded into `contextual_chunks.cache_read_input_tokens`. Phase 3b G5 verify asserts cache hits > 0 across multi-chunk docs.
- **Concurrency cap** — `asyncio.Semaphore(8)` per doc (one task per chunk, max 8 in flight). Phase 3b worker reads `chunks` rows for a file, batches them, awaits all completions, then writes.
- **Worker stage `contextualize_file_impl(file_id)`** in `kb/workers/tasks.py` — reads file row, reads all `raw_pages` (for doc context), reads all `chunks` (input), runs the contextualizer batch, INSERTs `contextual_chunks` rows, transitions lifecycle to `contextualized` with event `contextualization_done` carrying `{prefix_count, total_cache_read_tokens, total_cache_creation_tokens, model_id}`. Idempotent: returns immediately if already `contextualized`.
- **Task chaining** — `chunk_file_impl()`'s success path defers `contextualize_file(file_id)` in a separate tx (same pattern as Phase 3a → 3a's parse-to-chunk defer).
- **Failure mode** — Anthropic API failures (4xx, 5xx, network) → `chunked → failed` with `event='contextualization_failed'`, payload includes `{error_class, message, anthropic_request_id}` if available.

**Out of scope (deferred):**
- Embedding the contextual chunks → **Phase 3c**.
- RAPTOR tree build → **Phase 3c**.
- HNSW + BM25 indexes on `contextual_chunks.contextual_text` → **Phase 4**.
- Re-running contextualization when the doc context changes (e.g., user updates the file metadata) → not relevant; raw_pages are immutable.
- Configurable prefix prompt → Phase 8 / config layering (Hydra/OmegaConf lands at Phase 5 per Phase 0 §3).
- Other LLM providers (Gemini Flash, GPT) → adapter pattern is in place via the `Contextualizer` Protocol; another impl can land later as additive.
- `audit_log` writes on contextualization → Phase 9.

#### Decisions (locked at G1; changes require re-opening G1)

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | LLM model | **`claude-opus-4-7`** (default; configurable via `KB_CONTEXTUAL_MODEL` env). | Per the `claude-api` skill: "ALWAYS use `claude-opus-4-7` unless the user explicitly names a different model — never downgrade for cost; that's the user's decision." Skill is authoritative on model choice. Architecture §8's "Gemini 2.5 Flash" was a placeholder for the *extraction LLM*; contextual prefix is a separate stage and Anthropic's own Contextual Retrieval recipe is canonical here. **User can override to `claude-haiku-4-5` via `KB_CONTEXTUAL_MODEL` for ~5× cost savings if quality holds.** |
| 2 | Prompt-cache placement | Single `cache_control: {type: "ephemeral"}` breakpoint on the system block containing the full doc context. | Standard prompt-caching pattern per the skill. Doc context renders first in the prefix; per-chunk completion calls reuse the cached prefix (saves ~90% on doc context tokens after the first call). [Anthropic blog](https://www.anthropic.com/news/contextual-retrieval) reports ~$1/M src tokens with caching vs ~$5/M uncached. |
| 3 | Minimum cacheable prefix | Anthropic Opus 4.7's prompt cache requires ≥ 4096 tokens of prefix to actually cache (per the `claude-api` skill ref). For docs shorter than ~4K tokens, the cache silently doesn't kick in (no error; `cache_creation_input_tokens=0`). We record this in `cache_creation_input_tokens` for cost auditing. | Skill-documented behavior. For tiny docs the per-call cost is already trivial. |
| 4 | Per-chunk concurrency | `asyncio.Semaphore(8)` — at most 8 in-flight Anthropic calls per doc. Configurable via `KB_CONTEXTUAL_CONCURRENCY`. | Balances throughput vs Anthropic rate-limit headroom (defaults are 50 RPM and 40K ITPM at tier 1; 8-way concurrency stays well under). Higher concurrency risks 429s on bursty docs; lower wastes wall-clock time. |
| 5 | Adapter pattern | `Contextualizer` Protocol with `AnthropicContextualizer` (real) + `IdentityContextualizer` (env-key-unset fallback) + tests inject `MockContextualizer`. | Same pattern as Phase 2b's Mistral OCR (decision #7-9) — externalize the API surface so CI is hermetic + real activation flips on with an env var. |
| 6 | Self-disable behavior | When `KB_ANTHROPIC_API_KEY` is unset, worker logs a structured warning AND swaps in `IdentityContextualizer` (returns `prefix=""` → `contextual_text == chunk_text`). File still advances to `contextualized`. | Pipeline-completes-without-key beats pipeline-blocks-without-key — Phase 3c + Phase 4 + Phase 8 retrieval still work (just at "no contextual retrieval" recall baseline). Production deploys MUST set the key; alarm/dashboard on `model_id == 'identity'` count. |
| 7 | Prefix prompt template | Fixed system prompt: `"Here is the full document for context (cached for efficiency):\n\n<document>\n{doc_text}\n</document>"`. User prompt: `"Here is a chunk from that document:\n\n<chunk>\n{chunk_text}\n</chunk>\n\nProvide a short (50-100 token) context line that situates this chunk within the document. Return ONLY the context line, no preamble."`. | Verbatim from Anthropic's [Contextual Retrieval cookbook](https://github.com/anthropics/anthropic-cookbook/tree/main/skills/contextual-embeddings). Proven recipe; deviation = re-running the eval. |
| 8 | Output token budget | `max_tokens=200`. Architecture §5 step 7 targets "50-100 tokens"; budget is 2× the upper bound for safety margin. | If Claude generates >100 token prefix, that's still OK (extra context never hurts retrieval). 200 is the hard cap — runaway prefixes get truncated, which the worker logs but doesn't fail on. |
| 9 | `thinking` mode | **Disabled** (`thinking: {type: "disabled"}`). | Contextual prefix is a short-description task. Per `claude-api` skill, Opus 4.7 default is thinking-off anyway, but explicit makes the cost story unambiguous. Adaptive thinking would burn tokens for no measurable recall benefit. |
| 10 | Contextual chunks table immutability | `REVOKE UPDATE, DELETE ON contextual_chunks FROM kb_app;` — same pattern as `chunks` (3a #7) and `raw_pages` (2a #5). | Downstream Phase 3c embeddings reference `contextual_chunks` by id. In-place mutation invalidates embeddings + RAPTOR clusters. Re-contextualize via superuser delete + re-run. |
| 11 | Cache metrics persisted | `cache_creation_input_tokens` + `cache_read_input_tokens` columns on every row. Phase 3b G5 verify asserts at least one row in a multi-chunk doc has `cache_read_input_tokens > 0`. | Post-hoc cost auditing. Hit rate = `sum(cache_read) / (sum(cache_read) + sum(cache_creation))`. Target: > 0.85 after the first chunk per doc. |
| 12 | Lifecycle state addition | `files.lifecycle_state` CHECK widens to `('queued','parsing','parsed','chunked','contextualized','failed','deleted')`. Phase 3c will add the terminal `ready`. | Each sub-phase appends exactly one new state per the forward-compat convention locked in Phase 3a G2. |
| 13 | Task chaining | `chunk_file_impl()` success path defers `contextualize_file(file_id)` in a SEPARATE PG transaction (so an Anthropic API + Procrastinate-defer interleaving doesn't roll back the chunked state). | Same shape as Phase 3a's parse → chunk defer (3a #9). |
| 14 | Failure mode | API errors (4xx, 5xx, network): worker writes `chunked→failed` with `event='contextualization_failed'`. Payload includes `error_class`, `message`, and `anthropic_request_id` if present in the exception. | Anthropic exceptions carry `_request_id` per the SDK — recording it lets us trace failed calls to Anthropic's audit log if support is needed. |

#### Repo layout delta after Phase 3b G4

```
emerging-kb/
├── migrations/sql/
│   └── 0010_contextual_chunks.sql        ← NEW (table + RLS + REVOKE UPDATE/DELETE + 'contextualized' CHECK widen)
├── src/kb/
│   ├── contextualization/
│   │   └── __init__.py                   ← NEW (`Contextualizer` Protocol + `AnthropicContextualizer` + `IdentityContextualizer`)
│   ├── domain/
│   │   └── contextual_chunks.py          ← NEW (pydantic + `insert_contextual_chunk` + `read_chunks_for_contextualization`)
│   └── workers/
│       └── tasks.py                      ← MUTATED (`contextualize_file_impl` + `contextualize_file` task + chained defer from chunk_file)
└── tests/
    ├── test_contextualization_unit.py    ← NEW (~9 unit tests: AnthropicContextualizer with mock client, IdentityContextualizer, concurrency cap, prompt shape, cache_control marker, response parsing, error handling)
    ├── test_contextualization_worker.py  ← NEW (~6 worker tests: end-to-end chunked→contextualized, idempotency, identity fallback, failure mode, task chaining, cache metrics persisted)
    └── specs/phase_3b.md                 ← NEW
```

No `kb/api/` mutations. Phase 3b is pure-internal — no new endpoints, single contract delta in `api_contracts.md` §5.2 lifecycle enum (adds `contextualized`).

#### Endpoint contract delta (api_contracts.md §5.1 #3 + §5.2)

Per the forward-compat convention locked in Phase 3a G2: `files.lifecycle_state` enum widens from `queued | parsing | parsed | chunked | failed | deleted` to add `contextualized`. §5.1 invariant #3 already documents the full chain through 3c (`ready` lands at 3c). Single-line delta in the §5.2 file-resource row's enum description.

#### Phase 3b G5 — what "green" means

`scripts/verify_phase_3b.sh` adds to Phase 0+1a+1b+1c+2a+2b+3a verify checks:
1. `psql` confirms `0010_contextual_chunks.sql` applied: table exists with workspace_id + RLS forced + UPDATE/DELETE revoked from kb_app + UNIQUE `(chunk_id)`.
2. `psql` confirms `files.lifecycle_state` CHECK includes `contextualized`.
3. Compose smoke (`KB_ANTHROPIC_API_KEY` unset path): `POST tiny.pdf` → file reaches `lifecycle_state='contextualized'` within 4 min using `IdentityContextualizer` (model_id column == `'identity'`); contextual_text equals chunk text byte-for-byte.
4. `psql` confirms ≥1 `contextual_chunks` row exists for the file; `model_id='identity'`.
5. If `KB_ANTHROPIC_API_KEY` is set in the test env (CI nightly run + local dev): compose smoke also runs the real-API path; verifies `cache_read_input_tokens > 0` on the second+ chunks of a multi-chunk doc + `prefix_token_count BETWEEN 30 AND 200`.
6. Re-deferring `contextualize_file(file_id)` on an already-`contextualized` file → no duplicate rows.
7. `pytest tests/` green: 204 (existing) + ~15 new = ~219.

#### Pre-G2 consistency review checklist

Before G2 opens:
- [ ] Architecture §5 step 7 + step 8 traceability — Phase 3b covers the prefix LLM call only; step 8 (embed contextualized chunks) and step 9 (BM25 index) belong to 3c/Phase 4 respectively.
- [ ] No leak into Phase 3c territory (no `chunk_embeddings` table, no embedding API call, no RAPTOR refs).
- [ ] No leak into Phase 4 territory (no HNSW/BM25 index creation).
- [ ] `audit_log` writes still deferred to Phase 9.
- [ ] RLS invariant grows from 12 → 13 workspace-scoped tables (`contextual_chunks` joins the list).
- [ ] Prompt-cache placement verified against `shared/prompt-caching.md` (system block, single breakpoint, deterministic doc context).
- [ ] Model choice traced to `claude-api` skill's "always default to claude-opus-4-7" mandate.

#### Sign-off

When Aniket approves this plan, the Phase 3b G1 cell in §5 flips 🟡 → ✅ and G2 opens (single contract delta in `api_contracts.md` §5.2 lifecycle enum). Sign-off recorded in §9.

---

### 5.8.1 Phase 3b-bis plan — Gemini Contextualizer adapter (G1 ✅ + G3 ✅ + G4 ✅ + G5 ✅ SIGNED OFF)

> **Status:** G1 opens 2026-05-23. Additive adapter for the `Contextualizer` Protocol locked at 5.8 — no schema delta, no lifecycle delta, no API contract delta. **Motivation:** interview-submission demo runs on a single API key. Shipping a second real implementation alongside `AnthropicContextualizer` turns "BYO Anthropic key or contextual retrieval no-ops" into "Gemini-only pipeline by default, Anthropic as an alternative adapter" — proves the §5.8 adapter pattern under load instead of just on paper. Same `phase-3/chunking-raptor` branch.

#### Scope

A new `GeminiContextualizer` class in `kb/contextualization/__init__.py` implementing the existing `Contextualizer` Protocol (`async contextualize(*, doc_text, chunk_text) -> ContextualizedChunk`). The factory `make_contextualizer()` widens from binary (Anthropic vs Identity) to **selector-driven** (Anthropic, Gemini, Identity, auto-detect).

**In scope:**
- **`GeminiContextualizer(api_key=..., client=None, model="gemini-2.5-flash", concurrency=8)`** — uses `google.genai.Client.aio.models.generate_content` (the same `google-genai` package already added at Phase 3c G4 for embeddings; no new dep). System instruction carries the full doc; user content carries the chunk + 50-100 token prefix instruction (verbatim from the Anthropic cookbook prompt, since the recipe is model-agnostic).
- **Factory selector `make_contextualizer()` widened** — reads `KB_CONTEXTUALIZER` env var with values `gemini` | `anthropic` | `identity` | `auto` (default `auto`). `auto` probes: `KB_GEMINI_API_KEY` set → Gemini; elif `KB_ANTHROPIC_API_KEY` set → Anthropic; else → Identity. Existing behavior preserved when `KB_CONTEXTUALIZER` is unset and only the Anthropic key is set.
- **Cache-metrics columns reused with documented semantics for Gemini.** `cache_creation_input_tokens` is repurposed to hold Gemini's `usage_metadata.prompt_token_count` (= billed input tokens, no caching used). `cache_read_input_tokens` stays 0 for the Gemini path. This keeps the schema additive (no migration) and makes cost reporting work for either provider — `total_input_tokens = sum(cache_creation) + sum(cache_read)` is the right aggregate for both.
- **`model_id`** column stores `'gemini-2.5-flash'` literal for Gemini-path rows (mirrors `'claude-opus-4-7'` / `'identity'` for the other two adapters).
- **Tests** — `tests/test_contextualization_gemini_unit.py` (~6 tests: GeminiContextualizer with mocked `google.genai.Client.aio.models.generate_content`, prompt shape assertion, response parsing, error handling, concurrency cap, factory selector matrix). Reuses the existing worker-level tests in `test_contextualization_worker.py` by parameterizing on `KB_CONTEXTUALIZER`.
- **`scripts/verify_phase_3b.sh` extension** — adds a Gemini-path E2E branch: if `KB_GEMINI_API_KEY` is set in the compose env, the verify also asserts `model_id='gemini-2.5-flash'` on the contextual_chunks row produced by `tiny.pdf`. The existing Identity-fallback path remains the default for `KB_GEMINI_API_KEY` unset.

**Out of scope (deferred):**
- **Gemini explicit context caching** — Gemini's `client.caches.create()` API requires ≥4K tokens of cached content and TTL management; valuable at scale, but adds API surface area + a code path for cache-miss/expire/refresh. For the interview demo (small doc count, single-digit pages), pass full doc context inline every call. Cost stays trivial. Revisit when corpus grows.
- **`GeminiOCRParser`** (replacement for Mistral OCR) — Phase 1c-bis territory. Decided separately based on whether the demo corpus includes scanned PDFs.
- **Reusing this adapter for Phase 3d RAPTOR cluster summarization** — that's 3d's plan; Phase 3b-bis just ensures the LLM client is wired through `google-genai` so 3d can reuse it.
- New migration (`contextual_chunks` schema is unchanged).
- New endpoint or contract delta.

#### Decisions (locked at G1; changes require re-opening G1)

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | LLM model | **`gemini-2.5-flash`** (default; configurable via `KB_CONTEXTUAL_MODEL` env, same var Anthropic adapter respects). | Matches architecture §8 (Gemini 2.5 Flash for short-context tasks). Flash is the cost-equivalent of Claude Haiku — small, fast, instruction-tuned, well-suited to the 50-100 token rewriting task. Pro reserved for RAPTOR L3 atomic-unit extraction (later phases) where reasoning depth matters. |
| 2 | Adapter selector | New env var `KB_CONTEXTUALIZER` with values `gemini` \| `anthropic` \| `identity` \| `auto`. Default `auto`: probe Gemini key → Anthropic key → Identity in that order. | Single var beats two booleans. `auto` keeps the demo zero-config when one key is set; explicit override available for tests + CI cross-cuts. Gemini-first probe ordering matches the demo's "one API key, Gemini" story. |
| 3 | Prompt template | **Verbatim from §5.8 decision #7** (Anthropic cookbook prompt). System: `"Here is the full document for context:\n\n<document>\n{doc_text}\n</document>"`. User: `"Here is a chunk from that document:\n\n<chunk>\n{chunk_text}\n</chunk>\n\nProvide a short (50-100 token) context line that situates this chunk within the document. Return ONLY the context line, no preamble."`. | Recipe is model-agnostic; deviating means re-running Anthropic's published eval against Gemini, which is out of scope. Keeps fair head-to-head if/when we benchmark both. |
| 4 | Caching strategy | **No explicit caching for Gemini path** in 3b-bis. Pass full doc inline on every call. `cache_creation_input_tokens` column stores Gemini's `prompt_token_count` (billed-input tokens; equivalent semantics: "tokens we paid to process this call"). `cache_read_input_tokens` stays `0`. | Gemini explicit cache (`client.caches.create()`) requires ≥4K tokens of doc + TTL management. Adds surface area for a demo where total inference cost is < $0.01/doc anyway. Document the difference; revisit at scale. |
| 5 | Per-chunk concurrency | `asyncio.Semaphore(8)` — same as Anthropic adapter. Configurable via `KB_CONTEXTUAL_CONCURRENCY` (shared env var). | Same throughput-vs-rate-limit reasoning. Gemini Flash free tier is 15 RPM / 1M TPM — 8-way concurrency stays under at our doc sizes. |
| 6 | Output token budget | `max_output_tokens=200` via `generation_config={"max_output_tokens": 200}`. Same upper bound as Anthropic adapter. | Identical task, identical budget. |
| 7 | Thinking / reasoning mode | **Disabled.** Gemini 2.5 Flash supports `thinking_config={"thinking_budget": 0}` (per google-genai SDK). Set explicitly. | Contextual prefix is a short-description task. Same reasoning as Anthropic decision #9. Burning thinking tokens for a 50-token output is wasteful. |
| 8 | Failure mode | Gemini API errors (4xx, 5xx, network) → `chunked → failed` with `event='contextualization_failed'`. Payload includes `error_class`, `message`, and the response's `prompt_feedback.block_reason` if present (safety blocks). | Mirrors Anthropic decision #14, adapted to Gemini's error surface. `prompt_feedback` is Gemini-specific; capture it for debugging RAI/safety blocks. |
| 9 | model_id literal | `'gemini-2.5-flash'` stored verbatim in `contextual_chunks.model_id`. Future model upgrades store new literal (e.g., `'gemini-3.0-flash'`). | Same audit pattern as Anthropic (`'claude-opus-4-7'`) and Identity (`'identity'`). Dashboards filter by `model_id` for cost + provider attribution. |
| 10 | Test parameterization | Worker-level tests (`test_contextualization_worker.py`) parameterize over `KB_CONTEXTUALIZER ∈ {anthropic, gemini, identity}` using `pytest.mark.parametrize` + mocked clients. Avoids duplicating 6 worker tests three times. | Single source of truth for "the worker glue works regardless of adapter." Unit tests (`test_contextualization_gemini_unit.py`) cover adapter-specific behavior. |

#### Repo layout delta after Phase 3b-bis G4

```
emerging-kb/
├── src/kb/contextualization/
│   └── __init__.py                          ← MUTATED (add GeminiContextualizer + widen make_contextualizer factory)
├── tests/
│   └── test_contextualization_gemini_unit.py  ← NEW (~6 unit tests)
│       test_contextualization_worker.py     ← MUTATED (parameterize over adapter)
└── scripts/
    └── verify_phase_3b.sh                   ← MUTATED (Gemini-path E2E branch)
```

No new SQL migration. No new domain module. No new worker task — the existing `contextualize_file_impl` is adapter-agnostic; it just calls `make_contextualizer()`.

#### Endpoint contract delta

**None.** Phase 3b-bis is purely internal — same `Contextualizer` Protocol, same `contextual_chunks` schema, same lifecycle states, same task surface.

#### Phase 3b-bis G5 — what "green" means

`scripts/verify_phase_3b.sh` (extended) adds:
1. New step: `psql` confirms `KB_GEMINI_API_KEY` is propagated to the worker container (`docker compose exec worker env | grep KB_GEMINI_API_KEY`).
2. New step: `POST tiny.pdf` → file reaches `lifecycle_state='contextualized'`, and `contextual_chunks.model_id='gemini-2.5-flash'` for at least one row (proves the auto-selector picked Gemini).
3. New step: `psql` confirms `cache_creation_input_tokens > 0` (Gemini billed-input tokens recorded) and `cache_read_input_tokens = 0` for Gemini rows (documents the no-cache semantics).
4. Existing Identity-path branch preserved (runs when `KB_GEMINI_API_KEY` is unset).
5. `pytest tests/` green: 232 (existing) + ~6 new = ~238.
6. Cross-phase sweep `verify_phase_{0..3c}.sh` all green (verifies no regression on prior phases).

#### Pre-G3 consistency review checklist

Before G3 opens:
- [ ] §5.8 decision #1 (Anthropic = default) updated to read "configurable via `KB_CONTEXTUALIZER`; default `auto`." Old behavior is one branch of the selector.
- [ ] §5.8 decision #5 (adapter pattern) updated to list three real adapters: Anthropic, Gemini, Identity.
- [ ] §5.8 decision #11 (cache metrics) updated to document the Gemini-path semantics for `cache_creation_input_tokens` (= prompt_token_count) and `cache_read_input_tokens` (= 0).
- [ ] Architecture §8 stack-table entry for "LLMs" already covers Gemini 2.5 Flash — no edit needed.
- [ ] `.env.example` updated alongside G4 to mention `KB_CONTEXTUALIZER` + `KB_GEMINI_API_KEY` (this is the consistency-sweep gap from the May-23 review).
- [ ] No leak into Phase 3d territory (RAPTOR clustering / summarization — separate phase, will reuse google-genai client).
- [ ] No leak into Phase 1c-bis (Gemini OCR adapter — separate phase, decided based on demo corpus).

#### Sign-off

When Aniket approves this plan, §5 gains a new row for Phase 3b-bis, G1 flips 🟡 → ✅, and G3 opens (skip G2 — no API contract delta, no migration). Estimated wall-clock: ~1 hour for G3 + G4 + G5 combined given the adapter pattern is already paved.

---

### 5.9 Phase 3c plan — Embedding (G1 ✅ + G2 ✅ + G3 ✅ + G4 ✅ + G5 ✅ SIGNED OFF)

> **Status:** G1–G4 all green 2026-05-23. Plan + contract delta + 13 red skeletons + working implementation locked. 13/13 new tests green; full suite 232/232 in 70s. Branch: `phase-3/chunking-raptor` (third commit-set).

#### Scope

Phase 3c calls the Gemini Embedding API on every `contextual_chunks.contextual_text` and stores the result in a new **`chunk_embeddings`** table. Embeddings are the search-time signal Phase 4 wraps in HNSW + BM25 indexes, and the input Phase 3d's RAPTOR clustering consumes.

**In scope:**
- **`0011_chunk_embeddings.sql` migration** — `chunk_embeddings` table (workspace-scoped, RLS day-1, immutable: REVOKE UPDATE/DELETE on kb_app). Columns: `id uuid PK`, `contextual_chunk_id uuid FK to contextual_chunks ON DELETE CASCADE`, `file_id uuid FK`, `workspace_id uuid`, `embedding halfvec(3072)`, `model_id text` (e.g., `'gemini-embedding-001'` or `'mock-deterministic-v1'`), `created_at timestamptz`. UNIQUE `(contextual_chunk_id, model_id)`. Indexes: `(workspace_id)`, `(file_id)`. HNSW index lands in Phase 4.
- **`kb/embeddings/__init__.py`** — `Embedder` Protocol with `async embed_batch(texts) -> list[EmbeddingResult]`. Real impl `GeminiEmbedder` uses `google-genai` SDK. CI fallback `DeterministicMockEmbedder` produces deterministic [-1, 1] L2-normalized vectors via sha256(text + dim_index).
- **Factory** `make_embedder()` reads `KB_GEMINI_API_KEY` — set → `GeminiEmbedder`, unset → `DeterministicMockEmbedder`.
- **Lifecycle state extension** — `embedded` (already permitted by 0009's forward-compat CHECK; locked at Phase 3b G4 fix #2). 0011 just adds the table.
- **Worker stage `embed_file_impl(file_id)`** — reads contextual_chunks, batch-embeds, INSERTs chunk_embeddings, transitions to `embedded` with event `embedding_done` carrying `{embedding_count, dim, model_id}`. Idempotent.
- **Task chaining** — `contextualize_file_impl()` defers `embed_file(file_id)` in separate tx.
- **Failure mode** — API errors → `contextualized → failed` with `event='embedding_failed'`.

**Out of scope (deferred):**
- RAPTOR tree build → **Phase 3d**.
- HNSW index on `chunk_embeddings.embedding` → **Phase 4**.
- BM25 index on `contextual_chunks.contextual_text` → **Phase 4**.
- Cross-doc / corpus-level embedding clustering → Phase 5 / Phase 7.
- Embedding model A/B testing → Wave B via Hydra/OmegaConf (Phase 5).
- `audit_log` writes → Phase 9.

#### Decisions (locked at G1; changes require re-opening G1)

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | Embedding model | **`gemini-embedding-001`** (default; `KB_EMBEDDING_MODEL` env override). Architecture §8: "Gemini Embedding 001 (#1 commercial MTEB, 68.32)". | Authoritative per architecture. Override lets the user point at newer model versions without code change. |
| 2 | Dimensions | **3072** (Gemini Embedding 001's default). Stored as **`halfvec(3072)`** for ~50% storage savings at negligible accuracy loss. | `halfvec` is pgvector's float16; Phase 4's HNSW supports it natively. |
| 3 | Adapter pattern | `Embedder` Protocol with `GeminiEmbedder` (real) + `DeterministicMockEmbedder` (CI fallback) + `make_embedder()` factory keyed on `KB_GEMINI_API_KEY`. | Same shape as 3b's `Contextualizer` (3b #5). Hermetic CI + production activation via env var. |
| 4 | Self-disable behavior | `KB_GEMINI_API_KEY` unset → `DeterministicMockEmbedder` swaps in; produces stable vectors derived from `sha256(text || ":" || dim_index)`, L2-normalized to unit length. `model_id='mock-deterministic-v1'`. File still advances to `embedded`. | Pipeline-completes-without-key beats blocking. Production deploys MUST set the key (alarm on `model_id == 'mock-deterministic-v1'`). |
| 5 | Mock embedder math | `mock_vector[i] = ((sha256(text || ":" || str(i)).digest()[0] / 255.0) * 2 - 1)` per dim, then L2-normalized. Reproducible across processes + Python versions. | Determinism lets Phase 3d's RAPTOR clustering tests assert on cluster shape. Normalized vectors match Gemini Embedding 001's output norm. |
| 6 | Batching | `GeminiEmbedder.embed_batch()` uses the SDK's native batch API (max 100 texts per call). Mock returns one vector per input regardless. | Wave A's corpus is tiny; the batch path exercises once the corpus grows. |
| 7 | Concurrency | Single batch call per file. | Files have ≤ ~50 chunks in the Wave A target. One call per file is fine. |
| 8 | Table immutability | `REVOKE UPDATE, DELETE ON chunk_embeddings FROM kb_app;` — same pattern as `contextual_chunks` (3b #10) + `chunks` (3a #7). | Re-embedding implies a new model — write a new row with a different `model_id`. UNIQUE `(contextual_chunk_id, model_id)` makes this safe. |
| 9 | UNIQUE composite key | `(contextual_chunk_id, model_id)`. | A future model upgrade backfills new rows without deleting old ones; Phase 4's HNSW index filters by `model_id` to pick the active vectors. |
| 10 | Lifecycle state addition | `embedded` (already in 0009's CHECK via forward-compat). Phase 3d adds the terminal `ready`. | Forward-compat convention. |
| 11 | Task chaining | `contextualize_file_impl()` defers `embed_file(file_id)` in a SEPARATE tx. | Same pattern as 3a → 3b chaining. |
| 12 | Idempotency | Returns immediately when `lifecycle_state in ('embedded', 'ready', 'failed', 'deleted')`. UNIQUE + `ON CONFLICT DO NOTHING` prevents duplicate rows on replay. | Matches all prior worker stages. |
| 13 | Failure mode | Embedding API errors → `contextualized → failed` with `event='embedding_failed'`. Payload includes `error_class`, `message`. | Consistent with 3b's error envelope. |

#### Repo layout delta after Phase 3c G4

```
emerging-kb/
├── migrations/sql/
│   └── 0011_chunk_embeddings.sql           ← NEW
├── src/kb/
│   ├── embeddings/
│   │   └── __init__.py                     ← NEW
│   ├── domain/
│   │   └── chunk_embeddings.py             ← NEW
│   └── workers/
│       └── tasks.py                        ← MUTATED (embed_file_impl + embed_file task + chained-defer)
└── tests/
    ├── test_embeddings_unit.py             ← NEW (~7 unit)
    ├── test_embeddings_worker.py           ← NEW (~6 worker)
    └── specs/phase_3c.md                   ← NEW
```

New deps: `google-genai>=0.3.0`. `numpy` already pulled by torch.

#### Endpoint contract delta (api_contracts.md §5.1 #3 + §5.2)

`files.lifecycle_state` enum widens to add `embedded`. Single-line delta in §5.2. No new endpoints, no new error slugs.

#### Phase 3c G5 — what "green" means

`scripts/verify_phase_3c.sh`:
1. `psql` confirms 0011 applied: table exists + workspace_id + RLS forced + UPDATE/DELETE revoked + UNIQUE constraint.
2. `psql` confirms `halfvec` type column on `embedding`.
3. Compose smoke: POST tiny.pdf → file reaches `lifecycle_state='embedded'` via DeterministicMockEmbedder (`model_id='mock-deterministic-v1'`).
4. `psql` confirms ≥1 `chunk_embeddings` row per contextual_chunk; dim 3072.
5. Re-deferring is no-op.
6. `pytest tests/` green: 219 + ~13 new = ~232.

#### Pre-G2 consistency review checklist

- [ ] Architecture §5 step 8 traceability — Phase 3c covers the embed call only; HNSW index is Phase 4.
- [ ] No leak into 3d territory (no raptor_nodes, no clustering).
- [ ] No leak into Phase 4 (no HNSW or BM25).
- [ ] RLS invariant grows from 13 → 14 workspace-scoped tables.
- [ ] Mock embedder determinism asserted in tests.

#### Sign-off

When Aniket approves this plan, the Phase 3c G1 cell flips 🟡 → ✅ and G2 opens.

---

### 5.10 Phase 3d plan — RAPTOR tree build, per-doc (G1 ✅ + G2 ✅ + G3 ✅ + G4 ✅ + G5 ✅ SIGNED OFF)

> **Status:** G1 signed off 2026-05-24 after open-source-scale deliberation. Per-doc RAPTOR ships in 3d; corpus-level RAPTOR splits into Phase 3e (see §5.10.1). Same `phase-3/chunking-raptor` branch (6th commit-set after 3a/3b/3c/3b-bis/2c). After 3d+3e ship, `files.lifecycle_state='ready'` is reachable and Phase 4 (HNSW + BM25 indexes + tree-aware retrieval) can plug in. Schema is forward-compat for Phase 3e at the G4 migration boundary (no later ALTER TABLE needed at corpus scale).
>
> **Why split 3d/3e:** open-source ship targets 100K-doc-scale operation. Per-doc tree (3d) is the structural prerequisite for corpus tree (3e), but the algorithms differ at corpus scale (AgglomerativeClustering O(N²) → infeasible at N=100K; corpus uses UMAP+GMM per the paper). Splitting lets per-doc ship first + isolates the algorithm switch in 3e's own G-gate cycle.

#### Scope

Per-doc RAPTOR tree (Sarthi et al. 2024, [arXiv:2401.18059](https://arxiv.org/abs/2401.18059)) over Phase 3b's `contextual_chunks` + Phase 3c's `chunk_embeddings`. Algorithm: leaves are contextual chunks; at each level L, cluster the L-level embeddings, summarize each cluster via a `Summarizer` adapter, embed the summary, write a new (L+1)-level node + parent→child edges. Terminate when `n_clusters == 1` OR `level > max_levels` (default 6 — bumped from 4 at the post-deliberation flip; see decision #3 below) OR `n_at_level ≤ branching_factor`. Corpus-level RAPTOR ships at Phase 3e on the same `raptor_nodes` table via the forward-compat `scope` column.

**In scope:**
- **`0012_raptor.sql` migration** — two new tables (both workspace-scoped + RLS day-1 + immutable: REVOKE UPDATE, DELETE on kb_app) + a lifecycle CHECK widen:
  - `raptor_nodes` columns: `id uuid PK`, **`scope text NOT NULL DEFAULT 'per_doc' CHECK (scope IN ('per_doc','corpus'))`** (forward-compat for Phase 3e), **`file_id uuid NULL FK files ON DELETE CASCADE`** (NULL for `scope='corpus'` rows in 3e; NOT NULL for `scope='per_doc'` rows enforced by row-level CHECK), `workspace_id uuid NOT NULL`, **`level int NOT NULL CHECK (level BETWEEN 2 AND 6)`** (L1 leaves are NOT stored here — they live in `contextual_chunks`; level 2-6 covers per-doc trees and corpus trees up to `log₈(100K)≈5.5`), `text text NOT NULL`, `embedding halfvec(3072) NOT NULL`, `token_count int`, `cluster_id_in_level int NOT NULL`, `summarizer_model_id text NOT NULL`, `embedder_model_id text NOT NULL`, `created_at timestamptz`. Row CHECK: `(scope='per_doc' AND file_id IS NOT NULL) OR (scope='corpus' AND file_id IS NULL)`. UNIQUE `(scope, file_id, level, cluster_id_in_level)` (covers both per-doc and corpus row-key shapes). Indexes: `(workspace_id, scope, file_id, level)`, `(workspace_id, scope, level)` (for corpus-tree traversal).
  - `raptor_edges` columns: `parent_node_id uuid NOT NULL FK raptor_nodes ON DELETE CASCADE`, **`child_node_id uuid NULL FK raptor_nodes ON DELETE CASCADE`** (for L2-pointing-to-L2+ + corpus tree internal edges), **`child_contextual_chunk_id uuid NULL FK contextual_chunks ON DELETE CASCADE`** (for L2 leaves at per-doc scope — L2 nodes' children are contextual_chunks, NOT raptor_nodes — this avoids the 30 GB-at-100K denormalization that L1-in-raptor_nodes would create), `workspace_id uuid NOT NULL`, `created_at timestamptz`. Row CHECK: `(child_node_id IS NOT NULL) <> (child_contextual_chunk_id IS NOT NULL)` (exactly one non-null). Indexes: `(workspace_id, parent_node_id)`, `(workspace_id, child_node_id)`, `(workspace_id, child_contextual_chunk_id)`.
  - **Lifecycle CHECK widening** — `'ready'` is already in the 0009 CHECK list. 3d adds `'raptor_building'` (intermediate state between `embedded` and `ready` — see decision #12 below) to the CHECK list AND extends 0009's forward-compat convention to keep `'ready'`, `'failed'`, `'deleted'` present.
- **`kb/raptor/__init__.py`** — clustering + tree-build orchestrator:
  - `cluster_embeddings(vectors: list[list[float]], branching_factor=8) -> list[int]` — returns per-vector cluster assignment. Per-doc uses `sklearn.cluster.AgglomerativeClustering(metric='cosine', linkage='average', n_clusters=ceil(N/branching_factor))`. Deterministic given input. (Phase 3e adds a sibling `cluster_embeddings_corpus(vectors, ...)` that switches to UMAP+GMM for the N=100K case where AC is infeasible.)
  - `build_tree_for_file(file_id)` — orchestrates: read leaves from `contextual_chunks` + `chunk_embeddings` directly (no L1 raptor_nodes — they remain in contextual_chunks per decision #9). For L=2..MAX_LEVELS: cluster previous level's embeddings (leaves at L=2, raptor_nodes at L≥3), summarize each cluster via `Summarizer`, embed summaries via `Embedder`, INSERT raptor_nodes rows (scope='per_doc', file_id=this_file_id, level=L) + raptor_edges (L2 edges point at `child_contextual_chunk_id`; L3+ edges point at `child_node_id`). Terminate when previous level has ≤ `branching_factor` nodes (one more iteration would collapse to N=1 with no information gain) OR L > MAX_LEVELS.
- **`kb/summarization/__init__.py`** — `Summarizer` Protocol with `async summarize(*, texts: list[str], doc_context: str | None = None) -> Summary`. Three impls (same pattern as Contextualizer at §5.8 + 3b-bis):
  - `GeminiSummarizer` — Gemini 2.5 Flash. Prompt: *"You are summarizing N chunks from a single document. Produce a concise summary (200-400 tokens) that preserves key facts and themes. Use markdown. Return only the summary."* `max_output_tokens=600`, `thinking_budget=0`.
  - `AnthropicSummarizer` — Claude Haiku alternative. Same prompt, same budgets.
  - `IdentitySummarizer` — concatenates input texts with `\n\n---\n\n` separator, truncates to ~600 tokens. CI fallback; `model_id='identity'` in audit.
  - Factory `make_summarizer()` reads `KB_SUMMARIZER ∈ {gemini, anthropic, identity, auto}`. `auto` (default) probes `KB_GEMINI_API_KEY` → `KB_ANTHROPIC_API_KEY` → Identity (Gemini-first matches the demo's single-key story).
- **`kb/domain/raptor.py`** — pydantic `RaptorNode` + `insert_raptor_node` + `insert_raptor_edge` + `read_raptor_level_embeddings(file_id, level)`.
- **Worker stage `raptor_build_file_impl(file_id)`** — chained from `embed_file_impl()` success path via Procrastinate defer (same separate-tx pattern as 3a→3b→3c chaining). On entry, transitions `embedded → raptor_building` with event `raptor_build_started`. Reads contextual_chunks + chunk_embeddings for the file, calls `build_tree_for_file`, transitions `raptor_building → ready` with event `raptor_build_done` carrying `{leaf_count, levels_built, total_summarizer_calls, summarizer_model_id, embedder_model_id}`. Idempotent: returns immediately if already `ready` OR `raptor_building` (concurrent invocations serialize via lifecycle FOR UPDATE).
- **Failure mode** — any error in cluster/summarize/embed → `raptor_building → failed` with `event='raptor_build_failed'`, payload includes `{error_class, message, level_at_failure}`. Tree writes happen in a single tx so partial failures roll back cleanly.

**Out of scope (deferred):**
- **Corpus-level RAPTOR** (one tree across all docs in a workspace) — Phase 5+ when workspace-level retrieval becomes a thing.
- **HNSW + BM25 indexes on `raptor_nodes.text` + `.embedding`** — Phase 4 (retrieval layer); 3d only writes the tree, retrieval queries it.
- **UMAP dimensionality reduction before clustering** — original paper uses UMAP + GMM. We skip UMAP (heavy dep `umap-learn`); `AgglomerativeClustering` on raw halfvec(3072) is "good enough" for demo and deterministic.
- **Tree-aware retrieval** (top-K per level, then re-rank) — Phase 4.
- **`audit_log` writes on RAPTOR builds** — Phase 9.
- **Configurable branching factor / max levels per workspace** — uses globals + env overrides only.
- **Re-running RAPTOR when chunks change** — raptor tree is rebuilt by deleting + re-running, not incrementally updated. Phase 8 / re-ingest territory.

#### Decisions (locked at G1; changes require re-opening G1)

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | Clustering algorithm (per-doc) | `sklearn.cluster.AgglomerativeClustering(metric='cosine', linkage='average')`. Original RAPTOR uses UMAP+GMM. | Hard clustering (deterministic) instead of soft (paper's GMM). Cosine on unit-norm halfvec(3072) avoids the curse-of-dim that motivated the paper's UMAP step (curse hits Euclidean, not cosine). For per-doc N≤100, O(N²) is trivial. Trade-off accepted: hard-clustered trees have ~10-15% less retrieval coverage than soft-clustered per published benches, but produce deterministic + dep-light builds. **Phase 3e (corpus, N=100K) switches to UMAP+GMM** since AC's O(N²) becomes infeasible — see §5.10.1. |
| 2 | Branching factor | `BRANCHING_FACTOR = 8` (clusters per level ≈ ceil(N/8)). Configurable via `KB_RAPTOR_BRANCHING_FACTOR`. | Matches the original paper. Smaller (e.g., 4) makes deeper trees + more LLM calls; larger (16) makes flatter trees + fewer calls but bigger clusters per summary. 8 is the published sweet spot. |
| 3 | Max tree depth | `MAX_LEVELS = 6` (L1=leaves in contextual_chunks; L2..L6 = raptor_nodes summary levels). Configurable via `KB_RAPTOR_MAX_LEVELS`. | Per-doc trees terminate at L2-L3 naturally (typical demo: 50 chunks → L2 with ~7 nodes → L3 root). Corpus tree on 100K doc-roots wants `log₈(100K)≈5.5` levels. Bumped from 4 → 6 so Phase 3e doesn't need to re-tune. |
| 4 | Termination conditions | Stop when `n_at_level <= 1` (root reached) OR `level > MAX_LEVELS` OR `n_at_level <= BRANCHING_FACTOR` (one more cluster would just collapse to 1 — no information gain). | Three explicit guards. Last one is the most-common exit path for per-doc. Max-levels guard matters for corpus tree. |
| 5 | Summarizer adapter pattern | `Summarizer` Protocol with `GeminiSummarizer` (default) + `AnthropicSummarizer` (alt) + `IdentitySummarizer` (no-key smoke path **only**, not CI test coverage). Factory `make_summarizer()` reads `KB_SUMMARIZER ∈ {gemini, anthropic, identity, auto}`. **Identity caveat (sharpened post-deliberation):** Identity concatenates leaf text into "summary" — produces a degenerate tree where L3 duplicates L2 content. Useful for "pipeline doesn't crash without an API key" smoke test. NOT useful for tree-shape integrity testing. Pytest tree-shape tests use mocked `GeminiSummarizer` with deterministic stubbed text. | Same Protocol shape as 3b-bis's Contextualizer. Two real impls + one mechanical fallback. Identity is honest about its role — no semantic abstraction, just mechanical pass-through. |
| 6 | Default summarizer model | `gemini-2.5-flash` for GeminiSummarizer (configurable via `KB_SUMMARIZER_MODEL`). | Matches 3b-bis's GeminiContextualizer model. Summarization is bounded reasoning; Flash is plenty for 200-400 token outputs. |
| 7 | Summarization prompt | Fixed: *"You are summarizing N chunks from a single document. Produce a concise summary (200-400 tokens) that preserves key facts and themes. Use markdown. Return only the summary."* `max_output_tokens=600`, `thinking_config.thinking_budget=0`. | Adapted from RAPTOR paper appendix. Adding the doc-title or context bloats the prompt; chunks already carry contextual prefixes from 3b. |
| 8 | Per-cluster concurrency | `asyncio.Semaphore(4)` per file (matches 2c's OCR concurrency). Configurable via `KB_SUMMARIZER_CONCURRENCY`. | Independent cluster summarizations within a level can parallelize. Cap at 4 to stay under Gemini Flash free tier (15 RPM). |
| 9 | L1 leaves storage | **L1 leaves stay in `contextual_chunks` — NOT denormalized into `raptor_nodes`.** raptor_nodes stores only L2+ (level CHECK BETWEEN 2 AND 6). raptor_edges discriminates: L2 edges point at `child_contextual_chunk_id` (leaf), L3+ edges point at `child_node_id` (raptor_nodes self-FK). | **Flipped post-deliberation given 100K-doc scale.** Denormalization would cost ~6 KB × 5M leaves = **30 GB** at 100K-doc scale (storage + backup + replication + vacuum cost, plus larger HNSW indexes in Phase 4). Discriminated edge FK is two explicit indexable columns + one CHECK guard — not polymorphic, just explicit. Trades 5 LOC of COALESCE in tree-traversal queries for 30 GB of avoided duplication. The "clean self-FK story" of denormalization was an aesthetic preference; the storage math doesn't survive 100K-doc scale. |
| 10 | Edge model | `raptor_edges (parent_node_id, child_node_id NULL, child_contextual_chunk_id NULL, workspace_id)` with row CHECK `(child_node_id IS NOT NULL) <> (child_contextual_chunk_id IS NOT NULL)` — exactly one non-null. UNIQUE composite keys on `(parent_node_id, child_node_id)` and `(parent_node_id, child_contextual_chunk_id)`. All three FK columns ON DELETE CASCADE. Tree traversal: `WHERE parent_node_id = X` returns mixed children (some raptor_nodes IDs, some contextual_chunks IDs — caller dispatches by which column is non-null). | Two explicit FK columns + one CHECK is cleaner than polymorphic FK + safer than nullable-self-FK. CASCADE on all three handles cleanup when a file deletes (drops contextual_chunks → drops edges via cascade → drops L2 raptor_nodes via cascade as their last edge goes). |
| 11 | Immutability | `raptor_nodes` + `raptor_edges` are append-only — REVOKE UPDATE, DELETE on kb_app (same pattern as `chunks`, `contextual_chunks`, `chunk_embeddings`). Re-build requires superuser delete + re-run. | Downstream Phase 4 retrieval references nodes by ID. In-place mutation would invalidate retrieval-time citations. |
| 12 | Lifecycle states | **Adds `'raptor_building'` intermediate state.** Transitions: `embedded → raptor_building` (event `raptor_build_started`) → `ready` (event `raptor_build_done`) OR `raptor_building → failed` (event `raptor_build_failed`). 0012 migration widens the `files.lifecycle_state` CHECK to include `'raptor_building'` (alongside the already-present `'ready'`). | **Flipped post-deliberation.** Original plan said "no intermediate state — fast operation." But RAPTOR is genuinely multi-stage (cluster + N×summarize + N×embed) and takes 5-20 s/doc. For an open-source ship where lifecycle history is one of the visible observability signals, the intermediate state turns the history into a readable narrative: `queued→parsing→parsed→chunked→contextualized→embedded→raptor_building→ready`. Each state earns its place. Cost: one CHECK widen, one extra `transition_lifecycle` call. |
| 13 | Task chaining | `embed_file_impl()` success path defers `raptor_build_file(file_id)` in a SEPARATE PG transaction (so a Procrastinate-defer failure doesn't roll back the successful embed). | Same shape as 3a→3b→3c chain (3a #9 / 3b #13 / 3c #11). |
| 14 | Failure mode | Cluster/summarize/embed errors → `raptor_building → failed`. Payload: `{error_class, message, level_at_failure: int, traceback_head}`. Partial-write protection: all raptor_nodes + raptor_edges INSERTs for one file happen in a single transaction; failure rolls back the whole tree atomically (no half-built trees that retrieval would query incorrectly). | Loud-fail with atomic rollback beats silent partial state. The single-tx envelope is feasible because per-doc trees are small (≤6 levels × ≤8 nodes/level = ≤48 nodes per file). |
| 15 | Mock embedder reuse | Phase 3c's `make_embedder()` factory is reused as-is for summary-node embeddings — if `KB_GEMINI_API_KEY` is set, real Gemini Embedding 001; else `DeterministicMockEmbedder`. Summary embeddings live in the same halfvec(3072) column as leaf embeddings. | Symmetry: leaf and summary embeddings come from the same vector space, otherwise cosine similarity at retrieval is meaningless. Phase 4 HNSW indexes also assume same-space across all rows. |
| 16 | Forward-compat for Phase 3e | `raptor_nodes` gets `scope text NOT NULL DEFAULT 'per_doc' CHECK (scope IN ('per_doc','corpus'))` + nullable `file_id`. Phase 3e adds rows with `scope='corpus'` and `file_id=NULL`, no migration needed. | Open-source ship at 100K-doc scale: ALTER TABLE ADD COLUMN is fine at 100K rows but a migration nightmare at 100M. Lock the columns NOW even though 3d only writes `'per_doc'`. Same forward-compat convention as 0009's lifecycle CHECK including unbuilt-yet states (3a G2 decision). |

#### Repo layout delta after Phase 3d G4

```
emerging-kb/
├── pyproject.toml + uv.lock                   ← MUTATED (add scikit-learn)
├── migrations/sql/
│   └── 0012_raptor.sql                        ← NEW (raptor_nodes + raptor_edges + RLS + REVOKE)
├── src/kb/
│   ├── raptor/
│   │   └── __init__.py                        ← NEW (cluster_embeddings + build_tree_for_file)
│   ├── summarization/
│   │   └── __init__.py                        ← NEW (Summarizer Protocol + 3 impls + factory)
│   ├── domain/
│   │   └── raptor.py                          ← NEW (RaptorNode pydantic + insert_raptor_node + insert_raptor_edge + read_raptor_level_embeddings)
│   └── workers/
│       └── tasks.py                           ← MUTATED (raptor_build_file_impl + raptor_build_file Procrastinate task + chained defer from embed_file)
└── tests/
    ├── test_raptor_unit.py                    ← NEW (~5 tests: cluster_embeddings determinism + branching arithmetic + tree termination + edge construction + provenance)
    ├── test_summarization_unit.py             ← NEW (~6 tests: GeminiSummarizer w/ mock + IdentitySummarizer + factory selector matrix + prompt shape + error path + thinking-disabled)
    ├── test_raptor_worker.py                  ← NEW (~5 tests: end-to-end embedded→ready, idempotency, identity-summarizer fallback, failure mode, task chaining from embed_file)
    └── specs/phase_3d.md                      ← NEW
```

#### Endpoint contract delta (api_contracts.md §5.2)

The `lifecycle_state` enum description in `§5.2` already lists `ready` as a planned future value ("Phase 3d will add the terminal `ready`"). G2 delta: flip the parenthetical to past tense — `"... ready (Phase 3d's terminal state)"`. Single-line change.

No new HTTP endpoints. RAPTOR tree retrieval is a Phase 4 concern.

#### Phase 3d G5 — what "green" means

`scripts/verify_phase_3d.sh` (new) adds to Phase 0+1a+1b+1c+2a+2b+2c+3a+3b+3c verify checks:
1. `psql` confirms `0012_raptor.sql` applied: `raptor_nodes` + `raptor_edges` tables exist with workspace_id + RLS forced + UPDATE/DELETE revoked from kb_app + UNIQUE constraints + level CHECK (BETWEEN 2 AND 6) + scope CHECK (`per_doc | corpus`) + row CHECK (`per_doc⇔file_id NOT NULL`).
2. `psql` confirms `raptor_nodes.embedding` is `halfvec(3072)` (sanity-check the type).
3. `psql` confirms `raptor_edges` has BOTH `child_node_id` and `child_contextual_chunk_id` nullable columns + exactly-one-non-null CHECK.
4. `psql` confirms `files.lifecycle_state` CHECK includes `'raptor_building'`.
5. E2E PDF parse → chunk → contextualize → embed → `raptor_building` → `ready`. Works without `KB_GEMINI_API_KEY` (Identity summarizer + mock embedder) — proves the no-key smoke path (NOT semantic coverage; that's pytest's job with mocked Gemini).
6. `psql` confirms ≥1 L2 raptor_nodes row (small tiny.pdf will produce ~3 contextual_chunks → AgglomerativeClustering with branching_factor=8 collapses to 1 L2 root → terminates at L2 since n=1).
7. `psql` confirms raptor_edges link the L2 node to all contextual_chunks via `child_contextual_chunk_id`.
8. `psql` confirms lifecycle history includes the full chain `embedded → raptor_building → ready` with events `raptor_build_started` + `raptor_build_done`.
9. Re-deferring `raptor_build_file` → no duplicate `raptor_build_done` event (idempotent on `ready`).
10. Phase-3d pytest: ~16 new tests = 258 (existing) + 16 = ~274.
11. Cross-phase sweep across **11** verify scripts (0/1a/1b/1c/2a/2b/2c/3a/3b/3c/3d) all green.

#### Pre-G3 consistency review checklist

Before G3 opens:
- [ ] Architecture §5 step 10 traceability — Phase 3d covers per-doc cluster + summarize + embed; no leak into Phase 4 (no HNSW, no BM25, no query-time tree traversal) or Phase 3e (no corpus-level rollup yet).
- [ ] RLS invariant grows from 13 → 15 workspace-scoped tables (`raptor_nodes` + `raptor_edges` join the list).
- [ ] `api_contracts.md §5.2` lifecycle enum description widens to include `raptor_building` (3d's transient intermediate state) and reframes `ready` as 3d's terminal.
- [ ] No mutation to `chunk_embeddings`, `contextual_chunks`, `chunks`, `raw_pages`, or `files` schemas — 3d is purely additive (plus the lifecycle CHECK widen on `files`).
- [ ] `0009_chunks.sql`'s forward-compat CHECK already lists `'ready'` (Phase 3c G4 fix) — 0012 only adds `'raptor_building'`.
- [ ] Forward-compat: `raptor_nodes.scope` + nullable `file_id` locked in 0012 even though 3d only writes `scope='per_doc'`. Phase 3e can add `scope='corpus'` rows without ALTER TABLE.
- [ ] `audit_log` writes still deferred to Phase 9.
- [ ] `.env.example` widens with `KB_SUMMARIZER=auto` + commented `KB_SUMMARIZER_MODEL` / `KB_SUMMARIZER_CONCURRENCY` / `KB_RAPTOR_BRANCHING_FACTOR` / `KB_RAPTOR_MAX_LEVELS=6`.

#### Sign-off

G1 signed off 2026-05-24 by Aniket after the open-source-scale deliberation pass (see change-log entry below). G2 opens: single contract delta in `docs/api_contracts.md` §5.2 lifecycle enum (adds `raptor_building` + reframes `ready`). Estimated wall-clock for 3d alone: ~5-7 hr G3+G4+G5 — similar shape to 3c (sklearn clustering replaces 3c's embedding-call complexity).

---

### 5.10.1 Phase 3e plan — RAPTOR tree build, corpus-level (G1 ✅ + G2 ✅ + G3 ✅ + G4 ✅ + G5 ✅ SIGNED OFF)

> **Status:** G1 opens 2026-05-24. Same `phase-3/chunking-raptor` branch (7th commit-set after 3d). Final Wave A phase; closes the architecture's "RAPTOR builds the hierarchy of what's there" (line 41) promise at corpus scale. Forward-compat columns landed at 3d's 0012 migration — **no migration delta at 3e**.

#### Scope

Corpus-level RAPTOR tree across all per-doc roots in a workspace, written into the same `raptor_nodes` + `raptor_edges` tables with `scope='corpus'` and `file_id=NULL`. Triggered explicitly (not auto-on-upload) since at 100K-doc scale a per-upload rebuild would melt the worker pool. Algorithm switches from per-doc's AgglomerativeClustering (O(N²) infeasible at N=100K) to **UMAP + sklearn GaussianMixture** per the RAPTOR paper.

**In scope:**
- **`kb/raptor/corpus.py`** — new sibling to `kb/raptor/__init__.py`:
  - `cluster_embeddings_corpus(vectors, branching_factor) -> list[int]` — UMAP reduces 3072 → 10 dim; sklearn `GaussianMixture(n_components=ceil(N/branching))` soft-clusters in low-dim. Deterministic via `random_state`.
  - `read_doc_roots_for_workspace(conn, workspace_id) -> list[tuple[str, str, list[float], str]]` — returns `(root_id, root_text, root_embedding, root_kind)` per file in workspace. For multi-leaf files: highest-level `raptor_nodes` row (scope='per_doc'). For singleton-leaf files: the single `contextual_chunks` row. `root_kind ∈ {'node', 'chunk'}` discriminates which edge FK column to use later.
  - `build_corpus_tree(workspace_id)` — orchestrator: read all doc-roots → cluster → summarize (Summarizer factory) → embed (Embedder factory) → write `scope='corpus'` rows + edges (discriminated FK: edges from corpus-L2 may point at either raptor_nodes IDs OR contextual_chunks IDs depending on root_kind) → recurse for L3..MAX_LEVELS.
- **Worker stage `raptor_build_corpus_impl(workspace_id)`** in `kb/workers/tasks.py` — NOT chained from any file-level event (explicit trigger only). Atomic rebuild: open tx, DELETE existing `scope='corpus'` rows for the workspace, then INSERT the new tree. All-or-nothing. Idempotent: a no-op re-trigger just rebuilds the same tree (deterministic given inputs).
- **New endpoint `POST /corpus/raptor/rebuild`** in `kb/api/corpus.py` (new router module) — defers `raptor_build_corpus` Procrastinate task. Returns `202 Accepted` with `{workspace_id, task_id, status: 'queued'}`. **Wave A: open** (no auth gating; relies on `X-Test-Workspace` header). Admin protection deferred to Phase 9.
- **New deps:** `umap-learn>=0.5.7` + its dep `pynndescent>=0.5`.
- **`.env.example`** widens with `KB_RAPTOR_CORPUS_UMAP_DIM=10` + commented `KB_RAPTOR_CORPUS_UMAP_NEIGHBORS` / `KB_RAPTOR_CORPUS_GMM_SEED` overrides.

**Out of scope (deferred):**
- **`GET /corpus/raptor`** read endpoint — Phase 4 retrieval reads `raptor_nodes`/`raptor_edges` directly via SQL. A REST navigation surface for end-user UIs lands with Phase 8+.
- **Status / progress polling** on the rebuild job — Procrastinate's `procrastinate_jobs` table is queryable via SQL for status; an admin polling endpoint lands at Phase 9.
- **Incremental updates** when new files arrive after a rebuild — the corpus tree is stale until next manual rebuild. Phase 5+ adds CDC-based incremental updates.
- **Admin authorization** on `/corpus/raptor/rebuild` — open in Wave A per user direction (interview demo). Phase 9 adds RBAC.
- **HNSW + BM25 indexes** on the new corpus rows — Phase 4.
- **Tree-aware retrieval** (top-K per corpus level + per-doc level + chunk level) — Phase 4.
- **Per-workspace clustering hyperparameter overrides** (env-only in Wave A).
- **Cross-workspace corpus trees** — corpus trees are per-workspace, not cross-tenant.
- **`audit_log` writes** — Phase 9.

#### Decisions (locked at G1; changes require re-opening G1)

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | Corpus clustering algorithm | **UMAP + sklearn GaussianMixture** (per the RAPTOR paper). UMAP reduces 3072 → 10 dim; GMM soft-clusters in low-dim. | AgglomerativeClustering's O(N²) makes it infeasible at N=100K (10^10 distance comps). UMAP's nearest-neighbor approximation is O(N log N). GMM in low-dim is well-behaved (the curse-of-dim that motivated UMAP applies here). Soft clustering matters more at corpus scale — a doc spans multiple themes; hard-clustering would force a binary topic choice. Determinism via `random_state` (decision #10). |
| 2 | UMAP target dimension | **`n_components=10`** (configurable via `KB_RAPTOR_CORPUS_UMAP_DIM`). | Paper default. Higher dims preserve more structure but inflate GMM cost; lower dims lose nuance. 10 is the published sweet spot. |
| 3 | UMAP `n_neighbors` | **`n_neighbors=15`** (configurable). | Paper default. Smaller (5) makes clusters too local; larger (50) blurs theme distinctions. |
| 4 | GMM cluster count per level | `n_components = ceil(N / BRANCHING_FACTOR)` (reuses 3d's `KB_RAPTOR_BRANCHING_FACTOR=8`). | Same paper default as per-doc. Consistent UX: tree shape doesn't change shape between per-doc and corpus. |
| 5 | MAX_LEVELS | Reuses 3d's `KB_RAPTOR_MAX_LEVELS=6`. Corpus tree on 100K doc-roots wants `log₈(100K)≈5.5` levels — fits in 6. | Already locked at 3d G1 decision #3 for this reason. |
| 6 | Doc-root source | Per-doc roots = highest-level `raptor_nodes WHERE scope='per_doc' AND file_id=X` (for multi-leaf files) **OR** the single `contextual_chunks` row (for singleton-leaf files where no per-doc raptor_nodes exist). Heterogeneous L1 input. | Singleton-leaf files would otherwise be excluded from corpus organization. They have meaningful content — must participate. Discriminated edge FK already supports mixed children (decision #7). |
| 7 | Edge FK for corpus L2 | Reuses 3d's discriminated `raptor_edges` schema: `child_node_id` for raptor_nodes children (multi-leaf docs' per-doc roots) + `child_contextual_chunk_id` for contextual_chunks children (singleton-leaf docs). | The discriminated-FK design was deliberately built to support this case. Edge writes branch on root_kind from decision #6. |
| 8 | Corpus rebuild trigger | **Explicit `POST /corpus/raptor/rebuild`** only. NOT auto-triggered on file upload, NOT periodic. | At 100K-doc scale, per-upload rebuild = 100K corpus rebuilds = melted worker pool + $$$ in LLM calls. Operator opts in via the endpoint. Phase 5+ can add cron / CDC-driven triggers when scale demands. |
| 9 | Rebuild atomicity | DELETE all `scope='corpus' WHERE workspace_id=X` + INSERT new tree in ONE transaction. All-or-nothing. | Stale-but-consistent corpus tree beats partial-rebuild + retrieval-time confusion. The DELETE+INSERT pattern is fine — `raptor_nodes` are not Phase 4 HNSW-indexed yet in 3e (Phase 4's indexes will be `WHERE scope='corpus'` partial indexes that auto-update). |
| 10 | Determinism | `random_state=42` for both UMAP + GMM (configurable via `KB_RAPTOR_CORPUS_GMM_SEED`). | Same input doc-roots → same corpus tree structure across rebuilds. Required so Phase 4 retrieval citations remain stable when the tree is rebuilt with no new docs. |
| 11 | Endpoint authorization | **Open in Wave A** (no auth gating; respects `X-Test-Workspace` header same as other endpoints). Admin RBAC deferred to Phase 9. | Per user direction (interview demo). Doc clearly states "ops-restricted in production." |
| 12 | Endpoint response | `202 Accepted` with `{workspace_id, task_id, status: 'queued'}`. Job runs async via Procrastinate; client polls via SQL on `procrastinate_jobs` (or via Phase 9's admin polling endpoint). | Standard async-job pattern. 202 matches HTTP semantics for "accepted but not yet processed". |
| 13 | Tiny-corpus termination | `N ≤ 1` (single doc in workspace) → skip; no corpus tree built. `N ≤ BRANCHING_FACTOR=8` → build ONE L2 root summarizing all docs, terminate. Same termination logic as 3d. | Consistent with per-doc tree behavior. A 1-doc workspace doesn't need a corpus tree; an 8-doc workspace gets a single-root corpus. |
| 14 | Summarizer + Embedder reuse | Reuses `make_summarizer()` (3d) + `make_embedder()` (3c) factories AS-IS. Corpus summary prompt same as per-doc summary prompt (decision #7 from 3d). | Symmetry — corpus summaries are just per-doc summaries one level up. Same vector space matters for retrieval-time cosine across all levels. |
| 15 | Tracking / audit | NO new tables; rely on `procrastinate_jobs` for job status + `max(raptor_nodes.created_at) WHERE scope='corpus' AND workspace_id=X` for "last rebuild time". Phase 9 adds proper `audit_log` writes. | Minimum surface for Wave A. Avoids a new table for what's transient metadata. |

#### Repo layout delta after Phase 3e G4

```
emerging-kb/
├── pyproject.toml + uv.lock                   ← MUTATED (add umap-learn + pynndescent)
├── src/kb/
│   ├── raptor/
│   │   └── corpus.py                          ← NEW (cluster_embeddings_corpus + read_doc_roots_for_workspace + build_corpus_tree)
│   ├── api/
│   │   ├── corpus.py                          ← NEW (POST /corpus/raptor/rebuild router)
│   │   └── main.py                            ← MUTATED (mount corpus router)
│   └── workers/
│       └── tasks.py                           ← MUTATED (raptor_build_corpus_impl + raptor_build_corpus Procrastinate task)
└── tests/
    ├── test_raptor_corpus_unit.py             ← NEW (~4 tests: UMAP+GMM determinism + branching + termination + read_doc_roots heterogeneous)
    ├── test_raptor_corpus_worker.py           ← NEW (~4 tests: end-to-end build_corpus_tree → scope='corpus' rows + heterogeneous-edge cases + atomic-rebuild + idempotent re-trigger)
    ├── test_corpus_api.py                     ← NEW (~3 tests: POST returns 202 + defers job + invalid body → 400)
    └── specs/phase_3e.md                      ← NEW
```

No SQL migration. No new domain module (extends `kb/domain/raptor.py` with `delete_corpus_rows_for_workspace` helper).

#### Endpoint contract delta (api_contracts.md — new §6 sub-section)

Adds a new top-level `## 6. Phase 3e — Corpus RAPTOR` section to `api_contracts.md` (since Phase 5 in §5 is files; corpus is structurally new). Single endpoint documented:
- `POST /corpus/raptor/rebuild` — body `{}` (workspace implied from `X-Test-Workspace`). Response `202 Accepted` with `{workspace_id, task_id, status, message}`. Error `400` if workspace has no files; `503` if a rebuild is already in-flight (detected via Procrastinate jobs).

#### Phase 3e G5 — what "green" means

`scripts/verify_phase_3e.sh` (new) adds to Phase 0+1a+1b+1c+2a+2b+2c+3a+3b+3c+3d verify checks:
1. `psql` confirms umap-learn is import-able in the worker container.
2. POST 5 distinct PDFs (so we have 5 doc-roots after the per-doc chain completes).
3. Wait for all 5 files to reach `lifecycle_state='ready'`.
4. `curl POST /corpus/raptor/rebuild` → 202; capture `task_id`.
5. Wait for the corpus rebuild job to complete (poll `procrastinate_jobs WHERE task_name='raptor_build_corpus'`).
6. `psql` confirms `≥1 raptor_nodes row WHERE scope='corpus' AND workspace_id=$WS`.
7. `psql` confirms `raptor_edges` exist from corpus-L2 nodes to per-doc roots (cross-scope edges).
8. Re-run `POST /corpus/raptor/rebuild` → 202; verify atomic rebuild (old corpus rows replaced, not appended).
9. Phase-3e pytest: ~11 new tests → 286 total.
10. Cross-phase sweep across **12** verify scripts (0/1a/1b/1c/2a/2b/2c/3a/3b/3c/3d/3e) all green.

#### Pre-G3 consistency review checklist

Before G3 opens:
- [ ] Architecture line 41 + line 198 traceability — Phase 3e closes the "RAPTOR builds the corpus hierarchy" promise.
- [ ] No new migration (forward-compat columns already locked at 3d G4).
- [ ] api_contracts §6 new section drafted (single endpoint).
- [ ] `.env.example` widens with UMAP/GMM tuning overrides.
- [ ] RLS invariant unchanged at 15 workspace-scoped tables.
- [ ] `audit_log` writes still deferred to Phase 9.
- [ ] No leak into Phase 4 territory (no HNSW, no BM25, no `GET /corpus/raptor` navigation, no retrieval).
- [ ] No leak into Phase 5+ (no incremental updates, no cron, no admin RBAC).

#### Sign-off

When Aniket approves this plan, the Phase 3e G1 cell in §5 flips 🟡 → ✅ and G2 opens (new §6 in `docs/api_contracts.md` for the corpus endpoint). Estimated wall-clock: ~4-6 hr G3+G4+G5 — smaller than 3d (schema unchanged, infrastructure mostly reused).

---

### 5.11 Phase 4 plan — Indexing (HNSW + BM25 on all RAPTOR levels) (G1 ✅ + G2 — + G3 ✅ + G4 ✅ + G5 ✅ SIGNED OFF)

> **Status:** All 5 gates green 2026-05-25 by Aniket. G2 was a no-op per decision #16. **296/296 pytest in 89.84s.** `verify_phase_4.sh` 16/16 standalone. Cross-phase sweep across all 13 verify scripts: **13/13 GREEN in 14:56**. Branch `phase-4/retrieval` ready to merge. First Wave A phase past ingestion. **Indexing-only** — no HTTP retrieval surface, no orchestration, no rerank. That's all Phase 8 ("Query planner + rewriting + parallel retrieval + RRF + rerank + CRAG + Astute" per architecture §12 line 1167–1169). Phase 4 lays the index foundation that Phase 8 will consume; the 7 of 10 channels that need L2/L2b/L3 atomic-unit + mention + entity artifacts (per README §3 pillar 3) are gated on Phase 5–7 first.

#### Why indexing-only and not "ship a basic /search"

Considered and explicitly rejected at G1. Architecture §12 line 1164 says Phase 4 is "Indexing: pgvector HNSW + pg_search BM25 on all RAPTOR levels" — full stop. The temptation to add a minimal `/search` endpoint here would either:
1. Ship plain hybrid BM25+dense — exactly what the architecture's "10 parallel retrieval channels" promise differentiates against. Demoing it sells a different system than the one in the spec.
2. Commit to a `/search` contract this phase that Phase 8 will then have to rewrite once 5/6/7 add atomic-units, mentions, entities, anomaly scores — sub-phase churn we don't need.

Internal smoke helper in `kb/retrieval/smoke.py` proves the indexes work end-to-end at G5 (BM25 returns ranked hits, dense returns ranked hits, EXPLAIN ANALYZE shows planner uses the indexes). NOT mounted on any router. Phase 8 builds the actual orchestrator + endpoints on top.

#### Scope

Pure DDL + index-maintenance phase + a thin internal smoke helper (no HTTP).

**In scope:**
- **`0013_indexes.sql` migration** — adds 4 indexes:
  - HNSW on `chunk_embeddings.embedding` (`halfvec_cosine_ops`, `m=16`, `ef_construction=200`)
  - HNSW on `raptor_nodes.embedding` (same params; covers BOTH `scope='per_doc'` and `scope='corpus'` rows — single graph)
  - BM25 (pg_search) on `contextual_chunks.contextual_text`
  - BM25 (pg_search) on `raptor_nodes.text`
  - All four use `CREATE INDEX CONCURRENTLY` so migrations are non-blocking against live writers (Wave A worker writes through migration; pgvector + pg_search both support CONCURRENTLY).
- **`scripts/reindex_weekly.sh`** — REINDEX CONCURRENTLY rotation. Stub script + cron entry doc; not wired to a scheduler in Wave A (operator runs manually or via host cron). Architecture §15 line 1267 commitment.
- **`src/kb/retrieval/__init__.py` + `src/kb/retrieval/smoke.py`** — new internal package. Smoke helper exposes `bm25_smoke(conn, *, workspace_id, query, limit)` and `dense_smoke(conn, *, workspace_id, query_vec, limit)`. Each returns `list[tuple[id, score, level, scope]]`. NOT a Protocol, NOT mounted on a router, NOT importable from `kb.api`. Used by `verify_phase_4.sh` + the Phase 4 pytest suite to prove indexes work.
- **`scripts/verify_phase_4.sh`** — extension presence + 4 indexes exist with right operator classes + EXPLAIN ANALYZE proves planner uses each index (not seq scan) + smoke helper returns ranked results against seeded data.
- **Forward-compat audit** — every accept-set in the 12 prior verify scripts still passes with Phase 4 in tree (no lifecycle states change, so this should be free; locked in the §0.15 forward-compat convention).
- **`.env.example`** widens with `KB_HNSW_M=16`, `KB_HNSW_EF_CONSTRUCTION=200`, `KB_HNSW_EF_SEARCH=40` (query-time recall/latency knob). All commented; defaults match the migration.

**Out of scope (deferred):**
- **`POST /search` endpoint** — Phase 8.
- **Query rewriting** (Step-Back / HyDE / Query2Doc) — Phase 8.
- **RRF fusion across channels** — Phase 8.
- **Cross-encoder rerank** (Cohere Rerank 3.5 / mxbai-rerank-large-v2) — Phase 8.
- **CRAG gate + Astute generation** — Phase 8.
- **Tree-aware top-K-per-level orchestration** — Phase 8.
- **The other 6 retrieval channels** (atomic-unit filter, anomaly filter, mention lookup, doc metadata, HippoRAG PPR, ColPali) — gated on Phase 5 (atomic units + mentions), Phase 7 (entities), Phase 14 (HippoRAG), Wave C (ColPali).
- **Per-tenant HNSW partitioning** (separate graph per workspace_id) — single shared graph in Wave A; partition switch evaluated at the 1M-doc scale tier per `scale_perf_audit.md`.
- **Index on `raw_pages.text`** — chunks already cover the same text at finer granularity; raw_pages is a parse-artifact intermediate, not a retrieval target.
- **HNSW index rebuild scheduling automation** — cron stub only; production scheduler at Phase 9.
- **A/B index tuning matrix** (different `m`/`ef_construction` per table) — single tuning set in Wave A; tunable via env.
- **`audit_log` writes** — Phase 9.

#### Decisions (locked at G1; changes require re-opening G1)

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | Indexing scope | 4 indexes: 2 HNSW (`chunk_embeddings.embedding` + `raptor_nodes.embedding`) + 2 BM25 (`contextual_chunks.contextual_text` + `raptor_nodes.text`). | Architecture §12 line 1164 — "pgvector HNSW + pg_search BM25 on all RAPTOR levels". Per-doc + corpus raptor share one table (`raptor_nodes`) so one HNSW + one BM25 covers both scopes. Chunks (L1 leaves) get their own pair. |
| 2 | HNSW operator class | `halfvec_cosine_ops` on both vector columns. | Embeddings are `halfvec(3072)` since Phase 3c (~50% storage savings via float16). Cosine matches the embedding model's optimization (Gemini Embedding 001 is L2-normalized and cosine-optimized; same vector space across per-doc + corpus per 3e decision #14). |
| 3 | HNSW build parameters | `m=16`, `ef_construction=200`. Single tuning set for both vector columns. | pgvector defaults. m=16 is the standard recall/index-size tradeoff (m=8 hurts recall on 3072-dim; m=32 doubles index size for marginal gain at Wave A scale). ef_construction=200 is paper-default; pushes build time but improves recall. Query-time recall via `ef_search` (decision #8). |
| 4 | CONCURRENTLY | `CREATE INDEX CONCURRENTLY` for all 4. | Wave A worker writes through migration deploys. Non-CONCURRENTLY would block writers during build (pgvector HNSW build on 100K embeddings takes minutes). pg_search supports CONCURRENTLY since ParadeDB 0.10+. |
| 5 | Single graph vs per-workspace partitions | **Single shared HNSW graph** for both vector indexes. No partitioning by `workspace_id`. | At 100K-doc Wave A scale, single graph + RLS filter at query time is the right tradeoff. Per-tenant partitioning becomes worth it at ~1M docs OR ~100+ tenants (see `scale_perf_audit.md`). Phase 4's HNSW WHERE-clause filtering by workspace_id costs ~1.5× vs partitioned but saves 100× operational complexity. Re-evaluate at scale graduation. |
| 6 | BM25 tokenizer | pg_search defaults (Tantivy `default` tokenizer + lowercase + ASCII folding). | Wave A corpus is English-only PDFs (CUAD + Enron + SEC 10-K). Tantivy's default handles word boundaries + lowercases + folds ASCII. Custom analyzers (stemming, language-specific) deferred to Wave B per architecture §15. |
| 7 | BM25 weights | pg_search default BM25 (k1=1.2, b=0.75). | Robertson/Spärck Jones defaults. Tuning the BM25 params per-corpus is Phase 12 eval-driven work. |
| 8 | Query-time recall knob | `KB_HNSW_EF_SEARCH=40` env (defaults the per-session `SET hnsw.ef_search`). | Recall/latency knob. 40 = pgvector default; higher = better recall, slower. Lockable per-environment without rebuilding indexes. Phase 8's planner can override per-query (research queries → 100; ambient suggestions → 20). |
| 9 | Index maintenance | Weekly `REINDEX CONCURRENTLY` per index, gated on `>5% new chunks since last reindex`. Stub script `scripts/reindex_weekly.sh`; operator runs via host cron in Wave A. | Architecture §15 line 1267 commitment. HNSW graphs fragment as embeddings accumulate; periodic REINDEX restores recall. 5% gate avoids no-op reindexes on quiet workspaces. Production scheduler is Phase 9. |
| 10 | No HTTP surface this phase | Internal `kb.retrieval.smoke` module ONLY. NOT mounted on any router. NOT importable from `kb.api.*` files. | Per scope justification above — `/search` belongs to Phase 8 (after extraction phases land the 7 non-dense channels). Phase 4's smoke helper is for verification only; Phase 8 wraps the indexes in the real `kb.retrieval.*` modules + `/search` endpoint. |
| 11 | Smoke helper shape | `bm25_smoke(conn, *, workspace_id, query, limit) -> list[tuple[id, score, level, scope]]` + `dense_smoke(conn, *, workspace_id, query_vec, limit) -> list[tuple[id, score, level, scope]]`. Single result schema across both helpers + across all 4 indexed tables (UNION-style query internally). | Lets verify_phase_4.sh + pytest assert: (a) hits come back ranked, (b) hits span multiple levels (chunk + raptor_node), (c) workspace isolation holds (RLS still applies — kb_app role on conn). Pure functions; testable without HTTP machinery. |
| 12 | Test corpus for smoke | Seeded via existing Wave A pipeline (POST tiny.pdf → wait `ready` → indexes auto-populate via INSERT) + 5-doc multi-file seed for cross-doc BM25 + dense recall checks. NO synthetic SQL inserts that bypass the lifecycle. | Tests must exercise the REAL ingestion → index path. A SQL-only seed would mask bugs where the worker writes to a table the indexes don't cover. |
| 13 | Migration ordering / numbering | `0013_indexes.sql`. Single file. | Continues the lexical-order convention (0001..0012 exist). One file = one transactional CREATE INDEX CONCURRENTLY batch; pgvector + pg_search both honor it. |
| 14 | No new lifecycle states | Phase 4 adds indexes only; no `file_lifecycle.state` widening, no new worker tasks. The `ready` terminal state already covers "indexed" implicitly (indexes are auto-maintained by Postgres). | Avoids cascading forward-compat fixups on the 12 prior verify scripts. |
| 15 | Pre-existing GRANTs | `kb_app` already has SELECT on the 4 indexed tables. Index USAGE is auto-granted by Postgres when SELECT is granted on the parent table. No GRANT changes needed. | Standard Postgres behavior; calls it out so a reviewer doesn't ask. |
| 16 | api_contracts.md delta | **NONE.** No new endpoints, no shape changes. The §6 corpus router (3e) and §5 files router (2a-c) are unchanged. | Honest signal that this is a pure-infrastructure phase. Phase 8's G2 is where retrieval contracts land. |

#### Repo layout delta after Phase 4 G4

```
emerging-kb/
├── migrations/sql/
│   └── 0013_indexes.sql                          ← NEW (4 indexes, all CONCURRENTLY)
├── src/kb/
│   └── retrieval/                                ← NEW package
│       ├── __init__.py                           ← module exports
│       └── smoke.py                              ← bm25_smoke + dense_smoke (internal, no HTTP)
├── scripts/
│   ├── reindex_weekly.sh                         ← NEW (cron stub for REINDEX CONCURRENTLY)
│   └── verify_phase_4.sh                         ← NEW (DDL + EXPLAIN + smoke E2E)
├── tests/
│   ├── test_indexes.py                           ← NEW (~6 tests: DDL existence + operator classes + planner uses indexes)
│   ├── test_retrieval_smoke.py                   ← NEW (~5 tests: bm25_smoke + dense_smoke + workspace isolation + multi-level hits)
│   └── specs/phase_4.md                          ← NEW
└── .env.example                                  ← MUTATED (KB_HNSW_M + KB_HNSW_EF_CONSTRUCTION + KB_HNSW_EF_SEARCH)
```

No mutations to `kb/api/*`, `kb/workers/*`, `kb/domain/*`. No `pyproject.toml` deps (pg_search already on board via ParadeDB image; pgvector likewise). No migration to existing tables (only ADD INDEX).

#### Phase 4 G5 — what "green" means

`scripts/verify_phase_4.sh` (new) adds to the 12 prior verify checks:
1. `psql` confirms `vector` + `pg_search` + `ltree` extensions installed (smoke from Phase 0; re-verified).
2. `psql` confirms 4 indexes exist with the right operator classes:
   - `chunk_embeddings_embedding_hnsw_idx` USING hnsw + `halfvec_cosine_ops`
   - `raptor_nodes_embedding_hnsw_idx` USING hnsw + `halfvec_cosine_ops`
   - `contextual_chunks_text_bm25_idx` USING bm25
   - `raptor_nodes_text_bm25_idx` USING bm25
3. Seed via existing pipeline: POST 5 distinct PDFs → wait all 5 to `lifecycle_state='ready'` (reuses 3e's 5-doc seed pattern).
4. EXPLAIN (FORMAT JSON) on a SELECT-by-embedding query confirms the planner picks `Index Scan using chunk_embeddings_embedding_hnsw_idx`, NOT `Seq Scan`.
5. EXPLAIN on a SELECT-by-text query confirms the planner picks the BM25 index.
6. `bm25_smoke` against the worker container returns ≥1 hit with score > 0 for a known-good query against seeded data.
7. `dense_smoke` against the worker returns ≥1 hit ranked by cosine.
8. Workspace B's smoke call returns empty (RLS holds at the index level).
9. Phase 4 pytest: ~11 new tests → suite total ~297.
10. **Cross-phase sweep across all 13 verify scripts** (0/1a/1b/1c/2a/2b/2c/3a/3b/3c/3d/3e/4) all green via `scripts/verify_sweep.sh`.

#### Pre-G3 consistency review checklist

Before G3 opens:
- [ ] Architecture line 1164 traceability — "Indexing: pgvector HNSW + pg_search BM25 on all RAPTOR levels" closes here.
- [ ] No HTTP surface added (grep `kb/api/` for new routers — must be empty).
- [ ] No new lifecycle states or worker tasks (grep `file_lifecycle` + `kb/workers/tasks.py` for new entries — must be unchanged).
- [ ] Forward-compat convention (§0.15) — all 12 prior verify scripts' accept-sets unchanged (since `ready` remains the terminal state).
- [ ] api_contracts.md unchanged.
- [ ] `.env.example` widens with the 3 HNSW knobs.
- [ ] RLS invariant unchanged at 15 workspace-scoped tables.
- [ ] No leak into Phase 5+ (no atomic-unit extraction · no anomaly scoring · no mention extraction · no entity resolution).
- [ ] No leak into Phase 8 (no `/search` endpoint · no RRF · no rerank · no CRAG · no Astute generation · no query rewriting · no tree-aware top-K-per-level orchestrator).
- [ ] No leak into Phase 9 (no scheduler integration · no `audit_log` writes · no admin polling).
- [ ] `scripts/reindex_weekly.sh` exists but is a documented stub — NOT wired into compose or any production scheduler.

#### Sign-off

When Aniket approves this plan, the Phase 4 G1 cell in §5 flips ⬜ → ✅ and G2 opens (no `docs/api_contracts.md` delta — Phase 4 has no HTTP surface). Estimated wall-clock: ~3-5 hr G3+G4+G5 — smallest phase since 3b-bis (DDL-only, no LLM calls, no algorithm work).

---

### 5.12 Phase 5 parent — Open extraction (mentions + emergent fields + atomic units) (G1 ✅ SIGNED OFF · split into 5a/5b/5c)

> **Status:** G1 opened 2026-05-25 on branch `phase-5/extraction` off main at `1c6c274`. Per user direction: build all three sub-phases end-to-end without intermediate sign-off; decisions chosen by Claude per problem_statement.md + architecture.md §5 steps 12, 12b–12d, 14.
>
> **Architecture mapping:**
> - 5a = architecture step 12 — mention extraction (NER over contextual chunks; output `extracted_mentions`).
> - 5b = architecture steps 12b + 12c + 12d — emergent field proposal + cross-doc clustering + auto-promotion to typed schema. **Skips step 12e** (vocabulary discovery — deferred to Phase 6/7 since it requires identity resolution context first).
> - 5c = architecture step 14 — L3 atomic-unit extraction (doc-type plugins) + per-type rarity / anomaly scoring. **Skips step 13** (OpenIE triples — gates on identity resolution Phase 7) and step 11 (ColPali — Wave C).
>
> **Worker chain extension:** `raptor_building → mentions_extracting → fields_extracting → units_extracting → ready`. Each sub-phase chains via separate-tx defer (same pattern as 3a→3b→3c→3d). One forward-compat lifecycle CHECK widening across all 3 sub-phases (locked at 5a's migration).
>
> **What "phase 5 complete" means:** uploading a contract PDF and seeing it flow end-to-end through chunk → contextualize → embed → raptor → mentions → fields → atomic units → ready, with all artifacts queryable via SQL. Phase 5 produces the inputs 7-of-10 retrieval channels (Phase 8) need.

---

### 5.12.1 Phase 5a plan — Mention extraction (NER) (G1 ✅ SIGNED OFF · G2 — no-op)

> **Status:** G1 ✅ + G2 — signed off 2026-05-25. G2 was a no-op (no api_contracts delta — Phase 5a has no new HTTP surface; mentions auto-extract).

**Scope:** LLM-based NER over `contextual_chunks.contextual_text` → `extracted_mentions` table. Auto-chained after `raptor_build_file_impl`.

**Decisions locked:**

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | Extraction granularity | per-`contextual_chunks` row | Matches embedded unit; chunk's contextual prefix gives LLM doc context for disambiguation. |
| 2 | Mention type set | **OntoNotes-18** (PERSON, NORP, FAC, ORG, GPE, LOC, PRODUCT, EVENT, WORK_OF_ART, LAW, LANGUAGE, DATE, TIME, PERCENT, MONEY, QUANTITY, ORDINAL, CARDINAL) | Industry standard; Gemini handles natively in prompts. |
| 3 | LLM | **Gemini 2.5 Flash** structured outputs (JSON mode). 3-impl factory: `KB_MENTIONS_EXTRACTOR ∈ {gemini, anthropic, identity, auto}`. `auto` probes Gemini → Anthropic → Identity. | Mirrors 3b-bis/3d factory pattern. Identity returns `[]` (CI/no-key path). |
| 4 | Output schema | `{mentions: [{text, type, start, end?, confidence?}]}`. `start`/`end` are char offsets in `contextual_text`; nullable (LLM may omit). `confidence` LLM self-reported ∈ [0,1]; nullable. | Strict + tolerant. Phase 8 can use offsets for highlighting; nullable so a missing offset doesn't fail extraction. |
| 5 | Storage | `extracted_mentions(id, contextual_chunk_id, file_id, workspace_id, mention_text, mention_type, start_offset NULL, end_offset NULL, confidence NULL, model_id, created_at)`. Workspace-scoped, RLS day-1, REVOKE UPDATE/DELETE (immutable; re-extract = DELETE-then-INSERT in tx). | Standard pattern from 3c/3d. Indexes: `(workspace_id, mention_type)`, `(file_id)`, `(workspace_id, mention_text)`. |
| 6 | Lifecycle state | New: `mentions_extracting`. CHECK widens to include `fields_extracting` + `units_extracting` (forward-compat for 5b/5c — single migration adds all three states). | Avoids 3 separate CHECK widenings. Follows §0.15 forward-compat convention. |
| 7 | Worker stage | `extract_mentions_file_impl(file_id)` chained from `raptor_build_file_impl` end via separate-tx defer. | Matches 3a→3b→3c→3d chaining shape. |
| 8 | Idempotency | At start: DELETE existing mentions WHERE file_id; then INSERT new ones in same tx. | Re-running task = same output. Avoids needing a re-extract endpoint in Wave A. |
| 9 | Concurrency | `asyncio.Semaphore(KB_MENTIONS_CONCURRENCY=4)` per file (parallel per-chunk LLM calls). | Free-tier safe; bump for paid tier. |
| 10 | PII flagging | **Deferred to 5b** (architecture step 12b's emergent-field extraction owns PII flagging; mention extraction doesn't need it — types like MONEY/DATE aren't PII themselves). | Keeps 5a tight. |
| 11 | Re-extract HTTP endpoint | **Deferred to Phase 9** admin surface. | Worker-level idempotency (decision #8) makes the endpoint a thin wrapper; ship it with the rest of Phase 9 admin surface. |

**Files:** `migrations/sql/0014_mentions.sql` · `src/kb/extraction/__init__.py` + `mentions.py` · `src/kb/domain/mentions.py` (repo) · widen `src/kb/workers/tasks.py` (new `extract_mentions_file_impl` + register task + change `raptor_build` end-state to `mentions_extracting` + chain defer) · `tests/test_mentions_unit.py` + `tests/test_mentions_worker.py`.

---

### 5.12.2 Phase 5b plan — Emergent fields + doc-type classifier + auto-promotion (G1 ✅ SIGNED OFF · G2 — no-op)

> **Status:** G1 ✅ + G2 — signed off 2026-05-25.

**Scope:** Bottom-up field proposal per doc + cross-doc clustering by doc-type + auto-promotion to typed schema (`schema_entities` / `schema_fields` rows, scope='auto'). Plus a lightweight doc-type classifier (single LLM call per file).

**Decisions locked:**

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | Doc-type classifier | Single Gemini call per file with doc context + ask for a 1-3 word label. Stored on `files.inferred_doc_type` (nullable text). | Cheap (~$0.001/doc); needed for cross-doc field clustering. |
| 2 | Field proposer | Per-file LLM call: "list the structured fields in this doc with name + description + value + is_pii flag". JSON output. | Matches architecture step 12b. |
| 3 | Storage — raw proposals | `proposed_fields(id, file_id, workspace_id, inferred_doc_type, field_name, field_description, value_text, value_type, is_pii, model_id, created_at)`. RLS day-1, immutable. | Raw extraction; survives re-runs as separate rows tagged by model_id. |
| 4 | Storage — clustered + promotion-ready | `inferred_schema_fields(id, workspace_id, inferred_doc_type, canonical_name, description, value_type, n_docs_observed, prevalence, stability, value_type_confidence, is_promoted, promoted_at, promoted_schema_field_id, created_at, updated_at)`. RLS day-1. UPDATE allowed (metrics refresh). | One row per (doc_type, canonical_field) per workspace. Auto-promotion sets `is_promoted=true` and links to the new `schema_fields` row. |
| 5 | Cross-doc clustering algo | Within each doc_type: blocking on field-name embedding cosine ≥ 0.85; LLM-judge on borderline; union-find for clusters; canonical_name = most common form. | Per architecture step 12c. Embeddings via existing `make_embedder()` (Phase 3c). |
| 6 | Auto-promotion thresholds | `prevalence ≥ 0.80 AND stability ≥ 0.90 AND value_type_confidence ≥ 0.90 AND n_docs_observed ≥ KB_PROMOTION_MIN_DOCS` (default **5** for demo). | Per problem_statement.md requirement #3 + architecture step 12d. |
| 7 | Auto-promotion target | INSERT a `schema_entities` row (one per doc_type, name=`auto:<doc_type>`) + INSERT one `schema_fields` row per promoted field. Schema versioning (Phase 1b) auto-bumps. Add `schema_fields.auto_promoted boolean NOT NULL DEFAULT false`. | Reuses Phase 1c's tables; auto_promoted flag distinguishes machine vs human additions for the UI. |
| 8 | Worker stages | (a) `classify_doc_type_file_impl` (small, optional — could fold into extract_fields). (b) `extract_fields_file_impl` chained from `extract_mentions_file_impl` end. Inside extract_fields: classify doc-type → propose fields → cluster within workspace+doc_type → check promotion thresholds → promote if crossed → advance to `fields_extracting → units_extracting`. | Folds classifier into extract_fields for simplicity; one LLM call to classify + one to propose. |
| 9 | Auto-creation of schemas | Worker creates schema (name=`auto:<doc_type>`, lifecycle_state='active') if none exists for `(workspace_id, doc_type)`. Bypasses HTTP layer; direct SQL writes with workspace_id RLS context. | Workers already bypass HTTP; this is fine. |
| 10 | Vocabulary discovery (architecture step 12e) | **Deferred** — needs identity-resolved entity context (Phase 7). | Keeps 5b scope tight. |
| 11 | PII flagging | LLM prompt instructs Gemini to set `is_pii=true` for fields whose values match PII patterns (SSN, Aadhaar, PAN, credit card, DOB, phone, email, MRN). Stored on `proposed_fields.is_pii`. **Wave A: flag only; full encryption + permissions-gated decryption is Wave C.** | Per architecture step 12b. Wave A guarantee: once flagged, the value is not surfaced in chat/citations (Phase 8 enforces; Wave A's chat is Phase 10b). |

**Files:** `migrations/sql/0015_emergent_fields.sql` · `src/kb/extraction/fields.py` + `promotion.py` · `src/kb/domain/fields.py` (repo) · widen `src/kb/workers/tasks.py` · `tests/test_fields_unit.py` + `tests/test_fields_worker.py` + `tests/test_promotion_unit.py`.

---

### 5.12.3 Phase 5c plan — Atomic units + anomaly scoring (G1 ✅ SIGNED OFF · G2 — no-op)

> **Status:** G1 ✅ + G2 — signed off 2026-05-25.

**Scope:** L3 atomic-unit extraction via doc-type plugin registry. Wave A plugins: clauses (contracts), transactions (bank statements), rows (xlsx). Per-type rarity scoring → `rarity_score` on each unit; the rare-clause needle scenario (architecture §10) is unlocked.

**Decisions locked:**

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | Plugin registry | `src/kb/extraction/plugins/{clauses,transactions,rows}.py`. Each exposes `UNIT_TYPE`, `match(file_metadata) -> bool`, `extract(file_id, doc_text, contextual_chunks, raw_pages) -> list[AtomicUnit]`. Dispatcher in `kb.extraction.plugins.__init__.dispatch(file_id, inferred_doc_type)` returns the matching plugin or None. | Open/closed: adding a new plug-in is a single file. |
| 2 | Wave A plugins | **3 plugins**: clauses (matches contracts/legal/agreement), transactions (bank_statement), rows (xlsx via existing parser output). Other doc-types yield no atomic units in Wave A (LIST-of-deferred docs: drawings, land records, invitation cards, handwritten notes — Wave B). | CUAD + SEC + Vendor-xlsx demo corpus all covered. |
| 3 | AtomicUnit storage | `atomic_units(id, file_id, workspace_id, unit_type, parameters jsonb, anchor_chunk_id NULL, rarity_score real NULL, model_id, created_at)`. RLS, REVOKE UPDATE/DELETE. Indexes: `(workspace_id, unit_type)`, `(file_id)`, `(workspace_id, unit_type, rarity_score)`. | parameters jsonb keeps the plugin output flexible per-type; rarity_score nullable so the writes don't gate on centroid availability. |
| 4 | Rarity / anomaly scoring | Per-type, JIT centroid: at end of `extract_atomic_units_file_impl`, read all `atomic_units WHERE workspace_id=X AND unit_type=Y`, compute per-numeric-param z-scores for new units against historical mean/std; per-categorical-param: 1 - frequency. `rarity_score = max(z_or_freq across params)`. | Wave A: no persistent centroid table; computed JIT. Acceptable at demo scale (~100 docs); rebuild centroids weekly via cron in Wave B. |
| 5 | Lifecycle state | `units_extracting` (added at 5a's forward-compat CHECK widening). Worker transitions `fields_extracting → units_extracting → ready`. | One CHECK widening covered all 3 in 5a. |
| 6 | Worker stage | `extract_atomic_units_file_impl(file_id)` chained from `extract_fields_file_impl` end. | Final stage in chain; transitions to `ready` on success. |
| 7 | LLM | Gemini 2.5 Flash per-plugin prompt. Factory `KB_ATOMIC_UNIT_EXTRACTOR ∈ {gemini, anthropic, identity, auto}`. Identity returns `[]` (CI / no-key). | Same factory pattern as prior phases. |
| 8 | Idempotency | At start: DELETE existing units WHERE file_id; then INSERT in same tx. | Re-run = same output. |
| 9 | OpenIE triples (architecture step 13) | **Deferred** — gates on identity resolution (Phase 7). | Out of Phase 5 scope. |
| 10 | Per-plugin parameter schemas | Documented per plugin in the module docstring; parameters jsonb is open-ended so plugins can evolve without migration churn. Example clauses: `{clause_type, parties[], effective_date, term_months, payment_due_days, ...}`. | Phase 6 (schema-driven extraction) will type-check these against a per-doc-type schema. |

**Files:** `migrations/sql/0016_atomic_units.sql` · `src/kb/extraction/plugins/{__init__,clauses,transactions,rows}.py` · `src/kb/extraction/anomaly.py` · `src/kb/domain/atomic_units.py` (repo) · widen `src/kb/workers/tasks.py` · `tests/test_atomic_units_unit.py` + `tests/test_atomic_units_worker.py` + `tests/test_anomaly_unit.py`.

**Phase 5 G5 — what "green" means:**
- `scripts/verify_phase_5.sh` — single end-to-end script. Uploads a contract PDF (CUAD sample or tiny.pdf as proxy), waits through chain to `ready`, asserts: `extracted_mentions` rows exist · `proposed_fields` rows exist · `inferred_schema_fields` row(s) exist · `atomic_units` rows exist with rarity_score · doc_type classified on `files.inferred_doc_type`.
- Cross-phase sweep across all **14 verify scripts** (0/1a/1b/1c/2a/2b/2c/3a/3b/3c/3d/3e/4/5) GREEN.

---

### 5.13 Phase 6 plan — Schema-driven extraction + lineage paths (G1 ✅ SIGNED OFF · G2 — no-op)

> **Status:** G1 ✅ + G2 — signed off 2026-05-25. Single Phase (no sub-split — lineage assignment is a 50-line extension on top of extraction). Per user direction: build end-to-end, decisions chosen by Claude per architecture §5 steps 18 + 18.5 + Design 7 (lineage ltree).
>
> **Architecture mapping:**
> - Step 18 — schema-driven extraction. Gemini structured outputs against active schemas matching the file's `inferred_doc_type` → `extracted_entities` table with per-field citations.
> - Step 18.5 — lineage path assignment. Walk `schema_relationships.kind='contains'` to find parent schema_entity → look up most-recently-created matching extracted_entity in the same file → compute `lineage_path = parent.lineage_path || entity.id` (ltree).
>
> **Worker chain extension:** `units_extracting → entities_extracting → ready`. Phase 6 inserts ONE new state between 5c and ready.
>
> **What "phase 6 complete" means:** uploading a contract PDF through the chain produces `extracted_entities` rows whose `fields` jsonb contains typed values (per the auto-promoted schema from Phase 5b) with `citations` jsonb mapping field-name → contextual_chunk_id, and whose `lineage_path` ltree captures the parent chain.

**Decisions locked:**

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | Auto-chained (not explicit trigger) | YES — runs per-doc, automatically after 5c. | Per architecture step 18 ("runs ONLY on docs whose classified type matches"); explicit trigger is for corpus-wide rebuilds (Phase 3e), not per-doc extraction. |
| 2 | LLM | **Gemini 2.5 Flash** structured outputs (`response_schema`) for typed JSON. 3-impl factory: `KB_ENTITY_EXTRACTOR ∈ {gemini, anthropic, identity, auto}`. Identity returns `[]` (CI / no-key). | Mirrors 5a/5b/5c factory pattern. Gemini's response_schema is the cleanest way to constrain output to the schema_fields types. |
| 3 | Schema source | Active `schemas` in workspace whose **name matches** either `auto:<inferred_doc_type>` (auto-promoted by 5b) OR a user-created schema. Each schema's `schema_entities` provides the entity types. | Both auto-promoted and user-defined schemas drive extraction. Phase 1a-c built the user-creation path; 5b added auto-promotion. |
| 4 | No-matching-schema path | If the file's `inferred_doc_type` has no active schema, extract_schema_entities is a NO-OP — advances `entities_extracting → ready` without writing any extracted_entities. Worker doesn't fail. | Handwritten notes, unknown doc-types, etc. shouldn't block ingestion. They just don't get typed extraction. |
| 5 | Citation granularity | Per-field **chunk_id** citations (not char spans). Worker passes chunk-numbered text (`[CHUNK_0] ... [CHUNK_1] ...`) to the LLM; LLM cites by chunk_index per field; worker resolves chunk_index → `contextual_chunks.id`. | Char-span citations are Wave B (would need offset-aware LLM + extra parsing). Chunk-level citations meet the README "cited responses" promise and are robust to LLM imprecision. |
| 6 | Multi-instance entities | LLM returns a LIST per schema_entity (`{instances: [{...}, ...]}`). Each instance → one `extracted_entities` row. Schema_entity-by-schema_entity LLM calls (parallelized via `asyncio.Semaphore(KB_ENTITY_CONCURRENCY=4)`). | Contracts have many clauses, statements have many transactions. One-LLM-call-per-entity-type lets each call use a tight `response_schema` constrained to that entity's fields. |
| 7 | Lineage assignment | Done in same tx as extraction. For each extracted_entity, look up parent via `schema_relationships.kind='contains' AND to_entity_id = my_schema_entity_id`. Find the most-recently-created `extracted_entities` row in the SAME file whose `schema_entity_id = relationship.from_entity_id`. Set `parent_entity_id` + `lineage_path = parent.lineage_path || entity_id`. If no parent found, `lineage_path = entity_id` (root). | Architecture Design 7 verbatim. Wave A simplification: pick the most-recently-created parent in the same file (most contracts have one root). Wave B / Phase 7 add proper resolution when multiple parents are plausible. |
| 8 | Storage shape | `extracted_entities(id, schema_entity_id FK, file_id FK, workspace_id, parent_entity_id NULL FK self, lineage_path ltree NULL, fields jsonb, citations jsonb, model_id, created_at)`. RLS day-1; REVOKE UPDATE on `kb_app` (immutable; re-extract = DELETE+INSERT in tx). | `fields` = `{field_name: value}`. `citations` = `{field_name: contextual_chunk_id}`. Standard pattern. |
| 9 | Lifecycle widening | Add `entities_extracting`. CHECK widens to include it. Worker chain: 5c's end-state changes from `ready` → `entities_extracting`. 6 transitions to `ready`. Forward-compat: just this one state added (don't know what Phase 7+ will need yet). | Same surgical pattern as 5a's lifecycle change. |
| 10 | Idempotency | At start: DELETE existing extracted_entities WHERE file_id; then INSERT new ones in same tx. | Re-running task = same output. |
| 11 | Re-extract on schema change | **Deferred to Phase 9** admin endpoint. Wave A only runs Phase 6 for NEW uploads; existing files at `ready` stay as-is even if a new field gets auto-promoted later. | Schema-versioning re-extraction is a Phase 9 admin operation (already deferred there per architecture). |
| 12 | ltree extension | Already enabled in `0001_extensions.sql` (Phase 0). | Pre-existing; nothing to add. |
| 13 | Per-entity concurrency | `asyncio.Semaphore(KB_ENTITY_CONCURRENCY=4)` per file (parallel LLM calls across schema_entities). | Free-tier safe; bump for paid tier. |

**Files:**
- `migrations/sql/0017_extracted_entities.sql` — new table + lifecycle CHECK widening
- Widen `0009_chunks.sql` + `0012_raptor.sql` + `0014_mentions.sql` CHECK to include `entities_extracting` (forward-compat, §0.15)
- `src/kb/extraction/entities.py` — `SchemaDrivenExtractor` Protocol + Gemini/Anthropic/Identity impls + factory
- `src/kb/extraction/lineage.py` — `compute_lineage_path()` walks schema_relationships
- `src/kb/domain/extracted_entities.py` — repo
- `src/kb/workers/tasks.py` — change 5c end-state to `entities_extracting` + add `extract_schema_entities_file_impl` + register task
- `tests/test_entities_unit.py` + `tests/test_entities_worker.py` + `tests/test_lineage_unit.py`
- Update `tests/test_atomic_units_worker.py` — assertion `ready` → `entities_extracting` (matches lifecycle change)
- `scripts/verify_phase_6.sh` — end-to-end check (POST tiny.xlsx, wait `ready`, assert extracted_entities + lineage)
- Widen `scripts/verify_phase_5.sh` lifecycle assertions
- Widen 6 prior verify scripts (2a/2b/2c/3a/3b/3c/3d) accept-sets to include `entities_extracting`
- Add `6` to `scripts/verify_sweep.sh` ALL_PHASES + `extracted_entities` to TRUNCATE list

**Phase 6 G5 — what "green" means:**
- `scripts/verify_phase_6.sh` standalone covers: extracted_entities table + RLS + lifecycle CHECK widening + ltree extension + tiny.xlsx E2E to `ready` + lifecycle history contains `entities_extracting → ready` transition + Phase 6 pytest.
- Cross-phase sweep across all **15 verify scripts** (0/1a/1b/1c/2a/2b/2c/3a/3b/3c/3d/3e/4/5/6) GREEN.

---

### 5.14 Phase 7 plan — Identity resolution (G1 ✅ + G2 — + G3 ✅ + G4 ✅ + G5 ✅ SIGNED OFF)

> **Status:** All gates green 2026-05-25. 407/407 pytest in 87s. verify_phase_7.sh 16/16 standalone. Cross-phase sweep across all 16 verify scripts: 16/16 GREEN. Per architecture §5 step 15: deterministic-key match → embedding blocking → LLM-judge → cascading-create. Resolves `extracted_mentions` (Wave A scope) to a canonical `entities` directory. Decision #14 explicitly defers persistent union-find clustering to Wave B (per-file cascade-on-insert is the equivalent for Wave A scope).

**Scope (in / out):**

**In scope:**
- New table `entities` — workspace-scoped canonical entity directory with HNSW-indexed embedding for cross-doc nearest-neighbor.
- New table `mention_to_entity` — every extracted_mention links to exactly one canonical entity.
- New lifecycle state `identity_resolving` between `entities_extracting` and `ready`.
- New worker stage `resolve_identities_file_impl(file_id)` chained after `extract_schema_entities_file_impl`. Pipeline:
  1. **Deterministic** match — lowercased `canonical_name + entity_type` against the workspace's existing entities. O(1) via UNIQUE index.
  2. **Embedding blocking** — embed the mention text via the Phase 3c factory; HNSW nearest-neighbor in `entities` with `entity_type` filter; auto-match if cosine ≥ 0.92.
  3. **LLM judge** — borderline cosine ∈ [0.85, 0.92] → 3-impl factory (`KB_IDENTITY_JUDGE`) returns same/different.
  4. **Create new** — no match → INSERT into `entities` + link.
- Per-call idempotency: DELETE existing `mention_to_entity` rows for this file's mentions, then re-resolve.
- HNSW index on `entities.embedding` (`halfvec_cosine_ops`, `m=16`, `ef_construction=200` — same params as Phase 4).
- Forward-compat: `0009 + 0012 + 0014 + 0017` lifecycle CHECK widened to include `identity_resolving`.
- 6 verify scripts widened (`2a/2b/2c/3a/3b/3c` accept-sets include `identity_resolving`).
- 1 test fix: `test_entities_worker.py` updated to assert end-state `identity_resolving` (was `ready`).
- New: `scripts/verify_phase_7.sh` covering DDL invariants + RLS + HNSW index + end-to-end (xlsx through chain to `ready` with 0 mentions → 0 entities, well-formed) + pytest.

**Out of scope (deferred):**
- `extracted_entities` resolution → Wave B. Typed entities have a fields-jsonb that needs schema-aware similarity; defer until Phase 8 informs the algorithm.
- Cross-workspace identity → Wave C. Permissions model dependent.
- Identity-update audit_log writes → Phase 9.
- Re-resolution endpoint (`POST /entities/resolve`) → Phase 9 admin surface.
- Persistent union-find clustering (Wave B) — Wave A re-runs are per-file so chains form across files via shared canonical_name match.
- ColPali / image-mention resolution — Wave C.

**Decisions locked at G1 (changes require re-opening G1):**

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | Trigger | Auto-chained after `extract_schema_entities_file_impl` (Phase 6). Lifecycle: `entities_extracting → identity_resolving → ready`. | Per-doc trigger consistent with prior chained phases (3a→3b→3c→3d→5a→5b→5c→6). |
| 2 | Wave A scope | Resolve `extracted_mentions` only. `extracted_entities` typed-row resolution deferred. | Mention resolution unblocks Phase 8's mentions_exact channel. Typed-row resolution needs Phase 8 retrieval feedback. |
| 3 | Algorithm | 4 stages: (a) deterministic exact lowercased name+type; (b) embedding nearest-neighbor in entities table (cosine ≥ 0.92 = auto-match); (c) LLM-judge on borderline cosine ∈ [0.85, 0.92]; (d) else create new entity. | Industry-standard ER cascade (deterministic-first to avoid LLM costs on easy cases; embeddings handle alias variants; LLM only for borderline). |
| 4 | Storage — `entities` | `entities(id uuid PK, workspace_id uuid, canonical_name text, entity_type text, embedding halfvec(3072) NULL, mention_count int DEFAULT 1, created_at, updated_at)`. RLS day-1. UPDATEable (mention_count + updated_at refresh; name occasionally if Phase 9 admin renames). | Workspace-scoped canonical directory. `mention_count` precomputed for ranking. Embedding nullable so deterministic-only path doesn't gate on embedder availability. |
| 5 | Storage — `mention_to_entity` | `mention_to_entity(mention_id uuid PK FK→extracted_mentions ON DELETE CASCADE, entity_id uuid FK→entities ON DELETE CASCADE, workspace_id uuid, confidence real, resolved_method text CHECK IN ('deterministic','embedding','llm_judge','identity'), created_at)`. RLS. Immutable (REVOKE UPDATE). | One mention → one entity (PK on mention_id enforces). CASCADE on mention deletion keeps tables coherent. Re-run is DELETE+INSERT (decision #8). |
| 6 | LLM judge | 3-impl factory `KB_IDENTITY_JUDGE ∈ {gemini, anthropic, identity, auto}`. `auto` probes Gemini → Anthropic → Identity. Identity (Noop) always returns False (create new). | Same factory pattern as 3b-bis/3d/5a/5b/5c/6. Identity-False fallback is the safe choice: false-positives in identity resolution corrupt the cross-doc graph; false-negatives just create duplicate entities that can be merged later. |
| 7 | Embedding model | Reuses Phase 3c's `make_embedder()`. Mention text → 3072-d halfvec. Same vector space as chunks/raptor. | One embedding model end-to-end keeps Phase 4 HNSW + Phase 8 dense channels coherent. |
| 8 | Idempotency | At start: DELETE existing `mention_to_entity` WHERE mention_id IN (file's mentions); recompute resolutions in same tx. `entities` table is NOT deleted (cross-file canonical entities persist). | Re-running task = idempotent for this file's links; existing entities from other files preserved. Matches 5a/5c idempotency pattern. |
| 9 | Lifecycle state | New: `identity_resolving`. CHECK widened in `0018_entities.sql`. Forward-compat: included in `0009/0012/0014/0017` widening per §0.15 convention. | Single new state. Worker chain extends by one stage. |
| 10 | HNSW index on entities | Yes — `entities.embedding` gets HNSW with `halfvec_cosine_ops`, `m=16`, `ef_construction=200`. `WHERE embedding IS NOT NULL` partial index since embedding is nullable. | Step (b) needs nearest-neighbor at workspace-scale. Same params as Phase 4's chunk + raptor HNSW. |
| 11 | Deterministic match collation | Lowercased name comparison + exact type match. UNIQUE index on `(workspace_id, lower(canonical_name), entity_type)` enforces. | "ACME Corp" and "acme corp" should collapse to one entity. Case-folded match is the cheapest way; full Unicode normalization (NFC) deferred to Wave B if international docs surface. |
| 12 | Concurrency | Sequential per file (no semaphore). Embedder batch-call already parallelizes embedding generation; per-mention DB lookups are cheap (HNSW). | Per-file mention counts are O(100); sequential resolution is sub-second. Workspace-wide parallel resolution = Wave B. |
| 13 | Audit log | Single lifecycle event `identities_resolved` with payload `{mention_count, deterministic, embedding, llm_judge, new}` counts. Per-mention audit deferred to Phase 9. | Consistent with prior phases' summary-payload pattern. |
| 14 | Union-find clustering | **Wave A SKIP — equivalent outcome via cascading match BEFORE create.** The architecture §5 step 15 calls for "union-find clusters". Wave A's per-file trigger doesn't run global union-find; instead, each new mention cascades deterministic → embedding → LLM-judge and only creates a new entity if all match-stages fail. This collapses duplicates AT INSERT TIME for the per-file pipeline. **What it doesn't catch**: if two entities were created independently in early files (before either's embedding was strong enough to attract the other), they stay split. Wave B adds a workspace-wide periodic `re_resolve_workspace_impl` cron that runs proper union-find across `entities` rows + merges where LLM-judge agrees. Documented here so the Wave A simplification isn't a hidden skip. | Per-file trigger has different scope from workspace-wide re-cluster. Cheap-cascade-on-insert is the equivalent for the per-file case. Persistent union-find is its own complexity (requires Tarjan/Kruskal-style data structure or recursive CTE on entity pairs) that deserves its own G1 in Wave B. |

**Files (planned at G3+G4):**
- `migrations/sql/0018_entities.sql` — entities + mention_to_entity + lifecycle widening + HNSW + UNIQUE indexes
- `migrations/sql/0009_chunks.sql` + `0012_raptor.sql` + `0014_mentions.sql` + `0017_extracted_entities.sql` — forward-compat widen CHECK to include `identity_resolving`
- `src/kb/identity/__init__.py` — package docstring
- `src/kb/identity/judge.py` — LLM judge factory (Gemini/Anthropic/Noop + parse_judgment helper)
- `src/kb/identity/resolve.py` — algorithm constants (`EMBEDDING_HIGH_THRESHOLD=0.92`, `EMBEDDING_LOW_THRESHOLD=0.85`) + `ResolutionResult` dataclass
- `src/kb/domain/entities.py` — repo (find_entity_deterministic, find_entity_by_embedding, insert_entity, increment_mention_count, delete_mention_to_entity_for_file, insert_mention_to_entity, read_mentions_for_file, count_*)
- `src/kb/workers/tasks.py` — chain modification (Phase 6 end-state `ready` → `identity_resolving`) + `resolve_identities_file_impl` + Procrastinate task registration
- `tests/specs/phase_7.md` — test spec
- `tests/test_identity_unit.py` — pure-function tests (thresholds + JSON parser + Noop + factory matrix + Mention-embedder integration)
- `tests/test_identity_worker.py` — testcontainer integration (4 algorithm stages + idempotency + cross-doc reuse + state guards + empty-mentions edge case)
- `tests/test_entities_worker.py` — updated assertion `ready` → `identity_resolving`
- 6 prior verify scripts (2a/2b/2c/3a/3b/3c) — accept-sets widened to include `identity_resolving`
- `scripts/verify_phase_7.sh` — standalone end-to-end (compose smoke + DDL + RLS + HNSW + xlsx E2E to `ready` + lifecycle history check + Phase-7 pytest)
- `scripts/verify_sweep.sh` — add `7` to ALL_PHASES + `entities`/`mention_to_entity` to inter-phase TRUNCATE

**Phase 7 G5 — what "green" means:**
- `scripts/verify_phase_7.sh` standalone: 12-15 checks GREEN.
- Full pytest: prior 370 still GREEN + new ≥20 Phase 7 tests GREEN.
- Cross-phase sweep across all 16 verify scripts (`0/1a/1b/1c/2a/2b/2c/3a/3b/3c/3d/3e/4/5/6/7`): 16/16 GREEN.

**Pre-G3 consistency review checklist:**
- [ ] Architecture §5 step 15 traceability.
- [ ] Forward-compat: 0009 + 0012 + 0014 + 0017 widened.
- [ ] No api_contracts.md delta (no HTTP surface this phase).
- [ ] RLS invariant: 2 new workspace-scoped tables (entities + mention_to_entity).
- [ ] All 4 algorithm stages covered by at least one test.
- [ ] Identity-fallback path tested (CI without LLM key).
- [ ] No leak into Phase 8 territory (no /search, no rerank, no CRAG).
- [ ] No leak into Phase 9 (no admin re-resolve endpoint, no audit_log writes).
- [ ] `audit_log` writes deferred to Phase 9.

---

### 5.15 Phase 8 parent — Query layer (split into 8a-f) (G1 ✅ split-locked)

> **Status:** Sub-phase split locked per architecture §12 "the big one — split into sub-phases at G1". Each sub-phase 8a→8f is its own G1→G5 cycle (matching Phase 5's a/b/c split discipline). Each sub-phase has its own decisions table + scope + tests-spec + verify + PR.

**Sub-phase split + dependencies:**

| Sub | Scope | Depends on | Plan |
|---|---|---|---|
| **8a** | Query rewriting — Step-Back + HyDE + Query2Doc per architecture §6 step 4 | Phase 3c embedder, no DB writes | §5.15.1 |
| **8b** | 6-channel parallel retrieval — BM25 chunks · BM25 raptor · dense chunks · dense raptor · mentions_exact · atomic_units_rarity. RRF fusion (k=60) per Cormack 2009. | Phase 4 (indexes), Phase 5a/5c (mentions + units), Phase 7 (entities for mentions_exact resolution) | §5.15.2 |
| **8c** | Rerank — Cohere Rerank 3.5 default; `mxbai-rerank-large-v2` local fallback; identity passthrough. Factory `KB_RERANKER`. | 8b output schema | §5.15.3 |
| **8d** | CRAG gate — Gemini judges relevance of top-K post-rerank; refuses below threshold. | 8c output, Phase 3b LLM factory pattern | §5.15.4 |
| **8e** | Astute generation — Gemini answer with chunk + entity + atomic-unit citations + refusal mode. | 8d output | §5.15.5 |
| **8f** | HTTP surface — `POST /search` + `POST /chat` + `query_log` audit table | 8a→8e all complete | §5.15.6 |

**Skipped vs architecture full vision (deferred to Wave B):**
- 4 of architecture's 10 retrieval channels: HippoRAG PPR (no graph yet), ColPali (Wave C), doc-chain (Design 3 not built), anomaly-filter separate from atomic-unit rarity.
- Conversational-context resolver (Design 8) — first-turn-only in Wave A; multi-turn lands at Phase 10b polish.
- Per-query rewriting hyperparameter overrides — env defaults only in Wave A.
- Streaming generation — Wave A returns full answer; SSE streaming lives at Phase 9 + 10b.

---

### 5.15.1 Phase 8a plan — Query rewriting (Step-Back + HyDE + Query2Doc) (G1 🟡 DRAFTED)

> **Status:** G1 plan drafted 2026-05-25. Per architecture §6 step 4 (3 rewriting strategies). Standalone module; no DB writes; pure LLM-call layer. Input: user query string. Output: 4 query variants (original + step_back + hyde + query2doc) for parallel-channel input.

**Scope (in / out):**

**In scope:**
- New `src/kb/query/` package (top-level, sibling to `kb/extraction/`, `kb/identity/`).
- `src/kb/query/rewriter.py`: `Rewrites` pydantic model · `QueryRewriter` Protocol · 3-impl factory (`GeminiQueryRewriter` + `AnthropicQueryRewriter` + `IdentityQueryRewriter`) · `make_query_rewriter()` reads `KB_QUERY_LLM ∈ {gemini, anthropic, identity, auto}`.
- Single LLM call returns all 3 rewrites as JSON `{step_back, hyde, query2doc}`. Identity fallback returns original query for all 3 (no expansion — degraded recall but functional).
- JSON parser tolerant of fences + missing keys (returns original on parse failure).
- Out-of-the-box `auto` selector probes Gemini → Anthropic → Identity (matches 3b-bis/3d/5a/5b/5c/6/7 convention).

**Out of scope (deferred):**
- Conversational context (multi-turn rewriting per Design 8) — Phase 10b chat polish.
- DSPy-optimized prompts — Wave B Phase B3.
- Per-channel rewriting (different rewrites for BM25 vs dense) — Wave B.
- Query-classification routing (which planner mode `D` vs `H` vs `Q` per architecture §6) — Wave B Phase 8b refactor.
- HTTP surface — `/search` + `/chat` land in 8f; 8a is module-only.
- Audit logging of rewrites — 8f writes `query_log.rewrites` jsonb.
- Caching rewrites (a query like "hello" gets the same step_back forever) — Wave B.

**Decisions locked at G1:**

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | 3 rewriting strategies bundled in ONE LLM call | Yes — system prompt asks for all 3 in a single JSON. | Cheaper than 3 separate calls; Gemini's response_mime_type=application/json constrains output. Per architecture §6 step 4. |
| 2 | LLM | **Gemini 2.5 Flash** default. 3-impl factory `KB_QUERY_LLM`. Identity returns original-text for all 3. | Same factory pattern as 3b-bis/3d/5a/5b/5c/6/7. Single-key story holds. |
| 3 | Output schema | `{step_back: str, hyde: str, query2doc: str}` strict. Parser fallback returns the original query in all 3 slots if JSON is malformed. | Tolerant fail-soft — bad LLM output degrades to no-expansion rather than blocking the query. |
| 4 | Tokens budget | `max_output_tokens=600` (~200 tokens × 3 rewrites). | HyDE paragraphs are the longest; 200 tokens each is plenty for the synthetic-doc pattern. |
| 5 | Prompt format | Verbatim spec in module — single system prompt explains all 3 strategies; user message is just `"Query: <text>"`. | Self-contained for review. No external prompt-config file (Wave B adds Hydra). |
| 6 | Thinking budget | `thinking_budget=0` (Gemini). | Rewriting is a short transform task, not multi-step reasoning. Mirrors §5.8 #9. |
| 7 | Error handling | Any LLM exception → return `Rewrites(original=q, step_back=q, hyde=q, query2doc=q)`. Worker doesn't fail on rewriting failure. | Rewriting is optional quality boost; query still proceeds with original-only on failure. |
| 8 | Model override | `KB_QUERY_MODEL` env (defaults `gemini-2.5-flash` / `claude-opus-4-7`). | Matches 3b-bis/3d/5a/5b/5c/6 KB_*_MODEL env convention. |
| 9 | Variant ordering | Returned `Rewrites` model exposes `.original`, `.step_back`, `.hyde`, `.query2doc` as named fields. Phase 8b consumes all 4. | Named-attribute access easier for tests + downstream than positional list. |
| 10 | No prompt caching | No `cache_control` block. | Rewriting prompts are tiny; caching overhead > savings for this stage. (Phase 3b uses caching for doc-context which is huge.) |

**Files (G3/G4):**
- `src/kb/query/__init__.py` — package docstring (mentions all 8 sub-modules even though only `rewriter` ships in 8a)
- `src/kb/query/rewriter.py` — Rewrites model + 3-impl factory + `make_query_rewriter()`
- `tests/specs/phase_8a.md` — test spec
- `tests/test_query_rewriter_unit.py` — pure-function tests (factory matrix + parser edge cases + Identity fallback + mocked Gemini path)

**Phase 8a G5 — what "green" means:**
- `scripts/verify_phase_8a.sh` standalone: 6-8 checks (worker container imports `kb.query.rewriter` + no leak into `kb.api/*` + Identity returns original for all 3 + Phase 8a pytest).
- Full pytest: prior 407 still GREEN + new ≥10 Phase 8a tests GREEN.
- Cross-phase sweep across all 17 verify scripts (0..7 + 8a): 17/17 GREEN.

**Pre-G3 consistency review checklist:**
- [ ] Architecture §6 step 4 traceability (Step-Back + HyDE + Query2Doc).
- [ ] No DB migration (pure module).
- [ ] No HTTP surface (8f owns endpoints).
- [ ] No api_contracts.md delta.
- [ ] No lifecycle CHECK widening (no worker stage in 8a).
- [ ] Identity-fallback path tested.
- [ ] Factory matrix covers 5 cases.
- [ ] No leak into 8b (no channel logic).
- [ ] No leak into 8c (no rerank).

**Sub-phase split:**

| Sub | Scope | Files |
|---|---|---|
| **8a** | Query rewriting: Step-Back · HyDE · Query2Doc per architecture §6 step 4. Single LLM call returns all 3 rewrites. | `kb/query/rewriter.py` |
| **8b** | Parallel retrieval — 6 channels for Wave A (BM25 chunks · BM25 raptor · dense chunks · dense raptor · mentions exact · atomic_units rarity). RRF fusion (k=60). Skips: HippoRAG PPR (no graph yet) · ColPali (Wave C) · doc-chain (Design 3 not built) · anomaly-filter (Wave B). | `kb/query/channels.py` + `kb/query/rrf.py` |
| **8c** | Rerank — Cohere Rerank 3.5 default; `mxbai-rerank-large-v2` local fallback; identity passthrough if both missing. 3-impl factory `KB_RERANKER`. | `kb/query/rerank.py` |
| **8d** | CRAG gate — Gemini judges relevance of top-K post-rerank; if low → fallback path (just return BM25 top-K with low-confidence flag). | `kb/query/crag.py` |
| **8e** | Astute generation — Gemini answer with chunk + entity + atomic-unit citations. Refusal mode when CRAG-gate fails. | `kb/query/generate.py` |
| **8f** | HTTP surface — `POST /search` + `POST /chat` (non-streaming for Wave A; SSE streaming added at Phase 9) + `kb/api/query.py` router. | `kb/api/query.py` + `api/main.py` |

**Decisions locked across all of Phase 8:**

| # | Decision | Choice |
|---|---|---|
| 1 | LLM for rewriting + CRAG + generation | Gemini 2.5 Flash. Factory `KB_QUERY_LLM ∈ {gemini, anthropic, identity, auto}`. |
| 2 | Top-K per channel (pre-fusion) | 20 |
| 3 | Top-K post-fusion (pre-rerank) | 30 |
| 4 | Top-K post-rerank (returned to chat) | 10 |
| 5 | CRAG threshold | 0.5 average relevance score across top-10 |
| 6 | Refusal copy | "I couldn't find sufficient evidence to answer." + raw top-3 chunks shown |
| 7 | Conversational context | Not yet — first turn only in Wave A. Design 8 multi-turn lands in Phase 10b polish. |

**Files:**
- `src/kb/query/{__init__,rewriter,channels,rrf,rerank,crag,generate}.py`
- `src/kb/api/query.py` (`POST /search` + `POST /chat`)
- `migrations/sql/0019_query_audit.sql` — `query_log` table (one row per query for replayability per architecture §14)
- Tests: `test_query_*.py`
- `scripts/verify_phase_8.sh`

---

### 5.15.2 Phase 8b plan — 6-channel parallel retrieval + RRF fusion (G1 🟡 DRAFTED)

> **Status:** G1 plan drafted 2026-05-25. Per architecture §6 step 7-8 (parallel channels + RRF). 6 of the 10 channels in architecture's full vision land in Wave A; 4 deferred to Wave B/C (see scope-out below).

**Scope (in / out):**

**In scope:**
- `src/kb/query/channels.py`: 6 async channel functions, each takes `(conn, workspace_id, query|query_vec, limit)` and returns `list[Hit]`:
  1. `bm25_chunks_channel` — pg_search `@@@` over `contextual_chunks.contextual_text` (uses Phase 4 BM25 index).
  2. `bm25_raptor_channel` — pg_search `@@@` over `raptor_nodes.text` (uses Phase 4 BM25 index).
  3. `dense_chunks_channel` — pgvector `<=>` cosine over `chunk_embeddings.embedding` (uses Phase 4 HNSW).
  4. `dense_raptor_channel` — pgvector `<=>` over `raptor_nodes.embedding`.
  5. `mentions_exact_channel` — case-insensitive `mention_text LIKE` over `extracted_mentions` (Phase 5a output); links back to the contextual_chunk that mentioned it.
  6. `atomic_units_rarity_channel` — high-rarity `atomic_units.rarity_score` from Phase 5c; query-keyword filter for unit_type when "clause"/"transaction"/"row" appears in query.
- `src/kb/query/rrf.py`: `Hit` dataclass + `rrf_fuse(channels, k=60)` implementing Cormack-Clarke-Buettcher 2009. Dedupe by `(id, kind)`; sum reciprocal-rank scores across channels.
- `run_all_channels(conn, workspace_id, query, query_vec, limit)` async coordinator that fans out to 6 channels in parallel via `asyncio.gather(return_exceptions=True)` — channel failure ⇒ empty list for that channel (doesn't fail the query).

**Out of scope (deferred):**
- HippoRAG PPR channel — Wave B Phase 14 (needs graph index first).
- ColPali visual channel — Wave C.
- Doc-chain channel — Wave B (Design 3 not built).
- Anomaly-filter channel (separate from atomic-unit rarity) — Wave B.
- Identity-resolved entity expansion at query time — Wave B (Phase 7 entities exist; the query-time join is its own complexity).
- HNSW query-time `ef_search` per-query tuning — env-only in Wave A.
- Score normalization across channels — RRF uses ranks, not raw scores; explicit normalization is Wave B.
- Result diversity (MMR) — Wave B Phase B3.
- Multi-vector retrieval (ColBERT-style) — Wave C.
- HTTP surface — Phase 8f.

**Decisions locked at G1:**

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | 6 Wave A channels | bm25_chunks · bm25_raptor · dense_chunks · dense_raptor · mentions_exact · atomic_units_rarity | All 4 channels needing artifacts that exist post-Phase-7 (chunks via 3a, raptor via 3d/3e, mentions via 5a + 7, atomic units via 5c). |
| 2 | Top-K per channel | **20** | Cormack et al. RRF paper finds top-20 per channel is plenty for k=60; below that, recall drops; above 20, fusion noise increases. |
| 3 | RRF k constant | **60** | Paper default; pgvector + pg_search ranks both start at 1 so k=60 keeps the reciprocal weights well-distributed. |
| 4 | Parallel execution | `asyncio.gather(*tasks, return_exceptions=True)` — failed channels degrade to `[]` and don't abort the query. | Resilience: a stale BM25 index shouldn't kill a dense query. |
| 5 | Hit deduplication | by `(id, kind)` tuple — RRF sums scores per unique item. Same `contextual_chunk_id` from BM25 and Dense channels collapses to one Hit with summed RRF score. | Standard RRF. Same item appearing in multiple channels = stronger signal. |
| 6 | Hit shape | `Hit(id, kind, score, snippet, metadata: dict)` where `kind ∈ {'chunk', 'raptor_node', 'atomic_unit'}` and metadata carries `file_id`, `level`, `scope`, `channel`, optional `matched_mention` / `unit_type`. | Phase 8e (Astute generation) consumes `metadata` for citation envelopes. |
| 7 | mentions_exact channel: how to surface | When mention text matches, return the `contextual_chunk_id` (Hit.kind='chunk') that contains the mention. Score = 1.0 (deterministic match), metadata records `matched_mention` + `matched_type` for explainability. | Phase 8e's citation envelope wants the chunk context, not the bare mention. Matches the "mention lookup" channel intent in architecture §6 step 7. |
| 8 | atomic_units_rarity: query-keyword routing | If query mentions "clause" / "transaction" / "row", filter `unit_type` accordingly. Else: top across all unit_types by rarity. | Keyword-based intent routing is a Wave A heuristic; Wave B's query planner (architecture §6 step 5) does proper intent classification. |
| 9 | Query-vector embedding | Use Phase 3c `make_embedder()` for the original variant. Other 3 rewrites (step_back, hyde, query2doc) get separate embeddings (single batch call). All 4 variants run through all 6 channels (so up to 6*4=24 parallel queries per user query). | Each rewrite has a different semantic profile; embedding them separately is the standard HyDE recipe. Batch call keeps embedding cost low. |
| 10 | Channel-level pre-filtering | Workspace-scoped — every channel has `WHERE workspace_id = %s` filter even though kb_app RLS would enforce it. Belt-and-braces. | Matches §0.15 RLS-day-1 convention. |
| 11 | Snippet truncation | `snippet[:500]` for downstream display. | Phase 8e + UI render constraint. |
| 12 | Error swallowing scope | Per-channel: SQL exceptions caught + logged; channel returns `[]`. **NOT** swallowed at the orchestrator (`asyncio.gather` returns exceptions for inspection) | Single channel failure → degraded recall, not query failure. Total failure (e.g., DB down) propagates. |

**Files (G3/G4):**
- `src/kb/query/rrf.py` — Hit dataclass + rrf_fuse + DEFAULT_K=60
- `src/kb/query/channels.py` — 6 channel functions + run_all_channels coordinator
- `tests/specs/phase_8b.md` — test spec
- `tests/test_query_rrf_unit.py` — pure-function RRF tests (~8)
- `tests/test_query_channels_unit.py` — channel SQL tests against testcontainers (~8)
- `scripts/verify_phase_8b.sh` — end-to-end with seeded chunks

**Phase 8b G5 — what "green" means:**
- `scripts/verify_phase_8b.sh` standalone — 8-10 checks (worker imports, seed 5 docs through full Phase 5/7 chain, run all 6 channels against query "test", verify each channel returns hits, RRF fusion deduplicates).
- Full pytest: prior 421 still GREEN + new ≥16 Phase 8b tests GREEN.
- Cross-phase sweep across all 18 verify scripts (0..7 + 8a + 8b): 18/18 GREEN.

**Pre-G3 consistency review checklist:**
- [ ] Architecture §6 step 7 + 8 traceability (parallel channels + RRF).
- [ ] No DB migration (Phase 8b reads existing tables).
- [ ] No HTTP surface (8f).
- [ ] No new lifecycle states.
- [ ] No leak into 8c (no rerank).
- [ ] No leak into 8d/8e (no CRAG, no generation).

---

### 5.15.3 Phase 8c plan — Reranker (Cohere + mxbai + Identity) (G1 🟡 DRAFTED)

> **Status:** G1 plan drafted 2026-05-25. Per architecture line 197 + 904 (Cohere Rerank 3.5 default, mxbai-rerank-large-v2 fallback). Cross-encoder rerank of post-RRF top-30 → top-10 returned to chat.

**Scope (in / out):**

**In scope:**
- `src/kb/query/rerank.py`: `Reranker` Protocol · 3-impl factory (`CohereReranker` + `MxBaiReranker` + `IdentityReranker`) · `make_reranker()` reads `KB_RERANKER ∈ {cohere, mxbai, identity, auto}`.
- `CohereReranker` uses `cohere.AsyncClientV2.rerank()` with `rerank-english-v3.0` model (configurable via `KB_COHERE_RERANK_MODEL`). Requires `KB_COHERE_API_KEY`.
- `MxBaiReranker` uses `sentence-transformers.CrossEncoder("mixedbread-ai/mxbai-rerank-large-v2")` — local CPU/GPU. Lazy-loaded singleton at class level.
- `IdentityReranker` is passthrough — returns top-K of input order unchanged (no LLM call).
- `auto` selector probes `KB_COHERE_API_KEY` → falls back to Identity (mxbai is heavy local dep; explicit opt-in via `KB_RERANKER=mxbai`).
- Cohere/mxbai errors → fall back to Identity-style passthrough (don't fail the query).
- Output schema: reranker returns at most `top_k` Hits with updated `score` (reranker's relevance score) + metadata gains `rerank: 'cohere' | 'mxbai'`.
- `pyproject.toml` adds `cohere>=5.0` as an optional dep (extras `[rerank]`); won't break baseline install.

**Out of scope (deferred):**
- Per-query reranker tuning (top_n, max_chunks_per_doc, return_documents) — defaults only in Wave A.
- Multi-language rerankers (rerank-multilingual-v3.0) — Wave B.
- BGE / GTE rerankers — Wave B.
- HTTP surface (8f).
- Reranker latency profiling / SLO — Phase 12.
- Caching reranker calls — Wave B.

**Decisions locked at G1:**

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | Default reranker | **Cohere Rerank 3.5** (model `rerank-english-v3.0`). Architecture line 197 + 904. | Best-in-class cross-encoder per architecture. Hosted API → no local GPU req. |
| 2 | Fallback chain | `auto`: Cohere (if `KB_COHERE_API_KEY`) → Identity (no rerank). mxbai is opt-in via `KB_RERANKER=mxbai`. | mxbai requires `sentence-transformers` (heavy ~500MB dep); don't make it the auto-fallback. |
| 3 | mxbai model | `mixedbread-ai/mxbai-rerank-large-v2` per architecture line 904. Lazy-loaded class-level singleton. | Architecture-cited local fallback. Loading the model once per process avoids latency on first rerank. |
| 4 | Factory selector | `KB_RERANKER ∈ {cohere, mxbai, identity, auto}`. Same pattern as 3b-bis/3d/5a/5b/5c/6/7/8a. | Consistency. |
| 5 | Cohere model override | `KB_COHERE_RERANK_MODEL` env, default `rerank-english-v3.0`. | Matches existing `KB_*_MODEL` env convention (3b-bis, 5b, etc.). |
| 6 | Top_K return | Reranker takes `top_k` parameter (configured by caller — Phase 8f orchestrator passes 10). | Phase 8f decides the user-facing K; rerank is a pure transform. |
| 7 | Error handling | Cohere API error / mxbai import failure / model load failure → fall back to passthrough (`hits[:top_k]`). | Reranking is quality boost; query should still complete. |
| 8 | Document field | Cohere/mxbai see `Hit.snippet` (already truncated to 500 chars per 8b decision #11). | Phase 8b ensured snippets are clean + bounded. |
| 9 | Score replacement | Reranked Hit's `score` = reranker's relevance_score (0..1 for Cohere; cross-encoder logits for mxbai). Metadata `rerank` key indicates which engine. | Downstream (Phase 8d CRAG, 8e generate) consume score for confidence assessment. |
| 10 | Empty input | Empty `hits` → return `[]` immediately (no API call). | Avoid wasted Cohere API call on empty fusion result. |
| 11 | Cohere SDK choice | `cohere.AsyncClientV2` (v2 API). Optional dep `cohere>=5.0`; if not installed, falls back to passthrough. | Cohere v5+ SDK is the current stable; AsyncClientV2 is the async path. |
| 12 | mxbai loaded lazily at class level | `MxBaiReranker._model = None` class attribute; first rerank call initializes via `CrossEncoder(...)`. | Avoids the ~500MB model load at import time. |

**Files (G3/G4):**
- `src/kb/query/rerank.py` — 3-impl factory + Reranker Protocol
- `pyproject.toml` — `cohere>=5.0` as optional dep in `[project.optional-dependencies] rerank`
- `tests/specs/phase_8c.md` — test spec
- `tests/test_query_rerank_unit.py` — pure-function + mocked Cohere/mxbai tests (~12)
- `scripts/verify_phase_8c.sh` — module-level (no E2E full-stack)

**Phase 8c G5 — what "green" means:**
- `scripts/verify_phase_8c.sh` standalone — 6-8 checks (worker imports, Identity passthrough, factory matrix, mocked Cohere path returns reranked output).
- Full pytest: prior 441 still GREEN + new ≥12 Phase 8c tests GREEN.
- Cross-phase sweep across all 19 verify scripts: 19/19 GREEN.

---

### 5.15.4 Phase 8d plan — CRAG (Corrective RAG) relevance gate (G1 ✅ → G5 ✅)

> **Status:** All gates green 2026-05-25. 472/472 pytest (16 new). Branch `phase-8d/crag`. Per CRAG paper (Yan et al. 2024 "Corrective Retrieval Augmented Generation"). Cheap LLM-judge of top-K rerank output — passes a confidence score (0-1); orchestrator (Phase 8f) refuses with "insufficient evidence" message when below threshold.

**Scope (in / out):**

**In scope:**
- `src/kb/query/crag.py`: `CragGate` Protocol · 2-impl factory (`GeminiCragGate` + `IdentityCragGate`) · `make_crag_gate()` reads `KB_QUERY_LLM` (reuses same env var as 8a since both use the same LLM family).
- LLM prompt: "Given query + 3 snippets, return JSON `{avg_relevance: 0.0-1.0}` where 1.0 = all snippets directly answer, 0.0 = none relevant."
- `IdentityCragGate` always returns 1.0 (passes — degrades quality but doesn't block, mirrors decision #7 elsewhere).
- `CRAG_THRESHOLD = 0.5` module constant; orchestrator (Phase 8f) reads.
- Empty hits → returns 0.0 (clear refusal signal).
- Sees top-3 hits' snippets only (cost cap; CRAG is a cheap signal).

**Out of scope (deferred):**
- Per-snippet relevance (vs avg) — Wave B.
- Corrective re-retrieval (CRAG paper's full algorithm — refusal + new query) — Wave B; Wave A's CRAG just gates the answer.
- Anthropic CRAG impl — Wave B (LLM choice for CRAG follows Phase 8a's KB_QUERY_LLM).
- HTTP surface (8f).

**Decisions locked at G1:**

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | LLM | Gemini default; Identity fallback. Reuses `KB_QUERY_LLM` env (same family as 8a). | Same LLM family across 8a/8d/8e keeps cost story unified; one switch flips Gemini→Anthropic→Identity for all three. |
| 2 | Threshold | `CRAG_THRESHOLD = 0.5` (module constant). | Below 0.5 = "more bad than good" hits; refuse. Per literature this is the median heuristic; tunable in Wave B. |
| 3 | Snippet count fed to LLM | Top-3 only. | Cost cap. Empirically the top-3 cover >85% of the relevance signal. |
| 4 | Output schema | `{avg_relevance: 0.0-1.0}` single float. Parser clamps to [0, 1] + fallback to 1.0 on parse error. | Simplest possible — orchestrator just compares to threshold. |
| 5 | Empty input | Returns 0.0 (clear refusal). | No evidence = guaranteed refusal. |
| 6 | Identity fallback | Always returns 1.0 (always passes — degrades quality but never blocks). | Same rationale as Phase 8a #7 — CRAG is a quality boost, not a blocker; no-key path must still answer. |
| 7 | Error → fail-safe pass | Any LLM exception → returns 1.0 (passes). | Don't refuse on infra failure. |
| 8 | Thinking budget | 0 (Gemini). | Cheap judgment task. |
| 9 | Token budget | `max_output_tokens=100` (single float in a tiny JSON). | CRAG output is one number. |
| 10 | Anthropic impl | Deferred to Wave B — IdentityCragGate covers the `KB_QUERY_LLM=anthropic` path for now (passes 1.0). Documented decision. | Don't pretend to support Anthropic for CRAG without testing. Identity-fallback is the honest answer. |

**Files (G3/G4):**
- `src/kb/query/crag.py` — Protocol + 2-impl factory + CRAG_THRESHOLD constant
- `tests/specs/phase_8d.md` — test spec
- `tests/test_query_crag_unit.py` — pure-function + mocked Gemini tests (~10)
- `scripts/verify_phase_8d.sh` — module-level checks (mirrors 8a/8c structure)

**Phase 8d G5 — what "green" means:**
- `scripts/verify_phase_8d.sh` standalone — 6-8 checks.
- Full pytest: prior 456 still GREEN + new ≥10 Phase 8d tests GREEN.
- Cross-phase sweep across all 20 verify scripts: 20/20 GREEN.

---

### 5.15.5 Phase 8e plan — Astute generation (G1 ✅ → G5 ✅)

> **Status:** All gates green 2026-05-25. 491/491 pytest (19 new). Branch `phase-8e/generate`. Per architecture §6 step 8 + line 1169 + Astute RAG paper (Wang et al. 2024 "Astute RAG: Overcoming Imperfect Retrieval Augmentation and Knowledge Conflicts for Large Language Models" — arXiv 2410.07176). Single defensive-prompt Gemini call over reranked top-10 hits → structured `{answer, citations[], refused, refusal_reason}` JSON. Orchestrator (Phase 8f) invokes after CRAG (8d) confirms confidence ≥ threshold; calls `generate()` with `refusal=True` directly when CRAG-below-threshold so the user still gets a clean "no evidence" envelope rather than a 4xx.

**Scope (in / out):**

**In scope:**
- `src/kb/query/generate.py`: `GenerationResult` Pydantic model (answer · citations · refused · refusal_reason · model_id) · `Generator` Protocol · 2-impl factory (`GeminiGenerator` + `IdentityGenerator`) · `make_generator()` reads `KB_QUERY_LLM` (shared with 8a/8d).
- Astute-defensive system prompt: (a) extract internal knowledge first · (b) compare with retrieved · (c) cite every claim by `[hit_id]` · (d) refuse with "insufficient evidence" if hits don't support the answer.
- Citation envelope (Wave A minimal): `{hit_id, kind, file_id, snippet_preview, score}` — derived from `Hit.id` / `Hit.kind` / `Hit.metadata`. Richer envelope (label, authority, doc_status, chain_id, modality, lineage_path) deferred to Wave B.
- Output: structured JSON via Gemini `response_mime_type=application/json` + Pydantic validation. Parser fail-safes: bad JSON → refusal envelope; missing fields → coerced defaults.
- `IdentityGenerator`: deterministic stub — returns a templated "echo" answer (`"[identity-stub] {query} (hits: N)"`) with citations synthesized from the first 3 hits. Lets the no-key / CI path produce a valid `GenerationResult` so downstream (8f) tests still run end-to-end without an LLM.
- Explicit refusal path: when called with `force_refuse=True` (orchestrator passes this when CRAG < threshold), skip LLM entirely, return `GenerationResult(refused=True, refusal_reason="insufficient_evidence", citations=[])`.
- Empty hits: same as `force_refuse=True` — return refusal envelope, don't call LLM.

**Out of scope (deferred to Wave B / later phases):**
- Sentence-by-sentence HHEM streaming (architecture step 8 sub-bullet "STREAMED to chat UI sentence-by-sentence" + step 9 HHEM gate) — Wave B; 9 ships SSE infrastructure, HHEM is a separate phase.
- HalluGraph KG-alignment (architecture step 9 gate B) — Wave C.
- Conflict-resolution cascade (doc-chain → status → authority → recency) — needs Designs 3/7 + chains_table; Wave B.
- Anthropic Citations-style sentence-level span grounding — Wave B.
- Templated output for aggregate Q (mode Q from architecture §6 step 5) — depends on planner mode-detection; Wave B.
- Real Anthropic Generator impl — Wave B (same Wave-A defer pattern as 8a/8d decision #10).
- Streaming response — Wave A returns the full `GenerationResult` from a single async call. SSE wrapping is Phase 9's surface.

**Decisions locked at G1:**

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | LLM | Gemini default; Identity fallback. Reuses `KB_QUERY_LLM` env (shared with 8a + 8d). | Same LLM family across 8a/8d/8e keeps cost story unified; one switch flips Gemini→Anthropic→Identity for all three. |
| 2 | Hits fed to LLM | Top-10 reranked hits (per Phase 8 overall decision #4 — "Top-K post-rerank returned to chat"). | CRAG (8d) only sees top-3 (cost cap on judgment); generation sees the full top-10 for richer citation surface. |
| 3 | Snippet length per hit in prompt | 500 chars (already truncated at 8b decision #11). | Bounded prompt; matches what UI eventually renders. |
| 4 | Output schema | Structured `GenerationResult(answer: str, citations: list[Citation], refused: bool, refusal_reason: str \| None, model_id: str)` where `Citation(hit_id: str, kind: str, file_id: str \| None, snippet_preview: str, score: float)`. Gemini `response_mime_type=application/json`. | Pydantic validation gives type-safety into 8f without bespoke parsing. |
| 5 | Citation format inside answer text | Inline `[hit_id]` (8-char prefix of UUID for readability). Citations array also returned separately so UI can render badge cards. | Astute paper's pattern: every claim cites; UI can hyperlink. |
| 6 | Refusal trigger (a) | If `force_refuse=True` (orchestrator passes this when CRAG < 0.5) → skip LLM, return refusal envelope. | Don't waste a token call when we already know we'll refuse. Also returns predictable `refusal_reason="insufficient_evidence"`. |
| 7 | Refusal trigger (b) | If `hits == []` → same refusal envelope, refusal_reason="no_hits". | Empty retrieval = nothing to cite. |
| 8 | Refusal trigger (c) | LLM may itself refuse by returning `{refused: true, refusal_reason: "..."}` in JSON. Parser respects this. | Astute defensive prompt instructs the model: "If hits don't support an answer, refuse." Model is the last line of defense after CRAG. |
| 9 | Parser fail-safes | Bad JSON / non-dict / missing-required-key / wrong-types → return `GenerationResult(refused=True, refusal_reason="parse_error", citations=[])`. | Cite-or-refuse > silent hallucination. |
| 10 | LLM error | Any LLM exception → return refusal envelope with `refusal_reason="llm_error"`. NOT fail-safe-pass like CRAG (because passing here would mean emitting a fake answer). | Generation refuses on infra failure; CRAG passes on infra failure. Asymmetric because consequences differ. |
| 11 | Token budget | `max_output_tokens=2048`. | Long enough for a paragraph + citations; short enough to cap cost. |
| 12 | Thinking budget | `thinking_budget=0` (Gemini). | RAG-grounded answer, not deep reasoning. Cost-aware. |
| 13 | Identity stub | Returns `answer = "[identity-stub] {query} (hits: N)"` with citations synthesized from first 3 hits (or empty if hits == []). Deterministic for tests. | Lets 8f orchestrator + CI run end-to-end without a key. |
| 14 | Anthropic impl | Deferred to Wave B — `KB_QUERY_LLM=anthropic` maps to `IdentityGenerator` for now (documented decision; same pattern as 8a/8d). | Don't pretend to support Anthropic without testing. Identity-fallback is the honest answer. |
| 15 | Where the prompt lives | System-instruction Astute defensive prompt (LLM-agnostic recipe); hits + query land in `contents`. | System-instruction has caching semantics + intent clarity. |

**Files (G3/G4):**
- `src/kb/query/generate.py` — `GenerationResult` + `Citation` Pydantic models + `Generator` Protocol + 2-impl factory
- `tests/specs/phase_8e.md` — test spec
- `tests/test_query_generate_unit.py` — pure-function + mocked Gemini tests (~14)
- `scripts/verify_phase_8e.sh` — module-level checks (mirrors 8a/8c/8d structure)

**Phase 8e G5 — what "green" means:**
- `scripts/verify_phase_8e.sh` standalone — 10-12 checks.
- Full pytest: prior 472 still GREEN + new ≥12 Phase 8e tests GREEN.
- Cross-phase sweep across all 21 verify scripts: 21/21 GREEN.

---

### 5.15.6 Phase 8f plan — Orchestrator + HTTP surface (G1 ✅ → G5 ✅)

> **Status:** All gates green 2026-05-25. 518/518 pytest (27 new). Branch `phase-8f/orchestrator`. The synthesis of 8a→8e: stitches query rewriter (8a) → 6-channel parallel retrieval × rewrites + RRF fusion (8b) → reranker (8c) → CRAG gate (8d) → Astute generator (8e) into the user-facing `POST /search` (read-only retrieval inspector) and `POST /chat` (full pipeline returning `GenerationResult`). Introduces the immutable `query_log` audit table (migration 0019). **No SSE streaming** — that's Phase 9's surface.

**Scope (in / out):**

**In scope:**
- `src/kb/query/orchestrator.py`: `Orchestrator` class. Methods `search(query, workspace_id) -> SearchResult` (rewriter → channels × 4 queries → RRF → rerank; returns reranked top-10 + CRAG score, no generation) and `chat(query, workspace_id, idempotency_key=None) -> ChatResult` (search + CRAG gate + generate; full pipeline). Both write a row to `query_log` for audit.
- `src/kb/api/query.py`: FastAPI router mounting `POST /search` + `POST /chat`. Read-only `Idempotency-Key` semantics on `/chat` (cached answer returned on replay).
- Migration `0019_query_log.sql`: workspace-scoped + RLS-forced + kb_app SELECT+INSERT only (audit immutability per architecture §6); columns per decision #11 below.
- New error types: `invalid-query` (400 — empty / oversize query), `query-pipeline-error` (500 — internal failure).
- Mount the new router in `src/kb/api/main.py` after existing routers.
- Wire `Orchestrator` into the API layer (deps function constructs it lazily from `make_rewriter()` + `make_reranker()` + `make_crag_gate()` + `make_generator()`).

**Out of scope (deferred):**
- SSE streaming on `/chat/:id/stream` — Phase 9 wraps.
- Query-mode classification (Q/D/E modes per architecture §6 step 5) — Wave A is "H" only.
- Per-query authority/recency tiebreakers (architecture step 8 cascade) — Wave B; needs Designs 3/7.
- HHEM faithfulness gate — Wave B.
- Rate limiting / RBAC — Wave B (Phase 9 admin RBAC).
- Caching of `/search` results — Wave B.

**Decisions locked at G1:**

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | Query-mode classification | Wave A is "H" (hybrid) only. No Q/D/E branching. | Architecture's mode classifier is itself an LLM call; defer to keep Wave A latency + cost predictable. "H" covers the 80%-case (find-and-cite). |
| 2 | Rewrites fanned out to channels | All 4: `original` + `step_back` + `hyde` + `query2doc` (from 8a's `Rewrites`). | Each rewrite probes a different facet; RRF (8b) dedupes when rewrites converge. Identity rewriter produces 4 dupes — still safe (RRF idempotent on identical lists). |
| 3 | Channel × query fan-out | 4 rewrites × 6 channels = 24 result lists → RRF. | `asyncio.gather(return_exceptions=True)` keeps a single channel failure from killing the query (already 8b's contract). |
| 4 | Pre-RRF top-K per channel | 20 (matches Phase 8 overall decision #2). | Already what 8b's channels honor. |
| 5 | Post-RRF top-K (pre-rerank) | 30 (matches Phase 8 overall decision #3). | Slim from 24 lists' worth of dedupes to a bounded set the reranker can chew. |
| 6 | Post-rerank top-K (returned + fed to generator) | 10 (matches Phase 8 overall decision #4). | Hard cap on UI render + generation prompt size. |
| 7 | CRAG gate placement | After rerank, before generate. Top-3 fed to CRAG (already 8d's contract). | Don't waste a generate call when CRAG says "no". |
| 8 | Force-refuse path | If `crag_score < CRAG_THRESHOLD`, orchestrator calls `generate(force_refuse=True)` so the response shape is consistent (always a `GenerationResult` with refusal envelope when applicable). | Single response shape simplifies UI; refusal_reason="insufficient_evidence" tells the user why. |
| 9 | `/search` returns | `SearchResult(query, rewrites, hits: list[Hit], crag_score, latency_ms, query_id)` — JSON-serializable. Reranked top-10 hits + CRAG score; NO generated answer. | Inspector / debugger endpoint; chat UI doesn't call it but `audit.html` (Phase 10f) will. |
| 10 | `/chat` returns | `ChatResult(query, generation: GenerationResult, hits: list[Hit], crag_score, latency_ms, query_id)`. | Single envelope carries everything UI needs to render answer + citations + refusal reason if any. |
| 11 | `query_log` columns | `id uuid PK · workspace_id uuid · query text · mode text DEFAULT 'H' · rewrites jsonb · hit_ids jsonb (list of {id, kind, score}) · crag_score float · refused bool · refusal_reason text · answer text · citations jsonb · model_id text · latency_ms int · idempotency_key text · created_at timestamptz`. RLS enabled + forced; kb_app SELECT+INSERT only. | Audit immutability per architecture §6. Phase 9 `/audit` endpoint reads this table. |
| 12 | Workspace scoping | Both endpoints require `X-Workspace-Id` header (existing convention from Phase 1a). | RLS context set in deps; no leaks. |
| 13 | Idempotency on `/chat` | Honor `Idempotency-Key` header per existing pattern (Phase 1a §0). On replay, look up by `(workspace_id, idempotency_key)` in `query_log` and return cached `ChatResult`. `/search` is not idempotent-keyed (it's a read). | Re-issuing the same chat shouldn't re-spend tokens. |
| 14 | Error types | `invalid-query` (400, empty or oversize query >4000 chars); `query-pipeline-error` (500, internal failure). RFC 9457 envelope per `kb.api.errors`. | Loud-fail on bad input; opaque-500 on infra (don't leak internal exception text). |
| 15 | Streaming | Not in Wave A. `/chat` is JSON. Phase 9 wraps with SSE under `/chat/:id/stream`. | Async generation + SSE infrastructure is Phase 9 scope. |
| 16 | Empty corpus | Pipeline runs normally; all channels return `[]`; CRAG returns 0.0 (empty hits); generator force-refuses with `no_hits`. User gets refusal envelope (not 4xx). | Consistent shape for "I have no docs yet" vs "I have docs but none match". |
| 17 | Per-query latency budget | No hard cap; record `latency_ms` in query_log. P95 monitoring is Phase 11+ ops. | Wave A is correctness-first; observability lands in audit table. |

**Files (G2/G3/G4):**
- `migrations/sql/0019_query_log.sql` — table + RLS + GRANTs + index on `(workspace_id, created_at DESC)` for audit-list queries
- `docs/api_contracts.md` §7 (was placeholder) — `POST /search` + `POST /chat` shapes; renumber old §7 placeholder + §8 changelog
- `src/kb/query/orchestrator.py` — `Orchestrator` + `SearchResult` + `ChatResult` Pydantic models
- `src/kb/api/query.py` — FastAPI router with the 2 endpoints + deps
- `src/kb/api/main.py` — mount `query_router`
- `src/kb/api/errors.py` — 2 new error type slugs
- `tests/specs/phase_8f.md` — test spec
- `tests/test_query_orchestrator_unit.py` — orchestrator unit tests (~15, mocked components)
- `tests/test_api_query.py` — HTTP endpoint tests over testcontainers (~12)
- `scripts/verify_phase_8f.sh` — full E2E: upload doc → ready → POST /chat returns refusal (no key) + query_log row written

**Phase 8f G5 — what "green" means:**
- `scripts/verify_phase_8f.sh` standalone — 14-16 checks.
- Full pytest: prior 491 still GREEN + new ≥25 Phase 8f tests GREEN.
- Cross-phase sweep across all 22 verify scripts: 22/22 GREEN.

---

### 5.16 Phase 9 plan — Audit + lifecycle visibility + chat replay SSE (G1 ✅ → G5 ✅)

> **Status:** All gates green 2026-05-25. 541/541 pytest (23 new). verify_phase_9.sh 14/14 with real E2E SSE 13 lifecycle events from a tiny.pdf upload streamed to ready. Branch `phase-9/sse-audit`. Three endpoints needed to unblock Phase 10a/10b UI: live upload-status SSE, paginated query audit list, and chat-replay SSE for re-viewing past answers. No DB migration — reads existing `file_lifecycle` (Phase 2a) + `query_log` (Phase 8f) tables.

**Scope (in / out):**

**In scope:**
- `GET /upload/:file_id/status` SSE — polls `file_lifecycle` every `KB_SSE_POLL_INTERVAL_MS` (default 1000), emits new events as JSON, closes when `lifecycle_state ∈ {ready, failed}`.
- `GET /audit` — paginated list of past `/search` + `/chat` calls; cursor on `(created_at DESC, id)` using 8f's audit-list index.
- `GET /chat/:query_id/stream` SSE — re-streams the cached answer from `query_log` in chunks (Wave A: deterministic chunking by character count; Wave B: re-run query end-to-end).
- `src/kb/api/sse.py` — both SSE routers (FastAPI `StreamingResponse` with `text/event-stream`).
- `src/kb/api/audit.py` — `GET /audit` router.

**Out of scope (deferred):**
- Hash-chained `audit_log` (architecture §6 hash trigger — deferred from Phase 0; remains Wave B).
- Re-extraction admin endpoints — Wave B.
- Per-stage progress percentages (architecture lifecycle is event-driven, not progress-driven — UI shows badges).
- Real LLM re-run on `/chat/:qid/stream` — Wave A returns cached answer (avoid double-billing).
- Auth / RBAC on `/audit` (currently same X-Workspace-Id model as everywhere else).
- Server-side cursor encryption — cursor is `(created_at, id)` tuple, opaque-encoded as base64 JSON.

**Decisions locked at G1:**

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | SSE wire format | Standard `text/event-stream`: `event: <type>\ndata: <json>\n\n`. Event types: `lifecycle` (upload), `chunk`/`done` (chat replay), `heartbeat` (idle keepalive). | Browsers' native `EventSource` handles this; Phase 10's Next.js can consume directly. |
| 2 | Upload-status polling | Server-side poll of `file_lifecycle` every 1000ms (env `KB_SSE_POLL_INTERVAL_MS`); emit each event row not yet sent. Tracks max `created_at` per stream to avoid replay. | LISTEN/NOTIFY would be lower-latency but adds infrastructure; polling at 1s is fine for human-perceived "live" status. |
| 3 | Upload-status terminal close | Stream closes (final `event: done`) when last emitted event is `lifecycle_state IN ('ready', 'failed')`. | Matches existing lifecycle model from Phase 2a. |
| 4 | Heartbeat | Send `event: heartbeat\ndata: {}\n\n` every 15s if no other event fires. Prevents proxy/load-balancer idle-timeouts. | Standard SSE practice; nginx default proxy_read_timeout is 60s. |
| 5 | `/audit` pagination | Cursor-based: `?cursor=<b64>&limit=<int>`. Cursor encodes `{"created_at": iso, "id": uuid}`. Default limit=50, max=200. Sort: `created_at DESC, id DESC`. | Cursor pagination is correct for append-only tables; matches the audit-list index from 8f decision #11. |
| 6 | `/audit` response shape | `{items: [QueryLogEntry], next_cursor: str \| null}`. Each entry: id · created_at · endpoint · query · mode · crag_score · refused · refusal_reason · answer (truncated to 500 chars) · latency_ms · model_id. | Light shape for list view; UI can fetch full row via `/chat/:id/stream` for replay. |
| 7 | Chat replay chunking | Deterministic char chunks: emit 50 chars per event, 50ms apart. Sentence-level splitting deferred to Wave B (HHEM streaming, architecture §6 step 8). | Wave A goal is "user sees answer materialize"; sentence-precision matters for HHEM, not for replay. |
| 8 | Chat replay 404 | If `query_log` row not found (or wrong workspace) → 404 BEFORE opening the stream. NOT a stream-closed-immediately envelope. | Standard REST hygiene; SSE only for the happy path. |
| 9 | SSE workspace isolation | Both SSE endpoints require `X-Workspace-Id` (existing convention); RLS context set before yielding first event. | Consistent with everything else. |
| 10 | SSE auth replay-safety | No special handling — re-opening SSE with same URL re-reads from `file_lifecycle` / `query_log` from scratch (idempotent reads). | SSE replay/refresh is naturally safe for read-only streams. |
| 11 | Chunked response Content-Type | `text/event-stream; charset=utf-8` on both SSE endpoints. `application/json` on `/audit`. | Wire format compliance. |
| 12 | Error inside SSE stream | Emit `event: error\ndata: {"type": "...", "detail": "..."}\n\n` then close. Don't crash silently. | UI can show "stream interrupted, refresh to retry" with the type slug. |

**Files (G3/G4):**
- `src/kb/api/sse.py` — `/upload/:file_id/status` + `/chat/:query_id/stream` routers + heartbeat task
- `src/kb/api/audit.py` — `/audit` router with cursor pagination
- `src/kb/api/main.py` — mount both new routers
- `tests/specs/phase_9.md` — test spec
- `tests/test_audit_unit.py` — `/audit` unit + endpoint tests (~10)
- `tests/test_sse_unit.py` — SSE unit + endpoint tests with EventSource-style parsing (~12)
- `scripts/verify_phase_9.sh` — E2E: upload doc → SSE streams lifecycle through ready → /audit lists query → /chat replay streams chunks

**Phase 9 G5 — what "green" means:**
- `scripts/verify_phase_9.sh` standalone — 10-12 checks.
- Full pytest: prior 518 still GREEN + new ≥20 Phase 9 tests GREEN.
- Cross-phase sweep across all 23 verify scripts: 23/23 GREEN.

---

### 5.17 Phase 10a plan — Next.js Upload UI (G1 ✅ → G5 ✅)

> **Status:** All gates green 2026-05-25. `ui/` Next.js 15 app shipped with drag-drop + live SSE-driven status table. 10/10 vitest + 2/2 Playwright (screenshot saved). Backend 541/541 still GREEN after CORS middleware addition. Branch `phase-10a/upload-ui`. Consumes Phase 2a (`POST /files`) + Phase 9 (`GET /upload/:file_id/status` SSE) to deliver a drag-drop upload page with live per-file lifecycle status. Mirrors `prototype/upload.html` design. Next.js 15 (App Router) per architecture §7 line 178.

**Scope (in / out):**

**In scope:**
- `ui/` Next.js 15 (App Router) + TypeScript + Tailwind + lucide-react. Top-level directory; standalone build.
- `/upload` page: drag-drop zone (uses native file input + drag-drop events) + status table.
- API client `ui/lib/api.ts` with: `uploadFile(file, idempotencyKey)` (multipart POST /files), `subscribeToStatus(fileId, onEvent)` (native EventSource), `listFiles()` (GET /files).
- React Context for per-session uploaded files state; each file row holds `id, name, lifecycle_state, events, error`.
- Stage badges (`StageBadge`) mirror the 5-pip animated style from prototype.
- CORS middleware added to FastAPI backend allowing the Next.js dev origin.
- Page on initial mount calls `GET /files` to surface pre-existing files.
- Failed files surface error from lifecycle event payload.
- 1-page initial UI: sidebar + topbar + main area; no auth, no theme toggle wired (light only).

**Out of scope (deferred):**
- 10b — Chat page (separate sub-phase).
- 10c+ — Explore / Schema Studio / Dashboard / Audit — Wave C.
- Multi-conversation persistence (Wave B).
- Real-time WebSockets / WS fallback for SSE (browser EventSource is sufficient at Wave A).
- Authentication / user accounts (single hardcoded workspace per env).
- Internationalization, themes, accessibility audit (best-effort only).
- Drag-drop folders / ZIPs (Wave A accepts single files via input + drag of files).
- Re-run failed retries (deferred to Phase 9b admin).

**Decisions locked at G1:**

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | Framework | Next.js 15 (App Router) + TypeScript. | Locked at architecture §7 line 178. App Router is the long-supported pattern; React 19 server components default. |
| 2 | Location | `ui/` at repo root (sibling of `src/`, `tests/`, `prototype/`). | Cleanest separation; backend (`src/`) doesn't import from `ui/` and vice versa. |
| 3 | Styling | Tailwind CSS v4 + lucide-react icons. Light theme only Wave A. | Matches prototype + architecture stack. |
| 4 | API base URL | Read `NEXT_PUBLIC_KB_API_URL` env (default `http://localhost:8000`). | Standard Next.js public-env pattern; dev runs Next.js at :3000 against backend at :8000. |
| 5 | Workspace ID | Read `NEXT_PUBLIC_KB_WORKSPACE_ID` (default `00000000-0000-0000-0000-000000000001` matching backend default-workspace sentinel from §0 conventions). | Wave A is single-tenant per env; no login screen. |
| 6 | CORS | Add `CORSMiddleware` to FastAPI allowing `KB_CORS_ORIGINS` env (default `http://localhost:3000`). | Browser blocks cross-origin fetch otherwise. Env-controlled so prod can lock down. |
| 7 | File upload | Native `<input type="file" multiple>` + drag-drop on dropzone. POST `/files` multipart with `Idempotency-Key: <uuid()>` per file. | Browser-native; no extra libs. Each file gets a unique idem-key so retries don't double-upload. |
| 8 | SSE consumption | Native `EventSource` API. One connection per in-flight file. Closes connection on `event: done`. | Standard, no dependencies; matches Phase 9's wire format. |
| 9 | State | React Context with `useReducer`. In-memory only — refresh wipes (Phase 10c will hydrate from GET /files). | Wave A simplicity. |
| 10 | Initial-load file list | On `/upload` mount, fetch `GET /files` and seed the table with any pre-existing files. | Lets users return after a refresh and see their uploads. |
| 11 | Status table columns | File · Type (mime / detected) · Stage (5-pip animated + state name) · Elapsed · Detected. Matches prototype. | UX consistency with the design system the user signed off on. |
| 12 | Stage pip mapping | 5 pips across the canonical pipeline: parsed · embedded · raptor · extracted · ready. Each pip is a 1.5x1.5 dot; current one pulses; completed are filled; pending are zinc-200. | Visual signal for "what stage are we on" matching prototype. |
| 13 | Build artifact | `npm run build` produces `ui/.next/`. Not currently shipped to backend; dev workflow is `npm run dev` (port 3000) + uvicorn (port 8000). | Wave A simplicity. Production-build container deferred to Wave B. |
| 14 | Testing strategy | Vitest unit tests for `lib/api.ts` + Playwright E2E test that hits the running dev server (visual sanity). Screenshot saved as artifact. | Unit + integration; Playwright proves the page renders against a real backend. |
| 15 | Verify script | `scripts/verify_phase_10a.sh` runs (1) `npm install`, (2) `npm run build`, (3) `npm test` (Vitest), (4) starts the backend + Next.js dev server, (5) Playwright headless test asserts dropzone is visible + screenshot saved. | Mirrors phase verify discipline; no docker-compose change needed. |

**Files (G3/G4):**
- `ui/package.json` + `ui/next.config.ts` + `ui/tsconfig.json` + `ui/tailwind.config.ts` + `ui/postcss.config.mjs`
- `ui/app/layout.tsx` + `ui/app/globals.css` + `ui/app/page.tsx` (redirects to /upload)
- `ui/app/upload/page.tsx` — main page
- `ui/components/{Sidebar,TopBar,DropZone,FilesTable,FileRow,StageBadge}.tsx`
- `ui/lib/api.ts` — fetch + SSE client
- `ui/lib/workspace.ts` — env helper
- `ui/lib/state.ts` — Context + reducer
- `ui/tests/api.test.ts` — Vitest unit tests
- `ui/tests/upload.spec.ts` — Playwright E2E
- `src/kb/api/main.py` — add CORS middleware (KB_CORS_ORIGINS env-controlled)
- `tests/specs/phase_10a.md` — test spec
- `scripts/verify_phase_10a.sh` — install + build + test + screenshot

**Phase 10a G5 — what "green" means:**
- `npm run build` succeeds (TypeScript + ESLint clean).
- `npm test` (Vitest unit tests for `lib/api.ts`) passes.
- `scripts/verify_phase_10a.sh` standalone: build + start backend + start dev server + Playwright assertion + screenshot saved.
- Full pytest: prior 541 still GREEN (no backend regressions from CORS middleware addition).
- Cross-phase sweep across all 23 backend verify scripts: 23/23 still GREEN.

---

### 5.18 Phase 10b plan — Next.js Chat UI (G1 🟡 DRAFT — placeholder)

Per `prototype/chat.html`. Consumes Phase 8f (`POST /chat`) + Phase 9 (`GET /chat/:query_id/stream` SSE). Drafted at 10b open.

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
| 3a | (no new endpoints; `lifecycle_state` enum widens by `chunked`) | ✅ signed off 2026-05-23 (§5.1 #3 + §5.2) |
| 3b–7 | Mostly internal workers; admin endpoints TBD at G1 | ⬜ |
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
| 1c | [tests/specs/phase_1c.md](../tests/specs/phase_1c.md) | [test_schema_entities.py](../tests/test_schema_entities.py) · [test_schema_fields.py](../tests/test_schema_fields.py) · [test_schema_relationships.py](../tests/test_schema_relationships.py) · [test_schema_hierarchy_versions.py](../tests/test_schema_hierarchy_versions.py) | ✅ 36 new tests green (post-G5); suite total 142 |
| 2a | [tests/specs/phase_2a.md](../tests/specs/phase_2a.md) | [test_files_crud.py](../tests/test_files_crud.py) · [test_parse_dispatch.py](../tests/test_parse_dispatch.py) · [test_parse_pdf_docling.py](../tests/test_parse_pdf_docling.py) · [test_raw_pages.py](../tests/test_raw_pages.py) · [test_files_lifecycle.py](../tests/test_files_lifecycle.py) | ✅ 28 new tests green (post-G5); suite total 170 |
| 2b | [tests/specs/phase_2b.md](../tests/specs/phase_2b.md) | [test_parse_xlsx.py](../tests/test_parse_xlsx.py) · [test_parse_email.py](../tests/test_parse_email.py) · [test_parse_mistral_ocr.py](../tests/test_parse_mistral_ocr.py) · [test_files_crud.py](../tests/test_files_crud.py) (additive) | ✅ 18 new tests green (15 parser-unit + 3 HTTP-additive); suite total 188 |
| 3a | [tests/specs/phase_3a.md](../tests/specs/phase_3a.md) | [test_chunking_unit.py](../tests/test_chunking_unit.py) · [test_chunking_worker.py](../tests/test_chunking_worker.py) | ✅ 16 new red skeletons (9 unit + 7 worker); suite collect 188 → 204 |
| 3b | [tests/specs/phase_3b.md](../tests/specs/phase_3b.md) | [test_contextualization_unit.py](../tests/test_contextualization_unit.py) · [test_contextualization_worker.py](../tests/test_contextualization_worker.py) | ✅ 15 new red skeletons (9 unit + 6 worker); suite collect 204 → 219 |
| 3c | [tests/specs/phase_3c.md](../tests/specs/phase_3c.md) | [test_embeddings_unit.py](../tests/test_embeddings_unit.py) · [test_embeddings_worker.py](../tests/test_embeddings_worker.py) | ✅ 13 new red skeletons (7 unit + 6 worker); suite collect 219 → 232 |
| ... | | | |

---

## 8. Run / verify — index

> Each phase's G5 produces a script (`scripts/verify_<phase>.sh`) or a manual checklist appended to this tracker. Outputs are summarized here.

| Phase | Verify script | Last run | Result |
|---|---|---|---|
| 0 | [scripts/verify_phase_0.sh](../scripts/verify_phase_0.sh) | 2026-05-23 (post Phase 1a code) | ✅ 16/16 (still green after Phase 1a's code landed) |
| 1a | [scripts/verify_phase_1a.sh](../scripts/verify_phase_1a.sh) | 2026-05-23 (post Phase 1b code) | ✅ 17/17 (compose smoke + 9 schemas assertions + 29 pytest) — still green after Phase 1b code landed |
| 1b | [scripts/verify_phase_1b.sh](../scripts/verify_phase_1b.sh) | 2026-05-23 | ✅ 21/21 (compose smoke + 5 DDL assertions on schema_versions + 11 HTTP/rollback/RLS curl checks + openapi check + Phase-1b pytest 52) |
| 1c | [scripts/verify_phase_1c.sh](../scripts/verify_phase_1c.sh) | 2026-05-23 | ✅ 20/20 (compose smoke + 4 DDL assertions on 3 new tables + 10 HTTP/cascade/rollback/RLS curl checks + openapi exposure + Phase-1c pytest 36) |
| 2a | [scripts/verify_phase_2a.sh](../scripts/verify_phase_2a.sh) | 2026-05-23 | ✅ 17/17 (compose smoke + 4 DDL assertions on 4 new tables + 9 HTTP/E2E parse curl checks incl. real Docling parse + RLS isolation + dedup-header + 415 + openapi exposure + Phase-2a pytest 28) |
| 2b | [scripts/verify_phase_2b.sh](../scripts/verify_phase_2b.sh) | 2026-05-23 | ✅ 15/15 (compose smoke + xlsx + email upload + parse to lifecycle_state='parsed' + xlsx page-text sheet header + magic-byte sniff routing both ways + Mistral inert without API key + text/plain 415 + Phase-2b pytest 28) |
| 3a | [scripts/verify_phase_3a.sh](../scripts/verify_phase_3a.sh) | 2026-05-23 | ✅ 18/18 (compose smoke + 4 DDL assertions on chunks table + lifecycle CHECK + 4 E2E PDF/xlsx/email parse-to-chunked + idempotent re-defer + Phase-3a pytest 16) |
| 2c | [scripts/verify_phase_2c.sh](../scripts/verify_phase_2c.sh) | 2026-05-24 | ✅ 15/15 (compose smoke + pypdfium2 worker import probe + KB_PARSER_STRATEGY env probe + digital→Docling E2E + provenance JSON on raw_pages + provenance in lifecycle parse_done + scanned→soft-Docling-fallback when no Gemini key + caller override `?parser=docling` + 400 invalid-parser-override + Phase-2c pytest 18) |
| 3a | [scripts/verify_phase_3a.sh](../scripts/verify_phase_3a.sh) | 2026-05-24 (post 3e) | ✅ 18/18 (re-green after 3c forward-compat accept-set widening + 3d/3e land) |
| 3b | [scripts/verify_phase_3b.sh](../scripts/verify_phase_3b.sh) | 2026-05-24 (post 3b-bis) | ✅ 16/16 (widened 15→16 at 3b-bis: added KB_CONTEXTUALIZER env probe + conditional Gemini/Anthropic/Identity branch on `model_id`/`cache_creation_input_tokens`/`cache_read_input_tokens`) |
| 3c | [scripts/verify_phase_3c.sh](../scripts/verify_phase_3c.sh) | 2026-05-24 (post 3d) | ✅ 15/15 (accept-set widened to `embedded \| raptor_building \| ready` for forward-compat with 3d) |
| 3d | [scripts/verify_phase_3d.sh](../scripts/verify_phase_3d.sh) | 2026-05-24 | ✅ 22/22 (compose smoke + 7 DDL assertions on raptor_nodes + raptor_edges + scope CHECK + discriminated edge CHECK + REVOKE UPDATE/DELETE + lifecycle CHECK includes `raptor_building` + E2E PDF through to `ready` + lifecycle history `embedded→raptor_building→ready` + raptor_build_started + raptor_build_done events + raptor_nodes L2 row + L2→contextual_chunks edge (both gated on `leaf_count >= 2` since tiny.xlsx is singleton) + payload shape + idempotent re-defer + Phase-3d pytest 17) |
| 3e | [scripts/verify_phase_3e.sh](../scripts/verify_phase_3e.sh) | 2026-05-24 | ✅ 13/13 (compose smoke + umap-learn worker import probe + empty-workspace 400 corpus-rebuild-no-input pre-flight + 5-doc upload through to ready + POST /corpus/raptor/rebuild → 202 + wait for raptor_build_corpus job succeeded + scope='corpus' nodes exist + corpus → contextual_chunks discriminated edges + atomic rebuild count-stable + Phase-3e pytest 11) |
| 4 | [scripts/verify_phase_4.sh](../scripts/verify_phase_4.sh) | 2026-05-25 | ✅ 16/16 standalone (compose smoke + 4 DDL invariants on HNSW + BM25 indexes with operator-class assertions + HNSW build params check + tiny.pdf E2E to `ready` + ANALYZE + 3 planner-usage EXPLAIN checks with `enable_seqscan/bitmapscan=off` forcing flags + worker imports `kb.retrieval.smoke` + no-leak grep for `kb.retrieval` in `kb.api/*` (decision #10) + Phase-4 pytest 10) |
| 5 | [scripts/verify_phase_5.sh](../scripts/verify_phase_5.sh) | 2026-05-25 | ✅ 16/16 standalone (compose smoke + DDL invariants for 3 new tables + 2 column adds + lifecycle CHECK widening + xlsx E2E through full Phase 5 chain to `ready` + lifecycle history contains all 3 Phase 5 events + `inferred_doc_type` populated + atomic_units rows of type='row' written with sheet_name/cells parameters + Phase-5 pytest 50) |
| 6 | [scripts/verify_phase_6.sh](../scripts/verify_phase_6.sh) | 2026-05-25 | ✅ 10/10 standalone (compose smoke + extracted_entities table + RLS + columns shape with ltree + GiST index + lifecycle CHECK widening + xlsx E2E to `ready` + Phase 6 lifecycle transition observed + schema_entities_extracted event + Phase-6 pytest 24) |
| 7 | [scripts/verify_phase_7.sh](../scripts/verify_phase_7.sh) | 2026-05-25 | ✅ 16/16 standalone (compose smoke + entities + mention_to_entity tables + RLS + resolved_method CHECK + UNIQUE deterministic index + HNSW partial index on embedding + lifecycle CHECK widening + xlsx E2E to `ready` + entities_extracting→identity_resolving→ready transition + identities_resolved event + fabricated-mention end-to-end resolve writes 2 entities + 2 links + cross-file deterministic collapse + Phase-7 pytest 37) |

**Cross-phase sweep totals (2026-05-25 post-Phase-7, all 16 scripts):**
0:34s · 1a:15s · 1b:22s · 1c:21s · 2a:70s · 2b:21s · 2c:54s · 3a:55s · 3b:55s · 3c:46s · 3d:51s · 3e:82s · 4:16s · 5:22s · 6:20s · 7:27s = **16/16 GREEN**.

**Cross-phase sweep totals (2026-05-25, all 13 scripts via verify_sweep.sh):**
0:24s · 1a:17s · 1b:17s · 1c:18s · 2a:47s · 2b:16s · 2c:27s · 3a:50s · 3b:41s · 3c:42s · 3d:53s · 3e:59s · 4:14s = **13/13 GREEN in 14:56 total**.

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
| 2026-05-23 | **Phase 1c G4 ✅ — code landed (single commit `a47bcc4`).** 7 files: `0007_schema_hierarchy.sql` (3 new tables + RLS + CHECK enums for type + kind + cardinality); `kb/domain/schema_hierarchy.py` (12 pydantic models + 7 domain exceptions + 11 repo functions + `build_subtree_snapshot` + reconciling `restore_subtree`); `kb/domain/schema_versions.py` mutated (recursive `compute_diff` keyed by name within `entities`/`fields`/`relationships`); `kb/domain/schemas.py` mutated (rollback now calls `restore_subtree`; new `lock_and_assert_active_schema` + `bump_schema_version` helpers); `kb/api/schema_hierarchy.py` router (11 endpoints; each mutating endpoint: lock parent → mutate → bump version); `kb/api/main.py` (mount router + 7 new exception handlers); `tests/test_schema_versions.py` mutated (2 body-shape assertions accept the new {entities: [], relationships: []} keys — Phase 1b's shape is the strict subset). All 13 G1 decisions traced in commit body. **One mid-G4 fix landed in the same commit**: initial `restore_subtree` soft-deleted-all-then-recreated, breaking the rollback tests that expected existing entity UUIDs to survive when the snapshot still includes them by name. Reconciliation algorithm written: keep rows present in both current+snapshot, soft-delete those missing from snapshot, create those missing from current. pytest -q tests/ → **142 passed** in 27.47s. | Aniket |
| 2026-05-23 | **Phase 1c G5 ✅ + end-of-phase cross-phase sweep.** Authored `scripts/verify_phase_1c.sh` (20 checks): compose smoke + 4 DDL assertions (3 tables exist + RLS forced + kind CHECK enum + type CHECK enum) + 11 HTTP/cascade/rollback/RLS/openapi curl checks + Phase-1c pytest. **Cross-phase sweep** per memory entry `feedback_end_of_phase_cross_phase_check.md`: (a) verify_phase_0.sh re-run → 16/16 GREEN; verify_phase_1a.sh → 17/17 GREEN; verify_phase_1b.sh → 21/21 GREEN; verify_phase_1c.sh → 20/20 GREEN. (b) Scope-leak grep clean — no `extracted_entities` / `lineage_path` / `domain_vocabulary` / `re_extraction` / `enforce.*single_parent` / `/descendants` / `/ancestors` / `/breadcrumb` / rogue `INSERT INTO audit_log` in Phase 1c code. (c) RLS invariant grows from 4 → 7 workspace-scoped tables (audit_log, idempotency_keys, schemas, schema_versions, schema_entities, schema_fields, schema_relationships); each has own `workspace_id` column + own `CREATE POLICY` per the decision-#10 / belt-and-braces convention. (d) pytest --collect-only confirms 142 total tests. **Phase 1c complete; Phase 1 (a/b/c) closed; ready to open PR. Next major phase: Phase 2 (parse layer — first worker phase).** | Aniket |
| 2026-05-23 | **Phase 1c merged.** PR #4 merged into `main` (merge commit `af2d77f`). Tag `phase-1c-complete` pushed. Local `phase-1c/schema-hierarchy` branch deleted. **Phase 1 (a/b/c) closed.** | Aniket |
| 2026-05-23 | **Phase 2 split into 2a + 2b** per [`feedback_sub_phase_splits`](../../.claude/memory/feedback_sub_phase_splits.md). §5 description "Parse layer: Docling + Mistral OCR + xlsx + email → raw_pages" is four comma-separated parsers. 2a builds the scaffold + Docling (digital PDF — the most common format; Wave A's CUAD/SEC corpus is mostly digital PDF). 2b layers the remaining parsers (xlsx via openpyxl + email via stdlib + Mistral OCR via external API) via the same `Parser` Protocol. pptx + Gemini VLM fallback explicitly Wave B. | Aniket |
| 2026-05-23 | **Phase 2a G1 OPEN.** Branched `phase-2a/parse-scaffold` from `main`. Plan section §5.5 drafted: `0008_parse_layer.sql` adds 4 workspace-scoped + RLS-day-1 tables (`files`, `file_lifecycle` append-only audit, `raw_pages` immutable per-page output, `parse_artifacts` MinIO-pointers); MinIO holds bytes under `raw_files/<sha256>`; PG holds metadata; Procrastinate task `parse_file(file_id)` with 30-min lease + per-stage idempotency via `file_lifecycle` checkpoint; `Parser` Protocol + MIME/magic-bytes dispatcher; Docling parser for digital PDF; 5 endpoints (`POST /files` multipart-or-JSON · GET list · GET one · GET pages · DELETE soft). 15 decisions locked. End-to-end pipeline runnable on a PDF after 2a. Awaiting sign-off. | Aniket |
| 2026-05-23 | **Phase 2a G1 ✅ signed off. G2 drafted.** `api_contracts.md` §5 added (10 sub-sections): pipeline-model invariants (§5.1), file resource shape (§5.2 — `lifecycle_state` enum on wire + content_sha + mime_type + size_bytes; NO object_key/workspace_id on response), lifecycle history shape (§5.3 — append-only event array), raw-page shape (§5.4), POST /files with two modes (§5.5 — multipart OR JSON `{minio_object_key, name}` for tests + Phase 10a streaming upload), GET list (§5.6), GET one with lifecycle (§5.7), GET pages (§5.8), DELETE soft (§5.9), out-of-scope (§5.10). 2 new error slugs: `payload-too-large` (413 — > 100 MB) + `unsupported-media-type` (415 — 2a accepts only application/pdf; 2b adds the rest). Content-hash dedup returns `200 OK X-Dedup-Reason: content-hash` (NOT 409 — matches §5.1 #2 invariant + S3-style PUT semantics). Old §5 placeholder → §6, §6 changelog → §7. | Aniket |
| 2026-05-23 | **Phase 2a post-G2 cross-gate review (G1↔G2).** All 15 G1 decisions traced to §5: MinIO/PG split (§5.1 #1), content-hash dedup (§5.1 #2 + §5.5), state machine (§5.1 #3 + §5.2 enum + §5.5), file_lifecycle as event array (§5.3), raw_pages immutability (§5.1 #4 + §5.4), Procrastinate task enqueue (§5.5), worker workspace context (§5.1 #6), POST two modes (§5.5), idempotency two layers (§5.5 — both Idempotency-Key header + content-hash dedup), 100 MB limit + 1-500 name (§5.5 + §5.2), failure mode (§5.3 includes "failed" state + §5.10 retry endpoint deferred). Parser Protocol + Docling + RLS + parse_artifacts are internal implementations not exposed on wire. | Aniket |
| 2026-05-23 | **Phase 2a G2 ✅ signed off. G3 opens** — drafting `tests/specs/phase_2a.md` + 5 red skeleton files: `test_files_crud.py` (upload modes · dedup · 413/415 · soft delete · RLS) · `test_parse_dispatch.py` (Parser Protocol + registration + MIME routing) · `test_parse_pdf_docling.py` (Docling against a fixture PDF) · `test_raw_pages.py` (read endpoints + immutability via superuser INSERT block) · `test_files_lifecycle.py` (state machine transitions + append-only audit + per-stage idempotency). | Aniket |
| 2026-05-23 | **Phase 2a G3 drafted.** `tests/specs/phase_2a.md` covers the §5 surface with 28 tests across 5 new files: files_crud 10 (both POST modes · dedup-returns-200 · 413/415/400 · GET list/one + lifecycle · DELETE soft · RLS), parse_dispatch 5 (pure unit tests of Parser Protocol + registry + MIME/magic routing — no DB), parse_pdf_docling 3 (real Docling against tests/fixtures/tiny.pdf — fixture lands at G4), raw_pages 5 (GET pages while queued + after parse + pagination + 404 + DB-layer immutability via kb_app InsufficientPrivilege check), files_lifecycle 5 (state machine `null→queued→parsing→parsed` + failure event + idempotency on replay + DB-layer immutability). pytest --collect-only confirms 170 total. Awaiting sign-off. | Aniket |
| 2026-05-23 | **Phase 2a post-G3 cross-gate review (G1↔G2↔G3).** All 15 G1 decisions traced to ≥1 test: MinIO/PG split (test_post_creates_file_via_json_minio_key + no object_key on wire), content-hash dedup (test_post_content_hash_dedup_returns_existing — asserts 200 + X-Dedup-Reason), state machine (test_parse_task_transitions_queued_to_parsing_to_parsed), file_lifecycle audit (test_post_creates_initial_lifecycle_event), raw_pages immutability (test_raw_pages_table_rejects_update_via_kb_app — uses InsufficientPrivilege error to confirm REVOKE works), Procrastinate task (parse_file_impl direct-call pattern in raw_pages/lifecycle tests — bypasses queue for unit-level coverage), workspace context in worker (test_files_isolated_across_workspaces — worker reads file's workspace_id), POST two modes (separate Mode A + Mode B tests), idempotency two layers (test_post_requires_idempotency_key + test_post_content_hash_dedup_returns_existing — distinct paths), parser dispatcher (5 dedicated unit tests in test_parse_dispatch.py), Docling integration (3 dedicated tests in test_parse_pdf_docling.py against fixture), RLS (test_files_isolated_across_workspaces), upload validation (test_post_rejects_payload_too_large with KB_MAX_UPLOAD_BYTES env override + test_post_rejects_unsupported_mime + empty-name 422), failure mode (test_parse_task_failure_writes_failed_lifecycle_event — skeleton with G4 implementation note for Mode-B pre-stage). 2 new slugs (`payload-too-large` + `unsupported-media-type`) asserted. No cross-phase scope leak. | Aniket |
| 2026-05-23 | **Phase 2a G3 ✅ signed off. G4 opens.** Build order: (1) pyproject deps — `docling >= 2.0`, `python-magic`, `aiofiles`, optionally `python-multipart` if not already pulled by FastAPI; (2) `0008_parse_layer.sql` (4 tables + RLS + REVOKE UPDATE,DELETE on file_lifecycle + raw_pages); (3) `kb/parsers/__init__.py` (Parser Protocol + `ParserRegistry` + ParsedDocument/Page pydantic + `ParseError` + `NoParserForMime`); (4) `kb/parsers/docling_parser.py` (Docling integration; runs sync in worker thread); (5) `kb/storage/files.py` (MinIO put/get/key derivation); (6) `kb/domain/files.py` + `kb/domain/raw_pages.py`; (7) `kb/workers/tasks.py` (Procrastinate `parse_file` task wrapping `parse_file_impl`); (8) `kb/api/files.py` router + extend `kb/api/main.py` (mount + exception handlers for payload-too-large + unsupported-media-type); (9) `tests/fixtures/tiny.pdf` committed. | Aniket |
| 2026-05-23 | **Phase 2a G4 ✅ — code landed (commit `7800920`).** 8 new files + 5 mutated. All 28 Phase 2a tests pass; full suite 170/170 in 47.8s. **7 mid-G4 fixes captured by failing-test feedback** (all in the single G4 commit): (1) UploadFile imported from `starlette.datastructures` not fastapi — `fastapi.UploadFile` is a subclass; form returns the Starlette parent; isinstance against the subclass returns False. (2) Procrastinate App needs explicit `open_async()` before defer; added to FastAPI lifespan + conftest fixture (ASGITransport doesn't fire lifespan). (3) Procrastinate schema needs `procrastinate schema --apply` alongside our migrations — added to `db_migrated`. (4) Worker chicken-and-egg: RLS hides the file row before workspace context is set; switched worker DB connection to superuser URL (`settings.database_url`) for the initial lookup, then `SET LOCAL app.workspace_id` for downstream queries. (5) Config split: `KB_DB_URL` (kb_app override) and `KB_DATABASE_URL` (superuser override) — previously KB_DB_URL fed both, hiding (4). (6) `_LazyConninfoConnector` — Procrastinate connector reads env at `open_async()` not module-import (handles test fixture ordering). (7) Mac MPS doesn't support float64 (PyTorch limitation); Docling pinned to `AcceleratorDevice.CPU` — production Linux containers unaffected since they default to CPU or CUDA. All 15 G1 decisions traced. | Aniket |
| 2026-05-23 | **Phase 2a G5 ✅ — Docker stack verified end-to-end.** `scripts/verify_phase_2a.sh` 17/17 GREEN. Six additional cumulative Docker-stack fixes uncovered by running the actual compose stack (`239f362`): (a) Dockerfile system libs — `libxcb1 + libgl1 + libglib2.0-0 + libsm6 + libxext6 + libxrender1` for Docling's pillow/opencv image-decoding deps; without these, Docling errors with `libxcb.so.1: cannot open shared object file`. (b) `HF_HOME=/tmp/huggingface` + `XDG_CACHE_HOME=/tmp/cache` (pre-created `chown=kb:kb`) so non-root kb user can write HuggingFace model cache (~150 MB Docling layout/tableformer weights downloaded on first parse). (c) docker-compose db healthcheck switched to `pg_isready -h localhost` (TCP) — unix-socket-mode default was passing a few seconds before postgres bound to the TCP port, causing migrate to race against the gap. (d) api compose env gains `KB_DATABASE_URL` (superuser URL); Phase 2a's POST /files defers a Procrastinate task and Procrastinate needs the superuser URL to manage its own tables. (e) `scripts/bootstrap_db.sh` defensive retry loop with `psycopg.connect(connect_timeout=2)` — without the timeout each conn-refused attempt hung indefinitely, never exercising the 30× loop. (f) `bumped uv 0.5.0 → 0.9.7` (older uv silently hung on the docling-bearing lockfile) + pinned `torch + torchvision` to the pytorch-cpu PyPI index (saves ~3 GB of unused CUDA libraries; image went from ~5 GB → 571 MB). End-to-end pipeline verified: `POST /files (multipart PDF) → MinIO upload → Procrastinate task defer → worker container Docling parse (~3 min first run, models cached after) → raw_pages INSERT → file_lifecycle queued→parsing→parsed`. | Aniket |
| 2026-05-23 | **Phase 2a end-of-phase cross-phase sweep.** Re-ran all 5 verify scripts after the Docker/config changes to confirm no regression in Phase 0/1a/1b/1c. Scope-leak grep clean — no Phase 2b parsers (xlsx/email/Mistral OCR) leaked into 2a code; no `extracted_entities`/`lineage_path` (Phase 5/6); no rogue `audit_log` writes (Phase 9). RLS invariant grows from 7 → 11 workspace-scoped tables (audit_log, idempotency_keys, schemas, schema_versions, schema_entities, schema_fields, schema_relationships, files, file_lifecycle, raw_pages, parse_artifacts) — each has own `workspace_id` + own `CREATE POLICY`. pytest --collect-only confirms 170 tests. **Phase 2a complete; first worker phase + first real ML integration done. Ready to open PR.** | Aniket |
| 2026-05-23 | **Phase 2a merged.** PR #5 merged into `main` (merge commit `69690e7`). Tag `phase-2a-complete` pushed. Local `phase-2a/parse-scaffold` branch deleted. **First worker phase + first real ML integration complete.** | Aniket |
| 2026-05-23 | **Phase 2b G1 OPEN.** Branched `phase-2b/parse-formats` from `main`. Plan section §5.6 drafted: 3 parsers (xlsx via openpyxl, email via stdlib, Mistral OCR adapter mock-tested) registered into Phase 2a's `ParserRegistry`; `kb/api/files.py` mime whitelist widens to accept `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` + `application/vnd.ms-excel` + `message/rfc822`; magic-byte sniffer at upload picks parser when Content-Type is missing/octet-stream. No new HTTP endpoints. One `raw_pages` row per xlsx sheet; one per email (headers + body in text; attachments metadata-only in layout_json — recursive ingestion deferred). Mistral OCR registered AFTER Docling (currently inert at dispatch; activates when force-parser mechanism lands in Phase 2c). 13 decisions locked. Awaiting sign-off. | Aniket |
| 2026-05-23 | **Phase 2b G1 ✅ + G2 ✅ signed off (single drafting pass).** G1's 13 decisions are conservative + grounded in architecture line 419 (Mistral OCR for scanned PDF) — no contradictions surfaced. G2 is one contract delta in `api_contracts.md` §5.5: 415 row's narrative widens from "Phase 2a only `application/pdf`" to listing the 4 supported mimes + the magic-sniff fallback. No new endpoints, no new error slugs. Cross-gate G1↔G2 trace: decision #10 (xlsx mime whitelist) + #11 (email mime whitelist) + #6 (magic-byte sniffer) all map directly to the §5.5 narrative widening. **G3 opens** — drafting `tests/specs/phase_2b.md` + 3 new red skeleton files + 2 fixture files. | Aniket |
| 2026-05-23 | **Phase 2b G3 drafted.** `tests/specs/phase_2b.md` covers the §5.6 surface with 18 tests across 3 new files + additions: `test_parse_xlsx.py` 5 (one-page-per-sheet · TSV+sheet-header rendering · empty-sheet handling · layout shape · ZIP-magic detection), `test_parse_email.py` 5 (one-page · headers+body · attachment metadata · HTML-only body stripped · header-pattern magic), `test_parse_mistral_ocr.py` 5 (can_handle gated on KB_MISTRAL_API_KEY · mock-driven parse · per-page split · 4xx → ParseError), `test_files_crud.py` 3 additive (POST xlsx → 201 · POST eml → 201 · octet-stream + ZIP magic → xlsx). pytest --collect-only confirms 188 total (170 prior + 18 new). All Mistral tests use a mock HTTP client — zero real-API calls in CI. Awaiting sign-off. | Aniket |
| 2026-05-23 | **Phase 2b post-G3 cross-gate review (G1↔G2↔G3).** All 13 G1 decisions traced to ≥1 test: per-sheet pages (test_xlsx_parses_one_page_per_sheet), per-email page (test_email_parses_one_page), TSV rendering (test_xlsx_text_is_tsv_with_sheet_header), email rendering (test_email_text_includes_headers_and_body), attachment metadata (test_email_attachments_listed_in_layout_json), magic sniff (test_xlsx_can_handle_pk_zip_magic + test_email_magic_detection_via_header_pattern + test_post_octet_stream_xlsx_detected_via_magic), Mistral self-disables when no key (test_mistral_cannot_handle_when_api_key_absent), Mistral mock (test_mistral_parses_via_mock_response + test_mistral_returns_one_page_per_response_page + test_mistral_raises_parse_error_on_4xx), empty-content fallback (test_xlsx_handles_empty_sheet). Single new slug surface check: §5.5 415 narrative — no new slugs added (existing `unsupported-media-type` covers all rejected mimes). No cross-phase scope leak (no Phase 2c force-parser refs, no pptx, no Gemini VLM, no attachment-recursive-ingestion). | Aniket |
| 2026-05-23 | **Phase 2b G3 ✅ signed off. G4 opens.** Build order: (1) `kb/parsers/xlsx_parser.py` (openpyxl-driven; one page per sheet; TSV + sheet header text); (2) `kb/parsers/email_parser.py` (stdlib email.parser; multipart traversal; html.parser strip-tags fallback); (3) `kb/parsers/mistral_ocr_parser.py` (httpx-based adapter; constructor takes optional http_client for mock injection; can_handle gated on KB_MISTRAL_API_KEY); (4) extend `kb/parsers/__init__.py` `register_default_parsers()` to register the 3 new parsers (Docling first → xlsx → email → Mistral OCR); (5) widen `kb/api/files.py` `_PHASE_2A_WHITELIST` to set + add magic-byte sniff before mime check; (6) commit fixture bytes (`tiny.xlsx` ≈ 1 KB via openpyxl; `tiny.eml` + `tiny_with_attachment.eml` minimal RFC822). | Aniket |
| 2026-05-23 | **Phase 2b G4 ✅ — code landed (single commit `b5757da`).** All 18 new tests pass on first run; full suite 188/188 in 49.7s. **Zero in-G4 fixes** (Phase 2a's testing infra + parser Protocol meant the new parsers slot in cleanly). All 13 G1 decisions traced in commit body. 3 new parser modules (xlsx + email + mistral_ocr) + extension of `register_default_parsers()` + widened `_MIME_WHITELIST` in `kb/api/files.py` + `_sniff_mime_from_magic()` helper called from both `_handle_multipart` and `_handle_json` + 3 fixture files (`tiny.xlsx` 5 KB, `tiny.eml` 228 B, `tiny_with_attachment.eml` 413 B). | Aniket |
| 2026-05-23 | **Phase 2b G5 ✅ + cross-phase sweep running.** Authored `scripts/verify_phase_2b.sh` (15 checks): compose smoke + xlsx + email upload paths + worker parse to `lifecycle_state='parsed'` + xlsx page text starts with `# Sheet: Sheet1` + magic-byte sniff routing both ways (xlsx + email from octet-stream) + Mistral OCR inert (PDF still wins by Docling first in dispatch order) + text/plain still 415 + Phase-2b pytest. Full E2E pipeline verified for both new formats: `POST tiny.xlsx → MinIO → parse_file → openpyxl → 2 raw_pages (one per sheet)`; `POST tiny.eml → MinIO → parse_file → email.message_from_bytes → 1 raw_page (headers + body)`. **Phase 2b complete; cross-phase sweep verifies Phase 0/1a/1b/1c/2a still pass.** | Aniket |
| 2026-05-23 | **Phase 2b merged.** PR #6 squash-merged into `main` (merge commit `971a019`). Local fast-forward sync confirmed. **Phase 2 (a/b) closed.** Phase 2c (force-parser route + real Mistral activation) parked as a tracked deferral — will land as an additive PR when a Mistral API key is procured + a real scanned-PDF eval set is ready. | Aniket |
| 2026-05-23 | **Phase 3 split into 3a + 3b + 3c** per [`feedback_sub_phase_splits`](../../.claude/memory/feedback_sub_phase_splits.md). Architecture §5 step 6–10 lists four conceptual deliverables (late chunking, contextual prefix, embedding, RAPTOR build) — each end-to-end testable on its own. 3a = chunking only (no LLM, no embedding); 3b = Contextual Retrieval Anthropic prefix call; 3c = embeddings + RAPTOR tree (first embedding call). Each gets its own G1→G5 cycle but all three live on the same `phase-3/chunking-raptor` branch (no inter-PR dependency since each commit-set advances the same lifecycle state machine — opening 3 separate PRs is unnecessary friction). HNSW + BM25 index creation explicitly stays in Phase 4 per architecture §5 step 8–9. | Aniket |
| 2026-05-23 | **Phase 3a G1 OPEN.** Branched `phase-3/chunking-raptor` from `main`. Plan section §5.7 drafted: `0009_chunks.sql` adds workspace-scoped + RLS-day-1 + immutable `chunks` table; pure-function `chunk_pages(raw_pages, budget=2500, overlap=250)` using tiktoken `cl100k_base`; layout-aware (raw_page as default boundary, paragraph-break splits for over-budget pages, row-boundary splits for huge xlsx sheets); small-page joining when page < budget/4; new lifecycle state `chunked`; worker stage `chunk_file_impl` chained from `parse_file_impl`'s success path via separate-tx defer. 12 decisions locked. Out of scope: contextual prefix LLM (3b), embeddings + RAPTOR (3c), HNSW + BM25 indexes (Phase 4), force-rechunk admin endpoint (Phase 4), atomic-unit-aware chunking (Phase 5), Jina-style true late chunking (Wave B). Awaiting sign-off. | Aniket |
| 2026-05-23 | **Phase 3a G1 ✅ + G2 ✅ signed off (single drafting pass).** G1's 12 decisions are conservative + grounded in architecture §5 step 6 + Anthropic Contextual Retrieval writeup recommendations (chunk size ≥1500 tokens for recall, 10% overlap as industry default). G2 is one contract delta in `api_contracts.md` §5.1 invariant #3 + §5.2 file-shape enum: `lifecycle_state` widens from `queued/parsing/parsed/failed/deleted` to add `chunked`. Forward-compat pattern locked: each sub-phase appends exactly one new state. No new endpoints; no new error slugs. Cross-gate G1↔G2 trace: decision #8 (lifecycle state addition) maps directly to §5.2 enum widening; decision #9 (task chaining via separate-tx defer) is invisible on the wire (only observable as the state transition itself). **G3 opens** — drafting `tests/specs/phase_3a.md` + 2 new red skeleton files. | Aniket |
| 2026-05-23 | **Phase 3a G3 drafted.** `tests/specs/phase_3a.md` covers the §5.7 surface with 16 tests across 2 new files: `test_chunking_unit.py` 9 (single-short-page → 1 chunk · over-budget split at paragraph break · small-page joining · source_page_numbers tracks all contributors · chunk_index monotonic · overlap preserves tail · xlsx huge-sheet row-boundary preservation · empty input raises ChunkingError · content_sha = sha256(text) invariant), `test_chunking_worker.py` 7 (parsed→chunked transition · `chunking_done` lifecycle event with payload · idempotency on already-chunked · empty raw_pages marks failed · parse_file chains chunk_file via defer · REVOKE UPDATE on kb_app · RLS workspace isolation). pytest --collect-only confirms 204 total (188 prior + 16 new). Awaiting sign-off. | Aniket |
| 2026-05-23 | **Phase 3a post-G3 cross-gate review (G1↔G2↔G3).** All 12 G1 decisions traced to ≥1 G3 test: #1 budget enforced via the over-budget split test; #2 overlap via `test_chunk_pages_overlap_preserves_tail_of_prior_chunk`; #3 tokenizer implicit via budget assertions across all tests; #4 layout-aware boundary via single-short-page-stays-one-chunk; #5 small-page joining via `test_chunk_pages_small_pages_join_until_budget`; #6 source_page_numbers via dedicated test; #7 REVOKE UPDATE via `test_chunks_table_rejects_update_via_kb_app`; #8 lifecycle widening via `test_chunk_file_impl_reads_raw_pages_and_writes_chunks` (asserts lifecycle_state=='chunked'); #9 task chaining via `test_parse_file_impl_chains_chunk_file_via_defer`; #10 idempotency via `test_chunk_file_impl_is_idempotent_on_already_chunked`; #11 empty-input via `test_chunk_file_impl_empty_raw_pages_marks_failed`; #12 row-boundary via `test_chunk_pages_xlsx_huge_sheet_splits_on_row_boundary`. No scope leak (no embedding/LLM/RAPTOR/HNSW/BM25 refs in test sources). **G3 ✅ signed off. G4 opens.** | Aniket |
| 2026-05-23 | **Phase 3a G4 ✅ — code landed (single commit).** 5 new files + 3 mutated. All 16 new tests pass on first run; full suite 204/204 in 56.4s. **Zero in-G4 fixes** (Phase 2a's task infrastructure + Phase 2b's parser Protocol pattern meant the chunker slots in cleanly — second consecutive G4 with no rework). Files: `migrations/sql/0009_chunks.sql` (ALTER files CHECK + CREATE TABLE chunks + RLS + REVOKE UPDATE/DELETE + UNIQUE (file_id, chunk_index)); `src/kb/chunking/__init__.py` (Chunker pure-fn + ChunkingError + tiktoken cl100k_base + small-page joining + paragraph-/row-boundary back-off splitter); `src/kb/domain/chunks.py` (insert_chunk + count_chunks_for_file + read_pages_for_chunking); `src/kb/workers/tasks.py` MUTATED (chunk_file_impl + chunk_file Procrastinate task + parse_file_impl chained-defer in separate tx; _mark_failed generalised with from_state + event kwargs); `src/kb/config.py` MUTATED (chunk_tokens + chunk_overlap_tokens settings); `pyproject.toml` MUTATED (tiktoken>=0.8.0 dep). All 12 G1 decisions traced in implementation. | Aniket |
| 2026-05-23 | **Phase 3a G5 ✅ + cross-phase sweep complete.** Authored `scripts/verify_phase_3a.sh` (18 checks): compose smoke + 4 DDL assertions (chunks table + UNIQUE constraint + RLS forced + kb_app grants restricted + lifecycle CHECK widened) + 4 PDF/xlsx/email E2E parse-to-`chunked` flows + chunks-row + source_page_numbers + lifecycle history string assertion + chunked-event idempotent re-defer + Phase-3a pytest 16. **Two in-G5 fixes in the verify script** (psql booleans print as `true`/`false`, not `t`/`f` — corrected the string matchers). **Cross-phase sweep** ran all 6 prior verify scripts: Phase 0 16/16 · 1a 17/17 · 1b 21/21 · 1c 20/20 · 2a 17/17 · 2b 15/15 (124/124 cumulative). One ANTICIPATED regression caught + fixed in the same commit: Phase 2a + 2b's verify scripts polled for `lifecycle_state == 'parsed'` as their success condition, but Phase 3a's chained defer races past `parsed → chunked` within ~1s, so the polls timed out. Fixed by widening the accept-set: `parsed | chunked | contextualized | ready` all count as parse-success (forward-compat for 3b + 3c). Also widened Phase 2a's "queued→parsing→parsed exact sequence" assertion to a "starts with that prefix" check (chunked event now appended). **Phase 3a complete; first Phase 3 sub-phase shipped.** | Aniket |
| 2026-05-23 | **Phase 3b G1 OPEN.** Same `phase-3/chunking-raptor` branch (second commit-set). Plan section §5.8 drafted using the `claude-api` skill's prompt-caching guidance + Anthropic's Contextual Retrieval cookbook: `0010_contextual_chunks.sql` adds workspace-scoped + RLS-day-1 + immutable `contextual_chunks` table (denormalized `contextual_text = prefix + chunk_text` for index efficiency; persists `cache_creation_input_tokens` + `cache_read_input_tokens` columns for post-hoc cache-rate auditing); `Contextualizer` Protocol with `AnthropicContextualizer` (`claude-opus-4-7` default per skill mandate, configurable via `KB_CONTEXTUAL_MODEL`) + `IdentityContextualizer` fallback when `KB_ANTHROPIC_API_KEY` is unset (pipeline still completes at "no contextual retrieval" recall baseline so downstream phases stay unblocked); `asyncio.Semaphore(8)` concurrency cap; single `cache_control: {ephemeral}` breakpoint on the system block holding the doc context; prefix prompt verbatim from Anthropic's [Contextual Retrieval cookbook](https://github.com/anthropics/anthropic-cookbook/tree/main/skills/contextual-embeddings); new lifecycle state `contextualized`; worker stage `contextualize_file_impl` chained from `chunk_file_impl` via separate-tx defer (same pattern as 3a). 14 decisions locked. Out of scope: embeddings + RAPTOR (3c), HNSW + BM25 indexes (Phase 4), audit_log writes (Phase 9), Hydra/OmegaConf config layering (Phase 5). Awaiting sign-off. | Aniket |
| 2026-05-23 | **Phase 3b G1 ✅ + G2 ✅ signed off (single drafting pass — third consecutive sub-phase with this rhythm).** G1's 14 decisions are conservative and traceable: #1 model choice to `claude-api` skill mandate (`claude-opus-4-7` default, `KB_CONTEXTUAL_MODEL` user override); #2 prompt-cache placement to `shared/prompt-caching.md` (system block + single ephemeral breakpoint); #4 concurrency cap to Anthropic tier-1 RPM/ITPM headroom; #6 IdentityContextualizer fallback to operational pragmatism (pipeline-completes-without-key beats pipeline-blocks); #7 prefix prompt verbatim from Anthropic's Contextual Retrieval cookbook (proven recipe, no eval deviation); #10 immutability matches Phase 3a chunks + raw_pages; #12 forward-compat enum convention; #13 chained-defer matches 3a's parse-to-chunk shape. G2 is one contract delta in `api_contracts.md` §5.1 #3 + §5.2 file-shape enum: `lifecycle_state` widens to include `contextualized`. **G3 opens** — drafting `tests/specs/phase_3b.md` + 2 new red skeleton files (~15 tests: 9 unit on AnthropicContextualizer with mock client + Identity fallback + 6 worker integration through testcontainers). | Aniket |
| 2026-05-23 | **Phase 3b G3 ✅ drafted + signed off.** `tests/specs/phase_3b.md` covers the §5.8 surface with 15 tests across 2 new files: `test_contextualization_unit.py` 9 (request shape — system+cache_control+user role split · chunk in user message · response parsing · cache metrics from `usage.cache_*_input_tokens` · IdentityContextualizer empty-prefix + `model_id='identity'` · factory returns Identity when KB_ANTHROPIC_API_KEY unset · 4xx → ContextualizationError · `thinking={'type':'disabled'}` in request · `KB_CONTEXTUAL_MODEL` override), `test_contextualization_worker.py` 6 (chunked→contextualized transition · `contextualization_done` event with cache totals + model_id in payload · idempotency · chunk_file chains contextualize_file via defer · IdentityContextualizer fallback path · REVOKE UPDATE on kb_app). pytest --collect-only confirms 219 total (204 prior + 15 new). All 14 G1 decisions traced to ≥1 test; no scope leak (no embedding/RAPTOR/HNSW/BM25 refs in test sources). **G4 opens.** | Aniket |
| 2026-05-23 | **Phase 3b G4 ✅ — code landed.** 4 new files + 3 mutated. All 15 new tests + full suite 219/219 in 65.7s. **Two in-G4 fixes**: (1) `test_anthropic_contextualizer_4xx_raises_contextualization_error` constructed `anthropic.APIStatusError` with a hand-built response object missing `.request`; the SDK's `__init__` reads `response.request` and 400'd with AttributeError. Fixed by using a real `httpx.Response` + `httpx.Request` pair so the SDK sees the shape it expects. (2) `test_runner_bootstraps_schema_migrations_on_empty_db` failed on cross-test data pollution — Phase 3b worker tests advance files rows to `lifecycle_state='contextualized'`; when the migrations test later re-runs 0001-0010 from scratch, 0009's CHECK constraint (which didn't include `contextualized`) rejected those existing rows. Fixed by making 0009's CHECK forward-compatible (lists every lifecycle value through Phase 3c: `queued/parsing/parsed/chunked/contextualized/ready/failed/deleted`) and simplifying 0010 to no longer ALTER the CHECK (just CREATE TABLE contextual_chunks). Convention locked: each lifecycle-extending migration writes a CHECK that includes all currently-planned future states; the wire enum still grows one state per sub-phase. Files: `pyproject.toml` (anthropic>=0.40.0 dep — resolved as 0.104.1); `migrations/sql/0009_chunks.sql` MUTATED (forward-compat CHECK); `migrations/sql/0010_contextual_chunks.sql` (CREATE TABLE only); `src/kb/contextualization/__init__.py` (AnthropicContextualizer + IdentityContextualizer + ContextualizedChunk + ContextualizationError + make_contextualizer factory); `src/kb/domain/contextual_chunks.py` (insert_contextual_chunk + read_chunks_for_contextualization + read_doc_text); `src/kb/workers/tasks.py` MUTATED (contextualize_file_impl + contextualize_file Procrastinate task + chunk_file_impl chained-defer). All 14 G1 decisions traced. | Aniket |
| 2026-05-23 | **Phase 3b G5 ✅ + cross-phase sweep complete.** Authored `scripts/verify_phase_3b.sh` (15 checks): compose smoke + 4 DDL assertions (contextual_chunks table + UNIQUE on chunk_id + RLS forced + kb_app grants restricted + lifecycle CHECK includes contextualized) + E2E PDF upload → parse → chunk → contextualize (`model_id='identity'` via IdentityContextualizer since `KB_ANTHROPIC_API_KEY` unset in compose) + assertion that `contextual_text == chunk text` for identity-fallback path + lifecycle progression string match + idempotent re-defer (one contextualization_done event) + Phase-3b pytest 15. **Zero in-G5 fixes** — verify script ran clean first try. **Cross-phase sweep** ran all 7 prior verify scripts: Phase 0 16/16 · 1a 17/17 · 1b 21/21 · 1c 20/20 · 2a 17/17 · 2b 15/15 · 3a 18/18 · 3b 15/15 (139/139 cumulative). One ANTICIPATED regression caught + fixed in the G4 commit: Phase 3a's verify_phase_3a.sh polled for `lifecycle_state == 'chunked'`, but Phase 3b's chained defer races past `chunked → contextualized` within ~1s of chunk completion. Fixed by widening the accept-set: `chunked | contextualized | ready` all count as chunk-success (forward-compat for 3c). **Phase 3b complete; second Phase 3 sub-phase shipped.** | Aniket |
| 2026-05-23 | **Phase 3 split refined: 3c → 3c (embedding) + 3d (RAPTOR build).** Architecture §12's Phase 3 listed three pieces; we shipped 3a (chunking) + 3b (contextual retrieval) and now split the remaining piece into two sub-phases per the sub-phase-splits convention (each end-to-end testable; RAPTOR's algorithmic complexity deserves its own G4 debug surface). 3c covers Gemini Embedding 001 calls + chunk_embeddings table (lifecycle: contextualized → embedded). 3d covers per-doc recursive cluster→summarize→re-embed → raptor_nodes + raptor_edges (lifecycle: embedded → ready, the terminal state). Each adds exactly one new lifecycle value, preserving the forward-compat convention. | Aniket |
| 2026-05-23 | **Phase 3c G1 ✅ + G2 ✅ signed off (single drafting pass — fourth consecutive sub-phase with this rhythm).** G1's 13 decisions are conservative + traceable: #1 model `gemini-embedding-001` per architecture §8 (with `KB_EMBEDDING_MODEL` env override); #2 `halfvec(3072)` storage (pgvector's float16 variant — Phase 4's HNSW supports it natively); #3 `Embedder` Protocol with `GeminiEmbedder` + `DeterministicMockEmbedder` + `make_embedder()` factory keyed on `KB_GEMINI_API_KEY` (mirror of 3b's contextualizer adapter shape); #4 self-disable to mock when no API key — pipeline-completes-without-key beats blocking, alarm on `model_id='mock-deterministic-v1'` in prod; #5 mock embedder uses `sha256(text || ":" || dim_index)` for deterministic L2-normalized vectors so Phase 3d clustering tests can assert cluster shape; #8 immutability via REVOKE UPDATE/DELETE; #9 UNIQUE `(contextual_chunk_id, model_id)` allows safe model upgrades; #10 lifecycle widens to add `embedded` (CHECK already covers it via 0009's forward-compat widening locked at 3b G4 fix #2); #11 separate-tx chained defer matches 3a→3b shape; #13 API errors → `contextualized→failed`. G2 is one contract delta in `api_contracts.md` §5.1 #3 + §5.2 enum row. **G3 opens** — drafting `tests/specs/phase_3c.md` + 2 red skeleton files (~13 tests: 7 unit on Embedder adapter + 6 worker integration). | Aniket |
| 2026-05-23 | **Phase 3c G3 ✅ drafted + signed off.** `tests/specs/phase_3c.md` covers the §5.9 surface with 13 tests across 2 new files: `test_embeddings_unit.py` 7 (GeminiEmbedder request shape with mock SDK · DeterministicMockEmbedder reproducibility across calls · vector dim=3072 · L2 unit-norm · model_id='mock-deterministic-v1' · factory selects mock when KB_GEMINI_API_KEY unset · embed_batch returns 1:1), `test_embeddings_worker.py` 6 (contextualized→embedded transition · embedding_done event with dim+model_id payload · idempotency · contextualize_file chains embed_file via defer · mock-fallback path · REVOKE UPDATE on kb_app). pytest --collect-only confirms 232 total (219 prior + 13 new). All 13 G1 decisions traced to ≥1 test; no scope leak (no raptor_nodes/clustering/HNSW/BM25 refs). **G4 opens.** | Aniket |
| 2026-05-23 | **Phase 3c G4 ✅ — code landed.** 4 new files + 3 mutated. All 13 new tests + full suite 232/232 in 70.3s. **One in-G4 fix**: 0009's forward-compat CHECK list (added at 3b G4 fix #2) included `'ready'` but skipped the in-between `'embedded'` state. Insert into `files` of `lifecycle_state='embedded'` 400'd with CheckViolation. Fixed by extending the CHECK list to include every state through the terminal `ready`: `queued/parsing/parsed/chunked/contextualized/embedded/ready/failed/deleted`. Convention reinforced: every lifecycle-extending migration writes a CHECK with all currently-planned future states. Files: `pyproject.toml` + `uv.lock` (google-genai>=0.3.0 — resolved as 2.6.0); `migrations/sql/0009_chunks.sql` MUTATED (added `'embedded'` to CHECK); `migrations/sql/0011_chunk_embeddings.sql` (CREATE TABLE with halfvec(3072) + RLS + REVOKE + UNIQUE); `src/kb/embeddings/__init__.py` (Embedder Protocol + GeminiEmbedder via google-genai.aio.models.embed_content + DeterministicMockEmbedder using sha256(text||':'||dim) + L2-normalize + make_embedder factory); `src/kb/domain/chunk_embeddings.py` (insert_chunk_embedding with halfvec literal cast + read_contextual_chunks_for_embedding); `src/kb/workers/tasks.py` MUTATED (embed_file_impl + embed_file Procrastinate task + contextualize_file_impl chained-defer). All 13 G1 decisions traced. | Aniket |
| 2026-05-23 | **Phase 3c G5 🟡 — verify script authored, Docker-stack run blocked on host disk pressure.** Authored `scripts/verify_phase_3c.sh` (15 checks): compose smoke + 5 DDL assertions (chunk_embeddings table + UNIQUE + RLS forced + kb_app grants restricted + `halfvec` column type + lifecycle CHECK includes `embedded`) + E2E PDF parse → chunk → contextualize → embed (with `model_id='mock-deterministic-v1'` via DeterministicMockEmbedder since `KB_GEMINI_API_KEY` is unset in compose) + lifecycle progression assert + idempotent re-defer + Phase-3c pytest 13. Also widened Phase 3b's verify accept-set: `contextualized | embedded | ready` all count as contextualize-success (forward-compat for 3d). **Docker-stack execution deferred** — host disk hit 88% / ~2 GB free during 3c development; OrbStack daemon stopped. pytest suite remains authoritative + green at 232/232; the Docker-stack run validates ops-stack behavior but does not change the merge bar (Phase 3a + 3b shipped under the same gate). **Action for user**: free ~5-10 GB → restart Docker (OrbStack) → run `./scripts/verify_phase_3c.sh` + cross-phase sweep → flip G5 to ✅. | Aniket |
| 2026-05-23 | **Phase 3c G5 ✅ — disk reclaimed, full sweep green.** User freed disk (76 GB free, up from 2 GB); OrbStack restarted (Docker 29.4.0). `./scripts/verify_phase_3c.sh` returned 15/15. Cross-phase sweep across all 9 verify scripts (0/1a/1b/1c/2a/2b/3a/3b/3c): 8/9 GREEN on first pass; 3a tripped 3 checks with `last state: embedded` instead of `chunked` — same forward-compat race that 3b's accept-set already handled. Fix: widened 3a's accept-set so `chunked | contextualized | embedded | ready` all count as chunking-success (with comment updated to cite Phase 3b/3c chain). Re-ran 3a → 18/18. Final sweep: **0:16/16 · 1a:17/17 · 1b:21/21 · 1c:20/20 · 2a:17/17 · 2b:15/15 · 3a:18/18 · 3b:15/15 · 3c:15/15 — all GREEN**. Convention reinforced (matches 0009 CHECK convention): every accept-set in a verify script writes all currently-planned future states. Phase 3c officially shipped. | Aniket |
| 2026-05-24 | **🎉 Phase 3e G5 ✅ — shipped. Wave A FULLY COMPLETE.** New `scripts/verify_phase_3e.sh` (13 checks): compose smoke + umap-learn worker import probe + empty-workspace pre-flight (400 corpus-rebuild-no-input) + 5-doc upload + wait-for-all-ready + POST /corpus/raptor/rebuild → 202 + wait for raptor_build_corpus job to succeed + scope='corpus' raptor_nodes exist + corpus → contextual_chunks discriminated edges (singleton doc-roots) + atomic rebuild count-stable on re-trigger + Phase-3e pytest 11. Standalone first run: 13/13 GREEN. **Cross-phase sweep across ALL 12 verify scripts**: **12/12 GREEN on first pass — no regressions, no forward-compat fixes needed.** 3e doesn't change any file lifecycle states (corpus tree is workspace-scoped, not file-scoped), so the 0009 lifecycle CHECK + every upstream accept-set remains correct. Final sweep totals: **0:16 · 1a:17 · 1b:21 · 1c:20 · 2a:17 · 2b:15 · 2c:15 · 3a:18 · 3b:16 · 3c:15 · 3d:22 · 3e:13 = 205 checks total**. Branch `phase-3/chunking-raptor` now carries 7 commit-sets (3a → 3b → 3c → 3b-bis → 2c → 3d → 3e) ready to merge. **Architecture line 41 promise — "RAPTOR builds the hierarchy of what's there" — is now backed by code at 100K-doc scale.** Wave A delivers: 5 parser adapters (Docling/openpyxl/email/GeminiOCR/MistralOCR) with strategy-aware dispatch + provenance, 3 LLM adapter providers per stage (Anthropic/Gemini/Identity-fallback) via factory selectors, full ingestion lifecycle through `ready`, per-doc + corpus-level RAPTOR trees. Open for Phase 4: retrieval (HNSW + BM25 + tree-aware query). | Aniket |
| 2026-05-24 | **Phase 3e G4 ✅ — code landed.** 2 new modules + 4 mutations. `src/kb/raptor/corpus.py` (~190 LOC: cluster_embeddings_corpus via UMAP→GaussianMixture with random_state=42 + soft fall-back to GMM-only when N too small for UMAP + read_doc_roots_for_workspace heterogeneous reader returning `(id, text, vec, kind)` with kind ∈ {'node','chunk'} + delete_corpus_rows_for_workspace for atomic rebuild). `src/kb/api/corpus.py` (~85 LOC: POST /corpus/raptor/rebuild with 2 pre-flight checks — empty workspace → 400, in-flight job → 503). Worker mutations: raptor_build_corpus_impl (read-only Phase 1 → in-memory build Phase 2 with discriminated-FK-aware edge staging → atomic DELETE+INSERT Phase 3) + raptor_build_corpus Procrastinate task (explicit-trigger only, not chained from any file event). main.py mounts corpus router + 2 new exception handlers. errors.py adds CorpusRebuildNoInputError + CorpusRebuildInFlightError. .env.example adds 3 UMAP/GMM tuning vars. New deps: umap-learn>=0.5.12, pynndescent, numba, llvmlite. Two in-G4 fixes: missing `import math`/`import os` inside function body; corpus worker tests needed explicit `KB_DATABASE_URL` env + `get_settings.cache_clear()`. Suite 286/286 in 81s. | Aniket |
| 2026-05-24 | **Phase 3e G3 ✅ — spec + 11 red skeletons.** Spec at `tests/specs/phase_3e.md`. 11 red: 4 corpus unit (cluster + branching + determinism + heterogeneous reader) + 4 corpus worker (cross-scope edges + atomic rebuild + N≤1 skip + deterministic structural shape across rebuilds) + 3 API (202 happy path + 400 empty-workspace + 503 in-flight). Worker tests use fabricated SQL seeds (10 mixed multi-leaf + singleton doc-roots) to avoid driving the full upstream chain for 10 files. 275/275 + 11 RED, no collateral damage. | Aniket |
| 2026-05-24 | **Phase 3e G1 ✅ + G2 ✅ — plan + api_contracts §6 landed.** 15 G1 decisions at §5.10.1. Algorithm: UMAP+GaussianMixture per the paper (AC's O(N²) infeasible at N=100K). Heterogeneous doc-roots via discriminated edge FK from 3d. Explicit `POST /corpus/raptor/rebuild` trigger only. Atomic rebuild (DELETE-all + INSERT-new in one tx). Deterministic via random_state=42 for retrieval-citation stability. NO migration — 3d's 0012 already locked scope enum + nullable file_id. Open in Wave A; admin RBAC → Phase 9. G2 added new `## 6. Phase 3e — Corpus RAPTOR` to api_contracts.md (model invariants, single endpoint with 400+503 pre-flight errors, out-of-scope deferrals). Renumbered old §6→§7, §7→§8. | Aniket |
| 2026-05-24 | **Phase 3e G1 🟡 OPEN — corpus-level RAPTOR plan drafted (initial draft, superseded by signed-off entry above).** Final Wave A phase; closes the architecture's "RAPTOR builds the corpus hierarchy" promise (line 41). 15 decisions locked at §5.10.1. Key choices: (1) **UMAP + sklearn GaussianMixture** for clustering (paper's algorithm — AC is O(N²) infeasible at N=100K corpus roots); UMAP reduces 3072→10 dim, GMM soft-clusters in low-dim. (2) Heterogeneous doc-root source: multi-leaf files contribute their per-doc raptor root; singleton-leaf files contribute their `contextual_chunks` row directly. The discriminated edge FK landed at 3d (decision #10) was deliberately designed for this case — corpus L2 edges can point at either `raptor_nodes` IDs or `contextual_chunks` IDs depending on root_kind. (3) Explicit `POST /corpus/raptor/rebuild` trigger only — NOT auto-on-upload (at 100K-doc scale, per-upload rebuild would melt the worker pool). (4) Atomic rebuild via DELETE-all + INSERT-new in one tx — stale-but-consistent corpus tree beats partial. (5) Open endpoint in Wave A per user direction; admin RBAC deferred to Phase 9. (10) Determinism via `random_state=42` for both UMAP + GMM — required so retrieval citations are stable across rebuilds with no new docs. **No migration** — 3d's 0012 already locked `scope` enum + nullable `file_id`. New deps: `umap-learn>=0.5.7` + its `pynndescent` dep. Repo delta: new `kb/raptor/corpus.py` + new `kb/api/corpus.py` router + mutated `kb/workers/tasks.py` + 3 new test files + new spec. Endpoint contract: new `## 6. Phase 3e — Corpus RAPTOR` section in `api_contracts.md` (since corpus isn't structurally a "file" endpoint). Out of scope: `GET /corpus/raptor` navigation endpoint (Phase 8+), status polling (Phase 9), incremental updates (Phase 5+), admin gating (Phase 9), HNSW indexes (Phase 4). §5 table 3e row flips ⬜→🟡. Estimated ~4-6 hr G3+G4+G5. Awaiting Aniket sign-off. | Aniket |
| 2026-05-24 | **Phase 3d G5 ✅ — shipped. Wave A ingestion side complete.** New `scripts/verify_phase_3d.sh` (22 checks): compose smoke + 7 DDL assertions (raptor_nodes + raptor_edges RLS forced + halfvec(3072) embedding column + scope CHECK + nullable file_id + discriminated edge CHECK + REVOKE UPDATE/DELETE + `files.lifecycle_state` CHECK includes `raptor_building`) + E2E PDF parse → chunk → contextualize → embed → raptor_building → ready + lifecycle progression (`embedded→raptor_building→ready`) + raptor_build_started + raptor_build_done events + raptor_nodes L2 row assertion (gated on `leaf_count >= 2` since tiny.xlsx is singleton — pytest worker tests cover the multi-leaf case with N=5 fabricated leaves) + L2→contextual_chunks edge assertion (gated same way) + payload shape (leaf_count, levels_built, summarizer_model_id, embedder_model_id) + idempotent re-defer + Phase-3d pytest 17. Standalone first run: 22/22 GREEN. **Cross-phase sweep across all 11 verify scripts**: 10/11 GREEN first pass; 3c regressed at step 10 with `last state: ready` instead of `embedded` — same forward-compat race that 3a/3b handled earlier (3a widened when 3c shipped; 3b widened when 3c shipped; now 3c widens because 3d shipped). Fix: widened 3c's accept-set to `embedded \| raptor_building \| ready` (matches the 0009 CHECK convention from 3b G4 fix #2 — every accept-set writes all currently-planned future states). Re-ran 3c → 15/15. **Final sweep totals: 0:16 · 1a:17 · 1b:21 · 1c:20 · 2a:17 · 2b:15 · 2c:15 · 3a:18 · 3b:16 · 3c:15 · 3d:22 — 192 total**. Branch `phase-3/chunking-raptor` now carries 6 commit-sets (3a/3b/3c/3b-bis/2c/3d) ready to merge or extend with Phase 3e (corpus-level). | Aniket |
| 2026-05-24 | **Phase 3d G4 ✅ — code landed.** 5 new modules + 2 mutations. `migrations/sql/0012_raptor.sql` (raptor_nodes table with scope/file_id forward-compat columns + raptor_edges with discriminated child FK CHECK + lifecycle CHECK widens with `raptor_building`). `src/kb/summarization/__init__.py` (~280 LOC: Summarizer Protocol + GeminiSummarizer + AnthropicSummarizer + IdentitySummarizer + `make_summarizer()` 4-value `KB_SUMMARIZER` factory mirroring 3b-bis's KB_CONTEXTUALIZER pattern). `src/kb/raptor/__init__.py` (~160 LOC: AC-cosine `cluster_embeddings` + `_build_in_memory` test-injectable orchestrator). `src/kb/domain/raptor.py` (~150 LOC: RaptorNode pydantic + insert_raptor_node with ON CONFLICT re-fetch + insert_raptor_edge with discriminated dispatch + read_leaves_for_raptor_build). `src/kb/workers/tasks.py` MUTATED: `raptor_build_file_impl` builds tree in memory then flushes all nodes+edges in one atomic tx (partial failures roll back, decision #14) + chained defer from `embed_file_impl` success path in separate tx (decision #13) + `raptor_build_file` Procrastinate task. `.env.example` adds `KB_SUMMARIZER=auto` + 4 commented overrides. **Three in-G4 fixes**: (1) Initial 0012 migration had `PRIMARY KEY (parent_node_id, child_node_id, child_contextual_chunk_id)` which implicitly NOT-NULL's the child columns — breaks discriminated-FK design. Replaced with synthetic `id uuid PK`. (2) test_raptor_worker.py originally drove `tiny.pdf` through the real parse→chunk pipeline → only 1 chunk → singleton-tree case → worker correctly produces 0 nodes → assertions fail. Refactored `_post_parse_chunk_contextualize_embed` to seed N=5 fabricated chunks/contextual_chunks/chunk_embeddings via direct SQL + jump lifecycle to `embedded` (faster + isolated + exercises clustering). (3) Two tests (`test_raptor_build_writes_raptor_build_done_lifecycle_event`, `test_raptor_build_failure_writes_failed_event`) were missing `db_url_superuser` fixture in their signatures — added. New dep: `scikit-learn>=1.8.0`. Suite: 275/275 in 66s (258 prior + 17 new). All 16 G1 decisions traced. | Aniket |
| 2026-05-24 | **Phase 3d G2 ✅ — api_contracts §5.2 + §5.3 lifecycle deltas.** §5.2 `lifecycle_state` enum widens to include `raptor_building` + reframes `ready` as 3d's terminal state. §5.3 lifecycle history example annotated with all post-2c stage transitions (chunking_done, contextualization_done, embedding_done, raptor_build_started, raptor_build_done) + payload shapes per stage + failure-event convention noted explicitly. | Aniket |
| 2026-05-24 | **Phase 3d G3 ✅ — spec + 17 red skeletons.** Spec at `tests/specs/phase_3d.md`. 17 red across 3 new files: `tests/test_raptor_unit.py` (6 pure-function tests on `cluster_embeddings` + tree termination), `tests/test_summarization_unit.py` (6 adapter tests using `_MockGeminiClient` mirroring 3b-bis), `tests/test_raptor_worker.py` (5 testcontainers integration tests). Coverage hits all 16 G1 decisions. Suite: 258/258 + 17 RED (no collateral damage). | Aniket |
| 2026-05-24 | **Phase 3d G1 ✅ signed off after open-source-scale deliberation; split into 3d (per-doc) + 3e (corpus).** Initial G1 draft locked 15 decisions for per-doc + assumed corpus-level was Phase 5+ deferral. Pressure-test pass via the deliberate-decision skill flagged three issues; user response ("open source, 100K-doc scale") clarified that corpus-level isn't deferrable. **Three deliberation flips landed in the revised plan:** (1) Decision #9 — L1 leaves stay in `contextual_chunks` instead of denormalizing into `raptor_nodes`. Math: 6 KB/leaf × 5M leaves at 100K-doc scale = 30 GB of duplicated embeddings, plus larger HNSW indexes in Phase 4. Discriminated edge FK (`raptor_edges.child_node_id NULL` + `child_contextual_chunk_id NULL` with `(child_node_id IS NOT NULL) <> (child_contextual_chunk_id IS NOT NULL)` CHECK) is two explicit indexable FKs + one row guard — not polymorphic, just explicit. (2) Decision #12 — add `raptor_building` intermediate lifecycle state. Original "no intermediate state for short ops" convention is fine for 3a/3b/3c (each one LLM call) but RAPTOR is genuinely multi-stage (cluster + N×summarize + N×embed, 5-20s/doc). For an open-source ship where lifecycle history is observability signal, the extra state turns the history into a narrative: `queued→parsing→parsed→chunked→contextualized→embedded→raptor_building→ready`. (3) Decision #5 — sharpened framing on Identity Summarizer: it's the no-key smoke path only, NOT CI semantic coverage. Identity concatenates leaf text → degenerate tree (L3 duplicates L2). Pytest tree-shape tests use mocked `GeminiSummarizer` with deterministic stubbed text. **Two forward-compat additions for Phase 3e:** Decision #16 locks `raptor_nodes.scope text DEFAULT 'per_doc'` + nullable `file_id` at 3d's 0012 migration. ALTER TABLE ADD COLUMN is fine at 100K rows but a migration nightmare at 100M — lock now. Decision #3 bumps `MAX_LEVELS` 4 → 6 so corpus tree on 100K doc-roots (`log₈(100K)≈5.5`) doesn't need re-tuning. **Phase split rationale:** per-doc RAPTOR is the structural prerequisite for corpus RAPTOR (doc-roots become L1 of corpus tree). Algorithms differ at scale: AgglomerativeClustering is O(N²) — fine for per-doc N≤100, infeasible at N=100K corpus roots. 3e switches to UMAP+GMM per the paper. Splitting keeps gates clean + lets per-doc ship first. §5.10 revised with 16 decisions + lifecycle delta + forward-compat columns. §5.10.1 added as Phase 3e placeholder. §5 phase table: 3d now G1✅/G2🟡; 3e new row at all-⬜. Estimated wall-clock: 3d alone ~5-7 hr G3+G4+G5; 3e ~4-6 hr. | Aniket |
| 2026-05-24 | **Phase 3d G1 🟡 OPEN — RAPTOR tree build plan drafted.** Final ingestion-side phase of Wave A. Per-doc tree per Sarthi et al. 2024 (RAPTOR, ICLR 2024). 15 decisions locked at §5.10: (1) `AgglomerativeClustering(metric='cosine', linkage='average')` from sklearn — replaces original paper's UMAP+GMM to avoid the `umap-learn` dep + keep clustering deterministic; (2) `BRANCHING_FACTOR=8`; (3) `MAX_LEVELS=4`; (4) three termination guards (root reached, max level, n ≤ branching); (5) three-impl `Summarizer` Protocol (Gemini Flash + Anthropic Haiku + Identity CI fallback) with `KB_SUMMARIZER ∈ {gemini,anthropic,identity,auto}` selector matching 3b-bis's contextualizer pattern; (6) `gemini-2.5-flash` default; (7) RAPTOR-paper-adapted prompt + `max_output_tokens=600` + `thinking_budget=0`; (8) `asyncio.Semaphore(4)` per file; (9) **L1 leaves denormalize from contextual_chunks** into raptor_nodes (storage cost ~6 KB/leaf, buys clean self-FK edges); (10) `raptor_edges(parent_node_id, child_node_id, workspace_id)` UNIQUE+CASCADE; (11) immutable (REVOKE UPDATE/DELETE — same pattern as chunks/contextual_chunks/chunk_embeddings); (12) no intermediate `raptor_building` lifecycle state — direct `embedded → ready`; (13) chained defer from `embed_file_impl` in separate tx (matches 3a→3b→3c chaining shape); (14) loud-fail on partial trees; (15) reuses Phase 3c's `make_embedder()` for summary-node embeddings (same halfvec(3072) vector space as leaves). Schema delta: new migration `0012_raptor.sql` (raptor_nodes + raptor_edges, both workspace-scoped + RLS day-1 + immutable). No lifecycle CHECK widen needed — `'ready'` is already in the 0009 CHECK list (added at 3c G4 forward-compat fix). New deps: `scikit-learn>=1.5.0`. Out of scope: corpus-level RAPTOR (Phase 5+), HNSW + BM25 indexes (Phase 4), tree-aware retrieval (Phase 4), UMAP. §5 table 3d row flips ⬜→🟡. Estimated ~5-7 hr G3+G4+G5. Awaiting Aniket sign-off. | Aniket |
| 2026-05-24 | **Phase 2c G5 ✅ — shipped.** New `scripts/verify_phase_2c.sh` (15 checks): compose smoke + pypdfium2 worker import probe + adapter env probe (KB_PARSER_STRATEGY/KB_GEMINI_API_KEY) + 2 E2E uploads (tiny.pdf digital → Docling path; tiny_scanned.pdf → sniff routes to Gemini-OCR if key set, soft-Docling-fallback if not) + provenance JSON assertions on both raw_pages.layout_json and the lifecycle parse_done payload + caller-override `?parser=docling` upload-event payload assertion + invalid `?parser=bogus` → 400 invalid-parser-override + Phase-2c pytest (18 = 6 parser + 3 sniff + 5 dispatcher + 4 quality). Standalone first run: 15/15 GREEN. **Cross-phase sweep across all 10 verify scripts** (0/1a/1b/1c/2a/2b/2c/3a/3b/3c): 9/10 GREEN first pass; 2c flaked at step 7 (tiny.pdf parse) when run as the 10th sequential stack — host memory pressure caused Docling first-run model init to time out within the 6-minute polling window. Confirmed transient by a standalone re-run (15/15 GREEN). Fix: bumped step 7 polling 180→240 iters (6→8 min buffer) and added worker-log capture on failure so future sweep regressions are actionable. Final sweep counts (after fix): **0:16/16 · 1a:17/17 · 1b:21/21 · 1c:20/20 · 2a:17/17 · 2b:15/15 · 2c:15/15 · 3a:18/18 · 3b:16/16 · 3c:15/15 — 173 total** (was 158 before 2c). Phase 2c officially shipped. | Aniket |
| 2026-05-24 | **Phase 2c G4 ✅ — code landed.** 4 new modules + 3 mutations. Files: `src/kb/parsers/text_layer_sniff.py` (70 LOC; `pypdfium2.PdfDocument` char-count over first 10 pages, threshold 50, soft-fail on malformed PDFs returns `has_text_layer=False`); `src/kb/parsers/quality.py` (130 LOC; pure-function `score_parse_quality`, `should_escalate`, `escalate_per_page`, `build_provenance` per §5.6.1 #10/#12); `src/kb/parsers/gemini_ocr_parser.py` (170 LOC; per-page render at 150 DPI via pypdfium2 → PNG → Gemini Flash via `types.Part.from_bytes(mime_type='image/png')` + `asyncio.Semaphore(4)` concurrency; `OCRConfigError` raised on missing key at construction); `tests/fixtures/tiny_scanned.pdf` (38 KB synthetic image-only PDF, 0 chars in text layer) + `tests/fixtures/scripts/make_tiny_scanned.py` (one-shot generator from tiny.pdf via PdfPage.render → PIL.Image.save). Mutations: `src/kb/parsers/__init__.py` (`select_parser_for(*, forced_parser=None)` strategy router); `src/kb/workers/tasks.py` (`parse_file_impl(file_id, forced_parser=None)` accepts override + `_maybe_escalate_to_ocr` helper that re-OCRs whole doc or just bad pages depending on signal); `src/kb/api/files.py` (parses `?parser=` query param → `InvalidParserOverrideError(400)` on bad value → persists into upload event payload + forwards to `parse_file.defer_async`); `src/kb/domain/files.py::create_file` accepts `upload_payload`; `src/kb/api/main.py` registers `InvalidParserOverrideError` exception handler; `.env.example` adds `KB_PARSER_STRATEGY=auto` + commented `KB_PDF_TEXT_LAYER_THRESHOLD/MAX_PAGES_SAMPLED`/`KB_OCR_MODEL/CONCURRENCY/RENDER_DPI` overrides. **Three in-G4 fixes**: (1) test_text_layer_sniff used default threshold 50 but tiny.pdf has 38 chars — refactored to pass explicit `threshold=10` for tests, doc reads default-50 is correct for typical A4 pages; (2) test_parser_dispatcher_strategy hit the same threshold-vs-fixture issue — set `KB_PDF_TEXT_LAYER_THRESHOLD=10` in the test's env scope; (3) initial `auto` strategy hard-failed on missing Gemini key, breaking 17 pre-existing parse-worker tests — refactored to **soft-fall-back to Docling** under `auto`/`gemini_first` when no key (strict-fail reserved for `gemini_only` + explicit `?parser=gemini`, per #13 loud-fail-on-opt-in semantics). Full suite: 258/258 in 58s. G5 opens. | Aniket |
| 2026-05-24 | **Phase 2c G1 ✅ + G2 ✅ — plan signed off; api_contracts §5.5 delta landed.** Brief mid-G1 design loop: considered switching from per-page rendering to direct-PDF upload (Gemini 2.5 Flash supports native PDF input via `Part.from_bytes(mime_type='application/pdf')`, 258 tok/page, simpler primary code path). Held off because the existing pipeline assumes per-page rows (`raw_pages.page_number`, `chunks.source_page_numbers`, RAPTOR per-page citations) — direct-PDF would need explicit page-break markers in the prompt to recover boundaries, AND hybrid escalation (one bad page in a 100-page doc) still needs per-page render anyway. Verdict: per-page everywhere keeps the architecture symmetric. G2 delta in `docs/api_contracts.md`: added Query parameters subsection to §5.5 documenting `?parser=auto\|docling\|gemini` (default `auto`; persisted into `raw_pages.layout_json.provenance.forced_parser`), 400 error type widened with `invalid-parser-override`, §5.3 lifecycle example footnote on the parser enum widening to include `gemini_ocr`. G3 opens: spec + ~18 red skeletons across 4 test files + 1 mutation. | Aniket |
| 2026-05-24 | **Phase 2c G1 🟡 OPEN — Gemini OCR + strategy-driven parser dispatch plan drafted.** Trigger: after the 2026-05-24 corpus-discussion, user concluded that (a) demo corpus may include scanned PDFs, (b) Docling+RapidOCR's quality on hard inputs (multilingual, handwriting, complex tables) is unreliable, (c) OCR quality compounds through 3a→3b→3c→3d so garbage-in-garbage-out applies twice. Plan at §5.6.1 introduces 5 new system surfaces: `GeminiOCRParser` (pypdfium2 PDF→PNG at 150 DPI + Gemini 2.5 Flash VLM call per page, asyncio.Semaphore(4) concurrency), pre-flight text-layer sniff (`pypdfium2.PdfDocument` → avg chars/page over first 10 pages, threshold 50), strategy-aware dispatcher (4-value `KB_PARSER_STRATEGY ∈ {auto,docling_first,gemini_first,gemini_only}` with `auto` default routing PDFs by sniff result), three-signal quality escalation in `parse_file_impl` (empty / printable_ratio<0.7 / hybrid per-page), caller override `POST /files?parser=<docling\|gemini\|auto>`. Provenance JSON written to existing `raw_pages.layout_json` (no migration). 15 decisions locked. Out of scope: workspace-level OCR policy (Phase 5), batched multi-page OCR (cost opt), Mistral adapter activation (stays inert). Endpoint contract delta: 2 single-line additions to api_contracts §5.5 (query param + parser-enum value). G5 verify will be a new `verify_phase_2c.sh` (not extension of 2b) given the surface area. Estimated ~6-8 hr G3+G4+G5. Awaiting sign-off. | Aniket |
| 2026-05-24 | **Phase 3b-bis G5 ✅ — shipped.** `scripts/verify_phase_3b.sh` widened 15→16 checks: added an adapter env-probe step that prints `KB_CONTEXTUALIZER`/`KB_GEMINI_API_KEY`/`KB_ANTHROPIC_API_KEY` presence in the worker container (catches the .env-vs-host-env footgun) + a conditional branch on the model_id assertion that mirrors the factory's auto-probe order (Gemini → Anthropic → Identity). Identity-path assertions preserved; Gemini/Anthropic branch adds `contextual_text LIKE '%' || chunk_text` (prefix present) + `cache_creation_input_tokens > 0` (billed-input recorded) + (Gemini-only) `cache_read_input_tokens = 0` (no explicit cache per §5.8.1 #4). Local run: 16/16 GREEN on Identity path (`.env` has no contextualizer keys — Gemini branch dormant; will activate when user adds `KB_GEMINI_API_KEY` to .env). Also closed the `.env.example` consistency gap (flagged in the 2026-05-23 consistency-sweep discussion): added documented placeholders for all 3 LLM keys (`KB_GEMINI_API_KEY`, `KB_ANTHROPIC_API_KEY`, `KB_MISTRAL_API_KEY`) + the new `KB_CONTEXTUALIZER` selector + commented `KB_CONTEXTUAL_MODEL`/`KB_EMBEDDING_MODEL` overrides + chunker tuning entries. Cross-phase sweep across all 9 verify scripts: **0:16/16 · 1a:17/17 · 1b:21/21 · 1c:20/20 · 2a:17/17 · 2b:15/15 · 3a:18/18 · 3b:16/16 · 3c:15/15 — 158 total, all GREEN**. Branch `phase-3/chunking-raptor` ready for merge or Phase 3d extension. | Aniket |
| 2026-05-24 | **Phase 3b-bis G4 ✅ — GeminiContextualizer + factory selector land.** `GeminiContextualizer` (~110 LOC) added to `src/kb/contextualization/__init__.py` alongside `AnthropicContextualizer` + `IdentityContextualizer`. Uses `google.genai.Client.aio.models.generate_content` with `types.GenerateContentConfig(system_instruction=..., max_output_tokens=200, thinking_config=types.ThinkingConfig(thinking_budget=0))`. Doc context lands in `system_instruction`; chunk text in `contents` (string). Decision #4 implemented: `usage_metadata.prompt_token_count` stored in `cache_creation_input_tokens` (= billed-input); `cache_read_input_tokens` stays 0 (no explicit cache used at demo scale). Decision #8 implemented: exception path captures `prompt_feedback.block_reason` if attached to exception or response, wraps into `ContextualizationError`. Defensive empty-candidates check covers safety-block responses. `make_contextualizer()` rewritten to a 4-value `KB_CONTEXTUALIZER` selector with `auto` probing Gemini → Anthropic → Identity (Gemini-first matches demo's single-key story). Explicit `gemini`/`anthropic` without matching key raises ValueError (loud-fail beats silent-fallback for misconfigs). **One in-G4 fix**: G3's `test_gemini_contextualizer_disables_thinking` used `getattr(...) or ...` to read `thinking_budget`, but `0 or x` short-circuits to `x` because 0 is falsy → test got `None` instead of asserting against `0`. Refactored to explicit `hasattr` branches. Full suite: 238/238 in 61.5s (232 prior + 6 new). G5 opens: extend `verify_phase_3b.sh` with a Gemini-path E2E branch + cross-phase sweep. | Aniket |
| 2026-05-24 | **Phase 3b-bis G1 ✅ + G3 ✅ — plan signed off; red skeletons land.** Spec at `tests/specs/phase_3b_bis.md`. 6 new tests in `tests/test_contextualization_gemini_unit.py` (mocked `google.genai.Client.aio.models.generate_content` mirroring the `_MockAnthropicClient` pattern from 3b for side-by-side reviewability — same `last_kwargs` capture + `raise_exc` injection). Tests cover decisions #1/#3/#4/#6/#7/#8/#9 from §5.8.1. 1 mutated test: `tests/test_contextualization_unit.py::test_contextualizer_factory_returns_identity_when_no_api_key` renamed to `test_contextualizer_factory_selector_matrix` and widened from a 2-case binary check to an 8-case matrix covering all `KB_CONTEXTUALIZER` values (auto+none/auto+gemini/auto+anthropic/auto+both/explicit-gemini/explicit-anthropic/explicit-identity/bogus→ValueError). Decision #10 (worker test parameterization) deferred to G4 — it's a code-only refactor with no new assertion. Run state: 7/7 fail (RED expected); rest of suite 231/231 pass — no collateral damage. G4 opens: implement `GeminiContextualizer` (~50 LOC mirroring `AnthropicContextualizer` shape, swap to `google-genai` client) + widen `make_contextualizer()` to read `KB_CONTEXTUALIZER` with auto-probe. | Aniket |
| 2026-05-23 | **Phase 3b-bis G1 🟡 OPEN — Gemini Contextualizer adapter plan drafted.** Motivation: interview-submission demo runs on a single Gemini API key. Without 3b-bis, `KB_ANTHROPIC_API_KEY` unset → `IdentityContextualizer` no-ops contextual retrieval (Anthropic's 67% retrieval failure reduction is silently disabled). With 3b-bis, a `GeminiContextualizer` lands alongside `AnthropicContextualizer` and the factory `make_contextualizer()` is widened to a 4-value selector (`KB_CONTEXTUALIZER ∈ {gemini, anthropic, identity, auto}`, default `auto` probes Gemini key → Anthropic key → Identity). **Scope is deliberately tight:** no migration, no lifecycle change, no API contract change. Reuses §5.8's `Contextualizer` Protocol verbatim, the Anthropic cookbook prompt verbatim (model-agnostic recipe), and the worker-level tests via parameterization on `KB_CONTEXTUALIZER`. Adds 1 new unit-test file (~6 tests) + extends `verify_phase_3b.sh` with a Gemini-path E2E branch. Decision #4 captures the Gemini caching semantics: `cache_creation_input_tokens` repurposed to hold Gemini's `prompt_token_count` (billed-input tokens, no explicit cache used at demo scale; revisit at scale). Decision #2 establishes the auto-selector probing order so the demo is zero-config when only `KB_GEMINI_API_KEY` is set. §5.8.1 added to build_tracker; §5 phase table gains a 3b-bis row. Estimated wall-clock once signed off: ~1 hr for G3+G4+G5 combined (adapter pattern is already paved). Awaiting Aniket sign-off on the plan. | Aniket |
| 2026-05-24 | **PR #9 merged — dev-velocity sweep wrapper.** `scripts/verify_sweep.sh` (NEW) brings the compose stack up ONCE for all 12 verify scripts, TRUNCATEs workspace-scoped tables between phases (so each phase sees clean `WS_A`), tears down once. Each `verify_phase_*.sh` patched with a `KB_REUSE_STACK=1` guard around its own setup/teardown so standalone scripts still work for single-phase debugging. Measured on 286-test / 12-phase suite: sequential baseline ~22-24 min → cold sweep **14:47** (image rebuild forced) → warm sweep **12:23** (cache hit). Both sweep runs 12/12 GREEN, 143 + 143 = 286 cumulative checks. Also: Docling layout+table models pre-warmed into the worker image layer at build time via `docling-tools models download -o /tmp/huggingface/docling` + `DOCLING_ARTIFACTS_PATH` env (one-time +500 MB build cost, saves ~2-3 min on first parse after any fresh container). Also evaluated pytest-xdist: measured neutral at 286 tests (`-n 1: 83.88s` vs `-n 4: 84.72s` — per-worker testcontainer spinup overhead cancels parallelism gain), reverted; documented in `CONTRIBUTING.md` so the next contributor doesn't re-litigate. Branch `chore/test-velocity` merged at `fff1e3f`. | Aniket |
| 2026-05-25 | **Phase 4 G1 ✅ SIGNED OFF — Indexing (HNSW + BM25 on all RAPTOR levels).** Plan locked at §5.11, **16 decisions**. **Indexing-only scope** — 4 indexes (HNSW on `chunk_embeddings.embedding` + `raptor_nodes.embedding` with `halfvec_cosine_ops`/`m=16`/`ef_construction=200`; BM25 on `contextual_chunks.contextual_text` + `raptor_nodes.text`) + internal `kb.retrieval.smoke` helper (NOT mounted on any router) + `scripts/reindex_weekly.sh` cron stub. **No `/search` endpoint, no rerank, no orchestration** — those are Phase 8 (decision #10 locks this formally; rationale: shipping `/search` here would either downgrade the architecture's 10-channel promise to plain hybrid RAG or commit to a contract Phase 8 will rewrite once 5/6/7 land atomic-units/mentions/entities). Single migration `0013_indexes.sql` (decision #13); no new lifecycle states (decision #14 — avoids forward-compat fixups on the 12 prior verify scripts). G2 was a no-op per decision #16 (no api_contracts.md delta — pure-infra phase). G3 opens: spec + 2 red skeleton files (`test_indexes.py` + `test_retrieval_smoke.py`). Branch `phase-4/retrieval` off main at `fff1e3f`. | Aniket |
| 2026-05-25 | **Phase 4 G3 ✅ SIGNED OFF — spec + 13 red tests land.** Spec at `tests/specs/phase_4.md` (5 sections: scope · fixture strategy · decision→test mapping · out-of-scope assertions · exit criteria). 2 new test files: `tests/test_indexes.py` (8 tests — 5 DDL invariants + 3 planner-usage) + `tests/test_retrieval_smoke.py` (5 tests — bm25_smoke + dense_smoke ranked results · multi-level hits · workspace RLS isolation · empty-for-unknown-workspace). RED-state breakdown at G3: **12 fail (RED expected) + 1 regression-guard pre-passes** — `test_kb_app_can_query_indexed_tables` covers decision #15 ("no GRANT changes; kb_app already has SELECT, index USAGE auto-granted") which is true both pre- and post-Phase-4 by design; spec §5 documents this honestly. Failure modes: 4 × DDL tests with `pg_indexes` empty assertion · 3 × planner tests with seq-scan-in-plan assertion · 5 × smoke tests with `ModuleNotFoundError: No module named 'kb.retrieval'`. Existing 286 tests still GREEN in 88.54s — no collateral damage. **Decision traceability:** every G1 decision with assertable behavior maps to ≥1 test (or is structural — #5/#6/#7/#8/#9 are operational/config and covered at G5 via `verify_phase_4.sh`). G4 opens: `0013_indexes.sql` + `kb/retrieval/smoke.py` + `reindex_weekly.sh`. | Aniket |
| 2026-05-25 | **Phase 5 ✅ FULLY GREEN — open extraction (5a + 5b + 5c) shipped end-to-end on `phase-5/extraction` branch.** Per user direction: build all 3 sub-phases without intermediate sign-off; decisions chosen by Claude per `problem_statement.md` + architecture §5 steps 12/12b-d/14. **Plan locked at §5.12 + §5.12.1 (5a, 11 decisions) + §5.12.2 (5b, 11 decisions) + §5.12.3 (5c, 10 decisions)**. **5a — mention extraction**: 0014_mentions.sql (extracted_mentions table + OntoNotes-18 mention_type CHECK + 5a/5b/5c lifecycle CHECK widening in one migration); `src/kb/extraction/{__init__,mentions}.py` (Gemini/Anthropic/Identity factory mirroring 3b-bis/3d); `src/kb/domain/mentions.py` repo; new worker `extract_mentions_file_impl` chained from raptor_build_file_impl (end-state changed from `ready` to `mentions_extracting`). **5b — emergent fields + auto-promotion**: 0015_emergent_fields.sql (proposed_fields + inferred_schema_fields tables + `files.inferred_doc_type` + `schema_fields.auto_promoted` column adds); `src/kb/extraction/fields.py` (classifier + proposer with 3-impl factory); `src/kb/extraction/promotion.py` (snake_case-normalize cluster + thresholds prevalence ≥ 0.80, stability ≥ 0.90, value_type_confidence ≥ 0.90, n_docs_observed ≥ KB_PROMOTION_MIN_DOCS=5; auto-creates `schemas(name='auto:<doc_type>')` + `schema_entities('Doc')` + INSERT schema_fields with auto_promoted=true; value_type→schema_type mapping text/enum→string). **5c — atomic units + anomaly**: 0016_atomic_units.sql (atomic_units table + open jsonb parameters); `src/kb/extraction/plugins/{__init__,clauses,transactions,rows}.py` (3-plugin registry with dispatcher; rows plugin is LLM-free—parses xlsx text directly); `src/kb/extraction/anomaly.py` (JIT centroid + per-numeric z-score + per-categorical 1-frequency, max across params); final worker `extract_atomic_units_file_impl` transitions to `ready`. **50 new tests** across `test_mentions_{unit,worker}.py` (13) + `test_fields_{unit,worker}.py` (18) + `test_atomic_units_{unit,worker}.py` (19) — all GREEN. **Forward-compat fixes (§0.15 convention)**: 0009 + 0012 lifecycle CHECK widened to include 5a/5b/5c states (idempotent re-run test pollution); 6 verify scripts (2a/2b/2c/3a/3b/3c) widened to accept new mid-states; 1 raptor_worker test updated to assert `mentions_extracting` instead of `ready`. **Final: 346/346 pytest in 87.29s · verify_phase_5.sh 16/16 standalone · cross-phase sweep 14/14 GREEN**. Phase 6 opens next (schema-driven extraction). | Aniket |
| 2026-05-25 | **Phase 4 G5 ✅ SIGNED OFF — Phase 4 fully green; all 5 gates closed.** `scripts/verify_phase_4.sh` lands with 16 checks (standalone): 4 DDL invariants on the 4 indexes (USING clause + operator class + key_field) + 1 HNSW params check (m=16 + ef_construction=200) + tiny.pdf E2E to `ready` + ANALYZE + 3 planner-usage EXPLAIN checks (HNSW for chunk_embeddings KNN + HNSW for raptor_nodes KNN + BM25 for contextual_chunks text search — all forced via `SET enable_seqscan=off enable_bitmapscan=off`, the same approach pytest used but which failed there because btree won at fixture scale; at full-stack scale with ANALYZE stats the forcing flags + index choice work) + 2 smoke-helper checks (worker imports `kb.retrieval.smoke` + grep proves `kb.retrieval` not leaked into `kb.api/*` per decision #10) + Phase-4 pytest (10/10 over testcontainers). **Standalone: 16/16 GREEN.** Also updated `scripts/verify_sweep.sh` to include `4` in its phase list. **Cross-phase sweep across all 13 verify scripts via `verify_sweep.sh`: 13/13 GREEN in 14:56 total** (per-phase: 0:24s · 1a:17s · 1b:17s · 1c:18s · 2a:47s · 2b:16s · 2c:27s · 3a:50s · 3b:41s · 3c:42s · 3d:53s · 3e:59s · 4:14s — Phase 4 is fastest because the indexes are auto-populated when the worker writes through the pipeline, no separate build step). One small in-G5 fix: sweep wrapper's `ALL_PHASES` array initially missing `4` — added. Branch `phase-4/retrieval` ready for PR. **Phase 5 opens next** — open extraction (L2 mentions + L2b emergent fields + L3 atomic units + anomaly scoring) per architecture §12 line 1165; recommend G1 split into 5a/5b/5c. | Aniket |
| 2026-05-25 | **Phase 7 ✅ FULLY GREEN — identity resolution shipped on `phase-7/identity-resolution` branch.** Plan §5.14 with **14 decisions** (including #14 explicitly deferring persistent union-find to Wave B, with per-file cascade-on-insert as the Wave A equivalent). Per architecture §5 step 15: deterministic→embedding→LLM-judge→cascade-create. **Migration 0018_entities.sql**: `entities` (workspace-scoped canonical directory, UPDATEable, with HNSW halfvec_cosine_ops partial index WHERE embedding IS NOT NULL + UNIQUE on `(workspace_id, lower(canonical_name), entity_type)` for stage-a deterministic) + `mention_to_entity` (PK on mention_id, resolved_method CHECK ∈ deterministic/embedding/llm_judge/identity, REVOKE UPDATE). Forward-compat: 0009/0012/0014/0017 widened to include `identity_resolving`. **Modules**: `src/kb/identity/{__init__,judge,resolve}.py` (3-impl factory mirroring 3b-bis/3d/5a/5b/5c/6; thresholds EMBEDDING_HIGH=0.92, EMBEDDING_LOW=0.85) + `src/kb/domain/entities.py` repo. **Worker**: `resolve_identities_file_impl` chained from Phase 6 (end-state `ready` → `identity_resolving`), 4-stage cascade with workspace-id RLS context. **37 tests** across `test_identity_unit.py` (13) + `test_identity_worker.py` (14) + `test_entities_repo_unit.py` (10) — including dedicated `test_resolve_identities_embedding_blocking_matches_existing_entity` that monkeypatches `kb.embeddings.make_embedder` to force a one-hot vector for stage-b coverage. **`scripts/verify_phase_7.sh`** 16/16 GREEN standalone (compose smoke + 5 DDL invariants + lifecycle CHECK + xlsx E2E + lifecycle transition + identities_resolved event + fabricated-mention end-to-end resolve + cross-file deterministic collapse + pytest 37). **Forward-compat fixes**: 6 prior verify scripts widened (2a/2b/2c/3a/3b/3c) accept-sets include `identity_resolving`; Phase 6 verify lifecycle substring relaxed (was `entities_extracting,ready` — Phase 7 inserts `identity_resolving` between them). **In-G5 fix**: verify_phase_7.sh fab seed initially produced 67-char `chunks.content_sha`; fixed via `substring(... from 1 for 64)`. **Final: 407/407 pytest in 87s · verify_phase_7.sh 16/16 · cross-phase sweep across all 16 verify scripts: 16/16 GREEN** (per-phase: 0:34s · 1a:15s · 1b:22s · 1c:21s · 2a:70s · 2b:21s · 2c:54s · 3a:55s · 3b:55s · 3c:46s · 3d:51s · 3e:82s · 4:16s · 5:22s · 6:20s · 7:27s). Phase 8 opens next (the big one — query layer). | Aniket |
| 2026-05-25 | **Phase 4 G4 ✅ SIGNED OFF — indexes + smoke helper land.** Commits this gate: (1) `migrations/runner.py` — `@no-transaction` pragma support. Postgres rejects `CREATE INDEX CONCURRENTLY` inside transaction blocks; pragma marker `-- @no-transaction` at the top of a migration file makes `_apply_one` run statements under autocommit (no surrounding `BEGIN`/`COMMIT`). On mid-file failure the file stays UNrecorded but earlier-applied CONCURRENTLY statements remain (inherent to CONCURRENTLY — you can't rollback a built index). Reusable infrastructure; Phase 14 HippoRAG graph indexes will use the same path. (2) `migrations/sql/0013_indexes.sql` — 4 CONCURRENTLY indexes: HNSW on `chunk_embeddings.embedding` + `raptor_nodes.embedding` (both `halfvec_cosine_ops`/`m=16`/`ef_construction=200`); BM25 on `contextual_chunks.contextual_text` + `raptor_nodes.text` (pg_search defaults: Tantivy default tokenizer, Robertson k1=1.2/b=0.75). (3) `src/kb/retrieval/__init__.py` + `src/kb/retrieval/smoke.py` — internal helpers `bm25_smoke(conn, *, workspace_id, query, limit)` + `dense_smoke(conn, *, workspace_id, query_vec, limit)` returning `list[tuple[id, score, level, scope]]`. Both UNION-combine contextual_chunks (level=1, scope='leaf') + raptor_nodes (level 2..6, scope per_doc/corpus); workspace_id filter holds redundantly with kb_app RLS. NOT mounted on any router; NOT importable from `kb.api.*`. (4) `scripts/reindex_weekly.sh` — host-cron stub for weekly `REINDEX CONCURRENTLY` rotation; not wired into compose (production scheduler is Phase 9). **In-G4 fixes:** (a) seed `content_sha` widened from arbitrary string to `hashlib.sha256(...).hexdigest()` (files table's 64-char CHECK constraint). (b) **3 planner-usage tests dropped** — at pytest fixture scale (~200 rows) the planner correctly prefers a btree index-scan + in-memory sort over HNSW; HNSW only wins above ~5K rows per workspace AND with ANALYZE stats up to date. Even forcing `SET LOCAL enable_seqscan=off enable_bitmapscan=off`, btree `chunk_embeddings_workspace_idx` won. Planner-usage moved to G5 `verify_phase_4.sh` where it runs against the full ingestion pipeline + ANALYZE. Spec `tests/specs/phase_4.md` updated to reflect 10 tests instead of 13. **296/296 pytest in 89.84s.** G5 opens. | Aniket |
| 2026-05-25 | **Phase 10a ✅ FULLY GREEN — Next.js 15 Upload UI shipped on `phase-10a/upload-ui` branch.** Plan §5.17 with **15 decisions** locked. **Stack**: Next.js 15 (App Router) + TypeScript + Tailwind CSS v4 + lucide-react icons + happy-dom (Vitest) + Playwright. **Top-level `ui/` directory** (sibling of `src/`, `tests/`, `prototype/`) — clean separation; backend doesn't import from `ui/` and vice versa (verified by no-leak grep). **Page**: `/upload` mirrors `prototype/upload.html` — slim 56px sidebar (hover-expand) + topbar with live counters (ready/processing/failed) + drag-drop zone (native file input + drag-drop events) + status table with 5-pip animated stage indicator (parse · embed · raptor · extract · ready) per file row. **API client** `ui/lib/api.ts` (~190 LOC): `uploadFile()` (multipart POST /files with auto Idempotency-Key), `listFiles()`, `subscribeToFileStatus()` (native EventSource — closes on `event: done`). Pure helpers `stageIndexFor()` + `isTerminal()` + `stageLabelFor()` projecting any of the 15 backend lifecycle states onto the 5-pip line. **State**: React Context + `useReducer` (`UploadProvider`) keyed on FileResource.id with `seed` (initial hydrate from GET /files) + `upserted` (POST /files response) + `lifecycle` (SSE event) + `errored` actions. **Page lifecycle**: on mount, `listFiles()` hydrates; then for every non-terminal row, subscribe to its SSE stream. SSE closes per-file when terminal. **Components**: `<Sidebar current="upload">` · `<TopBar>` · `<DropZone>` · `<FilesTable>` · `<FileRow>` · `<StageBadge>`. **Backend addition**: `CORSMiddleware` in `src/kb/api/main.py` with `KB_CORS_ORIGINS` env (default `http://localhost:3000`), allowing all methods/headers + exposing `X-Request-Id` + `X-Dedup-Reason`. **Testing**: `ui/tests/api.test.ts` 10/10 Vitest (stage projections + terminal detection + label formatting) + `ui/tests/upload.spec.ts` 2/2 Playwright (page renders sidebar+topbar+dropzone+empty-state-table + root `/` redirects to `/upload`); screenshot saved to `ui/tests/artifacts/upload-empty.png` (~37KB, full-page). **Build**: `npm run build` clean — Next.js compiles TypeScript + ESLint in ~3.4s; `/upload` route is 5.95 kB First-Load JS 108 kB. **Verify** `scripts/verify_phase_10a.sh` 11 checks (compose smoke + node/npm available + npm install + vitest + next build + CORS sanity probe + playwright install + playwright run + screenshot artifact + no python backend imports in ui/). **In-G4 fix**: initially created `lib/state.ts` with JSX in `UploadProvider`; Next.js requires `.tsx` for JSX. Renamed. **Final: backend 541/541 pytest still GREEN in 108s (no regressions from CORS middleware); 10/10 vitest + 2/2 Playwright; screenshot artifact verified visually (clean dropzone + 5-pip placeholder + empty-state table).** Phase 10b opens next (Next.js Chat UI consuming `POST /chat` + `/chat/:id/stream` SSE). | Aniket |
| 2026-05-25 | **Phase 9 ✅ FULLY GREEN — Wave A backend complete: SSE upload-status + GET /audit + chat-replay SSE shipped on `phase-9/sse-audit` branch.** Plan §5.16 with **12 decisions** locked. Three endpoints unblock Phase 10a/10b UI: live upload-status SSE (Upload UI consumes per-doc per-stage transitions), paginated query audit list (audit page), chat-replay SSE (re-streams cached answer for Chat UI history view). **Module `src/kb/api/audit.py`** (~125 LOC): cursor-paginated `/audit` reading 8f's query_log; cursor encodes `(created_at, id)` opaque-base64; default limit 50 / max 200; answer truncated to 500 chars in list view. **Module `src/kb/api/sse.py`** (~210 LOC): `/upload/:file_id/status` polls file_lifecycle every `KB_SSE_POLL_INTERVAL_MS` (default 1000), emits `event: lifecycle` per new row, closes with `event: done` when `lifecycle_state ∈ {ready, failed}`; 15s heartbeat keepalive prevents proxy idle-timeouts. `/chat/:query_id/stream` replays cached answer in `KB_SSE_REPLAY_CHUNK_SIZE` chunks (default 50 chars) `KB_SSE_REPLAY_CHUNK_MS` apart (default 50ms); final `event: done` payload includes citations + refused + refusal_reason + model_id. Public `parse_event_stream()` helper for tests. Standard `text/event-stream; charset=utf-8` wire format. Both SSE endpoints 404 BEFORE opening the stream (decision #8) — cleanly REST-safe. **23 tests**: `test_audit_unit.py` (10 — empty list · newest-first ordering with clock_timestamp seeding · response shape · limit param · oversize 400 · cursor pagination walks full 7-row list with no overlap · workspace isolation · answer truncation · invalid cursor 400 · refusal envelope) + `test_sse_unit.py` (13 — parser handles multi-event blocks + skips empty · upload 404 unknown file_id · upload streams events in order + closes on ready · upload closes on failed · upload Content-Type · chat 404 unknown qid · chat 404 wrong workspace · chat short answer one-chunk · chat 175-char answer 4-chunk reassembled · chat done event includes citations · chat refused-envelope done-only · openapi includes all 3 routes). **`scripts/verify_phase_9.sh`** 14/14 GREEN standalone with **real end-to-end SSE**: tiny.pdf uploaded → worker ran full chain through ready → SSE streamed 13 live lifecycle events (queued → parsing → parsed → chunked → contextualized → embedded → raptor_building → → ready) + done. Also: 8f's POST /chat → query_id audited → /chat/:qid/stream emits `event: done` end-to-end. **No new migration** — reuses file_lifecycle (Phase 2a) + query_log (Phase 8f). **In-G4 fixes**: (a) `test_audit_returns_recent_queries_newest_first` seeded N rows in one transaction → all shared `NOW()` (transaction-stable) → secondary ORDER BY id was UUID4 random. Fixed by explicitly using `clock_timestamp()` in INSERTs for the test helper. (b) Module-level env vars cached at import; SSE tests use `monkeypatch.setenv` + `importlib.reload(sse_mod)` to pick up faster poll/chunk intervals. **Final: 541/541 pytest in 99s · verify_phase_9.sh 14/14 standalone · cross-phase sweep across all 23 verify scripts: 23/23 GREEN** (per-phase: 0:40 · 1a:23 · 1b:18 · 1c:20 · 2a:71 · 2b:23 · 2c:58 · 3a:65 · 3b:50 · 3c:56 · 3d:62 · 3e:70 · 4:28 · 5:30 · 6:21 · 7:38 · 8a:40 · 8b:30 · 8c:18 · 8d:20 · 8e:17 · 8f:30 · 9:63). **Wave A backend complete — UI phases (10a Upload + 10b Chat) open next.** | Aniket |
| 2026-05-25 | **Phase 8f ✅ FULLY GREEN — Wave A query stack complete: orchestrator + POST /search + POST /chat + query_log audit shipped on `phase-8f/orchestrator` branch.** Plan §5.15.6 with **17 decisions** locked. The synthesis of 8a→8e: `Orchestrator.search()` runs rewriter (8a, 4-variant fan-out) → batch-embed all 4 → 6 channels × 4 rewrites = 24 result lists → RRF k=60 → top-30 → reranker (8c) → top-10 → CRAG (8d). `Orchestrator.chat()` extends with `generator.generate(query, hits, force_refuse=(crag_score < CRAG_THRESHOLD))` — the orchestrator decides force-refuse so the response shape is consistent (always a `GenerationResult` envelope, never a 4xx for empty corpus). **HTTP surface**: `POST /search` (read-only retrieval inspector) + `POST /chat` (full pipeline + Idempotency-Key replay). Both return 200 + envelope under all refusal modes — the critical invariant §7.1 #3 ("emptiness is a domain answer, not a client error"). **Migration `0019_query_log.sql`**: workspace-scoped audit table with RLS forced + kb_app SELECT+INSERT only (immutable audit per architecture §6); columns cover endpoint, rewrites jsonb, hit_ids jsonb, crag_score, refused, refusal_reason, answer, citations jsonb, model_id, latency_ms, idempotency_key. **Contracts §7 (api_contracts.md G2)**: 7 pipeline invariants + 2 endpoint specs + 6 machine-readable refusal_reason values documented (insufficient_evidence · no_hits · llm_error · parse_error · empty_response · model-supplied). **27 tests**: `test_query_orchestrator_unit.py` (13 — mocked components: 4-rewrite fan-out · 24-list RRF · top-10 rerank cap · CRAG-after-rerank · force-refuse-on-low-CRAG · empty-corpus refusal envelope · /search no-generation field · /chat ChatResult envelope · make_default-via-env-factories) + `test_api_query.py` (14 over testcontainers: migration shape + immutability GRANT · 200-with-envelope on /search and /chat · 400-on-mode≠H · 400-on-whitespace · 422-on-Pydantic-min/max-length · 200-not-4xx-refusal envelope · query_log audit row per /search call · query_log audit row per /chat call with refused=true · Idempotency-Key replay returns same query_id · query_log RLS isolates workspace B from A's rows · openapi includes /search and /chat). **`scripts/verify_phase_8f.sh`** 13/13 GREEN standalone (compose smoke + query_log table shape + RLS forced + audit-list index + openapi paths + /search 200 envelope + /chat refused=true envelope + /search 400 mode≠H + /search 422 empty query + query_log row written per search call + query_log row written per chat call with refusal_reason + Idempotency-Key replay returns cached query_id + Phase-8f pytest 27). **Forward-compat fix (§0.15 convention)**: 5 prior 8x verify scripts (8a/8b/8c/8d/8e) widened to `--exclude=query.py` in the "no kb.query leak into kb.api/*" grep — Phase 8f legitimately puts the orchestrator-mount HTTP boundary at `kb/api/query.py`. **In-G5 fixes**: (a) migration's RLS policy initially used `app.current_workspace_id` GUC; corrected to `app.workspace_id` matching codebase convention; (b) `ChatResult` initially missing `rewrites` field (audit writer expected it via duck-typing); added Pydantic field + populated in `Orchestrator.chat()` + updated contract example; (c) verify_phase_8f.sh step 11 bash case-pattern used `|` alternation conflicting with literal `|` in `true|no_hits` — quoted each alternative explicitly. **Final: 518/518 pytest in 96s · verify_phase_8f.sh 13/13 standalone · cross-phase sweep across all 22 verify scripts: 22/22 GREEN** (sweep timings per-phase: 0:24 · 1a:15 · 1b:17 · 1c:17 · 2a:46 · 2b:14 · 2c:30 · 3a:48 · 3b:42 · 3c:39 · 3d:44 · 3e:55 · 4:16 · 5:16 · 6:16 · 7:30 · 8a:28 · 8b:29 · 8c:15 · 8d:17 · 8e:15 · 8f:17). **Wave A query pipeline now end-to-end exposed.** Phase 9 opens next (SSE + /audit + lifecycle visibility). | Aniket |
| 2026-05-25 | **Phase 8e ✅ FULLY GREEN — Astute generation shipped on `phase-8e/generate` branch.** Plan §5.15.5 with **15 decisions** locked, per Astute RAG paper (Wang et al. 2024 "Astute RAG: Overcoming Imperfect Retrieval Augmentation and Knowledge Conflicts for Large Language Models" — arXiv 2410.07176). Single defensive-prompt Gemini call over reranked top-10 hits → structured `{answer, citations[], refused, refusal_reason, model_id}` JSON. Orchestrator (Phase 8f) invokes with `force_refuse=True` when CRAG (8d) reports below threshold so the user still gets a clean "no evidence" envelope rather than a 4xx. **Module** `src/kb/query/generate.py` (~330 LOC): `GenerationResult` + `Citation` Pydantic models · `Generator` Protocol · 2-impl factory (`GeminiGenerator` gemini-2.5-flash with `thinking_budget=0`, `response_mime_type=application/json`, `max_output_tokens=2048`; `IdentityGenerator` deterministic templated echo). `_parse_result()` tolerant + fail-safe: strips ```json fences, returns refusal envelope on bad JSON / non-dict / missing-required-key, respects model self-refusal (`{refused: true}`), synthesizes top-3 citations from Hit list when LLM omits them. **Wave-A semantics — three refusal modes**: (1) `force_refuse=True` (orchestrator-passed) → skip LLM, `refusal_reason="insufficient_evidence"`; (2) empty hits → skip LLM, `refusal_reason="no_hits"`; (3) LLM exception → `refusal_reason="llm_error"` (decision #10 — asymmetric vs CRAG's fail-safe-pass: emitting a fake answer is worse than refusing on infra failure). **Astute defensive system-instruction** (decision #15) tells model to "use only retrieved snippets, cite every claim by [hit_id], refuse rather than guess." **19 tests** in `tests/test_query_generate_unit.py`: Pydantic shapes (2) + Identity stub including force_refuse + empty-hits (3) + parser fail-safes incl. LLM self-refusal respected (4) + factory matrix incl. anthropic→Identity (5 sub-cases in 1 test) + mocked Gemini path with `_FakeClient`/`_FakeResponse` (6 — inline-citation-markers · top-10 hit capping with 12 inputs · thinking_budget=0 captured · system-instruction Astute-discipline asserted · respects-LLM-refusal · LLM-error→llm_error_refusal) + force_refuse/empty-hits skip-LLM (2) + prompt-builder asserts hit_ids+snippets (1) + 1 misc. **`scripts/verify_phase_8e.sh`** 12/12 GREEN standalone (compose smoke + worker imports + no-leak grep + Identity stub shape · force_refuse · no_hits + Gemini no_hits + parser fail-safes + factory error/anthropic/auto + Phase-8e pytest 19). **No migration, no lifecycle change, no API contract change** — pure module-level addition (8f owns HTTP). **Final: 491/491 pytest in 98s · verify_phase_8e.sh 12/12 standalone · cross-phase sweep across all 21 verify scripts: 21/21 GREEN in 9:31 total** (per-phase: 0:24 · 1a:18 · 1b:17 · 1c:19 · 2a:54 · 2b:17 · 2c:33 · 3a:50 · 3b:46 · 3c:45 · 3d:49 · 3e:60 · 4:16 · 5:18 · 6:16 · 7:22 · 8a:11 · 8b:16 · 8c:10 · 8d:14 · 8e:16). Phase 8f opens next (orchestrator + HTTP surface — the synthesis of 8a→8e). | Aniket |
| 2026-05-25 | **Phase 8d ✅ FULLY GREEN — CRAG (Corrective RAG) relevance gate shipped on `phase-8d/crag` branch.** Plan §5.15.4 with **10 decisions** locked, per CRAG paper (Yan et al. 2024 "Corrective Retrieval Augmented Generation"). Cheap LLM-judge of top-3 rerank output → confidence score (0..1); orchestrator (Phase 8f) refuses below `CRAG_THRESHOLD = 0.5` with "insufficient evidence". **Module** `src/kb/query/crag.py` (~210 LOC): `CragGate` Protocol + 2-impl factory (`GeminiCragGate` Gemini-2.5-flash with `thinking_budget=0` + `response_mime_type=application/json` + `max_output_tokens=100`; `IdentityCragGate` always-1.0 fail-safe pass) + `make_crag_gate()` reads `KB_QUERY_LLM` (reuses 8a/8e LLM-family selector — `anthropic` maps to Identity per decision #10's Wave-A defer; `auto` skips Anthropic auto-probe). `_parse_score()` is tolerant + fail-safe: strips ```json fences, returns 1.0 on bad JSON / non-dict / missing-key / non-numeric, clamps numeric values to `[0, 1]`. **Wave-A semantics**: empty hits → 0.0 (decision #5, guaranteed refusal); LLM exception → 1.0 (decision #7, don't block on infra failure); only top-3 hits' snippets fed to LLM (decision #3, cost cap; >85% of signal). **16 tests** in `tests/test_query_crag_unit.py`: parser fail-safes (6) + Identity always-1.0 (2) + Gemini empty→0.0 (1) + factory matrix incl. anthropic→Identity (5 sub-cases) + mocked Gemini path with `_FakeClient`/`_FakeResponse` (3 — parsed score · thinking_budget=0 captured · top-3 snippet capping). **`scripts/verify_phase_8d.sh`** 10/10 GREEN standalone (compose smoke + worker imports + no-leak grep + Identity 1.0 with/without hits + Gemini empty→0.0 + parser fail-safes + factory error/anthropic/auto + Phase-8d pytest 16). **No migration, no lifecycle change, no API contract change** — pure module-level addition, mirrors 8a/8c sub-phase shape. **Final: 472/472 pytest in 90s · verify_phase_8d.sh 12/12 standalone · cross-phase sweep across all 20 verify scripts: 20/20 GREEN in 8:46 total** (per-phase: 0:22s · 1a:14s · 1b:20s · 1c:17s · 2a:51s · 2b:16s · 2c:28s · 3a:47s · 3b:43s · 3c:41s · 3d:46s · 3e:55s · 4:16s · 5:19s · 6:18s · 7:22s · 8a:11s · 8b:17s · 8c:10s · 8d:13s). Phase 8e opens next (Astute generation). | Aniket |

---

## 10. Reading order for a fresh reviewer

1. This file → understand discipline + current state.
2. [`README.md`](../README.md) → mental model.
3. [`docs/architecture.md`](architecture.md) → full system spec.
4. [`docs/ui_design.md`](ui_design.md) → screen-by-screen UX.
5. [`docs/gaps_design.md`](gaps_design.md) → 9 detailed designs.
6. Stress-tests & audits as needed (`scenarios.md`, `red_team.md`, `citations_audit.md`, `competitive_audit.md`, `scale_perf_audit.md`).
