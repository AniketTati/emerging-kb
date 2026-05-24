-- Phase 5b: emergent fields + doc-type classifier + auto-promotion plumbing.
-- Per build_tracker §5.12.2 (11 locked decisions).
--
-- Adds:
--   - files.inferred_doc_type (text NULL) — populated by the doc-type classifier
--     in extract_fields_file_impl.
--   - schema_fields.auto_promoted (boolean) — distinguishes machine-promoted
--     fields from user-added ones (Phase 10d UI badges them).
--   - proposed_fields (raw per-doc LLM output, immutable).
--   - inferred_schema_fields (per-(workspace, doc_type, canonical_name) row
--     with prevalence/stability/value_type_confidence metrics; UPDATEable
--     since metrics refresh as more docs land).
--
-- No lifecycle CHECK widening — already done at 0014 (forward-compat for
-- fields_extracting + units_extracting).
--
-- Idempotent so the migration runner's bootstrap test can re-apply.

-- ----------------------------------------------------------------------------
-- files.inferred_doc_type — populated by the classifier (decision #1)
-- ----------------------------------------------------------------------------

ALTER TABLE files ADD COLUMN IF NOT EXISTS inferred_doc_type text NULL;

-- Index for cross-doc clustering reads: WHERE workspace_id=X AND inferred_doc_type=Y.
CREATE INDEX IF NOT EXISTS files_workspace_doctype_idx
    ON files (workspace_id, inferred_doc_type)
    WHERE inferred_doc_type IS NOT NULL;

-- ----------------------------------------------------------------------------
-- schema_fields.auto_promoted — decision #7
-- ----------------------------------------------------------------------------
-- DEFAULT false so existing rows stay user-added.

ALTER TABLE schema_fields ADD COLUMN IF NOT EXISTS auto_promoted boolean NOT NULL DEFAULT false;

-- ----------------------------------------------------------------------------
-- proposed_fields — raw per-doc field-extraction output (decision #3)
-- ----------------------------------------------------------------------------
-- Immutable (REVOKE UPDATE on kb_app); re-extract = DELETE-then-INSERT in tx.

CREATE TABLE IF NOT EXISTS proposed_fields (
    id                      uuid          NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    file_id                 uuid          NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    workspace_id            uuid          NOT NULL,
    inferred_doc_type       text          NULL,
    field_name              text          NOT NULL CHECK (length(field_name) BETWEEN 1 AND 200),
    field_description       text          NOT NULL DEFAULT '',
    value_text              text          NULL,
    value_type              text          NOT NULL DEFAULT 'text'
                                          CHECK (value_type IN ('text','number','date','datetime','boolean','enum')),
    is_pii                  boolean       NOT NULL DEFAULT false,
    model_id                text          NOT NULL,
    created_at              timestamptz   NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS proposed_fields_workspace_doctype_idx
    ON proposed_fields (workspace_id, inferred_doc_type);
CREATE INDEX IF NOT EXISTS proposed_fields_file_idx
    ON proposed_fields (file_id);
CREATE INDEX IF NOT EXISTS proposed_fields_workspace_name_idx
    ON proposed_fields (workspace_id, field_name);

ALTER TABLE proposed_fields ENABLE ROW LEVEL SECURITY;
ALTER TABLE proposed_fields FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS proposed_fields_workspace_isolation ON proposed_fields;
CREATE POLICY proposed_fields_workspace_isolation
    ON proposed_fields
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

GRANT SELECT, INSERT, DELETE ON proposed_fields TO kb_app;
REVOKE UPDATE ON proposed_fields FROM kb_app;

-- ----------------------------------------------------------------------------
-- inferred_schema_fields — clustered + promotion-ready (decision #4)
-- ----------------------------------------------------------------------------
-- One row per (workspace, doc_type, canonical_name). UPDATEable because
-- metrics (n_docs_observed, prevalence, ...) refresh as docs accumulate.
-- promoted_at + promoted_schema_field_id link the auto-promoted typed-schema row.

CREATE TABLE IF NOT EXISTS inferred_schema_fields (
    id                          uuid          NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    workspace_id                uuid          NOT NULL,
    inferred_doc_type           text          NOT NULL,
    canonical_name              text          NOT NULL CHECK (length(canonical_name) BETWEEN 1 AND 200),
    description                 text          NOT NULL DEFAULT '',
    value_type                  text          NOT NULL DEFAULT 'text'
                                              CHECK (value_type IN ('text','number','date','datetime','boolean','enum')),
    n_docs_observed             int           NOT NULL DEFAULT 0,
    prevalence                  real          NOT NULL DEFAULT 0.0 CHECK (prevalence BETWEEN 0 AND 1),
    stability                   real          NOT NULL DEFAULT 0.0 CHECK (stability BETWEEN 0 AND 1),
    value_type_confidence       real          NOT NULL DEFAULT 0.0 CHECK (value_type_confidence BETWEEN 0 AND 1),
    is_promoted                 boolean       NOT NULL DEFAULT false,
    promoted_at                 timestamptz   NULL,
    promoted_schema_field_id    uuid          NULL REFERENCES schema_fields(id) ON DELETE SET NULL,
    created_at                  timestamptz   NOT NULL DEFAULT now(),
    updated_at                  timestamptz   NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS inferred_schema_fields_uniq
    ON inferred_schema_fields (workspace_id, inferred_doc_type, canonical_name);
CREATE INDEX IF NOT EXISTS inferred_schema_fields_workspace_doctype_idx
    ON inferred_schema_fields (workspace_id, inferred_doc_type);
CREATE INDEX IF NOT EXISTS inferred_schema_fields_promoted_idx
    ON inferred_schema_fields (workspace_id, is_promoted);

ALTER TABLE inferred_schema_fields ENABLE ROW LEVEL SECURITY;
ALTER TABLE inferred_schema_fields FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS inferred_schema_fields_workspace_isolation ON inferred_schema_fields;
CREATE POLICY inferred_schema_fields_workspace_isolation
    ON inferred_schema_fields
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

-- UPDATE allowed (metrics refresh per decision #4). DELETE allowed (workspace
-- rebuild — e.g. doc-type taxonomy change).
GRANT SELECT, INSERT, UPDATE, DELETE ON inferred_schema_fields TO kb_app;
