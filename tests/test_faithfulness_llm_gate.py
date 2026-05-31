"""Q5 / D6 — LLMFaithfulnessGate (claim-decomposition + per-claim entailment).

Replaces the Jaccard heuristic's weak token-overlap signal (which scored a
correct '9.4%' answer at 0.15, on the refuse cliff) with a real per-claim
entailment judge on a lite LLM. Tested with an injected fake JsonLLMClient
so it stays deterministic + offline.
"""

from __future__ import annotations

import pytest

from kb.query.faithfulness import (
    LLMFaithfulnessGate,
    _parse_faithfulness_verdicts,
)
from kb.query.llm_client import LLMCallError

pytestmark = pytest.mark.asyncio


class _FakeLLM:
    """Returns a canned JSON string; records the prompt it was given."""

    model_id = "gemini-2.5-flash-lite"

    def __init__(self, out: str, *, raises: Exception | None = None) -> None:
        self._out = out
        self._raises = raises
        self.last_user: str | None = None
        self.calls = 0

    async def generate_json(self, *, user: str, system: str, max_tokens: int = 800) -> str:
        self.calls += 1
        self.last_user = user
        if self._raises is not None:
            raise self._raises
        return self._out


# ----- parse helper (pure) -----


def test_parse_aligns_by_claim_index():
    raw = '{"verdicts":[{"claim":1,"supported":true},{"claim":2,"supported":false}]}'
    assert _parse_faithfulness_verdicts(raw, 2) == [1.0, 0.0]


def test_parse_positional_fallback_when_index_missing():
    raw = '{"verdicts":[{"supported":true},{"supported":false}]}'
    assert _parse_faithfulness_verdicts(raw, 2) == [1.0, 0.0]


def test_parse_omitted_claim_defaults_supported():
    # Judge only returned claim 1 → claim 2 defaults to supported (fail-safe).
    raw = '{"verdicts":[{"claim":1,"supported":false}]}'
    assert _parse_faithfulness_verdicts(raw, 2) == [0.0, 1.0]


def test_parse_strips_code_fence():
    raw = '```json\n{"verdicts":[{"claim":1,"supported":true}]}\n```'
    assert _parse_faithfulness_verdicts(raw, 1) == [1.0]


def test_parse_bare_list_accepted():
    raw = '[{"claim":1,"supported":true}]'
    assert _parse_faithfulness_verdicts(raw, 1) == [1.0]


def test_parse_unusable_returns_none():
    assert _parse_faithfulness_verdicts("not json", 2) is None
    assert _parse_faithfulness_verdicts('{"verdicts":[]}', 2) is None
    assert _parse_faithfulness_verdicts('{"other":1}', 2) is None


# ----- gate behaviour -----


async def test_grounded_answer_passes():
    llm = _FakeLLM('{"verdicts":[{"claim":1,"supported":true}]}')
    gate = LLMFaithfulnessGate(llm)
    r = await gate.assess(
        "The loan interest rate is 9.4%.",
        ["Amendment 2 revises the interest rate to 9.4% per annum."],
    )
    assert r.verdict == "pass"
    assert r.score == 1.0
    assert r.model_id == "llm-faithfulness:gemini-2.5-flash-lite"
    # the snippet + claim must reach the judge prompt
    assert "9.4%" in (llm.last_user or "")


async def test_hallucinated_answer_refused():
    llm = _FakeLLM('{"verdicts":[{"claim":1,"supported":false}]}')
    gate = LLMFaithfulnessGate(llm)
    r = await gate.assess(
        "The loan interest rate is 5.0%.",
        ["Amendment 2 revises the interest rate to 9.4% per annum."],
    )
    assert r.verdict == "refused"
    assert r.score == 0.0


async def test_mixed_claims_land_in_low_confidence_band():
    # 1 of 2 supported → 0.5 → low_confidence (>= 0.50, < 0.80).
    llm = _FakeLLM(
        '{"verdicts":[{"claim":1,"supported":true},{"claim":2,"supported":false}]}'
    )
    gate = LLMFaithfulnessGate(llm)
    r = await gate.assess(
        "The rate is 9.4%. It was set in 2019.",
        ["Amendment 2 revises the interest rate to 9.4% per annum."],
    )
    assert r.score == 0.5
    assert r.verdict == "low_confidence"
    assert r.per_claim_scores == (1.0, 0.0)


async def test_no_snippets_refused_without_calling_llm():
    llm = _FakeLLM("unused")
    gate = LLMFaithfulnessGate(llm)
    r = await gate.assess("The rate is 9.4%.", [])
    assert r.verdict == "refused"
    assert llm.calls == 0  # short-circuits before the judge call


async def test_empty_answer_skipped():
    llm = _FakeLLM("unused")
    gate = LLMFaithfulnessGate(llm)
    r = await gate.assess("   ", ["some snippet"])
    assert r.verdict == "skipped"
    assert llm.calls == 0


async def test_llm_error_fail_safe_passes():
    llm = _FakeLLM("", raises=LLMCallError("boom"))
    gate = LLMFaithfulnessGate(llm)
    r = await gate.assess("The rate is 9.4%.", ["grounding snippet"])
    assert r.verdict == "pass"
    assert "unavailable" in (r.notes or "")


async def test_unparseable_verdicts_fail_safe_passes():
    llm = _FakeLLM("totally not json")
    gate = LLMFaithfulnessGate(llm)
    r = await gate.assess("The rate is 9.4%.", ["grounding snippet"])
    assert r.verdict == "pass"
    assert "unparseable" in (r.notes or "")
