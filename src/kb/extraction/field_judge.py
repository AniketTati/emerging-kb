"""I2 / #19 — LLM judge for field-name convergence (EDC).

Given two discovered field definitions (name + description + value_type),
return True if they are the SAME underlying attribute under different
spellings (e.g. `total_cost` vs `total_amount`), False otherwise. Used by
`promotion.converge_clusters_semantic` as the `judge_fn` after the cheap
embedding-similarity blocking step, so the LLM only sees a few candidate
pairs, not O(n²).

Mirrors `kb.identity.judge` exactly (same factory selector + JSON contract +
fail-closed parsing). The judge takes plain strings, NOT FieldCluster, so this
module has no dependency on `promotion` (the caller adapts).

Factory: `KB_FIELD_JUDGE ∈ {gemini, anthropic, identity, auto}`. Identity
always returns False (never merge — safe: keeps fields separate rather than
wrongly collapsing distinct attributes when no LLM is available).
"""

from __future__ import annotations

import json
import os
from typing import Any, Protocol


_SYSTEM_PROMPT = (
    "You judge whether two structured-data FIELD definitions describe the "
    "same underlying attribute under different names. Return a single JSON "
    "object: {\"same\": true|false, \"confidence\": 0.0-1.0}. Be strict — "
    "only return same=true if a single column could hold both (e.g. "
    "'total_cost' and 'total_amount' for a contract value), NOT merely "
    "related fields (e.g. 'start_date' and 'end_date')."
)


def _build_user_prompt(
    name_a: str, desc_a: str, type_a: str,
    name_b: str, desc_b: str, type_b: str,
) -> str:
    return (
        f"Field A: name=\"{name_a}\" type={type_a} description=\"{desc_a}\"\n"
        f"Field B: name=\"{name_b}\" type={type_b} description=\"{desc_b}\"\n\n"
        "Are these the same underlying field? Respond JSON only."
    )


class FieldJudgeError(Exception):
    pass


class FieldMergeJudge(Protocol):
    async def same_field(
        self, *,
        name_a: str, desc_a: str, type_a: str,
        name_b: str, desc_b: str, type_b: str,
    ) -> bool: ...


class NoopFieldMergeJudge:
    """Identity fallback — always returns False (never merge)."""

    async def same_field(
        self, *,
        name_a: str, desc_a: str, type_a: str,
        name_b: str, desc_b: str, type_b: str,
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


class GeminiFieldMergeJudge:
    def __init__(self, *, api_key: str | None = None, client: Any | None = None) -> None:
        if client is None:
            if not api_key:
                raise FieldJudgeError("Gemini field judge requires api_key")
            from google.genai import Client
            client = Client(api_key=api_key)
        self._client = client
        self._model = os.environ.get("KB_FIELD_JUDGE_MODEL") or "gemini-2.5-flash"

    async def same_field(
        self, *,
        name_a: str, desc_a: str, type_a: str,
        name_b: str, desc_b: str, type_b: str,
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
                contents=_build_user_prompt(
                    name_a, desc_a, type_a, name_b, desc_b, type_b,
                ),
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


class AnthropicFieldMergeJudge:
    def __init__(self, *, api_key: str | None = None, client: Any | None = None) -> None:
        if client is None:
            if not api_key:
                raise FieldJudgeError("Anthropic field judge requires api_key")
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=api_key)
        self._client = client
        self._model = os.environ.get("KB_FIELD_JUDGE_MODEL") or "claude-opus-4-7"

    async def same_field(
        self, *,
        name_a: str, desc_a: str, type_a: str,
        name_b: str, desc_b: str, type_b: str,
    ) -> bool:
        import anthropic
        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=100,
                system=_SYSTEM_PROMPT,
                messages=[{
                    "role": "user",
                    "content": _build_user_prompt(
                        name_a, desc_a, type_a, name_b, desc_b, type_b,
                    ),
                }],
            )
        except anthropic.APIError:
            return False
        raw_text = ""
        for block in response.content:
            if getattr(block, "type", None) == "text":
                raw_text = getattr(block, "text", "")
                break
        return _parse_judgment(raw_text)


def make_field_merge_judge() -> FieldMergeJudge:
    selector = (os.environ.get("KB_FIELD_JUDGE") or "auto").lower()
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
            raise ValueError("KB_FIELD_JUDGE=gemini requires KB_GEMINI_API_KEY")
        return GeminiFieldMergeJudge(api_key=key)
    if selector == "anthropic":
        key = os.environ.get("KB_ANTHROPIC_API_KEY")
        if not key:
            raise ValueError("KB_FIELD_JUDGE=anthropic requires KB_ANTHROPIC_API_KEY")
        return AnthropicFieldMergeJudge(api_key=key)
    if selector == "identity":
        return NoopFieldMergeJudge()
    raise ValueError(f"Unknown KB_FIELD_JUDGE value: {selector!r}")
