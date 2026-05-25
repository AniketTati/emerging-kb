"""Phase 8d — CRAG relevance gate unit tests (no DB, no real LLM)."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from typing import Any

import pytest

from kb.query.crag import (
    CRAG_THRESHOLD,
    CragGate,
    GeminiCragGate,
    IdentityCragGate,
    _parse_score,
    make_crag_gate,
)
from kb.query.rrf import Hit


@contextmanager
def _env(**kwargs):
    prior = {k: os.environ.get(k) for k in kwargs}
    for k, v in kwargs.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in prior.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _hit(snippet: str) -> Hit:
    return Hit(id="x", kind="chunk", score=0.5, snippet=snippet,
               metadata={"file_id": "f1"})


# ===========================================================================
# Constants
# ===========================================================================


def test_crag_threshold_constant_equals_0_5():
    """Decision #2."""
    assert CRAG_THRESHOLD == 0.5


# ===========================================================================
# Parser (decision #4)
# ===========================================================================


def test_parse_score_valid_float():
    assert _parse_score('{"avg_relevance": 0.75}') == pytest.approx(0.75)
    assert _parse_score('{"avg_relevance": 0.0}') == 0.0
    assert _parse_score('{"avg_relevance": 1.0}') == 1.0


def test_parse_score_invalid_json_returns_default_1():
    """Decision #4 + #7: parse failure = safe (pass)."""
    assert _parse_score("not json") == 1.0


def test_parse_score_clamps_to_range():
    """Decision #4: clamp to [0, 1]."""
    assert _parse_score('{"avg_relevance": 1.5}') == 1.0
    assert _parse_score('{"avg_relevance": -0.3}') == 0.0


def test_parse_score_handles_code_fence():
    raw = "```json\n" + json.dumps({"avg_relevance": 0.6}) + "\n```"
    assert _parse_score(raw) == pytest.approx(0.6)


def test_parse_score_non_dict_returns_default_1():
    """Decision #4: top-level array → return default 1.0 (safe)."""
    assert _parse_score('[0.5]') == 1.0


def test_parse_score_missing_key_returns_default_1():
    assert _parse_score('{"other_key": 0.3}') == 1.0


def test_parse_score_non_numeric_value():
    """LLM might return a string instead of float."""
    # Implementation: try float() coercion; on TypeError, return 1.0.
    assert _parse_score('{"avg_relevance": "bad"}') == 1.0


# ===========================================================================
# Identity (decision #6)
# ===========================================================================


@pytest.mark.asyncio
async def test_identity_crag_always_returns_1():
    gate = IdentityCragGate()
    out = await gate.assess("q", [_hit("snippet a"), _hit("snippet b")])
    assert out == 1.0


@pytest.mark.asyncio
async def test_identity_crag_returns_1_for_empty_too():
    gate = IdentityCragGate()
    assert await gate.assess("q", []) == 1.0


# ===========================================================================
# Empty input (decision #5) — Gemini path
# ===========================================================================


@pytest.mark.asyncio
async def test_gemini_crag_returns_zero_for_empty_hits():
    """Decision #5: empty hits = guaranteed refusal (0.0)."""
    gate = GeminiCragGate(api_key="fake")
    out = await gate.assess("q", [])
    assert out == 0.0


# ===========================================================================
# Factory (decision #1)
# ===========================================================================


def test_factory_selector_matrix():
    # auto + no key → Identity
    with _env(KB_QUERY_LLM="auto", KB_GEMINI_API_KEY=None, KB_ANTHROPIC_API_KEY=None):
        assert isinstance(make_crag_gate(), IdentityCragGate)

    # auto + Gemini key → Gemini
    with _env(KB_QUERY_LLM="auto", KB_GEMINI_API_KEY="fake"):
        assert isinstance(make_crag_gate(), GeminiCragGate)

    # explicit identity
    with _env(KB_QUERY_LLM="identity"):
        assert isinstance(make_crag_gate(), IdentityCragGate)

    # explicit anthropic (Decision #10: Wave A maps Anthropic → Identity)
    with _env(KB_QUERY_LLM="anthropic", KB_ANTHROPIC_API_KEY="fake"):
        assert isinstance(make_crag_gate(), IdentityCragGate)

    # explicit gemini without key → ValueError-equivalent
    with _env(KB_QUERY_LLM="gemini", KB_GEMINI_API_KEY=None):
        with pytest.raises((ValueError, KeyError)):
            make_crag_gate()


# ===========================================================================
# Mocked Gemini path
# ===========================================================================


class _FakeResponse:
    def __init__(self, raw_text: str):
        self.candidates = [
            type("C", (), {
                "content": type("Ct", (), {
                    "parts": [type("P", (), {"text": raw_text})]
                })
            })
        ]


class _FakeModels:
    def __init__(self, raw_text: str, capture: dict):
        self._raw = raw_text
        self._cap = capture

    async def generate_content(self, **kwargs):
        self._cap.update(kwargs)
        return _FakeResponse(self._raw)


class _FakeAio:
    def __init__(self, raw_text: str, capture: dict):
        self.models = _FakeModels(raw_text, capture)


class _FakeClient:
    def __init__(self, raw_text: str):
        self.last_kwargs: dict[str, Any] = {}
        self.aio = _FakeAio(raw_text, self.last_kwargs)


@pytest.mark.asyncio
async def test_gemini_crag_returns_parsed_score():
    raw = json.dumps({"avg_relevance": 0.42})
    gate = GeminiCragGate(client=_FakeClient(raw))
    out = await gate.assess("q", [_hit("s1"), _hit("s2"), _hit("s3")])
    assert out == pytest.approx(0.42)


@pytest.mark.asyncio
async def test_gemini_crag_disables_thinking():
    """Decision #8."""
    raw = json.dumps({"avg_relevance": 0.7})
    client = _FakeClient(raw)
    gate = GeminiCragGate(client=client)
    await gate.assess("q", [_hit("s1")])
    config = client.last_kwargs.get("config")
    assert config is not None
    tc = getattr(config, "thinking_config", None)
    assert tc is not None
    budget = getattr(tc, "thinking_budget", None)
    assert budget == 0


@pytest.mark.asyncio
async def test_gemini_crag_api_error_returns_1():
    """Decision #7: error → return 1.0 (don't block on infra failure)."""

    class _ErrorClient:
        class _Aio:
            class _Models:
                async def generate_content(self, **kwargs):
                    raise RuntimeError("API down")
            models = _Models()
        aio = _Aio()

    gate = GeminiCragGate(client=_ErrorClient())
    out = await gate.assess("q", [_hit("s1")])
    assert out == 1.0


@pytest.mark.asyncio
async def test_gemini_crag_uses_only_top_3_snippets():
    """Decision #3: only top-3 fed to LLM."""
    raw = json.dumps({"avg_relevance": 0.7})
    client = _FakeClient(raw)
    gate = GeminiCragGate(client=client)
    # 10 hits — only 3 should appear in prompt
    hits = [_hit(f"snippet-{i}") for i in range(10)]
    await gate.assess("q", hits)
    contents = client.last_kwargs.get("contents", "")
    # Snippets 0, 1, 2 should appear; 5+ should NOT
    assert "snippet-0" in contents
    assert "snippet-2" in contents
    assert "snippet-5" not in contents
    assert "snippet-9" not in contents
