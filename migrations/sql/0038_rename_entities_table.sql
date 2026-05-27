-- Rename `entities` → `canonical_entities` to disambiguate from the
-- `extracted_entities` table (the per-file typed-instance store).
--
-- These two have wildly different roles but their names are dangerously
-- similar:
--   entities            (workspace-wide canonical entity directory:
--                        "Acme Corp" once per workspace)
--   extracted_entities  (per-file typed instances: one BankStatement +
--                        N Transaction rows per uploaded bank statement)
--
-- After this migration the directory is named clearly; every existing
-- foreign key (mention_to_entity, relationships.subject_entity_id +
-- object_entity_id, graph_edges.src_entity_id + dst_entity_id,
-- fact_conflicts.entity_id) automatically retargets — Postgres stores
-- FK references by table-id, not name. RLS, GRANT, and policies persist
-- through the rename; we re-create the policy with the new name only
-- for clarity in pg_policies output.
--
-- Index names carry the old `entities_*` prefix. We rename them too so
-- nothing in pg_indexes still references the historical name.

ALTER TABLE entities RENAME TO canonical_entities;

-- Rename indexes for cosmetic / observability cleanliness — they all
-- still WORK with the old names, but `\d canonical_entities` will look
-- inconsistent until we sync them.
ALTER INDEX IF EXISTS entities_workspace_type_idx
    RENAME TO canonical_entities_workspace_type_idx;
ALTER INDEX IF EXISTS entities_workspace_name_idx
    RENAME TO canonical_entities_workspace_name_idx;
ALTER INDEX IF EXISTS entities_workspace_name_type_unique
    RENAME TO canonical_entities_workspace_name_type_unique;
ALTER INDEX IF EXISTS entities_embedding_hnsw_idx
    RENAME TO canonical_entities_embedding_hnsw_idx;

-- Recreate the RLS policy with the new name so `pg_policies` doesn't
-- still surface `entities_workspace_isolation` on a table called
-- `canonical_entities`.
DROP POLICY IF EXISTS entities_workspace_isolation ON canonical_entities;
CREATE POLICY canonical_entities_workspace_isolation
    ON canonical_entities
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

-- RLS settings + GRANTs survive the rename — no need to redo them.
