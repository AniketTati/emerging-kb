"""B4a / WA-10 — Schema-aware planner.

Architecture §6 step 3: parses an intent + query into a typed `Plan` that
picks one of the 12 retrieval modes (E/F/S/H/T/M/G/D/C/A/Q/K) and carries
mode-specific parameters.

Modes (architecture §6 step 3):

  E — entity lookup            (by name/identifier)
  F — field filter             (schema field predicates)
  S — scoped chunk             (within a parent: doc, contract, project)
  H — hybrid semantic          (BM25 + dense + rerank over chunks; default)
  T — graph traversal          (multi-hop from seed entities via PPR)
  M — mention search           (L2 surface forms)
  G — global summary           (L7, LazyGraphRAG-lazy)
  D — doc metadata filter      (type, date, source, path, authority)
  C — atomic-unit filter       (any L3 unit type + parameter predicates)
  A — anomaly filter           (rarity_score > threshold)
  Q — STRUCTURED QUERY         (SQL aggregate — Q-mode pipeline ships in B4b)
  K — DOC-CHAIN AWARE          (chain context: current_version / all_versions)

Two impls:
  IdentityPlanner — deterministic intent→mode mapping
  GeminiPlanner   — Gemini Flash with constrained JSON

Factory: KB_PLANNER ∈ {identity, gemini, auto}
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from kb.query.intent import IntentResult


QUERY_MODES: tuple[str, ...] = (
    "E", "F", "S", "H", "T", "M", "G", "D", "C", "A", "Q", "K",
    "I",  # Inventory — SQL metadata listing (no chunks / no LLM)
)

# Architecture default — "H" (hybrid) when the planner has no confident pick.
DEFAULT_MODE: str = "H"

# Intent → default mode mapping (architecture §6 step 3 narrative).
_INTENT_TO_MODE: dict[str, str] = {
    "factoid":          "H",   # hybrid retrieve + answer
    "vague":            "H",
    "multi-hop":        "T",   # graph traversal
    "global/thematic":  "G",   # LazyGraphRAG
    "negative":         "H",
    "adversarial":      "H",   # planner doesn't refuse; downstream gates do
    "aggregation":      "Q",   # structured SQL (Q-mode in B4b)
    "set_operation":    "Q",
    "temporal_history": "K",   # doc-chain aware
    "chain_aware":      "K",
    "inventory":        "I",   # SQL metadata listing — orchestrator
                               # short-circuits retrieval / LLM
                               # (added with the inventory intent fix)
}


# ---------------------------------------------------------------------------
# Plan dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Plan:
    """The planner's typed output. Persisted in query_log.plan (JSONB)."""
    mode: str                       # one of QUERY_MODES
    # Optional intent + confidence carried through for audit.
    intent: str | None = None
    intent_confidence: float | None = None
    # Mode-specific parameters. All fields are optional and ignored when
    # not applicable to the chosen mode.
    seed_entities: tuple[str, ...] = field(default_factory=tuple)   # T / E
    file_ids: tuple[str, ...] = field(default_factory=tuple)        # S / D
    doc_types: tuple[str, ...] = field(default_factory=tuple)       # D
    unit_types: tuple[str, ...] = field(default_factory=tuple)      # C / A
    chain_view: str | None = None  # K: 'current_version' | 'all_versions' | 'history_only'
    field_filters: tuple[dict, ...] = field(default_factory=tuple)  # F / Q
    # Q-mode SQL payload — kept as a passthrough dict; the Q-mode
    # validator (B4b) will type-check it. None for non-Q modes.
    q_payload: dict[str, Any] | None = None
    # Free-form rationale — surfaces in the Plan inspector.
    notes: str | None = None
    model_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        """JSONB-serializable shape — matches query_log.plan."""
        return {
            "mode": self.mode,
            "intent": self.intent,
            "intent_confidence": self.intent_confidence,
            "seed_entities": list(self.seed_entities),
            "file_ids": list(self.file_ids),
            "doc_types": list(self.doc_types),
            "unit_types": list(self.unit_types),
            "chain_view": self.chain_view,
            "field_filters": list(self.field_filters),
            "q_payload": self.q_payload,
            "notes": self.notes,
            "model_id": self.model_id,
        }


