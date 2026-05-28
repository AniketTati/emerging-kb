-- Bug D Tier-1 Item #4 — canonical_entity merge tracking.
--
-- The live insert-time dedup pipeline (deterministic → embedding ≥0.92
-- → LLM judge → create-new) misses fuzzy variants of the same entity
-- (e.g. "Mahalaxmi" / "Mahalaxmi Infra" / "Mahalaxmi Infrastructure
-- Pvt Ltd" all landed as separate canonical_entities, splitting the
-- mention pool 109 / 25 / 62 across rows that should be one).
--
-- Adding `merged_into uuid` lets us soft-merge surplus rows post-hoc
-- without losing the audit trail. The dedup script (scripts/
-- dedup_canonical_entities.py) repoints mention_to_entity to the
-- survivor and stamps `merged_into = <survivor_id>` on the loser
-- rows. Read paths (graph queries, Q-mode entity counts, knowledge-map
-- entities tab) should filter `WHERE merged_into IS NULL` to see the
-- post-merge view; old rows stay visible to audit log / replay.
--
-- mention_count on the survivor is recomputed = sum(cluster) during
-- the merge so Q-mode COUNT/SUM aggregations stay correct against the
-- active set.

ALTER TABLE canonical_entities
    ADD COLUMN IF NOT EXISTS merged_into uuid REFERENCES canonical_entities(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS merged_at timestamptz;

-- A merged row shouldn't point at itself.
ALTER TABLE canonical_entities
    DROP CONSTRAINT IF EXISTS canonical_entities_merged_into_not_self;
ALTER TABLE canonical_entities
    ADD CONSTRAINT canonical_entities_merged_into_not_self
    CHECK (merged_into IS NULL OR merged_into <> id);

-- Most read paths want the active view — fast partial index.
CREATE INDEX IF NOT EXISTS canonical_entities_active_idx
    ON canonical_entities (workspace_id, entity_type, lower(canonical_name))
    WHERE merged_into IS NULL;

-- Reverse-lookup: "what merged into this survivor?" — used by audit
-- + by the read-side fallback that follows the chain when an old
-- entity_id is referenced.
CREATE INDEX IF NOT EXISTS canonical_entities_merged_into_idx
    ON canonical_entities (merged_into)
    WHERE merged_into IS NOT NULL;

GRANT UPDATE (merged_into, merged_at, mention_count) ON canonical_entities TO kb_app;
GRANT SELECT (merged_into, merged_at) ON canonical_entities TO kb_app_q;
