-- 0049_mentions_trigram_idx.sql
-- S3 — trigram GIN index for the mentions_exact retrieval channel.
--
-- channels.py mentions_exact_channel filters with
--   lower(mention_text) LIKE '%' || lower(<q>) || '%'
-- A leading-wildcard LIKE cannot use a btree index, so this was a full
-- scan of extracted_mentions (millions of rows at scale). A GIN trigram
-- index on lower(mention_text) makes the substring match index-backed.
-- pg_trgm is required for gin_trgm_ops; ensure it exists (it was not in
-- 0001_extensions.sql on this DB).
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS extracted_mentions_text_trgm_idx
    ON extracted_mentions
    USING gin (lower(mention_text) gin_trgm_ops);
