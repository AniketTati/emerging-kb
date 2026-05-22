# Wiring Inventory

> Every interactive element across all 10 prototype screens, mapped to its planned backend interaction. This is the input set for G2 (API contracts) — every `PLAN` row becomes an endpoint contract.
>
> **Status legend:**
> - `PLAN` — needs a real backend endpoint / contract; will be specified at G2
> - `LOCAL` — client-side only (UI state, routing, clipboard, modal toggle, etc.)
> - `SSE` — server-sent-event stream; contract specified at G2
> - `DECIDE` — purpose unclear; needs a decision before we move to G2
> - `REMOVE` — no real purpose; strip from prototype

---

## Shared shell (sidebar + top bar) — applies to every page

| Element | Behavior | Wiring | Status |
|---|---|---|---|
| App sidebar nav (Chat / Upload / Explore / Schema / Extraction / Playground / Dashboard / Audit / Settings) | Route to page | client-side router | LOCAL |
| `K` logo (top-left of sidebar) | Route to index | client-side router | LOCAL |
| User profile button (bottom of sidebar) | Open profile menu | `GET /me` then LOCAL menu | PLAN |
| ⌘K palette button (top-right) | Open global command palette (jump anywhere) | client + `GET /search?q=&types=` | PLAN |
| Theme toggle (sun icon) | Toggle light/dark | `localStorage` only | LOCAL |

---

## 1. Chat (`chat.html`)

### Top bar
| Element | Behavior | Wiring | Status |
|---|---|---|---|
| Breadcrumb conversation title | Display current chat title | `GET /chats/{id}` | PLAN |
| `+ New chat` | Create new conversation | `POST /chats` → route to new id | PLAN |

### Chat thread
| Element | Behavior | Wiring | Status |
|---|---|---|---|
| Composer textarea | User input | LOCAL | LOCAL |
| `@` doc filter button | Open doc picker overlay | `GET /docs?q={typed}&limit=10` | PLAN |
| `📎 attach` | File upload to this conversation | `POST /chats/{id}/attachments` | PLAN |
| `🧠 deep_research` toggle | Set flag for next message | LOCAL state → param on send | LOCAL |
| `📊 batch matrix` toggle | Set flag for next message | LOCAL state → param on send | LOCAL |
| `Send` button (or `⌘↵`) | Submit query | `POST /chats/{id}/messages` (streams response) | PLAN + SSE |
| Streaming caret on assistant reply | Show response is generating | SSE `/chats/{id}/messages/{mid}/stream` | SSE |
| Confidence pill ("grounded · 92%") | Display answer confidence | comes with response | PLAN (read) |
| "no conflicts" pill | Display conflict-detection result | comes with response | PLAN (read) |
| `How I answered` accordion | Expand/collapse plan trace | LOCAL (data in response) | LOCAL |
| Followup chips | Suggested follow-up queries | comes with response; click → new message | PLAN (read) + send |
| `👍 / 👎` | Submit feedback | `POST /chats/{id}/messages/{mid}/feedback` | PLAN |
| `Copy` | Copy answer to clipboard | `navigator.clipboard` | LOCAL |
| `Share` | Create shareable link | `POST /chats/{id}/share` → URL | PLAN |
| `Copy trace` (in plan inspector) | Copy plan trace JSON | LOCAL clipboard | LOCAL |
| `Re-run with deep_research` | Resubmit query with flag | `POST /chats/{id}/messages` with flag | PLAN |
| `Flag answer` | Create correction | `POST /corrections` (scope=answer) | PLAN |

### Citations panel (right)
| Element | Behavior | Wiring | Status |
|---|---|---|---|
| Filter chips (all / pdf / xlsx / scan) | Filter visible citations | LOCAL (citations already loaded) | LOCAL |
| Citation card body (hover) | Visual hover state | LOCAL | LOCAL |
| `Doc Detail` link on each card | Open Doc Detail slide-in for the cited doc + clause | `GET /docs/{id}/detail?focus_atomic_unit={uid}` | PLAN |
| `Show in PDF` | Open PDF preview to the bbox | `GET /docs/{id}/pdf?page={n}&bbox={...}` | PLAN |
| `Calc trace` (aggregate citation only) | Open the audit artifact for an aggregation | `GET /audit/aggregations/{id}` | PLAN |
| `Re-run` (aggregate citation) | Recompute the aggregation | `POST /aggregations/{id}/recompute` | PLAN |
| `Verify OCR` (scan citation) | Open OCR diagnostics for a scanned page | `GET /docs/{id}/pages/{n}/ocr-diagnostics` | PLAN |
| "5 more retrieved" accordion | Show retrieved-but-not-cited list | LOCAL (in plan trace already) | LOCAL |

