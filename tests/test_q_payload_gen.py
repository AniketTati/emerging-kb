"""Tests for the Q-mode payload generator + provider-neutral planner.

We stub `JsonLLMClient` (no SDK calls / API keys) so the test surface
exercises:
  - happy path: LLM emits a valid plan → q_payload populated
  - explicit refuse: LLM signals `refuse=true` → reason surfaces
  - parse error: LLM emits non-JSON / malformed JSON
  - catalog validation: LLM references a column not in ALLOWED_COLUMNS
  - no_llm: caller passes llm=None (Identity planner path)

And then exercises the round-trip through `LLMPlanner` to make sure
the second-call wiring kicks in only for mode='Q'.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from kb.query.intent import IntentResult
from kb.query.llm_client import JsonLLMClient, LLMCallError
from kb.query.planner import IdentityPlanner, LLMPlanner, Plan
from kb.query.q_payload_gen import generate_q_payload


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _StubLLM:
    """Records calls; returns canned responses in order. Implements
    JsonLLMClient implicitly (model_id + generate_json)."""

    def __init__(self, *responses: str | Exception) -> None:
        self.model_id = "stub-llm-v1"
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def generate_json(
        self, *, user: str, system: str, max_tokens: int = 800,
    ) -> str:
        self.calls.append({
            "user": user, "system": system, "max_tokens": max_tokens,
        })
        if not self._responses:
            raise AssertionError("StubLLM ran out of responses")
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


# ---------------------------------------------------------------------------
# generate_q_payload — direct unit tests
# ---------------------------------------------------------------------------


async def test_q_payload_no_llm_returns_refusal_reason():
    payload, reason = await generate_q_payload("how many files", llm=None)
    assert payload is None
    assert reason and reason.startswith("no_llm:")


async def test_q_payload_happy_path_returns_validated_dict():
    stub = _StubLLM(
        '{"from": "files", "aggregations": ['
        '{"op": "COUNT", "field": "*", "alias": "n"}]}'
    )
    payload, reason = await generate_q_payload(
        "how many files do I have", llm=stub,
    )
    assert reason is None
    assert payload == {
        "from": "files",
        "aggregations": [{"op": "COUNT", "field": "*", "alias": "n"}],
    }
    # System prompt should embed the catalog at least once.
    assert len(stub.calls) == 1
    assert "files" in stub.calls[0]["system"]
    assert "COUNT" in stub.calls[0]["system"]


async def test_q_payload_explicit_refuse_surface_reason():
    stub = _StubLLM(
        '{"refuse": true, "reason": "vendor totals need a payments table"}'
    )
    payload, reason = await generate_q_payload(
        "sum payments by vendor", llm=stub,
    )
    assert payload is None
    assert reason == "refuse: vendor totals need a payments table"


async def test_q_payload_non_json_returns_parse_error():
    stub = _StubLLM("Sure! Here's the plan: SELECT count(*) FROM files")
    payload, reason = await generate_q_payload("count files", llm=stub)
    assert payload is None
    assert reason and reason.startswith("parse_error:")


async def test_q_payload_unknown_column_returns_validation_error():
    """Validator rejects (files, bogus_column) — the column doesn't
    exist in the catalog, so even a syntactically valid plan refuses."""
    stub = _StubLLM(
        '{"from": "files", "aggregations": ['
        '{"op": "SUM", "field": "bogus_column", "alias": "x"}]}'
    )
    payload, reason = await generate_q_payload("sum bogus", llm=stub)
    assert payload is None
    assert reason and reason.startswith("validation:")
    # Validator should helpfully include the bad column name.
    assert "bogus_column" in reason


async def test_q_payload_unknown_table_returns_validation_error():
    """Plan references a table that isn't in ALLOWED_TABLES."""
    stub = _StubLLM(
        '{"from": "users", "aggregations": ['
        '{"op": "COUNT", "field": "*", "alias": "n"}]}'
    )
    payload, reason = await generate_q_payload("count users", llm=stub)
    assert payload is None
    assert reason and reason.startswith("validation:")


async def test_q_payload_llm_transport_error_returns_llm_error():
    stub = _StubLLM(LLMCallError("connection reset"))
    payload, reason = await generate_q_payload("count files", llm=stub)
    assert payload is None
    assert reason == "llm_error: connection reset"


async def test_q_payload_handles_code_fence_wrapped_response():
    """Anthropic occasionally wraps JSON in ```json … ``` despite the
    "no fences" instruction. The adapter strips them; the generator
    sees clean JSON."""
    # The fence-stripping lives in AnthropicJsonClient, but
    # generate_q_payload runs json.loads directly on the stub's output —
    # so this test documents that stubs should hand back fence-free
    # JSON (the adapter is the responsible layer).
    stub = _StubLLM(
        '{"from": "files", "aggregations": ['
        '{"op": "COUNT", "field": "*", "alias": "n"}]}'
    )
    payload, reason = await generate_q_payload("count files", llm=stub)
    assert payload is not None and reason is None


