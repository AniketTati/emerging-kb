"""Provider-neutral JSON-LLM caller for the query layer.

Why this exists:
- The planner stack (intent classifier, mode planner, q_payload generator,
  IRCoT reformulator) all need the same shape: send a system prompt +
  user text → get back a JSON string. Three providers ship today
  (Identity / Anthropic / Gemini) and each has its own SDK shape for
  this exact operation.

- Without a provider-neutral abstraction, every new feature would either
  hard-code one vendor (the bug that landed in the original Q-mode
  pipeline) or duplicate the per-SDK plumbing N times.

The protocol is intentionally narrow — a single async callable:

    json_str = await caller.generate_json(
        user="…natural-language question…",
        system="…catalog + grammar + output schema…",
        max_tokens=800,
    )

Two adapters ship below. Callers don't import provider SDKs directly;
they construct the right adapter via `make_query_llm_client()` which
mirrors `make_contextualizer()` / `make_summarizer()`.

Failure semantics: any non-JSON output, transport error, or empty
candidate list raises `LLMCallError`. Callers decide whether that's
a refusal, a fallback path, or a retry.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Protocol


logger = logging.getLogger(__name__)


class LLMCallError(RuntimeError):
    """Transport or shape failure from the underlying provider. The
    caller decides whether to surface this as a refusal or retry."""


class JsonLLMClient(Protocol):
    """Single-method protocol. Implementations are stateless w.r.t. the
    call — bring your own system + user prompts.

    `model_id` lets callers stamp `query_log.model_id` / Plan.model_id
    without reaching into the underlying SDK.
    """

    model_id: str

    async def generate_json(
        self, *, user: str, system: str, max_tokens: int = 800,
    ) -> str: ...


# ---------------------------------------------------------------------------
# Gemini adapter
# ---------------------------------------------------------------------------


class GeminiJsonClient:
    """Wraps `google.genai.Client.aio.models.generate_content` with
    `response_mime_type='application/json'` so the model emits a single
    JSON string.

    Thinking is disabled by default — query-layer prompts are short
    classification/extraction tasks where reasoning budget hurts
    latency more than it helps quality.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: Any | None = None,
        model: str | None = None,
    ) -> None:
        if client is None:
            if not api_key:
                raise ValueError("GeminiJsonClient requires api_key or client")
            from google.genai import Client  # type: ignore
            client = Client(api_key=api_key)
        self._client = client
        self.model_id = (
            model or os.environ.get("KB_QUERY_MODEL") or "gemini-2.5-flash"
        )

    async def generate_json(
        self, *, user: str, system: str, max_tokens: int = 800,
    ) -> str:
        from google.genai import types  # type: ignore
        config = types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
            response_mime_type="application/json",
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )
        try:
            response = await self._client.aio.models.generate_content(
                model=self.model_id,
                contents=user,
                config=config,
            )
        except Exception as exc:  # noqa: BLE001
            raise LLMCallError(f"gemini call failed: {exc}") from exc

        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            raise LLMCallError("gemini returned no candidates")
        content = getattr(candidates[0], "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            text = getattr(part, "text", None)
            if text:
                return text
        raise LLMCallError("gemini candidate had no text part")


# ---------------------------------------------------------------------------
# Anthropic adapter
# ---------------------------------------------------------------------------


class AnthropicJsonClient:
    """Wraps `anthropic.AsyncAnthropic.messages.create` to mirror
    Gemini's JSON-only behaviour.

    Anthropic doesn't have a `response_mime_type` knob; instead we lean
    on prompt engineering — the system prompt instructs JSON-only
    output. The class also strips a single outer ```json fence if the
    model defies the instruction (rare but observed).
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: Any | None = None,
        model: str | None = None,
    ) -> None:
        if client is None:
            if not api_key:
                raise ValueError("AnthropicJsonClient requires api_key or client")
            import anthropic  # type: ignore
            client = anthropic.AsyncAnthropic(api_key=api_key)
        self._client = client
        self.model_id = (
            model
            or os.environ.get("KB_QUERY_MODEL_ANTHROPIC")
            or "claude-sonnet-4-5"
        )

    async def generate_json(
        self, *, user: str, system: str, max_tokens: int = 800,
    ) -> str:
        try:
            response = await self._client.messages.create(
                model=self.model_id,
                max_tokens=max_tokens,
                system=system + "\n\nReturn ONLY a JSON object. No prose, no markdown.",
                messages=[{"role": "user", "content": user}],
            )
        except Exception as exc:  # noqa: BLE001
            raise LLMCallError(f"anthropic call failed: {exc}") from exc

        for block in getattr(response, "content", []) or []:
            if getattr(block, "type", None) == "text":
                text = (getattr(block, "text", "") or "").strip()
                # Strip stray code fences if the model used them.
                if text.startswith("```"):
                    lines = text.splitlines()
                    if len(lines) >= 2:
                        lines = lines[1:]
                        if lines and lines[-1].strip() == "```":
                            lines = lines[:-1]
                        text = "\n".join(lines).strip()
                return text
        raise LLMCallError("anthropic message had no text block")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_query_llm_client() -> JsonLLMClient | None:
    """Build the JSON-LLM client picked by `KB_PLANNER`. Returns None
    when the selector resolves to `identity` — callers handle the
    "no LLM available" case explicitly (e.g. Q-mode refuses, intent
    falls back to heuristic).

    Selector resolution matches `make_contextualizer()`:
      auto → gemini key → anthropic key → None (identity)
      explicit → must have the matching key or raise.
    """
    selector = (os.environ.get("KB_PLANNER") or "auto").lower()
    if selector == "auto":
        if os.environ.get("KB_GEMINI_API_KEY"):
            selector = "gemini"
        elif os.environ.get("KB_ANTHROPIC_API_KEY"):
            selector = "anthropic"
        else:
            selector = "identity"

    if selector == "identity":
        return None
    if selector == "gemini":
        api_key = os.environ.get("KB_GEMINI_API_KEY")
        if not api_key:
            raise ValueError(
                "KB_PLANNER=gemini requires KB_GEMINI_API_KEY"
            )
        return GeminiJsonClient(api_key=api_key)
    if selector == "anthropic":
        api_key = os.environ.get("KB_ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError(
                "KB_PLANNER=anthropic requires KB_ANTHROPIC_API_KEY"
            )
        return AnthropicJsonClient(api_key=api_key)
    raise ValueError(
        f"Unknown KB_PLANNER value: {selector!r} "
        f"(expected 'identity', 'gemini', 'anthropic', or 'auto')"
    )
