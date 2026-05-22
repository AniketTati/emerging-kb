# UI Design — Locked

> **Source of truth:** the clickable prototype at [`prototype/`](../prototype/). This document is its written companion — IA, per-screen purpose, key interactions, wiring references. When the two disagree, the prototype wins until the discrepancy is fixed here.
>
> **Status:** locked 2026-05-22 after a 10-screen review. All interactive elements mapped to backend endpoints in [`prototype/wiring_inventory.md`](../prototype/wiring_inventory.md).
>
> **Predecessor:** the prior version (ASCII mockups, pre-prototype IA) is preserved at [`docs/archive/ui_design_v1.md`](archive/ui_design_v1.md) for historical reasoning.
>
> **A note on the example content in the prototype.** The Aakash Constructions / Vertex Logistics / Aurangabad / ₹4,80,00,000 examples used throughout the prototype are **architectural illustrations** — concrete enough to demo a real KB workflow, but they are *not* the actual demo corpus content. The locked demo corpus is **CUAD + Enron + SEC 10-K + scans + xlsx (~80–100 docs)** per `architecture.md` §15. The illustrations exist to make the prototype feel real; the demo runs against the locked public-dataset corpus.

---

## 1. Mental model — the "smart workbook"

A giant workbook that **fills its own sheets in by reading your documents.** Each sheet = one doc-type. Each row = one atomic unit (clause / transaction / row / component). Two cross-cutting sheets — People & Orgs, Relationships — connect everything across all docs. The chat queries across all of them.

The system is **automatic by design** — schema auto-promotes from emerging fields, entities auto-resolve, doc-types auto-classify. Humans intervene only when (a) confidence is low, (b) something needs correcting, or (c) they want to inspect the system's reasoning.

---

## 2. Two users, one product

| User | % of opens | Primary surface | Primary job |
|---|---|---|---|
| **Knowledge worker** | ~95% | `/chat` | Ask question → get cited answer → click source to verify |
| **Admin / power user** | ~5% | Studio + Admin | Inspect, correct, configure, audit |

Chat is the front door. Everything else is reachable from the sidebar but lives behind it.

---

## 3. Information architecture

The sidebar (collapsed to 56px icons by default, expands on hover) groups everything into three buckets:

```
🏠 PRIMARY                        — the 95% surface
  💬 Chat                         — front door
  📤 Upload                       — drag-drop with live ingestion status
  🔍 Explore                      — Knowledge Explorer (progressive expansion)

🧪 STUDIO                         — power-user work surfaces
  🧠 Schema Studio                — Typed · Inferred · Collisions · Vocabulary · Lineage · Versions
  ⚗️  Extraction Studio            — per-doc PDF review · approve/edit/reject · prompt editor · test mode
  🎛️  Playground                   — sandbox for queries · eval suite · A/B compare configs

📊 ADMIN                          — operations + governance
  📊 Dashboard                    — counts · "what the system just learned" · top anomalies
  📋 Audit                        — immutable per-query logs
  ⚙️  Settings                     — workspace · models · API / swagger
```

**Universal surfaces** (reachable from every page):

- **Doc Detail slide-in panel** — any doc / citation / entity / clause anywhere → opens the same panel. See [`prototype/doc-detail.html`](../prototype/doc-detail.html).
- **⌘K global palette** — jump to any doc, entity, schema field, Studio tool, or setting from any screen.
- **Theme toggle** — light default, dark on demand.

---

## 4. Cross-cutting design rules (enforced at QA · §0.2 of `build_tracker.md`)

These hold on **every** screen. A page that violates one of them fails QA.

| Rule | What it means in UI |
|---|---|
| **Schema visible everywhere** | Wherever a field value is shown, its typed/inferred badge appears, and the field name links to Schema Studio at that field. No "view-only schema" surface. |
| **Schema editable everywhere** | Every shown field value has an edit affordance — inline edit on the current doc, or a one-click jump to Schema Studio to change the definition globally. |
| **Doc Detail universal** | Any doc / citation / entity / clause is one click away from opening the same Doc Detail slide-in. Wiring uses `/docs/{id}/detail`. |
| **⌘K reachable** | Every page has the ⌘K hint top-right. Palette opens with `GET /search?q=&types=` for fuzzy jump-anywhere. |
| **Streaming over spinners** | Long operations stream (ingest stages, chat responses, learning events). No centered spinners. |
| **Trust signals on every derived value** | Every system-produced value (answer, extracted field, anomaly score, promoted field) shows confidence + source. |

