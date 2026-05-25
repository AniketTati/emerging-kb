-- Phase 8f — query_log audit table.
--
-- Workspace-scoped, RLS-forced, immutable (kb_app: SELECT + INSERT only).
-- Audit table per architecture §6 + build_tracker §5.15.6 decision #11.
-- Written once per /search and /chat call by the orchestrator.

CREATE TABLE IF NOT EXISTS query_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id    UUID NOT NULL,
    query           TEXT NOT NULL,
    mode            TEXT NOT NULL DEFAULT 'H',
    endpoint        TEXT NOT NULL,   -- 'search' | 'chat'
    rewrites        JSONB,           -- {original, step_back, hyde, query2doc}
    hit_ids         JSONB,           -- [{id, kind, score}, ...]
    crag_score      DOUBLE PRECISION,
    refused         BOOLEAN NOT NULL DEFAULT FALSE,
    refusal_reason  TEXT,
    answer          TEXT,
    citations       JSONB,           -- [{hit_id, kind, file_id, snippet_preview, score}]
    model_id        TEXT,
    latency_ms      INTEGER,
    idempotency_key TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CHECK (endpoint IN ('search', 'chat')),
    CHECK (mode IN ('H'))            -- Wave A only; widened in Wave B
);

-- RLS — workspace isolation.
ALTER TABLE query_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE query_log FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS query_log_isolation ON query_log;
CREATE POLICY query_log_isolation ON query_log
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

-- Audit immutability — kb_app may read + write, NOT update or delete.
REVOKE ALL ON query_log FROM kb_app;
GRANT SELECT, INSERT ON query_log TO kb_app;

-- Audit-list index (Phase 9 /audit will paginate by created_at DESC).
CREATE INDEX IF NOT EXISTS query_log_workspace_created_idx
    ON query_log (workspace_id, created_at DESC);

-- Idempotency-replay lookup index (Phase 8f decision #13).
CREATE INDEX IF NOT EXISTS query_log_workspace_idem_idx
    ON query_log (workspace_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
