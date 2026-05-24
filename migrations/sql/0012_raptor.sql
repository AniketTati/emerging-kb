-- Phase 3d: raptor_nodes + raptor_edges tables — per-doc RAPTOR trees
-- (Phase 3e adds corpus-level rows via the forward-compat scope column).
-- Per build_tracker §5.10 (16 locked decisions) + api_contracts §5.2 + §5.3.
--
-- Schema invariants:
--   - L1 leaves stay in contextual_chunks (decision #9). raptor_nodes is L2..6.
--   - Discriminated edge FK (decision #10): an edge's child is either a
--     raptor_nodes row (for L3+ internal edges) OR a contextual_chunks row
--     (for L2 leaf edges) — never both, never neither.
--   - Forward-compat (decision #16): scope enum + nullable file_id so
--     Phase 3e can write scope='corpus' rows without ALTER TABLE at
--     potentially 100M-row scale.
--   - Lifecycle CHECK widening (decision #12): add 'raptor_building' state.
--     'ready' is already in the 0009 forward-compat CHECK list.
--
-- Idempotent so the migration runner's bootstrap test can re-apply.

-- ----------------------------------------------------------------------------
-- Lifecycle CHECK widening — add 'raptor_building' intermediate state.
-- ----------------------------------------------------------------------------
-- The full forward-compat list is unchanged otherwise — same convention
-- 0009 + 0010 + 0011 established (every lifecycle-extending migration
-- enumerates all currently-planned states through the terminal).

ALTER TABLE files DROP CONSTRAINT IF EXISTS files_lifecycle_state_check;
ALTER TABLE files ADD CONSTRAINT files_lifecycle_state_check
    CHECK (lifecycle_state IN (
        'queued','parsing','parsed','chunked','contextualized','embedded',
        'raptor_building','mentions_extracting','fields_extracting',
        'units_extracting','ready','failed','deleted'
    ));

-- ----------------------------------------------------------------------------
-- raptor_nodes — L2..6 summary nodes (L1 leaves live in contextual_chunks).
-- ----------------------------------------------------------------------------
-- Decision #9: NO L1 here — saves 30 GB at 100K-doc scale vs denormalization.
-- Decision #16: scope + nullable file_id is forward-compat for Phase 3e
--   corpus-level rows (scope='corpus', file_id=NULL).
-- Decision #15: embedding is halfvec(3072) — same vector space as
--   chunk_embeddings.embedding so Phase 4 HNSW can index across both.
-- Decision #11: REVOKE UPDATE/DELETE on kb_app — immutable; rebuild requires
--   superuser delete + re-run.

CREATE TABLE IF NOT EXISTS raptor_nodes (
    id                      uuid          NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    scope                   text          NOT NULL DEFAULT 'per_doc'
                                          CHECK (scope IN ('per_doc','corpus')),
    file_id                 uuid          NULL REFERENCES files(id) ON DELETE CASCADE,
    workspace_id            uuid          NOT NULL,
    level                   int           NOT NULL CHECK (level BETWEEN 2 AND 6),
    text                    text          NOT NULL,
    embedding               halfvec(3072) NOT NULL,
    token_count             int           NULL,
    cluster_id_in_level     int           NOT NULL,
    summarizer_model_id     text          NOT NULL,
    embedder_model_id       text          NOT NULL,
    created_at              timestamptz   NOT NULL DEFAULT now(),
    -- Row CHECK: scope='per_doc' requires file_id, scope='corpus' forbids it.
    CONSTRAINT raptor_nodes_scope_file_id_consistency
        CHECK (
            (scope = 'per_doc' AND file_id IS NOT NULL) OR
            (scope = 'corpus'  AND file_id IS NULL)
        ),
    UNIQUE (scope, file_id, level, cluster_id_in_level)
);

CREATE INDEX IF NOT EXISTS raptor_nodes_workspace_file_level_idx
    ON raptor_nodes (workspace_id, scope, file_id, level);
CREATE INDEX IF NOT EXISTS raptor_nodes_workspace_scope_level_idx
    ON raptor_nodes (workspace_id, scope, level);

ALTER TABLE raptor_nodes ENABLE ROW LEVEL SECURITY;
ALTER TABLE raptor_nodes FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS raptor_nodes_workspace_isolation ON raptor_nodes;
CREATE POLICY raptor_nodes_workspace_isolation
    ON raptor_nodes
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

GRANT SELECT, INSERT ON raptor_nodes TO kb_app;
REVOKE UPDATE, DELETE ON raptor_nodes FROM kb_app;

-- ----------------------------------------------------------------------------
-- raptor_edges — discriminated child FK (decision #10).
-- ----------------------------------------------------------------------------
-- An edge's child is EXACTLY ONE of:
--   - raptor_nodes (for L3+ internal edges) → child_node_id
--   - contextual_chunks (for L2 leaf edges)  → child_contextual_chunk_id
-- The row CHECK enforces "exactly one non-null". Two explicit indexable FKs +
-- one CHECK guard is cleaner than polymorphic FK + safer than nullable-self-FK.

CREATE TABLE IF NOT EXISTS raptor_edges (
    id                          uuid          NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    parent_node_id              uuid          NOT NULL REFERENCES raptor_nodes(id) ON DELETE CASCADE,
    child_node_id               uuid          NULL REFERENCES raptor_nodes(id) ON DELETE CASCADE,
    child_contextual_chunk_id   uuid          NULL REFERENCES contextual_chunks(id) ON DELETE CASCADE,
    workspace_id                uuid          NOT NULL,
    created_at                  timestamptz   NOT NULL DEFAULT now(),
    -- Exactly one child column non-null (decision #10).
    CONSTRAINT raptor_edges_exactly_one_child
        CHECK ((child_node_id IS NOT NULL)::int + (child_contextual_chunk_id IS NOT NULL)::int = 1)
);

-- Partial UNIQUE indexes — one per child kind, both keyed on (parent, child).
CREATE UNIQUE INDEX IF NOT EXISTS raptor_edges_parent_child_node_uidx
    ON raptor_edges (parent_node_id, child_node_id)
    WHERE child_node_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS raptor_edges_parent_child_chunk_uidx
    ON raptor_edges (parent_node_id, child_contextual_chunk_id)
    WHERE child_contextual_chunk_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS raptor_edges_workspace_parent_idx
    ON raptor_edges (workspace_id, parent_node_id);
CREATE INDEX IF NOT EXISTS raptor_edges_workspace_child_node_idx
    ON raptor_edges (workspace_id, child_node_id)
    WHERE child_node_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS raptor_edges_workspace_child_chunk_idx
    ON raptor_edges (workspace_id, child_contextual_chunk_id)
    WHERE child_contextual_chunk_id IS NOT NULL;

ALTER TABLE raptor_edges ENABLE ROW LEVEL SECURITY;
ALTER TABLE raptor_edges FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS raptor_edges_workspace_isolation ON raptor_edges;
CREATE POLICY raptor_edges_workspace_isolation
    ON raptor_edges
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

GRANT SELECT, INSERT ON raptor_edges TO kb_app;
REVOKE UPDATE, DELETE ON raptor_edges FROM kb_app;
