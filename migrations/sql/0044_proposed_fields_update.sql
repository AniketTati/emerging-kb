-- Grant UPDATE on proposed_fields to kb_app so the schema editor
-- (PATCH /knowledge-map/schemas/{doctype}/fields/{field_id}) can
-- backfill field_name renames across the per-file proposed_fields
-- rows.
--
-- The original 0015_emergent_fields migration deliberately marked
-- proposed_fields as append-only (REVOKE UPDATE), assuming each
-- extraction wipes and re-inserts. The schema editor is a new
-- workflow where users can rename a field deliberately — that
-- rename must propagate to existing per-file rows or query results
-- diverge from the user's chosen canonical name.

GRANT UPDATE ON proposed_fields TO kb_app;
