-- Phase 0: migration tracker. No workspace_id — this is global infrastructure.
-- The runner bootstraps this table before checking what's been applied.

CREATE TABLE IF NOT EXISTS schema_migrations (
    id          text        PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now()
);

-- Grant SELECT to kb_app so /ready's migrations check can read this table.
-- 0001 sets ALTER DEFAULT PRIVILEGES but only for tables created AFTER 0001
-- runs; schema_migrations is bootstrap-created BEFORE 0001 (chicken-and-egg
-- for the runner). Guard the GRANT with a DO block: on the bootstrap run
-- (kb_app doesn't yet exist) we no-op; on the real run inside the loop
-- (after 0001), we grant.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kb_app') THEN
        GRANT SELECT ON schema_migrations TO kb_app;
    END IF;
END
$$;
