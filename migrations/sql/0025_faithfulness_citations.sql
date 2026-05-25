-- B3 / WA-7 + WA-8 — Polymorphic citations + HHEM faithfulness gate.
--
-- Design 5 (polymorphic citations): the citation envelope today is a JSONB
-- column on query_log. We DON'T introduce a new citations table — the JSONB
-- payload is widened in code (kb/query/citations.py) and is forward-compatible.
-- We add a denormalized `citation_modalities TEXT[]` column on query_log for
-- fast dashboard filtering ("show me /chat calls that cited image_bbox").
--
-- HHEM faithfulness gate (architecture §6 step 9, gate A): runs after generation,
-- assigns a verdict in {pass, low_confidence, refused, skipped} + a 0-1 score
-- + a regeneration count. Default in CI is the IdentityFaithfulnessGate
-- (always 'pass'); opt-in via KB_FAITHFULNESS_GATE env. The 5-rule cascade
-- from B2 (conflicts) is upstream of this; HHEM is the post-gen safety net.

-- ============================================================================
-- 1) query_log extensions — faithfulness verdict + score + regen count
-- ============================================================================

ALTER TABLE query_log
    ADD COLUMN IF NOT EXISTS faithfulness_score DOUBLE PRECISION;

ALTER TABLE query_log
    ADD COLUMN IF NOT EXISTS faithfulness_verdict TEXT;

-- CHECK enum — kept loose so a Wave B switch to richer states doesn't need a
-- new migration. 'skipped' = gate not run (no answer to check, e.g. refusal).
ALTER TABLE query_log DROP CONSTRAINT IF EXISTS query_log_faithfulness_verdict_check;
ALTER TABLE query_log ADD CONSTRAINT query_log_faithfulness_verdict_check
    CHECK (
        faithfulness_verdict IS NULL
        OR faithfulness_verdict IN ('pass', 'low_confidence', 'refused', 'skipped')
    );

ALTER TABLE query_log
    ADD COLUMN IF NOT EXISTS faithfulness_regenerations INTEGER NOT NULL DEFAULT 0
    CHECK (faithfulness_regenerations >= 0 AND faithfulness_regenerations <= 5);

-- ============================================================================
-- 2) query_log — denormalized modality array for dashboard filtering
-- ============================================================================
-- The citations JSONB payload now carries per-citation `modality` strings
-- (pdf_span, xlsx_row, atomic_unit, entity_ref, chain_ref, ...). For the
-- Needs-attention dashboard to filter "/chat calls that fell back to a less
-- precise modality", we mirror the distinct set into a TEXT[] column.

ALTER TABLE query_log
    ADD COLUMN IF NOT EXISTS citation_modalities TEXT[];

-- ============================================================================
-- 3) Indexes for the dashboard surfaces
-- ============================================================================

-- "Show me low_confidence / refused answers in the last 7 days" — primary
-- Needs-attention sort for WA-14 dashboard.
CREATE INDEX IF NOT EXISTS query_log_workspace_verdict_idx
    ON query_log (workspace_id, faithfulness_verdict, created_at DESC)
    WHERE faithfulness_verdict IN ('low_confidence', 'refused');

-- GIN index for modality filtering ("/chat where citation_modalities @> ARRAY['image_bbox']").
CREATE INDEX IF NOT EXISTS query_log_modalities_gin_idx
    ON query_log USING GIN (citation_modalities);
