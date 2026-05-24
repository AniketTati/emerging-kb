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

**Now:** Phase 3b-bis ✅ shipped — `GeminiContextualizer` + 4-value `KB_CONTEXTUALIZER` factory selector + `.env.example` consistency gap closed. 238/238 pytest. verify_phase_3b.sh 16/16 (was 15). Cross-phase sweep across all 9 verify scripts (0/1a/1b/1c/2a/2b/3a/3b/3c) all GREEN — **158 checks total**, no regressions. Branch `phase-3/chunking-raptor` carries 4 commit-sets (3a/3b/3c/3b-bis); ready to merge or extend with 3d.
**Next:** Phase 3d (RAPTOR tree build) on same branch — clustering + Gemini-Flash summarization of L1 contextual chunks → tree nodes. Phase 1c-bis (GeminiOCRParser) gated on demo-corpus answer (scanned PDFs?).
**Blocked on:** corpus answer (for Phase 1c-bis scoping). Phase 3d unblocked.

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
| **3a** | Chunking — late chunking of `raw_pages` → `chunks` table (layout-aware, token-bounded, cross-page joining); worker stage `chunk_file`; new lifecycle state `chunked` | ✅ | ✅ | ✅ | ✅ | ✅ | All 5 gates green 2026-05-23. verify_phase_3a.sh 18/18. Cross-phase sweep: 0/1a/1b/1c/2a/2b all still green (124/124 cumulative checks). pytest 204/204. Ready to merge. |
| **3b** | Contextual Retrieval — Anthropic Claude per-chunk prefix with prompt-cached doc context; `contextual_chunks` table; worker stage `contextualize_file` | ✅ | ✅ | ✅ | ✅ | ✅ | All 5 gates green 2026-05-23. verify_phase_3b.sh 15/15. Cross-phase sweep: 0/1a/1b/1c/2a/2b/3a/3b all green (139/139 cumulative checks). pytest 219/219. Ready to merge. |
| **3c** | Embedding — Gemini Embedding 001 on contextual chunks → `chunk_embeddings` (`halfvec(3072)`); worker stage `embed_file`; new lifecycle state `embedded` | ✅ | ✅ | ✅ | ✅ | ✅ | First embedding call; gated on `KB_GEMINI_API_KEY` with DeterministicMockEmbedder for CI. 13/13 new tests green; suite 232/232. verify_phase_3c.sh 15/15 + cross-phase sweep 0/1a/1b/1c/2a/2b/3a/3b/3c all GREEN. One sweep fix: 3a's accept-set widened to also accept `embedded` (Phase 3c chained-defer races past `chunked` before the script polls — same forward-compat pattern handled at 3b). |
| **3b-bis** | Gemini Contextualizer adapter — `GeminiContextualizer` alongside `AnthropicContextualizer` + factory selector `KB_CONTEXTUALIZER ∈ {gemini,anthropic,identity,auto}`. No schema/lifecycle/API delta. | ✅ | — | ✅ | ✅ | ✅ | Shipped 2026-05-24. 238/238 pytest. verify_phase_3b.sh widened 15→16 checks (adapter env probe + conditional Gemini/Anthropic/Identity branch on `model_id`/`cache_creation_input_tokens`/`cache_read_input_tokens`). Cross-phase sweep 0/1a/1b/1c/2a/2b/3a/3b/3c all GREEN (158 checks total). `.env.example` consistency gap closed at G5 (all 3 LLM keys + KB_CONTEXTUALIZER documented). |
| **3d** | RAPTOR tree build — per-doc recursive cluster→summarize→re-embed → `raptor_nodes` + `raptor_edges`; lifecycle terminates at `ready` | ⬜ | ⬜ | ⬜ | ⬜ | ⬜ | GMM clustering via sklearn; Gemini Flash summarizer with IdentitySummarizer fallback; corpus-level RAPTOR deferred (per-doc only in Wave A) |
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

### 5.5 Phase 2a plan — Parse-layer scaffold + Docling (G1 ✅ SIGNED OFF)

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

### 5.6 Phase 2b plan — Additional parsers (G1 ✅ SIGNED OFF)

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

### 5.9 Phase 3c plan — Embedding (G1 ✅ + G2 ✅ + G3 ✅ + G4 ✅ SIGNED OFF)

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

### 5.10 Phase 3d plan — RAPTOR tree build (placeholder)

> **Status:** ⬜ Not yet drafted. Opens after Phase 3c G5 ✅.

