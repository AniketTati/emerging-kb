-- Phase 3b: contextual_chunks table — LLM-generated prefix per chunk.
-- Per build_tracker §5.8 (14 locked decisions) + api_contracts §5.1 #3 + §5.2.
--
-- Single change: CREATE TABLE contextual_chunks (workspace-scoped, RLS day-1,
-- immutable via REVOKE). The 'contextualized' lifecycle value is already
-- permitted by 0009's forward-compat CHECK constraint.
--
-- Idempotent so the migration runner's bootstrap test can re-apply.

-- ----------------------------------------------------------------------------
-- contextual_chunks — immutable per-chunk LLM-generated prefix.
-- ----------------------------------------------------------------------------
-- Decision #10: REVOKE UPDATE, DELETE — downstream Phase 3c embeddings
-- reference contextual_chunks by id; in-place mutation invalidates them.
--
-- Decision #11: persist cache_creation_input_tokens + cache_read_input_tokens
-- for post-hoc cache-rate auditing.
--
-- Decision #6: model_id column distinguishes 'identity' (no API key fallback)
-- from real LLM IDs like 'claude-opus-4-7' — alarm/dashboard on identity count
-- in production.

CREATE TABLE IF NOT EXISTS contextual_chunks (
    id                            uuid         NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    chunk_id                      uuid         NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    file_id                       uuid         NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    workspace_id                  uuid         NOT NULL,
    contextual_prefix             text         NOT NULL,
    contextual_text               text         NOT NULL,  -- = prefix + "\n\n" + chunks.text
    model_id                      text         NOT NULL,  -- 'claude-opus-4-7' | 'identity' | ...
    prefix_token_count            int          NOT NULL CHECK (prefix_token_count >= 0),
    cache_creation_input_tokens   int          NOT NULL DEFAULT 0,
    cache_read_input_tokens       int          NOT NULL DEFAULT 0,
    created_at                    timestamptz  NOT NULL DEFAULT now(),
    UNIQUE (chunk_id)
);

CREATE INDEX IF NOT EXISTS contextual_chunks_workspace_idx ON contextual_chunks (workspace_id);
CREATE INDEX IF NOT EXISTS contextual_chunks_file_idx      ON contextual_chunks (file_id);

ALTER TABLE contextual_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE contextual_chunks FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS contextual_chunks_workspace_isolation ON contextual_chunks;
CREATE POLICY contextual_chunks_workspace_isolation
    ON contextual_chunks
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

-- 0001 ALTER DEFAULT PRIVILEGES granted full CRUD; strip mutation set.
GRANT SELECT, INSERT ON contextual_chunks TO kb_app;
REVOKE UPDATE, DELETE ON contextual_chunks FROM kb_app;
