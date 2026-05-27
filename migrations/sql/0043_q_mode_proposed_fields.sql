-- Grant Q-mode read access to proposed_fields.
--
-- Construction eval (q033 "total cumulative change-order value")
-- showed Q-mode can't answer cross-doc summary aggregations because
-- the relevant scalar values (total_cost_premium, contract_value,
-- effective_date, etc.) are written to `proposed_fields`, not to
-- `extracted_entities`. Without this GRANT, the LLM can't query
-- those top-level scalars and falls back to narrow unit_type
-- filters that pick the wrong row.

GRANT SELECT ON proposed_fields TO kb_app_q;
