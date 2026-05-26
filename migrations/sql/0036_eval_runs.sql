-- Wave-A close-out — `eval_runs` + `eval_run_results` for the Playground
-- Eval tab.
--
-- Two tables:
--
--   eval_runs          — one row per `POST /eval/run`. Lifecycle:
--                        queued → running → succeeded | failed.
--                        Stores config used (ragas/hhem flags) +
--                        the aggregated ScoreReport on success.
--
--   eval_run_results   — one row per question in the run. Carries the
--                        full per-question payload as jsonb so the UI
--                        can drill in without re-running.
--
-- The worker (`run_eval_suite` Procrastinate task) drives state through
-- the same code path the CLI uses; this just persists it so the UI can
-- poll without keeping the connection open for 5+ minutes.
--
-- RLS matches the rest of the schema: every row carries workspace_id +
-- a USING (and WITH CHECK) policy that reads `app.workspace_id`.

-- ============================================================================
-- 1) eval_runs
-- ============================================================================

CREATE TABLE IF NOT EXISTS eval_runs (
    id                uuid          NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    workspace_id      uuid          NOT NULL,
    status            text          NOT NULL DEFAULT 'queued'
                                    CHECK (status IN (
                                        'queued', 'running',
                                        'succeeded', 'failed'
                                    )),
    -- Config knobs the run was started with — surface in the UI so a
    -- past run can be reproduced.
    enable_ragas      boolean       NOT NULL DEFAULT false,
    enable_hhem       boolean       NOT NULL DEFAULT false,
    concurrency       integer       NOT NULL DEFAULT 2
                                    CHECK (concurrency BETWEEN 1 AND 16),
    questions_path    text,
    -- Aggregate ScoreReport.to_dict() blob. Populated on
    -- status='succeeded'; null while queued / running, null on failure.
    summary           jsonb,
    -- Tail of the worker log when status='failed' (truncated to ~2KB).
    error             text,
    -- For idempotent re-submission. UNIQUE per workspace so two clients
    -- can't accidentally start the same run twice.
    idempotency_key   text,
    started_at        timestamptz   NOT NULL DEFAULT NOW(),
    finished_at       timestamptz
);

CREATE INDEX IF NOT EXISTS eval_runs_workspace_started_idx
    ON eval_runs (workspace_id, started_at DESC);
-- Used by the pre-flight "is a run already in flight?" check before
-- accepting a new POST /eval/run.
CREATE INDEX IF NOT EXISTS eval_runs_workspace_active_idx
    ON eval_runs (workspace_id, status)
    WHERE status IN ('queued', 'running');
-- Idempotency lookup. Partial so NULL keys don't fight for the index.
CREATE UNIQUE INDEX IF NOT EXISTS eval_runs_idem_uniq
    ON eval_runs (workspace_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

ALTER TABLE eval_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE eval_runs FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS eval_runs_isolation ON eval_runs;
CREATE POLICY eval_runs_isolation ON eval_runs
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

REVOKE ALL ON eval_runs FROM kb_app;
-- Full CRUD: the worker transitions status and writes summary.
GRANT SELECT, INSERT, UPDATE ON eval_runs TO kb_app;


-- ============================================================================
-- 2) eval_run_results
-- ============================================================================

CREATE TABLE IF NOT EXISTS eval_run_results (
    run_id            uuid          NOT NULL
                                    REFERENCES eval_runs(id) ON DELETE CASCADE,
    workspace_id      uuid          NOT NULL,
    question_id       text          NOT NULL,
    -- Full EvalResult.to_dict() — the UI uses this for drill-in
    -- without re-running the question. Keeps the answer + per-row
    -- RAGAS/HHEM scores when present.
    payload           jsonb         NOT NULL,
    created_at        timestamptz   NOT NULL DEFAULT NOW(),
    PRIMARY KEY (run_id, question_id)
);

CREATE INDEX IF NOT EXISTS eval_run_results_workspace_idx
    ON eval_run_results (workspace_id, run_id);

ALTER TABLE eval_run_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE eval_run_results FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS eval_run_results_isolation ON eval_run_results;
CREATE POLICY eval_run_results_isolation ON eval_run_results
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

REVOKE ALL ON eval_run_results FROM kb_app;
-- INSERT-only from the worker; SELECT for the UI drill-in.
GRANT SELECT, INSERT ON eval_run_results TO kb_app;