---

## 2. Upload (`upload.html`)

### Top bar
| Element | Behavior | Wiring | Status |
|---|---|---|---|
| Live counts ("87 ready · 5 processing · 2 failed") | Show ingestion state | SSE `/events/ingestion-counts` or `GET /stats/ingestion` | SSE |

### Drop zone + filters
| Element | Behavior | Wiring | Status |
|---|---|---|---|
| Drop zone (file/folder/ZIP drop) | Upload doc(s) | `POST /docs` (multipart) | PLAN |
| `click to browse` | Native file picker → upload | same `POST /docs` | LOCAL trigger + PLAN |
| Filter chips (All / Processing / Ready / Failed / Needs attn) | Filter doc list | `GET /docs?status={...}` | PLAN |
| Search input | Filter by filename/type/entity | `GET /docs?q={...}` | PLAN |
| `Re-run failed` | Bulk action on failed docs | `POST /docs/re-run?status=failed` | PLAN |

### Doc table
| Element | Behavior | Wiring | Status |
|---|---|---|---|
| Row expand (chevron) | Show per-stage detail | LOCAL (data pre-loaded with row) | LOCAL |
| Stage pips (5-dot pipeline) | Visual progress | SSE `/docs/{id}/stages` | SSE |
| `open` link on ready doc | Open Doc Detail | `GET /docs/{id}/detail` | PLAN |
| `Open Doc Detail` (in expanded row) | Open Doc Detail | `GET /docs/{id}/detail` | PLAN |
| `Preview PDF` | Open PDF viewer | `GET /docs/{id}/pdf` | PLAN |
| `Re-extract` | Run extraction again | `POST /docs/{id}/re-extract` | PLAN |
| `re-run` on failed row | Retry failed stage | `POST /docs/{id}/re-run?stage={...}` | PLAN |
| `Re-run with VLM fallback` | Retry with different parser | `POST /docs/{id}/re-extract?strategy=vlm` | PLAN |
| `Replace with higher-res scan` | Upload replacement doc | `PUT /docs/{id}` (multipart) | PLAN |
| `View OCR diagnostics` | Open diagnostics modal | `GET /docs/{id}/ocr-diagnostics` | PLAN |

---

## 3. Explore (`explore.html`)

### Search + filters
| Element | Behavior | Wiring | Status |
|---|---|---|---|
| Universal search box | Search across all categories | `GET /search?q={...}` | PLAN |
| `clear` button | Clear search | LOCAL | LOCAL |
| `⏎ search` hint | Keyboard shortcut | LOCAL | LOCAL |
| Sort dropdown ("Sort: relevance") | Sort results | `GET /search?sort={...}` | PLAN |
| Left rail "View as" buttons (All / Documents / Doc types / Atomic units / Entities / Relationships / Topics / Anomalies) | Filter result category | `GET /search?types={...}` | PLAN |
| Filter checkboxes (doc type) | Narrow by doc type | `GET /search?doc_types={...}` | PLAN |
| Date range select | Filter by date | `GET /search?since={...}` | PLAN |
| "Has" checkboxes (anomaly / conflicts / chain) | Narrow by attribute | `GET /search?has={...}` | PLAN |
| `+ 8 more` button on doc-type filter | Show remaining facets | `GET /search/facets?dim=doc_type` | PLAN |

