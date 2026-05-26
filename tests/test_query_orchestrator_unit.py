"""Phase 8f — Orchestrator unit tests (no DB, mocked components)."""

from __future__ import annotations

from typing import Any

import pytest

from kb.embeddings import EmbeddingResult
from kb.query.generate import Citation, GenerationResult
from kb.query.orchestrator import ChatResult, Orchestrator, SearchResult
from kb.query.rewriter import Rewrites
from kb.query.rrf import Hit


pytestmark = pytest.mark.asyncio


# ===========================================================================
# Test doubles
# ===========================================================================


class _FakeRewriter:
    def __init__(self):
        self.last_query: str | None = None

    async def rewrite(self, query: str) -> Rewrites:
        self.last_query = query
        return Rewrites(
            original=query,
            step_back=f"sb({query})",
            hyde=f"hy({query})",
            query2doc=f"q2d({query})",
        )


class _FakeEmbedder:
    MOCK_DIM = 4

    def __init__(self):
        self.last_texts: list[str] = []

    async def embed_batch(self, texts: list[str]) -> list[EmbeddingResult]:
        self.last_texts = list(texts)
        return [
            EmbeddingResult(vector=[1.0, 0.0, 0.0, 0.0],
                            model_id="fake-emb", dim=self.MOCK_DIM)
            for _ in texts
        ]


def _hit(hit_id: str, snippet: str = "s") -> Hit:
    return Hit(id=hit_id, kind="chunk", score=0.5, snippet=snippet,
               metadata={"file_id": "f1"})


def _make_run_channels(per_query_hits: list[Hit]):
    """Build a fake run_channels that returns 6 channel lists per call.
    The returned dict has all 6 expected channel names; total = 6 × len(per_query_hits)."""
    call_log: list[dict[str, Any]] = []

    async def _run(conn: Any, *, workspace_id: str, query: str,
                   query_vec: list[float], limit: int = 20,
                   bm25_query: str | None = None) -> dict[str, list[Hit]]:
        call_log.append({"query": query, "workspace_id": workspace_id,
                         "query_vec": query_vec, "limit": limit,
                         "bm25_query": bm25_query})
        return {
            "bm25_chunks": list(per_query_hits),
            "bm25_raptor": list(per_query_hits),
            "dense_chunks": list(per_query_hits),
            "dense_raptor": list(per_query_hits),
            "mentions_exact": list(per_query_hits),
            "atomic_units_rarity": list(per_query_hits),
        }

    return _run, call_log


class _FakeReranker:
    def __init__(self):
        self.last_query: str | None = None
        self.last_hits: list[Hit] = []
        self.last_top_k: int | None = None

    async def rerank(self, query: str, hits: list[Hit], *, top_k: int) -> list[Hit]:
        self.last_query = query
        self.last_hits = list(hits)
        self.last_top_k = top_k
        return hits[:top_k]


class _FakeCrag:
    def __init__(self, score: float = 0.8):
        self.score = score
        self.last_hits: list[Hit] = []

    async def assess(self, query: str, hits: list[Hit]) -> float:
        self.last_hits = list(hits)
        return self.score


class _FakeGenerator:
    MODEL_ID = "fake-gen"

    def __init__(self):
        self.last_force_refuse: bool | None = None
        self.last_hits: list[Hit] = []

    async def generate(self, query: str, hits: list[Hit], *,
                       force_refuse: bool = False,
                       conflict_context: str | None = None) -> GenerationResult:
        self.last_force_refuse = force_refuse
        self.last_hits = list(hits)
        # R1 — capture conflict_context for assertion tests
        self.last_conflict_context = conflict_context
        if force_refuse:
            return GenerationResult(
                answer="", citations=[], refused=True,
                refusal_reason="insufficient_evidence", model_id=self.MODEL_ID,
            )
        if not hits:
            return GenerationResult(
                answer="", citations=[], refused=True,
                refusal_reason="no_hits", model_id=self.MODEL_ID,
            )
        return GenerationResult(
            answer=f"answered ({len(hits)} hits)",
            citations=[Citation(hit_id=h.id, kind=h.kind, file_id="f1",
                                snippet_preview=h.snippet, score=h.score)
                       for h in hits[:3]],
            refused=False, refusal_reason=None, model_id=self.MODEL_ID,
        )


