-- Phase 7: identity resolution — entities + mention_to_entity tables.
-- Per build_tracker §5.14 (10 locked decisions). Architecture §5 step 15.

-- ----------------------------------------------------------------------------
-- Lifecycle CHECK widening — add identity_resolving
-- ----------------------------------------------------------------------------
ALTER TABLE files DROP CONSTRAINT IF EXISTS files_lifecycle_state_check;
ALTER TABLE files ADD CONSTRAINT files_lifecycle_state_check
    CHECK (lifecycle_state IN (
        'queued','parsing','parsed','chunked','contextualized','embedded',
        'raptor_building','mentions_extracting','fields_extracting',
        'units_extracting','entities_extracting','identity_resolving',
        'ready','failed','deleted'
    ));

-- ----------------------------------------------------------------------------
-- entities — canonical entity directory (workspace-scoped)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS entities (
    id                  uuid          NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    workspace_id        uuid          NOT NULL,
    canonical_name      text          NOT NULL CHECK (length(canonical_name) BETWEEN 1 AND 500),
    entity_type         text          NOT NULL,
    embedding           halfvec(3072) NULL,  -- nullable until embedder ran
    mention_count       int           NOT NULL DEFAULT 1,
    created_at          timestamptz   NOT NULL DEFAULT now(),
    updated_at          timestamptz   NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS entities_workspace_type_idx
    ON entities (workspace_id, entity_type);
CREATE INDEX IF NOT EXISTS entities_workspace_name_idx
    ON entities (workspace_id, canonical_name);
-- Deterministic-match lookup (decision #3 stage a): exact name+type per workspace.
CREATE UNIQUE INDEX IF NOT EXISTS entities_workspace_name_type_unique
    ON entities (workspace_id, lower(canonical_name), entity_type);
-- HNSW for embedding-blocking nearest-neighbor (decision #10).
CREATE INDEX IF NOT EXISTS entities_embedding_hnsw_idx
    ON entities USING hnsw (embedding halfvec_cosine_ops)
    WITH (m = 16, ef_construction = 200)
    WHERE embedding IS NOT NULL;

ALTER TABLE entities ENABLE ROW LEVEL SECURITY;
ALTER TABLE entities FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS entities_workspace_isolation ON entities;
CREATE POLICY entities_workspace_isolation
    ON entities
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

-- Decision #4: UPDATEable (canonical_name + mention_count refresh).
GRANT SELECT, INSERT, UPDATE, DELETE ON entities TO kb_app;

-- ----------------------------------------------------------------------------
-- mention_to_entity — one mention links to exactly one entity
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mention_to_entity (
    mention_id          uuid          NOT NULL REFERENCES extracted_mentions(id) ON DELETE CASCADE PRIMARY KEY,
    entity_id           uuid          NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    workspace_id        uuid          NOT NULL,
    confidence          real          NOT NULL DEFAULT 1.0 CHECK (confidence BETWEEN 0 AND 1),
    resolved_method     text          NOT NULL CHECK (resolved_method IN ('deterministic','embedding','llm_judge','identity')),
    created_at          timestamptz   NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS mention_to_entity_entity_idx
    ON mention_to_entity (entity_id);
CREATE INDEX IF NOT EXISTS mention_to_entity_workspace_idx
    ON mention_to_entity (workspace_id);

ALTER TABLE mention_to_entity ENABLE ROW LEVEL SECURITY;
ALTER TABLE mention_to_entity FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS mention_to_entity_workspace_isolation ON mention_to_entity;
CREATE POLICY mention_to_entity_workspace_isolation
    ON mention_to_entity
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

GRANT SELECT, INSERT, DELETE ON mention_to_entity TO kb_app;
REVOKE UPDATE ON mention_to_entity FROM kb_app;