### Result cards
| Element | Behavior | Wiring | Status |
|---|---|---|---|
| Entity card expand | Show related items + canonical info | data is in search response | LOCAL |
| "Related" rows (Contracts / Projects / Invoices / Employees / Connected people / Anomaly) expand | Inline list of related items | `GET /entities/{id}/related?kind={...}` | PLAN |
| `view all →` link | Navigate to filtered Explore | route + `GET /search?...` | PLAN |
| `Open Doc Detail` | Open doc/entity detail panel | `GET /entities/{id}/detail` or `/docs/{id}/detail` | PLAN |
| `Show as graph (lazy)` | Open focused subgraph | `GET /entities/{id}/subgraph?hops=1` | PLAN |
| `Suggest merge` | Suggest merge candidates for this entity | `GET /entities/{id}/merge-candidates` | PLAN |
| `Edit canonical` | Edit entity canonical name/aliases | `PATCH /entities/{id}` | PLAN |
| Atomic-unit / doc / relationship cards expand | Show details | `GET /atomic-units/{id}` etc. | PLAN |
| `+ N more` buttons | Paginate within category | `GET /search?...&offset={...}` | PLAN |

---

## 4. Schema Studio (`schema-studio.html`)

### Top bar
| Element | Behavior | Wiring | Status |
|---|---|---|---|
| `Export YAML` | Export schema as YAML | `GET /schema/{doc_type}/export?format=yaml` | PLAN |

### Tabs
| Element | Behavior | Wiring | Status |
|---|---|---|---|
| 6 tabs (Typed / Inferred / Collisions / Vocabulary / Lineage / Versions) | Switch view | LOCAL (data per tab loaded on activate) | LOCAL + PLAN per tab |

### Inferred tab (default)
| Element | Behavior | Wiring | Status |
|---|---|---|---|
| Doc-type rail | Pick doc-type | `GET /schema/{doc_type}/inferred` | PLAN |
| `Filter` button | Open filter overlay | LOCAL | LOCAL |
| `Sort: prevalence` dropdown | Re-sort | LOCAL (already loaded) | LOCAL |
| Field card expand | Show thresholds + samples + type | data pre-loaded | LOCAL |
| `View in Typed` (on promoted field) | Switch tabs + scroll | LOCAL | LOCAL |
| `Revert promotion` | Undo auto-promotion | `POST /schema/{doc_type}/fields/{name}/revert` | PLAN |
| `Rename` | Rename inferred field | opens rename input → `PATCH /schema/{doc_type}/fields/{name}` | PLAN |
| `Promote now (override)` | Force-promote sub-threshold field | `POST /schema/{doc_type}/fields/{name}/promote` | PLAN |
| `Merge with…` | Merge with existing field | `POST /schema/{doc_type}/fields/merge` | PLAN |
| `Discard` | Reject inferred field | `DELETE /schema/{doc_type}/inferred/{name}` | PLAN |
| `+ 7 more emerging fields →` | Paginate | `GET /schema/{doc_type}/inferred?offset={...}` | PLAN |
| Impact preview banner | Show cost/time for proposed edit | `POST /schema/{doc_type}/fields/{name}/preview-impact?op=rename&new_name={...}` | PLAN |
| `Confirm rename` | Apply the rename | `PATCH /schema/{doc_type}/fields/{name}` | PLAN |
| `Cancel` | Dismiss banner | LOCAL | LOCAL |

### Typed tab
| Element | Behavior | Wiring | Status |
|---|---|---|---|
| Doc-type rail | Pick doc-type | `GET /schema/{doc_type}/typed` | PLAN |
| `Add field` | Add manual typed field | `POST /schema/{doc_type}/fields` | PLAN |
| Row `...` menu | Edit / delete / rename | each → `PATCH` or `DELETE` | PLAN |

### Collisions tab
| Element | Behavior | Wiring | Status |
|---|---|---|---|
| Collision card data | List ambiguous fields | `GET /schema/collisions` | PLAN |
| `Keep separate (recommended)` | Resolve collision | `POST /schema/collisions/{id}/resolve?action=keep_separate` | PLAN |
| `Merge as X` | Resolve | `POST /schema/collisions/{id}/resolve?action=merge` | PLAN |
| `Rename one` | Open rename flow | LOCAL → `PATCH` | PLAN |
| `Promote with format validator` | Resolve type ambiguity | `POST /schema/collisions/{id}/resolve?action=promote_with_validator` | PLAN |
| `Flag N docs for review` | Mark docs as needing review | `POST /docs/flag?ids={...}` | PLAN |

### Vocabulary tab
| Element | Behavior | Wiring | Status |
|---|---|---|---|
| Type rail (Synonyms / Acronyms / Definitions) | Filter | `GET /vocabulary?type={...}` | PLAN |
| `Add entry` | New vocab entry | `POST /vocabulary` | PLAN |
| Row body (click) | Edit entry | `PATCH /vocabulary/{id}` | PLAN |

