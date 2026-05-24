-- @no-transaction
-- Phase 4: HNSW + BM25 indexes on all RAPTOR levels.
-- Per build_tracker §5.11 (16 locked decisions).
--
-- This file requires the `@no-transaction` pragma (line 1) so the runner
-- executes statements under autocommit. `CREATE INDEX CONCURRENTLY` is
-- forbidden inside a transaction block by Postgres — autocommit is required.
-- Pragma was added to migrations/runner.py at G4 alongside this file.
--
-- All 4 indexes are CONCURRENTLY (decision #4) so live writers aren't blocked
-- during build. Cost: each takes longer than non-concurrent (~2× wall-clock)
-- because of the double-pass scan. Trade-off: zero downtime for Wave A worker
-- writes through deploy. IF NOT EXISTS makes re-runs safe.

-- ----------------------------------------------------------------------------
-- HNSW indexes (pgvector ≥ 0.8 + halfvec)
-- ----------------------------------------------------------------------------
-- Decision #1: 4-index scope.
-- Decision #2: `halfvec_cosine_ops` operator class. Embeddings are halfvec(3072)
--   since Phase 3c (3d's raptor_nodes uses same shape; 3e corpus rows reuse).
--   Cosine matches Gemini Embedding 001's optimization + the cross-level vector
--   space (per-doc + corpus summaries embedded in the same space — 3e #14).
-- Decision #3: m=16 / ef_construction=200 — pgvector defaults.
-- Decision #5: single shared graph (no per-workspace partitioning until ~1M docs).
--   RLS filters at query time via workspace_id WHERE-clause on the SELECT.

CREATE INDEX CONCURRENTLY IF NOT EXISTS chunk_embeddings_embedding_hnsw_idx
    ON chunk_embeddings
    USING hnsw (embedding halfvec_cosine_ops)
    WITH (m = 16, ef_construction = 200);

CREATE INDEX CONCURRENTLY IF NOT EXISTS raptor_nodes_embedding_hnsw_idx
    ON raptor_nodes
    USING hnsw (embedding halfvec_cosine_ops)
    WITH (m = 16, ef_construction = 200);

-- ----------------------------------------------------------------------------
-- BM25 indexes (ParadeDB pg_search / Tantivy)
-- ----------------------------------------------------------------------------
-- Decision #6: Tantivy default tokenizer (English Wave A corpus).
-- Decision #7: Robertson defaults (k1=1.2, b=0.75) — pg_search built-ins.
-- pg_search BM25 indexes need a `key_field` pointing at the table's primary
-- key. Returned hits use this field to identify rows.

CREATE INDEX CONCURRENTLY IF NOT EXISTS contextual_chunks_text_bm25_idx
    ON contextual_chunks
    USING bm25 (id, contextual_text)
    WITH (key_field = 'id');

CREATE INDEX CONCURRENTLY IF NOT EXISTS raptor_nodes_text_bm25_idx
    ON raptor_nodes
    USING bm25 (id, text)
    WITH (key_field = 'id');

-- ----------------------------------------------------------------------------
-- No GRANT changes (decision #15)
-- ----------------------------------------------------------------------------
-- kb_app already has SELECT on the 4 indexed tables (granted in 0010, 0011,
-- 0012). Postgres auto-grants index USAGE when SELECT is granted on the parent
-- table — no explicit GRANT needed.
--
-- No REVOKE either: the underlying tables retain their UPDATE/DELETE protections
-- from earlier migrations (REVOKEs landed at 0010/0011/0012). Indexes don't
-- have separate UPDATE/DELETE semantics.
