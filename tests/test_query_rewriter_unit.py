"""Phase 8a — query rewriter unit tests.

RED at G3: imports `kb.query.rewriter` which lands at G4. Mocked LLM
client patterns mirror Phase 5a/6/7 unit tests for review consistency.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from typing import Any

import pytest

from kb.query.rewriter import (
    AnthropicQueryRewriter,
    GeminiQueryRewriter,
    IdentityQueryRewriter,
    Rewrites,
    _parse_rewrites,
    make_query_rewriter,
)


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


# ===========================================================================
# Rewrites model
# ===========================================================================


def test_rewrites_model_named_fields():
    r = Rewrites(original="q", step_back="sb", hyde="hy", query2doc="q2d")
    assert r.original == "q"
    assert r.step_back == "sb"
    assert r.hyde == "hy"
    assert r.query2doc == "q2d"


# ===========================================================================
# Identity fallback
# ===========================================================================


@pytest.mark.asyncio
async def test_identity_rewriter_returns_original_for_all_three():
    r = IdentityQueryRewriter()
    out = await r.rewrite("foundation issues")
    assert out.original == "foundation issues"
    assert out.step_back == "foundation issues"
    assert out.hyde == "foundation issues"
    assert out.query2doc == "foundation issues"


# ===========================================================================
# Parser edge cases (Decision #3)
# ===========================================================================


def test_parse_rewrites_handles_code_fence():
    raw = "```json\n" + json.dumps({
        "step_back": "sb", "hyde": "hy", "query2doc": "q2d",
    }) + "\n```"
    out = _parse_rewrites(raw, original="orig")
    assert out.original == "orig"
    assert out.step_back == "sb"
    assert out.hyde == "hy"
    assert out.query2doc == "q2d"


def test_parse_rewrites_handles_missing_key_fallback_to_original():
    raw = json.dumps({"step_back": "sb"})  # hyde + query2doc missing
    out = _parse_rewrites(raw, original="orig-query")
    assert out.step_back == "sb"
    assert out.hyde == "orig-query"
    assert out.query2doc == "orig-query"


def test_parse_rewrites_handles_invalid_json_fallback_to_original():
    out = _parse_rewrites("not valid json {{", original="orig")
    assert out.original == "orig"
    assert out.step_back == "orig"
    assert out.hyde == "orig"
    assert out.query2doc == "orig"


def test_parse_rewrites_handles_non_dict_top_level():
    out = _parse_rewrites('["array", "instead", "of", "dict"]', original="orig")
    assert out.step_back == "orig"
    assert out.hyde == "orig"
    assert out.query2doc == "orig"


def test_parse_rewrites_empty_string_falls_back_to_original():
    out = _parse_rewrites('{"step_back": "", "hyde": "", "query2doc": ""}', original="orig")
    # Empty-string truthy check in our parser falls back to original
    assert out.step_back == "orig"
    assert out.hyde == "orig"
    assert out.query2doc == "orig"


# ===========================================================================
# Factory matrix (Decision #2)
# ===========================================================================


def test_factory_selector_matrix():
    # auto + no keys → Identity
    with _env(KB_QUERY_LLM="auto", KB_GEMINI_API_KEY=None, KB_ANTHROPIC_API_KEY=None):
        assert isinstance(make_query_rewriter(), IdentityQueryRewriter)

    # auto + Gemini key → Gemini
    with _env(KB_QUERY_LLM="auto", KB_GEMINI_API_KEY="fake", KB_ANTHROPIC_API_KEY=None):
        assert isinstance(make_query_rewriter(), GeminiQueryRewriter)

    # auto + Anthropic key → Anthropic
    with _env(KB_QUERY_LLM="auto", KB_GEMINI_API_KEY=None, KB_ANTHROPIC_API_KEY="fake"):
        assert isinstance(make_query_rewriter(), AnthropicQueryRewriter)

    # explicit identity
    with _env(KB_QUERY_LLM="identity"):
        assert isinstance(make_query_rewriter(), IdentityQueryRewriter)

    # explicit gemini without key → loud fail
    with _env(KB_QUERY_LLM="gemini", KB_GEMINI_API_KEY=None):
        with pytest.raises((ValueError, KeyError), match="(KB_QUERY_LLM|KB_GEMINI_API_KEY)"):
            make_query_rewriter()

    # bogus → loud fail
    with _env(KB_QUERY_LLM="bogus"):
        with pytest.raises(ValueError, match="Unknown KB_QUERY_LLM"):
            make_query_rewriter()


def test_factory_honors_kb_query_model_env():
    with _env(KB_QUERY_LLM="gemini", KB_GEMINI_API_KEY="fake",
              KB_QUERY_MODEL="gemini-2.5-pro"):
        r = make_query_rewriter()
        assert isinstance(r, GeminiQueryRewriter)
        # Model env honored at construction
        assert r._model == "gemini-2.5-pro"


# ===========================================================================
# Mocked Gemini path — decisions #1, #4, #5, #6, #7
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
        self.usage_metadata = None


class _FakeModels:
    def __init__(self, raw_text: str, capture: dict):
        self._raw_text = raw_text
        self._capture = capture

    async def generate_content(self, **kwargs):
        self._capture.update(kwargs)
        return _FakeResponse(self._raw_text)


class _FakeAio:
    def __init__(self, raw_text: str, capture: dict):
        self.models = _FakeModels(raw_text, capture)


class _FakeClient:
    def __init__(self, raw_text: str):
        self.last_kwargs: dict[str, Any] = {}
        self.aio = _FakeAio(raw_text, self.last_kwargs)


@pytest.mark.asyncio
async def test_gemini_rewriter_parses_all_three_strategies():
    raw = json.dumps({
        "step_back": "What general topic does this query touch?",
        "hyde": "The document describes foundation cracks and structural integrity issues.",
        "query2doc": "foundation issues cracks structural building",
    })
    r = GeminiQueryRewriter(client=_FakeClient(raw))
    out = await r.rewrite("foundation issues")
    assert out.original == "foundation issues"
    assert "general topic" in out.step_back
    assert "foundation cracks" in out.hyde
    assert "structural" in out.query2doc


@pytest.mark.asyncio
async def test_gemini_rewriter_uses_system_prompt_template():
    """Decision #5: system prompt explains all 3 strategies."""
    raw = json.dumps({"step_back": "sb", "hyde": "hy", "query2doc": "q2d"})
    client = _FakeClient(raw)
    r = GeminiQueryRewriter(client=client)
    await r.rewrite("any query")
    # Inspect what was passed to generate_content
    config = client.last_kwargs.get("config")
    assert config is not None
    sysprompt = getattr(config, "system_instruction", "")
    assert "step_back" in sysprompt
    assert "hyde" in sysprompt.lower() or "ideal answer" in sysprompt.lower()
    assert "query2doc" in sysprompt


