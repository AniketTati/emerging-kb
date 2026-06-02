-- T1 (schema-as-a-view): user-facing display label for manual rename.
--
-- A manual Schema-Studio rename is a PRESENTATION change, not a data repair.
-- The system canonical key (schema_fields.name / inferred_schema_fields.
-- canonical_name — which equals the stored extracted_entities.fields jsonb key)
-- stays STABLE, and the user's chosen label lives in `display_name`. Renaming
-- is then an O(1) pointer update: no re-reading documents, no rewriting every
-- stored key, and no fight with automatic convergence (which never touches
-- display_name). The query layer maps display_name -> canonical key at query
-- time (see kb.query.mode_router._resolve_field_name).
--
-- This is the deliberate counterpart to automatic convergence: the machine
-- unifies its OWN spelling variants by rewriting stored keys (data hygiene);
-- a human relabels via this pointer (presentation). NULL display_name = no
-- custom label (fall back to the canonical name).
--
-- kb_app already holds table-level UPDATE on both tables (0007 / 0015), which
-- covers these new columns. Idempotent.

ALTER TABLE schema_fields
    ADD COLUMN IF NOT EXISTS display_name text NULL;

ALTER TABLE inferred_schema_fields
    ADD COLUMN IF NOT EXISTS display_name text NULL;
