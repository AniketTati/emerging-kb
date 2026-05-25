-- WA-1 / Design 9 — Layered configuration: runtime overrides.
--
-- Six-layer config resolution (most-specific → most-general):
--   1. user        config_overrides where scope_kind='user'
--   2. doc         config_overrides where scope_kind='doc'
--   3. doc_type    config_overrides where scope_kind='doc_type'
--   4. workspace   config_overrides where scope_kind='workspace'
--   5. domain      config/domains/<domain>.yaml
--   6. defaults    config/defaults.yaml
--
-- Layers 5 and 6 live as YAML on disk. Layers 1-4 live in this table for
-- runtime mutation via the Settings UI.
--
-- Per Design 9 §"Data model".

CREATE TABLE IF NOT EXISTS config_overrides (
    id              uuid          NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    workspace_id    uuid          NOT NULL,
    -- 'user' | 'doc' | 'doc_type' | 'workspace'
    scope_kind      text          NOT NULL,
    -- user_id (uuid as text) | doc_id (uuid as text) | doc_type_name (text)
    -- | workspace_id (uuid as text — equal to row's workspace_id for the
    -- 'workspace' scope; storing redundantly keeps the unique key flat)
    scope_id        text          NOT NULL,
    -- Dot-keyed path into the layered tree. E.g.,
    --   "extraction.l3.rarity_threshold"
    --   "retrieval.rerank.top_k"
    --   "models.extraction_llm"
    config_key      text          NOT NULL,
    -- JSON-typed value; matches the leaf type in the YAML config tree.
    config_value    jsonb         NOT NULL,
    -- Free-text "why this override exists" — surfaces in the Effective
    -- Config UI on hover. Audit log captures every change.
    reason          text,
    -- User id of the admin who set the override (uuid as text). NULL for
    -- automated overrides.
    set_by          text,
    set_at          timestamptz   NOT NULL DEFAULT now(),
    -- Soft-toggle without delete; revert sets active=false rather than
    -- removing the row so history is preserved.
    active          boolean       NOT NULL DEFAULT true,

    CHECK (scope_kind IN ('user', 'doc', 'doc_type', 'workspace'))
);

-- One active override per (workspace, scope, scope_id, key). Soft-deleting
-- via active=false allows re-setting the same key later without
-- ON CONFLICT acrobatics.
CREATE UNIQUE INDEX IF NOT EXISTS config_overrides_unique_active_idx
    ON config_overrides (workspace_id, scope_kind, scope_id, config_key)
    WHERE active = true;

CREATE INDEX IF NOT EXISTS config_overrides_lookup_idx
    ON config_overrides (workspace_id, scope_kind, scope_id, config_key)
    WHERE active = true;

CREATE INDEX IF NOT EXISTS config_overrides_workspace_active_idx
    ON config_overrides (workspace_id, active);

-- RLS — workspace isolation.
ALTER TABLE config_overrides ENABLE ROW LEVEL SECURITY;
ALTER TABLE config_overrides FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS config_overrides_isolation ON config_overrides;
CREATE POLICY config_overrides_isolation ON config_overrides
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

REVOKE ALL ON config_overrides FROM kb_app;
-- kb_app may read (Effective Config UI / resolve_config), insert (admin
-- override), update (toggle active), but NOT delete — history is preserved.
GRANT SELECT, INSERT, UPDATE ON config_overrides TO kb_app;