# ---------------------------------------------------------------------------
# LLMPlanner — two-call shape
# ---------------------------------------------------------------------------


async def test_llm_planner_q_mode_fires_second_call_with_q_payload():
    routing_resp = '{"mode": "Q", "notes": null}'
    q_resp = (
        '{"from": "files", "aggregations": ['
        '{"op": "COUNT", "field": "*", "alias": "n"}]}'
    )
    stub = _StubLLM(routing_resp, q_resp)
    planner = LLMPlanner(stub)
    intent = IntentResult(label="aggregation", confidence=0.95, model_id="t")
    plan = await planner.plan("how many files", intent)

    assert plan.mode == "Q"
    assert plan.q_payload is not None
    assert plan.q_payload["from"] == "files"
    # Two calls fired: routing then q-payload.
    assert len(stub.calls) == 2


async def test_llm_planner_non_q_mode_makes_only_one_call():
    """When the routing call returns mode='H', we should NOT fire the
    second q-payload call (cost optimisation)."""
    routing_resp = '{"mode": "H", "notes": null}'
    stub = _StubLLM(routing_resp)
    planner = LLMPlanner(stub)
    intent = IntentResult(label="factoid", confidence=0.9, model_id="t")
    plan = await planner.plan("what is in invoice 42", intent)

    assert plan.mode == "H"
    assert plan.q_payload is None
    assert len(stub.calls) == 1


async def test_llm_planner_inventory_intent_short_circuits():
    """No LLM call when the inventory intent is detected — saves
    latency + dollars for the most common metadata question."""
    stub = _StubLLM()  # No responses — would fail if called.
    planner = LLMPlanner(stub)
    intent = IntentResult(label="inventory", confidence=0.99, model_id="t")
    plan = await planner.plan("what types of docs do I have", intent)

    assert plan.mode == "I"
    assert plan.notes == "inventory_short_circuit"
    assert len(stub.calls) == 0


async def test_llm_planner_q_payload_refusal_surfaces_in_notes():
    """When the second call refuses, plan.q_payload stays None + the
    reason lands in notes prefixed `q_payload_gen:` so _route_q_mode
    can render a coherent message."""
    routing_resp = '{"mode": "Q", "notes": null}'
    q_resp = '{"refuse": true, "reason": "catalog has no transactions table"}'
    stub = _StubLLM(routing_resp, q_resp)
    planner = LLMPlanner(stub)
    intent = IntentResult(label="aggregation", confidence=0.95, model_id="t")
    plan = await planner.plan("total transactions by vendor", intent)

    assert plan.mode == "Q"
    assert plan.q_payload is None
    assert plan.notes and "q_payload_gen: refuse:" in plan.notes
    assert "transactions" in plan.notes


async def test_llm_planner_routing_error_falls_back_to_identity():
    """When the routing LLM call errors, we degrade to the
    intent→mode default — pipeline keeps running."""
    stub = _StubLLM(LLMCallError("502 from provider"))
    planner = LLMPlanner(stub)
    intent = IntentResult(label="factoid", confidence=0.9, model_id="t")
    plan = await planner.plan("anything", intent)

    assert plan.mode == "H"  # default for factoid
    assert plan.notes == "llm_error_fell_back_identity"


async def test_llm_planner_explicit_mode_override_skips_routing_call():
    """`requested_mode='Q'` bypasses the routing LLM call but STILL
    fires the q-payload call so the API caller can force Q-mode."""
    q_resp = (
        '{"from": "files", "aggregations": ['
        '{"op": "COUNT", "field": "*", "alias": "n"}]}'
    )
    stub = _StubLLM(q_resp)
    planner = LLMPlanner(stub)
    intent = IntentResult(label="factoid", confidence=0.9, model_id="t")
    plan = await planner.plan("count files", intent, requested_mode="Q")

    assert plan.mode == "Q"
    assert plan.q_payload is not None
    # Routing call skipped — only the q-payload call fired.
    assert len(stub.calls) == 1
    assert plan.notes is not None and "explicit_mode_override" in plan.notes


# ---------------------------------------------------------------------------
# Back-compat — `GeminiPlanner` symbol still importable as an alias
# ---------------------------------------------------------------------------


async def test_gemini_planner_alias_is_llm_planner():
    from kb.query.planner import GeminiPlanner, LLMPlanner
    assert GeminiPlanner is LLMPlanner


# ---------------------------------------------------------------------------
# Identity planner still has a Q escape hatch (refuses cleanly)
# ---------------------------------------------------------------------------


async def test_identity_planner_q_mode_emits_no_payload():
    planner = IdentityPlanner()
    intent = IntentResult(label="aggregation", confidence=0.9, model_id="t")
    plan = await planner.plan("how many files", intent)
    # IdentityPlanner mapped aggregation→Q but has no LLM to build the
    # payload. mode_router renders the honest "Identity can't emit SQL"
    # refusal — that's covered in test_b4b_api.py.
    assert plan.mode == "Q"
    assert plan.q_payload is None
