"""Phase 8c — cross-encoder reranker.

Per build_tracker §5.15.3 (12 locked decisions). Architecture line 197 (Cohere
Rerank 3.5 default) + line 904 (mxbai-rerank-large-v2 local fallback).

Three impls satisfy the same `Reranker` Protocol:

1. `CohereReranker` — Cohere Rerank 3.5 (hosted, best-in-class). Default
   when `KB_COHERE_API_KEY` is set.
2. `MxBaiReranker` — `mixedbread-ai/mxbai-rerank-large-v2` cross-encoder via
   `sentence-transformers.CrossEncoder`. Local CPU/GPU. Lazy-loaded
   singleton at class level (decision #12). Opt-in via `KB_RERANKER=mxbai`
   (NOT auto-probe — heavy ~500MB dep).
3. `IdentityReranker` — passthrough (`hits[:top_k]`). Auto-fallback when no
   Cohere key.

Factory `make_reranker()` reads `KB_RERANKER ∈ {cohere, mxbai, identity, auto}`.
`auto` (default): cohere → identity. mxbai requires explicit selector.
"""

from __future__ import annotations

import os
from typing import Protocol

from kb.query.rrf import Hit


DEFAULT_COHERE_MODEL = "rerank-english-v3.0"


class Reranker(Protocol):
    async def rerank(
        self, query: str, hits: list[Hit], top_k: int,
    ) -> list[Hit]: ...


# ---------------------------------------------------------------------------
# IdentityReranker — passthrough
# ---------------------------------------------------------------------------


class IdentityReranker:
    """Passthrough: returns input order, truncated to top_k. No LLM call."""

    async def rerank(
        self, query: str, hits: list[Hit], top_k: int,
    ) -> list[Hit]:
        return hits[:top_k]


# ---------------------------------------------------------------------------
# CohereReranker — Cohere Rerank 3.5
# ---------------------------------------------------------------------------


class CohereReranker:
    """Cohere Rerank 3.5 (architecture line 197). Default reranker when
    `KB_COHERE_API_KEY` is set.

    Decision #7: any Cohere API exception OR missing `cohere` Python package
    falls back to passthrough (`hits[:top_k]`). Rerank is quality boost;
    query should still complete on rerank failure.

    Decision #11: uses `cohere.AsyncClientV2.rerank()` — v5 SDK async path.
    """

    def __init__(self, *, api_key: str) -> None:
        self._api_key = api_key
        self._model = os.environ.get("KB_COHERE_RERANK_MODEL") or DEFAULT_COHERE_MODEL

    async def rerank(
        self, query: str, hits: list[Hit], top_k: int,
    ) -> list[Hit]:
        # Decision #10: empty input → return [] immediately, no API call.
        if not hits:
            return []

        # Decision #7: missing cohere pkg → passthrough.
        try:
            import cohere  # type: ignore[import-not-found]
        except ImportError:
            return hits[:top_k]
        if cohere is None:  # monkeypatched-to-None in tests
            return hits[:top_k]

        # Decision #8: snippet is the document (already truncated to 500
        # chars per 8b decision #11).
        documents = [h.snippet or "" for h in hits]

        try:
            client = cohere.AsyncClientV2(api_key=self._api_key)
            result = await client.rerank(
                model=self._model,
                query=query,
                documents=documents,
                top_n=top_k,
            )
        except Exception:
            return hits[:top_k]

        # Decision #9: reranked score = Cohere relevance_score.
        # metadata gains rerank='cohere'.
        reranked: list[Hit] = []
        for r in (result.results or []):
            src = hits[r.index]
            reranked.append(Hit(
                id=src.id,
                kind=src.kind,
                score=float(r.relevance_score),
                snippet=src.snippet,
                metadata={**src.metadata, "rerank": "cohere"},
            ))
        return reranked


# ---------------------------------------------------------------------------
# MxBaiReranker — local mxbai-rerank-large-v2 fallback
# ---------------------------------------------------------------------------


class MxBaiReranker:
    """`mixedbread-ai/mxbai-rerank-large-v2` via sentence-transformers
    CrossEncoder. Architecture line 904.

    Decision #12: model is lazy-loaded as a class-level singleton on first
    `.rerank()` call. Subsequent calls reuse the loaded model.

    Decision #7: missing sentence-transformers OR model load failure →
    passthrough.
    """

    _model = None  # class-level singleton

    async def rerank(
        self, query: str, hits: list[Hit], top_k: int,
    ) -> list[Hit]:
        if not hits:
            return []

        # Lazy-load the model. Catches import failure + load failure.
        if MxBaiReranker._model is None:
            try:
                # sentence_transformers may be monkeypatched to None in tests
                import sentence_transformers as st  # type: ignore[import-not-found]
                if st is None:
                    return hits[:top_k]
                MxBaiReranker._model = st.CrossEncoder(
                    "mixedbread-ai/mxbai-rerank-large-v2"
                )
            except ImportError:
                return hits[:top_k]
            except Exception:
                return hits[:top_k]

        try:
            pairs = [(query, h.snippet or "") for h in hits]
            scores = MxBaiReranker._model.predict(pairs)
        except Exception:
            return hits[:top_k]

        ranked = sorted(
            zip(hits, scores, strict=True),
            key=lambda t: float(t[1]),
            reverse=True,
        )
        out: list[Hit] = []
        for hit, sc in ranked[:top_k]:
            out.append(Hit(
                id=hit.id,
                kind=hit.kind,
                score=float(sc),
                snippet=hit.snippet,
                metadata={**hit.metadata, "rerank": "mxbai"},
            ))
        return out


# ---------------------------------------------------------------------------
# Factory — KB_RERANKER selector
# ---------------------------------------------------------------------------


def make_reranker() -> Reranker:
    """Pick a reranker based on `KB_RERANKER`.

    Values: cohere | mxbai | identity | auto (default auto).
    auto probes KB_COHERE_API_KEY → Identity. mxbai is opt-in only
    (decision #2 — heavy local dep, don't auto-load).
    """
    selector = (os.environ.get("KB_RERANKER") or "auto").lower()

    if selector == "auto":
        if os.environ.get("KB_COHERE_API_KEY"):
            selector = "cohere"
        else:
            selector = "identity"

    if selector == "cohere":
        api_key = os.environ.get("KB_COHERE_API_KEY")
        if not api_key:
            raise ValueError(
                "KB_RERANKER=cohere requires KB_COHERE_API_KEY"
            )
        return CohereReranker(api_key=api_key)

    if selector == "mxbai":
        return MxBaiReranker()

    if selector == "identity":
        return IdentityReranker()

    raise ValueError(
        f"Unknown KB_RERANKER value: {selector!r} "
        f"(expected 'cohere', 'mxbai', 'identity', or 'auto')"
    )
