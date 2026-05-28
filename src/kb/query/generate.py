"""Phase 8e — Astute generation (cite-or-refuse).

Per architecture §6 step 8 + Astute RAG paper (Wang et al. 2024, arXiv
2410.07176). Single defensive-prompt Gemini call over reranked top-10
hits → structured `GenerationResult(answer, citations, refused, ...)`.

Wave A scope:
- 2-impl factory: GeminiGenerator (real) + IdentityGenerator (templated echo).
- Single async call; no streaming (architecture's sentence-by-sentence
  HHEM streaming is Wave B + Phase 9 SSE infrastructure).
- Citation envelope minimal: {hit_id, kind, file_id, snippet_preview, score}.
  Rich envelope (label, authority, doc_status, chain_id, modality,
  lineage_path) deferred to Wave B.

Refusal modes (decisions #6/#7/#8/#9/#10):
- force_refuse=True (orchestrator passes when CRAG < threshold)
    → skip LLM, return refusal envelope (reason="insufficient_evidence")
- hits=[] → skip LLM, refusal (reason="no_hits")
- LLM exception → refusal (reason="llm_error")
- Bad JSON / missing fields → refusal (reason="parse_error")
- Model self-refuses by returning {refused: true} → respected
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Protocol

from pydantic import BaseModel, Field

from kb.query.rrf import Hit


logger = logging.getLogger(__name__)


# Brace-balanced JSON extractor — when Gemini wraps its JSON in stray
# prose ("Sure, here's the answer: { ... }") or appends a trailer
# ("} Hope that helps!"), naive json.loads fails. We scan for the first
# `{`, then walk character-by-character tracking brace depth + string
# state, returning the first balanced `{...}` block.
#
# Conservative on purpose: if the extractor can't find a clean block,
# we return None and the caller falls through to refusal.
def _extract_json_object(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if escape:
            escape = False
            continue
        if c == "\\":
            escape = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


# Phase 1.6 — refusal answer text.
#
# Pre-fix, every refusal path returned `answer=""`. The UI then rendered
# a BLANK CARD with no explanation — user clicks send, gets a void.
# Map each `refusal_reason` to a one-line user-facing message so the
# refusal renders with helpful text.
_REFUSAL_ANSWER_TEXT: dict[str, str] = {
    "insufficient_evidence": (
        "I couldn't find enough relevant information to answer this "
        "confidently. Try rephrasing or asking about a specific document."
    ),
    "no_hits": (
        "No documents matched this question. Try different terms, or "
        "upload more documents on the Upload page."
    ),
    "parse_error": (
        "I had trouble formatting the answer. Try asking again or "
        "breaking the question into smaller parts."
    ),
    "truncated": (
        "The answer ran past the length cap. Try a more specific question, "
        "or break it into multiple smaller asks."
    ),
    "model_refused": (
        "The model declined to answer this. If this was unexpected, try "
        "rephrasing — sometimes phrasing triggers safety filters."
    ),
    "llm_error": (
        "Something went wrong while calling the model. Please try again."
    ),
    "empty_response": (
        "The model returned an empty response. Please try again."
    ),
    "faithfulness_gate_refused": (
        "I couldn't verify the answer was grounded in the retrieved "
        "documents. Try rephrasing — the snippets may not actually "
        "contain the answer."
    ),
}


def refusal_answer_for(reason: str | None) -> str:
    """Map a refusal_reason to a user-readable answer string. Returns a
    safe fallback for unknown reasons. Pipeline-error reasons (prefixed
    with `pipeline_error:`) and adversarial subtypes have their own
    custom messages built upstream — don't overwrite those."""
    if not reason:
        return (
            "I can't confidently answer this from the available evidence. "
            "Try rephrasing or asking about a specific document."
        )
    if reason.startswith("pipeline_error:") or reason.startswith("adversarial:"):
        return ""  # caller already supplied a custom message
    return _REFUSAL_ANSWER_TEXT.get(reason, (
        "I can't confidently answer this. Try rephrasing or providing "
        "more context."
    ))