### Lineage tab
| Element | Behavior | Wiring | Status |
|---|---|---|---|
| Containment tree display | Show schema hierarchy | `GET /schema/{doc_type}/lineage` | PLAN |
| Revision chain display | Show doc chains | `GET /docs/{id}/chain` | PLAN |

### Versions tab
| Element | Behavior | Wiring | Status |
|---|---|---|---|
| Version cards | List versions | `GET /schema/versions` | PLAN |
| `+ 14 more versions →` | Paginate | `GET /schema/versions?offset={...}` | PLAN |
| Version revert (action on each card, not yet visible) | Roll back | `POST /schema/versions/{v}/restore` | PLAN |

---

## 5. Extraction Studio (`extraction-studio.html`)

### Doc queue
| Element | Behavior | Wiring | Status |
|---|---|---|---|
| Search box | Filter docs | `GET /extraction/docs?q={...}` | PLAN |
| Status chips (All / Needs review / Edited) | Filter | `GET /extraction/docs?status={...}` | PLAN |
| Doc-type dropdown | Filter | `GET /extraction/docs?doc_type={...}` | PLAN |
| Sort dropdown | Order | `GET /extraction/docs?sort={...}` | PLAN |
| Doc row (click) | Select doc → load extraction | `GET /docs/{id}/extraction` | PLAN |

### PDF preview (center)
| Element | Behavior | Wiring | Status |
|---|---|---|---|
| Page nav (◄ ►) | Change page | `GET /docs/{id}/pages/{n}` | PLAN |
| Zoom in/out | Visual scale | LOCAL | LOCAL |
| Open external (↗) | Open full-page PDF viewer | route | PLAN (route) |
| Bbox click | Highlight + scroll field panel | LOCAL | LOCAL |

### Field panel (right)
| Element | Behavior | Wiring | Status |
|---|---|---|---|
| `Re-extract` (header) | Re-run extraction on this doc | `POST /docs/{id}/re-extract` | PLAN |
| `Approve all` (header) | Approve every field at once | `POST /docs/{id}/fields/approve-all` | PLAN |
| Per-field `Approve` | Mark field approved | `PATCH /docs/{id}/fields/{name}?action=approve` | PLAN |
| Per-field `Edit` | Open inline input | LOCAL → `PATCH /docs/{id}/fields/{name}` (save) | LOCAL + PLAN |
| Per-field `Save edit` | Persist edited value | `PATCH /docs/{id}/fields/{name}` | PLAN |
| Per-field `Cancel` | Drop edit | LOCAL | LOCAL |
| Per-field `Reject` | Mark as not-a-field | `PATCH /docs/{id}/fields/{name}?action=reject` | PLAN |
| `Undo reject` | Revert reject | `PATCH /docs/{id}/fields/{name}?action=undo_reject` | PLAN |
| Field name (link) | Open Schema Studio at this field | route | LOCAL |
| `Fix in Prompt editor →` (smart suggestion) | Switch tab to prompt editor | LOCAL tab switch + `GET /schema/{doc_type}/prompt` | LOCAL + PLAN |
| `Dismiss` (smart suggestion) | Hide callout | LOCAL | LOCAL |
| `→ Schema Studio` link | Navigate to field in Schema Studio | route | LOCAL |

### Prompt editor tab
| Element | Behavior | Wiring | Status |
|---|---|---|---|
| Doc-type rail | Switch | `GET /schema/{doc_type}/prompt` | PLAN |
| YAML editor | Edit prompt | LOCAL (code editor) | LOCAL |
| `Test changes →` | Switch to Test mode | LOCAL tab + carry-over the unsaved prompt | LOCAL |
| `Save · v1.4.3` | Save and re-project | `POST /schema/{doc_type}/prompt` (creates new version) | PLAN |

### Test mode tab
| Element | Behavior | Wiring | Status |
|---|---|---|---|
| Doc selector | Pick test doc(s) | `GET /docs?doc_type={...}` | PLAN |
| Version comparator | Pick comparison base | `GET /schema/{doc_type}/versions` | PLAN |
| `Run` | Execute extraction with proposed prompt | `POST /extraction/test` | PLAN |
| `Save & re-project` | Commit + re-project affected docs | `POST /schema/{doc_type}/prompt` → triggers job | PLAN |
| `Discard` | Drop unsaved prompt | LOCAL | LOCAL |

