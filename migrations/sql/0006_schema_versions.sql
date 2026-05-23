-- Phase 1b: schema_versions table + schemas.current_version_id pointer.
-- Per build_tracker §5.3 (13 locked decisions) + api_contracts §3.
--
-- Versions are immutable: only SELECT + INSERT are GRANTed to kb_app.
-- Workspace-isolated via its own workspace_id + RLS policy (decision #10,
-- belt-and-braces — not relying on the parent schema's RLS via FK joins).
--
-- All statements are idempotent (IF NOT EXISTS, DROP POLICY IF EXISTS,
-- ADD COLUMN IF NOT EXISTS) so the migration runner's bootstrap test
-- can re-apply 0001..0006 against an existing DB.

CREATE TABLE IF NOT EXISTS schema_versions (
    id                       uuid         NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    schema_id                uuid         NOT NULL REFERENCES schemas(id) ON DELETE CASCADE,
    workspace_id             uuid         NOT NULL,
    version_number           int          NOT NULL CHECK (version_number >= 1),
    body                     jsonb        NOT NULL,
    parent_version_number    int          NULL CHECK (parent_version_number IS NULL OR parent_version_number >= 1),
    kind                     text         NOT NULL DEFAULT 'put'
                                          CHECK (kind IN ('post', 'put', 'rollback')),
    created_at               timestamptz  NOT NULL DEFAULT now(),
    UNIQUE (schema_id, version_number)
);

CREATE INDEX IF NOT EXISTS schema_versions_workspace_idx
    ON schema_versions (workspace_id);

CREATE INDEX IF NOT EXISTS schema_versions_schema_created_idx
    ON schema_versions (schema_id, created_at DESC);

ALTER TABLE schema_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE schema_versions FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS schema_versions_workspace_isolation ON schema_versions;
CREATE POLICY schema_versions_workspace_isolation
    ON schema_versions
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

-- Versions are immutable: SELECT + INSERT only (decision #10, invariant §3.1 #1).
-- 0001 sets ALTER DEFAULT PRIVILEGES that grant the full CRUD set to kb_app
-- on every NEW table in public; that's correct for ordinary CRUD tables, but
-- schema_versions is an audit log, so we explicitly REVOKE UPDATE + DELETE
-- to enforce immutability at the DB layer. An application bug that tried
-- to UPDATE a version (e.g., "fix a typo in v3") would error rather than
-- silently mutate audit history. Soft-delete the parent schema instead.
GRANT SELECT, INSERT ON schema_versions TO kb_app;
REVOKE UPDATE, DELETE ON schema_versions FROM kb_app;

-- Pointer from schemas → its head version. Nullable for migration safety;
-- application code maintains the "schema exists ⇒ ≥1 version exists"
-- invariant (decision #3) by inserting v1 in the same tx as the schema row.
-- ON DELETE SET NULL (decision #11): versions don't hard-delete in 1b, so
-- the clause is defensive; cascade would be wrong if Phase 9 introduces
-- per-version purge — the schema row's existence matters more than the
-- pointer's integrity.
ALTER TABLE schemas
    ADD COLUMN IF NOT EXISTS current_version_id uuid NULL
    REFERENCES schema_versions(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS schemas_current_version_idx
    ON schemas (current_version_id);
