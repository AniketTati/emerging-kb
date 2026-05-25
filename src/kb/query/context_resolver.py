"""B6a / WA-12 — ChatContext resolution (Design 8 step 0.5).

Runs BEFORE the intent classifier. Takes the current user query + the
ChatContext (carry_forward state + last K verbatim turns + Tier-2
rolling summary) and emits a resolved_query plus an updated
ContextDelta describing what should be folded into the session's
carry-forward state for the next turn.

Two impls, mirroring the CRAG / faithfulness / intent / planner pattern:

  IdentityContextResolver — pure-Python heuristic: when no pronoun-like
    token is present, returns query unchanged. Used in CI and when no
    LLM key is available.

  GeminiContextResolver — Gemini Flash call with constrained JSON.

Factory: KB_CONTEXT_RESOLVER ∈ {identity, gemini, auto}.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from kb.domain.chat_memory import ChatContext


# ---------------------------------------------------------------------------
# Result + delta dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnaphoraSubstitution:
    """Records a single anaphora resolution for the plan inspector."""
    from_text: str    # e.g. "his"
    to_text: str      # e.g. "Mr. Sharma (P-541)"


@dataclass(frozen=True)
class ContextResolution:
    """The resolver's output. Applied to:
      - the orchestrator (resolved_query feeds into intent classifier)
      - chat_sessions row (carry_forward_* deltas merged in)
      - chat_turns.context_used (audit)
    """
    resolved_query: str
    anaphora_resolved: tuple[AnaphoraSubstitution, ...] = field(default_factory=tuple)
    # Newly active entity ids — appended to session's carry_forward_entities.
    new_entities: tuple[str, ...] = field(default_factory=tuple)
    # New / updated filters merged into session's carry_forward_filters.
    new_filters: dict = field(default_factory=dict)
    # True when this query refines the prior result set.
    refinement_of_prior: bool = False
    notes: str | None = None
    model_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "resolved_query": self.resolved_query,
            "anaphora_resolved": [
                {"from": s.from_text, "to": s.to_text}
                for s in self.anaphora_resolved
            ],
            "new_entities": list(self.new_entities),
            "new_filters": self.new_filters,
            "refinement_of_prior": self.refinement_of_prior,
            "notes": self.notes,
            "model_id": self.model_id,
        }


class ContextResolver(Protocol):
    async def resolve(
        self, query: str, context: ChatContext | None,
    ) -> ContextResolution: ...


# ---------------------------------------------------------------------------
# Heuristics
# ---------------------------------------------------------------------------


# Pronoun-like tokens that hint at anaphora needing resolution.
_PRONOUN_RE = re.compile(
    r"\b(?:he|she|it|they|him|her|them|his|hers|its|their|"
    r"this|that|these|those|the\s+same|prior|previous|"
    r"the\s+(?:above|prior|previous|earlier|last)|"
    r"as\s+(?:above|before|earlier))\b",
    re.IGNORECASE,
)

_REFINEMENT_RE = re.compile(
    r"\b(?:just\s+the|only\s+the|filter|narrow|of\s+(?:those|these)|"
    r"from\s+(?:the\s+)?(?:prior|previous|above|earlier)|of\s+the\s+prior)\b",
    re.IGNORECASE,
)


def looks_like_anaphora(query: str) -> bool:
    return bool(_PRONOUN_RE.search(query or ""))


def looks_like_refinement(query: str) -> bool:
    return bool(_REFINEMENT_RE.search(query or ""))


# ---------------------------------------------------------------------------
# IdentityContextResolver
# ---------------------------------------------------------------------------


class IdentityContextResolver:
    """Deterministic heuristic. Returns query unchanged when no
    pronoun-like token is detected. When one IS detected and a session
    context exists, prepends the most-recent answer summary as a
    poor-person's coreference hint."""

    MODEL_ID = "identity-resolver-v1"

    async def resolve(
        self, query: str, context: ChatContext | None,
    ) -> ContextResolution:
        q = (query or "").strip()
        if not q:
            return ContextResolution(
                resolved_query=q, model_id=self.MODEL_ID,
                notes="empty_query",
            )

        if context is None or not context.last_k_verbatim_turns:
            # No prior context to draw from.
            return ContextResolution(
                resolved_query=q, model_id=self.MODEL_ID,
            )

        is_anaphora = looks_like_anaphora(q)
        is_refinement = looks_like_refinement(q)
        if not is_anaphora and not is_refinement:
            return ContextResolution(
                resolved_query=q, model_id=self.MODEL_ID,
            )

        # Heuristic: append the most recent answer's first ~120 chars as
        # a soft context hint. Cheap, no LLM — the Gemini resolver does
        # the real work when keys are present. Don't try sentence-
        # splitting on "." (breaks on abbreviations like "Mr.").
        last_turn = context.last_k_verbatim_turns[-1]
        last_answer = (last_turn.get("answer_summary") or "").strip()
        hint = ""
        if last_answer:
            snippet = last_answer[:120].strip()
            if snippet:
                hint = f" (context: {snippet})"

        return ContextResolution(
            resolved_query=q + hint,
            anaphora_resolved=(
                (AnaphoraSubstitution(
                    from_text="<pronouns>", to_text="<see context hint>",
                ),) if is_anaphora else ()
            ),
            refinement_of_prior=is_refinement,
            model_id=self.MODEL_ID,
            notes="heuristic_context_hint",
        )


# ---------------------------------------------------------------------------
# GeminiContextResolver
# ---------------------------------------------------------------------------


