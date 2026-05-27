-- Drop the `atomic_units` table.
--
-- After the KV+Tables collapse (extract_kv_tables_file_impl writes
-- extracted_entities children directly) and the legacy worker delete
-- (extract_atomic_units_file_impl + the per-plugin code paths are
-- gone from the codebase), nothing reads or writes atomic_units.
-- Every former reader migrated to extracted_entities WHERE unit_type
-- IS NOT NULL:
--   * Doc Detail sub-entities list  (domain/files.py:list_atomic_units)
--   * Explore-API counters          (api/explore.py)
--   * Rarity retrieval channel      (query/channels.py:atomic_units_rarity_channel)
--   * Conflict resolution scope     (query/conflict_resolution.py)
--   * Q-mode catalog                (q_planner/catalog.py — was migrated
--                                    at 0037; never read atomic_units to
--                                    begin with after the catalog refactor)
--
-- DROP TABLE CASCADE removes the table + every index + the RLS policy +
-- the workspace_isolation policy in one statement.

DROP TABLE IF EXISTS atomic_units CASCADE;
