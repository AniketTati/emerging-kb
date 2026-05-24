-- Phase 3a: chunks table — layout-aware token-bounded chunks of raw_pages.
-- Per build_tracker §5.7 (12 locked decisions) + api_contracts §5.1 #3 + §5.2.
--
-- Two changes in one file:
--   1) ALTER files.lifecycle_state CHECK to include 'chunked' (decision #8).
--   2) CREATE TABLE chunks (workspace-scoped, RLS day-1, immutable via REVOKE).
--
-- Idempotent so the migration runner's bootstrap test can re-apply.

-- ----------------------------------------------------------------------------
-- 1. Widen files.lifecycle_state CHECK to include 'chunked' (+ forward-compat).
-- ----------------------------------------------------------------------------
-- The CHECK lists EVERY lifecycle state we'll eventually use through Phase 3c
-- ('contextualized' Phase 3b + 'ready' Phase 3c). Listing future values here
-- doesn't enable them prematurely — no code paths write those states yet —
-- but it makes re-running the migration runner against a DB that ALREADY has
-- those values idempotent: tests that re-apply migrations against a polluted
-- DB no longer trip on a too-narrow CHECK.
--
-- This is the forward-compat convention locked at Phase 3a G2: each
-- sub-phase appends exactly one new state on the wire, but the DB-level
-- CHECK can be widened ahead of time without behavioural cost.

ALTER TABLE files DROP CONSTRAINT IF EXISTS files_lifecycle_state_check;
ALTER TABLE files ADD CONSTRAINT files_lifecycle_state_check
    CHECK (lifecycle_state IN (
        'queued','parsing','parsed','chunked','contextualized','embedded',
        'raptor_building','mentions_extracting','fields_extracting',
        'units_extracting','entities_extracting','ready','failed','deleted'
    ));

-- ----------------------------------------------------------------------------
-- 2. chunks — immutable, layout-aware token-bounded chunks
-- ----------------------------------------------------------------------------
-- Decision #7: REVOKE UPDATE, DELETE — chunks are an immutable derived
-- artifact; downstream embeddings (Phase 3c) reference them by id. In-place
-- mutation would silently invalidate the contextual prefix + embeddings.

CREATE TABLE IF NOT EXISTS chunks (
    id                   uuid         NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    file_id              uuid         NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    workspace_id         uuid         NOT NULL,
    chunk_index          int          NOT NULL CHECK (chunk_index >= 0),
    text                 text         NOT NULL,
    source_page_numbers  int[]        NOT NULL DEFAULT '{}'::int[],
    token_count          int          NOT NULL CHECK (token_count >= 0),
    content_sha          text         NOT NULL CHECK (length(content_sha) = 64),
    created_at           timestamptz  NOT NULL DEFAULT now(),
    UNIQUE (file_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS chunks_workspace_idx ON chunks (workspace_id);
CREATE INDEX IF NOT EXISTS chunks_file_idx      ON chunks (file_id, chunk_index);

ALTER TABLE chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE chunks FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS chunks_workspace_isolation ON chunks;
CREATE POLICY chunks_workspace_isolation
    ON chunks
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

-- 0001 ALTER DEFAULT PRIVILEGES granted full CRUD; strip the mutation set.
GRANT SELECT, INSERT ON chunks TO kb_app;
REVOKE UPDATE, DELETE ON chunks FROM kb_app;
