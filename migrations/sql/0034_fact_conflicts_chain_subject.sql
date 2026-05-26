-- Wave A close-up (Design 2) — allow fact_conflicts to record
-- chain-based subjects.
--
-- Pre-fix schema (0024) declared `entity_id NOT NULL REFERENCES
-- entities(id)`. The Design 2 conflict cascade fires on chain-based
-- supersession (MSA vs Amendment on payment_terms) where the subject
-- is the DOC CHAIN, not an entity row — populating `entity_id` with
-- the chain_id silently failed the FK constraint and the SAVEPOINT
-- rolled back. Result: `fact_conflicts` table stayed empty during
-- normal use even when the chain rule kept firing.
--
-- Fix: split the "subject" into two nullable columns
-- (entity_id / chain_id), each with its own FK, plus a CHECK ensuring
-- at least one is set. Entity-based and chain-based conflicts both
-- persist + the Dashboard/Audit pages can finally surface them.

ALTER TABLE fact_conflicts
    ALTER COLUMN entity_id DROP NOT NULL;

ALTER TABLE fact_conflicts
    ADD COLUMN IF NOT EXISTS chain_id uuid
        REFERENCES doc_chains(id) ON DELETE CASCADE;

-- At least one of entity_id / chain_id must be populated. Both being
-- null would mean we have no subject to attach the predicate to.
ALTER TABLE fact_conflicts
    DROP CONSTRAINT IF EXISTS fact_conflicts_has_subject_check;
ALTER TABLE fact_conflicts
    ADD CONSTRAINT fact_conflicts_has_subject_check
        CHECK (entity_id IS NOT NULL OR chain_id IS NOT NULL);

-- Speed up dashboard "needs attention" queries that filter by chain.
CREATE INDEX IF NOT EXISTS fact_conflicts_chain_idx
    ON fact_conflicts (workspace_id, chain_id)
 WHERE chain_id IS NOT NULL;
