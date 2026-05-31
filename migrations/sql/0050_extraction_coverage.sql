-- 0050 — FIX 3: per-doc extraction coverage + degraded flag (no silent success).
--
-- The frontmatter guard backfills header fields even when body extraction
-- returned nothing, so a failed extraction looked identical to a good one and
-- the doc still reached `ready`. Record per-doc coverage at KV+Tables write
-- time so a text-rich doc that yields 0 body fields is flagged `degraded`
-- (surfaced in the needs-review view) instead of silently succeeding.
--
-- `extraction_coverage` jsonb shape:
--   {"body_fields": int, "frontmatter_fields": int, "table_rows": int,
--    "text_rich": bool, "model_id": text, "degraded": bool}
--
-- Table-level GRANT on files (0008) already covers these new columns for
-- kb_app, so no column-level grant is needed.

ALTER TABLE files ADD COLUMN IF NOT EXISTS extraction_coverage jsonb NULL;
ALTER TABLE files ADD COLUMN IF NOT EXISTS extraction_degraded boolean NOT NULL DEFAULT false;

-- Partial index: the needs-review surface only ever filters for degraded docs.
CREATE INDEX IF NOT EXISTS files_extraction_degraded_idx
    ON files (workspace_id)
    WHERE extraction_degraded;
