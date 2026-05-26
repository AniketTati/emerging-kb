-- Wave A close-up — allow kb_app to DELETE chat_sessions + chat_turns.
--
-- Pre-fix: 0029 granted only SELECT/INSERT/UPDATE on chat_sessions and
-- SELECT/INSERT on chat_turns. The chat-history sidebar's row-trash UX
-- needed DELETE; without it the API returned 500 ("permission denied
-- for table chat_sessions"). Adding DELETE is safe — RLS still scopes
-- the rows the API can touch to the caller's workspace, and chat_turns
-- has FK ON DELETE CASCADE so deleting a session cleans up its turns
-- atomically.
--
-- We do NOT add DELETE on `query_log` — that table is meant to be
-- append-only for audit. The chat-turn's `query_log_id` FK is
-- ON DELETE SET NULL so deleting a turn doesn't blow up its audit row.

GRANT DELETE ON chat_sessions TO kb_app;
GRANT DELETE ON chat_turns TO kb_app;