# Decision #2: top-K post-rerank seen by the generator.
_TOP_N_HITS = 10

# Decision #11: max output tokens.
#
# Bumped 2048 → 8000 after the chat-UX audit found that summarize /
# overview queries hit MAX_TOKENS mid-response — Gemini was being
# asked to echo back `snippet_preview` (~200 chars × 10 citations) in
# the JSON output. The simplified citation schema below also helps,
# but a larger cap means longer answers don't truncate either.
#
# Bumped 8000 → 16000 after a follow-up failure: a compound query
# ("can you talk more about Vertex Industries / What more info do we
# have") combined with conflict-resolution context (3 conflicts
# inlined into the prompt) drove an answer past the 8K cap and
# truncated JSON mid-response → parse_error. Gemini Flash 2.5
# supports much larger output budgets; 16K leaves comfortable
# headroom without affecting cost (we're billed on actual usage).
_MAX_OUTPUT_TOKENS = 16000

# Decision #15: Astute defensive system prompt.
#
# Citation schema simplified to JUST `hit_id` per citation. Pre-fix the
# prompt asked the model to also echo back kind/file_id/snippet_preview/
# score, all of which the server already has on the Hit list and
# back-fills in `_parse_result`. Echoing them cost ~400 output tokens
# per citation and was the main driver of MAX_TOKENS truncation on
# synthesis answers.
_SYSTEM_PROMPT = (
    "You are a careful question-answering assistant grounded in the "
    "retrieved snippets below. Follow this discipline:\n"
    "1. Read each snippet's [hit_id] and snippet text.\n"
    "2. Compose an answer using ONLY information present in the snippets. "
    "Do NOT invent facts.\n"
    "\n"
    "## Answer the EXACT thing asked\n"
    "Parse the user's question carefully. If they ask for an ADDRESS or "
    "LOCATION, return the address — not the project name. If they ask "
    "for a NAME, return the name. If they ask for a DATE, return the "
    "date FIRST then any context. If they ask 'how many', return the "
    "number FIRST. If the snippets contain the project name but you "
    "were asked the location, search again — don't substitute one "
    "field for another. Common substitution traps:\n"
    "   asked 'location/address/site' → don't return project name\n"
    "   asked 'cost/value/amount' → don't return contract number\n"
    "   asked 'date' → don't return doc number\n"
    "   asked 'who is X / who did Y' → don't return what X is doing\n"
    "\n"
    "## Address EVERY part of a multi-part question\n"
    "If the question has multiple parts (compound: 'when AND why', "
    "'what AND who', 'list X with their Y'), explicitly address ALL "
    "parts. Don't drop sub-questions. If asked 'who is the architect "
    "of record' you must return BOTH the person's name AND the firm.\n"
    "\n"
    "## Inventory the snippets BEFORE answering\n"
    "Before composing your answer, mentally inventory the snippets. "
    "Identify every distinct ITEM relevant to the question — every "
    "revision (Rev A / B / C), every change order (CO-005 / CO-018), "
    "every party (Acme / Mahalaxmi / Phoenix MEP / Sundar / Sai), "
    "every event (incident initial / investigation / corrective), "
    "every value (initial / after CO-005 / final), every RFI, every "
    "schedule. The retrieved snippets often contain references to "
    "MULTIPLE such items; the user asked one question but expects to "
    "see ALL distinct items that satisfy it.\n"
    "\n"
    "## Enumerate, don't truncate — applies to MORE than just 'list' queries\n"
    "Many questions LOOK single-answer but have multi-item answers in "
    "the corpus:\n"
    "   'What is the contract value?' — usually multiple: initial, after "
    "CO-005, after CO-018, final.\n"
    "   'Current status of X?' — usually has sub-components: closed, "
    "outstanding, in DLP.\n"
    "   'Any docs with X?' — list ALL that match, not just the first.\n"
    "   'What changed?' / 'Walk the chain' / 'Trace history' — every "
    "change/version.\n"
    "   'Summarize project' — covers contract, construction phase, "
    "safety, completion.\n"
    "   'Tell me about [entity]' — every facet of that entity in the "
    "corpus (e.g. Mahalaxmi Infra AND Mahalaxmi Equipment if both "
    "exist).\n"
    "   'Any X > Y' — list ALL items that exceed the threshold.\n"
    "When in doubt, ENUMERATE rather than abbreviate. Bullets are fine; "
    "a list of 5 short items beats one verbose paragraph that covers "
    "only one.\n"
    "\n"
    "## Conflict resolution — surface BOTH sides + the winner\n"
    "When snippets disagree about the same fact (e.g. one doc says "
    "'worker error', another says 'system failure'; one says wall at "
    "Grid C, another at Grid D), your answer MUST:\n"
    "  (a) acknowledge the conflict explicitly,\n"
    "  (b) state which version is AUTHORITATIVE based on these signals "
    "(in priority order): explicit chain supersession (later revision "
    "/ amendment supersedes earlier), doc_status='superseded' loses to "
    "'live', signed/approved beats draft, investigation report beats "
    "preliminary report, corrective-action beats first-filed,\n"
    "  (c) explain WHY in one sentence — e.g. 'The investigation "
    "report supersedes the initial 24-hour report; its root-cause "
    "finding of PPE non-availability is authoritative.'\n"
    "Never report the LOSING side as the answer without naming the "
    "winning side.\n"
    "\n"
    "## Compute when asked\n"
    "If the question asks for a calculation the snippets contain the "
    "inputs for (sum, count, days between two dates, percent change), "
    "do the arithmetic yourself and show the result. Only refuse "
    "calculation if the inputs themselves are missing.\n"
    "\n"
    "## Format the answer field as readable Markdown\n"
    "Use bullets for lists, **bold** for key terms / numbers / dates, "
    "headings (## / ###) when the answer has 3+ distinct sections, "
    "tables for comparisons across documents, and short paragraphs "
    "(3-4 sentences max). Plain prose is fine for one-fact answers; "
    "structure helps when the answer has multiple parts. ALWAYS finish "
    "your sentences — never leave a clause mid-thought.\n"
    "\n"
    "## Citation format\n"
    "Cite every claim inline using the [hit_id] marker for the snippet "
    "that supports it. The marker stays INSIDE prose — e.g. \"payment "
    "terms are net-45 [a8b21618]\" — not as a separate line. When "
    "MULTIPLE snippets support the same claim, emit ONE bracket per "
    "snippet back-to-back: \"...are net-45 [a8b21618] [3f782075]\" — "
    "do NOT comma-join inside a single bracket (\"[a8b21618, "
    "3f782075]\" is wrong; the UI renders that as a raw UUID string "
    "instead of a clickable chip). Every citation MUST point to a "
    "snippet that actually supports the claim — don't add citations "
    "for atmosphere.\n"
    "\n"
    "## When to refuse\n"
    "If the snippets do not support a confident answer to the query, "
    "refuse: return JSON with refused=true and a brief refusal_reason. "
    "It is better to refuse than to guess. But refuse ONLY when the "
    "answer is genuinely absent — don't refuse just because the "
    "exact phrasing differs (paraphrase is fine) or because some "
    "supporting detail is missing (give the core answer + note the "
    "gap).\n"
    "\n"
    "## Output schema\n"
    "Return STRICTLY a JSON object matching: "
    '{"answer": str, "citations": [{"hit_id": str}], '
    '"refused": bool, "refusal_reason": str|null}. '
    "Only include hit_ids you actually cited in `answer`."
)


