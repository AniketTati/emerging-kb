-- Phase 0: append-only, partitioned-by-month audit log.
-- Architecture §6 lines 691-706 + §7 line 850.
--
-- Phase 0 ships the full table shape. Phase 9 adds:
--   • INSERT trigger that computes prev_hash + hash (SHA-256 chain).
--   • Nightly integrity walker job.
--   • Partition-rotation cron.
--   • GET /audit read API + SSE lifecycle endpoint.

CREATE TABLE IF NOT EXISTS audit_log (
    id            uuid         NOT NULL DEFAULT gen_random_uuid(),
    workspace_id  uuid         NOT NULL,
    created_at    timestamptz  NOT NULL DEFAULT now(),
    actor         text         NOT NULL,                -- user_id or 'system:<service>'
    action        text         NOT NULL,                -- 'schema.create', 'query.run', etc.
    entity_type   text,                                 -- 'schema', 'doc', 'entity', ...
    entity_id     text,
    query_id      uuid,                                 -- set on query-time audit rows (Phase 8+)
    payload       jsonb        NOT NULL,
    prev_hash     bytea,                                -- filled by Phase 9 INSERT trigger
    hash          bytea,                                -- filled by Phase 9 INSERT trigger
    PRIMARY KEY (id, created_at)                        -- partition key must be in PK
) PARTITION BY RANGE (created_at);

-- Initial partitions: current month + next month. Phase 9 cron rolls forward.
CREATE TABLE IF NOT EXISTS audit_log_2026_05 PARTITION OF audit_log
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE IF NOT EXISTS audit_log_2026_06 PARTITION OF audit_log
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');

-- Lookup indexes — Phase 8 query-time audit + Phase 9 read API both need these.
CREATE INDEX IF NOT EXISTS audit_log_ws_created_idx
    ON audit_log (workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS audit_log_ws_query_idx
    ON audit_log (workspace_id, query_id)
    WHERE query_id IS NOT NULL;

-- RLS day-1 per architecture §7. FORCE applies to table owner too.
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log FORCE ROW LEVEL SECURITY;

-- Policy: a session can only see rows for its current workspace.
-- current_setting(..., true) returns NULL when unset → cast to UUID is NULL
-- → equality is NULL → policy denies the row. This is the "no context, no rows" rule.
-- NULLIF(..., '') guards against the empty-string case PG returns when the GUC
-- was explicitly SET to ''.
DROP POLICY IF EXISTS audit_log_workspace_isolation ON audit_log;
CREATE POLICY audit_log_workspace_isolation
    ON audit_log
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

-- kb_app may INSERT and SELECT. UPDATE/DELETE intentionally NOT granted — append-only.
GRANT SELECT, INSERT ON audit_log TO kb_app;
