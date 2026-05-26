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
from kb.query.conflict_resolution import (
    build_conflict_prompt_block,
    persist_unresolved_conflicts,
    resolve_conflicts_for_hits,
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
from kb.query.context_resolver import (
    ContextResolution,
    ContextResolver,
    make_context_resolver,
)
from kb.query.intent import IntentClassifier, IntentResult, make_intent_classifier
from kb.query.mode_router import QModeNotImplementedError, apply_mode
from kb.query.planner import Plan, Planner, make_planner
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
    # B4a — intent + planner observability (also persisted in query_log).
    intent: str | None = None
    intent_confidence: float | None = None
    mode: str | None = None
    plan: dict[str, Any] | None = None
    # B6a — conversation memory (Design 8).
    session_id: str | None = None
    resolved_query: str | None = None
    context_resolution: dict[str, Any] | None = None


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
    # B4a — intent + planner observability.
    intent: str | None = None
    intent_confidence: float | None = None
    mode: str | None = None
    plan: dict[str, Any] | None = None
    # B6a — conversation memory.
    session_id: str | None = None
    resolved_query: str | None = None
    context_resolution: dict[str, Any] | None = None
    turn_index: int | None = None
    # R1 — Design 2 conflict resolutions surfaced for the UI. Each entry
    # describes one detected (entity, predicate) conflict and which rule
    # picked the winner. Empty list when no chained-doc disagreements were
    # found. Citations are independently tagged with `superseded=true` on
    # the loser side so the UI can render in-line annotations.
    conflict_resolutions: list[dict[str, Any]] = Field(default_factory=list)


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
        intent_classifier: IntentClassifier | None = None,
        planner: Planner | None = None,
        context_resolver: ContextResolver | None = None,
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
        # B4a / WA-9 + WA-10 — intent classifier + planner (Identity defaults).
        self._intent_classifier = intent_classifier or make_intent_classifier()
        self._planner = planner or make_planner()
        # B6a / WA-12 — conversation memory anaphora resolver.
        self._context_resolver = context_resolver or make_context_resolver()
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
            intent_classifier=make_intent_classifier(),
            planner=make_planner(),
            context_resolver=make_context_resolver(),
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
        requested_mode: str | None = None,
    ) -> SearchResult:
        """Run intent → planner → rewriter → channels → RRF → rerank →
        mode router → CRAG. Returns reranked top-10 + CRAG score.
        No generation.
        """
        t0 = time.monotonic()
        query_id = str(uuid.uuid4())

        # B4a — intent classifier + planner ahead of retrieval.
        intent = await self._intent_classifier.classify(query)
        plan = await self._planner.plan(
            query, intent, requested_mode=requested_mode,
        )

        rewrites = await self._rewriter.rewrite(query)
        hits = await self._retrieve_and_rerank(
            query=query,
            rewrites=rewrites,
            workspace_id=workspace_id,
            conn=conn,
        )
        # B4a — apply mode-conditional routing. Q-mode raises until B4b.
        hits = await apply_mode(
            plan, hits,
            workspace_id=workspace_id, query=query, conn=conn,
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
            intent=intent.label,
            intent_confidence=intent.confidence,
            mode=plan.mode,
            plan=plan.to_dict(),
        )

    async def chat(
        self,
        query: str,
        *,
        workspace_id: str,
        conn: Any = None,
        requested_mode: str | None = None,
        session_id: str | None = None,
    ) -> ChatResult:
        """Run context resolution → intent → planner → search → mode router
        → CRAG-gated generation → HHEM faithfulness gate → persist turn.

        When `session_id` is provided AND the session exists, the
        anaphora resolver rewrites the query using the 3-tier ChatContext
        before intent classification (Design 8 step 0.5). The final turn
        is persisted to chat_turns; the session's carry-forward state
        is rolled.
        """
        t0 = time.monotonic()
        query_id = str(uuid.uuid4())

        # B6a — context resolution. Skips quietly when no session_id /
        # no prior context.
        resolved_query, ctx_resolution = await self._resolve_context(
            query, session_id=session_id, conn=conn,
        )
        effective_query = resolved_query or query

        intent = await self._intent_classifier.classify(effective_query)
        plan = await self._planner.plan(
            effective_query, intent, requested_mode=requested_mode,
        )

        rewrites = await self._rewriter.rewrite(effective_query)
        hits = await self._retrieve_and_rerank(
            query=effective_query,
            rewrites=rewrites,
            workspace_id=workspace_id,
            conn=conn,
        )
        try:
            hits = await apply_mode(
                plan, hits,
                workspace_id=workspace_id, query=effective_query, conn=conn,
            )
        except QModeNotImplementedError as exc:
            # Q-mode pipeline ships in B4b; return a refusal envelope so
            # the API stays stable.
            return self._q_mode_refusal_envelope(
                query_id=query_id, query=query,
                rewrites=rewrites, intent=intent, plan=plan,
                latency_ms=int((time.monotonic() - t0) * 1000),
                reason=str(exc),
            )

        crag_score = await self._crag.assess(effective_query, hits)

        force_refuse = crag_score < self._crag_threshold

        # ---- R1 — Design 2 conflict resolution ----
        # Skip when we're about to refuse (no point computing conflicts
        # for an empty answer) or there's no DB connection (test path).
        conflict_resolutions = []
        conflict_context = None
        if not force_refuse and conn is not None and hits:
            try:
                conflict_resolutions = await resolve_conflicts_for_hits(
                    conn, hits,
                )
                if conflict_resolutions:
                    conflict_context = build_conflict_prompt_block(
                        conflict_resolutions,
                    ) or None
                    # Best-effort persistence of `unresolved` for the
                    # Needs-attention dashboard. Wrapped in SAVEPOINTs
                    # in persist_unresolved_conflicts so a failure
                    # doesn't poison the outer transaction.
                    await persist_unresolved_conflicts(
                        conn,
                        workspace_id=workspace_id,
                        resolutions=conflict_resolutions,
                    )
            except Exception:
                # Conflict resolution is additive — never let it break
                # the chat path. Logging is at module-level inside the
                # helper.
                import logging
                logging.getLogger(__name__).warning(
                    "conflict resolution skipped", exc_info=True,
                )
                conflict_resolutions = []
                conflict_context = None

        # ---- Generation + faithfulness retry loop ----
        from kb.query.faithfulness import MAX_REGENERATIONS

        regenerations = 0
        generation = await self._generator.generate(
            effective_query, hits, force_refuse=force_refuse,
            conflict_context=conflict_context,
        )
        faithfulness = await self._assess_faithfulness(generation, hits, conn)
        while (
            should_regenerate(faithfulness.verdict, regenerations)
            and not generation.refused
        ):
            regenerations += 1
            generation = await self._generator.generate(
                effective_query, hits, force_refuse=force_refuse,
                conflict_context=conflict_context,
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

        # R1 — tag citations whose source doc lost a conflict so the UI
        # can render a `superseded` ribbon. Runs AFTER citation
        # enrichment so the file_id mapping the generator returns is
        # already canonicalised.
        if conflict_resolutions and generation.citations:
            self._tag_superseded_citations(
                generation.citations, conflict_resolutions,
            )

        modalities = distinct_modalities(
            self._iter_rich_citations(generation.citations)
        )

        latency_ms = int((time.monotonic() - t0) * 1000)

        # B6a — persist the turn + roll the session's carry-forward state.
        turn_index = await self._persist_turn(
            conn=conn, workspace_id=workspace_id,
            session_id=session_id, original_query=query,
            resolved_query=resolved_query, ctx_resolution=ctx_resolution,
            generation=generation, query_log_id=query_id,
        )

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
            intent=intent.label,
            intent_confidence=intent.confidence,
            mode=plan.mode,
            plan=plan.to_dict(),
            session_id=session_id,
            resolved_query=resolved_query,
            context_resolution=(
                ctx_resolution.to_dict() if ctx_resolution else None
            ),
            turn_index=turn_index,
            conflict_resolutions=[
                {
                    "entity_id": r.entity_id,
                    "predicate": r.predicate,
                    "resolution": r.resolution,
                    "picked_value": r.picked_value,
                    "picked_doc_id": (
                        r.picked_candidate.doc_id if r.picked_candidate else None
                    ),
                    "loser_doc_ids": [c.doc_id for c in r.losers],
                    "loser_values": [c.value for c in r.losers],
                    "notes": r.notes,
                }
                for r in conflict_resolutions
            ],
        )

    def _q_mode_refusal_envelope(
        self,
        *,
        query_id: str,
        query: str,
        rewrites: Rewrites,
        intent: IntentResult,
        plan: Plan,
        latency_ms: int,
        reason: str,
    ) -> ChatResult:
        """Build a stable refusal envelope when Q-mode is requested before
        the B4b pipeline lands. Keeps /chat's response shape unchanged."""
        gen = GenerationResult(
            answer="",
            citations=[],
            refused=True,
            refusal_reason="q_mode_not_implemented",
            model_id="planner",
        )
        return ChatResult(
            query_id=query_id,
            query=query,
            rewrites=self._rewrites_to_dict(rewrites),
            generation=gen,
            hits=[],
            crag_score=0.0,
            latency_ms=latency_ms,
            faithfulness_verdict="skipped",
            faithfulness_score=0.0,
            faithfulness_regenerations=0,
            faithfulness_model_id=None,
            citation_modalities=[],
            intent=intent.label,
            intent_confidence=intent.confidence,
            mode=plan.mode,
            plan=plan.to_dict(),
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
            hit = hit_by_id.get(c.hit_id)
            if hit is None:
                continue
            # Always backfill file_id from the hit metadata — the LLM
            # routinely emits citations with file_id=null even though
            # the hit metadata has it. Without this, R1's superseded
            # tagging can't find a match and the citations.py modality
            # routing falls back to the generic "chunk" envelope.
            hit_file_id = (hit.metadata or {}).get("file_id")
            if hit_file_id and not c.file_id:
                c.file_id = hit_file_id

            if c.modality:
                # LLM (or upstream Identity stub) already supplied a
                # modality — respect it but make sure file_id is set
                # (which we just did above).
                continue
            file_id = c.file_id
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

    @staticmethod
    def _tag_superseded_citations(
        citations: list[Citation],
        resolutions: list[Any],
    ) -> None:
        """Mark citations whose source doc was a loser in a conflict.

        For each ResolvedConflict where a rule fired (chain / status /
        authority / recency), any citation whose `file_id` matches one
        of the loser candidates' `doc_id` gets:
          - superseded=True
          - superseded_by_doc_id=<picked doc_id>
          - conflict_resolution=<rule name>

        For `unresolved` cases we DON'T tag — neither side won, and the
        prompt instructed the model to surface both. UI can read the
        absence-of-supersession as "both shown side-by-side".
        """
        if not citations or not resolutions:
            return

        # Build a map: loser_doc_id → (winner_doc_id, rule). Last write
        # wins if the same doc appears in multiple resolutions (rare;
        # would mean the file lost on multiple predicates — taking the
        # most recently iterated rule is fine for Wave A).
        loser_to_winner: dict[str, tuple[str, str]] = {}
        for r in resolutions:
            if r.resolution in ("consensus", "unresolved"):
                continue
            picked = r.picked_candidate
            if picked is None:
                continue
            for c in r.losers:
                loser_to_winner[c.doc_id] = (picked.doc_id, r.resolution)

        if not loser_to_winner:
            return

        for citation in citations:
            if not citation.file_id:
                continue
            winner = loser_to_winner.get(citation.file_id)
            if winner is None:
                continue
            citation.superseded = True
            citation.superseded_by_doc_id = winner[0]
            citation.conflict_resolution = winner[1]

    async def _resolve_context(
        self,
        query: str,
        *,
        session_id: str | None,
        conn: Any,
    ) -> tuple[str | None, ContextResolution | None]:
        """B6a — load ChatContext + run anaphora resolver. Returns
        (resolved_query, ctx_resolution) tuple. (None, None) when no
        session_id supplied or session doesn't exist."""
        if not session_id or conn is None:
            return (None, None)
        from kb.domain.chat_memory import build_chat_context
        try:
            context = await build_chat_context(conn, session_id=session_id)
        except Exception:  # noqa: BLE001
            return (None, None)
        if context is None:
            return (None, None)
        try:
            resolution = await self._context_resolver.resolve(query, context)
        except Exception:  # noqa: BLE001
            return (None, None)
        return (resolution.resolved_query, resolution)

    async def _persist_turn(
        self,
        *,
        conn: Any,
        workspace_id: str,
        session_id: str | None,
        original_query: str,
        resolved_query: str | None,
        ctx_resolution: ContextResolution | None,
        generation: GenerationResult,
        query_log_id: str,
    ) -> int | None:
        """B6a — append a chat_turns row and roll the session's
        carry-forward state. Returns the new turn_index. Silently no-ops
        when session_id is None / conn is None / writes fail."""
        if not session_id or conn is None:
            return None
        from kb.domain.chat_memory import (
            insert_turn,
            read_session,
            update_session_carry_forward,
        )
        # Confirm the session exists in this workspace (cheap belt-and-braces).
        try:
            session = await read_session(conn, session_id=session_id)
        except Exception:  # noqa: BLE001
            return None
        if session is None:
            return None

        citations_payload = [
            c.model_dump(mode="json") for c in (generation.citations or [])
        ]
        context_used = (
            ctx_resolution.to_dict() if ctx_resolution
            else {"resolved_query": resolved_query}
        )
        try:
            _, turn_index = await insert_turn(
                conn,
                workspace_id=workspace_id,
                session_id=session_id,
                user_query=original_query,
                resolved_query=resolved_query,
                answer=generation.answer,
                citations=citations_payload,
                context_used=context_used,
                query_log_id=query_log_id,
            )
        except Exception:  # noqa: BLE001
            return None

        # Roll carry-forward state. We append any new entities from
        # ctx_resolution to the session's existing list.
        if ctx_resolution and (
            ctx_resolution.new_entities
            or ctx_resolution.new_filters
            or ctx_resolution.refinement_of_prior
        ):
            new_entities_combined = list(session.carry_forward_entities) + [
                e for e in ctx_resolution.new_entities
                if e not in session.carry_forward_entities
            ]
            merged_filters = {
                **(session.carry_forward_filters or {}),
                **(ctx_resolution.new_filters or {}),
            }
            try:
                await update_session_carry_forward(
                    conn,
                    session_id=session_id,
                    carry_forward_entities=new_entities_combined,
                    carry_forward_filters=merged_filters,
                )
            except Exception:  # noqa: BLE001
                pass

        return turn_index

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
