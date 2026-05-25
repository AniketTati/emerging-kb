-- Source-position provenance for citations.
--
-- Adds (source_chunk_id, source_char_start, source_char_end) to every
-- per-doc extraction table so the doc-detail UI can highlight the
-- EXACT character range the LLM cited from — no fuzzy text-search at
-- click time.
--
-- Approach: the LLM already returns the verbatim extracted text. A
-- worker-side resolver (src/kb/extraction/source_resolver.py) finds
-- that text in the chunk that was sent to the LLM and records the
-- offset. Deterministic; no LLM/prompt changes.
--
-- All columns are nullable — backfill is opportunistic, no row migrate.

ALTER TABLE extracted_mentions
    ADD COLUMN IF NOT EXISTS source_chunk_id UUID REFERENCES chunks(id),
    ADD COLUMN IF NOT EXISTS source_char_start INT,
    ADD COLUMN IF NOT EXISTS source_char_end INT;

ALTER TABLE proposed_fields
    ADD COLUMN IF NOT EXISTS source_chunk_id UUID REFERENCES chunks(id),
    ADD COLUMN IF NOT EXISTS source_char_start INT,
    ADD COLUMN IF NOT EXISTS source_char_end INT;

ALTER TABLE extracted_triples
    ADD COLUMN IF NOT EXISTS subject_char_start INT,
    ADD COLUMN IF NOT EXISTS subject_char_end INT,
    ADD COLUMN IF NOT EXISTS object_char_start INT,
    ADD COLUMN IF NOT EXISTS object_char_end INT;

-- atomic_units already carries provenance for xlsx (parameters.row_index +
-- sheet_name) but non-xlsx units (clauses, etc.) need source offsets too.
ALTER TABLE atomic_units
    ADD COLUMN IF NOT EXISTS source_chunk_id UUID REFERENCES chunks(id),
    ADD COLUMN IF NOT EXISTS source_char_start INT,
    ADD COLUMN IF NOT EXISTS source_char_end INT;

CREATE INDEX IF NOT EXISTS extracted_mentions_source_chunk_idx
    ON extracted_mentions(source_chunk_id);
CREATE INDEX IF NOT EXISTS proposed_fields_source_chunk_idx
    ON proposed_fields(source_chunk_id);
CREATE INDEX IF NOT EXISTS atomic_units_source_chunk_idx
    ON atomic_units(source_chunk_id);
