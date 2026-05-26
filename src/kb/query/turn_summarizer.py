"""Wave A close-up — Design 8 Tier 2 (Mem0-style rolling summary).

The 3-tier conversational memory (Design 8) has:
  Tier 1 — last K=6 turns verbatim (`chat_turns` recent rows)
  Tier 2 — rolling summary of older turns (`chat_sessions.older_turn_summary`)
  Tier 3 — structured carry-forward (`chat_sessions.carry_forward_*`)

Tier 1 + Tier 3 were wired end-to-end since B6a. Tier 2's column was
defined + read by `build_chat_context()`, but no code ever WROTE it —
so conversations past the 6th turn lost their mid-range context.
The anaphora resolver and the generator both saw the Tier-2 line
("[Summary of older turns]: ...") when the column had data, but it
was always empty.

This module fills the gap. After every Nth chat turn (default 3),
once the session has more than K=6 turns, we summarize the
"displaced" turns (those that have aged out of the verbatim
window) into the rolling summary. The old summary is folded in
so we never lose the deeper history — it just gets log-compressed.

Cost: one Gemini Flash call per N turns, ~$0.0001 each. Latency is
out-of-band (fire-and-forget after the chat response returns).

Tier choice — Identity vs Gemini — mirrors the other resolver/gate
factories: `KB_TURN_SUMMARIZER ∈ {identity, gemini, auto}`. Identity
produces a deterministic stub summary so unit tests can assert on
the exact text without burning API quota.
"""

from __future__ import annotations

import os
from typing import Any, Protocol

from kb.domain.chat_memory import ChatTurn


# Turn cadence: only summarize every Nth turn to keep cost bounded.
# At N=3, a 20-turn conversation triggers ~5 summary updates.
DEFAULT_SUMMARY_EVERY_N_TURNS: int = 3

# Hot-turn window — must match `chat_memory.DEFAULT_HOT_TURNS`. Turns
# at index ≤ (current_turn_idx - HOT_TURNS) have aged out and become
# candidates for Tier-2 compression.
DEFAULT_HOT_TURNS: int = 6


def should_summarize(
    *, turn_index: int,
    hot_turns: int = DEFAULT_HOT_TURNS,
    every_n: int = DEFAULT_SUMMARY_EVERY_N_TURNS,
) -> bool:
    """Cheap gate to decide whether the just-persisted turn warrants
    a Tier-2 summary refresh. Public so the orchestrator can call it
    inline without instantiating a summarizer when nothing's needed.

    The first eligible turn is `turn_index == hot_turns` (turn 6
    pushes turn 0 out of the verbatim window). Then every Nth turn
    after that. Pre-hot-turns conversations don't need a summary —
    everything fits in the verbatim window.
    """
    if turn_index < hot_turns:
        return False
    # Trigger on turn_index == hot_turns and every Nth turn after.
    return ((turn_index - hot_turns) % every_n) == 0


class TurnSummarizer(Protocol):
    """Pluggable contract; same Identity/Gemini pattern as the other
    LLM-touching modules."""

    async def summarize(
        self,
        *,
        older_turn_summary: str | None,
        displaced_turns: list[ChatTurn],
    ) -> str: ...


# ---------------------------------------------------------------------------
# IdentityTurnSummarizer — deterministic stub for CI / no-key path
# ---------------------------------------------------------------------------


class IdentityTurnSummarizer:
    """Deterministic Tier-2 rendering. Stitches the existing summary +
    each displaced turn's `[idx] user: query / assistant: answer`
    one-liner. Capped at 2000 chars total so log-compression actually
    happens.
    """

    MODEL_ID = "identity-turn-summarizer"
    MAX_CHARS = 2000

    async def summarize(
        self,
        *,
        older_turn_summary: str | None,
        displaced_turns: list[ChatTurn],
    ) -> str:
        parts: list[str] = []
        if older_turn_summary:
            parts.append(older_turn_summary.strip())
        for t in displaced_turns:
            ans = (t.answer or "").strip()
            if len(ans) > 200:
                ans = ans[:200].rstrip() + "..."
            parts.append(
                f"[t{t.turn_index}] user: {t.user_query.strip()[:200]} "
                f"| assistant: {ans}"
            )
        joined = " ".join(parts)
        if len(joined) > self.MAX_CHARS:
            # Keep the tail — recent displaced turns are more
            # relevant than the very oldest summary fragment.
            joined = "..." + joined[-(self.MAX_CHARS - 3):]
        return joined


