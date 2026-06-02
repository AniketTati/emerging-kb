-- T1 (schema-as-a-view): append-only audit of in-place canonical key rewrites.
--
-- Corpus convergence now consolidates variant field spellings (e.g.
-- `end_balance`, `closing_bal` → `closing_balance`) by RENAMING the key in
-- the stored schema-based extraction (`extracted_entities.fields`) instead of
-- re-reading the documents. That key rewrite is a single set-based SQL UPDATE,
-- no LLM, no re-parse — the scalable replacement for the old blanket
-- re-extraction. The raw open-vocab layer (`proposed_fields`) is left
-- untouched as immutable evidence.
--
-- Every rewrite is recorded here so the decision is reversible + auditable
-- (finance provenance): you can see exactly which raw key became which
-- canonical key, by what method, and how many rows it touched.
--
-- Append-only: kb_app gets SELECT + INSERT only (no UPDATE / DELETE).
-- Idempotent (IF NOT EXISTS / DROP POLICY IF EXISTS) so the migration
-- runner's bootstrap re-apply test can re-run it.

CREATE TABLE IF NOT EXISTS field_rename_audit (
    id                  uuid          NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    workspace_id        uuid          NOT NULL,
    -- doc_type for scalar renames; NULL for column renames (which are
    -- unit_type-scoped, and a table can span doc_types).
    inferred_doc_type   text          NULL,
    -- 'scalar' = doc_root field key; 'column' = sub_entity (table) column key.
    scope               text          NOT NULL
                                      CHECK (scope IN ('scalar', 'column')),
    unit_type           text          NULL,        -- set when scope = 'column'
    raw_key             text          NOT NULL,
    canonical_key       text          NOT NULL,
    method              text          NOT NULL DEFAULT 'convergence'
                                      CHECK (method IN ('normalize', 'convergence', 'manual')),
    confidence          real          NOT NULL DEFAULT 1.0
                                      CHECK (confidence BETWEEN 0 AND 1),
    n_rows              int           NOT NULL DEFAULT 0,   -- rows rewritten
    created_at          timestamptz   NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS field_rename_audit_workspace_doctype_idx
    ON field_rename_audit (workspace_id, inferred_doc_type);
CREATE INDEX IF NOT EXISTS field_rename_audit_workspace_created_idx
    ON field_rename_audit (workspace_id, created_at DESC);

ALTER TABLE field_rename_audit ENABLE ROW LEVEL SECURITY;
ALTER TABLE field_rename_audit FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS field_rename_audit_workspace_isolation ON field_rename_audit;
CREATE POLICY field_rename_audit_workspace_isolation
    ON field_rename_audit
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

-- Append-only: no UPDATE / DELETE grant (audit rows are immutable).
GRANT SELECT, INSERT ON field_rename_audit TO kb_app;
