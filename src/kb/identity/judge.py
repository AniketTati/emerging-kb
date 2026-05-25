"""Phase 7 — LLM judge for borderline identity resolution.

Returns `True` (same entity) or `False` (different) given two mention strings
+ types. Used only for borderline embedding-similarity cases (decision #3 stage c).

Factory pattern: `KB_IDENTITY_JUDGE ∈ {gemini, anthropic, identity, auto}`.
Identity always returns False (treats every borderline as different — sub-optimal
recall but correct fallback when no LLM available).
"""

from __future__ import annotations

import json
import os
from typing import Any, Protocol


_SYSTEM_PROMPT = (
    "You judge whether two named-entity mentions refer to the same real-world "
    "entity. Return a single JSON object: {\"same\": true|false, "
    "\"confidence\": 0.0-1.0}. Be strict — only return same=true if the "
    "mentions plausibly refer to the same specific entity."
)


def _build_user_prompt(text_a: str, type_a: str, text_b: str, type_b: str) -> str:
    return (
        f"Mention A: \"{text_a}\" (type: {type_a})\n"
        f"Mention B: \"{text_b}\" (type: {type_b})\n\n"
        "Are these the same real-world entity? Respond JSON only."
    )


class IdentityJudgeError(Exception):
    pass


class IdentityJudge(Protocol):
    async def same_entity(
        self, *, text_a: str, type_a: str, text_b: str, type_b: str,
    ) -> bool: ...


class NoopIdentityJudge:
    """Identity fallback — always returns False."""

    async def same_entity(
        self, *, text_a: str, type_a: str, text_b: str, type_b: str,
    ) -> bool:
        return False


def _parse_judgment(raw: str) -> bool:
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if len(lines) >= 2 and lines[-1].strip() == "```":
            lines = lines[1:-1]
        else:
            lines = lines[1:]
        raw = "\n".join(lines)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return False
    if not isinstance(data, dict):
        return False
    return bool(data.get("same", False))


class GeminiIdentityJudge:
    def __init__(self, *, api_key: str | None = None, client: Any | None = None) -> None:
        if client is None:
            if not api_key:
                raise IdentityJudgeError("Gemini judge requires api_key")
            from google.genai import Client
            client = Client(api_key=api_key)
        self._client = client
        self._model = os.environ.get("KB_IDENTITY_MODEL") or "gemini-2.5-flash"

    async def same_entity(
        self, *, text_a: str, type_a: str, text_b: str, type_b: str,
    ) -> bool:
        from google.genai import types
        config = types.GenerateContentConfig(
            system_instruction=_SYSTEM_PROMPT,
            max_output_tokens=100,
            response_mime_type="application/json",
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=_build_user_prompt(text_a, type_a, text_b, type_b),
                config=config,
            )
        except Exception:
            return False
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return False
        raw_text = ""
        content = getattr(candidates[0], "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            t = getattr(part, "text", None)
            if t:
                raw_text = t
                break
        return _parse_judgment(raw_text)


class AnthropicIdentityJudge:
    def __init__(self, *, api_key: str | None = None, client: Any | None = None) -> None:
        if client is None:
            if not api_key:
                raise IdentityJudgeError("Anthropic judge requires api_key")
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=api_key)
        self._client = client
        self._model = os.environ.get("KB_IDENTITY_MODEL") or "claude-opus-4-7"

    async def same_entity(
        self, *, text_a: str, type_a: str, text_b: str, type_b: str,
    ) -> bool:
        import anthropic
        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=100,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": _build_user_prompt(text_a, type_a, text_b, type_b)}],
            )
        except anthropic.APIError:
            return False
        raw_text = ""
        for block in response.content:
            if getattr(block, "type", None) == "text":
                raw_text = getattr(block, "text", "")
                break
        return _parse_judgment(raw_text)


def make_identity_judge() -> IdentityJudge:
    selector = (os.environ.get("KB_IDENTITY_JUDGE") or "auto").lower()
    if selector == "auto":
        if os.environ.get("KB_GEMINI_API_KEY"):
            selector = "gemini"
        elif os.environ.get("KB_ANTHROPIC_API_KEY"):
            selector = "anthropic"
        else:
            selector = "identity"

    if selector == "gemini":
        key = os.environ.get("KB_GEMINI_API_KEY")
        if not key:
            raise ValueError("KB_IDENTITY_JUDGE=gemini requires KB_GEMINI_API_KEY")
        return GeminiIdentityJudge(api_key=key)
    if selector == "anthropic":
        key = os.environ.get("KB_ANTHROPIC_API_KEY")
        if not key:
            raise ValueError("KB_IDENTITY_JUDGE=anthropic requires KB_ANTHROPIC_API_KEY")
        return AnthropicIdentityJudge(api_key=key)
    if selector == "identity":
        return NoopIdentityJudge()
    raise ValueError(f"Unknown KB_IDENTITY_JUDGE value: {selector!r}")