# ---------------------------------------------------------------------------
# GeminiTurnSummarizer — real LLM compression
# ---------------------------------------------------------------------------


_GEMINI_SYSTEM_PROMPT = (
    "You are compressing the older portion of a chat conversation so "
    "the assistant can stay coherent across long sessions. Inputs:\n"
    "  - existing_summary: prior compressed summary (may be empty)\n"
    "  - displaced_turns: the user/assistant exchanges that just "
    "aged out of the verbatim window\n"
    "Produce a tight summary that:\n"
    "  - preserves named entities, dates, numbers, doc references\n"
    "  - preserves filters and qualifications the user applied\n"
    "  - preserves the user's stated goals + the assistant's findings\n"
    "  - drops social pleasantries, conversational scaffolding\n"
    "  - reads as continuous prose, not a transcript\n"
    "Keep total length under 600 words. Return plain text, no JSON."
)


class GeminiTurnSummarizer:
    """Gemini Flash with a system prompt tuned for compression-of-history."""

    def __init__(
        self, *, api_key: str | None = None, client: Any | None = None,
    ) -> None:
        if client is None:
            if not api_key:
                raise ValueError("GeminiTurnSummarizer requires api_key or client")
            from google.genai import Client
            client = Client(api_key=api_key)
        self._client = client
        self._model = os.environ.get("KB_QUERY_MODEL") or "gemini-2.5-flash"

    async def summarize(
        self,
        *,
        older_turn_summary: str | None,
        displaced_turns: list[ChatTurn],
    ) -> str:
        if not displaced_turns:
            return (older_turn_summary or "").strip()

        from google.genai import types

        existing = (older_turn_summary or "").strip() or "(none)"
        lines: list[str] = []
        for t in displaced_turns:
            ans = (t.answer or "").strip()
            if len(ans) > 600:
                ans = ans[:600].rstrip() + "..."
            lines.append(
                f"Turn {t.turn_index}:\n"
                f"  user: {t.user_query.strip()}\n"
                f"  assistant: {ans}"
            )
        user_msg = (
            f"existing_summary:\n{existing}\n\n"
            f"displaced_turns:\n" + "\n\n".join(lines) + "\n\n"
            "Return the new compressed summary as plain text."
        )

        config = types.GenerateContentConfig(
            system_instruction=_GEMINI_SYSTEM_PROMPT,
            max_output_tokens=800,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model, contents=user_msg, config=config,
            )
        except Exception:
            # On any LLM failure, fall back to the deterministic
            # Identity summarizer so we still write SOMETHING — better
            # than leaving older_turn_summary stale for the rest of
            # the conversation.
            return await IdentityTurnSummarizer().summarize(
                older_turn_summary=older_turn_summary,
                displaced_turns=displaced_turns,
            )

        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return (older_turn_summary or "").strip()
        parts = getattr(candidates[0].content, "parts", None) or []
        text = "".join(getattr(p, "text", "") for p in parts).strip()
        return text or (older_turn_summary or "").strip()


# ---------------------------------------------------------------------------
# Factory — mirrors KB_INTENT_CLASSIFIER / KB_CONTEXT_RESOLVER pattern
# ---------------------------------------------------------------------------


def make_default_turn_summarizer() -> TurnSummarizer:
    """Read KB_TURN_SUMMARIZER from env; default to 'auto' which picks
    Gemini when GEMINI_API_KEY is present, else Identity."""
    mode = (os.environ.get("KB_TURN_SUMMARIZER") or "auto").lower().strip()
    if mode == "identity":
        return IdentityTurnSummarizer()
    if mode == "gemini":
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        return GeminiTurnSummarizer(api_key=key)
    # auto
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if key:
        return GeminiTurnSummarizer(api_key=key)
    return IdentityTurnSummarizer()