@pytest.mark.asyncio
async def test_gemini_rewriter_disables_thinking():
    """Decision #6: thinking_config.thinking_budget=0."""
    raw = json.dumps({"step_back": "sb", "hyde": "hy", "query2doc": "q2d"})
    client = _FakeClient(raw)
    r = GeminiQueryRewriter(client=client)
    await r.rewrite("q")
    config = client.last_kwargs.get("config")
    tc = getattr(config, "thinking_config", None)
    assert tc is not None
    budget = getattr(tc, "thinking_budget", None)
    assert budget == 0


@pytest.mark.asyncio
async def test_gemini_rewriter_api_error_returns_original_in_all_slots():
    """Decision #7: any exception → return original for all 3 (no crash)."""

    class _ErrorClient:
        class _Aio:
            class _Models:
                async def generate_content(self, **kwargs):
                    raise RuntimeError("API down")
            models = _Models()
        aio = _Aio()

    r = GeminiQueryRewriter(client=_ErrorClient())
    out = await r.rewrite("my-query")
    assert out.original == "my-query"
    assert out.step_back == "my-query"
    assert out.hyde == "my-query"
    assert out.query2doc == "my-query"


@pytest.mark.asyncio
async def test_gemini_rewriter_empty_candidates_returns_original():
    """Decision #7: empty candidates list (safety-block) → fallback to original."""

    class _EmptyResponse:
        candidates = []
        prompt_feedback = None

    class _EmptyClient:
        class _Aio:
            class _Models:
                async def generate_content(self, **kwargs):
                    return _EmptyResponse()
            models = _Models()
        aio = _Aio()

    r = GeminiQueryRewriter(client=_EmptyClient())
    out = await r.rewrite("blocked")
    assert out.step_back == "blocked"
    assert out.hyde == "blocked"
    assert out.query2doc == "blocked"
