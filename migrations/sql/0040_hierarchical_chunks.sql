-- Hierarchical chunking (LlamaIndex HierarchicalNodeParser pattern).
--
-- A document is now parsed into a TREE of chunks:
--   * level 0 (leaves)   — ~128 tokens, indexed for retrieval
--   * level 1 (mids)     — ~512 tokens
--   * level 2 (roots)    — ~2048 tokens
--
-- Each child links to its parent via `parent_chunk_id`. Only leaves
-- carry `chunk_embeddings` and `contextual_chunks` rows — parents are
-- looked up by FK at AutoMerging time, not retrieved by similarity.
--
-- Backwards compat: existing rows default to `parent_chunk_id=NULL` +
-- `node_level=0` (treated as orphan leaves). A re-chunk pass rebuilds
-- the tree for any file we want hierarchical retrieval on.

ALTER TABLE chunks
    ADD COLUMN IF NOT EXISTS parent_chunk_id uuid
        REFERENCES chunks(id) ON DELETE CASCADE,
    ADD COLUMN IF NOT EXISTS node_level integer NOT NULL DEFAULT 0
        CHECK (node_level >= 0 AND node_level <= 5);

CREATE INDEX IF NOT EXISTS chunks_parent_idx
    ON chunks (parent_chunk_id) WHERE parent_chunk_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS chunks_workspace_level_idx
    ON chunks (workspace_id, node_level);
CREATE INDEX IF NOT EXISTS chunks_file_level_idx
    ON chunks (file_id, node_level);

-- The retrieval channels only want leaves (level=0). Add a partial
-- index on (file_id) WHERE node_level=0 so BM25/dense channels'
-- file-scoped lookups can pin to leaves cheaply.
CREATE INDEX IF NOT EXISTS chunks_file_leaves_idx
    ON chunks (file_id) WHERE node_level = 0;

GRANT UPDATE (parent_chunk_id, node_level) ON chunks TO kb_app;
