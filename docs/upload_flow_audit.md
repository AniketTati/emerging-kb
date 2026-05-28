# Upload flow audit — docs · backend · UI

Date: 2026-05-25 · branch: `waveB/demo-corpus-and-pages`
Last refresh: 2026-05-27 — backend + docs gaps closed; UI gaps still open
(see Status column).

This audit walks the upload flow from the docs through the backend
through the UI, lists every gap, and links to the fixes that ship in
the same PR.

> **Status check (2026-05-27).** The four backend `FileResponse` fields
> and the lifecycle / event taxonomy gap are now fixed in code +
> `docs/api_contracts.md` (§5.1 #3, §5.2, §5.3). The mentions-extractor
> issue (§7) is tracked as **E1** in
> [`extraction_and_citation_plan.md`](extraction_and_citation_plan.md) and
> escalated to CRITICAL by the broader-corpus audit (PR3). The doc-chain
> issue (§6) was fixed by moving detection to the post-KV+Tables stage
> + adding an explicit-`chain_id` path that honors `proposed_fields`
> (+ frontmatter via Bug K). UI gaps below are still open.

## TL;DR

| Layer | Score (orig.) | Status now | What was wrong |
|---|---|---|---|
| `POST /files` | ✅ works | ✅ **fixed** | docs claimed `doc_type: null` always — `FileResponse` now exposes `inferred_doc_type`, `source_authority`, `source_authority_reason`, `doc_status` (`src/kb/domain/files.py:32`). |
| `GET /files` | ⚠️ thin | ✅ **fixed** (response shape); ⏳ filter params still nice-to-have | same widening as POST. `?doc_type=` / `?status=` filters not yet added. |
| `GET /files/:id` | ⚠️ thin | ✅ **fixed** | response widened; full event taxonomy now documented in `api_contracts.md` §5.3 (16 event types incl. additive post-ready ones). |
| `GET /upload/:id/status` (SSE) | ✅ works | ✅ works | verified live. |
| Lifecycle state machine | ✅ works | ✅ **fixed** (docs) | docs now list all 19 states with per-phase introduction (`api_contracts.md` §5.1 #3). |
| **UI: `/upload` page** | ❌ thin | ⏳ still thin | missing 6 of 10 prototype features; the columns that matter (Type · Detected · Actions · row-expand) aren't there. UI work not started. |

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

## 6. Cross-cutting — doc-chain detection (RESOLVED)

**Original finding (2026-05-25).** The WA-3 chain detector did NOT link
`vertex-msa.pdf` and `vertex-amendment.txt`. The amendment's text body
explicitly references the MSA, but the detector ran at parse time —
before any L3 fields existed — and matched filename / title patterns
within an extension family, so the cross-format link was lost.

**Fix shipped (2026-05-27).** Two changes:

1. **Moved chain detection to post-KV+Tables.** The
   `detect_doc_chain_file` task is now deferred from the end of
   `extract_kv_tables_file_impl` (see [tasks.py:1994](../src/kb/workers/tasks.py:1994))
   instead of from `parse_file_impl`. By the time the detector runs,
   the file's `proposed_fields` (chain_id, parent_doc, chain_role,
   chain_version, doc_id) are populated.
2. **Added an explicit-chain path with 100% precision.** When
   `proposed_fields` declares a `chain_id`, the detector attaches the
   file to the matching workspace chain and resolves `parent_doc`
   against sibling docs' `doc_id`. This is deterministic — no
   filename matching, no extension family limit. The heuristic
   detector still fires as a fallback for docs without explicit chain
   fields.
3. **Frontmatter guard rail (Bug K).** YAML frontmatter at the top of
   markdown / text docs is parsed deterministically and lands as
   `proposed_fields` (overwriting any LLM-extracted value for the same
   key). Declaring `chain_id: vertex-msa` in an amendment's
   frontmatter is enough to link it to the parent contract.

See [`docs/architecture.md`](architecture.md) §5 step 12.5 +
[`docs/walkthrough.md`](walkthrough.md) T+32s for the rewritten
description, and [`docs/extraction_and_citation_plan.md`](extraction_and_citation_plan.md)
§1.1 E4 for the original tracking entry.

## 7. Cross-cutting — mentions extractor produces 0 mentions on some doc types (STILL OPEN — promoted to CRITICAL)

**Original finding (2026-05-25, narrow corpus).** The Gemini mention
extractor returned 0 results on `.pdf` / `.txt` / `.eml` chunks while
working on `.md` and `.xlsx`. Hypotheses: prompt sensitivity to
Docling-extracted PDF layout artifacts, TextParser paragraph join,
.eml nested-quote thread structure.

**Refined finding (2026-05-25 evening, broader 26-doc corpus —
[`extraction_and_citation_plan.md`](extraction_and_citation_plan.md) §4b.3).**
The pattern is **content-shape dependent, not format dependent**:

- PDFs that work (dense legal/insurance prose): vertex-msa,
  saas-subscription, mutual-nda, insurance-eob.
- PDFs that fail (structured / form layouts): resume, invoice,
  employment-offer-letter, lab-blood-panel, tiny.pdf.
- Only 1 of 9 markdown files produces mentions (eval-notes); 8 fail.
- All 3 xlsx + 3 .txt + 2 .eml: 0 mentions on re-upload with
  deterministic SHA.

Affects 22 of 26 docs (85%). Re-tracked as **E1 (CRITICAL)** in the
extraction plan with three concrete investigation steps. Not blocking
the upload UI, but degrades Explore + Doc Detail downstream.

Note: the entities surfaced on the Dashboard ("NorthWind Capital · 4
mentions", etc.) come from the `entities` table populated via a
*different* path (the L4 schema-entity extractor). The L2
`extracted_mentions` table is the surface-form detector; it's the one
underperforming.

## 7. Plan for this PR

In one PR:

**Backend** (~50 lines) — ✅ done as of 2026-05-27
1. ✅ Widen `FileResponse` to include `inferred_doc_type`,
   `source_authority`, `source_authority_reason`, `doc_status`
   (`src/kb/domain/files.py:32`).
2. ✅ New endpoint `GET /files/:id/details` returning per-doc rollups
   (chunk count, mention count, entity count, triple count, chain
   membership) — what the row-expand wants. (Atomic-unit count is now
   derived from `extracted_entities WHERE unit_type IS NOT NULL` since
   the `atomic_units` table was dropped in migration 0039.)

**UI** (~400 lines, mostly the table + expand)
3. New columns in `FilesTable`: `Type` (inferred_doc_type), `Status`
   (doc_status badge), `Detected` (counts from /details on expand).
4. Row expand with 5-stage timeline computed from lifecycle events.
5. Action buttons: `Open Doc Detail` (deferred until that page lands
   — soft 404 link is fine for now), `Re-extract`.
6. Authority + status badges next to filename when not default.
7. **Needs-attention** filter chip — files with `failed` lifecycle OR
   `low source_authority` OR `superseded` doc_status.

**Docs** (~80 lines) — ✅ done as of 2026-05-27
8. ✅ `docs/api_contracts.md` updated:
   - §5.1 #3 — full 19-state lifecycle enum table with per-phase
     introduction.
   - §5.2 — `FileResponse` widened with `inferred_doc_type`,
     `source_authority`, `source_authority_reason`, `doc_status`.
   - §5.3 — full event taxonomy (16 events) with payload shapes,
     including the additive post-ready events
     (`triples_extracted`, `relationships_built`, `graph_built`,
     `doc_chain_detected`).
9. ⏳ `docs/lifecycle_events_reference.md` — not split out as a
   separate doc; the §5.3 table in `api_contracts.md` now serves as
   the authoritative reference. Revisit if it grows beyond one
   section.

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
