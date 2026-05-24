"""Phase 3b — contextualizer adapter unit tests (no DB, no real API).

RED at G3: imports from `kb.contextualization` land at G4.

Spec: tests/specs/phase_3b.md §4.1.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from unittest.mock import AsyncMock

import pytest


pytestmark = pytest.mark.asyncio


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


def _build_mock_anthropic_response(text: str, *, cache_creation: int = 0, cache_read: int = 0):
    """Return an object shaped like anthropic.types.Message."""
    response = AsyncMock()
    response.content = [type("Block", (), {"type": "text", "text": text})()]
    response.usage = type(
        "Usage", (), {
            "cache_creation_input_tokens": cache_creation,
            "cache_read_input_tokens": cache_read,
            "input_tokens": 100,
            "output_tokens": 50,
        }
    )()
    return response


class _MockAnthropicClient:
    """Mimics anthropic.AsyncAnthropic for unit tests. Records the kwargs of
    every messages.create() call so tests can assert on request shape."""

    def __init__(self, *, response_text: str = "Test prefix.", cache_read: int = 0,
                 cache_creation: int = 0, raise_exc: Exception | None = None):
        self.last_kwargs: dict | None = None
        self._response_text = response_text
        self._cache_read = cache_read
        self._cache_creation = cache_creation
        self._raise_exc = raise_exc

        client_self = self

        class _Messages:
            async def create(self, **kwargs):
                client_self.last_kwargs = kwargs
                if client_self._raise_exc:
                    raise client_self._raise_exc
                return _build_mock_anthropic_response(
                    client_self._response_text,
                    cache_creation=client_self._cache_creation,
                    cache_read=client_self._cache_read,
                )

        self.messages = _Messages()


# ===========================================================================
# §5.8 decisions #2, #7 — prompt-cache placement + prompt template
# ===========================================================================


async def test_anthropic_contextualizer_sends_doc_as_cached_system_block():
    from kb.contextualization import AnthropicContextualizer

    mock_client = _MockAnthropicClient()
    contextualizer = AnthropicContextualizer(client=mock_client, api_key="fake")

    await contextualizer.contextualize(
        doc_text="ACME Corp 2024 10-K financial report.",
        chunk_text="Q3 revenue grew 12%.",
    )

    kwargs = mock_client.last_kwargs
    assert kwargs is not None
    # System should be a list of blocks with cache_control on the last.
    system = kwargs.get("system")
    assert isinstance(system, list)
    assert any("ACME Corp" in (b.get("text") or "") for b in system)
    last_cacheable = next(
        b for b in reversed(system) if b.get("type") == "text"
    )
    assert last_cacheable.get("cache_control") == {"type": "ephemeral"}


async def test_anthropic_contextualizer_sends_chunk_in_user_message():
    from kb.contextualization import AnthropicContextualizer

    mock_client = _MockAnthropicClient()
    contextualizer = AnthropicContextualizer(client=mock_client, api_key="fake")

    await contextualizer.contextualize(
        doc_text="Doc context here.",
        chunk_text="Q3 revenue grew 12%.",
    )

    kwargs = mock_client.last_kwargs
    messages = kwargs.get("messages", [])
    assert len(messages) >= 1
    assert messages[0]["role"] == "user"
    # Chunk text should appear in the user message content.
    user_content = messages[0]["content"]
    if isinstance(user_content, list):
        combined = " ".join(
            b.get("text", "") for b in user_content if b.get("type") == "text"
        )
    else:
        combined = user_content
    assert "Q3 revenue grew 12%" in combined


# ===========================================================================
# Response parsing + cache metrics
# ===========================================================================


async def test_anthropic_contextualizer_parses_prefix_from_response():
    from kb.contextualization import AnthropicContextualizer

    mock_client = _MockAnthropicClient(
        response_text="This chunk is from the ACME 10-K and describes Q3 revenue."
    )
    contextualizer = AnthropicContextualizer(client=mock_client, api_key="fake")

    result = await contextualizer.contextualize(
        doc_text="doc",
        chunk_text="Q3 revenue grew 12%.",
    )

    assert "ACME 10-K" in result.contextual_prefix
    assert result.contextual_text.startswith(result.contextual_prefix)
    assert result.contextual_text.endswith("Q3 revenue grew 12%.")


async def test_anthropic_contextualizer_records_cache_metrics():
    from kb.contextualization import AnthropicContextualizer

    mock_client = _MockAnthropicClient(
        response_text="prefix",
        cache_creation=4500,
        cache_read=4500,
    )
    contextualizer = AnthropicContextualizer(client=mock_client, api_key="fake")

    result = await contextualizer.contextualize(
        doc_text="doc",
        chunk_text="chunk",
    )

    assert result.cache_creation_input_tokens == 4500
    assert result.cache_read_input_tokens == 4500


# ===========================================================================
# §5.8 decision #6 — IdentityContextualizer fallback
# ===========================================================================


async def test_identity_contextualizer_returns_empty_prefix():
    from kb.contextualization import IdentityContextualizer

    contextualizer = IdentityContextualizer()
    result = await contextualizer.contextualize(
        doc_text="foo",
        chunk_text="bar",
    )

    assert result.contextual_prefix == ""
    assert result.contextual_text == "bar"
    assert result.model_id == "identity"
    assert result.cache_creation_input_tokens == 0
    assert result.cache_read_input_tokens == 0


async def test_contextualizer_factory_selector_matrix():
    """Phase 3b-bis decision #2: KB_CONTEXTUALIZER ∈ {gemini, anthropic, identity, auto}.

    Default `auto` probes Gemini key → Anthropic key → Identity in that order
    (Gemini-first matches the demo's "one API key, Gemini" default story).
    Explicit values override the probe.
    """
    from kb.contextualization import (
        AnthropicContextualizer,
        GeminiContextualizer,
        IdentityContextualizer,
        make_contextualizer,
    )

    # `auto` (default) + no keys → Identity.
    with _env(
        KB_CONTEXTUALIZER=None,
        KB_GEMINI_API_KEY=None,
        KB_ANTHROPIC_API_KEY=None,
    ):
        assert isinstance(make_contextualizer(), IdentityContextualizer)

    # `auto` + Gemini-only → Gemini (Gemini-first probe order).
    with _env(
        KB_CONTEXTUALIZER=None,
        KB_GEMINI_API_KEY="fake-gemini",
        KB_ANTHROPIC_API_KEY=None,
    ):
        assert isinstance(make_contextualizer(), GeminiContextualizer)

    # `auto` + Anthropic-only → Anthropic.
    with _env(
        KB_CONTEXTUALIZER=None,
        KB_GEMINI_API_KEY=None,
        KB_ANTHROPIC_API_KEY="fake-anthropic",
    ):
        assert isinstance(make_contextualizer(), AnthropicContextualizer)

    # `auto` + both keys set → Gemini wins (Gemini-first probe).
    with _env(
        KB_CONTEXTUALIZER=None,
        KB_GEMINI_API_KEY="fake-gemini",
        KB_ANTHROPIC_API_KEY="fake-anthropic",
    ):
        assert isinstance(make_contextualizer(), GeminiContextualizer)

    # Explicit `gemini` → Gemini (key required).
    with _env(
        KB_CONTEXTUALIZER="gemini",
        KB_GEMINI_API_KEY="fake-gemini",
        KB_ANTHROPIC_API_KEY=None,
    ):
        assert isinstance(make_contextualizer(), GeminiContextualizer)

    # Explicit `anthropic` → Anthropic (key required).
    with _env(
        KB_CONTEXTUALIZER="anthropic",
        KB_GEMINI_API_KEY=None,
        KB_ANTHROPIC_API_KEY="fake-anthropic",
    ):
        assert isinstance(make_contextualizer(), AnthropicContextualizer)

    # Explicit `identity` → Identity (ignores any keys).
    with _env(
        KB_CONTEXTUALIZER="identity",
        KB_GEMINI_API_KEY="fake-gemini",
        KB_ANTHROPIC_API_KEY="fake-anthropic",
    ):
        assert isinstance(make_contextualizer(), IdentityContextualizer)

    # Unknown selector value → ValueError.
    with _env(
        KB_CONTEXTUALIZER="bogus",
        KB_GEMINI_API_KEY=None,
        KB_ANTHROPIC_API_KEY=None,
    ):
        with pytest.raises(ValueError):
            make_contextualizer()


# ===========================================================================
# §5.8 decision #14 — failure mode
# ===========================================================================


async def test_anthropic_contextualizer_4xx_raises_contextualization_error():
    import httpx

    import anthropic

    from kb.contextualization import (
        AnthropicContextualizer,
        ContextualizationError,
    )

    # Construct a real httpx.Response so APIStatusError.__init__ can read
    # response.request without AttributeError.
    httpx_request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    httpx_response = httpx.Response(
        status_code=429, request=httpx_request, content=b'{"error": "rate limit"}',
    )
    mock_client = _MockAnthropicClient(
        raise_exc=anthropic.APIStatusError(
            "rate limited", response=httpx_response, body={"type": "error"}
        )
    )
    contextualizer = AnthropicContextualizer(client=mock_client, api_key="fake")

    with pytest.raises(ContextualizationError):
        await contextualizer.contextualize(
            doc_text="doc",
            chunk_text="chunk",
        )


# ===========================================================================
# §5.8 decision #9 — thinking disabled
# ===========================================================================


async def test_anthropic_contextualizer_uses_disabled_thinking():
    from kb.contextualization import AnthropicContextualizer

    mock_client = _MockAnthropicClient()
    contextualizer = AnthropicContextualizer(client=mock_client, api_key="fake")

    await contextualizer.contextualize(doc_text="doc", chunk_text="chunk")

    thinking = mock_client.last_kwargs.get("thinking")
    assert thinking == {"type": "disabled"}


# ===========================================================================
# §5.8 decision #1 — model choice + override
# ===========================================================================


async def test_anthropic_contextualizer_uses_configurable_model():
    from kb.contextualization import AnthropicContextualizer

    mock_client = _MockAnthropicClient()

    # Default model.
    with _env(KB_CONTEXTUAL_MODEL=None):
        contextualizer = AnthropicContextualizer(client=mock_client, api_key="fake")
        await contextualizer.contextualize(doc_text="doc", chunk_text="chunk")
        assert mock_client.last_kwargs["model"] == "claude-opus-4-7"

    # Overridden model.
    with _env(KB_CONTEXTUAL_MODEL="claude-haiku-4-5"):
        contextualizer = AnthropicContextualizer(client=mock_client, api_key="fake")
        await contextualizer.contextualize(doc_text="doc", chunk_text="chunk")
        assert mock_client.last_kwargs["model"] == "claude-haiku-4-5"
