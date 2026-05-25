# RFC: Source-Offset Citation Resolver (KB-RFC-008)

**Authors**: Vikram Iyer (vikram.iyer@vertex.com)
**Status**: Draft → seeking review
**Filed**: 2026-03-29
**Reviewers**: Priya Menon, Maya Iyer
**Target merge**: 2026-04-15

## Problem

When users click an extracted fact (mention, field, triple) in the
doc-detail UI, we want to highlight the exact source location in the
original file. Today, the right-pane click publishes a fuzzy text-search
string; the source pane does first-substring-match. This produces wrong
highlights when the snippet collides (e.g. "1.5" finds the first "1.5"
on the page, not the cited "1.5%" interest rate).

## Goals

1. Pixel-precise highlight for every L2/L3/L4 extraction where the LLM
   returned verbatim text.
2. Honest "no source location" UI when the LLM paraphrased.
3. No new LLM prompt design ("offset-aware LLM") — keep working with
   what the existing extractors return.

## Non-goals

- PDF bbox precision (Wave C; needs Docling layout persistence).
- Backfill for pre-RFC data (separate one-shot script).
- Cross-doc citation aggregation (Wave B item, not blocked by this).

## Proposal

After each LLM extraction stage, run a **deterministic resolver** that
locates the LLM's verbatim snippet inside the chunk that was sent to
the LLM. Persist `(source_chunk_id, source_char_start, source_char_end)`
on the extraction row.

### Schema

```sql
ALTER TABLE extracted_mentions
    ADD COLUMN source_chunk_id UUID REFERENCES chunks(id),
    ADD COLUMN source_char_start INT,
    ADD COLUMN source_char_end INT;
-- and analogous columns on proposed_fields, atomic_units, extracted_triples
```

### Resolver

Two-pass match against the chunk text:

1. **Exact substring** — `chunk_text.find(snippet)`. Most LLM output
   is verbatim; this catches the easy case.
2. **Whitespace-normalized** — collapse `\s+` to single space on both
   sides; remember offset map to translate the normalized match back
   to the original.

Returns `None` when neither pass matches. UI surfaces "no source
location" rather than mis-highlighting.

### UI behavior

| Found | Behavior |
|---|---|
| Yes | Fetch `/chunks/:id`; slice `[char_start:char_end]`; highlight the verbatim string in the format-specific viewer (text `<mark>`, xlsx `<td>` outline, PDF.js text-layer span). |
| No  | Show "no source location stored — best-effort search" + fall back to fuzzy text-search. |

## Considered alternatives

### Alt 1: Offset-aware LLM

Prompt the LLM to return `{text, start, end}` for each extracted item.

**Pros**: Single round-trip, no resolver code.

**Cons**: LLMs are poor at character offsets, off-by-one errors are
common, prompt complexity grows, validation overhead. Output schema
gets noisier. Rejected.

### Alt 2: Store the raw chunk text on each extraction row

Denormalize chunk text into each mention/field row.

**Pros**: UI doesn't need a `/chunks/:id` round-trip.

**Cons**: Massive data duplication (chunks are ~2KB, mentions are
~50B), schema bloat. Rejected.

### Alt 3: Compute offsets at retrieval time only

Run the resolver at API-read time instead of extraction-write time.

**Pros**: No schema migration.

**Cons**: Repeated work per request, slower API, doesn't scale with
read volume. Rejected.

## Migration plan

1. Land schema migration as additive nullable columns.
2. Ship worker resolver wired into all 4 extractors.
3. Run one-shot backfill script (`scripts/backfill_source_positions.py`)
   on existing rows.
4. Update UI to prefer exact citations, fall back to text-search.

## Rollout

- **Week 1**: schema migration + resolver lands. No UI change.
- **Week 2**: UI uses the new positions for newly-extracted data.
- **Week 3**: Backfill old data + flip UI to use positions for everything.

## Open questions

- How do we handle the 3% of fields where the LLM paraphrases? Today
  the UI shows "no source location"; should we attempt fuzzy fallback
  with a confidence indicator?
- What's the migration story for the `extracted_entities.citations`
  jsonb (which already has `{field: chunk_id}` per Design 5)? Do we
  also extend that to include char offsets?
- Once Wave C ships per-element bbox in `raw_pages.layout_elements`,
  do we add a `source_bbox` column or keep bbox lookup as a derived
  read-time operation?
