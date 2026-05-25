-- B2 / WA-6 — Design 2 Conflicts + Source Authority.
--
-- Two changes:
--   1. Extend `files` with source_authority + source_authority_reason +
--      doc_status (per Design 2 §"Data model").
--   2. New `fact_conflicts` table for query-time conflict tracking.
--
-- Authority defaults are read from config/doc_types/<type>.yaml at ingest
-- (WA-1 layered config). The DB-level default of 0.5 covers the
-- "authority unknown" case per Design 2 §"Failure modes".

-- ============================================================================
-- 1) files extensions
-- ============================================================================

ALTER TABLE files
    ADD COLUMN IF NOT EXISTS source_authority NUMERIC(3,2)
        NOT NULL DEFAULT 0.5
        CHECK (source_authority >= 0 AND source_authority <= 1);

ALTER TABLE files
    ADD COLUMN IF NOT EXISTS source_authority_reason TEXT;

ALTER TABLE files
    ADD COLUMN IF NOT EXISTS doc_status TEXT
        NOT NULL DEFAULT 'live';

-- doc_status CHECK enum per Design 2 §"Data model"
ALTER TABLE files DROP CONSTRAINT IF EXISTS files_doc_status_check;
ALTER TABLE files ADD CONSTRAINT files_doc_status_check
    CHECK (doc_status IN ('live', 'superseded', 'draft', 'archived', 'retracted'));

-- Index for the Dashboard Needs-attention surface (Wave A WA-14 reads
-- "show me draft / superseded files for review").
CREATE INDEX IF NOT EXISTS files_doc_status_idx
    ON files (workspace_id, doc_status)
    WHERE doc_status <> 'live';


-- ============================================================================
-- 2) fact_conflicts — query-time conflict tracking
-- ============================================================================
-- Per Design 2 §"Data model" + §"Conflict detection". Written by the
-- generator (kb/query/generate.py) when its conflict detector finds
-- disagreement on (entity, predicate). Read by the Dashboard Needs-
-- attention surface + per-doc Doc Detail panel.

CREATE TABLE IF NOT EXISTS fact_conflicts (
    id              uuid          NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    workspace_id    uuid          NOT NULL,
    entity_id       uuid          NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    predicate       text          NOT NULL,
    observed_at     timestamptz   NOT NULL DEFAULT NOW(),
    -- evidence jsonb shape (per Design 2):
    --   [{doc_id, value, authority, recency, span, doc_status}, ...]
    evidence        jsonb         NOT NULL,
    -- Resolution applied (or 'unresolved' if no rule fired).
    resolution      text          NOT NULL DEFAULT 'unresolved'
                                  CHECK (resolution IN (
                                      'chain', 'status', 'authority',
                                      'recency', 'unresolved', 'user'
                                  )),
    -- The picked value (NULL when unresolved).
    resolved_value  text,
    resolved_doc_id uuid          REFERENCES files(id) ON DELETE SET NULL,
    notes           text,
    -- Track admin-resolution + audit.
    resolved_by     text,
    resolved_at     timestamptz,

    CHECK (length(predicate) > 0)
);

-- Per-workspace list with priority on unresolved (UI default sort).
CREATE INDEX IF NOT EXISTS fact_conflicts_workspace_resolution_idx
    ON fact_conflicts (workspace_id, resolution, observed_at DESC);

-- Per-entity lookup (Doc Detail panel surfaces conflicts on the entity).
CREATE INDEX IF NOT EXISTS fact_conflicts_entity_idx
    ON fact_conflicts (workspace_id, entity_id);

ALTER TABLE fact_conflicts ENABLE ROW LEVEL SECURITY;
ALTER TABLE fact_conflicts FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS fact_conflicts_isolation ON fact_conflicts;
CREATE POLICY fact_conflicts_isolation ON fact_conflicts
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

REVOKE ALL ON fact_conflicts FROM kb_app;
-- Full CRUD: insert from generator, update on admin resolve, delete for
-- false positives (admin "this isn't a conflict" action).
GRANT SELECT, INSERT, UPDATE, DELETE ON fact_conflicts TO kb_app;