# ---------------------------------------------------------------------------
# Pydantic models (decision #4)
# ---------------------------------------------------------------------------


class Citation(BaseModel):
    """Citation envelope (Design 5 polymorphic — B3 / WA-7).

    The original five fields stay mandatory for back-compat. The new fields
    are all optional and populated by the orchestrator's enrichment pass
    (kb.query.citations.build_citations_for_hits)."""

    hit_id: str
    kind: str
    file_id: str | None = None
    snippet_preview: str = ""
    score: float = 0.0
    # B3 polymorphic extensions — None when the generator emits the bare
    # envelope, populated when the orchestrator enriches via citations.py.
    modality: str | None = None
    ref: dict[str, Any] | None = None
    label: str | None = None
    authority: float | None = None
    doc_status: str | None = None
    chain_id: str | None = None
    confidence: float | None = None
    # R1 — populated by the orchestrator's conflict-resolution pass.
    # `superseded=True` means another doc in the same chain currently
    # holds the authoritative value for a predicate this citation's
    # source disagrees on. The UI grays out / annotates these.
    superseded: bool = False
    # When supersession fires, names the doc that won. Helpful for the
    # UI's "Amendment supersedes MSA on payment_terms" hint.
    superseded_by_doc_id: str | None = None
    # Optional human-friendly reason ("chain", "status", "authority",
    # "recency", "unresolved") — surfaced in the UI tooltip.
    conflict_resolution: str | None = None


