"""Phase 3c — embedder adapter unit tests (no DB, no real API).

RED at G3: imports from `kb.embeddings` land at G4.

Spec: tests/specs/phase_3c.md §4.1.
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


class _MockGeminiClient:
    """Mimics the google-genai SDK shape for unit tests. Records the kwargs
    of every embed_content() call so tests can assert on request shape."""

    def __init__(self, *, vectors: list[list[float]] | None = None):
        self.last_kwargs: dict | None = None
        self._vectors = vectors

        client_self = self

        class _Models:
            async def embed_content(self, **kwargs):
                client_self.last_kwargs = kwargs
                # Return an object shaped like google-genai's EmbedContentResponse.
                texts = kwargs.get("contents") or kwargs.get("content") or []
                if isinstance(texts, str):
                    texts = [texts]
                n = len(texts)
                vecs = client_self._vectors or [[0.1] * 3072 for _ in range(n)]
                return type("Resp", (), {
                    "embeddings": [
                        type("Emb", (), {"values": v})() for v in vecs
                    ],
                })()

        self.aio = type("Aio", (), {"models": _Models()})()


async def test_gemini_embedder_sends_batch_with_model_name():
    from kb.embeddings import GeminiEmbedder

    mock_client = _MockGeminiClient()

    # Default model
    with _env(KB_EMBEDDING_MODEL=None):
        embedder = GeminiEmbedder(client=mock_client, api_key="fake")
        await embedder.embed_batch(["chunk one", "chunk two"])
        kwargs = mock_client.last_kwargs
        assert kwargs is not None
        assert kwargs["model"] == "gemini-embedding-001"

    # Overridden model
    with _env(KB_EMBEDDING_MODEL="text-embedding-005"):
        embedder = GeminiEmbedder(client=mock_client, api_key="fake")
        await embedder.embed_batch(["chunk three"])
        assert mock_client.last_kwargs["model"] == "text-embedding-005"


async def test_deterministic_mock_embedder_is_reproducible_across_calls():
    from kb.embeddings import DeterministicMockEmbedder

    embedder = DeterministicMockEmbedder()
    result1 = await embedder.embed_batch(["the quick brown fox"])
    result2 = await embedder.embed_batch(["the quick brown fox"])
    result3 = await embedder.embed_batch(["the quick brown fox"])

    assert result1[0].vector == result2[0].vector == result3[0].vector


async def test_deterministic_mock_embedder_returns_3072_dim_vectors():
    from kb.embeddings import DeterministicMockEmbedder

    embedder = DeterministicMockEmbedder()
    result = await embedder.embed_batch(["any text"])
    assert len(result[0].vector) == 3072


async def test_deterministic_mock_embedder_l2_normalizes_to_unit_length():
    import math

    from kb.embeddings import DeterministicMockEmbedder

    embedder = DeterministicMockEmbedder()
    result = await embedder.embed_batch(["any text here"])
    vector = result[0].vector
    norm = math.sqrt(sum(v * v for v in vector))
    assert abs(norm - 1.0) < 1e-5, f"expected unit norm, got {norm}"


async def test_mock_embedder_model_id_distinguishes_from_real():
    from kb.embeddings import DeterministicMockEmbedder

    embedder = DeterministicMockEmbedder()
    result = await embedder.embed_batch(["test"])
    assert result[0].model_id == "mock-deterministic-v1"


async def test_embedder_factory_returns_mock_when_no_api_key():
    from kb.embeddings import (
        DeterministicMockEmbedder,
        GeminiEmbedder,
        make_embedder,
    )

    with _env(KB_GEMINI_API_KEY=None):
        embedder = make_embedder()
        assert isinstance(embedder, DeterministicMockEmbedder)

    with _env(KB_GEMINI_API_KEY="fake-key"):
        embedder = make_embedder()
        assert isinstance(embedder, GeminiEmbedder)


async def test_embed_batch_returns_one_vector_per_input():
    from kb.embeddings import DeterministicMockEmbedder

    embedder = DeterministicMockEmbedder()
    result = await embedder.embed_batch(["one", "two", "three", "four"])
    assert len(result) == 4
    # Each result is distinct (different texts → different vectors).
    vectors = [tuple(r.vector) for r in result]
    assert len(set(vectors)) == 4
