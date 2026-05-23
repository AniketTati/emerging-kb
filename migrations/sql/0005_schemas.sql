-- Phase 1a: schemas table — workspace-scoped CRUD foundation.
-- Per build_tracker §5.2. RLS day-1; partial unique index makes the
-- soft-deleted lifecycle_state pattern work (deleted names can be reused).

CREATE TABLE IF NOT EXISTS schemas (
    id              uuid         NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    workspace_id    uuid         NOT NULL,
    name            text         NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    description     text         NOT NULL DEFAULT '',
    lifecycle_state text         NOT NULL DEFAULT 'active'
                                 CHECK (lifecycle_state IN ('active', 'deleted')),
    created_at      timestamptz  NOT NULL DEFAULT now(),
    updated_at      timestamptz  NOT NULL DEFAULT now()
);

-- Unique per workspace among active rows only; deleted rows free the name.
CREATE UNIQUE INDEX IF NOT EXISTS schemas_workspace_name_active_idx
    ON schemas (workspace_id, name)
    WHERE lifecycle_state = 'active';

CREATE INDEX IF NOT EXISTS schemas_workspace_lifecycle_idx
    ON schemas (workspace_id, lifecycle_state);

ALTER TABLE schemas ENABLE ROW LEVEL SECURITY;
ALTER TABLE schemas FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS schemas_workspace_isolation ON schemas;
CREATE POLICY schemas_workspace_isolation
    ON schemas
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

GRANT SELECT, INSERT, UPDATE, DELETE ON schemas TO kb_app;