class GenerationResult(BaseModel):
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    refused: bool = False
    refusal_reason: str | None = None
    model_id: str = ""


class Generator(Protocol):
    async def generate(
        self,
        query: str,
        hits: list[Hit],
        *,
        force_refuse: bool = False,
        conflict_context: str | None = None,
    ) -> GenerationResult: ...


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _citations_from_hits(hits: list[Hit], limit: int = 3) -> list[Citation]:
    """Synthesize Citations from Hit list — used by Identity stub and as a
    fallback when LLM omits citations."""
    out: list[Citation] = []
    for h in hits[:limit]:
        metadata = h.metadata or {}
        out.append(
            Citation(
                hit_id=str(h.id),
                kind=str(h.kind),
                file_id=metadata.get("file_id"),
                snippet_preview=(h.snippet or "")[:200],
                score=float(h.score),
            )
        )
    return out


def _build_user_prompt(
    query: str,
    hits: list[Hit],
    *,
    conflict_context: str | None = None,
) -> str:
    """Build the user message — top-N hits per decision #2.

    When `conflict_context` is non-empty (R1 wiring), it's injected
    BEFORE the retrieved-snippets block so the model can apply the
    pre-computed resolution decisions when phrasing the answer."""
    blocks: list[str] = []
    for h in hits[:_TOP_N_HITS]:
        # Per-snippet ceiling — matched to the channel-layer _SNIPPET_MAX
        # in channels.py (currently 2500). Keep the two in sync: chunks
        # arrive from BM25/dense already truncated to that ceiling, so
        # any smaller value here would re-truncate. Kept as a defensive
        # second cap in case a channel ever forgets to truncate.
        snippet = (h.snippet or "")[:2500]
        # Surface file_name / doc_type when the orchestrator stashed
        # them on hit.metadata. Without filenames in the prompt the LLM
        # is forced to refer to documents by opaque UUID — Q12 ("which
        # documents mention Vertex") then answers with **uuid**: blurb
        # instead of the readable filename. With filenames it produces
        # ["vertex-msa.pdf — describes the master service agreement", …].
        md = h.metadata or {}
        fname = md.get("file_name")
        dtype = md.get("inferred_doc_type")
        ctx_bits = []
        if fname:
            ctx_bits.append(f"file={fname}")
        if dtype:
            ctx_bits.append(f"type={dtype}")
        ctx_suffix = (" [" + ", ".join(ctx_bits) + "]") if ctx_bits else ""
        # Use full UUID as hit_id so callers can resolve back to the Hit.
        blocks.append(
            f"[hit_id: {h.id}] (kind={h.kind}){ctx_suffix} {snippet}"
        )
    snippets = "\n\n".join(blocks)

    conflict_block = (
        (conflict_context.strip() + "\n\n") if conflict_context and conflict_context.strip() else ""
    )
    return (
        f"Query: {query}\n\n"
        f"{conflict_block}"
        f"Retrieved snippets (top {min(len(hits), _TOP_N_HITS)}):\n"
        f"{snippets}\n\n"
        f"Return JSON only."
    )


