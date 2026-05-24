-- Phase 6: extracted_entities table + lifecycle CHECK widening.
-- Per build_tracker §5.13 (13 locked decisions). Architecture §5 steps 18 + 18.5.
--
-- Schema invariants:
--   - One row per extracted instance of a schema_entity (e.g., one Clause).
--   - `fields` jsonb: {field_name: value} per the schema_entity's schema_fields.
--   - `citations` jsonb: {field_name: contextual_chunk_id} per decision #5.
--   - `lineage_path` ltree: built from parent_entity_id chain per Design 7.
--   - Immutable; re-extract = DELETE+INSERT in same tx (decision #10).
--
-- Lifecycle CHECK widening adds `entities_extracting` between
-- units_extracting and ready. Previous 0009/0012/0014 migrations are
-- widened separately in the same commit (forward-compat per §0.15).
--
-- ltree extension is pre-existing from 0001_extensions.sql.

-- ----------------------------------------------------------------------------
-- Lifecycle CHECK widening — add `entities_extracting`
-- ----------------------------------------------------------------------------
ALTER TABLE files DROP CONSTRAINT IF EXISTS files_lifecycle_state_check;
ALTER TABLE files ADD CONSTRAINT files_lifecycle_state_check
    CHECK (lifecycle_state IN (
        'queued','parsing','parsed','chunked','contextualized','embedded',
        'raptor_building','mentions_extracting','fields_extracting',
        'units_extracting','entities_extracting','identity_resolving','ready','failed','deleted'
    ));

-- ----------------------------------------------------------------------------
-- extracted_entities — typed extracted instances per schema_entity
-- ----------------------------------------------------------------------------
-- Decision #8: REVOKE UPDATE — immutable. DELETE allowed for re-extract.

CREATE TABLE IF NOT EXISTS extracted_entities (
    id                      uuid          NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    schema_entity_id        uuid          NOT NULL REFERENCES schema_entities(id) ON DELETE CASCADE,
    file_id                 uuid          NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    workspace_id            uuid          NOT NULL,
    -- Self-FK for parent chain (per Design 7). NULL = root entity in this doc.
    parent_entity_id        uuid          NULL REFERENCES extracted_entities(id) ON DELETE SET NULL,
    -- ltree lineage path: <root_id>.<...>.<self_id>. NULL allowed until
    -- lineage assignment completes (rare race; usually populated in same tx).
    lineage_path            ltree         NULL,
    -- Typed field values per the schema_entity's schema_fields. Open jsonb so
    -- the worker doesn't need a per-schema migration.
    fields                  jsonb         NOT NULL DEFAULT '{}'::jsonb,
    -- Per-field citation map: {field_name: contextual_chunk_id}. Phase 8 retrieval
    -- joins this to contextual_chunks for the citation envelope.
    citations               jsonb         NOT NULL DEFAULT '{}'::jsonb,
    model_id                text          NOT NULL,
    created_at              timestamptz   NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS extracted_entities_workspace_schema_entity_idx
    ON extracted_entities (workspace_id, schema_entity_id);
CREATE INDEX IF NOT EXISTS extracted_entities_file_idx
    ON extracted_entities (file_id);
CREATE INDEX IF NOT EXISTS extracted_entities_parent_idx
    ON extracted_entities (parent_entity_id)
    WHERE parent_entity_id IS NOT NULL;
-- GiST index for ltree ancestor/descendant queries (Phase 8 lineage traversal).
CREATE INDEX IF NOT EXISTS extracted_entities_lineage_gist_idx
    ON extracted_entities USING gist (lineage_path);

ALTER TABLE extracted_entities ENABLE ROW LEVEL SECURITY;
ALTER TABLE extracted_entities FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS extracted_entities_workspace_isolation ON extracted_entities;
CREATE POLICY extracted_entities_workspace_isolation
    ON extracted_entities
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

GRANT SELECT, INSERT, DELETE ON extracted_entities TO kb_app;
-- Decision #8: UPDATE only on lineage_path + parent_entity_id (since these
-- are populated post-INSERT once parent is known). All other columns immutable.
GRANT UPDATE (lineage_path, parent_entity_id) ON extracted_entities TO kb_app;