def _make_orchestrator(
    *,
    hits_per_query: list[Hit] | None = None,
    crag_score: float = 0.8,
    crag_threshold: float = 0.5,
):
    if hits_per_query is None:
        hits_per_query = [_hit(f"h{i}") for i in range(5)]
    run_channels, call_log = _make_run_channels(hits_per_query)
    rewriter = _FakeRewriter()
    embedder = _FakeEmbedder()
    reranker = _FakeReranker()
    crag = _FakeCrag(score=crag_score)
    generator = _FakeGenerator()
    orch = Orchestrator(
        rewriter=rewriter,
        embedder=embedder,
        reranker=reranker,
        crag=crag,
        generator=generator,
        run_channels=run_channels,
        crag_threshold=crag_threshold,
    )
    return orch, {
        "rewriter": rewriter, "embedder": embedder, "reranker": reranker,
        "crag": crag, "generator": generator, "call_log": call_log,
    }


# ===========================================================================
# Pydantic shapes (decisions #9 / #10)
# ===========================================================================


def test_search_result_pydantic_shape():
    r = SearchResult(
        query_id="q1", query="q", rewrites={"original": "q"},
        hits=[], crag_score=0.0, latency_ms=10,
    )
    assert r.query_id == "q1"
    assert r.rewrites == {"original": "q"}
    assert r.hits == []
    assert r.latency_ms == 10


def test_chat_result_pydantic_shape():
    gen = GenerationResult(
        answer="x", citations=[], refused=False,
        refusal_reason=None, model_id="m",
    )
    r = ChatResult(
        query_id="q1", query="q", generation=gen,
        hits=[], crag_score=0.0, latency_ms=10,
    )
    assert r.generation.answer == "x"
    assert r.latency_ms == 10


# ===========================================================================
# Pipeline fan-out (decisions #2 / #3 / #6)
# ===========================================================================


async def test_orchestrator_fans_out_all_4_rewrites_to_channels():
    """Decision #2: 4 rewrites (original + step_back + hyde + query2doc)."""
    orch, deps = _make_orchestrator()
    await orch.search("hello", workspace_id="ws1", conn=None)
    queries = [c["query"] for c in deps["call_log"]]
    # All 4 rewrite variants should have been fed to channels
    assert "hello" in queries
    assert "sb(hello)" in queries
    assert "hy(hello)" in queries
    assert "q2d(hello)" in queries
    assert len(queries) == 4


async def test_orchestrator_fuses_24_channel_lists_via_rrf():
    """Decision #3: 4 rewrites × 6 channels = 24 result lists."""
    # 5 unique hits → reranked top-10 returns all 5 (after RRF dedup).
    hits = [_hit(f"h{i}") for i in range(5)]
    orch, deps = _make_orchestrator(hits_per_query=hits)
    result = await orch.search("hello", workspace_id="ws1", conn=None)
    # 4 calls × 6 channels each
    assert len(deps["call_log"]) == 4
    # Reranker saw the fused list (deduped to 5 unique hits even though 24
    # channel lists fed in)
    assert deps["reranker"].last_hits is not None
    assert len(deps["reranker"].last_hits) == 5
    assert len(result.hits) == 5


async def test_orchestrator_caps_at_top_10_after_rerank():
    """Decision #6: top-10 returned after rerank."""
    hits = [_hit(f"h{i}") for i in range(50)]
    orch, deps = _make_orchestrator(hits_per_query=hits)
    result = await orch.search("hello", workspace_id="ws1", conn=None)
    assert deps["reranker"].last_top_k == 10
    assert len(result.hits) == 10


# ===========================================================================
# CRAG placement (decisions #7 / #8)
# ===========================================================================


