"""Phase 3c embeddings — Gemini Embedding 001 + deterministic mock for CI.

Per build_tracker §5.9 (13 decisions). Same adapter pattern as Phase 3b's
contextualization module:

1. `GeminiEmbedder` — calls Gemini's `embed_content` with the contextual_text
   batch. Returns 3072-dim float vectors. Default model: `gemini-embedding-001`
   (per architecture §8; configurable via `KB_EMBEDDING_MODEL`).

2. `DeterministicMockEmbedder` — fallback when `KB_GEMINI_API_KEY` is unset.
   Produces stable vectors derived from `sha256(text || ":" || dim_index)`,
   L2-normalized to unit length. Reproducible across processes + Python
   versions so Phase 3d clustering tests can assert cluster shape.

Both impls satisfy the `Embedder` Protocol.
"""

from __future__ import annotations

import hashlib
import math
import os
from typing import Any, Protocol

from pydantic import BaseModel


DEFAULT_MODEL = "gemini-embedding-001"
EMBEDDING_DIM = 3072
MOCK_MODEL_ID = "mock-deterministic-v1"


class EmbeddingError(Exception):
    """An embedding call failed. Worker catches this and writes a
    `contextualized→failed` lifecycle event."""


class EmbeddingResult(BaseModel):
    """Output of `Embedder.embed_batch()`. One per input text."""

    vector: list[float]
    model_id: str
    dim: int


class Embedder(Protocol):
    async def embed_batch(self, texts: list[str]) -> list[EmbeddingResult]: ...


# ---------------------------------------------------------------------------
# DeterministicMockEmbedder — CI fallback when no API key
# ---------------------------------------------------------------------------


def _mock_vector(text: str, dim: int = EMBEDDING_DIM) -> list[float]:
    """Deterministic [-1, 1] L2-normalized vector for `text`.

    Per build_tracker §5.9 #5: `mock_vector[i] = (sha256(text || ":" ||
    str(i)).digest()[0] / 255.0) * 2 - 1`, then L2-normalize.
    """
    raw: list[float] = []
    for i in range(dim):
        h = hashlib.sha256(f"{text}:{i}".encode("utf-8")).digest()
        raw.append((h[0] / 255.0) * 2 - 1)
    # L2 normalize.
    norm = math.sqrt(sum(v * v for v in raw))
    if norm == 0.0:
        # Degenerate; return a unit vector along first dim.
        return [1.0] + [0.0] * (dim - 1)
    return [v / norm for v in raw]


class DeterministicMockEmbedder:
    """Reproducible mock embedder. `model_id='mock-deterministic-v1'`."""

    async def embed_batch(self, texts: list[str]) -> list[EmbeddingResult]:
        return [
            EmbeddingResult(
                vector=_mock_vector(t),
                model_id=MOCK_MODEL_ID,
                dim=EMBEDDING_DIM,
            )
            for t in texts
        ]


# ---------------------------------------------------------------------------
# GeminiEmbedder — real LLM call via google-genai
# ---------------------------------------------------------------------------


class GeminiEmbedder:
    """Adapter for Google's google-genai SDK + Gemini Embedding 001."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: Any | None = None,
        model: str | None = None,
    ) -> None:
        if client is None:
            if not api_key:
                raise EmbeddingError(
                    "GeminiEmbedder requires api_key or client"
                )
            from google.genai import Client
            client = Client(api_key=api_key)
        self._client = client
        self._model = model or os.environ.get("KB_EMBEDDING_MODEL") or DEFAULT_MODEL

    async def embed_batch(self, texts: list[str]) -> list[EmbeddingResult]:
        # Re-read env at call time so tests can swap KB_EMBEDDING_MODEL on
        # each call without rebuilding the embedder.
        model = os.environ.get("KB_EMBEDDING_MODEL") or self._model

        try:
            response = await self._client.aio.models.embed_content(
                model=model,
                contents=texts,
            )
        except Exception as exc:
            raise EmbeddingError(f"Gemini embed call failed: {exc}") from exc

        embeddings = getattr(response, "embeddings", None) or []
        if len(embeddings) != len(texts):
            raise EmbeddingError(
                f"Gemini returned {len(embeddings)} embeddings for "
                f"{len(texts)} inputs"
            )

        results: list[EmbeddingResult] = []
        for emb in embeddings:
            # google-genai's ContentEmbedding has .values: list[float].
            values = getattr(emb, "values", None) or list(emb)
            results.append(EmbeddingResult(
                vector=list(values),
                model_id=model,
                dim=len(values),
            ))
        return results


# ---------------------------------------------------------------------------
# Factory — picks Gemini vs Mock based on env
# ---------------------------------------------------------------------------


def make_embedder() -> Embedder:
    """Return the appropriate Embedder based on env.

    Decision #4: KB_GEMINI_API_KEY unset → DeterministicMockEmbedder
    (degraded mode); set → GeminiEmbedder.
    """
    api_key = os.environ.get("KB_GEMINI_API_KEY")
    if not api_key:
        return DeterministicMockEmbedder()
    return GeminiEmbedder(api_key=api_key)