---

## 6. Playground (`playground.html`)

### Top bar
| Element | Behavior | Wiring | Status |
|---|---|---|---|
| `Save preset` | Save current settings | `POST /playground/presets` | PLAN |
| `Share` | Generate shareable URL of current config | `POST /playground/share` | PLAN |

### Single query tab
| Element | Behavior | Wiring | Status |
|---|---|---|---|
| Query textarea | User input | LOCAL | LOCAL |
| Planner mode dropdown | Param | LOCAL state | LOCAL |
| Channel chips | Param | LOCAL state | LOCAL |
| `all on` toggle | Quick toggle all channels | LOCAL | LOCAL |
| Quality gate checkboxes | Params | LOCAL state | LOCAL |
| Parameter sliders (hops / candidates / rerank-cited / temperature) | Params | LOCAL state | LOCAL |
| Scope dropdown | Param | `GET /docs/scopes` (for options) | PLAN |
| `Run query` button | Submit | `POST /query/sandbox` (with all params) | PLAN |
| Summary row (grounded%, time, cost, funnel) | Response metadata | comes with response | PLAN (read) |
| `copy answer` | Copy to clipboard | LOCAL | LOCAL |
| `share trace` | Share URL with full trace | `POST /audit/{query_id}/share` | PLAN |
| Retrieval trace steps | Display | comes with response | PLAN (read) |
| Candidates table | Display | comes with response | PLAN (read) |

### Eval suite tab
| Element | Behavior | Wiring | Status |
|---|---|---|---|
| `Filter` button | Open filters | LOCAL | LOCAL |
| Summary cards (pass rate / latency / cost / faithfulness / regressions) | Display | `GET /eval/summary` | PLAN |
| `Run all` button | Kick off full eval | `POST /eval/runs` | PLAN |
| Eval matrix cells (click) | Open question detail | `GET /eval/questions/{id}` | PLAN |

### Compare configs tab
| Element | Behavior | Wiring | Status |
|---|---|---|---|
| Query textarea | Input | LOCAL | LOCAL |
| Two config panels | Display each result | `POST /query/sandbox` x 2 | PLAN |
| Verdict block | Recommendation | derived from responses | LOCAL |
| `Save B as new default` | Promote config | `PATCH /workspace/retrieval-defaults` | PLAN |
| `Keep production` | No-op dismiss | LOCAL | LOCAL |

---

## 7. Dashboard (`dashboard.html`)

### Top bar
| Element | Behavior | Wiring | Status |
|---|---|---|---|
| Time range select (Last 24h / 7d / 30d / All / Custom) | Scope all data | `GET /stats?range={...}` (applies to many calls below) | PLAN |

### Stat cards
| Element | Behavior | Wiring | Status |
|---|---|---|---|
| Counts (docs / atomic units / entities / relationships) | Display | `GET /stats/counts?range={...}` | PLAN |
| Sparkline data | Display | `GET /stats/timeseries?metric={...}&range={...}` | PLAN |

### "What the system just learned" stream
| Element | Behavior | Wiring | Status |
|---|---|---|---|
| Event feed | Live events | SSE `/events/learning?since={...}` | SSE |
| Event filter dropdown | Filter event types | LOCAL (re-subscribe with filter) | LOCAL + SSE |
| Event row click | Navigate to source | route | LOCAL |
| `view all →` | Open full event log | route to `/audit?type=events` | LOCAL |

### Needs attention
| Element | Behavior | Wiring | Status |
|---|---|---|---|
| List data | Aggregate counts | `GET /stats/attention` | PLAN |
| Row click | Navigate to relevant page | route | LOCAL |

### Corpus by doc-type
| Element | Behavior | Wiring | Status |
|---|---|---|---|
| Bar chart data | Counts per doc-type | `GET /stats/by-doc-type` | PLAN |