_GEMINI_SYSTEM_PROMPT = (
    "You resolve anaphora in a follow-up query using prior conversation. "
    "Output STRICTLY JSON: {\"resolved_query\": str, "
    "\"anaphora_resolved\": [{\"from\": str, \"to\": str}], "
    "\"new_entities\": [str], \"new_filters\": object, "
    "\"refinement_of_prior\": bool}. "
    "Rewrite the query so pronouns/demonstratives reference their concrete "
    "referents from the prior turns. Set refinement_of_prior=true if the "
    "user is asking to filter / narrow the prior result set."
)


def _parse_resolution_json(raw: str, fallback_query: str) -> ContextResolution:
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
        return ContextResolution(
            resolved_query=fallback_query, notes="parse_error",
        )
    if not isinstance(data, dict):
        return ContextResolution(
            resolved_query=fallback_query, notes="parse_error",
        )

    rq = str(data.get("resolved_query") or "").strip() or fallback_query

    anaphora_raw = data.get("anaphora_resolved") or []
    anaphora: list[AnaphoraSubstitution] = []
    if isinstance(anaphora_raw, list):
        for item in anaphora_raw:
            if not isinstance(item, dict):
                continue
            f = item.get("from")
            t = item.get("to")
            if isinstance(f, str) and isinstance(t, str):
                anaphora.append(AnaphoraSubstitution(from_text=f, to_text=t))

    new_entities_raw = data.get("new_entities") or []
    new_entities: list[str] = []
    if isinstance(new_entities_raw, list):
        for e in new_entities_raw:
            if isinstance(e, str) and e.strip():
                new_entities.append(e.strip())

    new_filters = data.get("new_filters")
    if not isinstance(new_filters, dict):
        new_filters = {}

    refinement = bool(data.get("refinement_of_prior", False))

    return ContextResolution(
        resolved_query=rq,
        anaphora_resolved=tuple(anaphora),
        new_entities=tuple(new_entities),
        new_filters=new_filters,
        refinement_of_prior=refinement,
    )


class GeminiContextResolver:
    """Gemini Flash with constrained JSON."""

    def __init__(
        self, *, api_key: str | None = None, client: Any | None = None,
    ) -> None:
        if client is None:
            if not api_key:
                raise ValueError("GeminiContextResolver requires api_key or client")
            from google.genai import Client
            client = Client(api_key=api_key)
        self._client = client
        self._model = os.environ.get("KB_QUERY_MODEL") or "gemini-2.5-flash"

    async def resolve(
        self, query: str, context: ChatContext | None,
    ) -> ContextResolution:
        if not (query or "").strip():
            return ContextResolution(
                resolved_query="", notes="empty_query",
                model_id=self._model,
            )
        if context is None or not context.last_k_verbatim_turns:
            return ContextResolution(
                resolved_query=query, model_id=self._model,
            )

        from google.genai import types
        # Build the user message — Tier 2 summary + Tier 1 hot turns.
        prior_lines: list[str] = []
        if context.older_turn_summary:
            prior_lines.append(f"[Summary of older turns]: {context.older_turn_summary}")
        for t in context.last_k_verbatim_turns:
            prior_lines.append(
                f"Turn {t['turn_index']} user: {t['user_query']}"
            )
            if t.get("answer_summary"):
                prior_lines.append(
                    f"Turn {t['turn_index']} assistant: {t['answer_summary']}"
                )
        carry = ""
        if context.carry_forward_entities or context.carry_forward_filters:
            carry = (
                f"\n[Carry-forward state]: entities={list(context.carry_forward_entities)}, "
                f"filters={context.carry_forward_filters}"
            )

        user_msg = (
            "Prior turns:\n" + "\n".join(prior_lines)
            + carry
            + f"\n\nCurrent query: {query}\n\nReturn JSON only."
        )

        config = types.GenerateContentConfig(
            system_instruction=_GEMINI_SYSTEM_PROMPT,
            max_output_tokens=400,
            response_mime_type="application/json",
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model, contents=user_msg, config=config,
            )
        except Exception:
            # Fail-safe — return the original query unchanged.
            return ContextResolution(
                resolved_query=query, notes="llm_error",
                model_id=self._model,
            )

        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return ContextResolution(
                resolved_query=query, notes="empty_response",
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

        parsed = _parse_resolution_json(raw_text, fallback_query=query)
        return ContextResolution(
            resolved_query=parsed.resolved_query,
            anaphora_resolved=parsed.anaphora_resolved,
            new_entities=parsed.new_entities,
            new_filters=parsed.new_filters,
            refinement_of_prior=parsed.refinement_of_prior,
            notes=parsed.notes,
            model_id=self._model,
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_context_resolver() -> ContextResolver:
    selector = (os.environ.get("KB_CONTEXT_RESOLVER") or "auto").lower()
    if selector == "auto":
        selector = "gemini" if os.environ.get("KB_GEMINI_API_KEY") else "identity"
    if selector == "identity":
        return IdentityContextResolver()
    if selector == "gemini":
        api_key = os.environ.get("KB_GEMINI_API_KEY")
        if not api_key:
            raise ValueError("KB_CONTEXT_RESOLVER=gemini requires KB_GEMINI_API_KEY")
        return GeminiContextResolver(api_key=api_key)
    raise ValueError(
        f"Unknown KB_CONTEXT_RESOLVER value: {selector!r} "
        f"(expected 'identity', 'gemini', or 'auto')"
    )
