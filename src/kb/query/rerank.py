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

import asyncio
import logging
import os
from typing import Protocol

from kb.query.rrf import Hit


_LOG = logging.getLogger(__name__)


DEFAULT_COHERE_MODEL = "rerank-english-v3.0"


def _is_transient(exc: Exception) -> bool:
    """A retryable Cohere error: rate limit (429), timeout, or a 5xx.
    Permanent errors (bad key, bad model, 4xx other than 429) are not
    retried — retrying them just wastes the rate budget."""
    msg = str(exc).lower()
    return any(
        s in msg for s in (
            "429", "rate limit", "too many requests",
            "timeout", "timed out", "503", "502", "504",
            "service unavailable",
        )
    )


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

    # Number of times to retry a TRANSIENT failure (rate limit / timeout)
    # before giving up and falling back to passthrough. Env-tunable so a
    # rate-limited trial key (Cohere trial ≈ 10 rerank/min) can wait out a
    # burst instead of silently degrading rerank to a no-op — which would
    # corrupt any eval measuring the rerank stage.
    _MAX_RETRIES = int(os.environ.get("KB_COHERE_MAX_RETRIES") or "3")
    _BASE_BACKOFF_S = float(os.environ.get("KB_COHERE_BACKOFF_S") or "6.0")

    def __init__(self, *, api_key: str) -> None:
        self._api_key = api_key
        self._model = os.environ.get("KB_COHERE_RERANK_MODEL") or DEFAULT_COHERE_MODEL
        self._client = None  # lazily built, reused across calls

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

        if self._client is None:
            self._client = cohere.AsyncClientV2(api_key=self._api_key)

        result = None
        last_exc: Exception | None = None
        for attempt in range(self._MAX_RETRIES + 1):
            try:
                result = await self._client.rerank(
                    model=self._model,
                    query=query,
                    documents=documents,
                    top_n=top_k,
                )
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                # Permanent error (bad key/model) → fall back now; retrying
                # only burns the rate budget.
                if not _is_transient(exc) or attempt == self._MAX_RETRIES:
                    break
                # Exponential backoff — gives a rate-limited trial key time
                # to recover its per-minute budget before the next try.
                await asyncio.sleep(self._BASE_BACKOFF_S * (attempt + 1))

        if result is None:
            # LOUD fallback — a silent passthrough here would make rerank
            # look like a no-op and quietly corrupt rerank-stage metrics.
            _LOG.warning(
                "cohere rerank fell back to passthrough after %d attempt(s): %s",
                self._MAX_RETRIES + 1, last_exc,
            )
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
# BGEReranker — BAAI/bge-reranker-v2-m3 (lighter + faster than mxbai-large-v2)
# ---------------------------------------------------------------------------


