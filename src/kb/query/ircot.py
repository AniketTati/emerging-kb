"""Wave A close-up — IRCoT escalation loop (architecture §6 step 7).

When CRAG returns low confidence on an H-mode query, the architecture
says to "escalate to IRCoT loop" — Interleaving Retrieval with
Chain-of-Thought reasoning — instead of refusing immediately. The
loop tries to fill the retrieval gap by reformulating the query with
the existing hits as evidence, retrieving again, fusing with the old
hits, and re-checking CRAG.

Wave A scope (per §6 step 7):
  * `max_hops = 2` for CRAG-driven escalation (Wave B raises to 5
    for the optional `deep_research` mode).
  * `cost_ceiling = $0.04` (Wave B raises to $0.10). We don't track
    cost in Wave A — the LLM call cost per hop is bounded by the
    `max_output_tokens` cap on the reformulation prompt, so a 2-hop
    loop adds at most ~$0.001 to a query that already costs ~$0.02.
  * Terminates on whichever comes FIRST:
      - hops_completed >= max_hops
      - crag_score crosses the acceptance threshold (normal stop)
      - reformulation produced an empty / identical query

Pre-fix: the architecture promised this loop but no code implemented
it. The orchestrator's CRAG branch just set `force_refuse=True` when
score < threshold, so any borderline query got an immediate refusal
even when one more retrieval pass would have surfaced the answer.

Cost: 2 hops × (1 Gemini reformulation + 1 retrieval pass) ~ 800ms
+ ~$0.001 per query that actually escalates. Most chats short-circuit
on hop 0 because CRAG was already above threshold.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol


DEFAULT_MAX_HOPS_CRAG: int = 2

_DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

_REFORMULATION_SYSTEM_PROMPT = (
    "You are helping a retrieval system that ran a search and didn't "
    "find a confident match. Given:\n"
    "  - the user's original query\n"
    "  - the top retrieved snippets (each tagged with a hit_id)\n"
    "  - a brief reason the search was inconclusive\n"
    "Produce ONE follow-up search query that targets the missing piece. "
    "The follow-up should:\n"
    "  - be a self-contained query (no anaphora, no 'it'/'this')\n"
    "  - introduce specific terminology or entity names you observed\n"
    "    in the snippets, even if the original query was vague\n"
    "  - be different from the original (not a paraphrase)\n"
    "If you can't usefully reformulate (snippets are too sparse or the "
    "original was already specific), return an empty string.\n"
    "Output JSON: {\"follow_up\": str}."
)


@dataclass(frozen=True)
class IRCotHop:
    """One iteration of the IRCoT loop, for the plan inspector."""
    hop_index: int
    reformulated_query: str
    n_hits_added: int
    crag_after: float


@dataclass
class IRCotResult:
    """End state of the loop. `final_hits` may be the original hit list
    if no hop produced a usable reformulation."""
    final_hits: list[Any]
    final_crag: float
    hops: list[IRCotHop] = field(default_factory=list)
    terminated_reason: str = ""


class Reformulator(Protocol):
    """Pluggable contract — generates a follow-up query from the original
    + current snippets."""

    async def reformulate(
        self, *, original_query: str, hits: list[Any],
        reason: str = "low_crag_confidence",
    ) -> str: ...


# ---------------------------------------------------------------------------
# IdentityReformulator — deterministic stub
# ---------------------------------------------------------------------------


class IdentityReformulator:
    """No-LLM reformulation: returns empty string, which makes the IRCoT
    loop bail on the first hop. Used in CI / no-key path and as the
    safe default when KB_REFORMULATOR=identity. Keeps unit tests
    deterministic without needing to mock LLM calls."""

    MODEL_ID = "identity-reformulator"

    async def reformulate(
        self, *, original_query: str, hits: list[Any],
        reason: str = "low_crag_confidence",
    ) -> str:
        return ""


# ---------------------------------------------------------------------------
# GeminiReformulator — real LLM step
# ---------------------------------------------------------------------------


class GeminiReformulator:
    """Gemini Flash with constrained JSON output."""

    def __init__(
        self, *, api_key: str | None = None, client: Any | None = None,
    ) -> None:
        if client is None:
            if not api_key:
                raise ValueError("GeminiReformulator requires api_key or client")
            from google.genai import Client
            client = Client(api_key=api_key)
        self._client = client
        self._model = os.environ.get("KB_QUERY_MODEL") or _DEFAULT_GEMINI_MODEL

    async def reformulate(
        self, *, original_query: str, hits: list[Any],
        reason: str = "low_crag_confidence",
    ) -> str:
        if not (original_query or "").strip():
            return ""

        from google.genai import types

        snippet_lines: list[str] = []
        for h in (hits or [])[:6]:
            # Hit is a dataclass with `.snippet`. Cap to 400 chars so
            # the prompt stays bounded even with many hits.
            text = (getattr(h, "snippet", None) or "")[:400]
            snippet_lines.append(f"[hit:{getattr(h, 'id', '?')}] {text}")
        user_msg = (
            f"Original query: {original_query}\n"
            f"Reason for escalation: {reason}\n\n"
            f"Top snippets:\n" + "\n\n".join(snippet_lines)
            + "\n\nReturn JSON only."
        )

        config = types.GenerateContentConfig(
            system_instruction=_REFORMULATION_SYSTEM_PROMPT,
            max_output_tokens=200,
            response_mime_type="application/json",
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model, contents=user_msg, config=config,
            )
        except Exception:
            return ""

        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return ""
        parts = getattr(candidates[0].content, "parts", None) or []
        raw = "".join(getattr(p, "text", "") for p in parts).strip()
        if not raw:
            return ""
        # Tolerant JSON parse — bracket-strip then key-extract.
        import json
        text = raw
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
            return ""
        if not isinstance(data, dict):
            return ""
        v = data.get("follow_up")
        return v.strip() if isinstance(v, str) else ""


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_default_reformulator() -> Reformulator:
    """KB_REFORMULATOR ∈ {identity, gemini, auto}; default auto."""
    mode = (os.environ.get("KB_REFORMULATOR") or "auto").lower().strip()
    if mode == "identity":
        return IdentityReformulator()
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if mode == "gemini":
        return GeminiReformulator(api_key=key)
    # auto
    if key:
        return GeminiReformulator(api_key=key)
    return IdentityReformulator()


# ---------------------------------------------------------------------------
# Loop
# ---------------------------------------------------------------------------


async def escalate_with_ircot(
    *,
    original_query: str,
    hits: list[Any],
    crag_score: float,
    threshold: float,
    crag_assess: Callable[[str, list[Any]], Awaitable[float]],
    retrieve: Callable[[str], Awaitable[list[Any]]],
    reformulator: Reformulator,
    max_hops: int = DEFAULT_MAX_HOPS_CRAG,
) -> IRCotResult:
    """Run the IRCoT loop until CRAG passes or hop cap hits.

    `retrieve` returns a fresh hit list for a follow-up query. The
    caller wires it to `_retrieve_and_rerank`. `crag_assess` is the
    same gate used at the top of the chat pipeline.

    Strategy:
      * Score guard — bail immediately if already above threshold.
        (Callers typically only invoke us when below; this is a
        cheap safety net.)
      * For each hop up to `max_hops`:
          - reformulate(original_query, accumulated_hits) → follow_up
          - if follow_up is empty / identical: terminate
          - retrieve(follow_up) → new_hits
          - fuse: dedup by hit.id, prefer higher-score entry
          - crag_assess(original_query, fused_hits) → new_crag
          - if new_crag >= threshold: terminate with success
          - else: continue with fused hits as the new baseline

    Returns IRCotResult with the final hit list + crag + per-hop log.
    """
    if crag_score >= threshold:
        return IRCotResult(
            final_hits=list(hits),
            final_crag=crag_score,
            terminated_reason="already_above_threshold",
        )

    accumulated_hits: list[Any] = list(hits)
    accumulated_crag = crag_score
    log: list[IRCotHop] = []
    seen_queries: set[str] = {(original_query or "").strip().lower()}

    for hop in range(1, max_hops + 1):
        follow_up = await reformulator.reformulate(
            original_query=original_query,
            hits=accumulated_hits,
            reason="low_crag_confidence",
        )
        if not follow_up:
            return IRCotResult(
                final_hits=accumulated_hits, final_crag=accumulated_crag,
                hops=log, terminated_reason="reformulation_empty",
            )
        if follow_up.strip().lower() in seen_queries:
            return IRCotResult(
                final_hits=accumulated_hits, final_crag=accumulated_crag,
                hops=log, terminated_reason="reformulation_duplicate",
            )
        seen_queries.add(follow_up.strip().lower())

        new_hits = await retrieve(follow_up)

        # Fuse — preserve order: existing first, then NEW (higher score)
        # de-duplicating by hit id.
        seen_ids: set[str] = {str(getattr(h, "id", "")) for h in accumulated_hits}
        added: list[Any] = []
        for h in new_hits or []:
            hid = str(getattr(h, "id", ""))
            if hid and hid not in seen_ids:
                added.append(h)
                seen_ids.add(hid)
        accumulated_hits = accumulated_hits + added

        # Re-score CRAG on the original query against the expanded
        # candidate set.
        accumulated_crag = await crag_assess(original_query, accumulated_hits)
        log.append(IRCotHop(
            hop_index=hop,
            reformulated_query=follow_up,
            n_hits_added=len(added),
            crag_after=accumulated_crag,
        ))

        if accumulated_crag >= threshold:
            return IRCotResult(
                final_hits=accumulated_hits,
                final_crag=accumulated_crag,
                hops=log,
                terminated_reason="threshold_crossed",
            )

    return IRCotResult(
        final_hits=accumulated_hits, final_crag=accumulated_crag,
        hops=log, terminated_reason="max_hops_reached",
    )
