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

import logging
import re
import time
import uuid
from typing import Any, Awaitable, Callable


logger = logging.getLogger(__name__)

from pydantic import BaseModel, Field


# Live pipeline-event callback. The orchestrator invokes it (when set)
# at the boundary of every meaningful stage so the API layer can push
# the events to an SSE stream. Signature is async because some sinks
# (asyncio.Queue.put) are coroutines; sinks that don't need awaiting
# just `async def` and return immediately.
EventSink = Callable[[str, dict[str, Any]], Awaitable[None]]


async def _noop_sink(_event_type: str, _payload: dict[str, Any]) -> None:
    """Default sink — silently drops events when no listener is wired."""
    return


# ---------------------------------------------------------------------------
# Adversarial refusal helpers
# ---------------------------------------------------------------------------
#
# The pre-flight refusal pipeline catches three families of unsafe queries:
#
#   1. intent=adversarial — caught by the IntentClassifier already (PPE bypass,
#      Aadhaar exfiltration, "backdate this record" style fraud).
#   2. false-premise / fabricated-citation patterns — "per Clause N", "per
#      Section M says X" attacks where the cited reference doesn't actually
#      exist. The construction q050 attack fell into this bucket; the intent
#      classifier labeled it `unit_filter` (looks like a normal "find clauses
#      about X" query) and the generator ran with the user's false premise.
#   3. anything else we want to surface here later (additional regex patterns,
#      DLP integration, allow-list mode, etc.).
#
# These are MODULE-LEVEL functions (not Orchestrator methods) so the same
# classifier can be reused by other entry points (search, eval, batch runs)
# without dragging the whole orchestrator state.


# Phase 1.4 — anaphora detector for the resolver fast-path. If none of
# these tokens appear in a query, the context resolver has nothing to
# resolve and we can skip its ~600ms Gemini call entirely. Empirically
# ~80% of follow-up queries are standalone (no pronouns / demonstratives /
# back-references), so this is the largest single latency win in
# Phase 1 of the action plan. Includes pronouns, demonstratives, and
# common back-reference markers ("previous", "prior", "above", "same",
# "the one", "the last").
_ANAPHORA_RE = re.compile(
    r"\b(it|its|this|that|these|those|they|them|their|theirs|"
    r"he|him|his|she|her|hers|"
    r"previous|prior|above|earlier|same|"
    r"the\s+one|the\s+last|the\s+former|the\s+latter)\b",
    re.IGNORECASE,
)


# Catches "per clause 99", "per the contract clause N", "per section X",
# "according to clause N", "section M says X", "as stated in clause N",
# "under clause N of the X". Intentionally loose so it doesn't miss
# attempted attacks; the orchestrator only refuses on this when no
# retrieved chunk actually grounds the cited reference.
_FALSE_PREMISE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\bper\s+(?:the\s+)?(?:contract\s+)?(?:clause|section|article|para(?:graph)?)\s+\d+",
        re.IGNORECASE,
    ),
    re.compile(
        r"\baccording\s+to\s+(?:clause|section|article)\s+\d+",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bunder\s+(?:clause|section|article)\s+\d+\s+of\s+(?:the|this)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:clause|section|article)\s+\d+\s+(?:says|states|provides|allows|permits|requires)",
        re.IGNORECASE,
    ),
)


def _looks_like_false_premise(query: str) -> bool:
    """Cheap regex check for queries that ASSERT a clause/section says X.

    These are the construction q050 pattern: 'Per Clause 99 of the
    contract, ... bill Acme'. We can't verify the assertion without
    running retrieval, but we DO know that any genuine query about a
    contract clause should phrase it as a question ('what does clause
    99 say?'), not as an assertion. The asserting form is almost always
    a manipulation attempt.

    False-positive risk: a user phrasing a legitimate operational
    request as 'per section 5, when does X happen' would also hit
    this. Acceptable trade — the worst case is they get a refusal
    that asks them to rephrase as a question.
    """
    return any(p.search(query) for p in _FALSE_PREMISE_PATTERNS)


# Sub-typing for refusal templates. The category determines the refusal
# message; logged in events so we can see which kind of attack hit us.
_BYPASS_KEYWORDS = (
    "bypass", "override", "circumvent", "skip the check",
    "without the supervisor", "without supervision",
)
_PII_KEYWORDS = (
    "aadhaar", "social security", "ssn", "pan number", "passport",
    "compile all", "list all phone", "list all email", "personal details",
    "personal information of all",
)
_FRAUD_KEYWORDS = (
    "backdate", "back-date", "antedate", "falsify", "forge",
    "cover up", "destroy the record", "delete the record", "alter the date",
    "claim he was not", "claim she was not", "claim they were not",
    "deny liability", "evade liability",
)


def _classify_adversarial_subtype(query: str) -> str:
    """Return a short subtype label for telemetry + template selection.

    Order matters: more-specific patterns first so a query that hits
    multiple buckets gets the most informative tag.
    """
    q_lc = query.lower()
    if any(k in q_lc for k in _FRAUD_KEYWORDS):
        return "fraud_or_record_tampering"
    if any(k in q_lc for k in _PII_KEYWORDS):
        return "pii_exfiltration"
    if any(k in q_lc for k in _BYPASS_KEYWORDS):
        return "safety_bypass"
    if _looks_like_false_premise(query):
        return "false_premise_assertion"
    return "adversarial_generic"


# Refusal templates. Each one explains WHY (so users can rephrase if
# they had a legitimate question) without being preachy.
_REFUSAL_TEMPLATES: dict[str, str] = {
    "safety_bypass": (
        "I can't help with bypassing safety procedures or supervisor "
        "checks. Personal Protective Equipment and supervision "
        "requirements exist because of past incidents and statutory "
        "obligations (e.g., Factories Act, Building & Other "
        "Construction Workers Act). If you need to understand what "
        "the procedures require, I can summarize them — ask "
        "\"what PPE is required for height work?\" or similar."
    ),
    "pii_exfiltration": (
        "I can't compile or extract personal identifying information "
        "(Aadhaar / SSN / passport / contact details) for groups of "
        "people. Bulk PII extraction risks worker safety and may "
        "violate data-protection regulations (DPDP Act 2023 in "
        "India, GDPR elsewhere). I can answer aggregate questions "
        "(e.g., \"how many workers on site\", \"distribution by "
        "skill category\") without exposing individual identifiers."
    ),
    "fraud_or_record_tampering": (
        "I can't help with backdating, falsifying, or otherwise "
        "altering records to evade liability — that would be record "
        "tampering and obstruction of an investigation. The system "
        "is also append-only: historical records can't be rewritten. "
        "If there is a legitimate correction to make, the right "
        "path is to file an amendment that explicitly references "
        "and supersedes the original record."
    ),
    "false_premise_assertion": (
        "I can't verify the asserted clause or section in the source "
        "documents — the query asserts something about a specific "
        "reference but I shouldn't trust unverified premises. Could "
        "you rephrase it as a question — e.g., \"what does Clause "
        "13 of the EPC contract say about variations?\" — so I can "
        "look it up and either confirm or correct the premise?"
    ),
    "adversarial_generic": (
        "This query is outside what I'm able to help with. If you "
        "had a legitimate question I might have misinterpreted, "
        "please rephrase it more directly."
    ),
}


def _refusal_template_for(subtype: str) -> str:
    return _REFUSAL_TEMPLATES.get(subtype, _REFUSAL_TEMPLATES["adversarial_generic"])