class Planner(Protocol):
    async def plan(
        self,
        query: str,
        intent: IntentResult,
        *,
        requested_mode: str | None = None,
    ) -> Plan: ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def default_mode_for_intent(intent_label: str) -> str:
    """Pure-function intent → mode dispatch. Falls back to H for unknown."""
    return _INTENT_TO_MODE.get(intent_label, DEFAULT_MODE)


# Cheap regex for extracting unit-type keywords from the query.
_UNIT_KEYWORDS = ("clause", "transaction", "row", "line_item", "decision",
                  "component", "invoice", "amendment")


def _extract_unit_types(query: str) -> tuple[str, ...]:
    q = (query or "").lower()
    return tuple(kw for kw in _UNIT_KEYWORDS if kw in q)


# Chain-view cues for K-mode.
def _infer_chain_view(query: str) -> str:
    q = (query or "").lower()
    if any(k in q for k in (
        "history", "all versions", "evolved", "over time", "every version",
    )):
        return "all_versions"
    if any(k in q for k in (
        "previous", "earlier", "history only", "before the current",
    )):
        return "history_only"
    # Default: current_version
    return "current_version"


# ---------------------------------------------------------------------------
# IdentityPlanner
# ---------------------------------------------------------------------------


class IdentityPlanner:
    """Deterministic planner. Maps intent → mode using _INTENT_TO_MODE,
    honors a request override when valid, and fills in mode-specific
    parameters by lightweight regex over the query."""

    MODEL_ID = "identity-planner-v1"

    async def plan(
        self,
        query: str,
        intent: IntentResult,
        *,
        requested_mode: str | None = None,
    ) -> Plan:
        # Request override wins when it's a valid mode (and not 'H' which
        # is the explicit "let the planner decide" default for legacy
        # clients).
        if requested_mode and requested_mode in QUERY_MODES and requested_mode != "H":
            mode = requested_mode
        else:
            mode = default_mode_for_intent(intent.label)

        unit_types = _extract_unit_types(query) if mode in ("C", "A") else ()
        chain_view = _infer_chain_view(query) if mode == "K" else None

        notes = (
            f"intent={intent.label} (conf={intent.confidence:.2f}) → mode={mode}"
        )

        return Plan(
            mode=mode,
            intent=intent.label,
            intent_confidence=intent.confidence,
            unit_types=unit_types,
            chain_view=chain_view,
            notes=notes,
            model_id=self.MODEL_ID,
        )


# ---------------------------------------------------------------------------
# Gemini planner
# ---------------------------------------------------------------------------


_GEMINI_SYSTEM_PROMPT = (
    "You are a query planner for a knowledge base. Given the user's query, "
    "the intent label, and the available modes, return STRICTLY a JSON "
    "object: {\"mode\": one of "
    f"{list(QUERY_MODES)}, "
    "\"unit_types\": list[str], \"chain_view\": str|null, "
    "\"doc_types\": list[str], \"notes\": str|null}. "
    "Default to 'H' when uncertain. Choose 'Q' only for aggregation / "
    "set-op queries. Choose 'K' for chain-aware / temporal-history. "
    "Choose 'T' for multi-hop entity questions. Choose 'G' for "
    "corpus-level summary requests."
)


def _parse_plan_json(raw: str, intent: IntentResult) -> Plan:
    """Tolerant parser. Falls back to identity mapping on parse failure."""
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
        return Plan(
            mode=default_mode_for_intent(intent.label),
            intent=intent.label,
            intent_confidence=intent.confidence,
            notes="parse_error",
        )
    if not isinstance(data, dict):
        return Plan(
            mode=default_mode_for_intent(intent.label),
            intent=intent.label,
            intent_confidence=intent.confidence,
            notes="parse_error",
        )
    mode = str(data.get("mode") or "").strip().upper()
    if mode not in QUERY_MODES:
        mode = default_mode_for_intent(intent.label)

    def _str_list(key: str) -> tuple[str, ...]:
        val = data.get(key) or []
        if not isinstance(val, list):
            return ()
        return tuple(str(v) for v in val if isinstance(v, str) and v.strip())

    chain_view = data.get("chain_view")
    if chain_view is not None and not isinstance(chain_view, str):
        chain_view = None
    if mode == "K" and not chain_view:
        chain_view = "current_version"

    return Plan(
        mode=mode,
        intent=intent.label,
        intent_confidence=intent.confidence,
        unit_types=_str_list("unit_types"),
        doc_types=_str_list("doc_types"),
        chain_view=chain_view,
        notes=str(data.get("notes")) if data.get("notes") else None,
    )