---

## 5. 2026 GenAI design patterns we apply

| Pattern | Where it shows up |
|---|---|
| **Inspectable AI** — every answer has a collapsible "How I answered" with planner mode, channels fired, rerank pass, faithfulness check, latency, cost | Chat answer footer; Playground always-expanded |
| **Studio = direct manipulation** — schema edits show inline diff + impact preview *("412 contracts, ~$4, ~3 min")* before commit | Schema Studio Inferred tab; Extraction Studio prompt editor; Test mode |
| **Progressive disclosure** — Knowledge Explorer expands on click; no monster graph dump | Explore entity card → Related rows → first 3 items + "view all"; LazyGraph pattern via "Show as graph (lazy)" button |
| **Universal Doc Detail** | One panel, reused from chat / explore / upload / extraction / audit |
| **Light, restrained palette** — single accent (zinc-900) used sparingly; lucide line icons; no decorative color | All 10 screens |
| **Citation cards as first-class** | Chat right rail; each card carries modality icon · page/section · snippet · confidence · rarity · edit affordances |
| **Live "what just learned" feed** | Dashboard center column; SSE-driven |
| **A/B before promote** | Playground Compare tab; Extraction Studio Test mode |

---

## 6. The 10 screens

For each screen below: purpose (JTBD) · key interactions · prototype file. Full wiring (every interactive element → API endpoint) lives in [`prototype/wiring_inventory.md`](../prototype/wiring_inventory.md).

**Wave assignment** (cross-ref `architecture.md` §12):

| Surface | Wave | Phase(s) |
|---|---|---|
| Chat + Doc Detail | **A** | 10b |
| Upload | **A** | 10a |
| Explore | **A** | 10c |
| Schema Studio (6 tabs) | **A** | 10d + Designs 6 / 7 / 9 |
| Dashboard | **A** | 10e |
| Audit | **A** | 10f + Phase 9 |
| Settings (+ `/swagger`) | **A** | 10g |
| Playground (basic query + eval matrix) | **A** | 12 |
| Playground (Compare configs + advanced) | **B** | 14b |
| Extraction Studio | **C** | 23 |

All 10 surfaces are prototyped here to lock the IA + interactions before code. Wave C surfaces ship as written-up future work, not as code in the MVP.

### 6.1 💬 Chat — front door

**Prototype:** [`prototype/chat.html`](../prototype/chat.html)

**Purpose:** Ask a question, get a cited answer, verify the source.

**Layout:** 3 columns — narrow sidebar · centered chat thread · right rail of citation cards (~360px).

**Key interactions:**
- Composer with `@ doc filter`, `📎 attach`, `🧠 deep_research` toggle, `📊 batch matrix` toggle, cost-per-query estimate inline
- Assistant answer shows `grounded · 92%` confidence pill + `no conflicts` flag + inline citations `[1] [2] [3]`
- "How I answered" inspector collapsed by default — expand to see planner mode, rewriter, channels fired, RRF fusion, rerank, anchor hop, faithfulness check, conflicts
- Followup chip suggestions
- Citation card per source: modality (PDF · Aggregate · Scan) · location (page/section) · snippet · structured metadata pills (rarity · field=value · OCR confidence) · `Doc Detail` + `Show in PDF` actions
- "5 more retrieved" accordion for full retrieval transparency
- `Re-run with deep_research` from the inspector

**Notable cross-cutting:** field-value pills (`delivery_window=4h`) link to Schema Studio; doc filenames in citations link to Doc Detail; aggregation citation has audit-artifact link; query-id pill links to Audit.

### 6.2 📤 Upload — drag-drop + live ingestion

**Prototype:** [`prototype/upload.html`](../prototype/upload.html)

**Purpose:** Drop docs, watch them process per-stage in real time, fix anything that fails.

**Layout:** Single column. Dashed drop zone on top; filter chips + search + bulk action row; live table.

