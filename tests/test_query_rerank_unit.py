"""Phase 8c — reranker unit tests (no DB, no real Cohere/mxbai)."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from kb.query.rerank import (
    CohereReranker,
    IdentityReranker,
    MxBaiReranker,
    make_reranker,
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


def _hit(id_: str, score: float = 0.5, snippet: str = "x") -> Hit:
    return Hit(id=id_, kind="chunk", score=score, snippet=snippet,
               metadata={"file_id": "f1", "level": 1})


# ===========================================================================
# Factory
# ===========================================================================


def test_factory_selector_matrix():
    # auto + no key → Identity (mxbai is opt-in, NOT auto-probe)
    with _env(KB_RERANKER="auto", KB_COHERE_API_KEY=None):
        assert isinstance(make_reranker(), IdentityReranker)

    # auto + Cohere key → CohereReranker
    with _env(KB_RERANKER="auto", KB_COHERE_API_KEY="fake-cohere"):
        assert isinstance(make_reranker(), CohereReranker)

    # explicit cohere requires key
    with _env(KB_RERANKER="cohere", KB_COHERE_API_KEY=None):
        with pytest.raises(ValueError, match="KB_RERANKER=cohere"):
            make_reranker()

    # explicit mxbai (no key required — local)
    with _env(KB_RERANKER="mxbai"):
        assert isinstance(make_reranker(), MxBaiReranker)

    # explicit identity
    with _env(KB_RERANKER="identity"):
        assert isinstance(make_reranker(), IdentityReranker)

    # bogus
    with _env(KB_RERANKER="bogus"):
        with pytest.raises(ValueError, match="Unknown KB_RERANKER"):
            make_reranker()


def test_factory_default_is_cohere_when_key_set():
    with _env(KB_RERANKER=None, KB_COHERE_API_KEY="fake"):
        assert isinstance(make_reranker(), CohereReranker)


def test_factory_auto_falls_back_to_identity_when_no_cohere_key():
    """mxbai is opt-in (heavy dep); auto skips it."""
    with _env(KB_RERANKER="auto", KB_COHERE_API_KEY=None):
        r = make_reranker()
        # Not MxBai — Identity
        assert isinstance(r, IdentityReranker)
        assert not isinstance(r, MxBaiReranker)


def test_factory_mxbai_is_opt_in_not_auto():
    with _env(KB_RERANKER="auto", KB_COHERE_API_KEY=None):
        assert not isinstance(make_reranker(), MxBaiReranker)


# ===========================================================================
# IdentityReranker (decision: passthrough)
# ===========================================================================


@pytest.mark.asyncio
async def test_identity_reranker_passthrough_preserves_order():
    r = IdentityReranker()
    hits = [_hit("a"), _hit("b"), _hit("c"), _hit("d")]
    out = await r.rerank("query", hits, top_k=2)
    assert [h.id for h in out] == ["a", "b"]


@pytest.mark.asyncio
async def test_reranker_returns_empty_for_empty_input():
    """Decision #10."""
    r = IdentityReranker()
    assert await r.rerank("q", [], top_k=10) == []
    cr = CohereReranker(api_key="fake")
    assert await cr.rerank("q", [], top_k=10) == []


# ===========================================================================
# Cohere reranker (mocked)
# ===========================================================================


class _FakeRerankResult:
    def __init__(self, results):
        self.results = results


class _FakeRerankItem:
    def __init__(self, index: int, relevance_score: float):
        self.index = index
        self.relevance_score = relevance_score


@pytest.mark.asyncio
async def test_cohere_rerank_updates_score_and_metadata(monkeypatch):
    """Decision #9: reranked Hit's score = Cohere relevance_score;
    metadata gains rerank='cohere'."""
    hits = [_hit("a"), _hit("b"), _hit("c")]

    # Mock cohere.AsyncClientV2
    fake_client = MagicMock()
    async def _mock_rerank(model, query, documents, top_n):
        # Return hits in REVERSE order with high scores
        return _FakeRerankResult([
            _FakeRerankItem(index=2, relevance_score=0.95),
            _FakeRerankItem(index=0, relevance_score=0.50),
        ])
    fake_client.rerank = _mock_rerank

    fake_cohere_module = MagicMock()
    fake_cohere_module.AsyncClientV2 = MagicMock(return_value=fake_client)
    monkeypatch.setitem(__import__("sys").modules, "cohere", fake_cohere_module)

    r = CohereReranker(api_key="fake")
    out = await r.rerank("q", hits, top_k=2)
    assert len(out) == 2
    # Reordered: c (index 2 from input) first, then a (index 0)
    assert out[0].id == "c"
    assert out[0].score == 0.95
    assert out[0].metadata["rerank"] == "cohere"
    assert out[1].id == "a"
    assert out[1].score == 0.50


@pytest.mark.asyncio
async def test_cohere_api_error_falls_back_to_passthrough(monkeypatch):
    """Decision #7: API error → passthrough hits[:top_k]."""
    fake_client = MagicMock()
    async def _broken_rerank(**kwargs):
        raise RuntimeError("Cohere down")
    fake_client.rerank = _broken_rerank
    fake_cohere_module = MagicMock()
    fake_cohere_module.AsyncClientV2 = MagicMock(return_value=fake_client)
    monkeypatch.setitem(__import__("sys").modules, "cohere", fake_cohere_module)

    hits = [_hit("a"), _hit("b"), _hit("c")]
    r = CohereReranker(api_key="fake")
    out = await r.rerank("q", hits, top_k=2)
    assert [h.id for h in out] == ["a", "b"]  # passthrough order


