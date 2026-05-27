-- Grant Q-mode read access to canonical_entities.
--
-- Construction eval (q034 "how many distinct sub-contractors") showed
-- Q-mode is forced to filter extracted_entities by ONE narrow
-- unit_type, missing all the cross-doc entities (sub-contractors are
-- mentioned across drawings, contracts, daily reports — not in one
-- single unit_type). canonical_entities is the dedup'd cross-doc
-- entity layer; exposing it to Q-mode lets it answer entity-cardinality
-- questions correctly.
--
-- Also adds the role to the q-mode reader so RLS works the same as it
-- does on the existing extracted_entities path.

GRANT SELECT ON canonical_entities TO kb_app_q;