**Key interactions:**
- Drop / click-to-browse for files, folders, ZIPs (PDFs digital + scanned, xlsx, csv, jpg, png, eml)
- Live counts header: `87 ready · 5 processing · 2 failed`
- Filter chips: All / Processing / Ready / Failed / Needs-attention
- Doc-type filter dropdown + text search
- Bulk action: `Re-run failed`
- Table columns: File · Type · Stage · Elapsed · Detected · Actions
- Stage pips (5-dot pipeline: Parse → Contextualize → Extract → Resolve → Index); current stage pulses
- Row expand reveals per-stage breakdown with adapter + timing + counts, plus doc-type/entities/atomic-units summary
- Failed rows: inline error + recovery actions (Re-run with VLM fallback · Replace with higher-res · View diagnostics)
- Cross-cutting: filenames link to Doc Detail; doc-type cells link to Schema Studio for that type

### 6.3 🔍 Explore — Knowledge Explorer

**Prototype:** [`prototype/explore.html`](../prototype/explore.html)

**Purpose:** Browse what's in the KB. Search across categories. Progressive expansion — no graph dump.

**Layout:** 3 columns — sidebar · left rail (View as + Filter by, 220px) · result list (centered).

**Key interactions:**
- Universal search box (works across entities, docs, atomic units, chunks)
- Left rail "View as" buttons: All / Documents / Doc types / Atomic units / Entities / Relationships / Topics / Anomalies — each with live count
- Left rail "Filter by": doc-type checkboxes, date range, has-anomaly / has-conflicts / has-chain
- Results grouped by category (when "All"); each card expandable inline
- Entity card expanded shows: 1-line description · Related panel with **2-level progressive expansion** (▶ 17 Contracts → click → first 3 + "view all"); canonical metadata (canonical name · aliases · first/last mention); actions (Open Doc Detail · Show as graph lazy · Suggest merge · Edit canonical)
- Atomic unit cards show clause snippet · location · rarity · extracted fields
- Doc cards show doc-type badge · filename · date · pages · summary
- Relationship rows compact: `Aakash Constructions — procurer → Vertex Logistics` with provenance
- Cross-cutting: every doc name → Doc Detail; every field pill → Schema Studio; every type badge → Schema Studio

### 6.4 🧠 Schema Studio

**Prototype:** [`prototype/schema-studio.html`](../prototype/schema-studio.html)

**Purpose:** See what the system has learned about your data shape. Edit, promote, merge, version. The system runs automatically — humans intervene rarely, but when they do this is where.

**Layout:** Sidebar · tab bar (6 tabs) · per-tab content (with its own doc-type rail when relevant).

**Tabs:**

| Tab | Content |
|---|---|
| **Typed** | Locked, auto-promoted fields per doc-type. Table with field · type · description · coverage. Add field manually. Per-row edit/rename/delete with impact preview. |
| **Inferred (default)** | Per doc-type field cards with **threshold bar** (prevalence) + marker at 0.80 cutoff. Status pills (just-promoted / N docs away / emerging). Expanded card shows 4 thresholds vs targets, sample values from named source docs, inferred type, first-proposed date. Actions: Promote override / Rename / Merge with… / Discard. **Impact preview banner** appears for destructive ops. |
| **Collisions** | Same field name in 2+ doc-types with different types. Side-by-side cards with primary recommendation + alternatives. |
| **Vocabulary** | Synonyms · Acronyms · Definitions. Add entry · edit · confidence-scored. Used for query expansion and L2b disambiguation. |
| **Lineage** | Two panels: containment ltree (contract → parties → clauses → delivery → window/penalty) and doc-revision chain (v1 draft → v2 redline → v3 signed). |
| **Versions** | Time-ordered list of every schema change. Auto-saved · reversible. Each version shows the delta + cost + time. |