@pytest.mark.asyncio
async def test_cohere_reranker_falls_back_when_cohere_not_installed(monkeypatch):
    """Decision #7 + #11: missing `cohere` package → passthrough."""
    import sys
    monkeypatch.setitem(sys.modules, "cohere", None)  # ImportError on `import cohere`

    hits = [_hit("a"), _hit("b")]
    r = CohereReranker(api_key="fake")
    out = await r.rerank("q", hits, top_k=2)
    assert [h.id for h in out] == ["a", "b"]


@pytest.mark.asyncio
async def test_cohere_reranker_honors_kb_cohere_rerank_model_env():
    """Decision #5: KB_COHERE_RERANK_MODEL env overrides default."""
    with _env(KB_COHERE_RERANK_MODEL="rerank-multilingual-v3.0"):
        r = CohereReranker(api_key="fake")
        assert r._model == "rerank-multilingual-v3.0"


@pytest.mark.asyncio
async def test_cohere_reranker_uses_async_client_v2_pattern(monkeypatch):
    """Decision #11: uses cohere.AsyncClientV2 (the async client)."""
    captured = {}
    fake_client = MagicMock()
    async def _capture_rerank(**kwargs):
        captured.update(kwargs)
        return _FakeRerankResult([])
    fake_client.rerank = _capture_rerank
    fake_cohere_module = MagicMock()
    fake_cohere_module.AsyncClientV2 = MagicMock(return_value=fake_client)
    monkeypatch.setitem(__import__("sys").modules, "cohere", fake_cohere_module)

    hits = [_hit("a"), _hit("b")]
    r = CohereReranker(api_key="fake")
    await r.rerank("query-text", hits, top_k=2)

    # The kwargs sent to Cohere
    assert "documents" in captured
    assert captured["query"] == "query-text"
    assert captured["top_n"] == 2


# ===========================================================================
# MxBai reranker (mocked sentence-transformers)
# ===========================================================================


@pytest.mark.asyncio
async def test_mxbai_missing_dep_falls_back_to_passthrough(monkeypatch):
    """Decision #7 + #12: sentence-transformers not installed → passthrough."""
    # Reset class-level singleton so the test starts fresh
    MxBaiReranker._model = None
    import sys
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)

    hits = [_hit("a"), _hit("b"), _hit("c")]
    r = MxBaiReranker()
    out = await r.rerank("q", hits, top_k=2)
    assert [h.id for h in out] == ["a", "b"]


@pytest.mark.asyncio
async def test_mxbai_reranker_lazy_singleton(monkeypatch):
    """Decision #12: _model is class-level singleton; second call doesn't
    re-load."""
    # Reset singleton
    MxBaiReranker._model = None
    load_count = {"n": 0}

    class _FakeCrossEncoder:
        def __init__(self, model_name):
            load_count["n"] += 1
            self._name = model_name

        def predict(self, pairs):
            # Return scores: hit_0 = 0.1, hit_1 = 0.9, hit_2 = 0.5
            return [0.1, 0.9, 0.5][:len(pairs)]

    fake_st = MagicMock()
    fake_st.CrossEncoder = _FakeCrossEncoder
    import sys
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)

    r = MxBaiReranker()
    hits = [_hit("a"), _hit("b"), _hit("c")]

    out1 = await r.rerank("q", hits, top_k=2)
    out2 = await r.rerank("q", hits, top_k=2)
    # Loaded once across both calls (class-level singleton)
    assert load_count["n"] == 1
    # Top 2 by mxbai score: hit_1 (0.9), hit_2 (0.5)
    assert [h.id for h in out1] == ["b", "c"]
    assert out1[0].score == 0.9
    assert out1[0].metadata["rerank"] == "mxbai"


# ===========================================================================
# Hit input handling (decision #8: snippet is the document)
# ===========================================================================


@pytest.mark.asyncio
async def test_reranker_sees_hit_snippet_as_document(monkeypatch):
    captured = {}
    fake_client = MagicMock()
    async def _capture(**kwargs):
        captured.update(kwargs)
        return _FakeRerankResult([])
    fake_client.rerank = _capture
    fake_cohere_module = MagicMock()
    fake_cohere_module.AsyncClientV2 = MagicMock(return_value=fake_client)
    monkeypatch.setitem(__import__("sys").modules, "cohere", fake_cohere_module)

    hits = [
        Hit(id="x", kind="chunk", score=0, snippet="snippet content 1"),
        Hit(id="y", kind="chunk", score=0, snippet="snippet content 2"),
    ]
    r = CohereReranker(api_key="fake")
    await r.rerank("q", hits, top_k=2)
    assert captured.get("documents") == ["snippet content 1", "snippet content 2"]


# ===========================================================================
# Top-K truncation (decision #6)
# ===========================================================================


@pytest.mark.asyncio
async def test_reranker_top_k_truncates_input():
    """Identity reranker: top_k=2 from 5 hits returns first 2."""
    r = IdentityReranker()
    hits = [_hit(c) for c in "abcde"]
    out = await r.rerank("q", hits, top_k=2)
    assert len(out) == 2
