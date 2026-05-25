-- B4a / WA-9 + WA-10 — Intent classifier + Schema-aware planner.
--
-- Two adjustments to query_log so the planner's output is auditable:
--   1. Widen `mode` CHECK to admit all 12 spec modes (architecture §6 step 3).
--      We keep the CHECK loose ("any of the 12") rather than per-mode columns
--      so the planner can emit any mode without a schema migration.
--   2. Add `intent`, `intent_confidence`, and `plan` JSONB columns for
--      observability + replay. The Plan inspector (WA-14 dashboard) reads
--      `plan` to render "what the system did".
--
-- Q-mode (SQL aggregation) lands in a dedicated follow-up commit (B4b);
-- this migration only widens validation so the mode is *accepted* by the
-- API but immediately refused by the orchestrator until B4b.

-- ============================================================================
-- 1) Widen query_log.mode CHECK from {'H'} to all 12 spec modes
-- ============================================================================

ALTER TABLE query_log DROP CONSTRAINT IF EXISTS query_log_mode_check;
ALTER TABLE query_log ADD CONSTRAINT query_log_mode_check
    CHECK (mode IN (
        'E',  -- entity lookup
        'F',  -- field filter
        'S',  -- scoped chunk
        'H',  -- hybrid semantic (legacy default)
        'T',  -- graph traversal (PPR)
        'M',  -- mention search
        'G',  -- global summary (LazyGraphRAG)
        'D',  -- doc metadata filter
        'C',  -- atomic-unit filter
        'A',  -- anomaly filter
        'Q',  -- structured SQL query (defense lands in B4b)
        'K'   -- doc-chain aware
    ));

-- ============================================================================
-- 2) query_log — intent classifier + planner audit columns
-- ============================================================================
-- Per architecture §6 steps 1 + 3: the intent classifier emits one of 10
-- labels; the planner emits a typed JSON plan. Both are persisted so the
-- /audit and Plan-inspector surfaces can replay any query.

ALTER TABLE query_log
    ADD COLUMN IF NOT EXISTS intent TEXT;

-- Loose CHECK — forward-compatible. The 10 spec labels are enforced
-- in code (kb.query.intent.INTENT_LABELS); DB just guards against the
-- empty string + obvious typos.
ALTER TABLE query_log DROP CONSTRAINT IF EXISTS query_log_intent_check;
ALTER TABLE query_log ADD CONSTRAINT query_log_intent_check
    CHECK (intent IS NULL OR length(intent) BETWEEN 2 AND 64);

ALTER TABLE query_log
    ADD COLUMN IF NOT EXISTS intent_confidence DOUBLE PRECISION
        CHECK (
            intent_confidence IS NULL
            OR (intent_confidence >= 0.0 AND intent_confidence <= 1.0)
        );

ALTER TABLE query_log
    ADD COLUMN IF NOT EXISTS plan JSONB;

-- ============================================================================
-- 3) Index for the Plan inspector + mode analytics
-- ============================================================================

CREATE INDEX IF NOT EXISTS query_log_workspace_mode_idx
    ON query_log (workspace_id, mode, created_at DESC);

CREATE INDEX IF NOT EXISTS query_log_workspace_intent_idx
    ON query_log (workspace_id, intent, created_at DESC)
    WHERE intent IS NOT NULL;