**Auto-promotion thresholds (locked, README "Three principles" + Open-choice #3, mirrored in `architecture.md` §15):** prevalence ≥ 0.80 · stability ≥ 0.90 · vt-confidence ≥ 0.90 · min_docs = 20 (prod) / 5 (demo).

**Schema swap demo affordance:** the Schema Studio header carries a "Switch schema" action (e.g., `legal_contract → corporate_email` on the same uploaded data). Triggers an impact preview (docs re-projected · cost · time · data-loss) before commit. L1 parse + L2 mentions + L2b emergent fields stay; only L3/L4 schema-projection reruns. Live progress shown on Upload.

### 6.5 ⚗️ Extraction Studio

**Prototype:** [`prototype/extraction-studio.html`](../prototype/extraction-studio.html)

**Purpose:** Per-doc verification when extraction needs human eyes. Three triggers: (1) field confidence below threshold, (2) correction filed elsewhere, (3) manual browse-in. **The system does not require this for routine operation.**

**Layout:** Sidebar · tab bar (3 tabs) · 3-column workspace.

**Tabs:**

| Tab | Content |
|---|---|
| **Per-doc review (default)** | Doc queue (left, 300px) · PDF preview with bbox highlights (center) · detected fields with approve/edit/reject (right, 400px). Default filter is **All** (not Needs-review). Each needs-review row shows the trigger inline (e.g., `jurisdiction conf 0.65 below 0.70`). Smart callout: when same field has been edited 3+ times across docs, suggest schema-level fix → jump to Prompt editor. |
| **Prompt editor** | YAML rules per doc-type. Editable. `Test changes →` switches to Test mode. `Save` creates new schema version + triggers re-projection. |
| **Test mode** | Pick a doc → run new prompt → side-by-side diff vs current production version. **Impact preview** (docs re-projected · cost · time · conf delta) before save. |

**Coherence:** the three tabs are chained — review reveals a recurring issue → Prompt editor fixes the rule → Test mode validates before save.

### 6.6 🎛️ Playground

**Prototype:** [`prototype/playground.html`](../prototype/playground.html)

**Purpose:** Sandbox for tuning retrieval and answer behavior without affecting production or audit history.

**Layout:** Sidebar · tab bar (3 tabs) · 2-column.

**Tabs:**

| Tab | Content |
|---|---|
| **Single query (default)** | Left (360px): query input · planner mode dropdown · 10 retrieval channel chips · 4 quality-gate checkboxes · 4 parameter sliders · doc scope · Run button with ⌘↵. Right: summary row · streamed answer · 8-step retrieval trace · top-10 candidates table with per-channel scores + cited indicators. |
| **Eval suite** | 9 strata × 5 questions = 45-question regression matrix (per `architecture.md` §9: needle · rare-clause · adversarial · long-form · ambiguous · negative · aggregation · chain-aware · conflict-resolution). The prototype labels are illustrative; the canonical stratum names live in `architecture.md`. Summary cards (pass rate · latency · cost · faithfulness · regressions). "Run all" with cost estimate. Click any cell for question detail. |
| **Compare configs** | A/B side-by-side. Same query, two configs. Verdict block with cost/recall tradeoff analysis. "Save B as new default" promotes the proposed config to workspace defaults. |

### 6.7 📊 Dashboard

**Prototype:** [`prototype/dashboard.html`](../prototype/dashboard.html)

**Purpose:** Operations view for admins. KB health, what's learning, what needs attention — all in one glance.

**Layout:** 4 stat cards · two-column (live feed left, attention list right) · doc-type breakdown bar chart · bottom row (ingestion health, query activity, cost).

**Key elements:**
- Header shows `live` indicator with pulsing dot
- Time range selector (24h / 7d / 30d / All / Custom) — scopes everything
- Stat cards with 7-day sparkline bars (Documents · Atomic units · Entities · Relationships) + breakdown sub-line
- "What the system just learned" — live SSE feed with auto-promotions, new doc-types, entity merges, prevalence crossings, anomalies, doc-chain detection, synonym discovery, new canonical entities, corrections filed. Filterable. Each event one-click to its source.
- "Needs attention" — opinionated, not exhaustive: docs awaiting review · collisions to disambiguate · top anomalies (with rarity scores) · open corrections · failed uploads
- Corpus by doc-type — horizontal bar chart, typed (dark) vs inferred (faint)
- Ingestion health / Query activity / Cost cards with drill-down links

### 6.8 📋 Audit

**Prototype:** [`prototype/audit.html`](../prototype/audit.html)

**Purpose:** Immutable per-query log. Compliance-ready provenance. Quality monitoring.

**Layout:** 5 summary stat cards · filter bar · query log table with inline expand.

**Key interactions:**
- Filters: time range · user · status (answered / refused / errored) · feedback (👍 / 👎 / has correction)
- Search across query text, doc-id, entity, user
- Summary stats: queries · cost · time · confidence · feedback (👍/👎 + corrections)
- Each row: time · user · query preview · confidence · cost · latency · status · feedback
- Row expand reveals **two-column trace**: left (full query · full answer · cited sources with rerank scores · 7-step retrieval trace), right (feedback panel · metadata · 4 actions)
- Actions per row: **Re-run with current config** (replay against today's KB) · Copy as cURL · Open in chat · **Add to regression set**
- Export filtered logs as CSV/JSON

### 6.9 📑 Doc Detail (universal slide-in)

**Prototype:** [`prototype/doc-detail.html`](../prototype/doc-detail.html)

**Purpose:** Everything about one doc, opened from anywhere. **Built around the primary JTBD: verify a cited claim in 2 seconds, zero scroll.**

**Layout:** Slide-in panel from the right edge (820px) over a dimmed parent page.

**Hero zone (no scroll):**
- Header: file icon · filename · doc-type badge · "12 pages · signed 2024-11-14" · "Opened from chat citation [1] — Aurangabad supplier inquiry" breadcrumb · Open in tab · Share · close X
- 2-column: **PDF preview** (320px) of the cited page with the cited region highlighted (active bbox pulses) · **Cited clause card** with clause text · rarity · extracted fields as pills · confidence · `wrong?` feedback button
- 4 key facts row: Procurer · Supplier · Signed · Value

**Accordions (all collapsed by default, summary in header):**
- All extracted fields (8 · 6 typed · 2 inferred · avg 0.89)
- All clauses in this doc (4 · 1 anomalous)
- Entities mentioned (23 → 19 canonical)
- Relationships in this doc (7)
- Revision history (3 versions · current = v3)
- Usage & processing (cited 3 · retrieved 12 · ingested 56s)

**Sticky footer:** Re-extract · Replace · Export · Delete + keyboard hint `Esc to close · ⌘↑↓ to navigate docs`

### 6.10 ⚙️ Settings

**Prototype:** [`prototype/settings.html`](../prototype/settings.html)

**Purpose:** Workspace configuration. Pick models, set defaults, manage API access, expose `/swagger`.

**Layout:** 3-group nav (Workspace / Developers / Account) · content area.

**Sections (Models & retrieval is shown as the default in the prototype):**

| Group | Sections |
|---|---|
| **Workspace** | General · Members & access · **Models & retrieval** (LLM / Embedding / Reranker / Parser preferences / Retrieval defaults including channels and refusal+faithfulness sliders) · Auto-discovery · Ingestion · Cost & limits · Notifications |
| **Developers** | API keys · API docs (link to `/swagger`) · Webhooks · Storage & retention |
| **Account** | Profile · Danger zone |

**`/swagger` exposure:** auto-generated OpenAPI spec from the live FastAPI service. Prominent card with `Open /swagger`, `Download openapi.json`, version, endpoint count, and active API keys list (masked previews, last-used audit).

**Embedding-model swap warning:** changing the embedder triggers re-embedding of all chunks (~$1,500 at 100K-doc scale) — explicit warning in the UI.

**Effective Config (layered config) resolved view** — lives under the *Auto-discovery* settings section. Shows every config key with the layer that produced it (defaults → domain → workspace → doc-type → doc → user override), with one-click override + revert. Closes `gaps_design.md` Design 9 (Hydra + DB layered configuration) on the UI side.

---

## 7. Where you intervene vs. what the system does

| Task | System does | You intervene when |
|---|---|---|
| Parse a new doc | Auto · multi-parser chain · adapts to modality | Parser fails or OCR is low-conf (Upload row turns "failed") |
| Classify doc-type | Auto · proposes new types after N docs | The proposal is wrong (Schema Studio Collisions) |
| Discover fields | Auto · L2b emerging per doc-type | Names need renaming or two doc-types collide (Schema Studio) |
| Promote a field to typed | Auto · when 4 thresholds clear | Override to force-promote, or revert (Schema Studio Inferred) |
| Resolve entities | Auto · deterministic → embedding → LLM judge → union-find | Wrong merge (Explore "Suggest merge" or chat `wrong?`) |
| Answer a query | Auto · 12 planner modes · 10 channels · RRF · rerank · faithfulness | Answer is wrong (chat 👎 → correction → re-extraction) |
| Detect anomalies | Auto · per-doc-type rarity scoring | Investigate or dismiss (Dashboard Needs-attention) |

---

## 8. Demo flow (end-to-end, ~10 minutes)

The flow that lands the "magic" moments hardest:

1. **Open Dashboard — empty workspace.** Counts at 0. Learning feed empty.
2. **Drop the first 5 contract docs in Upload.** Live stage pips animate per doc. Within seconds:
   - Dashboard learning feed fires: *"new doc-type proposed: contract (5 docs, 8 fields tracking)"*
   - Schema Studio Inferred tab shows the new type with all 8 fields and prevalence bars
3. **Click any doc → Doc Detail slide-in.** Show the cited clause hero · extracted fields with badges · revision chain · entities · relationships.
4. **Drop 15 more docs.** Watch the Learning feed:
   - prevalence numbers tick upward
   - field `delivery_window` crosses 0.80 → auto-promotion fires live
   - Schema Studio Typed gains the field with a "just promoted" badge
5. **Switch to Explore.** Browse the 4 doc-types (3 inferred + 1 typed), 412 clauses across all contracts, 312 canonical entities with merge counts.
6. **Open Chat.** Ask:
   - **Retrieval:** *"What's the indemnity cap in our license agreements?"* → multi-doc cited answer.
   - **Aggregation:** *"Total aggregate indemnification cap across all contracts."* → templated answer + audit-artifact citation.
   - **Multi-hop:** *"Which contracts share the same arbitration venue as the Enron/EPE agreement?"* → entity + relationship walk.
   - **Conflict resolution:** *"What's the indemnity cap in the Enron / El Paso power-supply contract?"* → conflict card surfaces original ($25M) vs amendment ($50M) with chain-aware resolution.
   - **Refusal:** *"What was our Q4 2026 revenue?"* → graceful refusal listing what was searched.
7. **Open Schema Studio · Inferred.** Show the still-emerging fields. Drop 3 more docs → watch one promote live with the impact preview.
8. **Schema swap.** Switch from contract schema to corporate-email schema on the same uploaded data. Atomic Units sheet repopulates with email-typed units in seconds. **No re-parse.**
9. **Open Explore → Anomalies.** The 4-hour delivery clause at rarity 0.99. Click → Doc Detail with the clause highlighted in the PDF.
10. **Open Audit.** A single query's full trace — plan, channels fired, candidates, scores, judges, decision. Click `Re-run with current config` → answer regenerated against current schema.
11. **Open Playground · Eval suite.** Show the 45-question regression matrix · 87% pass · 0 regressions.
12. **Open Settings → /swagger.** The 100-endpoint OpenAPI spec, auto-generated, ready for integration.

The "watch the schema grow" moment in step 4 — auto-promotion firing on screen with no human click — and the schema-swap moment in step 8 are the two demos that land hardest.

---

## 9. How this doc relates to others

| Doc | Role |
|---|---|
| **[`prototype/*.html`](../prototype/)** | Source of truth for visuals + interactions. Open in a browser to click through. |
| **[`prototype/wiring_inventory.md`](../prototype/wiring_inventory.md)** | Every interactive element → planned backend endpoint. Input set for G2 (API contracts). |
| **[`prototype/qa_checklist.md`](../prototype/qa_checklist.md)** | Visual QA checklist applied per page per viewport. |
| **[`prototype/qa/reports/`](../prototype/qa/reports/)** | Per-page QA reports with screenshots + auto-check results. |
| **[`docs/architecture.md`](architecture.md)** | What the system does. The "engine room" — backend layers, retrieval pipeline, eval design. |
| **[`docs/gaps_design.md`](gaps_design.md)** | 9 detailed gap designs (aggregation, conflicts, doc chains, feedback loop, citations, vocabulary, lineage, conversational context, layered config). |
| **[`docs/build_tracker.md`](build_tracker.md)** | The gate-by-gate build discipline. UI design is locked at G1.5a + G1.5b; wiring at G1.6; contracts at G2. |
| **[`docs/archive/ui_design_v1.md`](archive/ui_design_v1.md)** | Pre-prototype design doc (ASCII mockups, old IA). Preserved for historical reasoning. |
