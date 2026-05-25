"""Phase 8f — Orchestrator stitching 8a→8e into a coherent query pipeline.

Pipeline shape (per build_tracker §5.15.6):

    query
      ↓
    rewriter (8a)   →  Rewrites(original, step_back, hyde, query2doc)
      ↓
    channels (8b)   ×  4 rewrites  →  RRF → top-30
      ↓
    rerank (8c)     →  top-10
      ↓
    CRAG (8d)       →  crag_score
      ↓
    generate (8e)   (force_refuse=True if crag_score < CRAG_THRESHOLD)
      ↓
    ChatResult / SearchResult

Wave A is "H" (hybrid) mode only. Q/D/E mode classification is Wave B.
Each call also writes one row to `query_log` for audit (Phase 9 consumes
via `/audit`).
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from pydantic import BaseModel, Field

from kb.embeddings import Embedder, make_embedder
from kb.query.channels import run_all_channels
from kb.query.citations import (
    build_citations_for_hits,
    distinct_modalities,
    fetch_file_metas,
    build_citation,
)
from kb.query.crag import CRAG_THRESHOLD, CragGate, make_crag_gate
from kb.query.faithfulness import (
    FaithfulnessGate,
    FaithfulnessResult,
    make_faithfulness_gate,
    should_regenerate,
)
from kb.query.generate import (
    Citation,
    GenerationResult,
    Generator,
    make_generator,
)
from kb.query.rerank import Reranker, make_reranker
from kb.query.rewriter import QueryRewriter, Rewrites, make_query_rewriter
from kb.query.rrf import DEFAULT_K, Hit, rrf_fuse


# Phase 8 overall decision #3 / #4 — top-K after fusion / after rerank.
_POST_FUSION_TOP_K = 30
_POST_RERANK_TOP_K = 10


class SearchResult(BaseModel):
    """`/search` response shape — retrieval inspector, no generation."""

    query_id: str
    query: str
    rewrites: dict[str, str]
    hits: list[Hit] = Field(default_factory=list)
    crag_score: float = 0.0
    latency_ms: int = 0


class ChatResult(BaseModel):
    """`/chat` response shape — full pipeline."""

    query_id: str
    query: str
    rewrites: dict[str, str] = Field(default_factory=dict)
    generation: GenerationResult
    hits: list[Hit] = Field(default_factory=list)
    crag_score: float = 0.0
    latency_ms: int = 0
    # B3 / WA-8 — HHEM-style faithfulness gate verdict.
    faithfulness_verdict: str | None = None       # one of FAITHFULNESS_VERDICTS
    faithfulness_score: float | None = None       # 0.0 - 1.0
    faithfulness_regenerations: int = 0
    faithfulness_model_id: str | None = None
    # B3 / WA-7 — denormalized distinct modalities for dashboard filtering.
    citation_modalities: list[str] = Field(default_factory=list)


class Orchestrator:
    """Wires rewriter + channels + rerank + CRAG + generator into one call.

    Components are injected for testability; `make_default()` builds a real
    orchestrator from the per-module factories.
    """

    def __init__(
        self,
        *,
        rewriter: QueryRewriter,
        embedder: Embedder,
        reranker: Reranker,
        crag: CragGate,
        generator: Generator,
        faithfulness: FaithfulnessGate | None = None,
        run_channels: Any = run_all_channels,
        crag_threshold: float = CRAG_THRESHOLD,
    ) -> None:
        self._rewriter = rewriter
        self._embedder = embedder
        self._reranker = reranker
        self._crag = crag
        self._generator = generator
        # B3 / WA-8 — faithfulness gate (default Identity = always-pass).
        self._faithfulness = faithfulness or make_faithfulness_gate()
        self._run_channels = run_channels
        self._crag_threshold = crag_threshold

    @classmethod
    def make_default(cls) -> "Orchestrator":
        """Build an orchestrator from the env-driven factories."""
        return cls(
            rewriter=make_query_rewriter(),
            embedder=make_embedder(),
            reranker=make_reranker(),
            crag=make_crag_gate(),
            generator=make_generator(),
            faithfulness=make_faithfulness_gate(),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        workspace_id: str,
        conn: Any = None,
    ) -> SearchResult:
        """Run rewriter → channels × rewrites → RRF → rerank → CRAG.

        Returns reranked top-10 + CRAG score. No generation.
        """
        t0 = time.monotonic()
        query_id = str(uuid.uuid4())

        rewrites = await self._rewriter.rewrite(query)
        hits = await self._retrieve_and_rerank(
            query=query,
            rewrites=rewrites,
            workspace_id=workspace_id,
            conn=conn,
        )
        crag_score = await self._crag.assess(query, hits)

        latency_ms = int((time.monotonic() - t0) * 1000)

        return SearchResult(
            query_id=query_id,
            query=query,
            rewrites=self._rewrites_to_dict(rewrites),
            hits=hits,
            crag_score=crag_score,
            latency_ms=latency_ms,
        )

    async def chat(
        self,
        query: str,
        *,
        workspace_id: str,
        conn: Any = None,
    ) -> ChatResult:
        """Run search → CRAG-gated generation → HHEM faithfulness gate.

        When CRAG < threshold, generator is force-refused so the response
        shape is consistent (always a `GenerationResult`).

        After generation we enrich citations via Design 5's polymorphic
        builder, then run the faithfulness gate. On 'refused' verdict we
        regenerate up to MAX_REGENERATIONS times before surfacing the
        refusal (architecture §6 step 9).
        """
        t0 = time.monotonic()
        query_id = str(uuid.uuid4())

        rewrites = await self._rewriter.rewrite(query)
        hits = await self._retrieve_and_rerank(
            query=query,
            rewrites=rewrites,
            workspace_id=workspace_id,
            conn=conn,
        )
        crag_score = await self._crag.assess(query, hits)

        force_refuse = crag_score < self._crag_threshold

        # ---- Generation + faithfulness retry loop ----
        from kb.query.faithfulness import MAX_REGENERATIONS

        regenerations = 0
        generation = await self._generator.generate(
            query, hits, force_refuse=force_refuse
        )
        faithfulness = await self._assess_faithfulness(generation, hits, conn)
        while (
            should_regenerate(faithfulness.verdict, regenerations)
            and not generation.refused
        ):
            regenerations += 1
            generation = await self._generator.generate(
                query, hits, force_refuse=force_refuse
            )
            faithfulness = await self._assess_faithfulness(generation, hits, conn)

        if faithfulness.verdict == "refused" and not generation.refused:
            # Out of retries — abstain (architecture §6 step 9 final branch).
            generation = generation.model_copy(update={
                "refused": True,
                "refusal_reason": "faithfulness_gate_refused",
            })

        # ---- Citation enrichment (Design 5) ----
        await self._enrich_citations(generation, hits, conn)
        modalities = distinct_modalities(
            self._iter_rich_citations(generation.citations)
        )

        latency_ms = int((time.monotonic() - t0) * 1000)

        return ChatResult(
            query_id=query_id,
            query=query,
            rewrites=self._rewrites_to_dict(rewrites),
            generation=generation,
            hits=hits,
            crag_score=crag_score,
            latency_ms=latency_ms,
            faithfulness_verdict=faithfulness.verdict,
            faithfulness_score=faithfulness.score,
            faithfulness_regenerations=regenerations,
            faithfulness_model_id=faithfulness.model_id or None,
            citation_modalities=modalities,
        )

    async def _assess_faithfulness(
        self,
        generation: GenerationResult,
        hits: list[Hit],
        conn: Any,
    ) -> FaithfulnessResult:
        """Run the faithfulness gate. When the generator refused upstream
        (no_hits / insufficient_evidence / parse_error / llm_error) we mark
        the gate 'skipped' — there's no answer to check."""
        if generation.refused or not (generation.answer or "").strip():
            return FaithfulnessResult(
                verdict="skipped", score=0.0,
                notes="generator refused upstream", model_id="",
            )
        snippets = [
            (c.snippet_preview or "") for c in generation.citations
        ]
        if not any(snippets):
            # Fallback: ground on the top-K reranked hits' snippets.
            snippets = [(h.snippet or "") for h in hits[:5]]
        return await self._faithfulness.assess(
            generation.answer, snippets,
            model_id_hint=generation.model_id,
        )

    async def _enrich_citations(
        self,
        generation: GenerationResult,
        hits: list[Hit],
        conn: Any,
    ) -> None:
        """Populate Design 5 polymorphic fields (modality, ref, authority,
        doc_status, chain_id, label, confidence) on each Citation in-place
        — only for citations that are not already enriched by the LLM."""
        if not generation.citations or conn is None:
            return
        hit_by_id = {str(h.id): h for h in hits}
        file_ids = [
            (h.metadata or {}).get("file_id")
            for c in generation.citations
            for h in [hit_by_id.get(c.hit_id)]
            if h is not None
        ]
        metas = await fetch_file_metas(
            conn, file_ids=[f for f in file_ids if f]
        )
        for c in generation.citations:
            if c.modality:
                # LLM (or upstream Identity stub) already supplied a
                # modality — respect it.
                continue
            hit = hit_by_id.get(c.hit_id)
            if hit is None:
                continue
            file_id = (hit.metadata or {}).get("file_id") or c.file_id
            meta = metas.get(file_id) if file_id else None
            rich = build_citation(hit, meta)
            c.modality = rich.modality
            c.ref = rich.ref
            c.label = c.label or rich.label
            c.authority = c.authority if c.authority is not None else rich.authority
            c.doc_status = c.doc_status or rich.doc_status
            c.chain_id = c.chain_id or rich.chain_id
            c.confidence = c.confidence if c.confidence is not None else rich.confidence

    @staticmethod
    def _iter_rich_citations(citations: list[Citation]):
        """Adapter — yields objects with .modality so distinct_modalities()
        works on either RichCitation or our extended Citation."""
        for c in citations:
            if c.modality:
                yield c

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _retrieve_and_rerank(
        self,
        *,
        query: str,
        rewrites: Rewrites,
        workspace_id: str,
        conn: Any,
    ) -> list[Hit]:
        """Fan out 4 rewrites × 6 channels → RRF → rerank → top-10."""
        rewrite_texts = self._iter_rewrites(rewrites)

        # Batch-embed all 4 rewrites in one call (dense channels need vectors).
        embeddings = await self._embedder.embed_batch(rewrite_texts)

        all_lists: list[list[Hit]] = []
        for rewrite_text, emb in zip(rewrite_texts, embeddings):
            channel_results = await self._run_channels(
                conn,
                workspace_id=workspace_id,
                query=rewrite_text,
                query_vec=emb.vector,
            )
            # `channel_results` is dict[str, list[Hit]] — collect per-channel lists.
            for channel_hits in channel_results.values():
                all_lists.append(channel_hits)

        # RRF (k=60) → top-30 (decision #5).
        fused = rrf_fuse(all_lists, k=DEFAULT_K)[:_POST_FUSION_TOP_K]

        # Rerank → top-10 (decision #6).
        reranked = await self._reranker.rerank(
            query, fused, top_k=_POST_RERANK_TOP_K
        )
        return reranked

    @staticmethod
    def _iter_rewrites(rewrites: Rewrites) -> list[str]:
        """Return the 4 query variants as a list of strings."""
        return [
            rewrites.original,
            rewrites.step_back,
            rewrites.hyde,
            rewrites.query2doc,
        ]

    @staticmethod
    def _rewrites_to_dict(rewrites: Rewrites) -> dict[str, str]:
        return {
            "original": rewrites.original,
            "step_back": rewrites.step_back,
            "hyde": rewrites.hyde,
            "query2doc": rewrites.query2doc,
        }


__all__ = [
    "Orchestrator",
    "SearchResult",
    "ChatResult",
    "Citation",
    "GenerationResult",
]
