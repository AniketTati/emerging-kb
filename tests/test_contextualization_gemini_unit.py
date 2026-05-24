"""Phase 3b-bis — GeminiContextualizer adapter unit tests (no DB, no real API).

RED at G3: imports `kb.contextualization.GeminiContextualizer` which doesn't
exist yet — lands at G4 alongside a widened `make_contextualizer()` factory.

Spec: tests/specs/phase_3b_bis.md §3.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from unittest.mock import AsyncMock

import pytest


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Test infrastructure — mirrors _MockAnthropicClient in
# test_contextualization_unit.py so the two adapters can be reviewed side-by-side.
# ---------------------------------------------------------------------------


@contextmanager
def _env(**kwargs):
    """Temporarily set environment variables; restore prior values on exit."""
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


def _build_mock_gemini_response(
    text: str,
    *,
    prompt_tokens: int = 100,
    candidates_tokens: int = 50,
    prompt_feedback: object | None = None,
):
    """Return an object shaped like google.genai.types.GenerateContentResponse."""
    part = type("Part", (), {"text": text})()
    content = type("Content", (), {"parts": [part], "role": "model"})()
    candidate = type("Candidate", (), {"content": content, "finish_reason": "STOP"})()
    usage = type("UsageMetadata", (), {
        "prompt_token_count": prompt_tokens,
        "candidates_token_count": candidates_tokens,
        "total_token_count": prompt_tokens + candidates_tokens,
    })()
    response = type("GenerateContentResponse", (), {
        "candidates": [candidate],
        "usage_metadata": usage,
        "prompt_feedback": prompt_feedback,
        "text": text,  # google-genai exposes a top-level .text convenience
    })()
    return response


class _MockGeminiClient:
    """Mimics google.genai.Client for unit tests. Records the kwargs of every
    aio.models.generate_content() call so tests can assert on request shape.

    Same surface as `_MockAnthropicClient` in test_contextualization_unit.py
    but shaped to the google-genai SDK's request/response surface.
    """

    def __init__(
        self,
        *,
        response_text: str = "Test prefix.",
        prompt_tokens: int = 100,
        candidates_tokens: int = 50,
        prompt_feedback: object | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self.last_kwargs: dict | None = None
        self._response_text = response_text
        self._prompt_tokens = prompt_tokens
        self._candidates_tokens = candidates_tokens
        self._prompt_feedback = prompt_feedback
        self._raise_exc = raise_exc

        client_self = self

        class _Models:
            async def generate_content(self, **kwargs):
                client_self.last_kwargs = kwargs
                if client_self._raise_exc:
                    raise client_self._raise_exc
                return _build_mock_gemini_response(
                    client_self._response_text,
                    prompt_tokens=client_self._prompt_tokens,
                    candidates_tokens=client_self._candidates_tokens,
                    prompt_feedback=client_self._prompt_feedback,
                )

        self.aio = type("Aio", (), {"models": _Models()})()


# ===========================================================================
# §5.8.1 decision #3 — prompt template (verbatim from §5.8 #7)
# ===========================================================================


async def test_gemini_contextualizer_sends_doc_as_system_instruction():
    """Doc text rides in the system_instruction config; chunk in user content."""
    from kb.contextualization import GeminiContextualizer

    mock_client = _MockGeminiClient()
    contextualizer = GeminiContextualizer(client=mock_client, api_key="fake")

    await contextualizer.contextualize(
        doc_text="ACME Corp 2024 10-K financial report.",
        chunk_text="Q3 revenue grew 12%.",
    )

    kwargs = mock_client.last_kwargs
    assert kwargs is not None
    # google-genai uses a `config` kwarg (GenerateContentConfig) which carries
    # system_instruction + generation params. The doc context must land there.
    config = kwargs.get("config")
    assert config is not None, "expected config kwarg with system_instruction"
    system_text = getattr(config, "system_instruction", None) or (
        config.get("system_instruction") if isinstance(config, dict) else None
    )
    assert system_text is not None
    assert "ACME Corp 2024 10-K" in str(system_text)
    # max_output_tokens=200 per decision #6 (covered here to avoid a
    # standalone shape-only test).
    max_out = getattr(config, "max_output_tokens", None) or (
        config.get("max_output_tokens") if isinstance(config, dict) else None
    )
    assert max_out == 200


async def test_gemini_contextualizer_sends_chunk_in_user_content():
    """Chunk text rides in the contents arg (user-role payload)."""
    from kb.contextualization import GeminiContextualizer

    mock_client = _MockGeminiClient()
    contextualizer = GeminiContextualizer(client=mock_client, api_key="fake")

    await contextualizer.contextualize(
        doc_text="Doc context here.",
        chunk_text="Q3 revenue grew 12%.",
    )

    kwargs = mock_client.last_kwargs
    contents = kwargs.get("contents")
    assert contents is not None
    # google-genai accepts contents as a string OR a list of Content/dict.
    if isinstance(contents, str):
        combined = contents
    else:
        # Walk the structure to collect any text payloads.
        combined_parts: list[str] = []
        for item in contents if isinstance(contents, list) else [contents]:
            if isinstance(item, str):
                combined_parts.append(item)
            elif hasattr(item, "parts"):
                for p in item.parts:
                    combined_parts.append(getattr(p, "text", "") or "")
            elif isinstance(item, dict):
                for p in item.get("parts", []):
                    combined_parts.append(p.get("text", ""))
        combined = " ".join(combined_parts)
    assert "Q3 revenue grew 12%" in combined


# ===========================================================================
# §5.8.1 decision #7 — thinking disabled
# ===========================================================================


async def test_gemini_contextualizer_disables_thinking():
    """thinking_budget=0 must be set in the GenerateContentConfig."""
    from kb.contextualization import GeminiContextualizer

    mock_client = _MockGeminiClient()
    contextualizer = GeminiContextualizer(client=mock_client, api_key="fake")

    await contextualizer.contextualize(doc_text="doc", chunk_text="chunk")

    config = mock_client.last_kwargs.get("config")
    thinking = getattr(config, "thinking_config", None) or (
        config.get("thinking_config") if isinstance(config, dict) else None
    )
    assert thinking is not None, "expected thinking_config in GenerateContentConfig"
    budget = getattr(thinking, "thinking_budget", None) or (
        thinking.get("thinking_budget") if isinstance(thinking, dict) else None
    )
    assert budget == 0


# ===========================================================================
# §5.8.1 decision #4 — cache metrics repurposed for Gemini
# ===========================================================================


async def test_gemini_contextualizer_records_prompt_tokens_as_cache_creation():
    """`cache_creation_input_tokens` repurposed to hold Gemini's prompt_token_count
    (billed-input). `cache_read_input_tokens` stays 0 (no explicit cache used)."""
    from kb.contextualization import GeminiContextualizer

    mock_client = _MockGeminiClient(
        response_text="prefix line",
        prompt_tokens=2730,
        candidates_tokens=45,
    )
    contextualizer = GeminiContextualizer(client=mock_client, api_key="fake")

    result = await contextualizer.contextualize(doc_text="doc", chunk_text="chunk")

    assert result.cache_creation_input_tokens == 2730
    assert result.cache_read_input_tokens == 0
    assert result.prefix_token_count == 45
    assert result.contextual_prefix == "prefix line"
    assert result.contextual_text.endswith("chunk")


# ===========================================================================
# §5.8.1 decision #8 — failure mode (API error → ContextualizationError)
# ===========================================================================


async def test_gemini_contextualizer_api_error_raises_contextualization_error():
    """Underlying google-genai exceptions must surface as ContextualizationError
    so the worker's chunked→failed transition fires cleanly."""
    from kb.contextualization import (
        ContextualizationError,
        GeminiContextualizer,
    )

    mock_client = _MockGeminiClient(
        raise_exc=RuntimeError("gemini api 500: internal"),
    )
    contextualizer = GeminiContextualizer(client=mock_client, api_key="fake")

    with pytest.raises(ContextualizationError):
        await contextualizer.contextualize(doc_text="doc", chunk_text="chunk")


# ===========================================================================
# §5.8.1 decisions #1 + #9 — model literal + KB_CONTEXTUAL_MODEL override
# ===========================================================================


async def test_gemini_contextualizer_uses_configurable_model():
    """Default `gemini-2.5-flash`; KB_CONTEXTUAL_MODEL overrides per call.
    `result.model_id` echoes the resolved model literal (decision #9)."""
    from kb.contextualization import GeminiContextualizer

    mock_client = _MockGeminiClient()

    # Default model.
    with _env(KB_CONTEXTUAL_MODEL=None):
        contextualizer = GeminiContextualizer(client=mock_client, api_key="fake")
        result = await contextualizer.contextualize(doc_text="doc", chunk_text="chunk")
        assert mock_client.last_kwargs["model"] == "gemini-2.5-flash"
        assert result.model_id == "gemini-2.5-flash"

    # Override.
    with _env(KB_CONTEXTUAL_MODEL="gemini-2.5-pro"):
        contextualizer = GeminiContextualizer(client=mock_client, api_key="fake")
        result = await contextualizer.contextualize(doc_text="doc", chunk_text="chunk")
        assert mock_client.last_kwargs["model"] == "gemini-2.5-pro"
        assert result.model_id == "gemini-2.5-pro"
