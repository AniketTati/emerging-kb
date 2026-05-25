-- WA-2 / Design 6 — Domain Vocabulary.
--
-- Per Design 6 §"Data model": one row per canonical term, with synonyms +
-- acronym expansion + definition + embedding for fallback lookup. Domain-
-- scoped (NOT workspace-scoped); a single domain can be shared across
-- many workspaces.
--
-- "domain" here is the same value used by `config/domains/<domain>.yaml`
-- (mixed_demo, legal_contracts, corporate_email, financial_filings, ...).
-- Stored as text to avoid coupling to a domains table we don't have.
--
-- Discovery integration (architecture §5 step 12e + Design 6 §"Pipeline
-- integration"): the L2b cross-doc field clusterer (Phase 5b
-- src/kb/extraction/fields.py) calls `discover_vocabulary_candidates()`
-- after clustering — when two emergent fields with semantically similar
-- names (cosine ≥ 0.85, ≥ 5 docs) survive, a row is INSERTed here with
-- source='discovered' and confidence set.
--
-- Query-time consumption (architecture §6 step 2.5, lands in WA-9):
-- - `resolve_synonyms(domain, term)` returns the row's synonyms[] for
--   BM25 augmentation
-- - `expand_acronym(domain, term)` returns expansion text for inline use
-- - `embedding_lookup(domain, vector, top_k)` for soft-expansion via
--   the HNSW index below

-- pgvector is enabled in Phase 0; halfvec is available too. Architecture
-- §3c chose halfvec(3072) for chunk embeddings; vocabulary uses the same
-- embedder so we match dimensionality. (Gemini embedding-001 = 3072.)
-- Per Design 6 §"Data model" the doc says VECTOR(768) — that was the
-- design's BGE-M3 era assumption. We store halfvec(3072) to match our
-- actual embedder.
CREATE TABLE IF NOT EXISTS domain_vocabulary (
    id              uuid          NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    domain_id       text          NOT NULL,
    -- The canonical term. For an acronym entry, this is the short form
    -- (e.g., "GST"); `expansion` holds the long form.
    canonical_term  text          NOT NULL,
    -- Free-vocab synonyms — used by BM25 augmentation.
    synonyms        text[]        NOT NULL DEFAULT '{}',
    -- For acronym entries: the long-form. NULL for synonym/definition entries.
    acronym_of      text,
    expansion       text,
    -- Domain definition (user-editable in /schema Vocabulary tab).
    definition      text,
    embedding       halfvec(3072),
    -- Provenance. 'discovered' = L2b clustering produced it; 'user_defined'
    -- = admin added via UI; 'imported' = bulk import from external vocab.
    source          text          NOT NULL DEFAULT 'discovered'
                                  CHECK (source IN ('user_defined', 'discovered', 'imported')),
    -- For discovered rows; user_defined rows are typically 1.0.
    confidence      double precision NOT NULL DEFAULT 1.0,
    -- How many docs contributed evidence (relevant for discovered).
    n_docs_observed integer       NOT NULL DEFAULT 0,
    active          boolean       NOT NULL DEFAULT true,
    created_at      timestamptz   NOT NULL DEFAULT NOW(),
    updated_at      timestamptz   NOT NULL DEFAULT NOW(),

    CHECK (confidence >= 0 AND confidence <= 1)
);

-- Per Design 6 UNIQUE(domain_id, canonical_term). PG requires expression
-- uniqueness via a separate index, not an inline column constraint.
CREATE UNIQUE INDEX IF NOT EXISTS domain_vocabulary_domain_term_unique_idx
    ON domain_vocabulary (domain_id, lower(canonical_term));

-- Synonyms GIN index for fast array containment lookup
-- ("is 'hold harmless' a synonym of any entry?").
CREATE INDEX IF NOT EXISTS domain_vocabulary_synonyms_gin
    ON domain_vocabulary USING gin (synonyms);

-- HNSW on the embedding for fallback similarity lookup. Partial so the
-- index only covers rows with embeddings (some user-defined entries may
-- not have them computed yet).
CREATE INDEX IF NOT EXISTS domain_vocabulary_embedding_hnsw
    ON domain_vocabulary USING hnsw (embedding halfvec_cosine_ops)
    WITH (m = 16, ef_construction = 200)
    WHERE embedding IS NOT NULL;

-- List lookups
CREATE INDEX IF NOT EXISTS domain_vocabulary_domain_active_idx
    ON domain_vocabulary (domain_id, active)
    WHERE active = true;

-- Acronyms: short form lookup. Partial on the rows that have an acronym.
CREATE INDEX IF NOT EXISTS domain_vocabulary_acronym_idx
    ON domain_vocabulary (domain_id, lower(canonical_term))
    WHERE acronym_of IS NOT NULL AND active = true;

-- ---------------------------------------------------------------------------
-- NOT workspace-scoped → no RLS. Vocabulary is a shared dictionary at the
-- domain layer. Workspace-level vocab in Wave B (architecture's stated
-- per-tenant vocab path).
-- ---------------------------------------------------------------------------

REVOKE ALL ON domain_vocabulary FROM kb_app;
-- kb_app may read and INSERT (discovery + admin add) + UPDATE (toggle
-- active, edit definition) + DELETE (admin discard).
GRANT SELECT, INSERT, UPDATE, DELETE ON domain_vocabulary TO kb_app;
