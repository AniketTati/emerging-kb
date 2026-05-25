-- B1 / WA-4 + WA-5 — architecture §5 stages 13, 16, 17.
--
-- Three layers in one migration because they share the same conceptual
-- entity graph:
--   stage 13 — extracted_triples       (subj, pred, obj) lifted from chunks
--   stage 16 — relationships           triples resolved to entity ids
--              relationship_evidence   per-triple backing
--   stage 17 — graph_edges             HippoRAG-ready adjacency for PPR
--
-- Plus forward-compat lifecycle CHECK widening for `triples_extracting`,
-- `relationships_building`, `graph_building`. Wave A runs all three as
-- additive post-stage events (no state gating), but the states are in the
-- enum so a Wave B switch doesn't need another migration.

-- ============================================================================
-- 0) Lifecycle widening — forward-compat for B1's three additive stages
-- ============================================================================

ALTER TABLE files DROP CONSTRAINT IF EXISTS files_lifecycle_state_check;
ALTER TABLE files ADD CONSTRAINT files_lifecycle_state_check
    CHECK (lifecycle_state IN (
        'queued', 'parsing', 'parsed',
        'doc_chaining',
        'chunked', 'contextualized', 'embedded',
        'raptor_building',
        'mentions_extracting', 'fields_extracting',
        'units_extracting',
        'triples_extracting',
        'entities_extracting', 'identity_resolving',
        'relationships_building', 'graph_building',
        'ready', 'failed', 'deleted'
    ));


-- ============================================================================
-- 1) extracted_triples — architecture §5 stage 13
-- ============================================================================
-- Light OpenIE — (subject, predicate, object) tuples extracted per
-- contextual chunk. Source material for stage 16 (relationships layer).
-- Kept after resolution for audit + UI inspection.

CREATE TABLE IF NOT EXISTS extracted_triples (
    id              uuid          NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    workspace_id    uuid          NOT NULL,
    file_id         uuid          NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    -- Source chunk we lifted the triple from. Citation grounding back-ref.
    -- Nullable so identity/imported triples can omit it.
    chunk_id        uuid,
    subject_text    text          NOT NULL,
    predicate_text  text          NOT NULL,
    object_text     text          NOT NULL,
    -- 0.0-1.0 — extractor confidence. Identity extractor never emits rows
    -- (so this is only set by the LLM path).
    confidence      double precision NOT NULL DEFAULT 0.5,
    model_id        text          NOT NULL DEFAULT 'identity',
    created_at      timestamptz   NOT NULL DEFAULT NOW(),

    CHECK (confidence >= 0 AND confidence <= 1),
    CHECK (length(subject_text) > 0),
    CHECK (length(predicate_text) > 0),
    CHECK (length(object_text) > 0)
);

CREATE INDEX IF NOT EXISTS extracted_triples_workspace_file_idx
    ON extracted_triples (workspace_id, file_id);
CREATE INDEX IF NOT EXISTS extracted_triples_subject_lookup
    ON extracted_triples (workspace_id, lower(subject_text));
CREATE INDEX IF NOT EXISTS extracted_triples_object_lookup
    ON extracted_triples (workspace_id, lower(object_text));

ALTER TABLE extracted_triples ENABLE ROW LEVEL SECURITY;
ALTER TABLE extracted_triples FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS extracted_triples_isolation ON extracted_triples;
CREATE POLICY extracted_triples_isolation ON extracted_triples
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

REVOKE ALL ON extracted_triples FROM kb_app;
-- INSERT only — extracted triples are immutable audit material.
GRANT SELECT, INSERT ON extracted_triples TO kb_app;


-- ============================================================================
-- 2) relationships — architecture §5 stage 16
-- ============================================================================
-- Entity-id-resolved relationships. Free-form `predicate` text for
-- Wave A (normalized via embedding clustering in worker; Wave B will
-- enum-lock the top-K predicates per domain).

CREATE TABLE IF NOT EXISTS relationships (
    id                  uuid          NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    workspace_id        uuid          NOT NULL,
    -- Both reference entities(id) — Phase 7 canonical directory.
    subject_entity_id   uuid          NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    object_entity_id    uuid          NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    predicate           text          NOT NULL,
    confidence          double precision NOT NULL DEFAULT 0.5,
    -- Number of supporting triples (rolled up from relationship_evidence
    -- on each insert into evidence).
    n_evidence          integer       NOT NULL DEFAULT 0,
    created_at          timestamptz   NOT NULL DEFAULT NOW(),
    updated_at          timestamptz   NOT NULL DEFAULT NOW(),

    CHECK (confidence >= 0 AND confidence <= 1),
    CHECK (length(predicate) > 0),
    CHECK (subject_entity_id <> object_entity_id),
    -- Same (subj, obj, predicate) tuple → one row. Resolver UPSERTs.
    UNIQUE (workspace_id, subject_entity_id, object_entity_id, predicate)
);

