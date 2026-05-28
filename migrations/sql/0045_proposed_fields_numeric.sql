-- Bug D Tier-1 Item #3 — numeric + currency columns on proposed_fields.
--
-- proposed_fields.value_text is the original surface form ("₹22 lakh",
-- "5,300,000", "$5.3M", "INR 22,00,000"). Q-mode aggregations (SUM /
-- AVG / MIN / MAX) currently cast value_text::numeric, which returns
-- NULL for everything but bare-numeric strings. Add a clean numeric
-- column (parsed at extraction time by kb.extraction.value_normalize)
-- plus a currency tag so cross-doc aggregations stop silently dropping
-- rows formatted with magnitude words or currency symbols.
--
-- Both columns are NULLable — old rows + non-numeric fields stay NULL,
-- which Q-mode SUM/AVG correctly skip.

ALTER TABLE proposed_fields
    ADD COLUMN IF NOT EXISTS value_numeric numeric,
    ADD COLUMN IF NOT EXISTS value_currency text;

CREATE INDEX IF NOT EXISTS proposed_fields_value_numeric_idx
    ON proposed_fields (workspace_id, inferred_doc_type, field_name)
    WHERE value_numeric IS NOT NULL;

GRANT UPDATE (value_numeric, value_currency) ON proposed_fields TO kb_app;
GRANT SELECT (value_numeric, value_currency) ON proposed_fields TO kb_app_q;
