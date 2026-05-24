"""Phase 3d — Summarizer adapter unit tests (no DB, no real LLM API).

RED at G3: imports `kb.summarization.{GeminiSummarizer, IdentitySummarizer,
make_summarizer, SummarizationError, Summary}` which don't exist yet — land
at G4 alongside the factory selector matrix.

Mock client mirrors `_MockGeminiClient` from `test_contextualization_gemini_unit.py`
(3b-bis), adapted to return text-only summaries (no images).

Spec: tests/specs/phase_3d.md §3 (decisions #5, #6, #7).
"""

from __future__ import annotations

import os
from contextlib import contextmanager

import pytest


pytestmark = pytest.mark.asyncio


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


def _build_mock_summary_response(text: str, *, prompt_tokens: int = 500, candidates_tokens: int = 250):
    part = type("Part", (), {"text": text})()
    content = type("Content", (), {"parts": [part], "role": "model"})()
    candidate = type("Candidate", (), {"content": content, "finish_reason": "STOP"})()
    usage = type("UsageMetadata", (), {
        "prompt_token_count": prompt_tokens,
        "candidates_token_count": candidates_tokens,
        "total_token_count": prompt_tokens + candidates_tokens,
    })()
    return type("GenerateContentResponse", (), {
        "candidates": [candidate],
        "usage_metadata": usage,
        "prompt_feedback": None,
        "text": text,
    })()


class _MockGeminiClient:
    def __init__(
        self,
        *,
        response_text: str = "Summary text.",
        prompt_tokens: int = 500,
        candidates_tokens: int = 250,
        raise_exc: Exception | None = None,
    ) -> None:
        self.last_kwargs: dict | None = None
        self._response_text = response_text
        self._prompt_tokens = prompt_tokens
        self._candidates_tokens = candidates_tokens
        self._raise_exc = raise_exc

        client_self = self

        class _Models:
            async def generate_content(self, **kwargs):
                client_self.last_kwargs = kwargs
                if client_self._raise_exc:
                    raise client_self._raise_exc
                return _build_mock_summary_response(
                    client_self._response_text,
                    prompt_tokens=client_self._prompt_tokens,
                    candidates_tokens=client_self._candidates_tokens,
                )

        self.aio = type("Aio", (), {"models": _Models()})()


# ===========================================================================
# §5.10 decision #7 — prompt shape (RAPTOR cookbook adaptation) + max_output_tokens
# ===========================================================================


async def test_gemini_summarizer_sends_chunks_with_correct_prompt():
    """The N input chunks must reach Gemini concatenated with their
    boundaries marked; the instruction prompt names the 200-400 token
    target; max_output_tokens=600 is enforced."""
    from kb.summarization import GeminiSummarizer

    mock = _MockGeminiClient()
    summarizer = GeminiSummarizer(client=mock, api_key="fake")

    chunks = [
        "Chunk one. Discusses revenue.",
        "Chunk two. Discusses headcount.",
        "Chunk three. Discusses risks.",
    ]
    await summarizer.summarize(texts=chunks)

    kwargs = mock.last_kwargs
    assert kwargs is not None

    # All three chunks appear somewhere in the request.
    serialized = repr(kwargs.get("contents", "")) + repr(kwargs.get("config", ""))
    for chunk in chunks:
        assert chunk in serialized, f"chunk text missing from request: {chunk}"

    # max_output_tokens=600 lands in the config.
    config = kwargs.get("config")
    max_out = getattr(config, "max_output_tokens", None) or (
        config.get("max_output_tokens") if isinstance(config, dict) else None
    )
    assert max_out == 600


async def test_gemini_summarizer_disables_thinking():
    """thinking_budget=0 must be set in GenerateContentConfig — same reasoning
    as 3b-bis #7: summarization is a bounded rewriting task, not a reasoning
    task. Burning thinking tokens for 200-400 token outputs is wasteful."""
    from kb.summarization import GeminiSummarizer

    mock = _MockGeminiClient()
    summarizer = GeminiSummarizer(client=mock, api_key="fake")

    await summarizer.summarize(texts=["chunk a", "chunk b"])

    config = mock.last_kwargs.get("config")
    # Don't use `or` on thinking_budget — 0 is the value, and 0 is falsy.
    if hasattr(config, "thinking_config"):
        thinking = config.thinking_config
    elif isinstance(config, dict):
        thinking = config.get("thinking_config")
    else:
        thinking = None
    assert thinking is not None

    if hasattr(thinking, "thinking_budget"):
        budget = thinking.thinking_budget
    elif isinstance(thinking, dict):
        budget = thinking.get("thinking_budget")
    else:
        budget = None
    assert budget == 0


# ===========================================================================
# §5.10 decision #6 — model literal + KB_SUMMARIZER_MODEL override
# ===========================================================================


