-- B6b / WA-13 — User feedback / correction loop (Design 4).
--
-- Four tables back the feedback surface:
--
--   corrections             — the user's complaint, structured at the
--                             point of click. Routes per (scope, severity).
--   entity_overrides        — admin / user rules (never_merge, always_merge,
--                             rename, split). Identity resolver respects
--                             these on next pass.
--   schema_field_overrides  — undo / retype / rename / blacklist a schema
--                             field. Promotion pipeline respects these.
--   regression_set          — auto-built from corrections; the eval harness
--                             (WA-17) re-runs these queries on each pipeline
--                             change to catch regressions.

-- ============================================================================
-- 1) corrections
-- ============================================================================

CREATE TABLE IF NOT EXISTS corrections (
    id                uuid          NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    workspace_id      uuid          NOT NULL,
    user_id           uuid,
    scope             text          NOT NULL
                                    CHECK (scope IN (
                                        'answer', 'citation', 'extraction',
                                        'entity_merge', 'entity_split',
                                        'schema_field', 'doc_chain',
                                        'source_authority', 'other'
                                    )),
    -- Precise target: query_id, doc_id+span, entity_id, field_name, chain_id, etc.
    target            jsonb         NOT NULL,
    observed_value    text,
    correct_value     text,
    reason            text,
    severity          text          NOT NULL DEFAULT 'important'
                                    CHECK (severity IN (
                                        'blocker', 'important', 'minor', 'enhancement'
                                    )),
    status            text          NOT NULL DEFAULT 'open'
                                    CHECK (status IN (
                                        'open', 'triaged', 'fixing',
                                        'verified', 'closed', 'rejected'
                                    )),
    resolution        jsonb,
    audit_query_id    uuid,
    created_at        timestamptz   NOT NULL DEFAULT NOW(),
    resolved_at       timestamptz
);

CREATE INDEX IF NOT EXISTS corrections_workspace_scope_status_idx
    ON corrections (workspace_id, scope, status, created_at DESC);
CREATE INDEX IF NOT EXISTS corrections_workspace_severity_idx
    ON corrections (workspace_id, severity, status)
    WHERE status NOT IN ('closed', 'rejected', 'verified');
-- Per Design 4: indexes on common drill targets.
CREATE INDEX IF NOT EXISTS corrections_target_doc_idx
    ON corrections ((target->>'doc_id'));
CREATE INDEX IF NOT EXISTS corrections_target_entity_idx
    ON corrections ((target->>'entity_id'));

ALTER TABLE corrections ENABLE ROW LEVEL SECURITY;
ALTER TABLE corrections FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS corrections_isolation ON corrections;
CREATE POLICY corrections_isolation ON corrections
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

REVOKE ALL ON corrections FROM kb_app;
-- Full CRUD: admins update status as they triage.
GRANT SELECT, INSERT, UPDATE ON corrections TO kb_app;


-- ============================================================================
-- 2) entity_overrides
-- ============================================================================

CREATE TABLE IF NOT EXISTS entity_overrides (
    id            uuid          NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    workspace_id  uuid          NOT NULL,
    rule_type     text          NOT NULL
                                CHECK (rule_type IN (
                                    'never_merge', 'always_merge', 'rename', 'split'
                                )),
    entity_a      uuid,
    entity_b      uuid,
    rename_to     text,
    reason        text,
    created_by    uuid,
    created_at    timestamptz   NOT NULL DEFAULT NOW(),
    active        boolean       NOT NULL DEFAULT true,
    -- Back-link to the correction that generated this rule.
    correction_id uuid          REFERENCES corrections(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS entity_overrides_workspace_active_idx
    ON entity_overrides (workspace_id, active, rule_type);
CREATE INDEX IF NOT EXISTS entity_overrides_workspace_entity_a_idx
    ON entity_overrides (workspace_id, entity_a)
    WHERE entity_a IS NOT NULL;

ALTER TABLE entity_overrides ENABLE ROW LEVEL SECURITY;
ALTER TABLE entity_overrides FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS entity_overrides_isolation ON entity_overrides;
CREATE POLICY entity_overrides_isolation ON entity_overrides
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

REVOKE ALL ON entity_overrides FROM kb_app;
GRANT SELECT, INSERT, UPDATE ON entity_overrides TO kb_app;


-- ============================================================================
-- 3) schema_field_overrides
-- ============================================================================

CREATE TABLE IF NOT EXISTS schema_field_overrides (
    id            uuid          NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    workspace_id  uuid          NOT NULL,
    -- Field reference as "<entity_name>.<field_name>" (e.g. "Contract.cap").
    field_path    text          NOT NULL CHECK (length(field_path) > 0),
    override_kind text          NOT NULL
                                CHECK (override_kind IN (
                                    'undo_promotion', 'retype', 'rename', 'blacklist'
                                )),
    details       jsonb         NOT NULL DEFAULT '{}'::jsonb,
    reason        text,
    created_by    uuid,
    created_at    timestamptz   NOT NULL DEFAULT NOW(),
    active        boolean       NOT NULL DEFAULT true,
    correction_id uuid          REFERENCES corrections(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS schema_field_overrides_workspace_active_idx
    ON schema_field_overrides (workspace_id, field_path, active);

ALTER TABLE schema_field_overrides ENABLE ROW LEVEL SECURITY;
ALTER TABLE schema_field_overrides FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS schema_field_overrides_isolation ON schema_field_overrides;
CREATE POLICY schema_field_overrides_isolation ON schema_field_overrides
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

REVOKE ALL ON schema_field_overrides FROM kb_app;
GRANT SELECT, INSERT, UPDATE ON schema_field_overrides TO kb_app;


-- ============================================================================
-- 4) regression_set
-- ============================================================================

CREATE TABLE IF NOT EXISTS regression_set (
    id                    uuid          NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    workspace_id          uuid          NOT NULL,
    source_correction_id  uuid          REFERENCES corrections(id) ON DELETE SET NULL,
    query_text            text          NOT NULL CHECK (length(query_text) > 0),
    -- Structured assertions: "answer must cite X", "field Y must equal Z".
    expected_facts        jsonb         NOT NULL DEFAULT '{}'::jsonb,
    implicated_docs       uuid[]        NOT NULL DEFAULT '{}'::uuid[],
    severity              text          NOT NULL DEFAULT 'important'
                                        CHECK (severity IN (
                                            'blocker', 'important', 'minor', 'enhancement'
                                        )),
    active                boolean       NOT NULL DEFAULT true,
    last_pass_at          timestamptz,
    last_fail_at          timestamptz,
    fail_count            integer       NOT NULL DEFAULT 0 CHECK (fail_count >= 0),
    created_at            timestamptz   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS regression_set_workspace_active_idx
    ON regression_set (workspace_id, active, severity);

ALTER TABLE regression_set ENABLE ROW LEVEL SECURITY;
ALTER TABLE regression_set FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS regression_set_isolation ON regression_set;
CREATE POLICY regression_set_isolation ON regression_set
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

REVOKE ALL ON regression_set FROM kb_app;
GRANT SELECT, INSERT, UPDATE ON regression_set TO kb_app;