Scope sketch (to be locked at G1 when 3c closes): per-doc RAPTOR tree per architecture §5 step 10. New tables `raptor_nodes` (id, file_id, workspace_id, level, text, embedding halfvec(3072), token_count, model_id) + `raptor_edges` (parent_node_id, child_node_id) — both workspace-scoped, RLS, immutable. Algorithm: leaf nodes = contextual chunks; at each level L, GMM-cluster level-L embeddings via sklearn (n_components=ceil(N/8)), summarize each cluster via `Summarizer` Protocol (real `GeminiSummarizer` with Gemini Flash, mock `IdentitySummarizer` concatenates leaf texts), embed the summary, write a new level-(L+1) node + edges. Terminate when n_clusters == 1 OR level == max_levels=4. Worker stage `raptor_build_file_impl` chained from `embed_file_impl`. Lifecycle terminates at `ready`. Corpus-level RAPTOR deferred to Phase 5+. New deps: `scikit-learn>=1.5.0`. HNSW + BM25 indexes themselves land in Phase 4.

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
| 3b | [scripts/verify_phase_3b.sh](../scripts/verify_phase_3b.sh) | 2026-05-23 | ✅ 15/15 (compose smoke + 4 DDL assertions on contextual_chunks + lifecycle CHECK + E2E PDF parse-chunk-contextualize + model_id='identity' check + identity-fallback contextual_text=chunk_text + lifecycle progression assert + idempotent re-defer + Phase-3b pytest 15) |
| 3c | [scripts/verify_phase_3c.sh](../scripts/verify_phase_3c.sh) | 2026-05-23 | ✅ 15/15 (compose smoke + 5 DDL assertions on chunk_embeddings: table + UNIQUE on (contextual_chunk_id, model_id) + RLS forced + kb_app grants restricted + halfvec column type + lifecycle CHECK includes `embedded` + E2E PDF parse → chunk → contextualize → embed via DeterministicMockEmbedder fallback (KB_GEMINI_API_KEY unset in compose) + model_id='mock-deterministic-v1' assertion + lifecycle history substring match for contextualized→embedded + idempotent re-defer + Phase-3c pytest 13) |
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
| 2026-05-24 | **Phase 3b-bis G5 ✅ — shipped.** `scripts/verify_phase_3b.sh` widened 15→16 checks: added an adapter env-probe step that prints `KB_CONTEXTUALIZER`/`KB_GEMINI_API_KEY`/`KB_ANTHROPIC_API_KEY` presence in the worker container (catches the .env-vs-host-env footgun) + a conditional branch on the model_id assertion that mirrors the factory's auto-probe order (Gemini → Anthropic → Identity). Identity-path assertions preserved; Gemini/Anthropic branch adds `contextual_text LIKE '%' || chunk_text` (prefix present) + `cache_creation_input_tokens > 0` (billed-input recorded) + (Gemini-only) `cache_read_input_tokens = 0` (no explicit cache per §5.8.1 #4). Local run: 16/16 GREEN on Identity path (`.env` has no contextualizer keys — Gemini branch dormant; will activate when user adds `KB_GEMINI_API_KEY` to .env). Also closed the `.env.example` consistency gap (flagged in the 2026-05-23 consistency-sweep discussion): added documented placeholders for all 3 LLM keys (`KB_GEMINI_API_KEY`, `KB_ANTHROPIC_API_KEY`, `KB_MISTRAL_API_KEY`) + the new `KB_CONTEXTUALIZER` selector + commented `KB_CONTEXTUAL_MODEL`/`KB_EMBEDDING_MODEL` overrides + chunker tuning entries. Cross-phase sweep across all 9 verify scripts: **0:16/16 · 1a:17/17 · 1b:21/21 · 1c:20/20 · 2a:17/17 · 2b:15/15 · 3a:18/18 · 3b:16/16 · 3c:15/15 — 158 total, all GREEN**. Branch `phase-3/chunking-raptor` ready for merge or Phase 3d extension. | Aniket |
| 2026-05-24 | **Phase 3b-bis G4 ✅ — GeminiContextualizer + factory selector land.** `GeminiContextualizer` (~110 LOC) added to `src/kb/contextualization/__init__.py` alongside `AnthropicContextualizer` + `IdentityContextualizer`. Uses `google.genai.Client.aio.models.generate_content` with `types.GenerateContentConfig(system_instruction=..., max_output_tokens=200, thinking_config=types.ThinkingConfig(thinking_budget=0))`. Doc context lands in `system_instruction`; chunk text in `contents` (string). Decision #4 implemented: `usage_metadata.prompt_token_count` stored in `cache_creation_input_tokens` (= billed-input); `cache_read_input_tokens` stays 0 (no explicit cache used at demo scale). Decision #8 implemented: exception path captures `prompt_feedback.block_reason` if attached to exception or response, wraps into `ContextualizationError`. Defensive empty-candidates check covers safety-block responses. `make_contextualizer()` rewritten to a 4-value `KB_CONTEXTUALIZER` selector with `auto` probing Gemini → Anthropic → Identity (Gemini-first matches demo's single-key story). Explicit `gemini`/`anthropic` without matching key raises ValueError (loud-fail beats silent-fallback for misconfigs). **One in-G4 fix**: G3's `test_gemini_contextualizer_disables_thinking` used `getattr(...) or ...` to read `thinking_budget`, but `0 or x` short-circuits to `x` because 0 is falsy → test got `None` instead of asserting against `0`. Refactored to explicit `hasattr` branches. Full suite: 238/238 in 61.5s (232 prior + 6 new). G5 opens: extend `verify_phase_3b.sh` with a Gemini-path E2E branch + cross-phase sweep. | Aniket |
| 2026-05-24 | **Phase 3b-bis G1 ✅ + G3 ✅ — plan signed off; red skeletons land.** Spec at `tests/specs/phase_3b_bis.md`. 6 new tests in `tests/test_contextualization_gemini_unit.py` (mocked `google.genai.Client.aio.models.generate_content` mirroring the `_MockAnthropicClient` pattern from 3b for side-by-side reviewability — same `last_kwargs` capture + `raise_exc` injection). Tests cover decisions #1/#3/#4/#6/#7/#8/#9 from §5.8.1. 1 mutated test: `tests/test_contextualization_unit.py::test_contextualizer_factory_returns_identity_when_no_api_key` renamed to `test_contextualizer_factory_selector_matrix` and widened from a 2-case binary check to an 8-case matrix covering all `KB_CONTEXTUALIZER` values (auto+none/auto+gemini/auto+anthropic/auto+both/explicit-gemini/explicit-anthropic/explicit-identity/bogus→ValueError). Decision #10 (worker test parameterization) deferred to G4 — it's a code-only refactor with no new assertion. Run state: 7/7 fail (RED expected); rest of suite 231/231 pass — no collateral damage. G4 opens: implement `GeminiContextualizer` (~50 LOC mirroring `AnthropicContextualizer` shape, swap to `google-genai` client) + widen `make_contextualizer()` to read `KB_CONTEXTUALIZER` with auto-probe. | Aniket |
| 2026-05-23 | **Phase 3b-bis G1 🟡 OPEN — Gemini Contextualizer adapter plan drafted.** Motivation: interview-submission demo runs on a single Gemini API key. Without 3b-bis, `KB_ANTHROPIC_API_KEY` unset → `IdentityContextualizer` no-ops contextual retrieval (Anthropic's 67% retrieval failure reduction is silently disabled). With 3b-bis, a `GeminiContextualizer` lands alongside `AnthropicContextualizer` and the factory `make_contextualizer()` is widened to a 4-value selector (`KB_CONTEXTUALIZER ∈ {gemini, anthropic, identity, auto}`, default `auto` probes Gemini key → Anthropic key → Identity). **Scope is deliberately tight:** no migration, no lifecycle change, no API contract change. Reuses §5.8's `Contextualizer` Protocol verbatim, the Anthropic cookbook prompt verbatim (model-agnostic recipe), and the worker-level tests via parameterization on `KB_CONTEXTUALIZER`. Adds 1 new unit-test file (~6 tests) + extends `verify_phase_3b.sh` with a Gemini-path E2E branch. Decision #4 captures the Gemini caching semantics: `cache_creation_input_tokens` repurposed to hold Gemini's `prompt_token_count` (billed-input tokens, no explicit cache used at demo scale; revisit at scale). Decision #2 establishes the auto-selector probing order so the demo is zero-config when only `KB_GEMINI_API_KEY` is set. §5.8.1 added to build_tracker; §5 phase table gains a 3b-bis row. Estimated wall-clock once signed off: ~1 hr for G3+G4+G5 combined (adapter pattern is already paved). Awaiting Aniket sign-off on the plan. | Aniket |

---

## 10. Reading order for a fresh reviewer

1. This file → understand discipline + current state.
2. [`README.md`](../README.md) → mental model.
3. [`docs/architecture.md`](architecture.md) → full system spec.
4. [`docs/ui_design.md`](ui_design.md) → screen-by-screen UX.
5. [`docs/gaps_design.md`](gaps_design.md) → 9 detailed designs.
6. Stress-tests & audits as needed (`scenarios.md`, `red_team.md`, `citations_audit.md`, `competitive_audit.md`, `scale_perf_audit.md`).
