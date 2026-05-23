-- Phase 0: extensions + non-superuser application role.
-- Runs first. All later migrations depend on this.

CREATE EXTENSION IF NOT EXISTS vector;     -- pgvector ≥ 0.8 (HNSW + halfvec)
CREATE EXTENSION IF NOT EXISTS pg_search;  -- ParadeDB BM25
CREATE EXTENSION IF NOT EXISTS ltree;      -- hierarchical labels (architecture §7 / Design 7)

-- kb_app: the role the API + workers connect as. RLS applies to it; superuser
-- bypasses RLS so the migration runner can still DDL freely.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kb_app') THEN
        CREATE ROLE kb_app WITH LOGIN;
    END IF;
END
$$;

DO $$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO kb_app', current_database());
END
$$;

GRANT USAGE ON SCHEMA public TO kb_app;

-- Future-proofing: tables/sequences created after this point are usable by kb_app.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO kb_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO kb_app;
