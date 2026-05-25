-- WA-3 / Design 3 — Doc chains.
--
-- Architecture §5 new step 5.5 — runs immediately after parse, before
-- chunking. Lifecycle widening below adds `doc_chaining` between `parsed`
-- and `chunked`.
--
-- Two tables per Design 3 §"Data model":
--
-- 1. doc_chains            One row per logical chain (email thread,
--                          contract+amendment, drawing-revision set,
--                          circular+corrigendum, patient encounters).
-- 2. doc_chain_members     N rows per chain — one per member file.
--                          PK is (chain_id, doc_id). parent_doc_id is
--                          set for tree-shaped email threads.

-- ============================================================================
-- 1) Lifecycle widening — add 'doc_chaining' as the new step-5.5 state
-- ============================================================================

ALTER TABLE files DROP CONSTRAINT IF EXISTS files_lifecycle_state_check;
ALTER TABLE files ADD CONSTRAINT files_lifecycle_state_check
    CHECK (lifecycle_state IN (
        'queued', 'parsing', 'parsed',
        'doc_chaining',
        'chunked', 'contextualized', 'embedded',
        'raptor_building', 'mentions_extracting', 'fields_extracting',
        'units_extracting', 'entities_extracting', 'identity_resolving',
        'ready', 'failed', 'deleted'
    ));

-- ============================================================================
-- 2) doc_chains
-- ============================================================================

CREATE TABLE IF NOT EXISTS doc_chains (
    id                    uuid          NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    workspace_id          uuid          NOT NULL,
    -- Per Design 3: 6 enum values matching the per-type detectors.
    type                  text          NOT NULL
                                        CHECK (type IN (
                                            'email_thread', 'contract_chain',
                                            'drawing_revisions', 'circular_chain',
                                            'patient_chart', 'other'
                                        )),
    -- Display name for the chain. Inferred per-type (subject line for
    -- threads, normalized contract title, project_id, etc.).
    title                 text,
    -- The "current" version of the chain. For threads (no current version),
    -- this stays NULL. ON DELETE SET NULL so deleting the current_version
    -- file doesn't cascade the chain.
    current_version_id    uuid          REFERENCES files(id) ON DELETE SET NULL,
    -- Deduplication key — different per detector type. Stops duplicate
    -- chain creation when two parses converge on the same logical chain.
    chain_key             text,
    created_at            timestamptz   NOT NULL DEFAULT NOW(),
    member_count          integer       NOT NULL DEFAULT 0,
    detection_confidence  numeric(3,2)  NOT NULL,

    CHECK (detection_confidence >= 0 AND detection_confidence <= 1)
);

-- Per-workspace lookups by type (Schema-Studio › Inferred uses this).
CREATE INDEX IF NOT EXISTS doc_chains_workspace_type_idx
    ON doc_chains (workspace_id, type);

-- Idempotent chain reuse — when the detector recomputes the same key
-- (e.g., same email Message-ID root), look up an existing chain instead
-- of inserting a duplicate.
CREATE UNIQUE INDEX IF NOT EXISTS doc_chains_chain_key_unique_idx
    ON doc_chains (workspace_id, type, chain_key)
    WHERE chain_key IS NOT NULL;

ALTER TABLE doc_chains ENABLE ROW LEVEL SECURITY;
ALTER TABLE doc_chains FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS doc_chains_isolation ON doc_chains;
CREATE POLICY doc_chains_isolation ON doc_chains
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

REVOKE ALL ON doc_chains FROM kb_app;
-- kb_app inserts on detection, updates current_version on later amendments,
-- soft-deletes on "Unlink chain" UI action (DELETE permitted for Wave A;
-- audit log captures the unlink action separately).
GRANT SELECT, INSERT, UPDATE, DELETE ON doc_chains TO kb_app;


-- ============================================================================
-- 3) doc_chain_members
-- ============================================================================

CREATE TABLE IF NOT EXISTS doc_chain_members (
    chain_id      uuid          NOT NULL REFERENCES doc_chains(id) ON DELETE CASCADE,
    doc_id        uuid          NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    workspace_id  uuid          NOT NULL,
    -- Position within the chain. Email threads use chronological + tree
    -- index; contracts use amendment number; drawings use revision number;
    -- patient charts use encounter date order.
    version_index integer       NOT NULL,
    -- Per Design 3 11-value enum. Detector picks the appropriate role.
    role          text          NOT NULL
                                CHECK (role IN (
                                    'original', 'amendment', 'side_letter',
                                    'superseded', 'reply', 'forward',
                                    'revision', 'corrigendum',
                                    'encounter', 'lab', 'discharge', 'other'
                                )),
    -- For tree-shaped email threads — points at the email being replied to.
    parent_doc_id uuid          REFERENCES files(id) ON DELETE SET NULL,
    added_at      timestamptz   NOT NULL DEFAULT NOW(),

    PRIMARY KEY (chain_id, doc_id)
);

-- Reverse lookup: "what chain does this doc belong to?" — fast O(1) on
-- the index since the chain is at most ~100 members per Design 3 cap.
CREATE INDEX IF NOT EXISTS doc_chain_members_doc_idx
    ON doc_chain_members (doc_id);

-- Workspace-scoped variant for RLS (the PK + doc_idx don't carry
-- workspace_id so the policy needs an explicit index).
CREATE INDEX IF NOT EXISTS doc_chain_members_workspace_idx
    ON doc_chain_members (workspace_id, chain_id);

ALTER TABLE doc_chain_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE doc_chain_members FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS doc_chain_members_isolation ON doc_chain_members;
CREATE POLICY doc_chain_members_isolation ON doc_chain_members
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

REVOKE ALL ON doc_chain_members FROM kb_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON doc_chain_members TO kb_app;