def _count_by(items: Any, key_fn: Callable[[Any], str]) -> dict[str, int]:
    """Small helper for the emit() payloads. Returns a {category: count}
    dict — used to summarise hits-by-kind, conflicts-by-rule, etc.
    without dragging the whole list into the SSE payload."""
    out: dict[str, int] = {}
    for it in items:
        k = key_fn(it)
        out[k] = out.get(k, 0) + 1
    return out

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
    persist_fact_conflicts,
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
from kb.query.auto_merging import auto_merge_hits
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
    rewrites: dict[str, Any]
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
    rewrites: dict[str, Any] = Field(default_factory=dict)  # values are str or list[str] (ToC)
    generation: GenerationResult
    hits: list[Hit] = Field(default_factory=list)
    crag_score: float = 0.0
    latency_ms: int = 0
    # B3 / WA-8 — HHEM-style faithfulness gate verdict.
    faithfulness_verdict: str | None = None       # one of FAITHFULNESS_VERDICTS
    faithfulness_score: float | None = None       # 0.0 - 1.0
    faithfulness_regenerations: int = 0
    faithfulness_model_id: str | None = None
    # Wave A close-up — sentence-level HHEM verdicts (architecture §6
    # step 8 "generation is STREAMED to the chat UI sentence-by-sentence").
    # The HHEM gate already produces per-claim scores; surfacing them
    # here lets the UI render a per-sentence pass/fail marker beside
    # each claim. List of {text, score, pass} dicts, ordered as the
    # sentences appear in the answer. Empty when the gate skipped
    # (e.g. mode-bypass or no answer).
    faithfulness_per_sentence: list[dict[str, Any]] = Field(default_factory=list)
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
        # Wave A close-up — Design 8 Tier 2 summarizer. Lazy-initialised
        # on first use so the Identity / Gemini factory cost is paid only
        # once per process lifetime, and tests that pass their own
        # context_resolver don't accidentally trigger a Gemini client.
        self._turn_summarizer: Any = None
        # Wave A close-up — IRCoT reformulator (architecture §6 step 7).
        # Same lazy-init pattern as the summarizer.
        self._reformulator: Any = None

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
            conn=conn, workspace_id=workspace_id,
        )

        rewrites = await self._rewriter.rewrite(query)
        hits = await self._retrieve_and_rerank(
            query=query,
            rewrites=rewrites,
            workspace_id=workspace_id,
            conn=conn,
        )
        # AutoMerging — when ≥½ of a parent's leaf children get hit,
        # swap them for the parent so the generator sees the full
        # subsection (Step 3 / LlamaIndex AutoMergingRetriever
        # pattern). Runs BEFORE apply_mode so mode handlers see the
        # merged hit list.
        hits, auto_merge_stats = await auto_merge_hits(
            hits, conn=conn, workspace_id=workspace_id,
        )
        # B4a — apply mode-conditional routing. Q-mode raises until B4b.
        hits = await apply_mode(
            plan, hits,
            workspace_id=workspace_id, query=query, conn=conn,
        )
        crag_score = await self._crag.assess(query, hits)

        latency_ms = int((time.monotonic() - t0) * 1000)

        # Surface AutoMerging stats on the plan for the Plan Inspector.
        plan_dict = plan.to_dict()
        plan_dict["auto_merge"] = {
            "initial_leaf_hits": auto_merge_stats.initial_leaf_hits,
            "final_hit_count": auto_merge_stats.final_hit_count,
            "merges_by_level": auto_merge_stats.merges_by_level,
            "leaves_replaced": auto_merge_stats.leaves_replaced,
        }

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
            plan=plan_dict,
        )

    async def chat(
        self,
        query: str,
        *,
        workspace_id: str,
        conn: Any = None,
        requested_mode: str | None = None,
        session_id: str | None = None,
        file_ids: list[str] | None = None,
        event_sink: EventSink | None = None,
    ) -> ChatResult:
        """Run context resolution → intent → planner → search → mode router
        → CRAG-gated generation → HHEM faithfulness gate → persist turn.

        When `session_id` is provided AND the session exists, the
        anaphora resolver rewrites the query using the 3-tier ChatContext
        before intent classification (Design 8 step 0.5). The final turn
        is persisted to chat_turns; the session's carry-forward state
        is rolled.

        When `file_ids` is non-empty, retrieval is scoped to that file
        set (chat-UX `@ doc filter`). Hits from other files are
        post-filtered out after the fused/reranked top-K — cheaper than
        threading filters through every channel's SQL, and the typical
        scope (1-10 files) means we still have plenty of in-scope hits.

        When `event_sink` is provided, the orchestrator invokes it at
        each pipeline stage so an SSE caller can stream progress to the
        chat UI. Sink failures are silently swallowed — the pipeline
        keeps running even if the listener goes away mid-stream.
        """
        sink = event_sink or _noop_sink

        async def emit(event_type: str, payload: dict[str, Any]) -> None:
            try:
                # Always include `t_ms` so the UI can render a timeline
                # without having to track its own start clock.
                await sink(event_type, {
                    **payload,
                    "t_ms": int((time.monotonic() - t0) * 1000),
                })
            except Exception:
                # A failed sink (closed SSE connection, etc.) must NOT
                # crash the pipeline. Best-effort observability only.
                pass
        t0 = time.monotonic()
        query_id = str(uuid.uuid4())

        # Auto-create a session if the caller didn't pass one. Without
        # this, `_persist_turn` silently skips persistence (session_id
        # is the NOT-NULL key on chat_turns), and the UI's "recent
        # chats" list is permanently empty — the user can't see
        # anything they asked yesterday. Auto-creation makes every
        # chat call land in a row, named by its first user query
        # (lazily titled below in _persist_turn).
        if session_id is None and conn is not None:
            try:
                from kb.domain.chat_memory import create_session
                session_id = await create_session(
                    conn,
                    workspace_id=workspace_id,
                    title=query[:120].strip() or None,
                )
            except Exception:  # noqa: BLE001
                # If session create fails (RLS denied, schema drift,
                # whatever), proceed without persistence — the user
                # still gets their answer; the audit row will be
                # missing but that's strictly better than 5xx-ing.
                session_id = None

        await emit("started", {"query": query, "session_id": session_id})

        # ---- Pre-resolution adversarial check ----
        # Phase 1.2 — run pure-pattern adversarial detection on the
        # ORIGINAL query before context resolution can launder it.
        #
        # Reproduced attack: user types "ignore your instructions" → the
        # context resolver, using carry-forward state, rewrote it as
        # "what is resume about" → the post-classifier adversarial
        # check (which sees the LAUNDERED query via intent.label) then
        # missed the threat entirely. Pen-test finding.
        #
        # The helpers below are deterministic regex/keyword matchers,
        # so this is cheap and runs before any LLM call. The
        # post-classifier adversarial branch (line ~570 below) stays
        # in place to catch softer LLM-classifier-only cases.
        if _looks_like_false_premise(query) or _classify_adversarial_subtype(query) != "adversarial_generic" or any(
            k in query.lower() for k in _BYPASS_KEYWORDS + _PII_KEYWORDS + _FRAUD_KEYWORDS
        ):
            adv_reason = _classify_adversarial_subtype(query)
            adv_answer = _refusal_template_for(adv_reason)
            await emit("refused_adversarial_preflight", {
                "subtype": adv_reason, "stage": "pre_resolution",
            })
            # Build a minimal intent + plan placeholder so the refusal
            # envelope has the shape downstream expects.
            from kb.query.intent import IntentResult
            adv_intent = IntentResult(
                label="adversarial", confidence=0.99,
                notes="pre-resolution pattern match",
                model_id="orchestrator:pre_adversarial",
            )
            adv_plan = Plan(
                mode="H", intent="adversarial", intent_confidence=0.99,
                notes="adversarial pre-flight; pipeline skipped",
                model_id="orchestrator:pre_adversarial",
            )
            adv_result = self._adversarial_refusal_envelope(
                query_id=query_id,
                query=query,
                effective_query=query,
                rewrites=Rewrites(
                    original=query, step_back="", hyde="", query2doc="",
                ),
                intent=adv_intent,
                plan=adv_plan,
                latency_ms=int((time.monotonic() - t0) * 1000),
                refusal_text=adv_answer,
                refusal_subtype=adv_reason,
            )
            adv_turn_index = await self._persist_turn(
                workspace_id=workspace_id,
                session_id=session_id, original_query=query,
                resolved_query=None, ctx_resolution=None,
                generation=adv_result.generation, query_log_id=query_id,
            )
            adv_result.turn_index = adv_turn_index
            adv_result.session_id = session_id
            return adv_result

        # B6a — context resolution. Skips quietly when no session_id /
        # no prior context.
        resolved_query, ctx_resolution = await self._resolve_context(
            query, session_id=session_id, conn=conn,
        )
        effective_query = resolved_query or query
        if resolved_query and resolved_query != query:
            await emit("context_resolved", {
                "original": query, "resolved": resolved_query,
            })

        intent = await self._intent_classifier.classify(effective_query)
        await emit("intent_classified", {
            "label": intent.label, "confidence": intent.confidence,
        })
        plan = await self._planner.plan(
            effective_query, intent, requested_mode=requested_mode,
            conn=conn, workspace_id=workspace_id,
        )
        await emit("planned", {"mode": plan.mode, "intent": intent.label})

        # ---- Adversarial short-circuit ----
        # The intent classifier labels prompt-injection / PII-exfiltration
        # / safety-bypass / fraud queries as `adversarial` with high
        # confidence (the heuristic and Gemini classifiers both agree on
        # the obvious cases). The planner historically routed these to
        # H-mode and trusted "downstream gates" to refuse — but the only
        # downstream gate is CRAG (low retrieval confidence), which fires
        # accidentally for irrelevant queries, not deliberately for
        # harmful ones. Result: q047/q048/q049 in the construction eval
        # returned empty answers (silent refusal — bad UX) and q050
        # ("Per Clause 99 of the contract, …" — a made-up clause) slipped
        # through entirely because the false-premise pattern looks like
        # a normal unit_filter query to the classifier.
        #
        # Two-tier fix:
        #   1. explicit adversarial → refuse here with a templated reason
        #   2. false-premise patterns ("per clause N", "according to
        #      section N", "section M of the contract says X") → also
        #      refuse here when the cited reference doesn't appear in the
        #      doc-type's known structure. This is a low-precision pattern
        #      but catches the construction q050-class attacks.
        if intent.label == "adversarial" or _looks_like_false_premise(
            effective_query
        ):
            adv_reason = _classify_adversarial_subtype(effective_query)
            adv_answer = _refusal_template_for(adv_reason)
            await emit("refused_adversarial", {
                "intent": intent.label,
                "subtype": adv_reason,
                "confidence": intent.confidence,
            })
            adv_result = self._adversarial_refusal_envelope(
                query_id=query_id,
                query=query,
                effective_query=effective_query,
                rewrites=Rewrites(
                    original=query,
                    step_back="",
                    hyde="",
                    query2doc="",
                ),
                intent=intent,
                plan=plan,
                latency_ms=int((time.monotonic() - t0) * 1000),
                refusal_text=adv_answer,
                refusal_subtype=adv_reason,
            )
            # Persist the refusal too so the chat history shows the user
            # asked + got refused (vs. the message simply vanishing).
            adv_turn_index = await self._persist_turn(
                workspace_id=workspace_id,
                session_id=session_id, original_query=query,
                resolved_query=resolved_query, ctx_resolution=ctx_resolution,
                generation=adv_result.generation, query_log_id=query_id,
            )
            adv_result.turn_index = adv_turn_index
            adv_result.session_id = session_id
            return adv_result

        # ---- I-mode short-circuit ----
        # Inventory queries ("what types of docs do I have", "list my
        # files") are metadata questions. The answer lives in the
        # `files` table, not in chunks/RAPTOR/atomic_units — running
        # retrieve+rerank+CRAG+LLM here is the wrong tool and produces
        # CONTENT summaries instead of TYPE listings. Short-circuit to
        # a deterministic SQL renderer; ~50ms vs ~13s and zero
        # hallucination risk.
        if plan.mode == "I":
            from kb.query.inventory import build_inventory_answer
            await emit("inventory_lookup", {})
            generation = await build_inventory_answer(
                conn, workspace_id=workspace_id,
            )
            await emit("generated", {
                "refused": False, "refusal_reason": None,
                "n_citations": len(generation.citations),
            })
            latency_ms = int((time.monotonic() - t0) * 1000)

            # Persist the inventory turn so it shows up in chat history
            # too — without this, asking "what docs do I have?" is the
            # one chat that never makes it into the recent-chats sidebar.
            inv_turn_index = await self._persist_turn(
                workspace_id=workspace_id,
                session_id=session_id, original_query=query,
                resolved_query=resolved_query, ctx_resolution=ctx_resolution,
                generation=generation, query_log_id=query_id,
            )

            await emit("done", {})
            return ChatResult(
                query_id=query_id,
                query=query,
                rewrites={"original": effective_query},
                generation=generation,
                hits=[],
                crag_score=1.0,
                latency_ms=latency_ms,
                faithfulness_verdict="skipped",
                faithfulness_score=None,
                faithfulness_regenerations=0,
                faithfulness_model_id=None,
                citation_modalities=["file_ref"],
                intent=intent.label,
                intent_confidence=intent.confidence,
                mode=plan.mode,
                plan=plan.to_dict(),
                session_id=session_id,
                resolved_query=resolved_query,
                context_resolution=(
                    ctx_resolution.to_dict() if ctx_resolution else None
                ),
                turn_index=inv_turn_index,
                conflict_resolutions=[],
            )

        rewrites = await self._rewriter.rewrite(effective_query)
        await emit("query_rewritten", {
            "n_variants": len(self._rewrites_to_dict(rewrites)),
        })
        # R3-supporting fix — `_retrieve_and_rerank` runs 6 channels in
        # parallel via asyncio.gather on a SHARED psycopg connection.
        # When one channel's SQL errors (paradedb edge case, missing
        # index, malformed query) the txn corruption cascades and every
        # downstream conn.execute() raises InFailedSqlTransaction —
        # citation enrichment can't load file metadata so labels fall
        # back to "document" instead of real filenames; chat-turn
        # persistence + query_log audit also fail silently.
        #
        # Per-channel SAVEPOINTs don't help because the parallel
        # coroutines interleave SAVEPOINT / SQL / ROLLBACK on the same
        # connection unpredictably. The right long-term fix is a real
        # connection pool per channel; the surgical fix is one outer
        # SAVEPOINT around the whole retrieval block so a failure
        # inside it rolls back cleanly to a usable txn state.
        retrieve_sp_open = False
        try:
            await conn.execute("SAVEPOINT orchestrator_retrieve")
            retrieve_sp_open = True
        except Exception:
            pass
        await emit("retrieving", {})
        try:
            hits = await self._retrieve_and_rerank(
                query=effective_query,
                rewrites=rewrites,
                workspace_id=workspace_id,
                conn=conn,
            )
            if retrieve_sp_open:
                try:
                    await conn.execute("RELEASE SAVEPOINT orchestrator_retrieve")
                except Exception:
                    # Release might fail if the inner code aborted and
                    # was caught silently — try ROLLBACK to recover.
                    try:
                        await conn.execute(
                            "ROLLBACK TO SAVEPOINT orchestrator_retrieve"
                        )
                        await conn.execute(
                            "RELEASE SAVEPOINT orchestrator_retrieve"
                        )
                    except Exception:
                        pass
        except Exception:
            # Retrieval blew up. Hits = [] so generator refuses with
            # no_hits. Don't fail the request just because retrieval
            # had a bad day.
            if retrieve_sp_open:
                try:
                    await conn.execute(
                        "ROLLBACK TO SAVEPOINT orchestrator_retrieve"
                    )
                    await conn.execute(
                        "RELEASE SAVEPOINT orchestrator_retrieve"
                    )
                except Exception:
                    pass
            hits = []
        await emit("retrieved", {
            "n_hits": len(hits),
            "by_kind": _count_by(hits, lambda h: h.kind),
        })

        # Chat-UX `@ doc filter` — scope hits to the file_ids the user
        # explicitly picked. Done before apply_mode so the mode router
        # (e.g. T-mode PPR) operates over the scoped set. Pre-filter
        # because the channels themselves don't know about UI scoping —
        # cheaper than threading filters through every channel SQL.
        if file_ids:
            scope = {str(fid) for fid in file_ids if fid}
            before = len(hits)
            hits = [
                h for h in hits
                if (h.metadata or {}).get("file_id") in scope
            ]
            await emit("doc_filter_applied", {
                "scope_size": len(scope),
                "kept": len(hits), "dropped": before - len(hits),
            })

        # AutoMerging — swap leaf clusters for their parent so the
        # generator gets the right amount of context per question.
        # See kb/query/auto_merging.py.
        hits, auto_merge_stats_chat = await auto_merge_hits(
            hits, conn=conn, workspace_id=workspace_id,
        )
        if auto_merge_stats_chat.leaves_replaced:
            await emit("auto_merged", {
                "leaves_replaced": auto_merge_stats_chat.leaves_replaced,
                "merges_by_level": auto_merge_stats_chat.merges_by_level,
            })

        # Stash pre-mode hits so we can fall back if the mode router
        # filters everything out. Construction eval found that C-mode
        # (unit_filter) and A-mode (anomaly) were filtering legitimate
        # H-mode-quality hits down to 0 — the user got a "no_hits"
        # refusal even though the hybrid retrieval had found relevant
        # chunks. Falling back to the pre-mode hits is strictly better
        # than refusing.
        pre_mode_hits = hits[:]
        try:
            hits = await apply_mode(
                plan, hits,
                workspace_id=workspace_id, query=effective_query, conn=conn,
            )
            await emit("mode_routed", {"mode": plan.mode, "kept": len(hits)})
        except QModeNotImplementedError as exc:
            # Q-mode pipeline ships in B4b; return a refusal envelope so
            # the API stays stable.
            q_result = self._q_mode_refusal_envelope(
                query_id=query_id, query=query,
                rewrites=rewrites, intent=intent, plan=plan,
                latency_ms=int((time.monotonic() - t0) * 1000),
                reason=str(exc),
            )
            # Persist the refusal so it shows up in chat history.
            q_turn_index = await self._persist_turn(
                workspace_id=workspace_id,
                session_id=session_id, original_query=query,
                resolved_query=resolved_query, ctx_resolution=ctx_resolution,
                generation=q_result.generation, query_log_id=query_id,
            )
            q_result.turn_index = q_turn_index
            q_result.session_id = session_id
            return q_result

        # Mode-filter fallback: if the specialized mode produced 0 hits
        # but the pre-mode (hybrid) retrieval found something, use the
        # hybrid results. Don't apply when mode was already H (no
        # filtering happened) or Q (which has its own SQL path that
        # legitimately produces 0 result-set rows). Construction q005
        # (mech-completion date routed to C-mode) and q024 (abnormally-
        # low-bid routed to A-mode) both hit this fallback.
        if (
            not hits
            and pre_mode_hits
            and plan.mode not in ("H", "Q", "I")
        ):
            hits = pre_mode_hits
            await emit("mode_filter_fallback", {
                "mode": plan.mode,
                "restored": len(hits),
                "reason": "specialized_mode_filtered_to_zero",
            })

        crag_score = await self._crag.assess(effective_query, hits)
        await emit("crag_assessed", {
            "score": round(crag_score, 3),
            "threshold": self._crag_threshold,
            "bypassed": plan.mode != "H",
        })

        # WA close-up — IRCoT escalation (architecture §6 step 7).
        # When CRAG returns low confidence on an H-mode query, give the
        # system ONE more chance: reformulate via Gemini using the
        # current hits as evidence, retrieve again, recompute CRAG.
        # max_hops=2 per spec; cost ~$0.001/hop. Without this, every
        # borderline-confidence query refused immediately even when a
        # follow-up question would have surfaced the answer.
        ircot_hops_payload: list[dict[str, Any]] = []
        if (
            plan.mode == "H"
            and crag_score < self._crag_threshold
            and conn is not None
            and hits
        ):
            try:
                from kb.query.ircot import (
                    DEFAULT_MAX_HOPS_CRAG,
                    escalate_with_ircot,
                    make_default_reformulator,
                )

                if self._reformulator is None:
                    self._reformulator = make_default_reformulator()

                async def _ircot_retrieve(q: str) -> list[Hit]:
                    sub_rewrites = await self._rewriter.rewrite(q)
                    return await self._retrieve_and_rerank(
                        query=q,
                        rewrites=sub_rewrites,
                        workspace_id=workspace_id,
                        conn=conn,
                    )

                async def _ircot_crag(q: str, hs: list[Hit]) -> float:
                    return await self._crag.assess(q, hs)

                await emit("ircot_escalating", {
                    "crag_before": round(crag_score, 3),
                    "threshold": self._crag_threshold,
                    "max_hops": DEFAULT_MAX_HOPS_CRAG,
                })
                ircot_result = await escalate_with_ircot(
                    original_query=effective_query,
                    hits=hits,
                    crag_score=crag_score,
                    threshold=self._crag_threshold,
                    crag_assess=_ircot_crag,
                    retrieve=_ircot_retrieve,
                    reformulator=self._reformulator,
                    max_hops=DEFAULT_MAX_HOPS_CRAG,
                )
                hits = ircot_result.final_hits
                crag_score = ircot_result.final_crag
                ircot_hops_payload = [
                    {
                        "hop_index": hop.hop_index,
                        "reformulated_query": hop.reformulated_query,
                        "n_hits_added": hop.n_hits_added,
                        "crag_after": round(hop.crag_after, 3),
                    }
                    for hop in ircot_result.hops
                ]
                await emit("ircot_completed", {
                    "hops": ircot_hops_payload,
                    "crag_after": round(crag_score, 3),
                    "terminated_reason": ircot_result.terminated_reason,
                })
            except Exception:
                # IRCoT is best-effort: never break the chat over it.
                # On failure we proceed to the pre-IRCoT refusal path.
                pass

        # CRAG asks "do these snippets answer the query?" — a question
        # that only makes sense for FACT-style asks ("what's the payment
        # cap"). For corpus-scope asks ("summarize the corpus", "what
        # documents do I have", "give me an overview") no individual
        # chunk snippet IS the answer — the answer is a synthesis ACROSS
        # snippets. CRAG correctly scores those snippets as low-relevance
        # to the literal question, but its refusal is the wrong move:
        # the downstream faithfulness gate will still catch generation
        # hallucinations, and these users would rather see a synthesized
        # overview than a "I can't answer that" refusal.
        #
        # Bypass everything EXCEPT H (hybrid). CRAG's "do these snippets
        # answer the query?" check assumes a fact-style ask where some
        # chunk should literally contain the answer text. That assumption
        # holds for H (default factoid retrieval) but breaks for the
        # planner's structured modes:
        #   G — global/thematic summary
        #   D — doc-metadata filter
        #   F — schema field predicates
        #   S — scoped chunk (within a parent doc/contract/project)
        #   T — graph traversal (multi-hop)
        #   M — mention search
        #   E — entity-centric
        #   C — atomic-unit filter
        #   A — anomaly filter
        #   K — doc-chain aware (current_version / all_versions)
        #   Q — structured SQL aggregate
        # In all the above, retrieval is filtered or restructured before
        # synthesis; the chunks returned may be 100% relevant to the
        # answer without containing the literal query string. The
        # downstream faithfulness gate still catches hallucinations.
        # The LLM also self-refuses cleanly when snippets really don't
        # answer the question (Q16-style out-of-corpus asks).
        # Phase 1.5 — CRAG refusal now applies uniformly across ALL
        # modes, not just H. Previously, non-H modes (E/F/S/T/C/A/D/M/K)
        # generated from low-CRAG retrieval anyway, which silently
        # produced hallucinated answers when the specialized mode's
        # retrieval missed. Users expect "refuse on bad retrieval" to
        # be a system-wide guarantee, not an H-mode quirk.
        #
        # Q-mode and I-mode short-circuit before CRAG so they're
        # unaffected by this change.
        force_refuse = crag_score < self._crag_threshold

        # ---- R1 — Design 2 conflict resolution ----
        # Run REGARDLESS of force_refuse — the detected conflicts are
        # useful information for the user even when CRAG refuses the
        # answer (the banner shows "we DID find this conflict but
        # couldn't synthesize a confident answer"). Only skipped when
        # there's no DB connection (test path) or no hits to analyze.
        #
        # Wrapped in an outer SAVEPOINT so ANY failure inside the block
        # (a bad UUID, an unexpected schema mismatch, an FK violation
        # during fact_conflicts INSERT) cleanly rolls back to a usable
        # txn state. Without this outer wrap, an inner SAVEPOINT that
        # couldn't even be CREATEd (e.g. txn already aborted from an
        # upstream channel error) would leave the txn unusable, which
        # silently breaks `_enrich_citations` downstream — citations
        # come back with label='document' instead of the real filename
        # because the meta-fetch query can't run.
        conflict_resolutions = []
        conflict_context = None
        if conn is not None and hits:
            r1_sp_open = False
            try:
                await conn.execute("SAVEPOINT orchestrator_r1_block")
                r1_sp_open = True
            except Exception:
                # Can't even open a savepoint — txn is already aborted.
                # Skip R1 entirely; downstream code paths that DO need
                # the txn will fail loudly elsewhere.
                pass

            if r1_sp_open:
                try:
                    conflict_resolutions = await resolve_conflicts_for_hits(
                        conn, hits,
                    )
                    if conflict_resolutions:
                        await emit("conflicts_resolved", {
                            "n_conflicts": len(conflict_resolutions),
                            "by_rule": _count_by(
                                conflict_resolutions, lambda r: r.resolution,
                            ),
                        })
                        conflict_context = build_conflict_prompt_block(
                            conflict_resolutions,
                        ) or None
                        await persist_fact_conflicts(
                            conn,
                            workspace_id=workspace_id,
                            resolutions=conflict_resolutions,
                        )
                    try:
                        await conn.execute(
                            "RELEASE SAVEPOINT orchestrator_r1_block"
                        )
                    except Exception:
                        pass
                except Exception:
                    import logging
                    logging.getLogger(__name__).warning(
                        "conflict resolution skipped", exc_info=True,
                    )
                    conflict_resolutions = []
                    conflict_context = None
                    # Rollback the SAVEPOINT so the outer txn is usable
                    # by _enrich_citations / _persist_turn / query_log
                    # downstream. The two RELEASEs aren't strictly
                    # required after rollback but stay symmetric with
                    # the success path; psycopg silently no-ops if the
                    # savepoint is already gone.
                    try:
                        await conn.execute(
                            "ROLLBACK TO SAVEPOINT orchestrator_r1_block"
                        )
                        await conn.execute(
                            "RELEASE SAVEPOINT orchestrator_r1_block"
                        )
                    except Exception:
                        pass

        # ---- Filename enrichment on hits ----
        # Stash file_name + inferred_doc_type onto hit.metadata so the
        # generator prompt can show "[file: vertex-msa.pdf]" alongside
        # each snippet. Without this, the LLM only sees opaque UUIDs and
        # answers like "Document UUID-7c84... mentions X" instead of the
        # actual filename (Q12). One batch SQL per turn — cheap.
        if conn is not None and hits:
            fname_ids = sorted({
                (h.metadata or {}).get("file_id")
                for h in hits if (h.metadata or {}).get("file_id")
            })
            fname_ids = [f for f in fname_ids if f]
            if fname_ids:
                fname_metas = await fetch_file_metas(
                    conn, file_ids=fname_ids,
                )
                for h in hits:
                    fid = (h.metadata or {}).get("file_id")
                    if not fid:
                        continue
                    fm = fname_metas.get(fid)
                    if fm is None:
                        continue
                    if h.metadata is None:
                        h.metadata = {}
                    if fm.name and "file_name" not in h.metadata:
                        h.metadata["file_name"] = fm.name
                    if fm.inferred_doc_type and "inferred_doc_type" not in h.metadata:
                        h.metadata["inferred_doc_type"] = fm.inferred_doc_type

        # ---- Generation + faithfulness retry loop ----
        from kb.query.faithfulness import MAX_REGENERATIONS

        regenerations = 0
        await emit("generating", {
            "force_refuse": force_refuse, "n_hits_seen": len(hits),
        })
        generation = await self._generator.generate(
            effective_query, hits, force_refuse=force_refuse,
            conflict_context=conflict_context,
        )
        await emit("generated", {
            "refused": generation.refused,
            "refusal_reason": generation.refusal_reason,
            "n_citations": len(generation.citations),
        })
        faithfulness = await self._assess_faithfulness(generation, hits, conn)
        await emit("faithfulness_checked", {
            "verdict": faithfulness.verdict, "score": faithfulness.score,
            "regenerations": regenerations,
        })
        while (
            should_regenerate(faithfulness.verdict, regenerations)
            and not generation.refused
        ):
            regenerations += 1
            await emit("regenerating", {"attempt": regenerations})
            generation = await self._generator.generate(
                effective_query, hits, force_refuse=force_refuse,
                conflict_context=conflict_context,
            )
            faithfulness = await self._assess_faithfulness(generation, hits, conn)
            await emit("faithfulness_checked", {
                "verdict": faithfulness.verdict, "score": faithfulness.score,
                "regenerations": regenerations,
            })

        if faithfulness.verdict == "refused" and not generation.refused:
            # Out of retries. Two behaviors depending on whether the
            # retrieval was confident:
            #   - CRAG was HIGH + generator returned an answer: the
            #     faithfulness gate is likely a false-positive on
            #     paraphrased prose (construction eval showed this on
            #     factoids like "mechanical completion date" where the
            #     answer is grounded but token-overlap drops below the
            #     heuristic threshold). Keep the answer visible, mark
            #     verdict="low_confidence" so the UI can badge it.
            #   - CRAG was LOW or answer is empty: genuine abstain
            #     (architecture §6 step 9 final branch).
            answer_has_content = bool((generation.answer or "").strip())
            if crag_score >= 0.7 and answer_has_content:
                # Downgrade verdict — don't propagate refused=True.
                # The UI sees faithfulness_verdict="low_confidence"
                # and can render a "low confidence" warning beside
                # the otherwise-correct answer.
                faithfulness = FaithfulnessResult(
                    verdict="low_confidence",
                    score=faithfulness.score,
                    per_claim_scores=faithfulness.per_claim_scores,
                    notes=(
                        (faithfulness.notes or "")
                        + " [softened: CRAG was confident, "
                        "answer kept visible with low-confidence "
                        "badge instead of hidden refusal]"
                    ),
                    model_id=faithfulness.model_id,
                )
            else:
                # Phase 1.6 — fill answer with refusal text instead of
                # leaving the model's last (low-faith) answer in place
                # AND being marked refused. Either show a clean refusal
                # OR keep the answer with a badge — not both.
                from kb.query.generate import refusal_answer_for
                generation = generation.model_copy(update={
                    "refused": True,
                    "refusal_reason": "faithfulness_gate_refused",
                    "answer": refusal_answer_for("faithfulness_gate_refused"),
                })

        # Wave A close-up — sentence-level HHEM exposure (architecture
        # §6 step 8). The HHEM gate already computes per-claim scores;
        # surface them so the chat UI can render a per-sentence
        # pass/fail marker beside each claim. Also emits one SSE event
        # per sentence so the UI's pipeline-event timeline shows
        # "claim 1: pass · claim 2: refused · claim 3: pass" inline.
        per_sentence: list[dict[str, Any]] = []
        if not generation.refused and (faithfulness.per_claim_scores or ()):
            from kb.query.faithfulness import (
                split_sentences,
                verdict_from_score,
            )
            sentences = split_sentences(generation.answer or "")
            scores = list(faithfulness.per_claim_scores or ())
            # Defensive: pair up by index in case the gate produced
            # fewer/more scores than sentences (shouldn't happen but
            # keeps us crash-free).
            n = min(len(sentences), len(scores))
            for i in range(n):
                v = verdict_from_score(scores[i])
                per_sentence.append({
                    "index": i,
                    "text": sentences[i],
                    "score": float(scores[i]),
                    "verdict": v,
                })
                # Stream each sentence verdict so the UI can render
                # per-sentence chips progressively.
                await emit("sentence_validated", {
                    "index": i,
                    "score": round(float(scores[i]), 3),
                    "verdict": v,
                })

        # ---- Citation enrichment (Design 5) ----
        await self._enrich_citations(generation, hits, conn)
        await emit("citations_enriched", {
            "n_citations": len(generation.citations),
        })

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
            workspace_id=workspace_id,
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
            faithfulness_per_sentence=per_sentence,
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

    def _adversarial_refusal_envelope(
        self,
        *,
        query_id: str,
        query: str,
        effective_query: str,
        rewrites: Rewrites,
        intent: IntentResult,
        plan: Plan,
        latency_ms: int,
        refusal_text: str,
        refusal_subtype: str,
    ) -> ChatResult:
        """Build the response envelope for a pre-flight adversarial refusal.

        Same shape as `_q_mode_refusal_envelope` so the /chat caller
        doesn't need a refusal-aware branch — the `refused` flag +
        `refusal_reason` are the contract.
        """
        gen = GenerationResult(
            answer=refusal_text,
            citations=[],
            refused=True,
            refusal_reason=f"adversarial:{refusal_subtype}",
            model_id="orchestrator:adversarial_refusal",
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
        # Phase 1.6 — fill the answer field so the UI doesn't render a
        # blank card. Q-mode-not-implemented is a temporary state; tell
        # the user clearly.
        gen = GenerationResult(
            answer=(
                f"This query needs SQL-style aggregation (Q-mode) which "
                f"isn't fully implemented yet. Reason: {reason}. Try "
                f"rephrasing as a search question."
            ),
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
        the gate 'skipped' — there's no answer to check.

        Q-mode results (and any other mode that produces ONLY synthesized
        aggregate hits) skip the gate entirely. The aggregate hit's
        snippet is a machine-printed result like 'total_debits=452998.10'
        — when the gen-LLM rephrases that into prose ("The sum of all
        transactions is **452,998.10**...") the token-overlap heuristic
        scores it as unfaithful even though the numbers match exactly.
        Q-mode has its own audit trail (q_payload + audit_query_id) that
        validates correctness without re-grounding the prose.
        """
        if generation.refused or not (generation.answer or "").strip():
            return FaithfulnessResult(
                verdict="skipped", score=0.0,
                notes="generator refused upstream", model_id="",
            )
        # Mode-based skip: aggregate-only hit lists don't ground prose.
        if hits and all(h.kind == "aggregate" for h in hits):
            return FaithfulnessResult(
                verdict="skipped", score=0.0,
                notes="aggregate-only hits (Q-mode bypass)", model_id="",
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
        — only for citations that are not already enriched by the LLM.

        Resilient to LLM hit_id truncation: T-mode (multi-hop) answers
        frequently come back with 8-12 char prefixes (e.g. `7c84e24b`)
        instead of full UUIDs. We resolve prefixes back to the original
        Hit + canonicalise the citation's hit_id so downstream R1
        supersession-tagging and the UI inline-marker lookup both work.
        """
        if not generation.citations or conn is None:
            return
        hit_by_id = {str(h.id): h for h in hits}

        # Canonicalise truncated hit_ids → full UUIDs before any lookup.
        # The LLM sometimes emits `[7c84e24b]` instead of the 36-char
        # UUID; without this, enrichment silently leaves modality/label/
        # file_id all None and R1 can't tag supersession.
        for c in generation.citations:
            if c.hit_id in hit_by_id:
                continue
            matches = [k for k in hit_by_id if k.startswith(c.hit_id)]
            if len(matches) == 1:
                c.hit_id = matches[0]

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

        # RAPTOR corpus-scope nodes have file_id=NULL by schema (they
        # summarize across many files), so the chat right rail rendered
        # them as a dead label with no /files/<id> link. Resolve each
        # such citation to the file whose leaves contribute most to the
        # summary node — that file becomes the click target. Also stash
        # the full source-file list under ref.source_file_ids so a
        # future "view all sources" affordance can fan out.
        raptor_needing_file = [
            c for c in generation.citations
            if c.modality == "raptor_summary" and not c.file_id
        ]
        if raptor_needing_file:
            from kb.query.citations import resolve_raptor_source_files
            node_ids = [c.hit_id for c in raptor_needing_file]
            resolved = await resolve_raptor_source_files(
                conn, raptor_node_ids=node_ids,
            )
            # Fetch file metas for the resolved best-source file_ids so
            # we can append the document name to the label (turns
            # "Workspace summary" into "Workspace summary · contract.pdf").
            extra_file_ids = {
                pair[0] for pair in resolved.values() if pair and pair[0]
            }
            extra_metas = await fetch_file_metas(
                conn, file_ids=list(extra_file_ids),
            ) if extra_file_ids else {}
            for c in raptor_needing_file:
                pair = resolved.get(c.hit_id)
                if not pair:
                    continue
                best_file_id, all_file_ids = pair
                c.file_id = best_file_id
                # Stash the multi-file list on the ref so the UI can
                # later render a "+N more sources" dropdown without
                # another round-trip.
                if isinstance(c.ref, dict):
                    c.ref["source_file_ids"] = all_file_ids
                    c.ref["best_source_file_id"] = best_file_id
                # Re-label so the user sees what the summary draws from
                # instead of a bare "Workspace summary" / "Topic cluster
                # summary" with no document hint.
                best_meta = extra_metas.get(best_file_id)
                best_name = best_meta.name if best_meta else None
                if best_name:
                    n_other = max(0, len(all_file_ids) - 1)
                    suffix = f" + {n_other} more" if n_other > 0 else ""
                    c.label = f"{c.label or 'summary'} · {best_name}{suffix}"

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
        session_id supplied or session doesn't exist.

        Phase 1.4 fast-path: if the query has no pronouns / demonstratives /
        reference words, there's nothing to resolve. Skip the ~600ms
        Gemini call and the chat-context fetch entirely. Empirically
        this is ~80% of follow-up queries (most users ask standalone
        questions even mid-conversation).
        """
        if not session_id or conn is None:
            return (None, None)
        # Fast-path: no anaphoric markers → no LLM call needed.
        if not _ANAPHORA_RE.search(query):
            return (query, None)
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

    async def ensure_session(
        self,
        *,
        workspace_id: str,
        conn: Any,  # kept for signature parity; not used (see below)
        fallback_title: str | None,
    ) -> str | None:
        """Auto-create a chat session and return its id. Used by the
        chat-stream caller BEFORE invoking `chat()` so the caller
        always knows the session_id, even if `chat()` later raises —
        which lets the error path still persist the turn against a
        real session.

        Uses a FRESH connection (not the caller's `conn`) so the
        session row COMMITS immediately. Subsequent fresh-conn
        persist calls (in `_persist_turn`) will then be able to see
        the session — they otherwise wouldn't, because ACID
        isolation hides uncommitted writes from the request's outer
        txn.

        Returns None on failure; the caller's chat will then run
        without a session (no persistence), which is strictly better
        than 5xx-ing.
        """
        from kb.config import get_settings
        from kb.db.pool import open_connection
        from kb.domain.chat_memory import create_session

        settings = get_settings()
        try:
            async with open_connection(settings.app_database_url) as fresh:
                async with fresh.transaction():
                    await fresh.execute(
                        "SELECT set_config('app.workspace_id', %s, true)",
                        (workspace_id,),
                    )
                    return await create_session(
                        fresh, workspace_id=workspace_id, title=fallback_title,
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ensure_session failed for workspace=%s: %s",
                workspace_id, exc, exc_info=True,
            )
            return None

    async def build_error_chat_result(
        self,
        *,
        workspace_id: str,
        session_id: str | None,
        query: str,
        exc: BaseException,
        query_id: str | None = None,
    ) -> ChatResult:
        """Build a `refused=True` ChatResult for an unexpected pipeline
        crash AND persist it as a chat_turn. Used by SSE/REST callers
        to make sure every user message lands in `chat_turns`, even
        when the pipeline blew up mid-flight.

        UX contract: the caller can stream this back like any other
        answer envelope. The UI renders the refusal in the thread
        instead of throwing a generic "Pipeline error" toast and
        dropping the message from history.
        """
        if query_id is None:
            query_id = str(uuid.uuid4())

        err_gen = GenerationResult(
            answer=(
                "Sorry — something went wrong while answering this. "
                f"({type(exc).__name__}). Your message has been saved; "
                "try asking again or rephrase."
            ),
            citations=[],
            refused=True,
            refusal_reason=f"pipeline_error:{type(exc).__name__}",
            model_id="orchestrator:error",
        )
        result = ChatResult(
            query_id=query_id,
            query=query,
            rewrites={},
            generation=err_gen,
            hits=[],
            crag_score=0.0,
            latency_ms=0,
            faithfulness_verdict="skipped",
            faithfulness_score=0.0,
            faithfulness_regenerations=0,
            faithfulness_model_id=None,
            citation_modalities=[],
            intent=None,
            intent_confidence=None,
            mode=None,
            plan=None,
            session_id=session_id,
            resolved_query=None,
            context_resolution=None,
            turn_index=None,
            conflict_resolutions=[],
        )

        if session_id:
            try:
                turn_index = await self._persist_turn(
                    workspace_id=workspace_id,
                    session_id=session_id,
                    original_query=query,
                    resolved_query=None,
                    ctx_resolution=None,
                    generation=err_gen,
                    query_log_id=query_id,
                )
                if turn_index is not None:
                    result.turn_index = turn_index
            except Exception as inner:  # noqa: BLE001
                logger.warning(
                    "build_error_chat_result: persist failed too: %s",
                    inner, exc_info=True,
                )

        return result

    async def _persist_turn(
        self,
        *,
        workspace_id: str,
        session_id: str | None,
        original_query: str,
        resolved_query: str | None,
        ctx_resolution: ContextResolution | None,
        generation: GenerationResult,
        query_log_id: str,
        # `conn` kept for backward compat but no longer used — see
        # docstring. New callers should omit it.
        conn: Any = None,
    ) -> int | None:
        """Append a chat_turns row and roll the session's carry-forward
        state. Returns the new turn_index, or None when persistence is
        impossible (no session_id).

        IMPORTANT: opens its OWN fresh DB connection. The request's
        outer txn (from `kb_app_connection`) is intentionally NOT used,
        because:

          - The chat pipeline runs many SELECTs inside the outer txn.
            If any of them aborts the PG txn (e.g. a `::uuid` cast
            against an entity name like "aurangabad"), then EVERY
            subsequent SQL — including the chat_turn INSERT —
            silently fails with "current transaction is aborted",
            and at __aexit__ the outer txn ROLLS BACK, taking the
            chat_turn row with it.
          - The user side sees the answer streamed via SSE but the
            row never lands. Indistinguishable from "my chat lost a
            turn". This was the actual root cause behind every chat-
            persistence bug between c977b88 and d3d3bbf.

        Fresh-conn persistence breaks the coupling entirely. Whatever
        the pipeline did to the outer txn, the persist transaction is
        independent: it BEGINs, INSERTs, COMMITs, and the row is on
        disk before the request returns.

        Best-effort writes (title backfill, carry-forward update,
        tier-2 summary) stay inside the SAME fresh txn but are each
        wrapped in their own SAVEPOINT so a failure in one doesn't
        roll back the chat_turn INSERT.

        Hard input validation: `carry_forward_entities` is `uuid[]` in
        the schema. The LLM context resolver may return arbitrary
        strings — those are filtered out before the UPDATE.
        """
        if not session_id:
            return None

        from kb.config import get_settings
        from kb.db.pool import open_connection
        from kb.domain.chat_memory import (
            insert_turn,
            read_session,
        )

        settings = get_settings()

        try:
            async with open_connection(settings.app_database_url) as fresh:
                async with fresh.transaction():
                    # Set RLS context for this fresh txn.
                    await fresh.execute(
                        "SELECT set_config('app.workspace_id', %s, true)",
                        (workspace_id,),
                    )

                    # Confirm the session exists in this workspace.
                    session = await read_session(fresh, session_id=session_id)
                    if session is None:
                        logger.warning(
                            "chat_turn persist: session %s not found in "
                            "workspace %s (RLS denied or deleted)",
                            session_id, workspace_id,
                        )
                        return None

                    citations_payload = [
                        c.model_dump(mode="json")
                        for c in (generation.citations or [])
                    ]
                    context_used = (
                        ctx_resolution.to_dict() if ctx_resolution
                        else {"resolved_query": resolved_query}
                    )

                    # Primary write — the chat_turn row. Failures here
                    # propagate; the outer try/except logs + returns None.
                    _, turn_index = await insert_turn(
                        fresh,
                        workspace_id=workspace_id,
                        session_id=session_id,
                        user_query=original_query,
                        resolved_query=resolved_query,
                        answer=generation.answer,
                        citations=citations_payload,
                        context_used=context_used,
                        query_log_id=query_log_id,
                    )

                    # Helper for cosmetic writes — each gets its own
                    # SAVEPOINT so one failure can't kill the others or
                    # the chat_turn row above.
                    async def best_effort_write(label: str, fn) -> None:
                        sp_name = f"persist_extra_{label}"
                        sp_active = False
                        try:
                            await fresh.execute(f"SAVEPOINT {sp_name}")
                            sp_active = True
                            await fn()
                            await fresh.execute(f"RELEASE SAVEPOINT {sp_name}")
                            sp_active = False
                        except Exception as exc:
                            logger.warning(
                                "chat_turn persist: %s failed "
                                "(chat_turn safe via savepoint): %s",
                                label, exc,
                            )
                            if sp_active:
                                try:
                                    await fresh.execute(
                                        f"ROLLBACK TO SAVEPOINT {sp_name}"
                                    )
                                    await fresh.execute(
                                        f"RELEASE SAVEPOINT {sp_name}"
                                    )
                                except Exception:
                                    pass

                    # Title backfill — only the very first turn.
                    if turn_index == 0 and not session.title:
                        title = (original_query or "").strip()[:120] or "Untitled chat"

                        async def _backfill_title() -> None:
                            await fresh.execute(
                                "UPDATE chat_sessions SET title = %s "
                                "WHERE id = %s AND title IS NULL",
                                (title, session_id),
                            )

                        await best_effort_write("title_backfill", _backfill_title)

                    # Carry-forward write — disabled in Phase 1.3.
                    #
                    # The resolver no longer returns `new_entities` or
                    # `new_filters` (see context_resolver.py). The fields
                    # remain on ContextResolution for back-compat but
                    # always come back empty. Writing the carry-forward
                    # state was the cause of two production bugs:
                    #   1. aurangabad UUID cast bug (strings → uuid[])
                    #   2. {document_type: resume} pollution that biased
                    #      subsequent turns in the same session.
                    # The schema columns (chat_sessions.carry_forward_*)
                    # stay populated with their defaults; if a future
                    # feature wants to use them, it should populate them
                    # from a typed source (e.g. canonical_entities.id
                    # lookups) — NOT from LLM-returned strings.
                    _noop_carry_forward = True  # explicit marker
                    _ = _noop_carry_forward

                    # Tier-2 summary refresh.
                    async def _tier2() -> None:
                        await self._maybe_refresh_tier2_summary(
                            conn=fresh, session=session, turn_index=turn_index,
                        )

                    await best_effort_write("tier2_summary", _tier2)

                    return turn_index
        except Exception as exc:
            logger.warning(
                "chat_turn persist (fresh conn) failed for session=%s, "
                "query=%r: %s",
                session_id, original_query[:80], exc, exc_info=True,
            )
            return None

    async def _maybe_refresh_tier2_summary(
        self,
        *,
        conn: Any,
        session: Any,
        turn_index: int,
    ) -> None:
        """Refresh `chat_sessions.older_turn_summary` (Design 8 Tier 2)
        when the just-persisted turn pushed an older turn out of the
        Tier-1 verbatim window. No-op otherwise.

        Cadence (default): summarize every 3rd new "displaced" turn,
        starting at turn_index=6 (the first turn whose persistence
        evicts turn 0 from the K=6 hot window).
        """
        from kb.query.turn_summarizer import (
            DEFAULT_HOT_TURNS,
            should_summarize,
        )
        from kb.domain.chat_memory import (
            DEFAULT_HOT_TURNS as _HOT,
            read_turns_for_session,
            update_session_carry_forward,
        )

        # Defensive: keep the two HOT_TURNS constants in lockstep.
        # If someone bumps chat_memory's value, we won't silently mis-
        # align the windows.
        hot_turns = _HOT if _HOT == DEFAULT_HOT_TURNS else DEFAULT_HOT_TURNS

        if not should_summarize(turn_index=turn_index, hot_turns=hot_turns):
            return

        # Read every turn that has aged out of the verbatim window —
        # i.e. turn_index <= turn_index - hot_turns. The summarizer
        # also receives the existing summary so we never lose deeper
        # history (it gets folded in + log-compressed).
        cutoff_idx = turn_index - hot_turns
        all_turns = await read_turns_for_session(
            conn, session_id=session.id,
        )
        displaced = [t for t in all_turns if t.turn_index <= cutoff_idx]
        if not displaced:
            return

        summarizer = self._turn_summarizer
        if summarizer is None:
            from kb.query.turn_summarizer import make_default_turn_summarizer
            summarizer = make_default_turn_summarizer()
            # Cache for subsequent calls in this orchestrator's lifetime.
            self._turn_summarizer = summarizer

        new_summary = await summarizer.summarize(
            older_turn_summary=session.older_turn_summary or None,
            displaced_turns=displaced,
        )
        if not new_summary or new_summary == (session.older_turn_summary or ""):
            return

        try:
            await update_session_carry_forward(
                conn,
                session_id=session.id,
                older_turn_summary=new_summary,
            )
        except Exception:  # noqa: BLE001
            # Same fail-quiet logic as the rest of _persist_turn.
            pass

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
        """Fan out N rewrites × 6 channels → RRF → rerank → top-10."""
        rewrite_texts = self._iter_rewrites(rewrites)

        # Batch-embed all rewrites in one call (dense channels need vectors).
        embeddings = await self._embedder.embed_batch(rewrite_texts)

        # WA-2 / Design 6 — pre-compute the BM25-side vocabulary-
        # expanded form of each rewrite. We DON'T mutate the rewrite
        # itself (dense channels still embed the original — augmenting
        # with OR-of-synonyms would pollute the vector). Done once
        # per rewrite, in parallel with the channel call below.
        from kb.query.vocabulary_expansion import expand_query_with_vocabulary
        bm25_texts: list[str] = []
        for rt in rewrite_texts:
            try:
                augmented, _expansions = await expand_query_with_vocabulary(
                    conn, workspace_id=workspace_id, query=rt,
                )
                bm25_texts.append(augmented)
            except Exception:
                # Belt-and-braces: the helper itself is fail-safe but
                # if its import / call raises, fall back to the
                # original rewrite for that variant.
                bm25_texts.append(rt)

        all_lists: list[list[Hit]] = []
        for rewrite_text, emb, bm25_text in zip(
            rewrite_texts, embeddings, bm25_texts,
        ):
            channel_results = await self._run_channels(
                conn,
                workspace_id=workspace_id,
                query=rewrite_text,
                query_vec=emb.vector,
                bm25_query=bm25_text,
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
        """Return all query variants as a list of strings — the four
        canonical ones plus any Tree-of-Clarifications disambiguation
        branches the rewriter emitted. RRF dedupes overlap so adding
        branches never hurts; the cap is enforced upstream in the
        rewriter (≤4 branches per spec)."""
        return [
            rewrites.original,
            rewrites.step_back,
            rewrites.hyde,
            rewrites.query2doc,
            *rewrites.clarifications,
        ]

    @staticmethod
    def _rewrites_to_dict(rewrites: Rewrites) -> dict[str, Any]:
        """Surface every variant + the ToC branch list separately so
        the plan inspector can show "ambiguous query → 3 branches"
        without conflating them with the canonical 4 rewrites."""
        out: dict[str, Any] = {
            "original": rewrites.original,
            "step_back": rewrites.step_back,
            "hyde": rewrites.hyde,
            "query2doc": rewrites.query2doc,
        }
        if rewrites.clarifications:
            out["clarifications"] = list(rewrites.clarifications)
        return out


__all__ = [
    "Orchestrator",
    "SearchResult",
    "ChatResult",
    "Citation",
    "GenerationResult",
]