### Bottom row
| Element | Behavior | Wiring | Status |
|---|---|---|---|
| Ingestion health (active/queued/stuck/latency) | Display | `GET /stats/ingestion` | PLAN |
| Query activity (24h queries/cost/faithfulness/refusal) | Display | `GET /stats/queries?range=24h` | PLAN |
| Cost this month | Display | `GET /stats/cost?range=month` | PLAN |
| `View pipeline →` / `View audit →` / `Cost breakdown →` | Navigate | route | LOCAL |

---

## 8. Audit (`audit.html`)

### Top bar
| Element | Behavior | Wiring | Status |
|---|---|---|---|
| `Export` | Export filtered log as CSV/JSON | `GET /audit/export?filters={...}` | PLAN |

### Summary stats
| Element | Behavior | Wiring | Status |
|---|---|---|---|
| 5 cards (queries / cost / time / confidence / feedback) | Display | `GET /audit/summary?filters={...}` | PLAN |

### Filters
| Element | Behavior | Wiring | Status |
|---|---|---|---|
| Time range / user / status / feedback selects | Apply filters | `GET /audit/queries?filters={...}` | PLAN |
| Search box | Filter | `GET /audit/queries?q={...}` | PLAN |

### Query log table
| Element | Behavior | Wiring | Status |
|---|---|---|---|
| Row expand | Show full trace | data pre-loaded with row | LOCAL |
| Cited source rows | List sources used | data in row payload | LOCAL |
| Source row click | Open Doc Detail | `GET /docs/{id}/detail` | PLAN |
| `Re-run with current config` | Re-execute query against today's KB | `POST /query/replay?audit_id={...}` | PLAN |
| `Copy as cURL` | Clipboard | LOCAL | LOCAL |
| `Open in chat` | Navigate to chat with this message | route + `GET /chats/?message_id={...}` | PLAN |
| `Add to regression set` | Add as a CI test | `POST /eval/regression-set` | PLAN |

---

## 9. Doc Detail (`doc-detail.html`)

### Header
| Element | Behavior | Wiring | Status |
|---|---|---|---|
| `Open in tab` | Full-page view | route to `/docs/{id}` | LOCAL |
| `Share` | Create share link | `POST /docs/{id}/share` | PLAN |
| Close X (or Esc) | Close panel | LOCAL | LOCAL |
| `⌘↑↓ navigate` (footer hint) | Move between adjacent docs in source list | LOCAL with `GET /docs/{id}/neighbors` | PLAN |

### Hero zone
| Element | Behavior | Wiring | Status |
|---|---|---|---|
| PDF preview page nav (◄ ►) | Change page | `GET /docs/{id}/pages/{n}` | PLAN |
| Bbox highlights | Show extracted regions | data in detail response | LOCAL |
| Cited clause card | Display | data in detail response | LOCAL |
| Field pills on cited clause (`delivery_window=PT4H` etc.) | Click → Schema Studio at field | route | LOCAL |
| `wrong?` button on cited clause | Create correction | `POST /corrections` (scope=citation) | PLAN |
| Key facts (Procurer / Supplier / Signed / Value) | Display | data in detail response | LOCAL |

### Accordions
| Element | Behavior | Wiring | Status |
|---|---|---|---|
| All accordion expands | Show pre-loaded data | LOCAL (loaded with detail) | LOCAL |
| `→ Schema Studio` field links | Navigate | route | LOCAL |
| Per-field edit pencil | Inline edit + save | `PATCH /docs/{id}/fields/{name}` | PLAN |
| Entity row click | Open entity profile | `GET /entities/{id}/detail` | PLAN |
| `+ N more →` buttons | Expand list | LOCAL or `GET` paginated | LOCAL/PLAN |

### Footer actions
| Element | Behavior | Wiring | Status |
|---|---|---|---|
| `Re-extract` | Re-run extraction | `POST /docs/{id}/re-extract` | PLAN |
| `Replace` | Upload replacement | `PUT /docs/{id}` (multipart) | PLAN |
| `Export` | Download original + extracted JSON | `GET /docs/{id}/export?format={...}` | PLAN |
| `Delete` | Soft-delete with confirmation | `DELETE /docs/{id}` | PLAN |

---

## 10. Settings (`settings.html`)

### Top bar
| Element | Behavior | Wiring | Status |
|---|---|---|---|
| `auto-saved` indicator | Display save state | reflects mutation state | LOCAL |

