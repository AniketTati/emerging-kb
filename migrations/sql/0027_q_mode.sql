-- B4b — Q-mode (architecture §6 step 3 mode "Q"; gaps_design.md Design 1).
--
-- Two facets land in one migration:
--   1. `audit_queries` — one row per executed Q-mode plan. Stores the
--      planner's plan JSON, the compiled SQL + parameters, the row count,
--      the runtime, and the MinIO object key of the result CSV. The
--      Dashboard / Audit surfaces read this to render "what aggregate
--      was computed for which user query". Per Design 1 §"Execution +
--      Security" layer 10, this row is the audit primitive.
--   2. `kb_app_q` — a read-only role earmarked for Q-mode execution.
--      Defense-in-depth (Design 1 layer 7): even if a future bypass
--      slips a non-SELECT through the compiler, the role itself lacks
--      INSERT/UPDATE/DELETE/DDL on the allowed tables. For Wave A the
--      orchestrator still uses kb_app + SET LOCAL transaction_read_only
--      = on + SET LOCAL statement_timeout (layers 7+8 enforced at the
--      transaction); kb_app_q is wired in a follow-up when we move to
--      per-mode connection pools.

-- ============================================================================
-- 1) audit_queries table
-- ============================================================================

CREATE TABLE IF NOT EXISTS audit_queries (
    id                 uuid          NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    workspace_id       uuid          NOT NULL,
    -- Optional FK back to the query_log row that triggered this execution.
    -- ON DELETE SET NULL so retention purges on query_log don't cascade
    -- and destroy the audit_queries audit primitive.
    query_log_id       uuid          REFERENCES query_log(id) ON DELETE SET NULL,
    -- The Q-mode plan emitted by the planner — exactly what the validator
    -- + compiler consumed. Reproducible.
    plan               jsonb         NOT NULL,
    -- The parameterized SQL that the compiler emitted. NEVER contains
    -- user values — only $N placeholders.
    compiled_sql       text          NOT NULL CHECK (length(compiled_sql) BETWEEN 1 AND 65536),
    -- Parameter values bound to the placeholders. JSONB array preserves
    -- ordering. Each element is a primitive (string / number / bool / null).
    params             jsonb         NOT NULL DEFAULT '[]'::jsonb,
    -- Execution outcome.
    row_count          integer       NOT NULL DEFAULT 0 CHECK (row_count >= 0),
    runtime_ms         integer       NOT NULL DEFAULT 0 CHECK (runtime_ms >= 0),
    -- Status enum.
    status             text          NOT NULL DEFAULT 'ok'
                                     CHECK (status IN (
                                         'ok', 'refused', 'timeout',
                                         'row_cap_exceeded', 'error'
                                     )),
    refusal_reason     text,
    -- MinIO artifact key (e.g. 'q_mode_artifacts/<workspace>/<id>.csv').
    -- NULL when no rows / refusal. Downloaders read via storage layer.
    csv_artifact_key   text,
    created_at         timestamptz   NOT NULL DEFAULT NOW()
);

-- Workspace-scoped lookup index for /audit-queries list.
CREATE INDEX IF NOT EXISTS audit_queries_workspace_created_idx
    ON audit_queries (workspace_id, created_at DESC);

-- Drill-down from a query_log row to its Q-mode executions.
CREATE INDEX IF NOT EXISTS audit_queries_query_log_idx
    ON audit_queries (workspace_id, query_log_id)
    WHERE query_log_id IS NOT NULL;

ALTER TABLE audit_queries ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_queries FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS audit_queries_isolation ON audit_queries;
CREATE POLICY audit_queries_isolation ON audit_queries
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

-- Append-only audit semantics — INSERT + SELECT only, no UPDATE/DELETE.
REVOKE ALL ON audit_queries FROM kb_app;
GRANT SELECT, INSERT ON audit_queries TO kb_app;

-- ============================================================================
-- 2) kb_app_q — read-only role earmarked for Q-mode (forward-compat)
-- ============================================================================
-- Not wired by Wave A orchestrator (which uses kb_app + SET LOCAL transaction
-- read_only). Created here so a follow-up commit can opt in without a new
-- migration.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kb_app_q') THEN
        CREATE ROLE kb_app_q WITH LOGIN;
    END IF;
END
$$;

DO $$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO kb_app_q', current_database());
END
$$;

GRANT USAGE ON SCHEMA public TO kb_app_q;

-- SELECT only on the Wave A Q-mode catalog (mirror of kb.q_planner.catalog).
GRANT SELECT ON
    files,
    extracted_entities,
    atomic_units,
    relationships,
    fact_conflicts,
    doc_chains,
    doc_chain_members
    TO kb_app_q;
