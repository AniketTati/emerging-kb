-- Phase 0: cross-cutting idempotent request handling.
-- Workspace-scoped: (workspace_id, key) is the natural primary key.
-- Phase 9 may add a TTL cleanup job.

CREATE TABLE idempotency_keys (
    workspace_id  uuid         NOT NULL,
    key           text         NOT NULL,                -- value from Idempotency-Key header
    response      jsonb        NOT NULL,
    status_code   int          NOT NULL,
    created_at    timestamptz  NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, key)
);

CREATE INDEX idempotency_keys_created_idx ON idempotency_keys (created_at);

ALTER TABLE idempotency_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE idempotency_keys FORCE ROW LEVEL SECURITY;

CREATE POLICY idempotency_keys_workspace_isolation
    ON idempotency_keys
    USING (workspace_id = current_setting('app.workspace_id', true)::uuid)
    WITH CHECK (workspace_id = current_setting('app.workspace_id', true)::uuid);

GRANT SELECT, INSERT, UPDATE, DELETE ON idempotency_keys TO kb_app;
