-- Phase 2a: parse-layer scaffold (files + file_lifecycle + raw_pages + parse_artifacts).
-- Per build_tracker §5.5 (15 locked decisions) + api_contracts §5.
--
-- Four new workspace-scoped tables. Each carries own `workspace_id` + own
-- RLS policy (decision #12 — belt-and-braces invariant grows from 7 to 11
-- workspace-scoped tables).
--
-- Immutability:
-- - file_lifecycle: append-only audit (REVOKE UPDATE, DELETE from kb_app
--   per decision #4 — same pattern as schema_versions).
-- - raw_pages: immutable per-page output (REVOKE UPDATE, DELETE per #5).
--
-- All statements idempotent (IF NOT EXISTS, DROP POLICY IF EXISTS) so the
-- migration runner's bootstrap test can re-apply 0001..0008 against an
-- existing DB.

-- ----------------------------------------------------------------------------
-- files — file metadata; MinIO holds bytes under raw_files/<sha256>.
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS files (
    id              uuid         NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    workspace_id    uuid         NOT NULL,
    name            text         NOT NULL CHECK (length(name) BETWEEN 1 AND 500),
    content_sha     text         NOT NULL CHECK (length(content_sha) = 64),
    object_key      text         NOT NULL,
    mime_type       text         NOT NULL,
    size_bytes      bigint       NOT NULL CHECK (size_bytes >= 0),
    doc_type        text         NULL,
    lifecycle_state text         NOT NULL DEFAULT 'queued'
                                 CHECK (lifecycle_state IN ('queued','parsing','parsed','failed','deleted')),
    created_at      timestamptz  NOT NULL DEFAULT now(),
    updated_at      timestamptz  NOT NULL DEFAULT now()
);

-- Content-hash dedup per workspace among non-deleted rows (decision #2).
CREATE UNIQUE INDEX IF NOT EXISTS files_workspace_sha_active_idx
    ON files (workspace_id, content_sha)
    WHERE lifecycle_state <> 'deleted';

CREATE INDEX IF NOT EXISTS files_workspace_lifecycle_idx
    ON files (workspace_id, lifecycle_state);
CREATE INDEX IF NOT EXISTS files_workspace_created_idx
    ON files (workspace_id, created_at DESC);

ALTER TABLE files ENABLE ROW LEVEL SECURITY;
ALTER TABLE files FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS files_workspace_isolation ON files;
CREATE POLICY files_workspace_isolation
    ON files
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

GRANT SELECT, INSERT, UPDATE, DELETE ON files TO kb_app;

-- ----------------------------------------------------------------------------
-- file_lifecycle — append-only audit of state transitions (decision #4).
-- One row per (file, transition). Immutable — REVOKE UPDATE/DELETE.
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS file_lifecycle (
    id              uuid         NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    file_id         uuid         NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    workspace_id    uuid         NOT NULL,
    from_state      text         NULL,           -- NULL for the initial upload event
    to_state        text         NOT NULL,
    event           text         NOT NULL,       -- 'upload' | 'task_started' | 'parse_done' | 'parse_failed' | 'soft_delete' | ...
    payload         jsonb        NOT NULL DEFAULT '{}'::jsonb,
    created_at      timestamptz  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS file_lifecycle_file_idx ON file_lifecycle (file_id, created_at);
CREATE INDEX IF NOT EXISTS file_lifecycle_workspace_idx ON file_lifecycle (workspace_id);

ALTER TABLE file_lifecycle ENABLE ROW LEVEL SECURITY;
ALTER TABLE file_lifecycle FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS file_lifecycle_workspace_isolation ON file_lifecycle;
CREATE POLICY file_lifecycle_workspace_isolation
    ON file_lifecycle
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

-- Same immutability pattern as schema_versions (Phase 1b decision #10):
-- 0001 ALTER DEFAULT PRIVILEGES grants the full CRUD set, so we REVOKE the
-- write privileges that don't apply.
GRANT SELECT, INSERT ON file_lifecycle TO kb_app;
REVOKE UPDATE, DELETE ON file_lifecycle FROM kb_app;

-- ----------------------------------------------------------------------------
-- raw_pages — IMMUTABLE per-page output (decision #5). Content-hash keyed.
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS raw_pages (
    id              uuid         NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    file_id         uuid         NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    workspace_id    uuid         NOT NULL,
    page_number     int          NOT NULL CHECK (page_number >= 1),
    text            text         NOT NULL,
    layout_json     jsonb        NOT NULL DEFAULT '{}'::jsonb,
    content_sha     text         NOT NULL CHECK (length(content_sha) = 64),
    created_at      timestamptz  NOT NULL DEFAULT now(),
    UNIQUE (file_id, page_number)
);

CREATE INDEX IF NOT EXISTS raw_pages_workspace_idx ON raw_pages (workspace_id);
CREATE INDEX IF NOT EXISTS raw_pages_file_idx ON raw_pages (file_id, page_number);

ALTER TABLE raw_pages ENABLE ROW LEVEL SECURITY;
ALTER TABLE raw_pages FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS raw_pages_workspace_isolation ON raw_pages;
CREATE POLICY raw_pages_workspace_isolation
    ON raw_pages
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

GRANT SELECT, INSERT ON raw_pages TO kb_app;
REVOKE UPDATE, DELETE ON raw_pages FROM kb_app;

-- ----------------------------------------------------------------------------
-- parse_artifacts — secondary parser output (layout JSON, tables JSON, OCR
-- confidences). MinIO holds the artifact body; PG holds the pointer.
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS parse_artifacts (
    id              uuid         NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    file_id         uuid         NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    workspace_id    uuid         NOT NULL,
    kind            text         NOT NULL CHECK (kind IN ('layout','tables','ocr_confidence')),
    object_key      text         NOT NULL,
    created_at      timestamptz  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS parse_artifacts_file_idx ON parse_artifacts (file_id, kind);
CREATE INDEX IF NOT EXISTS parse_artifacts_workspace_idx ON parse_artifacts (workspace_id);

ALTER TABLE parse_artifacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE parse_artifacts FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS parse_artifacts_workspace_isolation ON parse_artifacts;
CREATE POLICY parse_artifacts_workspace_isolation
    ON parse_artifacts
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

GRANT SELECT, INSERT, UPDATE, DELETE ON parse_artifacts TO kb_app;
