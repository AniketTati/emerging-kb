-- GIN index on query_log.citations so the doc-detail page's
-- "queries that cited this doc" lookup is O(log N) instead of a full scan.
-- The endpoint queries with `citations @> '[{"file_id":"…"}]'::jsonb`;
-- jsonb_path_ops keeps the index small (only @> uses it) since that's
-- the only operator we need.

CREATE INDEX IF NOT EXISTS query_log_citations_gin_idx
    ON query_log USING GIN (citations jsonb_path_ops);
