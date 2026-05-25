-- B6a / WA-12 — Conversation memory (Design 8).
--
-- Two tables back the 3-tier ChatContext:
--
--   chat_sessions — one row per conversation. Carries the Tier-3
--     structured state (carry_forward_entities, carry_forward_filters,
--     prior_result_set_id) + the Tier-2 rolling summary string. Updated
--     each turn.
--
--   chat_turns    — one row per user/assistant exchange in a session.
--     Stores both the original user_query and the anaphora-resolved
--     version, the context snapshot used for that turn (for replay),
--     the final answer, and the citations. Tier-1 hot turns are
--     materialized by SELECT … ORDER BY turn_index DESC LIMIT K.
--
-- The conversation itself is unbounded (Design 8 §"Three-tier memory":
-- ChatGPT/Claude pattern). MTRAG's K=6 cap applies only to the
-- retrieval-side anaphora resolver's input window.

-- ============================================================================
-- 1) chat_sessions
-- ============================================================================

CREATE TABLE IF NOT EXISTS chat_sessions (
    id                       uuid          NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    workspace_id             uuid          NOT NULL,
    user_id                  uuid,
    created_at               timestamptz   NOT NULL DEFAULT now(),
    last_active_at           timestamptz   NOT NULL DEFAULT now(),
    -- Tier-3 carry-forward state.
    carry_forward_entities   uuid[]        NOT NULL DEFAULT '{}'::uuid[],
    carry_forward_filters    jsonb         NOT NULL DEFAULT '{}'::jsonb,
    prior_result_set_id      uuid,
    -- Tier-2 rolling Mem0-style summary of older turns (≥1 paragraph;
    -- regenerated when older turns exceed a threshold).
    older_turn_summary       text          NOT NULL DEFAULT '',
    -- Optional human-friendly title (set by the UI on first message).
    title                    text
);

CREATE INDEX IF NOT EXISTS chat_sessions_workspace_active_idx
    ON chat_sessions (workspace_id, last_active_at DESC);

ALTER TABLE chat_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat_sessions FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS chat_sessions_isolation ON chat_sessions;
CREATE POLICY chat_sessions_isolation ON chat_sessions
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

REVOKE ALL ON chat_sessions FROM kb_app;
GRANT SELECT, INSERT, UPDATE ON chat_sessions TO kb_app;


-- ============================================================================
-- 2) chat_turns
-- ============================================================================

CREATE TABLE IF NOT EXISTS chat_turns (
    id              uuid          NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    workspace_id    uuid          NOT NULL,
    session_id      uuid          NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    turn_index      integer       NOT NULL CHECK (turn_index >= 0),
    user_query      text          NOT NULL,
    resolved_query  text,                 -- post anaphora resolution
    -- Snapshot of the ChatContext that was used for this turn — JSONB
    -- shape mirrors kb.domain.chat_memory.ChatContext. Enables replay.
    context_used    jsonb         NOT NULL DEFAULT '{}'::jsonb,
    answer          text,
    citations       jsonb         NOT NULL DEFAULT '[]'::jsonb,
    -- Optional back-link to the orchestrator's query_log row.
    query_log_id    uuid,
    -- Optional pointer for "filter the previous results" patterns.
    result_set_id   uuid,
    created_at      timestamptz   NOT NULL DEFAULT now(),
    UNIQUE (session_id, turn_index)
);

CREATE INDEX IF NOT EXISTS chat_turns_session_index_idx
    ON chat_turns (session_id, turn_index DESC);
CREATE INDEX IF NOT EXISTS chat_turns_workspace_created_idx
    ON chat_turns (workspace_id, created_at DESC);

ALTER TABLE chat_turns ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat_turns FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS chat_turns_isolation ON chat_turns;
CREATE POLICY chat_turns_isolation ON chat_turns
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

-- Append-only at the role level: SELECT + INSERT only. The session row
-- holds the mutating carry-forward state; individual turns never change.
REVOKE ALL ON chat_turns FROM kb_app;
GRANT SELECT, INSERT ON chat_turns TO kb_app;