CREATE INDEX IF NOT EXISTS relationships_subject_idx
    ON relationships (workspace_id, subject_entity_id);
CREATE INDEX IF NOT EXISTS relationships_object_idx
    ON relationships (workspace_id, object_entity_id);
CREATE INDEX IF NOT EXISTS relationships_predicate_idx
    ON relationships (workspace_id, lower(predicate));

ALTER TABLE relationships ENABLE ROW LEVEL SECURITY;
ALTER TABLE relationships FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS relationships_isolation ON relationships;
CREATE POLICY relationships_isolation ON relationships
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

REVOKE ALL ON relationships FROM kb_app;
-- INSERT/UPDATE for upsert; DELETE for user-driven unlink (Wave B feedback);
-- kept SELECT for queries.
GRANT SELECT, INSERT, UPDATE, DELETE ON relationships TO kb_app;


-- relationship_evidence — per-triple backing for each relationship.
-- Lets the UI render "shown 3x in vendor_xyz.pdf p.4" via JOIN to triples.
CREATE TABLE IF NOT EXISTS relationship_evidence (
    id               uuid          NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    workspace_id     uuid          NOT NULL,
    relationship_id  uuid          NOT NULL REFERENCES relationships(id) ON DELETE CASCADE,
    triple_id        uuid          REFERENCES extracted_triples(id) ON DELETE SET NULL,
    file_id          uuid          REFERENCES files(id) ON DELETE CASCADE,
    chunk_id         uuid,
    confidence       double precision NOT NULL DEFAULT 0.5,
    created_at       timestamptz   NOT NULL DEFAULT NOW(),

    CHECK (confidence >= 0 AND confidence <= 1)
);

CREATE INDEX IF NOT EXISTS relationship_evidence_rel_idx
    ON relationship_evidence (relationship_id);
CREATE INDEX IF NOT EXISTS relationship_evidence_file_idx
    ON relationship_evidence (workspace_id, file_id);

ALTER TABLE relationship_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE relationship_evidence FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS relationship_evidence_isolation ON relationship_evidence;
CREATE POLICY relationship_evidence_isolation ON relationship_evidence
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

REVOKE ALL ON relationship_evidence FROM kb_app;
GRANT SELECT, INSERT ON relationship_evidence TO kb_app;


-- ============================================================================
-- 3) graph_edges — architecture §5 stage 17 (HippoRAG-2 ready)
-- ============================================================================
-- Wave A: derived adjacency for PPR. Edge kinds:
--   * 'relationship' — aggregated from relationships table
--   * 'co_mention'   — entities mentioned in the same atomic_unit
--   * 'lineage'      — parent/child via extracted_entities.lineage_path
-- Weights normalized in app code on rebuild (log-scaled evidence count).
-- Wave B Phase 14 swaps this for the full HippoRAG paper graph with
-- mention nodes + sentence nodes + PPR seed pre-computation.

CREATE TABLE IF NOT EXISTS graph_edges (
    id             uuid          NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    workspace_id   uuid          NOT NULL,
    src_entity_id  uuid          NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    dst_entity_id  uuid          NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    edge_kind      text          NOT NULL
                                  CHECK (edge_kind IN (
                                      'relationship', 'co_mention', 'lineage'
                                  )),
    -- Aggregated weight (sum of evidence + log scaling done in app code).
    weight         double precision NOT NULL DEFAULT 1.0,
    -- Provenance jsonb: array of {kind: 'rel'|'mention'|'lineage', ids: [...]}
    -- so the UI can drill from an edge to its supporting triples / mentions.
    source_refs    jsonb         NOT NULL DEFAULT '[]'::jsonb,
    created_at     timestamptz   NOT NULL DEFAULT NOW(),
    updated_at     timestamptz   NOT NULL DEFAULT NOW(),

    CHECK (src_entity_id <> dst_entity_id),
    UNIQUE (workspace_id, src_entity_id, dst_entity_id, edge_kind)
);

CREATE INDEX IF NOT EXISTS graph_edges_src_idx
    ON graph_edges (workspace_id, src_entity_id);
CREATE INDEX IF NOT EXISTS graph_edges_dst_idx
    ON graph_edges (workspace_id, dst_entity_id);

ALTER TABLE graph_edges ENABLE ROW LEVEL SECURITY;
ALTER TABLE graph_edges FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS graph_edges_isolation ON graph_edges;
CREATE POLICY graph_edges_isolation ON graph_edges
    USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
    WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);

REVOKE ALL ON graph_edges FROM kb_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON graph_edges TO kb_app;