### Settings nav (left)
| Element | Behavior | Wiring | Status |
|---|---|---|---|
| Section nav (General / Members / Models / Auto-discovery / Ingestion / Cost / Notifications / API keys / API docs / Webhooks / Storage / Profile / Danger zone) | Switch section | LOCAL (each section is its own load) | LOCAL |

### Models & retrieval section
| Element | Behavior | Wiring | Status |
|---|---|---|---|
| Language model dropdown | Change LLM | `PATCH /workspace/settings/llm` | PLAN |
| Embedding model dropdown | Change embedder (triggers re-index) | `PATCH /workspace/settings/embedding` → background job | PLAN |
| Reranker dropdown | Change reranker | `PATCH /workspace/settings/reranker` | PLAN |
| Parser preference dropdowns (digital / scanned / xlsx / fallback) | Change parsers | `PATCH /workspace/settings/parsers` | PLAN |
| Default planner mode dropdown | Set workspace default | `PATCH /workspace/retrieval-defaults` | PLAN |
| Channel checkboxes | Set workspace default | same | PLAN |
| Refusal threshold slider | Set workspace default | same | PLAN |
| Faithfulness gate slider | Set workspace default | same | PLAN |
| "How I answered" toggle | UI default | `PATCH /workspace/ui-settings` | PLAN |

### API & docs section
| Element | Behavior | Wiring | Status |
|---|---|---|---|
| `Open /swagger` | Navigate | route to `/swagger` (FastAPI auto-generated) | LOCAL |
| `Download openapi.json` | Download spec | `GET /openapi.json` | PLAN (provided by framework) |
| `+ new key` | Create API key | `POST /api-keys` | PLAN |
| API key list | List active keys | `GET /api-keys` | PLAN |
| Revoke key (action per row, not yet shown) | Revoke | `DELETE /api-keys/{id}` | PLAN |

### Sections not yet built (placeholders in nav)
Each will be built in the production UI. Listed for the wiring inventory record:

| Section | Likely endpoints |
|---|---|
| General | `PATCH /workspace`, `DELETE /workspace` (danger zone) |
| Members & access | `GET /members`, `POST /invitations`, `PATCH /members/{id}/role`, `DELETE /members/{id}` |
| Auto-discovery | `PATCH /workspace/settings/auto-discovery` (4 thresholds + min-docs) |
| Ingestion | `PATCH /workspace/settings/ingestion` (OCR threshold, doc-type-classifier flag, fallback chain) |
| Cost & limits | `PATCH /workspace/settings/budget` (monthly cap, per-query ceiling, alert email) |
| Notifications | `PATCH /workspace/settings/notifications` (per-event subscription) |
| Webhooks | `GET/POST/DELETE /webhooks` |
| Storage & retention | `PATCH /workspace/settings/storage`, `PATCH /workspace/settings/retention` |
| Profile | `GET /me`, `PATCH /me` |
| Danger zone | `DELETE /workspace` |

---

## Summary

- **Total interactive elements inventoried:** ~210 across 10 screens
- **`PLAN` (needs backend endpoint):** ~135 elements → these become the input set for G2 (API contracts)
- **`LOCAL` (client-side only):** ~75 elements
- **`SSE` (server-sent-event streams):** 4 streams (ingestion stages, ingestion counts, learning events, chat message streaming)
- **`DECIDE` / `REMOVE`:** 0 — every element has a documented purpose

## Endpoint groups (preview of what G2 will produce)

