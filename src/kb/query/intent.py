"""B4a / WA-9 — Intent classifier.

Architecture §6 step 1: maps a user query to one of 10 intent labels so
the planner can pick the right mode + the retriever can gate channels.

Spec labels (architecture §6 step 1):

  factoid          — direct fact lookup, expects a single span
  vague            — under-specified; will benefit from rewriting
  multi-hop        — needs traversal across entities
  global/thematic  — corpus-level summary, no single span
  negative         — "what doesn't exist", "show me failures"
  adversarial      — out-of-scope, PII, jailbreak, etc. — refuse early
  aggregation      — count / sum / avg — routes to Q-mode (B4b)
  set_operation    — intersect / union / except — Q-mode set ops
  temporal_history — "what changed", "version history"
  chain_aware      — "amended by", "supersedes" — routes to K-mode

Three impls (mirrors the CRAG / faithfulness factory pattern):

  IdentityIntentClassifier — keyword heuristics; deterministic, CI-default.
  GeminiIntentClassifier   — single Gemini Flash call with constrained JSON.
  make_intent_classifier() — KB_INTENT_CLASSIFIER ∈ {identity, gemini, auto}.

The classifier returns a label + a 0-1 confidence + free-form notes. The
planner (kb.query.planner) consumes this to pick a mode.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Protocol


INTENT_LABELS: tuple[str, ...] = (
    "factoid",
    "vague",
    "multi-hop",
    "global/thematic",
    "negative",
    "adversarial",
    "aggregation",
    "set_operation",
    "temporal_history",
    "chain_aware",
    # Inventory — "what types of docs do I have", "list my files",
    # "how many invoices", "show me my contracts". The answer comes
    # from `files` table metadata, not chunk content; the orchestrator
    # short-circuits retrieval + LLM for these and returns a SQL-
    # rendered markdown table directly. Pattern-matched deterministically
    # (see `INVENTORY_PATTERNS`) so it doesn't depend on the LLM's
    # mood. Added 2026-05-26 after Q2 deep-dive showed the LLM
    # classifier landed `factoid` on "what types of documents do I have"
    # and the resulting chunk-based answer summarised content, not types.
    "inventory",
    # The 7 modes that were previously declared but unreachable.
    # Each one routes to its eponymous Q-mode planner mode (E/F/S/D/M/C/A).
    "entity_lookup",      # "who is X", "tell me about <entity>"           → E
    "field_filter",       # "find docs where amount > 1000"                 → F
    "scoped_summarize",   # "summarize this contract" (scoped to a file)    → S
    "doc_metadata",       # "PDFs from 2024", "files by author X"           → D
    "mention_search",     # "where is X mentioned", "all references to X"   → M
    "unit_filter",        # "clauses about non-compete", "transactions > $X" → C
    "anomaly",            # "what's unusual", "outliers", "rare X"          → A
)


# Deterministic pattern match for the inventory intent — runs BEFORE
# the configured classifier (LLM or heuristic) so an "obvious" inventory
# question always routes correctly regardless of LLM nondeterminism.
# Returns (True, confidence) on match or (False, 0.0).
#
# Patterns are intentionally narrow — only fire when the query is CLEARLY
# about metadata listing, not a content question that happens to use
# similar words.
INVENTORY_PATTERNS: tuple[re.Pattern[str], ...] = (
    # "what types/kinds of (docs|files|documents) ..."
    re.compile(r"\bwhat\s+(types?|kinds?)\s+of\s+(documents?|docs?|files?)\b", re.IGNORECASE),
    # "what documents/files do I have"
    re.compile(r"\bwhat\s+(documents?|docs?|files?)\s+(do\s+(i|we)|are)\b", re.IGNORECASE),
    # "list ... (documents|docs|files)" — loose so "list all the docs",
    # "list out my files", "list every uploaded doc" all match. 40-char
    # window keeps false matches contained (a long query that mentions
    # both "list" and "docs" 200 chars apart probably isn't an inventory
    # ask).
    re.compile(r"\blist\b[^.!?\n]{0,40}\b(documents?|docs?|files?)\b", re.IGNORECASE),
    # "show (me)? (my|all|the)? (documents|docs|files)" — "me" optional
    # so "show all docs" matches the same as "show me all docs".
    re.compile(r"\bshow\s+(?:me\s+)?(?:my|all|the|every)?\s*(documents?|docs?|files?)\b", re.IGNORECASE),
    # "how many (documents|docs|files|invoices|contracts|...)" — single
    # noun form, EXCLUDING time-qualified asks ("how many invoices last
    # quarter" wants aggregation Q-mode + date filter, not a raw count
    # by doc_type). The negative lookahead drops any query that pairs
    # the count with a temporal qualifier.
    re.compile(
        r"\bhow\s+many\s+"
        r"(documents?|docs?|files?|invoices?|contracts?|emails?|reports?)\b"
        r"(?!.*\b(last|this|since|next|past|previous|after|before|in\s+\d)\b)",
        re.IGNORECASE,
    ),
    # "what's in (my|the) (workspace|corpus|knowledge\s*base)"
    re.compile(r"\bwhat'?s?\s+in\s+(my|the|this)\s+(workspace|corpus|knowledge\s*base|kb)\b", re.IGNORECASE),
    # "inventory of (my|the) ..."
    re.compile(r"\binventory\s+of\b", re.IGNORECASE),
)


# Negative guard. If the query SCOPES to a specific file / doctype /
# entity ("in bank-statement", "from invoice.pdf", "inside the
# contract"), it's NOT a workspace-wide inventory ask — it's a content
# question about a specific thing, and downstream wants S/H/F mode, not
# I-mode's "show me the 26-doc table".
#
# This caught a reproducible chat bug: after a turn focused on
# bank-statement.xlsx, the follow-up "What else is in bank-statement?"
# either matched an inventory regex (post context-resolver rewrite to
# "what documents are…") OR got LLM-labelled as `inventory`. Either
# path routed to I-mode which ignores file scope and re-rendered the
# global workspace inventory — looking to the user like the previous
# turn's context evaporated.
#
# Two-step check:
#   1. _HAS_IN_SCOPE — query mentions "in/inside/within <something>"
#   2. _WORKSPACE_SCOPE_PHRASES — that something IS one of the
#      workspace-scope synonyms (workspace / corpus / kb / files / docs)
# Inventory fires only when (1) doesn't match, or when both match.
# Anything else (e.g. "in bank-statement", "inside the invoice") is a
# file-scoped content question and routes to S/H/F mode instead.
#
# Why not a single negative-lookahead regex: the negative-lookahead
# form lets the engine skip the optional determiner ("my/the/this")
# and then accept the determiner itself as the scope noun, so "in my
# workspace" still matches and false-positive-rejects. Two separate
# checks are clearer and don't have that backtracking hole.
_HAS_IN_SCOPE = re.compile(
    r"\b(?:in|inside|within)\s+\S+", re.IGNORECASE,
)
_WORKSPACE_SCOPE_PHRASES = re.compile(
    r"\b(?:in|inside|within)\s+(?:(?:my|the|this|that|all)\s+)?"
    r"(?:workspace|corpus|kb|knowledge\s*base|files?|docs?|documents?|"
    r"inventory|catalog|index|database|system)\b",
    re.IGNORECASE,
)


def detect_inventory_intent(query: str) -> tuple[bool, float]:
    """Pattern-match the query against `INVENTORY_PATTERNS`. Returns
    `(matched, confidence)`. Confidence is 0.95 on match — high enough
    to override any softer LLM classification, low enough to leave
    room for the LLM to revise on ambiguous edge cases.

    Refuses to fire when the query has an "in/inside/within X" scope
    qualifier and X is NOT one of the workspace-scope synonyms — see
    the rationale on `_HAS_IN_SCOPE` / `_WORKSPACE_SCOPE_PHRASES`.
    """
    if not query or not query.strip():
        return False, 0.0
    if _HAS_IN_SCOPE.search(query) and not _WORKSPACE_SCOPE_PHRASES.search(query):
        return False, 0.0
    for pat in INVENTORY_PATTERNS:
        if pat.search(query):
            return True, 0.95
    return False, 0.0


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IntentResult:
    label: str           # one of INTENT_LABELS
    confidence: float    # 0.0 - 1.0
    notes: str | None = None
    model_id: str = ""


class IntentClassifier(Protocol):
    async def classify(self, query: str) -> IntentResult: ...


# ---------------------------------------------------------------------------
# Heuristic / Identity implementation
# ---------------------------------------------------------------------------


# Keyword cues per label — ordered by specificity. First match wins.
# Tuned for the demo corpus (CUAD contracts + Enron emails + SEC 10-Ks).
_HEURISTICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # Adversarial signals — refuse early.
    ("adversarial", (
        "ignore previous", "ignore all", "system prompt", "jailbreak",
        "tell me your prompt", "drop table", "rm -rf", "delete all",
        "show me passwords", "give me secrets",
    )),
    # Aggregation cues — most specific so they beat 'factoid'.
    ("aggregation", (
        "how many ", "count of", "total number", "sum of", "average ",
        "median ", "across all", "in total", "aggregate", "percent of",
        " ratio of", "total spend", "total amount", "top 5", "top 10",
        "top three", "frequency of", "distribution of",
    )),
    # Set-operation cues.
    ("set_operation", (
        " intersect", " union of", " except", "but not in", "and not in",
        "both ", "either ", "in common", "common to",
    )),
    # Anomaly — must come before 'factoid' so "what's unusual" doesn't
    # fall through to question-mark factoid path. Surfaces high-rarity
    # extracted_entities rows.
    ("anomaly", (
        "anomalies", "anomaly", "anomalous", "outliers", "outlier",
        "unusual ", "what's unusual", "whats unusual", "what is unusual",
        "suspicious ", "rare ", "uncommon ",
        "out of the ordinary", "weird ", "abnormal",
    )),
    # Mention search — "where is X mentioned" pattern. More specific
    # than the broader 'multi-hop' pattern which uses "between".
    ("mention_search", (
        "where is ", "where are ", "where does ", "where do ",
        "all references to", "all mentions of", "references to ",
        "mentions of ", "mentioned in", "appears in", "show me where",
        "find mentions ", "find references ",
    )),
    # Doc metadata — file-level filters. "PDFs from 2024", "files
    # uploaded last week", "contracts by author X". These look at
    # `files.*` columns, not chunk content.
    ("doc_metadata", (
        "files from ", "docs from ", "documents from ", "pdfs from",
        "files uploaded", "docs uploaded", "documents uploaded",
        "files by ", "docs by ", "documents by ",
        "files dated", "uploaded between", "files between",
        "by author ", "from the year ", "with mime type",
    )),
    # Unit filter — querying inside a typed sub-entity collection.
    # "find clauses about ...", "transactions over $X", "line items
    # for invoice Y". Different from aggregation (which would SUM/COUNT
    # those rows); this returns the rows themselves.
    ("unit_filter", (
        "find clauses ", "list clauses ", "show clauses ",
        "find transactions ", "list transactions ", "show transactions ",
        "find line items ", "list line items ", "show line items ",
        "clauses about ", "transactions over ", "transactions under ",
        "line items for ", "line items where ", "messages about",
        "messages where", "lab results where", "lab results above",
        "lab results below", "rows where ",
    )),
    # Field filter — generic "find X where field = value" pattern.
    # Sits between unit_filter (typed rows) and doc_metadata (file-
    # level fields). Captures "list contracts with effective_date >
    # 2024", "show invoices over $1000", "find anything where X=Y".
    ("field_filter", (
        " where ", " with field ", "filter by ",
        " greater than ", " less than ", " more than ",
        "find anything ", "find docs with",
        "having field ", "match field ",
    )),
    # Scoped summarize — summary intent BUT scoped to a specific
    # file / contract / doc, not corpus-level. Must include a doc-like
    # noun ("this document/contract/file/email/...") so it doesn't fire
    # on broader "this corpus" / "this workspace" asks (those route to
    # global/thematic).
    ("scoped_summarize", (
        "summarize this document", "summarize this contract",
        "summarize this file", "summarize this doc",
        "summarize the document", "summarize the contract",
        "summarize the file", "summarize this email",
        "summarize this pdf",
        "give me an overview of this document",
        "give me an overview of this contract",
        "overview of this document", "overview of this contract",
        "overview of the document", "tl;dr of this",
        "what does this document say", "what's in this document",
    )),
    # Chain-aware cues — explicit doc-chain references. These are the
    # MOST specific lineage signal, so we test them before the broader
    # temporal_history cues. "amends the prior version" → chain_aware
    # (the amend relation is the load-bearing word), not temporal_history.
    ("chain_aware", (
        "supersedes", "amends", "amended by", "amended ", " amend ",
        "current version", "latest version", "newest version",
        "chain of", "in the thread", "thread context", "doc chain",
        "all versions",
    )),
    # Temporal-history cues — "what changed over time" (broader than chain).
    ("temporal_history", (
        " changed", "version history", "what was the previous",
        "earlier version", "prior version", "over time", "evolved",
        " timeline", "history of", "when did",
    )),
    # Global / thematic.
    ("global/thematic", (
        "summarize", "overview of", "themes ", " in general",
        "high-level summary", "give me a summary", "what's this corpus",
        "what is this corpus", " strategy ", " landscape",
    )),
    # Negative.
    ("negative", (
        "doesn't", "does not", "no mention", "absent", "missing",
        "not present", "without ", "lacking ", "not contain",
    )),
    # Multi-hop.
    ("multi-hop", (
        " related to ", " connected to ", "links between", "relationship between",
        "between ", "via ", " through ", "path from", "shortest path",
        "who works with", " involves ",
    )),
    # Entity lookup — single-entity ask: "who is X" / "tell me about X" /
    # "profile of X". Sits AFTER multi-hop so "between X and Y" still
    # routes to multi-hop. Excludes "what is the" / "what's the" — those
    # are syntactic-factoid queries (handled by _FACTOID_HINTS later);
    # entity_lookup is the broader "give me everything about <X>".
    ("entity_lookup", (
        "who is ", "who was ", "who's ",
        "tell me about ", "tell me more about",
        "background on ", "profile of ", "info on ",
        "information on ", "details on ", "details about ",
    )),
    # Vague.
    ("vague", (
        "what about", "talk about", "what's going on",
        "anything interesting", "give me everything",
    )),
)


_FACTOID_HINTS = (
    "what is the", "what's the", "who is the", "where is the",
    "define ", "value of", "amount of", "name of", "date of",
)


def _heuristic_label(query: str) -> tuple[str, float]:
    """Pure-function keyword classifier. Returns (label, confidence).

    Confidence is heuristic: 0.6 for a heuristic match, 0.7 for a factoid
    hint, 0.4 for the default 'vague' fallback. The planner treats
    confidence < 0.5 as "low signal; default to H mode"."""
    q = (query or "").lower().strip()
    if not q:
        return ("vague", 0.4)
    for label, cues in _HEURISTICS:
        if any(cue in q for cue in cues):
            return (label, 0.6)
    if any(cue in q for cue in _FACTOID_HINTS):
        return ("factoid", 0.7)
    # Short question with question mark + no other cues → factoid.
    if q.endswith("?") and len(q) < 80:
        return ("factoid", 0.55)
    return ("vague", 0.4)


class IdentityIntentClassifier:
    """Pure-function keyword classifier. CI default; deterministic.

    Mirrors the IdentityCragGate / IdentityFaithfulnessGate pattern —
    must NEVER raise, must always return a valid label."""

    MODEL_ID = "identity-heuristic-v1"

    async def classify(self, query: str) -> IntentResult:
        # Inventory short-circuit — deterministic pattern match takes
        # precedence over the heuristic. See `INVENTORY_PATTERNS`.
        inv, inv_conf = detect_inventory_intent(query)
        if inv:
            return IntentResult(
                label="inventory", confidence=inv_conf,
                notes="pattern_match", model_id=self.MODEL_ID,
            )
        label, conf = _heuristic_label(query)
        if label not in INTENT_LABELS:
            label = "vague"
        return IntentResult(label=label, confidence=conf, model_id=self.MODEL_ID)


# ---------------------------------------------------------------------------
# Gemini implementation
# ---------------------------------------------------------------------------


_SYSTEM_PROMPT = (
    "You are an intent classifier for a knowledge-base query system. Given "
    "a user query, return STRICTLY a JSON object: "
    "{\"label\": str, \"confidence\": 0.0-1.0, \"notes\": str|null}. "
    f"The label must be one of: {list(INTENT_LABELS)}.\n\n"
    "Pick the MOST SPECIFIC label that applies:\n"
    "  - aggregation: 'how many invoices', 'sum of debits', 'average X' — \n"
    "    SQL-style numeric aggregation across multiple docs.\n"
    "  - anomaly: 'what's unusual', 'outliers', 'rare transactions' — \n"
    "    surface rare/anomalous extracted rows, not aggregates.\n"
    "  - inventory: 'list my files', 'how many docs of type X', \n"
    "    'what's in my workspace' — WORKSPACE-WIDE file-metadata \n"
    "    listing, no chunk content. Do NOT use `inventory` when the \n"
    "    query is scoped to a SPECIFIC file/doc/contract/entity \n"
    "    ('what's in bank-statement', 'what else is in the invoice', \n"
    "    'what does this MSA cover') — those are scoped_summarize or \n"
    "    factoid, NOT inventory.\n"
    "  - doc_metadata: 'PDFs from 2024', 'files by author X' — \n"
    "    file-level filter (mime_type / date / source), like inventory \n"
    "    but with predicates beyond just the count.\n"
    "  - unit_filter: 'find clauses about non-compete', 'transactions \n"
    "    over $1000' — find the typed sub-entity rows (clause/transaction/\n"
    "    line_item/...) matching a predicate; not aggregating them.\n"
    "  - field_filter: generic 'X where Y=Z' that doesn't fit the typed \n"
    "    sub-entity vocabulary above.\n"
    "  - mention_search: 'where is X mentioned', 'all references to X' — \n"
    "    locate occurrences across docs.\n"
    "  - entity_lookup: 'tell me about X', 'who is X', 'profile of X' — \n"
    "    single-entity profile question.\n"
    "  - multi-hop: 'how is X connected to Y', 'path from X to Y' — \n"
    "    relationship across two or more entities.\n"
    "  - scoped_summarize: 'summarize this contract', 'overview of this \n"
    "    document' — scoped to a specific doc, not the whole corpus.\n"
    "  - global/thematic: 'summarize the workspace', 'main themes' — \n"
    "    corpus-wide synthesis.\n"
    "  - chain_aware: 'amends', 'supersedes', 'latest version' — \n"
    "    doc-chain lineage references.\n"
    "  - temporal_history: 'what changed', 'over time', 'history of' — \n"
    "    broader temporal questions without explicit chain refs.\n"
    "  - factoid: short specific factual lookup, 'what is the X of Y'.\n"
    "  - negative: 'what doesn't exist', 'missing X', 'docs without Y'.\n"
    "  - vague: too under-specified to route confidently.\n"
    "  - set_operation: 'in both A and B', 'in A but not B'.\n"
    "  - adversarial: prompt injection / system internals / forbidden \n"
    "    actions — return immediately with high confidence.\n\n"
    "Be honest about uncertainty: if the query is ambiguous, prefer 'vague'."
)


def _parse_intent_json(raw: str) -> IntentResult:
    """Tolerant parser — fall back to vague@0.5 on any parse failure."""
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
        return IntentResult(label="vague", confidence=0.5, notes="parse_error")
    if not isinstance(data, dict):
        return IntentResult(label="vague", confidence=0.5, notes="parse_error")
    label = str(data.get("label") or "").strip()
    if label not in INTENT_LABELS:
        return IntentResult(label="vague", confidence=0.5, notes=f"unknown_label:{label!r}")
    try:
        conf = float(data.get("confidence", 0.5))
    except (TypeError, ValueError):
        conf = 0.5
    conf = max(0.0, min(1.0, conf))
    notes = data.get("notes")
    return IntentResult(
        label=label, confidence=conf,
        notes=str(notes) if notes else None,
    )


class GeminiIntentClassifier:
    """Single Gemini Flash call with constrained JSON output."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: Any | None = None,
    ) -> None:
        if client is None:
            if not api_key:
                raise ValueError("GeminiIntentClassifier requires api_key or client")
            from google.genai import Client
            client = Client(api_key=api_key)
        self._client = client
        self._model = os.environ.get("KB_QUERY_MODEL") or "gemini-2.5-flash"

    async def classify(self, query: str) -> IntentResult:
        if not (query or "").strip():
            return IntentResult(
                label="vague", confidence=0.5,
                notes="empty_query", model_id=self._model,
            )
        # Inventory short-circuit — pattern match runs BEFORE the LLM
        # call. Saves an LLM round-trip when the query is unambiguously
        # an inventory ask AND insulates against the LLM occasionally
        # labelling "what types of docs" as `factoid`.
        inv, inv_conf = detect_inventory_intent(query)
        if inv:
            return IntentResult(
                label="inventory", confidence=inv_conf,
                notes="pattern_match", model_id=self._model,
            )
        from google.genai import types
        config = types.GenerateContentConfig(
            system_instruction=_SYSTEM_PROMPT,
            max_output_tokens=200,
            response_mime_type="application/json",
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=f"Query: {query}\n\nReturn JSON only.",
                config=config,
            )
        except Exception:
            # Fail-safe: a transient LLM failure must not block /chat.
            # Fall through to the heuristic classifier.
            label, conf = _heuristic_label(query)
            return IntentResult(
                label=label, confidence=conf,
                notes="llm_error_fellback_heuristic",
                model_id=self._model,
            )

        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            label, conf = _heuristic_label(query)
            return IntentResult(
                label=label, confidence=conf,
                notes="empty_response", model_id=self._model,
            )
        raw_text = ""
        content = getattr(candidates[0], "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            t = getattr(part, "text", None)
            if t:
                raw_text = t
                break
        result = _parse_intent_json(raw_text)
        # Preserve the model id on the LLM-derived result.
        return IntentResult(
            label=result.label, confidence=result.confidence,
            notes=result.notes, model_id=self._model,
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_intent_classifier() -> IntentClassifier:
    """Pick a classifier from `KB_INTENT_CLASSIFIER`.

      identity → IdentityIntentClassifier (default, fail-safe)
      gemini   → GeminiIntentClassifier (requires KB_GEMINI_API_KEY)
      auto     → gemini if key else identity
    """
    selector = (os.environ.get("KB_INTENT_CLASSIFIER") or "auto").lower()
    if selector == "auto":
        selector = "gemini" if os.environ.get("KB_GEMINI_API_KEY") else "identity"
    if selector == "identity":
        return IdentityIntentClassifier()
    if selector == "gemini":
        api_key = os.environ.get("KB_GEMINI_API_KEY")
        if not api_key:
            raise ValueError(
                "KB_INTENT_CLASSIFIER=gemini requires KB_GEMINI_API_KEY"
            )
        return GeminiIntentClassifier(api_key=api_key)
    raise ValueError(
        f"Unknown KB_INTENT_CLASSIFIER value: {selector!r} "
        f"(expected 'identity', 'gemini', or 'auto')"
    )
