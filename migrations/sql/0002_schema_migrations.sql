-- Phase 0: migration tracker. No workspace_id — this is global infrastructure.
-- The runner bootstraps this table before checking what's been applied.

CREATE TABLE IF NOT EXISTS schema_migrations (
    id          text        PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now()
);
