-- Phase 5c: atomic_units table — L3 doc-type-specific structured units.
-- Per build_tracker §5.12.3 (10 locked decisions).
--
-- One row per atomic unit (clause / transaction / row). The `parameters`
-- jsonb is open-ended per plugin — see plugin docstrings for the
-- per-type schema.
--
-- Immutable (decision #8 idempotency = DELETE+INSERT in tx).
-- No lifecycle CHECK widening (already done at 0014).

CREATE TABLE IF NOT EXISTS atomic_units (
    id                      uuid          NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    file_id                 uuid          NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    workspace_id            uuid          NOT NULL,
    unit_type               text          NOT NULL CHECK (length(unit_type) BETWEEN 1 AND 50),
    parameters              jsonb         NOT NULL DEFAULT '{}'::jsonb,
    anchor_chunk_id         uuid          NULL REFERENCES contextual_chunks(id) ON DELETE SET NULL,
    rarity_score            real          NULL CHECK (rarity_score IS NULL OR rarity_score >= 0),
    model_id                text          NOT NULL,
    created_at              timestamptz   NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS atomic_units_workspace_type_idx
    ON atomic_units (workspace_id, unit_type);
CREATE INDEX IF NOT EXISTS atomic_units_file_idx
    ON atomic_units (file_id);
CREATE INDEX IF NOT EXISTS atomic_units_workspace_type_rarity_idx
    ON atomic_units (workspace_id, unit_type, rarity_score DESC NULLS LAST);

ALTER TABLE atomic_units ENABLE ROW LEVEL SECURITY;
ALTER TABLE atomic_units FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS atomic_units_workspace_isolation ON atomic_units;
CREATE POLICY atomic_units_workspace_isolation
    ON atomic_units
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

-- Immutable within a model_id run; UPDATE forbidden. DELETE allowed for
-- the re-extract pattern (decision #8 — DELETE-then-INSERT in same tx).
-- UPDATE only allowed on rarity_score (centroid recompute may write back).
GRANT SELECT, INSERT, UPDATE, DELETE ON atomic_units TO kb_app;