class GeminiPlanner:
    """Gemini Flash → constrained JSON plan."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: Any | None = None,
    ) -> None:
        if client is None:
            if not api_key:
                raise ValueError("GeminiPlanner requires api_key or client")
            from google.genai import Client
            client = Client(api_key=api_key)
        self._client = client
        self._model = os.environ.get("KB_QUERY_MODEL") or "gemini-2.5-flash"

    async def plan(
        self,
        query: str,
        intent: IntentResult,
        *,
        requested_mode: str | None = None,
    ) -> Plan:
        # Honor an explicit valid request override without an LLM call.
        if requested_mode and requested_mode in QUERY_MODES and requested_mode != "H":
            return Plan(
                mode=requested_mode,
                intent=intent.label,
                intent_confidence=intent.confidence,
                notes="explicit_mode_override",
                model_id=self._model,
            )

        # Inventory intent → mode I, no LLM call. The Gemini planner's
        # system prompt doesn't enumerate mode I (we'd have to retrain
        # it on every new mode); short-circuit here keeps the contract
        # stable: when the intent classifier says "inventory" the
        # planner ALWAYS picks I, regardless of LLM weather.
        if intent.label == "inventory":
            return Plan(
                mode="I",
                intent=intent.label,
                intent_confidence=intent.confidence,
                notes="inventory_short_circuit",
                model_id=self._model,
            )

        from google.genai import types
        config = types.GenerateContentConfig(
            system_instruction=_GEMINI_SYSTEM_PROMPT,
            max_output_tokens=400,
            response_mime_type="application/json",
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=(
                    f"Intent: {intent.label} (conf={intent.confidence:.2f})\n"
                    f"Query: {query}\n\nReturn JSON only."
                ),
                config=config,
            )
        except Exception:
            # Fail-safe: degrade to identity mapping.
            return Plan(
                mode=default_mode_for_intent(intent.label),
                intent=intent.label,
                intent_confidence=intent.confidence,
                notes="llm_error_fell_back_identity",
                model_id=self._model,
            )

        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return Plan(
                mode=default_mode_for_intent(intent.label),
                intent=intent.label,
                intent_confidence=intent.confidence,
                notes="empty_response",
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
        plan = _parse_plan_json(raw_text, intent)
        # Re-attach model id.
        return Plan(
            mode=plan.mode, intent=plan.intent,
            intent_confidence=plan.intent_confidence,
            seed_entities=plan.seed_entities, file_ids=plan.file_ids,
            doc_types=plan.doc_types, unit_types=plan.unit_types,
            chain_view=plan.chain_view, field_filters=plan.field_filters,
            q_payload=plan.q_payload, notes=plan.notes,
            model_id=self._model,
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_planner() -> Planner:
    selector = (os.environ.get("KB_PLANNER") or "auto").lower()
    if selector == "auto":
        selector = "gemini" if os.environ.get("KB_GEMINI_API_KEY") else "identity"
    if selector == "identity":
        return IdentityPlanner()
    if selector == "gemini":
        api_key = os.environ.get("KB_GEMINI_API_KEY")
        if not api_key:
            raise ValueError("KB_PLANNER=gemini requires KB_GEMINI_API_KEY")
        return GeminiPlanner(api_key=api_key)
    raise ValueError(
        f"Unknown KB_PLANNER value: {selector!r} "
        f"(expected 'identity', 'gemini', or 'auto')"
    )
