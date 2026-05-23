-- Phase 1c: schema_entities + schema_fields + schema_relationships.
-- Per build_tracker §5.4 (13 locked decisions) + api_contracts §4.
--
-- Three workspace-scoped tables, each with own workspace_id + own RLS policy
-- (decision #11, belt-and-braces — does NOT rely on the parent schema's RLS
-- via FK joins). Soft delete via lifecycle_state; partial unique on
-- (parent_id, name) WHERE lifecycle_state='active' lets re-create with
-- same name after a delete.
--
-- All statements idempotent (IF NOT EXISTS, DROP POLICY IF EXISTS) so the
-- migration runner's bootstrap test can re-apply 0001..0007 against an
-- existing DB.

-- ----------------------------------------------------------------------------
-- schema_entities — entity types within a schema (e.g. File, Case, Note).
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS schema_entities (
    id              uuid         NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    schema_id       uuid         NOT NULL REFERENCES schemas(id) ON DELETE CASCADE,
    workspace_id    uuid         NOT NULL,
    name            text         NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    description     text         NOT NULL DEFAULT '',
    lifecycle_state text         NOT NULL DEFAULT 'active'
                                 CHECK (lifecycle_state IN ('active', 'deleted')),
    created_at      timestamptz  NOT NULL DEFAULT now(),
    updated_at      timestamptz  NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS schema_entities_schema_name_active_idx
    ON schema_entities (schema_id, name) WHERE lifecycle_state = 'active';
CREATE INDEX IF NOT EXISTS schema_entities_workspace_idx
    ON schema_entities (workspace_id);
CREATE INDEX IF NOT EXISTS schema_entities_schema_lifecycle_idx
    ON schema_entities (schema_id, lifecycle_state);

ALTER TABLE schema_entities ENABLE ROW LEVEL SECURITY;
ALTER TABLE schema_entities FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS schema_entities_workspace_isolation ON schema_entities;
CREATE POLICY schema_entities_workspace_isolation
    ON schema_entities
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

GRANT SELECT, INSERT, UPDATE, DELETE ON schema_entities TO kb_app;

-- ----------------------------------------------------------------------------
-- schema_fields — typed attributes on each entity, with NL extraction prompts.
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS schema_fields (
    id              uuid         NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    entity_id       uuid         NOT NULL REFERENCES schema_entities(id) ON DELETE CASCADE,
    workspace_id    uuid         NOT NULL,
    name            text         NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    type            text         NOT NULL
                                 CHECK (type IN ('string','number','boolean','date','datetime')),
    nl_description  text         NOT NULL DEFAULT '',
    is_required     boolean      NOT NULL DEFAULT false,
    lifecycle_state text         NOT NULL DEFAULT 'active'
                                 CHECK (lifecycle_state IN ('active', 'deleted')),
    created_at      timestamptz  NOT NULL DEFAULT now(),
    updated_at      timestamptz  NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS schema_fields_entity_name_active_idx
    ON schema_fields (entity_id, name) WHERE lifecycle_state = 'active';
CREATE INDEX IF NOT EXISTS schema_fields_workspace_idx
    ON schema_fields (workspace_id);
CREATE INDEX IF NOT EXISTS schema_fields_entity_lifecycle_idx
    ON schema_fields (entity_id, lifecycle_state);

ALTER TABLE schema_fields ENABLE ROW LEVEL SECURITY;
ALTER TABLE schema_fields FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS schema_fields_workspace_isolation ON schema_fields;
CREATE POLICY schema_fields_workspace_isolation
    ON schema_fields
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

GRANT SELECT, INSERT, UPDATE, DELETE ON schema_fields TO kb_app;

-- ----------------------------------------------------------------------------
-- schema_relationships — typed edges between entity types within a schema.
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS schema_relationships (
    id              uuid         NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    schema_id       uuid         NOT NULL REFERENCES schemas(id) ON DELETE CASCADE,
    workspace_id    uuid         NOT NULL,
    name            text         NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    from_entity_id  uuid         NOT NULL REFERENCES schema_entities(id) ON DELETE CASCADE,
    to_entity_id    uuid         NOT NULL REFERENCES schema_entities(id) ON DELETE CASCADE,
    kind            text         NOT NULL
                                 CHECK (kind IN ('contains','part_of','references','associates','attribute_link')),
    cardinality     text         NOT NULL DEFAULT 'one_to_many'
                                 CHECK (cardinality IN ('one_to_one','one_to_many','many_to_many')),
    cascade_delete  boolean      NOT NULL DEFAULT false,
    single_parent   boolean      NOT NULL DEFAULT true,
    lifecycle_state text         NOT NULL DEFAULT 'active'
                                 CHECK (lifecycle_state IN ('active', 'deleted')),
    created_at      timestamptz  NOT NULL DEFAULT now(),
    updated_at      timestamptz  NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS schema_relationships_schema_name_active_idx
    ON schema_relationships (schema_id, name) WHERE lifecycle_state = 'active';
CREATE INDEX IF NOT EXISTS schema_relationships_workspace_idx
    ON schema_relationships (workspace_id);
CREATE INDEX IF NOT EXISTS schema_relationships_from_idx
    ON schema_relationships (from_entity_id);
CREATE INDEX IF NOT EXISTS schema_relationships_to_idx
    ON schema_relationships (to_entity_id);
CREATE INDEX IF NOT EXISTS schema_relationships_schema_lifecycle_idx
    ON schema_relationships (schema_id, lifecycle_state);

ALTER TABLE schema_relationships ENABLE ROW LEVEL SECURITY;
ALTER TABLE schema_relationships FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS schema_relationships_workspace_isolation ON schema_relationships;
CREATE POLICY schema_relationships_workspace_isolation
    ON schema_relationships
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

GRANT SELECT, INSERT, UPDATE, DELETE ON schema_relationships TO kb_app;
