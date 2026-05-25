# Upload flow audit — docs · backend · UI

Date: 2026-05-25 · branch: `waveB/demo-corpus-and-pages`

This audit walks the upload flow from the docs through the backend
through the UI, lists every gap, and links to the fixes that ship in
the same PR.

## TL;DR

| Layer | Score | What's wrong |
|---|---|---|
| `POST /files` | ✅ works | docs claim `doc_type: null` always — true at Phase 2a, no longer true; response shape doesn't expose `inferred_doc_type`, `source_authority`, or `doc_status` |
| `GET /files` | ⚠️ thin | same gap as POST — UI can't see Gemini's classification or source authority |
| `GET /files/:id` | ⚠️ thin | same fields missing; `lifecycle` array is complete but doc only describes events through Phase 3d (10+ subsequent events fire and aren't documented) |
| `GET /upload/:id/status` (SSE) | ✅ works | verified live |
| Lifecycle state machine | ✅ works | the CHECK constraint has all 19 states; docs only list 8 |
| **UI: `/upload` page** | ❌ thin | missing 6 of 10 prototype features; the columns that matter (Type · Detected · Actions · row-expand) aren't there |

## 1. Backend — `POST /files`

### Docs say
[`docs/api_contracts.md` §5.5](api_contracts.md#55-post-files--upload)
returns a `FileResponse` (§5.2) with `doc_type: null` and
`lifecycle_state: queued`.

### Reality
Returns `FileResponse` with `doc_type: null` ✓ but **omits 4 fields
that exist in the DB row**:

| DB column | Set by | Currently in response? |
|---|---|---|
| `inferred_doc_type` | Phase 5b classifier (Gemini) | ❌ no |
| `source_authority` | B2 / WA-6 (`apply_source_authority_from_config`) | ❌ no |
| `source_authority_reason` | B2 / WA-6 | ❌ no |
| `doc_status` | B2 / WA-6 — one of `live`/`superseded`/`draft`/`archived`/`retracted` | ❌ no |

Source: `src/kb/domain/files.py:33` `FileResponse` only lists the
Phase 2a fields; subsequent phases added DB columns but never widened
the response.

### Fix
Extend `FileResponse` + the SELECT projection (`_FILE_COLS`) to
include all four. Backward-compatible because they're additive
optional fields.

## 2. Backend — `GET /files`

### Docs say
[`docs/api_contracts.md` §5.6](api_contracts.md#56-get-files--list-active-files)
returns paginated `FileListResponse`. No mention of doc-type filter
or status filter.

### Reality
Same response-shape gap as POST /files. No filter parameters
(`doc_type=…`, `status=…`) — the Dashboard's "Needs Attention" hint
("low_authority_file") wants to drill in by `source_authority < 0.5`
but the only way today is to fetch ALL files and filter client-side.

### Fix (priority B)
- Same response widening as #1.
- Optional: `?doc_type=…` and `?status=…` filters. Wave-A nice-to-have,
  not blocking the UI for now (page-level filtering on small corpora
  works fine).

## 3. Backend — `GET /files/:id`

### Docs say
[`docs/api_contracts.md` §5.3](api_contracts.md#53-lifecycle-history-shape)
documents the `lifecycle` array. Events listed:

```
upload                  null → queued
task_started            queued → parsing
parse_done              parsing → parsed
chunking_done           parsed → chunked
contextualization_done  chunked → contextualized
embedding_done          contextualized → embedded
raptor_build_started    embedded → raptor_building
raptor_build_done       raptor_building → ready
<stage>_failed          → failed
```

### Reality
The pipeline fires **11+ additional events** the doc doesn't mention:

```
doc_chain_detected           chunked → chunked (or chunked → doc_chaining → chunked)   WA-3
mentions_extracted           ... → mentions_extracting → fields_extracting             Phase 5a
fields_extracted             fields_extracting → units_extracting                      Phase 5b
                             (+ apply_source_authority side-effect)                    B2 / WA-6
atomic_units_extracted       units_extracting → entities_extracting                    Phase 5c
schema_entities_extracted    entities_extracting → identity_resolving                  Phase 6
identities_resolved          identity_resolving → ready                                Phase 7
triples_extracted            ready → ready                                             B1 / WA-4
relationships_built          ready → ready                                             B1 / WA-5
graph_built                  ready → ready                                             B1 / WA-5
```

Plus the lifecycle CHECK enum now admits 19 states; doc lists 8.

### Fix
Update `docs/api_contracts.md` §5.1 #3 + §5.3 + the lifecycle-state
list to reflect every event the pipeline actually emits. Document
which phase introduced each.

## 4. Backend — `GET /upload/:id/status` (SSE)

### Verified live ✓
- Connects, emits `lifecycle` events with the full payload
- Emits `done` on terminal state
- Survives the SSE-bug fix from PR #24 (effect-deps regression)

No changes needed.

## 5. UI — `/upload` page vs `prototype/upload.html`

### Per `docs/ui_design.md` §6.2

| Feature | Prototype | Built? |
|---|---|---|
| Drag-drop dropzone (files) | ✅ | ✅ |
| Folders + ZIPs | ✅ | ❌ |
| Live counts header `N ready · M processing · K failed` | ✅ | ✅ |
| Filter chips: All / Processing / Ready / Failed | ✅ | ✅ |
| Filter chip: **Needs-attention** | ✅ | ❌ |
| Doc-type filter dropdown | ✅ | ❌ |
| Text search | ✅ | ✅ |
| Bulk **Re-run failed** | ✅ | ❌ |
| Table column: File (with icon, clickable → Doc Detail) | ✅ | ⚠️ icon only, not clickable |
| Table column: **Type** (clickable → Schema Studio) | ✅ | ❌ — shows MIME type, not inferred_doc_type |
| Table column: Stage (5-pip + label) | ✅ | ✅ via `StageBadge` |
| Table column: Elapsed | ✅ | ✅ |
| Table column: **Detected** (entity count · AU count) | ✅ | ❌ |
| Table column: **Actions** | ✅ | ❌ |
| **Row expand** with per-stage timeline | ✅ | ❌ |
| Per-stage sub-counts (parser · chunks · mentions · entities) | ✅ | ❌ |
| Doc-type / Entities / Atomic-units summary in expand | ✅ | ❌ |
| **Failed row** recovery actions (re-run with VLM · replace · view OCR diagnostics) | ✅ | ❌ |
| Cross-link: filename → Doc Detail | ✅ | ❌ |
| Cross-link: doc-type cell → Schema Studio | ✅ | ❌ |
| `source_authority` indicator | (implicit — prototype's "authority" pill on Doc Detail) | ❌ |
| `doc_status` badge (superseded, draft, etc.) | ✅ | ❌ |

### Built but slim
- The stage pips work but the table doesn't have a Detected column
- Filter chips work but Needs-attention isn't an option

### Verdict
The current `/upload` page is functionally minimal — it ingests and
shows stages, nothing else. The prototype defines a much richer
information surface. Whether we build the full prototype now or
iterate is the conscious call to make.

## 6. Cross-cutting — doc-chain detection (separate concern)

When the demo corpus was ingested, the WA-3 chain detector did NOT
link `vertex-msa.pdf` and `vertex-amendment.txt`. The amendment's
text body explicitly references the MSA. The detector at
`src/kb/extraction/doc_chains.py` likely filename-pattern matches
within an extension family (e.g. `*.pdf` only) — needs investigation
but out of scope for the upload PR.

Tracking as a separate item; not blocking.

## 7. Cross-cutting — mentions extractor produces 0 mentions on some doc types

While verifying the new `/files/:id/details` endpoint we noticed:

| File | inferred_doc_type | mentions extracted |
|---|---|---|
| tiny.pdf (11-token blank) | handwritten_note | 0 (expected) |
| vertex-sales-thread.eml   | email_thread | **0** (suspicious) |
| vertex-pricing-tiers.xlsx | price_sheet | 44 |
| vertex-msa.pdf            | master_services_agreement | **0** (suspicious) |
| vertex-amendment.txt      | legal_contract | **0** (suspicious) |
| vertex-eval-notes.md      | vendor_evaluation | 44 |

All files passed through `mentions_extracting` cleanly (same lifecycle
events emitted, same chunk shape) — the Gemini mention extractor
returned 0 results on `.pdf`, `.txt`, `.eml` chunks. Possible causes:

- Prompt sensitivity to Docling-extracted PDF text (may have layout
  artifacts that confuse the LLM).
- TextParser's paragraph-joined content reaching the extractor differs
  enough from .md to throw the prompt.
- The .eml's nested-quote thread structure (Reply-In-To headers + quote
  blocks) may exceed an internal limit in the extractor.

The entities surfaced on the Dashboard ("NorthWind Capital · 4
mentions", etc.) come from the `entities` table (canonicalized) — they
were populated via a *different* path (likely the L4 schema-entity
extractor). The L2 `extracted_mentions` table is the surface-form
detector; it's underperforming on plain-text inputs.

Tracking as a separate item; not blocking the upload UI but worth
investigating in a follow-up since it degrades the Explore + Doc
Detail pages downstream.

## 7. Plan for this PR

In one PR:

**Backend** (~50 lines)
1. Widen `FileResponse` to include `inferred_doc_type`,
   `source_authority`, `source_authority_reason`, `doc_status`.
2. New endpoint `GET /files/:id/details` returning per-doc rollups
   (chunk count, mention count, atomic unit count, entity count,
   chain membership) — what the row-expand wants.

**UI** (~400 lines, mostly the table + expand)
3. New columns in `FilesTable`: `Type` (inferred_doc_type), `Status`
   (doc_status badge), `Detected` (counts from /details on expand).
4. Row expand with 5-stage timeline computed from lifecycle events.
5. Action buttons: `Open Doc Detail` (deferred until that page lands
   — soft 404 link is fine for now), `Re-extract`.
6. Authority + status badges next to filename when not default.
7. **Needs-attention** filter chip — files with `failed` lifecycle OR
   `low source_authority` OR `superseded` doc_status.

**Docs** (~80 lines)
8. Update `docs/api_contracts.md`:
   - §5.2 `FileResponse` shape — add the 4 new fields with which phase
     introduced each.
   - §5.3 lifecycle event list — add the 11 missing events with their
     stages + payload shapes.
   - §5.1 #3 lifecycle state list — list all 19 states with the phase
     gate that admits each.
9. Add a `docs/lifecycle_events_reference.md` — one authoritative
   table of every event the worker ever emits (stage, from/to state,
   payload schema, code location). The UI's row-expand reads this
   shape; the doc lets a reader cross-check.

**Tests**
10. Playwright spec for the row expand + Detected column + Type
    column — uses the demo corpus to assert real values render.

**Out of scope** for this PR (called out so we don't regress later)
- Folder / ZIP upload
- Bulk "Re-run failed"
- Failed-row recovery actions (re-run with VLM fallback, replace,
  diagnostics) — none of the underlying re-run endpoints exist yet
- Doc Detail page link target
- Schema Studio cross-link target
- Doc-chain detector improvement