| Group | Endpoints planned |
|---|---|
| **Chat** | `POST /chats`, `GET /chats/{id}`, `POST /chats/{id}/messages`, SSE `/chats/{id}/messages/{mid}/stream`, `POST /chats/{id}/messages/{mid}/feedback`, `POST /chats/{id}/share`, `POST /chats/{id}/attachments` |
| **Docs / Upload** | `POST /docs`, `GET /docs`, `GET /docs/{id}`, `GET /docs/{id}/detail`, `GET /docs/{id}/extraction`, `GET /docs/{id}/pdf`, `GET /docs/{id}/pages/{n}`, `GET /docs/{id}/pages/{n}/ocr-diagnostics`, `POST /docs/{id}/re-extract`, `POST /docs/{id}/re-run`, `PUT /docs/{id}`, `DELETE /docs/{id}`, `PATCH /docs/{id}/fields/{name}`, `POST /docs/{id}/fields/approve-all`, `POST /docs/{id}/share`, `GET /docs/{id}/export`, `GET /docs/{id}/neighbors`, SSE `/docs/{id}/stages` |
| **Search / Explore** | `GET /search`, `GET /search/facets`, `GET /entities/{id}/detail`, `GET /entities/{id}/related`, `GET /entities/{id}/subgraph`, `GET /entities/{id}/merge-candidates`, `PATCH /entities/{id}`, `GET /atomic-units/{id}` |
| **Schema** | `GET /schema/{doc_type}/typed`, `GET /schema/{doc_type}/inferred`, `POST /schema/{doc_type}/fields`, `PATCH /schema/{doc_type}/fields/{name}`, `POST /schema/{doc_type}/fields/{name}/promote`, `POST /schema/{doc_type}/fields/{name}/revert`, `DELETE /schema/{doc_type}/inferred/{name}`, `POST /schema/{doc_type}/fields/merge`, `POST /schema/{doc_type}/fields/{name}/preview-impact`, `GET /schema/{doc_type}/lineage`, `GET /schema/collisions`, `POST /schema/collisions/{id}/resolve`, `GET /schema/versions`, `POST /schema/versions/{v}/restore`, `GET /schema/{doc_type}/prompt`, `POST /schema/{doc_type}/prompt`, `GET /schema/{doc_type}/export`, `GET /vocabulary`, `POST /vocabulary`, `PATCH /vocabulary/{id}` |
| **Extraction** | `GET /extraction/docs`, `POST /extraction/test`, `GET /docs/{id}/chain` |
| **Query / Playground** | `POST /query/sandbox`, `POST /query/replay`, `POST /aggregations/{id}/recompute`, `GET /audit/aggregations/{id}`, `POST /audit/{query_id}/share` |
| **Eval** | `GET /eval/summary`, `POST /eval/runs`, `GET /eval/questions/{id}`, `POST /eval/regression-set` |
| **Playground** | `POST /playground/presets`, `POST /playground/share` |
| **Stats / Dashboard** | `GET /stats/counts`, `GET /stats/timeseries`, `GET /stats/attention`, `GET /stats/by-doc-type`, `GET /stats/ingestion`, `GET /stats/queries`, `GET /stats/cost`, `GET /stats/ingestion-counts` |
| **Events / SSE** | SSE `/events/learning`, SSE `/events/ingestion`, SSE `/events/ingestion-counts` |
| **Audit** | `GET /audit/queries`, `GET /audit/summary`, `GET /audit/export` |
| **Workspace / Settings** | `GET /workspace`, `PATCH /workspace`, `DELETE /workspace`, `PATCH /workspace/settings/llm`, `PATCH /workspace/settings/embedding`, `PATCH /workspace/settings/reranker`, `PATCH /workspace/settings/parsers`, `PATCH /workspace/retrieval-defaults`, `PATCH /workspace/ui-settings`, `PATCH /workspace/settings/auto-discovery`, `PATCH /workspace/settings/ingestion`, `PATCH /workspace/settings/budget`, `PATCH /workspace/settings/notifications`, `PATCH /workspace/settings/storage`, `PATCH /workspace/settings/retention` |
| **Members / Auth** | `GET /me`, `PATCH /me`, `GET /members`, `POST /invitations`, `PATCH /members/{id}/role`, `DELETE /members/{id}`, `POST /sessions` (login) |
| **API keys / Webhooks** | `GET /api-keys`, `POST /api-keys`, `DELETE /api-keys/{id}`, `GET /webhooks`, `POST /webhooks`, `DELETE /webhooks/{id}` |
| **Corrections** | `POST /corrections`, `GET /corrections`, `PATCH /corrections/{id}` |
| **OpenAPI** | `GET /openapi.json`, route `/swagger` |

**Approximately 100 unique endpoints** across 16 groups will need contracts at G2.

---

## Discipline

This inventory is the **input set** to G2. Every `PLAN` row must become an API contract with:
- HTTP method + path
- Request schema (path/query/body params, types, required/optional)
- Response schema (success body, error body)
- Status codes
- Auth requirements
- Idempotency expectations (for `POST`/`PATCH`/`DELETE`)

When a new UI element gets added in the future, it goes through this same audit before it lands.
