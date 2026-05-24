-- Phase 3c: chunk_embeddings table — Gemini Embedding 001 vectors per
-- contextual chunk.
-- Per build_tracker §5.9 (13 locked decisions) + api_contracts §5.1 #3 + §5.2.
--
-- Single change: CREATE TABLE chunk_embeddings (workspace-scoped, RLS day-1,
-- immutable via REVOKE). The 'embedded' lifecycle value is already permitted
-- by 0009's forward-compat CHECK constraint.
--
-- Idempotent so the migration runner's bootstrap test can re-apply.

-- ----------------------------------------------------------------------------
-- chunk_embeddings — immutable per-chunk vector.
-- ----------------------------------------------------------------------------
-- Decision #8: REVOKE UPDATE, DELETE — re-embedding implies a new model; write
-- a new row with a different model_id instead of mutating existing rows.
--
-- Decision #2: halfvec(3072) — pgvector's float16 variant; ~50% storage savings
-- vs vector(3072). Phase 4's HNSW index supports halfvec natively.
--
-- Decision #9: UNIQUE (contextual_chunk_id, model_id) — a future model upgrade
-- can backfill new rows without deleting old ones; Phase 4's HNSW filters by
-- model_id to pick the active vectors.

CREATE TABLE IF NOT EXISTS chunk_embeddings (
    id                      uuid         NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    contextual_chunk_id     uuid         NOT NULL REFERENCES contextual_chunks(id) ON DELETE CASCADE,
    file_id                 uuid         NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    workspace_id            uuid         NOT NULL,
    embedding               halfvec(3072) NOT NULL,
    model_id                text         NOT NULL,
    created_at              timestamptz  NOT NULL DEFAULT now(),
    UNIQUE (contextual_chunk_id, model_id)
);

CREATE INDEX IF NOT EXISTS chunk_embeddings_workspace_idx ON chunk_embeddings (workspace_id);
CREATE INDEX IF NOT EXISTS chunk_embeddings_file_idx      ON chunk_embeddings (file_id);

ALTER TABLE chunk_embeddings ENABLE ROW LEVEL SECURITY;
ALTER TABLE chunk_embeddings FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS chunk_embeddings_workspace_isolation ON chunk_embeddings;
CREATE POLICY chunk_embeddings_workspace_isolation
    ON chunk_embeddings
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

GRANT SELECT, INSERT ON chunk_embeddings TO kb_app;
REVOKE UPDATE, DELETE ON chunk_embeddings FROM kb_app;
