"""Phase 8e — Astute generation unit tests (no DB, no real LLM)."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from typing import Any

import pytest

from kb.query.generate import (
    Citation,
    GenerationResult,
    GeminiGenerator,
    Generator,
    IdentityGenerator,
    _build_user_prompt,
    _parse_result,
    make_generator,
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


def _hit(snippet: str, hit_id: str = "h1", kind: str = "chunk", score: float = 0.5) -> Hit:
    return Hit(id=hit_id, kind=kind, score=score, snippet=snippet,
               metadata={"file_id": "f1"})


# ===========================================================================
# Pydantic shapes (decision #4)
# ===========================================================================


def test_generation_result_pydantic_shape():
    r = GenerationResult(
        answer="Hello.",
        citations=[],
        refused=False,
        refusal_reason=None,
        model_id="identity",
    )
    assert r.answer == "Hello."
    assert r.citations == []
    assert r.refused is False
    assert r.refusal_reason is None
    assert r.model_id == "identity"


def test_citation_pydantic_shape():
    c = Citation(
        hit_id="abc",
        kind="chunk",
        file_id="f1",
        snippet_preview="some text",
        score=0.42,
    )
    assert c.hit_id == "abc"
    assert c.kind == "chunk"
    assert c.file_id == "f1"
    assert c.snippet_preview == "some text"
    assert c.score == pytest.approx(0.42)


# ===========================================================================
# Identity generator (decision #13)
# ===========================================================================


@pytest.mark.asyncio
async def test_identity_generator_returns_templated_echo():
    gen = IdentityGenerator()
    out = await gen.generate("What is X?", [_hit("snippet a", hit_id="a1"),
                                              _hit("snippet b", hit_id="a2"),
                                              _hit("snippet c", hit_id="a3")])
    assert out.refused is False
    assert "[identity-stub]" in out.answer
    assert "What is X?" in out.answer
    assert "hits: 3" in out.answer
    assert len(out.citations) == 3
    assert out.citations[0].hit_id == "a1"
    assert out.model_id == "identity"


@pytest.mark.asyncio
async def test_identity_generator_with_empty_hits_returns_refusal():
    gen = IdentityGenerator()
    out = await gen.generate("q", [])
    assert out.refused is True
    assert out.refusal_reason == "no_hits"
    assert out.citations == []


@pytest.mark.asyncio
async def test_identity_generator_force_refuse_returns_refusal():
    gen = IdentityGenerator()
    out = await gen.generate("q", [_hit("s")], force_refuse=True)
    assert out.refused is True
    assert out.refusal_reason == "insufficient_evidence"


# ===========================================================================
# Parser fail-safes (decision #9)
# ===========================================================================


def test_parse_result_bad_json_returns_parse_error_refusal():
    out = _parse_result("not json", hits=[_hit("s")], model_id="gemini-2.5-flash")
    assert out.refused is True
    assert out.refusal_reason == "parse_error"
    assert out.citations == []
    assert out.model_id == "gemini-2.5-flash"


def test_parse_result_missing_answer_field_returns_refusal():
    out = _parse_result(json.dumps({"foo": "bar"}), hits=[_hit("s")], model_id="m")
    assert out.refused is True
    assert out.refusal_reason == "parse_error"


def test_parse_result_strips_code_fence():
    raw = "```json\n" + json.dumps({"answer": "Yes.", "citations": []}) + "\n```"
    out = _parse_result(raw, hits=[_hit("s")], model_id="m")
    assert out.refused is False
    assert out.answer == "Yes."


def test_parse_result_respects_llm_refusal():
    raw = json.dumps({
        "refused": True,
        "refusal_reason": "evidence_insufficient_per_model",
        "answer": "",
        "citations": [],
    })
    out = _parse_result(raw, hits=[_hit("s")], model_id="m")
    assert out.refused is True
    assert out.refusal_reason == "evidence_insufficient_per_model"


# ---------------------------------------------------------------------------
# Truncation + prose-wrapped recovery — added after a real chat session
# hit MAX_TOKENS on a compound query and the parser returned a generic
# `parse_error`, hiding the real cause.
# ---------------------------------------------------------------------------


def test_parse_result_max_tokens_finish_reason_emits_truncated():
    """When Gemini's finish_reason=MAX_TOKENS, the refusal reason
    bucket is 'truncated' — distinct + actionable (bump
    _MAX_OUTPUT_TOKENS or shorten the prompt) instead of the generic
    'parse_error' that hides what actually went wrong."""
    # Truncated mid-string: opens an answer field but never closes it.
    raw = '{"answer": "NorthWind Capital LLC is a New York'
    out = _parse_result(
        raw, hits=[_hit("s")], model_id="m",
        finish_reason="MAX_TOKENS",
    )
    assert out.refused is True
    assert out.refusal_reason == "truncated"


def test_parse_result_recovers_json_wrapped_in_prose():
    """Gemini sometimes prefixes its JSON with prose ('Sure, here's
    the JSON: { ... }'). The brace-balanced extractor peels the JSON
    out so the answer surfaces instead of refusing."""
    raw = (
        "Sure, here you go:\n"
        + json.dumps({"answer": "All good.", "citations": []})
        + "\nHope that helps!"
    )
    out = _parse_result(raw, hits=[_hit("s")], model_id="m")
    assert out.refused is False
    assert out.answer == "All good."


def test_parse_result_recovers_when_json_loads_fails_but_block_extracts():
    """Trailing comma → json.loads fails → brace extractor finds
    a clean {...} block earlier in the buffer. Note: extractor only
    helps when the EARLY block is valid; this test pins the prose-
    wrapper recovery path specifically."""
    valid = json.dumps({"answer": "Yes.", "citations": []})
    raw = "Here's the JSON: " + valid + ". Let me know if you need more."
    out = _parse_result(raw, hits=[_hit("s")], model_id="m")
    assert out.refused is False
    assert out.answer == "Yes."


def test_parse_result_truly_unrecoverable_still_returns_parse_error():
    """If there's no balanced {...} block anywhere, refusal_reason is
    plain 'parse_error' (without a MAX_TOKENS hint)."""
    out = _parse_result("definitely not json at all",
                        hits=[_hit("s")], model_id="m")
    assert out.refused is True
    assert out.refusal_reason == "parse_error"


# ===========================================================================
# Factory (decision #1 + #14)
# ===========================================================================


def test_factory_selector_matrix():
    # auto + no key → Identity
    with _env(KB_QUERY_LLM="auto", KB_GEMINI_API_KEY=None, KB_ANTHROPIC_API_KEY=None):
        assert isinstance(make_generator(), IdentityGenerator)

    # auto + Gemini key → Gemini
    with _env(KB_QUERY_LLM="auto", KB_GEMINI_API_KEY="fake"):
        assert isinstance(make_generator(), GeminiGenerator)

    # explicit identity
    with _env(KB_QUERY_LLM="identity"):
        assert isinstance(make_generator(), IdentityGenerator)

    # explicit anthropic → Identity (decision #14, Wave A defer)
    with _env(KB_QUERY_LLM="anthropic", KB_ANTHROPIC_API_KEY="fake"):
        assert isinstance(make_generator(), IdentityGenerator)

    # explicit gemini without key → ValueError
    with _env(KB_QUERY_LLM="gemini", KB_GEMINI_API_KEY=None):
        with pytest.raises((ValueError, KeyError)):
            make_generator()


# ===========================================================================
# Gemini path — mocked
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
async def test_gemini_generator_returns_inline_citation_markers():
    raw = json.dumps({
        "answer": "Foo [h1] bar [h2].",
        "citations": [
            {"hit_id": "h1", "kind": "chunk", "file_id": "f1",
             "snippet_preview": "snip a", "score": 0.9},
            {"hit_id": "h2", "kind": "chunk", "file_id": "f1",
             "snippet_preview": "snip b", "score": 0.8},
        ],
    })
    gen = GeminiGenerator(client=_FakeClient(raw))
    hits = [_hit("snip a", hit_id="h1"), _hit("snip b", hit_id="h2")]
    out = await gen.generate("q", hits)
    assert out.refused is False
    assert "[h1]" in out.answer
    assert len(out.citations) == 2
    assert out.citations[0].hit_id == "h1"


@pytest.mark.asyncio
async def test_gemini_generator_passes_top_10_hits():
    """Decision #2: top-10 hits only."""
    raw = json.dumps({"answer": "ok", "citations": []})
    client = _FakeClient(raw)
    gen = GeminiGenerator(client=client)
    # 12 hits — only 10 should appear in prompt
    hits = [_hit(f"unique-snippet-{i}", hit_id=f"h{i}") for i in range(12)]
    await gen.generate("q", hits)
    contents = client.last_kwargs.get("contents", "")
    assert "unique-snippet-0" in contents
    assert "unique-snippet-9" in contents
    assert "unique-snippet-10" not in contents
    assert "unique-snippet-11" not in contents


@pytest.mark.asyncio
async def test_gemini_generator_disables_thinking():
    """Decision #12."""
    raw = json.dumps({"answer": "ok", "citations": []})
    client = _FakeClient(raw)
    gen = GeminiGenerator(client=client)
    await gen.generate("q", [_hit("s")])
    config = client.last_kwargs.get("config")
    assert config is not None
    tc = getattr(config, "thinking_config", None)
    assert tc is not None
    budget = getattr(tc, "thinking_budget", None)
    assert budget == 0


@pytest.mark.asyncio
async def test_gemini_generator_uses_system_instruction_for_astute_prompt():
    """Decision #15."""
    raw = json.dumps({"answer": "ok", "citations": []})
    client = _FakeClient(raw)
    gen = GeminiGenerator(client=client)
    await gen.generate("q", [_hit("s")])
    config = client.last_kwargs.get("config")
    si = getattr(config, "system_instruction", None) or ""
    assert "cite" in si.lower() or "citation" in si.lower()
    assert "refuse" in si.lower() or "refusal" in si.lower()


@pytest.mark.asyncio
async def test_gemini_generator_respects_llm_refusal():
    """Decision #8: model can self-refuse."""
    raw = json.dumps({
        "answer": "",
        "citations": [],
        "refused": True,
        "refusal_reason": "evidence_does_not_support_query",
    })
    gen = GeminiGenerator(client=_FakeClient(raw))
    out = await gen.generate("q", [_hit("s")])
    assert out.refused is True
    assert out.refusal_reason == "evidence_does_not_support_query"


@pytest.mark.asyncio
async def test_gemini_generator_llm_exception_returns_llm_error_refusal():
    """Decision #10: error → refusal (NOT silent pass)."""

    class _ErrorClient:
        class _Aio:
            class _Models:
                async def generate_content(self, **kwargs):
                    raise RuntimeError("API down")
            models = _Models()
        aio = _Aio()

    gen = GeminiGenerator(client=_ErrorClient())
    out = await gen.generate("q", [_hit("s")])
    assert out.refused is True
    assert out.refusal_reason == "llm_error"


@pytest.mark.asyncio
async def test_force_refuse_skips_llm_returns_refusal_envelope():
    """Decision #6: orchestrator passes force_refuse=True when CRAG < threshold."""
    raw = json.dumps({"answer": "should not be called", "citations": []})
    client = _FakeClient(raw)
    gen = GeminiGenerator(client=client)
    out = await gen.generate("q", [_hit("s")], force_refuse=True)
    assert out.refused is True
    assert out.refusal_reason == "insufficient_evidence"
    # LLM should not have been called
    assert client.last_kwargs == {}


@pytest.mark.asyncio
async def test_empty_hits_skips_llm_returns_no_hits_refusal():
    """Decision #7."""
    raw = json.dumps({"answer": "should not be called", "citations": []})
    client = _FakeClient(raw)
    gen = GeminiGenerator(client=client)
    out = await gen.generate("q", [])
    assert out.refused is True
    assert out.refusal_reason == "no_hits"
    assert client.last_kwargs == {}


# ===========================================================================
# Prompt builder
# ===========================================================================


def test_build_user_prompt_includes_hit_ids_and_snippets():
    hits = [_hit("alpha", hit_id="x1"), _hit("beta", hit_id="x2")]
    prompt = _build_user_prompt("query?", hits)
    assert "query?" in prompt
    assert "x1" in prompt
    assert "alpha" in prompt
    assert "x2" in prompt
    assert "beta" in prompt