async def test_orchestrator_calls_crag_after_rerank():
    """Decision #7: CRAG sees the reranked hits."""
    orch, deps = _make_orchestrator(crag_score=0.9)
    await orch.search("hello", workspace_id="ws1", conn=None)
    # CRAG's hits should match the reranker's output (top-10)
    assert deps["crag"].last_hits == deps["reranker"].last_hits[:10]


async def test_orchestrator_force_refuses_generator_when_crag_below_threshold():
    """Decision #8: when CRAG < threshold, generator is force-refused."""
    orch, deps = _make_orchestrator(crag_score=0.2, crag_threshold=0.5)
    result = await orch.chat("hello", workspace_id="ws1", conn=None)
    assert deps["generator"].last_force_refuse is True
    assert result.generation.refused is True
    assert result.generation.refusal_reason == "insufficient_evidence"


async def test_orchestrator_does_not_force_refuse_when_crag_above_threshold():
    orch, deps = _make_orchestrator(crag_score=0.7, crag_threshold=0.5)
    result = await orch.chat("hello", workspace_id="ws1", conn=None)
    assert deps["generator"].last_force_refuse is False
    assert result.generation.refused is False


# ===========================================================================
# /search vs /chat envelope shape (decisions #9 / #10)
# ===========================================================================


async def test_orchestrator_search_returns_no_generation():
    """Decision #9: SearchResult has hits + crag_score but NO answer field."""
    orch, _ = _make_orchestrator(crag_score=0.7)
    result = await orch.search("hello", workspace_id="ws1", conn=None)
    assert isinstance(result, SearchResult)
    assert "generation" not in result.model_dump()
    assert result.crag_score == pytest.approx(0.7)
    assert len(result.hits) > 0


async def test_orchestrator_chat_returns_chat_result_envelope():
    """Decision #10."""
    orch, _ = _make_orchestrator(crag_score=0.7)
    result = await orch.chat("hello", workspace_id="ws1", conn=None)
    assert isinstance(result, ChatResult)
    assert result.generation.answer.startswith("answered")
    assert len(result.generation.citations) > 0
    assert result.crag_score == pytest.approx(0.7)


# ===========================================================================
# Empty corpus (decision #16)
# ===========================================================================


async def test_orchestrator_chat_with_empty_corpus_returns_refusal_envelope():
    """Decision #16: all channels return [] → CRAG=0 → generator force-refused."""
    orch, deps = _make_orchestrator(hits_per_query=[], crag_score=0.0)
    result = await orch.chat("hello", workspace_id="ws1", conn=None)
    assert result.generation.refused is True
    # CRAG score 0.0 < 0.5 → force_refuse path → reason="insufficient_evidence"
    assert result.generation.refusal_reason == "insufficient_evidence"
    assert len(result.hits) == 0


async def test_orchestrator_search_with_empty_corpus_still_returns_envelope():
    """Decision #16: /search returns an envelope (not 4xx) even with empty corpus."""
    orch, _ = _make_orchestrator(hits_per_query=[], crag_score=0.0)
    result = await orch.search("hello", workspace_id="ws1", conn=None)
    assert isinstance(result, SearchResult)
    assert result.hits == []
    assert result.crag_score == pytest.approx(0.0)


# ===========================================================================
# Factory wiring
# ===========================================================================


def test_orchestrator_make_default_uses_env_factories():
    """The classmethod constructor calls make_query_rewriter, make_embedder,
    make_reranker, make_crag_gate, make_generator under the hood."""
    import os
    # Auto path with no keys → all 4 LLM-bound factories fall back to Identity.
    saved = {k: os.environ.pop(k, None) for k in [
        "KB_QUERY_LLM", "KB_GEMINI_API_KEY", "KB_ANTHROPIC_API_KEY",
        "KB_COHERE_API_KEY", "KB_RERANKER",
    ]}
    try:
        o = Orchestrator.make_default()
        assert o is not None
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v
