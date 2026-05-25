-- B5 / WA-11 — Hash-chained audit_log.
--
-- Phase 0's migration 0003_audit_log already shipped:
--   - audit_log table with prev_hash + hash bytea columns (unused so far)
--   - workspace-scoped RLS
--   - kb_app GRANT SELECT + INSERT only (append-only at the role level)
--
-- This migration installs the missing pieces per architecture §6 step 10:
--
--   1. pgcrypto extension (for SHA-256 via `digest()`)
--   2. BEFORE INSERT trigger that fills prev_hash + hash atomically:
--        prev_hash = last row's hash for this workspace (or genesis)
--        hash      = SHA-256(prev_hash || workspace_id || created_at || payload_json)
--      Genesis (first row in a workspace):
--        prev_hash = SHA-256("workspace:" + workspace_id + ":init:" + created_at)
--   3. Per-workspace advisory lock so concurrent inserts serialize their
--      chain reads (defends against fork-on-burst).
--   4. (workspace_id, hash) index for the integrity walker's lookup.
--   5. Helper function for the integrity walker (Python repo calls this).

-- ============================================================================
-- 1) pgcrypto for digest()
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;


-- ============================================================================
-- 1a) Append-only role grants — close a Phase 0 oversight
-- ============================================================================
-- Phase 0's 0001_extensions.sql sets ALTER DEFAULT PRIVILEGES granting full
-- CRUD on every NEW table to kb_app. Phase 0's 0003_audit_log.sql then
-- adds an explicit GRANT SELECT, INSERT — but that's additive, not
-- restrictive. The result: kb_app currently has UPDATE + DELETE on
-- audit_log too. Strip those now so the table is truly append-only at
-- the role level (matches audit_queries from B4b).

REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM kb_app;


-- ============================================================================
-- 1b) Per-statement timestamp default
-- ============================================================================
-- Phase 0 defaulted audit_log.created_at to now(), which returns the txn
-- start time and so collides for multiple rows in the same transaction.
-- The hash-chain trigger needs deterministic row ordering — switching
-- to clock_timestamp() gives sub-microsecond per-statement granularity.

ALTER TABLE audit_log ALTER COLUMN created_at SET DEFAULT clock_timestamp();


-- ============================================================================
-- 2) Hash-chain trigger
-- ============================================================================
-- Trigger body uses a per-workspace advisory lock keyed by the workspace UUID's
-- bigint hash. The lock auto-releases at transaction end.
--
-- Canonical payload bytes: workspace_id::text || '|' || created_at_epoch_us
-- || '|' || payload::text. Using '|' as a delimiter keeps the chain
-- reproducible from Python.

CREATE OR REPLACE FUNCTION audit_log_chain_trigger()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    prev_h bytea;
    lock_key bigint;
    canonical bytea;
BEGIN
    -- Per-workspace advisory lock. hashtextextended() → bigint; xact-scoped
    -- lock releases automatically on COMMIT/ROLLBACK.
    lock_key := hashtextextended(NEW.workspace_id::text, 0);
    PERFORM pg_advisory_xact_lock(lock_key);

    -- Find the most recent row for this workspace.
    SELECT hash INTO prev_h
      FROM audit_log
     WHERE workspace_id = NEW.workspace_id
  ORDER BY created_at DESC, id DESC
     LIMIT 1;

    IF prev_h IS NULL THEN
        -- Genesis hash: SHA-256("workspace:" + workspace_id + ":init:" + created_at)
        prev_h := digest(
            'workspace:' || NEW.workspace_id::text
                || ':init:' || NEW.created_at::text,
            'sha256'
        );
    END IF;

    NEW.prev_hash := prev_h;

    -- Canonical payload: prev_hash bytes || '|' || workspace_id::text || '|' ||
    -- created_at::text || '|' || payload::text (jsonb cast to text — PG's
    -- jsonb_to_text is stable for canonicalization at this layer).
    canonical := prev_h
        || convert_to('|' || NEW.workspace_id::text
                          || '|' || NEW.created_at::text
                          || '|' || COALESCE(NEW.payload::text, '{}'), 'UTF8');

    NEW.hash := digest(canonical, 'sha256');
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS audit_log_chain_trg ON audit_log;
CREATE TRIGGER audit_log_chain_trg
    BEFORE INSERT ON audit_log
    FOR EACH ROW
    EXECUTE FUNCTION audit_log_chain_trigger();


-- ============================================================================
-- 3) Indexes for the integrity walker
-- ============================================================================

CREATE INDEX IF NOT EXISTS audit_log_ws_hash_idx
    ON audit_log (workspace_id, hash);

-- Already exists from Phase 0:
--   audit_log_ws_created_idx ON (workspace_id, created_at DESC)


-- ============================================================================
-- 4) Integrity walker helper (server-side recompute)
-- ============================================================================
-- Returns one row per audit_log row in the workspace ordered by chain
-- position, with the EXPECTED hash recomputed alongside the STORED hash.
-- The Python walker (kb.domain.audit_chain.walk_chain) compares them
-- and surfaces the first mismatch.

CREATE OR REPLACE FUNCTION audit_log_recompute_chain(
    p_workspace_id uuid,
    p_limit int DEFAULT 5000
)
RETURNS TABLE (
    row_id uuid,
    chain_position int,
    created_at timestamptz,
    stored_prev_hash bytea,
    stored_hash bytea,
    expected_prev_hash bytea,
    expected_hash bytea,
    payload jsonb
)
LANGUAGE plpgsql
AS $$
DECLARE
    running_prev bytea;
    rec record;
    pos int := 0;
BEGIN
    running_prev := NULL;
    FOR rec IN
        SELECT id, audit_log.created_at, audit_log.prev_hash, audit_log.hash,
               audit_log.payload, audit_log.workspace_id
          FROM audit_log
         WHERE audit_log.workspace_id = p_workspace_id
      ORDER BY audit_log.created_at ASC, id ASC
         LIMIT p_limit
    LOOP
        IF running_prev IS NULL THEN
            -- Genesis prev_hash.
            running_prev := digest(
                'workspace:' || rec.workspace_id::text
                    || ':init:' || rec.created_at::text,
                'sha256'
            );
        END IF;
        row_id := rec.id;
        pos := pos + 1;
        chain_position := pos;
        created_at := rec.created_at;
        stored_prev_hash := rec.prev_hash;
        stored_hash := rec.hash;
        expected_prev_hash := running_prev;
        expected_hash := digest(
            running_prev
                || convert_to('|' || rec.workspace_id::text
                                  || '|' || rec.created_at::text
                                  || '|' || COALESCE(rec.payload::text, '{}'),
                              'UTF8'),
            'sha256'
        );
        payload := rec.payload;
        RETURN NEXT;
        -- The chain walks forward using the STORED hash so we surface the
        -- first divergence without propagating the error.
        running_prev := rec.hash;
    END LOOP;
END;
$$;

GRANT EXECUTE ON FUNCTION audit_log_recompute_chain(uuid, int) TO kb_app;