class BGEReranker:
    """`BAAI/bge-reranker-v2-m3` cross-encoder via sentence-transformers.

    Smaller (~568MB) + faster than mxbai-large-v2 (~1.5GB) at similar
    quality on English; better on multilingual (Marathi labour contracts
    in the construction corpus benefit). Cross-encoder rerank is the
    fix for the construction eval's retrieval-miss queries (q006 site
    address, q009 foundation depth, q011 PPE clause, q025 fire-stop) —
    those failures are not "right doc not in top-30" but "right doc not
    in top-10" because the initial BM25/dense ranking is keyword-biased.

    Performance notes:
      - On Mac Apple Silicon (M1/M2/M3), `device='mps'` is 5-10x faster
        than CPU. Probed automatically via `KB_RERANK_DEVICE` env var
        (default 'auto'). 'auto' tries mps → cuda → cpu in order.
      - The reranker only sees the top `KB_RERANK_POOL` candidates
        (default 20). RRF gave us top-30 to choose from; rerank picks
        the best 10. We can shrink the pool to cap latency at the cost
        of recall. 20 is the empirical sweet spot.

    Decision identical to MxBaiReranker: lazy-loaded class-level
    singleton; import / load failure → passthrough.
    """

    _model = None  # class-level singleton

    _MODEL_NAME = "BAAI/bge-reranker-v2-m3"

    @staticmethod
    def _resolve_device(requested: str) -> str:
        """Pick the best available torch device. 'auto' → mps → cuda → cpu."""
        if requested != "auto":
            return requested
        try:
            import torch  # type: ignore[import-not-found]
            if torch.backends.mps.is_available():
                return "mps"
            if torch.cuda.is_available():
                return "cuda"
        except Exception:
            pass
        return "cpu"

    async def rerank(
        self, query: str, hits: list[Hit], top_k: int,
    ) -> list[Hit]:
        if not hits:
            return []
        if BGEReranker._model is None:
            try:
                import sentence_transformers as st  # type: ignore[import-not-found]
                if st is None:
                    return hits[:top_k]
                device = self._resolve_device(
                    os.environ.get("KB_RERANK_DEVICE") or "auto"
                )
                # max_length defaults to BGE's native 512 tokens — we
                # need the full window to catch signals that live
                # deeper in a chunk (e.g. the site address line in an
                # EPC contract section header). Tune via
                # KB_RERANK_MAX_LENGTH to trade latency for recall.
                try:
                    max_len = int(
                        os.environ.get("KB_RERANK_MAX_LENGTH") or "512"
                    )
                except ValueError:
                    max_len = 512
                BGEReranker._model = st.CrossEncoder(
                    self._MODEL_NAME, device=device, max_length=max_len,
                )
            except ImportError:
                return hits[:top_k]
            except Exception:
                return hits[:top_k]

        # Latency cap — only rerank the top N candidates (RRF gave us 30;
        # we score the best 20 by default and keep the rest in original
        # order beyond the rerank window). Env-tunable.
        try:
            pool = int(os.environ.get("KB_RERANK_POOL") or "20")
        except ValueError:
            pool = 20
        pool = max(top_k, min(pool, len(hits)))
        head = hits[:pool]
        tail = hits[pool:]

        try:
            # No pre-truncation — let the BGE tokenizer handle it via
            # max_length. The signal we care about (e.g. site address,
            # PPE clause references) may live anywhere in the chunk.
            # KB_RERANK_CHAR_CAP can re-enable truncation if latency
            # becomes a problem at scale.
            cap_str = os.environ.get("KB_RERANK_CHAR_CAP") or ""
            if cap_str:
                try:
                    char_cap = int(cap_str)
                except ValueError:
                    char_cap = None
            else:
                char_cap = None
            if char_cap:
                pairs = [(query, (h.snippet or "")[:char_cap]) for h in head]
            else:
                pairs = [(query, h.snippet or "") for h in head]
            scores = BGEReranker._model.predict(pairs, batch_size=len(pairs))
        except Exception:
            return hits[:top_k]

        ranked = sorted(
            zip(head, scores, strict=True),
            key=lambda t: float(t[1]),
            reverse=True,
        )
        # Take top_k from the reranked pool. If pool < top_k we'd never
        # get here (pool >= top_k guaranteed above), but if the caller
        # wants more than we reranked, fall back to original-order tail.
        out: list[Hit] = []
        for hit, sc in ranked[:top_k]:
            out.append(Hit(
                id=hit.id,
                kind=hit.kind,
                score=float(sc),
                snippet=hit.snippet,
                metadata={**hit.metadata, "rerank": "bge"},
            ))
        if len(out) < top_k:
            out.extend(tail[: top_k - len(out)])
        return out


# ---------------------------------------------------------------------------
# Factory — KB_RERANKER selector
# ---------------------------------------------------------------------------


def make_reranker() -> Reranker:
    """Pick a reranker based on `KB_RERANKER`.

    Values: cohere | bge | mxbai | identity | auto (default auto).
    auto probes KB_COHERE_API_KEY → Identity. bge / mxbai are opt-in
    only (heavy local deps, don't auto-load).
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

    if selector == "bge":
        return BGEReranker()

    if selector == "mxbai":
        return MxBaiReranker()

    if selector == "identity":
        return IdentityReranker()

    raise ValueError(
        f"Unknown KB_RERANKER value: {selector!r} "
        f"(expected 'cohere', 'bge', 'mxbai', 'identity', or 'auto')"
    )
