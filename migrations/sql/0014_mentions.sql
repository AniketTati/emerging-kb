-- Phase 5a: extracted_mentions table — LLM-based NER over contextual chunks.
-- Per build_tracker §5.12.1 (11 locked decisions).
--
-- Schema invariants:
--   - Per-contextual_chunk granularity (decision #1).
--   - OntoNotes-18 mention_type set (decision #2) — enforced by CHECK.
--   - Immutable (decision #5): REVOKE UPDATE/DELETE on kb_app.
--   - Re-extraction = DELETE-then-INSERT in same tx (decision #8).
--
-- Lifecycle CHECK widening (decision #6): adds mentions_extracting +
-- fields_extracting (Phase 5b) + units_extracting (Phase 5c) in ONE
-- migration. Forward-compat convention §0.15: every lifecycle-widening
-- migration enumerates ALL currently-planned states through the terminal.
--
-- Idempotent so the migration runner's bootstrap test can re-apply.

-- ----------------------------------------------------------------------------
-- Lifecycle CHECK widening — adds 5a's mentions_extracting +
-- forward-compat for 5b's fields_extracting and 5c's units_extracting.
-- ----------------------------------------------------------------------------

ALTER TABLE files DROP CONSTRAINT IF EXISTS files_lifecycle_state_check;
ALTER TABLE files ADD CONSTRAINT files_lifecycle_state_check
    CHECK (lifecycle_state IN (
        'queued','parsing','parsed','chunked','contextualized','embedded',
        'raptor_building','mentions_extracting','fields_extracting',
        'units_extracting','ready','failed','deleted'
    ));

-- ----------------------------------------------------------------------------
-- extracted_mentions — immutable per-chunk mention list.
-- ----------------------------------------------------------------------------
-- Decision #5: REVOKE UPDATE, DELETE — re-extract overwrites by DELETE+INSERT.
-- Decision #4: start/end/confidence nullable — LLM may omit; don't fail extract.
-- Decision #2: mention_type CHECK enforces OntoNotes-18 set.

CREATE TABLE IF NOT EXISTS extracted_mentions (
    id                      uuid          NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    contextual_chunk_id     uuid          NOT NULL REFERENCES contextual_chunks(id) ON DELETE CASCADE,
    file_id                 uuid          NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    workspace_id            uuid          NOT NULL,
    mention_text            text          NOT NULL CHECK (length(mention_text) BETWEEN 1 AND 1000),
    mention_type            text          NOT NULL
                                          CHECK (mention_type IN (
                                              'PERSON','NORP','FAC','ORG','GPE','LOC','PRODUCT',
                                              'EVENT','WORK_OF_ART','LAW','LANGUAGE','DATE','TIME',
                                              'PERCENT','MONEY','QUANTITY','ORDINAL','CARDINAL'
                                          )),
    start_offset            int           NULL,
    end_offset              int           NULL,
    confidence              real          NULL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    model_id                text          NOT NULL,
    created_at              timestamptz   NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS extracted_mentions_workspace_type_idx
    ON extracted_mentions (workspace_id, mention_type);
CREATE INDEX IF NOT EXISTS extracted_mentions_file_idx
    ON extracted_mentions (file_id);
CREATE INDEX IF NOT EXISTS extracted_mentions_workspace_text_idx
    ON extracted_mentions (workspace_id, mention_text);
CREATE INDEX IF NOT EXISTS extracted_mentions_chunk_idx
    ON extracted_mentions (contextual_chunk_id);

ALTER TABLE extracted_mentions ENABLE ROW LEVEL SECURITY;
ALTER TABLE extracted_mentions FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS extracted_mentions_workspace_isolation ON extracted_mentions;
CREATE POLICY extracted_mentions_workspace_isolation
    ON extracted_mentions
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

GRANT SELECT, INSERT, DELETE ON extracted_mentions TO kb_app;
-- Decision #5: extracted_mentions are immutable WITHIN a model_id run; UPDATE
-- forbidden. DELETE is allowed for the re-extract pattern (decision #8 —
-- DELETE-then-INSERT in same tx).
REVOKE UPDATE ON extracted_mentions FROM kb_app;
