-- T2 (structured-first query pipeline) — two foundational schema additions.
--
-- (1) chat_sessions.carry_forward_predicate
--     The scope state machine (§6.7) carries the *ResolvedPredicate* across
--     turns, NOT just a set of file_ids. v1 stored file_ids only, which meant
--     a follow-up could intersect ("of those, the ones over 9%") but could
--     never RELAX a clause ("make it 8% not 9%") — the original bound was lost
--     the moment it became a doc-id set. Persisting the predicate (clauses +
--     ops + values + row_filters + confidence) makes RELAX possible and lets
--     Q-mode read row-level WHERE filters back out of the carried scope.
--
--     `carry_forward_file_scope` is a denormalized convenience copy of the
--     predicate's resolved file_scope, kept only for cheap surfacing in the
--     Plan Inspector ("scoped to N docs from your previous question"). The
--     predicate jsonb is the source of truth; this array is derived.
--
-- (2) workspace_schema_epoch
--     A monotonically-increasing token, one row per workspace, bumped on ANY
--     schema change — T1 corpus convergence (canonical key rewrites) AND a T1
--     manual display-rename (a pure presentation pointer). The live-schema
--     cache (kb.domain.structured_schema) is keyed on this epoch so a cached
--     LiveSchema / field_display_map is invalidated the instant the schema
--     moves. v1 keyed caches on schema_version, which a label-only rename
--     never bumped — so a freshly renamed field stayed invisible to the
--     planner until the process restarted (A10). The epoch fixes that.
--
-- There is intentionally no `workspaces` table in this codebase (everything is
-- workspace-keyed by uuid), so the epoch lives in its own tiny table rather
-- than a column on a central row.
--
-- Idempotent (IF NOT EXISTS / DROP POLICY IF EXISTS) so the migration runner's
-- bootstrap re-apply test can re-run it.

-- ---------------------------------------------------------------------------
-- (1) Carry the predicate (+ denormalized file scope) on chat_sessions.
--     Table-level GRANT SELECT/INSERT/UPDATE to kb_app (0029) already covers
--     these new columns, so no column-level grant is needed.
-- ---------------------------------------------------------------------------
ALTER TABLE chat_sessions
    ADD COLUMN IF NOT EXISTS carry_forward_predicate jsonb NOT NULL
        DEFAULT '{}'::jsonb;

ALTER TABLE chat_sessions
    ADD COLUMN IF NOT EXISTS carry_forward_file_scope uuid[] NULL;

-- ---------------------------------------------------------------------------
-- (2) Per-workspace schema epoch.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS workspace_schema_epoch (
    workspace_id    uuid          NOT NULL PRIMARY KEY,
    epoch           bigint        NOT NULL DEFAULT 0,
    updated_at      timestamptz   NOT NULL DEFAULT now()
);

ALTER TABLE workspace_schema_epoch ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspace_schema_epoch FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS workspace_schema_epoch_isolation ON workspace_schema_epoch;
CREATE POLICY workspace_schema_epoch_isolation ON workspace_schema_epoch
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

-- kb_app bumps the epoch on a manual display-rename (API path) and reads it on
-- every query; the worker (superuser role) bumps it during convergence.
-- SELECT + INSERT + UPDATE (the bump is an INSERT ... ON CONFLICT DO UPDATE).
REVOKE ALL ON workspace_schema_epoch FROM kb_app;
GRANT SELECT, INSERT, UPDATE ON workspace_schema_epoch TO kb_app;
