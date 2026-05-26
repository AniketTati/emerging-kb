-- Inventory I-mode (Q2 fix follow-up) — admit "I" in the query_log.mode
-- CHECK constraint. Without this, every chat call that routed through
-- the inventory short-circuit silently fails its audit log write with
-- "violates check constraint query_log_mode_check" — the response still
-- reaches the user (the log insert is best-effort) but the row never
-- lands in query_log, breaking /audit replay + the analytics dashboard
-- for inventory queries.

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
        'Q',  -- structured SQL query
        'K',  -- doc-chain aware
        'I'   -- inventory (workspace metadata listing)
    ));