async def test_gemini_summarizer_uses_configurable_model():
    """Default `gemini-2.5-flash`; KB_SUMMARIZER_MODEL overrides."""
    from kb.summarization import GeminiSummarizer

    mock = _MockGeminiClient()

    with _env(KB_SUMMARIZER_MODEL=None):
        summarizer = GeminiSummarizer(client=mock, api_key="fake")
        result = await summarizer.summarize(texts=["c1", "c2"])
        assert mock.last_kwargs["model"] == "gemini-2.5-flash"
        assert result.model_id == "gemini-2.5-flash"

    mock2 = _MockGeminiClient()
    with _env(KB_SUMMARIZER_MODEL="gemini-2.5-pro"):
        summarizer = GeminiSummarizer(client=mock2, api_key="fake")
        result = await summarizer.summarize(texts=["c1", "c2"])
        assert mock2.last_kwargs["model"] == "gemini-2.5-pro"
        assert result.model_id == "gemini-2.5-pro"


# ===========================================================================
# §5.10 decision #5 — Identity Summarizer (sharpened framing: smoke path only)
# ===========================================================================


async def test_identity_summarizer_concatenates_input_texts():
    """Identity is the no-key smoke path. Concatenates input chunks with
    `\\n\\n---\\n\\n` separator + truncates to ~600 tokens-equivalent
    (~2400 chars at 4 chars/tok rough). model_id='identity' so dashboards
    can alarm on it in production."""
    from kb.summarization import IdentitySummarizer

    summarizer = IdentitySummarizer()
    result = await summarizer.summarize(
        texts=["First chunk text.", "Second chunk text."],
    )

    assert "First chunk text." in result.text
    assert "Second chunk text." in result.text
    assert "---" in result.text  # separator
    assert result.model_id == "identity"
    assert result.input_token_count == 0  # no LLM call → no token accounting
    assert result.output_token_count == 0


# ===========================================================================
# §5.10 decision #5 — factory selector matrix
# ===========================================================================


async def test_summarizer_factory_selector_matrix():
    """`KB_SUMMARIZER ∈ {gemini, anthropic, identity, auto}`. `auto` probes
    KB_GEMINI_API_KEY → KB_ANTHROPIC_API_KEY → Identity. Mirrors 3b-bis's
    contextualizer factory exactly."""
    from kb.summarization import (
        AnthropicSummarizer,
        GeminiSummarizer,
        IdentitySummarizer,
        make_summarizer,
    )

    # auto + no keys → Identity
    with _env(KB_SUMMARIZER=None, KB_GEMINI_API_KEY=None, KB_ANTHROPIC_API_KEY=None):
        assert isinstance(make_summarizer(), IdentitySummarizer)

    # auto + Gemini-only → Gemini (Gemini-first probe)
    with _env(KB_SUMMARIZER=None, KB_GEMINI_API_KEY="fake-g", KB_ANTHROPIC_API_KEY=None):
        assert isinstance(make_summarizer(), GeminiSummarizer)

    # auto + Anthropic-only → Anthropic
    with _env(KB_SUMMARIZER=None, KB_GEMINI_API_KEY=None, KB_ANTHROPIC_API_KEY="fake-a"):
        assert isinstance(make_summarizer(), AnthropicSummarizer)

    # auto + both → Gemini wins (Gemini-first)
    with _env(KB_SUMMARIZER=None, KB_GEMINI_API_KEY="fake-g", KB_ANTHROPIC_API_KEY="fake-a"):
        assert isinstance(make_summarizer(), GeminiSummarizer)

    # explicit identity → Identity (ignores keys)
    with _env(KB_SUMMARIZER="identity", KB_GEMINI_API_KEY="fake-g"):
        assert isinstance(make_summarizer(), IdentitySummarizer)

    # explicit gemini without key → ValueError (loud-fail-on-opt-in)
    with _env(KB_SUMMARIZER="gemini", KB_GEMINI_API_KEY=None, KB_ANTHROPIC_API_KEY=None):
        with pytest.raises(ValueError):
            make_summarizer()

    # unknown selector → ValueError
    with _env(KB_SUMMARIZER="bogus", KB_GEMINI_API_KEY=None, KB_ANTHROPIC_API_KEY=None):
        with pytest.raises(ValueError):
            make_summarizer()


# ===========================================================================
# Error path — Gemini API exception surfaces as SummarizationError
# ===========================================================================


async def test_gemini_summarizer_api_error_raises_summarization_error():
    """Underlying google-genai exceptions surface as SummarizationError so
    the worker's raptor_building→failed transition fires cleanly."""
    from kb.summarization import GeminiSummarizer, SummarizationError

    mock = _MockGeminiClient(raise_exc=RuntimeError("gemini 500 internal"))
    summarizer = GeminiSummarizer(client=mock, api_key="fake")

    with pytest.raises(SummarizationError):
        await summarizer.summarize(texts=["chunk a", "chunk b"])
