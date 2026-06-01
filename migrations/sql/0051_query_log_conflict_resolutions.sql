-- 0051 — persist conflict_resolutions on query_log so the chat conflict-
-- resolution banner survives a session reopen.
--
-- The R1 conflict-resolution banner (AnswerCard) renders from
-- ChatResult.conflict_resolutions — the per-(entity, predicate) disagreements
-- the structured layer resolved (picked value vs. superseded losers + which
-- rule decided). It was computed live but never persisted, so reopening a
-- chat dropped the banner (the per-citation `superseded` flags survived via
-- the persisted citations, but the summary banner did not). Store it here so
-- the /sessions/{id}/turns replay can read it back and re-render the banner.
--
-- Shape: jsonb array of {entity_id, predicate, resolution, picked_value,
--   picked_doc_id, loser_doc_ids, ...}. NULL when no structured conflict fired.
--
-- Table-level GRANT on query_log (Phase 0 grants) already covers new columns
-- for kb_app, so no column-level grant is needed here.

ALTER TABLE query_log ADD COLUMN IF NOT EXISTS conflict_resolutions jsonb NULL;