def _parse_result(
    raw: str,
    hits: list[Hit],
    model_id: str,
    finish_reason: str | None = None,
) -> GenerationResult:
    """Parse Gemini's JSON output into GenerationResult. Tolerant + fail-safe.

    Decision #9: any parse failure → refusal with reason='parse_error'
    (or 'truncated' when finish_reason indicates MAX_TOKENS — that's a
    distinct, actionable failure and surfacing it as parse_error hides
    the real fix, which is to bump _MAX_OUTPUT_TOKENS or summarize the
    conflict-resolution context).
    Decision #8: respects model's own refusal flag.

    Parse pipeline (each step recovers if the prior one fails):
      1. Strip ```json fenced wrappers.
      2. Try json.loads directly.
      3. If that fails, extract first balanced {...} block (handles
         Gemini wrapping its JSON in prose or appending trailers).
      4. If that ALSO fails, log + return refusal.

    Every failure path logs the raw_text preview + finish_reason so an
    operator can diagnose without re-running the query. Pre-fix the
    raw response was silently dropped on parse failure.
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 2 and lines[-1].strip() == "```":
            lines = lines[1:-1]
        else:
            lines = lines[1:]
        text = "\n".join(lines)

    def _refused_parse(reason: str, note: str = "") -> GenerationResult:
        # MAX_TOKENS gets its own bucket — distinct refusal_reason +
        # actionable log message. Other parse failures stay bucketed
        # under "parse_error".
        actual_reason = (
            "truncated" if finish_reason == "MAX_TOKENS" else reason
        )
        logger.warning(
            "generate: %s (finish_reason=%s, raw_len=%d, note=%s); "
            "raw[:500]=%r",
            actual_reason, finish_reason, len(raw or ""),
            note, (raw or "")[:500],
        )
        return GenerationResult(
            answer=refusal_answer_for(actual_reason),
            citations=[],
            refused=True,
            refusal_reason=actual_reason,
            model_id=model_id,
        )

    data: Any
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        # Recovery attempt: peel off prose / trailers around the JSON.
        block = _extract_json_object(text)
        if block is None:
            return _refused_parse("parse_error", f"json.loads={exc}; no_block")
        try:
            data = json.loads(block)
            logger.info(
                "generate: recovered via brace-balanced extractor "
                "(stripped %d leading + %d trailing chars)",
                text.find(block),
                len(text) - text.find(block) - len(block),
            )
        except json.JSONDecodeError as exc2:
            return _refused_parse(
                "parse_error", f"json.loads={exc}; block_loads={exc2}",
            )

    if not isinstance(data, dict):
        return _refused_parse("parse_error", f"not_dict: type={type(data).__name__}")

    refused = bool(data.get("refused", False))
    refusal_reason = data.get("refusal_reason")
    answer = data.get("answer")

    if refused:
        # Model self-refused — respect it (decision #8).
        return GenerationResult(
            answer=str(answer or ""),
            citations=[],
            refused=True,
            refusal_reason=str(refusal_reason) if refusal_reason else "model_refused",
            model_id=model_id,
        )

    if not isinstance(answer, str) or not answer.strip():
        # Missing/empty answer field but not refused → treat as parse error
        # (or truncation when MAX_TOKENS is the reason).
        return _refused_parse(
            "parse_error",
            f"missing_answer: keys={list(data.keys())[:10]}",
        )

    # Post-fix the citation schema only requires `hit_id` from the LLM
    # (everything else is on the Hit list and gets backfilled here). We
    # still accept the old shape for back-compat — old prompts in flight
    # / replayed cache entries / external clients sending the legacy
    # `{hit_id, kind, file_id, snippet_preview, score}` envelope all
    # parse the same way.
    hit_index: dict[str, Hit] = {str(h.id): h for h in hits}
    raw_citations = data.get("citations") or []
    citations: list[Citation] = []
    if isinstance(raw_citations, list):
        for rc in raw_citations:
            if not isinstance(rc, dict):
                continue
            hit_id = str(rc.get("hit_id", "")).strip()
            if not hit_id:
                continue
            # Backfill kind / file_id / snippet / score from the matching
            # Hit. The LLM's echo (when present) wins — useful for the
            # rare case where the model annotates page numbers / labels
            # the server doesn't have.
            hit = hit_index.get(hit_id)
            hit_md = (hit.metadata if hit else None) or {}
            try:
                citations.append(Citation(**{
                    "hit_id": hit_id,
                    "kind": str(rc.get("kind") or (hit.kind if hit else "chunk")),
                    "file_id": rc.get("file_id") or hit_md.get("file_id"),
                    "snippet_preview": (
                        str(rc.get("snippet_preview"))[:500]
                        if rc.get("snippet_preview")
                        else ((hit.snippet or "")[:200] if hit else "")
                    ),
                    "score": float(
                        rc.get("score")
                        if rc.get("score") is not None
                        else (hit.score if hit else 0.0)
                    ),
                    # B3 polymorphic fields — accept whatever the LLM gives
                    # (or omits). Orchestrator does the canonical enrichment.
                    "modality": rc.get("modality"),
                    "ref": rc.get("ref") if isinstance(rc.get("ref"), dict) else None,
                    "label": rc.get("label"),
                    "authority": rc.get("authority"),
                    "doc_status": rc.get("doc_status"),
                    "chain_id": rc.get("chain_id"),
                    "confidence": rc.get("confidence"),
                }))
            except (TypeError, ValueError):
                continue

    # If model produced an answer but no citations, fall back to synthesizing
    # the top-3 hits — the UI still gets something to render.
    if not citations and hits:
        citations = _citations_from_hits(hits, limit=3)

    return GenerationResult(
        answer=answer,
        citations=citations,
        refused=False,
        refusal_reason=None,
        model_id=model_id,
    )


# ---------------------------------------------------------------------------
# IdentityGenerator (decision #13)
# ---------------------------------------------------------------------------


class IdentityGenerator:
    """Deterministic stub for CI / no-key path. Templated echo answer."""

    MODEL_ID = "identity"

    async def generate(
        self,
        query: str,
        hits: list[Hit],
        *,
        force_refuse: bool = False,
        conflict_context: str | None = None,
    ) -> GenerationResult:
        # Identity ignores `conflict_context` — deterministic stub. Real
        # impl (GeminiGenerator) injects it into the user prompt.
        _ = conflict_context
        if force_refuse:
            return GenerationResult(
                answer=refusal_answer_for("insufficient_evidence"),
                citations=[],
                refused=True,
                refusal_reason="insufficient_evidence",
                model_id=self.MODEL_ID,
            )
        if not hits:
            return GenerationResult(
                answer=refusal_answer_for("no_hits"),
                citations=[],
                refused=True,
                refusal_reason="no_hits",
                model_id=self.MODEL_ID,
            )
        return GenerationResult(
            answer=f"[identity-stub] {query} (hits: {len(hits)})",
            citations=_citations_from_hits(hits, limit=3),
            refused=False,
            refusal_reason=None,
            model_id=self.MODEL_ID,
        )


# ---------------------------------------------------------------------------
# GeminiGenerator
# ---------------------------------------------------------------------------


class GeminiGenerator:
    """Gemini-backed Astute generator (decisions #1, #11, #12, #15)."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: Any | None = None,
    ) -> None:
        if client is None:
            if not api_key:
                raise ValueError("GeminiGenerator requires api_key or client")
            from google.genai import Client
            client = Client(api_key=api_key)
        self._client = client
        self._model = os.environ.get("KB_QUERY_MODEL") or "gemini-2.5-flash"

    async def generate(
        self,
        query: str,
        hits: list[Hit],
        *,
        force_refuse: bool = False,
        conflict_context: str | None = None,
    ) -> GenerationResult:
        # Decision #6: orchestrator already knows we should refuse — don't
        # waste a token call.
        if force_refuse:
            return GenerationResult(
                answer=refusal_answer_for("insufficient_evidence"),
                citations=[],
                refused=True,
                refusal_reason="insufficient_evidence",
                model_id=self._model,
            )
        # Decision #7: empty hits = nothing to cite.
        if not hits:
            return GenerationResult(
                answer=refusal_answer_for("no_hits"),
                citations=[],
                refused=True,
                refusal_reason="no_hits",
                model_id=self._model,
            )

        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=_SYSTEM_PROMPT,
            max_output_tokens=_MAX_OUTPUT_TOKENS,
            response_mime_type="application/json",
            # Answer generation: low-but-nonzero temperature for natural
            # prose without going off-script. Gemini SDK default (~1.0)
            # produced too much variance in eval. 0.3 is a standard RAG
            # value. See docs/RAG_AUDIT_AND_ACTION_PLAN.md Phase 1.1.
            temperature=0.3,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )

        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=_build_user_prompt(
                    query, hits, conflict_context=conflict_context,
                ),
                config=config,
            )
        except Exception:
            # Decision #10: error → refusal (NOT fail-safe pass; consequence
            # of fake answer >> consequence of refusing).
            return GenerationResult(
                answer=refusal_answer_for("llm_error"),
                citations=[],
                refused=True,
                refusal_reason="llm_error",
                model_id=self._model,
            )

        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return GenerationResult(
                answer=refusal_answer_for("empty_response"),
                citations=[],
                refused=True,
                refusal_reason="empty_response",
                model_id=self._model,
            )

        raw_text = ""
        content = getattr(candidates[0], "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            t = getattr(part, "text", None)
            if t:
                raw_text = t
                break

        # finish_reason tells us if the model hit max_output_tokens
        # mid-response (a recoverable, distinct failure mode) vs. just
        # produced garbage JSON. Gemini's SDK enum-stringifies to
        # 'STOP' / 'MAX_TOKENS' / 'SAFETY' / 'RECITATION' / 'OTHER'.
        fr = getattr(candidates[0], "finish_reason", None)
        finish_reason = str(fr.name if hasattr(fr, "name") else fr) if fr else None

        return _parse_result(
            raw_text, hits=hits, model_id=self._model,
            finish_reason=finish_reason,
        )


# ---------------------------------------------------------------------------
# Factory — KB_QUERY_LLM selector
# ---------------------------------------------------------------------------


def make_generator() -> Generator:
    """Pick a generator based on `KB_QUERY_LLM` (shared with 8a/8d).

    Decision #1 + #14:
      - gemini → GeminiGenerator (requires KB_GEMINI_API_KEY)
      - anthropic → IdentityGenerator (Wave A defer; per decision #14)
      - identity → IdentityGenerator
      - auto → gemini if key else identity
    """
    selector = (os.environ.get("KB_QUERY_LLM") or "auto").lower()

    if selector == "auto":
        if os.environ.get("KB_GEMINI_API_KEY"):
            selector = "gemini"
        else:
            # Skip Anthropic auto-probe — decision #14 maps it to Identity anyway.
            selector = "identity"

    if selector == "gemini":
        api_key = os.environ.get("KB_GEMINI_API_KEY")
        if not api_key:
            raise ValueError(
                "KB_QUERY_LLM=gemini requires KB_GEMINI_API_KEY"
            )
        return GeminiGenerator(api_key=api_key)

    if selector == "anthropic":
        # Decision #14: Wave A maps Anthropic to Identity.
        return IdentityGenerator()

    if selector == "identity":
        return IdentityGenerator()

    raise ValueError(
        f"Unknown KB_QUERY_LLM value: {selector!r} "
        f"(expected 'gemini', 'anthropic', 'identity', or 'auto')"
    )
