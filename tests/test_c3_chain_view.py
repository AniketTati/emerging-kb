"""C3 — K-mode chain_view inference.

The Gemini planner often fills `chain_view` with a topic/doc-type string
instead of a view enum; coercing that to current_version dropped the
historical chain members for chain-walk questions. The parser now infers
the view from the query when chain_view is absent OR invalid.
"""

from __future__ import annotations

from kb.query.intent import IntentResult
from kb.query.planner import _infer_chain_view, _parse_plan_json


def test_infer_chain_view_walk_and_supersession():
    assert _infer_chain_view("Walk the safety incident-fall chain — initial vs investigation vs corrective") == "all_versions"
    assert _infer_chain_view("Summarise the incident from initial filing through corrective action") == "all_versions"
    assert _infer_chain_view("Is revA still authoritative for the wall position?") == "all_versions"
    assert _infer_chain_view("show the previous version only") == "history_only"
    assert _infer_chain_view("what is the current contract value") == "current_version"


def _intent():
    return IntentResult(label="lookup", confidence=0.9)


def test_parser_coerces_invalid_chain_view_to_inferred():
    # Gemini planner emitted a topic string instead of a view enum.
    raw = '{"mode": "K", "chain_view": "safety incident-fall"}'
    plan = _parse_plan_json(raw, _intent(),
                            "Walk the safety incident-fall chain")
    assert plan.chain_view == "all_versions"  # inferred, not coerced→current


def test_parser_infers_when_chain_view_absent():
    raw = '{"mode": "K"}'
    plan = _parse_plan_json(raw, _intent(),
                            "trace the amendment history over time")
    assert plan.chain_view == "all_versions"


def test_parser_keeps_valid_chain_view():
    raw = '{"mode": "K", "chain_view": "history_only"}'
    plan = _parse_plan_json(raw, _intent(), "anything")
    assert plan.chain_view == "history_only"
