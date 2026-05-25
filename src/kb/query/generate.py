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
import os
from typing import Any, Protocol

from pydantic import BaseModel, Field

from kb.query.rrf import Hit


# Decision #2: top-K post-rerank seen by the generator.
_TOP_N_HITS = 10

# Decision #11: max output tokens.
_MAX_OUTPUT_TOKENS = 2048

# Decision #15: Astute defensive system prompt.
_SYSTEM_PROMPT = (
    "You are a careful question-answering assistant grounded in the "
    "retrieved snippets below. Follow this discipline:\n"
    "1. Read each snippet's [hit_id] and snippet text.\n"
    "2. Compose an answer using ONLY information present in the snippets. "
    "Do NOT invent facts.\n"
    "3. Cite every claim inline using the [hit_id] marker for the snippet "
    "that supports it.\n"
    "4. If the snippets do not support a confident answer to the query, "
    "refuse: return JSON with refused=true and a brief refusal_reason. "
    "It is better to refuse than to guess.\n"
    "5. Return STRICTLY a JSON object matching: "
    '{"answer": str, "citations": [{"hit_id": str, "kind": str, '
    '"file_id": str|null, "snippet_preview": str, "score": float}], '
    '"refused": bool, "refusal_reason": str|null}.'
)


# ---------------------------------------------------------------------------
# Pydantic models (decision #4)
# ---------------------------------------------------------------------------


class Citation(BaseModel):
    """Minimal citation envelope. Rich envelope is Wave B."""

    hit_id: str
    kind: str
    file_id: str | None = None
    snippet_preview: str = ""
    score: float = 0.0


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


def _build_user_prompt(query: str, hits: list[Hit]) -> str:
    """Build the user message — top-N hits per decision #2."""
    blocks: list[str] = []
    for h in hits[:_TOP_N_HITS]:
        snippet = (h.snippet or "")[:500]
        # Use full UUID as hit_id so callers can resolve back to the Hit.
        blocks.append(f"[hit_id: {h.id}] (kind={h.kind}) {snippet}")
    snippets = "\n\n".join(blocks)
    return (
        f"Query: {query}\n\n"
        f"Retrieved snippets (top {min(len(hits), _TOP_N_HITS)}):\n"
        f"{snippets}\n\n"
        f"Return JSON only."
    )


def _parse_result(
    raw: str,
    hits: list[Hit],
    model_id: str,
) -> GenerationResult:
    """Parse Gemini's JSON output into GenerationResult. Tolerant + fail-safe.

    Decision #9: any parse failure → refusal with reason='parse_error'.
    Decision #8: respects model's own refusal flag.
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 2 and lines[-1].strip() == "```":
            lines = lines[1:-1]
        else:
            lines = lines[1:]
        text = "\n".join(lines)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return GenerationResult(
            answer="",
            citations=[],
            refused=True,
            refusal_reason="parse_error",
            model_id=model_id,
        )

    if not isinstance(data, dict):
        return GenerationResult(
            answer="",
            citations=[],
            refused=True,
            refusal_reason="parse_error",
            model_id=model_id,
        )

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
        # Missing/empty answer field but not refused → treat as parse error.
        return GenerationResult(
            answer="",
            citations=[],
            refused=True,
            refusal_reason="parse_error",
            model_id=model_id,
        )

    raw_citations = data.get("citations") or []
    citations: list[Citation] = []
    if isinstance(raw_citations, list):
        for rc in raw_citations:
            if not isinstance(rc, dict):
                continue
            try:
                citations.append(Citation(**{
                    "hit_id": str(rc.get("hit_id", "")),
                    "kind": str(rc.get("kind", "chunk")),
                    "file_id": rc.get("file_id"),
                    "snippet_preview": str(rc.get("snippet_preview", ""))[:500],
                    "score": float(rc.get("score", 0.0) or 0.0),
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
    ) -> GenerationResult:
        if force_refuse:
            return GenerationResult(
                answer="",
                citations=[],
                refused=True,
                refusal_reason="insufficient_evidence",
                model_id=self.MODEL_ID,
            )
        if not hits:
            return GenerationResult(
                answer="",
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
    ) -> GenerationResult:
        # Decision #6: orchestrator already knows we should refuse — don't
        # waste a token call.
        if force_refuse:
            return GenerationResult(
                answer="",
                citations=[],
                refused=True,
                refusal_reason="insufficient_evidence",
                model_id=self._model,
            )
        # Decision #7: empty hits = nothing to cite.
        if not hits:
            return GenerationResult(
                answer="",
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
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )

        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=_build_user_prompt(query, hits),
                config=config,
            )
        except Exception:
            # Decision #10: error → refusal (NOT fail-safe pass; consequence
            # of fake answer >> consequence of refusing).
            return GenerationResult(
                answer="",
                citations=[],
                refused=True,
                refusal_reason="llm_error",
                model_id=self._model,
            )

        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return GenerationResult(
                answer="",
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

        return _parse_result(raw_text, hits=hits, model_id=self._model)


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
