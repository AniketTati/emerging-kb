"""Query half of FIX 4 (step 1) — the planner must PARSE field_filters
from the routing JSON so F-mode (mode_router._route_f_mode) has predicates
to apply.

Before this, `_parse_plan_json` silently dropped `field_filters`, so every
F-mode query degraded to an unfiltered H pass-through — the structured
query "which loans have an interest rate over 9%" could never filter on the
typed `extracted_entities.fields` value.
"""

from __future__ import annotations

from kb.query.intent import IntentResult
from kb.query.planner import _parse_plan_json


def _intent() -> IntentResult:
    return IntentResult(label="field_filter", confidence=0.9)


def test_parses_numeric_field_filter():
    raw = (
        '{"mode": "F", "field_filters": '
        '[{"field": "interest_rate", "op": "gt", "value": 9}]}'
    )
    plan = _parse_plan_json(
        raw, _intent(), "which loans have an interest rate over 9%"
    )
    assert plan.mode == "F"
    assert plan.field_filters == (
        {"field": "interest_rate", "op": "gt", "value": 9.0},
    )


def test_coerces_stringy_percent_value_for_numeric_op():
    # The LLM frequently echoes the query's "%" / thousands separators.
    raw = (
        '{"mode": "F", "field_filters": '
        '[{"field": "interest_rate", "op": "ge", "value": "9%"}]}'
    )
    plan = _parse_plan_json(raw, _intent(), "rate >= 9%")
    assert plan.field_filters[0]["value"] == 9.0

    raw2 = (
        '{"mode": "F", "field_filters": '
        '[{"field": "principal_amount", "op": "gt", "value": "1,000,000"}]}'
    )
    plan2 = _parse_plan_json(raw2, _intent(), "principal over a million")
    assert plan2.field_filters[0]["value"] == 1_000_000.0


def test_non_numeric_op_value_passes_through():
    raw = (
        '{"mode": "F", "field_filters": '
        '[{"field": "doc_status", "op": "like", "value": "superseded"}]}'
    )
    plan = _parse_plan_json(raw, _intent(), "superseded loans")
    assert plan.field_filters == (
        {"field": "doc_status", "op": "like", "value": "superseded"},
    )


def test_default_op_is_eq():
    raw = (
        '{"mode": "F", "field_filters": '
        '[{"field": "doc_status", "value": "active"}]}'
    )
    plan = _parse_plan_json(raw, _intent(), "active loans")
    assert plan.field_filters == (
        {"field": "doc_status", "op": "eq", "value": "active"},
    )


def test_drops_malformed_and_unknown_op_filters():
    raw = (
        '{"mode": "F", "field_filters": ['
        '{"field": "interest_rate", "op": "gt", "value": 9},'
        '{"op": "gt", "value": 1},'                       # no field
        '{"field": "  ", "op": "eq", "value": 1},'        # blank field
        '{"field": "x", "op": "approx", "value": 1},'     # unknown op
        '"not-a-dict"]}'
    )
    plan = _parse_plan_json(raw, _intent(), "q")
    assert plan.field_filters == (
        {"field": "interest_rate", "op": "gt", "value": 9.0},
    )


def test_absent_field_filters_is_empty_tuple():
    # No regression for the overwhelmingly common non-F query.
    plan = _parse_plan_json('{"mode": "H"}', _intent(), "hello there")
    assert plan.field_filters == ()


def test_non_list_field_filters_is_ignored():
    plan = _parse_plan_json(
        '{"mode": "F", "field_filters": "interest_rate>9"}', _intent(), "q"
    )
    assert plan.field_filters == ()
