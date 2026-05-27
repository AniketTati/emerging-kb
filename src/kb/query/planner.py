"""B4a / WA-10 — Schema-aware planner.

Architecture §6 step 3: parses an intent + query into a typed `Plan` that
picks one of the 13 retrieval modes (E/F/S/H/T/M/G/D/C/A/Q/K/I) and
carries mode-specific parameters.

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
  I — inventory                (SQL metadata listing; orchestrator short-circuits)

Two impls (mirrors the Contextualizer / Summarizer pattern):
  IdentityPlanner — deterministic intent→mode mapping; no LLM.
  LLMPlanner      — provider-neutral; takes a `JsonLLMClient`. Today's
                    factory wires Gemini-Flash or Claude based on env.

Factory: `KB_PLANNER ∈ {identity, gemini, anthropic, auto}`.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from kb.query.intent import IntentResult
from kb.query.llm_client import JsonLLMClient, LLMCallError, make_query_llm_client


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
    "inventory":        "I",   # SQL metadata listing
    # The 7 dedicated modes — each routes to its eponymous handler in
    # mode_router.py. Each handler degrades to H if its specific signal
    # can't be resolved (no entity match for E, no rows for C, etc.).
    "entity_lookup":    "E",
    "field_filter":     "F",
    "scoped_summarize": "S",
    "doc_metadata":     "D",
    "mention_search":   "M",
    "unit_filter":      "C",
    "anomaly":          "A",
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
_UNIT_KEYWORDS = (
    "clause", "transaction", "row", "line_item", "decision",
    "component", "invoice", "amendment", "message", "lab_result",
    "analyte_result", "payment_milestone", "deliverable",
    "action", "work_experience", "education", "qualification",
)


def _extract_unit_types(query: str) -> tuple[str, ...]:
    q = (query or "").lower()
    hits = []
    for kw in _UNIT_KEYWORDS:
        # Match singular OR plural (clauses, transactions, line_items, …)
        if kw in q or (kw + "s") in q:
            hits.append(kw)
    return tuple(hits)


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


# Doc-type cues for D-mode — surface keywords the LLM or user typed
# referring to file-type / doc-type / mime. We're conservative: only
# emit doc_types when the user spelled out a recognizable type.
_DOC_TYPE_KEYWORDS = (
    "bank_statement", "bank statement", "invoice", "contract",
    "msa", "master_services_agreement", "master services agreement",
    "email", "email_thread", "lab_report", "lab report",
    "incident_report", "postmortem", "resume", "performance_review",
    "performance review", "side_letter", "side letter", "sow",
    "statement of work", "nda", "subscription_agreement",
    "subscription agreement", "case_study", "case study",
)


def _extract_doc_types(query: str) -> tuple[str, ...]:
    q = (query or "").lower()
    hits = []
    for kw in _DOC_TYPE_KEYWORDS:
        if kw in q:
            # Normalize "bank statement" → "bank_statement" etc.
            hits.append(kw.replace(" ", "_"))
    # Deduplicate while preserving order.
    seen = set()
    out = []
    for h in hits:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return tuple(out)


# Seed-entity extraction — capitalized multi-word sequences are likely
# entity surface forms ("John Doe", "Acme Corp"). Used by E/T modes.
_CAPITALIZED_TOKEN = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})\b")


def _extract_seed_entity_surface_forms(query: str) -> tuple[str, ...]:
    if not query:
        return ()
    hits = [m.group(1) for m in _CAPITALIZED_TOKEN.finditer(query)]
    # Deduplicate, cap at 5 to keep downstream resolution cheap.
    seen = set()
    out = []
    for h in hits:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return tuple(out[:5])


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

        unit_types = (
            _extract_unit_types(query) if mode in ("C", "A") else ()
        )
        chain_view = _infer_chain_view(query) if mode == "K" else None
        # Doc-type hints feed D mode (file-metadata filter) and also
        # surface on M/C/F as best-effort context for downstream
        # filters / boosts.
        doc_types = (
            _extract_doc_types(query) if mode in ("D", "F", "M", "C") else ()
        )
        # Surface entity name candidates for E/T modes. The mode handler
        # resolves these against the `entities` table before applying.
        seed_entities = (
            _extract_seed_entity_surface_forms(query)
            if mode in ("E", "T", "M") else ()
        )

        notes = (
            f"intent={intent.label} (conf={intent.confidence:.2f}) → mode={mode}"
        )

        return Plan(
            mode=mode,
            intent=intent.label,
            intent_confidence=intent.confidence,
            seed_entities=seed_entities,
            doc_types=doc_types,
            unit_types=unit_types,
            chain_view=chain_view,
            notes=notes,
            model_id=self.MODEL_ID,
        )


# ---------------------------------------------------------------------------
# LLM planner — provider-neutral (Gemini / Anthropic via JsonLLMClient)
# ---------------------------------------------------------------------------


# Routing prompt — fired once per query to pick the mode + carry the
# mode-specific params. Identical across providers; differences in
# JSON discipline are handled by each adapter (Gemini uses
# `response_mime_type=application/json`; Anthropic appends a "JSON
# only" reminder + we strip stray code fences).
_ROUTING_SYSTEM_PROMPT = (
    "You are a query planner for a knowledge base. Given the user's query "
    "and the intent label, return STRICTLY a JSON object: "
    "{\"mode\": one of "
    f"{list(QUERY_MODES)}, "
    "\"unit_types\": list[str], \"chain_view\": str|null, "
    "\"doc_types\": list[str], \"seed_entities\": list[str], "
    "\"notes\": str|null}.\n\n"
    "Pick the mode based on what the user is really asking for:\n"
    "  H — default hybrid; pick this when no other mode clearly fits.\n"
    "  Q — SQL aggregation (SUM/COUNT/AVG/MIN/MAX). Use for 'how many', \n"
    "      'total of', 'average across all'. Q-payload generated separately.\n"
    "  K — doc-chain aware (amendments / current version / supersedes).\n"
    "  T — multi-hop graph traversal between entities.\n"
    "  G — corpus-level synthesis ('summarize the workspace').\n"
    "  I — inventory metadata listing ('list my files', 'what doc types').\n"
    "  E — single-entity profile ('tell me about Acme Corp'). Carry the\n"
    "      entity name in seed_entities.\n"
    "  F — generic 'X where Y=Z' field-predicate filter, when the user\n"
    "      doesn't name a typed sub-entity. Carry filters in field_filters.\n"
    "  S — scoped summarize a SPECIFIC document/contract/file (not corpus).\n"
    "      Carry the target in file_ids when you can resolve it.\n"
    "  D — doc-metadata filter (file-level: doc_type, date, source).\n"
    "      Carry the targeted doc_types.\n"
    "  M — mention search ('where is X mentioned'). Carry the term in\n"
    "      seed_entities so the handler can lookup extracted_mentions.\n"
    "  C — atomic-unit filter: surface typed sub-entity rows (transactions,\n"
    "      clauses, line items, messages) matching the query. Carry the\n"
    "      unit_type(s) in unit_types.\n"
    "  A — anomaly: surface rare/unusual extracted_entities rows by\n"
    "      rarity_score. Carry unit_types when the query names a type\n"
    "      ('unusual transactions' → unit_types=['transaction']).\n\n"
    "Examples:\n"
    "  'sum all debits' → Q\n"
    "  'how is Acme connected to Vertex?' → T (seed_entities=['Acme','Vertex'])\n"
    "  'summarize the workspace' → G\n"
    "  'list my contracts' → I\n"
    "  'tell me about Acme Corp' → E (seed_entities=['Acme Corp'])\n"
    "  'find transactions over 1000 in Feb' → C (unit_types=['transaction'])\n"
    "  'anything unusual in the bank statement?' → A (unit_types=['transaction'])\n"
    "  'summarize this contract' → S\n"
    "  'where is Vertex mentioned?' → M (seed_entities=['Vertex'])\n"
    "  'PDFs from 2024' → D (doc_types=[…])\n\n"
    "Default to 'H' when uncertain. Be concise in notes."
)

# Legacy alias kept until call-sites are renamed.
_GEMINI_SYSTEM_PROMPT = _ROUTING_SYSTEM_PROMPT


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
        seed_entities=_str_list("seed_entities"),
        unit_types=_str_list("unit_types"),
        doc_types=_str_list("doc_types"),
        chain_view=chain_view,
        notes=str(data.get("notes")) if data.get("notes") else None,
    )


class LLMPlanner:
    """Provider-neutral planner. Two-call shape:

      1. Routing call — LLM picks `mode` + carries mode-specific params
         (unit_types / chain_view / doc_types / notes).

      2. Q-mode second call — when (1) returns `mode='Q'`, we fire a
         second, narrower call that emits the structured `q_payload`
         against the catalog + grammar. Skipped for non-Q modes so
         normal queries pay only one call.

    The underlying provider (Gemini, Anthropic, …) is injected via the
    `JsonLLMClient` Protocol; this class never imports a vendor SDK
    directly. Construct via `make_planner()` to get the env-driven
    factory, or pass a custom client for tests.
    """

    def __init__(self, llm: JsonLLMClient) -> None:
        self._llm = llm
        self._model = llm.model_id

    async def plan(
        self,
        query: str,
        intent: IntentResult,
        *,
        requested_mode: str | None = None,
    ) -> Plan:
        # Honor an explicit valid request override without an LLM call.
        if requested_mode and requested_mode in QUERY_MODES and requested_mode != "H":
            plan_override = Plan(
                mode=requested_mode,
                intent=intent.label,
                intent_confidence=intent.confidence,
                notes="explicit_mode_override",
                model_id=self._model,
            )
            return await self._maybe_augment_q(query, plan_override)

        # Inventory intent → mode I, no LLM call. The routing prompt
        # doesn't enumerate mode I (we'd have to retrain it on every
        # new mode); short-circuit here keeps the contract stable:
        # when the intent classifier says "inventory" the planner
        # ALWAYS picks I, regardless of LLM weather.
        if intent.label == "inventory":
            return Plan(
                mode="I",
                intent=intent.label,
                intent_confidence=intent.confidence,
                notes="inventory_short_circuit",
                model_id=self._model,
            )

        # ---- Routing call ----
        try:
            raw_text = await self._llm.generate_json(
                user=(
                    f"Intent: {intent.label} (conf={intent.confidence:.2f})\n"
                    f"Query: {query}\n\nReturn JSON only."
                ),
                system=_ROUTING_SYSTEM_PROMPT,
                max_tokens=400,
            )
        except LLMCallError:
            return Plan(
                mode=default_mode_for_intent(intent.label),
                intent=intent.label,
                intent_confidence=intent.confidence,
                notes="llm_error_fell_back_identity",
                model_id=self._model,
            )

        plan = _parse_plan_json(raw_text, intent)
        # Re-attach model id on the routing result.
        plan = Plan(
            mode=plan.mode, intent=plan.intent,
            intent_confidence=plan.intent_confidence,
            seed_entities=plan.seed_entities, file_ids=plan.file_ids,
            doc_types=plan.doc_types, unit_types=plan.unit_types,
            chain_view=plan.chain_view, field_filters=plan.field_filters,
            q_payload=plan.q_payload, notes=plan.notes,
            model_id=self._model,
        )
        return await self._maybe_augment_q(query, plan)

    async def _maybe_augment_q(self, query: str, plan: Plan) -> Plan:
        """Second LLM call to fill `plan.q_payload` when mode='Q'. The
        same provider client is reused — no extra config.

        When the second call fails (refuse / parse_error / validation /
        llm_error), we stash the reason on `notes` prefixed with
        `q_payload_gen:` so `_route_q_mode` can render a coherent
        refusal message naming the actual failure mode.
        """
        if plan.mode != "Q" or plan.q_payload is not None:
            return plan
        from kb.query.q_payload_gen import generate_q_payload
        payload, reason = await generate_q_payload(query, llm=self._llm)
        if payload is not None:
            return Plan(
                mode=plan.mode, intent=plan.intent,
                intent_confidence=plan.intent_confidence,
                seed_entities=plan.seed_entities, file_ids=plan.file_ids,
                doc_types=plan.doc_types, unit_types=plan.unit_types,
                chain_view=plan.chain_view,
                field_filters=plan.field_filters,
                q_payload=payload, notes=plan.notes,
                model_id=plan.model_id,
            )
        new_notes = (
            f"{plan.notes + ' · ' if plan.notes else ''}q_payload_gen: {reason}"
        )
        return Plan(
            mode=plan.mode, intent=plan.intent,
            intent_confidence=plan.intent_confidence,
            seed_entities=plan.seed_entities, file_ids=plan.file_ids,
            doc_types=plan.doc_types, unit_types=plan.unit_types,
            chain_view=plan.chain_view, field_filters=plan.field_filters,
            q_payload=None, notes=new_notes,
            model_id=plan.model_id,
        )


# Back-compat alias — older tests / imports referenced `GeminiPlanner`
# directly. Keeping the name as an alias avoids a sweeping rename in
# this PR; deprecate in a follow-up.
GeminiPlanner = LLMPlanner


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_planner() -> Planner:
    """Pick a planner based on `KB_PLANNER`.

    Selector ∈ {identity, gemini, anthropic, auto} — mirrors
    `make_contextualizer()` / `make_summarizer()`. `auto` probes the
    Gemini key first, then Anthropic, then falls back to Identity.

    The LLM path always uses `LLMPlanner` with a `JsonLLMClient`
    adapter; the vendor SDK is encapsulated in `kb.query.llm_client`.
    """
    llm = make_query_llm_client()
    if llm is None:
        return IdentityPlanner()
    return LLMPlanner(llm)
