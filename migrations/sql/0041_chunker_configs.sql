-- Runtime-configurable chunker routing per (workspace, doc_type).
--
-- An admin can set "bank_statement docs use row-per-leaf with
-- chunk_sizes=[512,128]" via UI/API without a code deploy. The chunker
-- worker reads this table at the start of every chunk_file run; falls
-- back to a built-in default when no row matches.
--
-- doc_type='*' is the workspace-wide default (matches when no doc_type-
-- specific row exists). Both wildcard + specific rows can coexist; the
-- specific row wins.
--
-- chunker_kind values:
--   * hierarchical   — LlamaIndex HierarchicalNodeParser. Default.
--                      Uses chunk_sizes from config.
--   * row_per_leaf   — every parsed row (xlsx, csv) becomes its own
--                      level-0 leaf. Used for bank_statement, invoice,
--                      lab_report — docs where one row = one logical
--                      retrievable unit.
--   * message_per_leaf — every message in an email_thread becomes one
--                        leaf. Mid + root chunks group threads into
--                        topical subsections.
--   * clause_per_leaf — for contracts where Docling already identified
--                        section boundaries. Each clause = one leaf.

CREATE TABLE IF NOT EXISTS chunker_configs (
    id                  uuid          NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    workspace_id        uuid          NOT NULL,
    -- '*' = wildcard default for this workspace.
    doc_type            text          NOT NULL,
    chunker_kind        text          NOT NULL CHECK (chunker_kind IN (
        'hierarchical', 'row_per_leaf', 'message_per_leaf', 'clause_per_leaf'
    )),
    -- Chunk-size triple in tokens. Order: [root, mid, leaf]. NULL = use
    -- the chunker_kind's defaults.
    chunk_sizes         integer[]     NULL CHECK (
        chunk_sizes IS NULL OR (
            array_length(chunk_sizes, 1) BETWEEN 1 AND 4
            AND chunk_sizes <@ ARRAY[16, 32, 64, 128, 256, 512, 1024, 2048, 4096]
        )
    ),
    -- Overlap tokens between sibling chunks at the same level.
    -- NULL = chunker_kind default (typically 10-20% of leaf size).
    overlap_tokens      integer       NULL CHECK (
        overlap_tokens IS NULL OR (overlap_tokens >= 0 AND overlap_tokens < 512)
    ),
    -- Free-form jsonb for chunker-kind-specific knobs (e.g.
    -- row_per_leaf might carry `{"include_header_row": true}`).
    extra               jsonb         NOT NULL DEFAULT '{}'::jsonb,
    description         text          NOT NULL DEFAULT '',
    created_at          timestamptz   NOT NULL DEFAULT now(),
    updated_at          timestamptz   NOT NULL DEFAULT now()
);

-- One config per (workspace, doc_type) — the wildcard '*' coexists with
-- specific doc_types.
CREATE UNIQUE INDEX IF NOT EXISTS chunker_configs_workspace_doc_type_unique
    ON chunker_configs (workspace_id, doc_type);
CREATE INDEX IF NOT EXISTS chunker_configs_workspace_idx
    ON chunker_configs (workspace_id);

ALTER TABLE chunker_configs ENABLE ROW LEVEL SECURITY;
ALTER TABLE chunker_configs FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS chunker_configs_workspace_isolation ON chunker_configs;
CREATE POLICY chunker_configs_workspace_isolation
    ON chunker_configs
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

GRANT SELECT, INSERT, UPDATE, DELETE ON chunker_configs TO kb_app;
