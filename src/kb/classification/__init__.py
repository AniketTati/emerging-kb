"""I1 — lightweight pre-chunk document classifier.

The chunker router (`kb.chunking.doc_type_router`) picks a chunking strategy
from the document's `doc_type` (bank_statement → row_per_leaf, contract →
clause_per_leaf, …). But classification historically happened LATE (inside
`extract_kv_tables`, after chunking), so at chunk time `inferred_doc_type` was
always NULL and the type-aware chunkers never fired for PDFs/markdown. This
module classifies a document up front from its first pages, so the right
chunker is selected.

Two impls behind one Protocol:

1. `GeminiDocTypeClassifier` — one cheap Gemini-Flash call on the first ~N
   chars; constrained to emit a doc_type from the router's KNOWN vocabulary
   (or "unknown"). Matching the router's exact keys is load-bearing — a
   free-text label like "bank statement" would never route to row_per_leaf.
2. `IdentityDocTypeClassifier` — returns "unknown" (no key / CI path); the
   router then falls back to MIME/hierarchical exactly as before.

Factory `make_doc_type_classifier()` mirrors the contextualizer/mentions
factories: `auto` probes KB_GEMINI_API_KEY → Identity.
"""

from __future__ import annotations

import json
import os
from typing import Any, Protocol

from kb.chunking.doc_type_router import known_doc_types


DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
# How much of the document to show the classifier. doc_type is usually
# obvious from the first page or two; keep the call cheap.
_CLASSIFY_CHAR_BUDGET = 4000


class DocTypeClassifier(Protocol):
    async def classify(self, *, text: str, file_name: str | None = None) -> str:
        """Return a router doc_type key, or 'unknown'."""
        ...


class IdentityDocTypeClassifier:
    """No-LLM fallback — always 'unknown' (router falls back to MIME/hier)."""

    async def classify(self, *, text: str, file_name: str | None = None) -> str:
        return "unknown"


class GeminiDocTypeClassifier:
    """Gemini Flash, constrained to the router's known doc_type vocabulary."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: Any | None = None,
        model: str | None = None,
    ) -> None:
        if client is None:
            if not api_key:
                raise ValueError(
                    "GeminiDocTypeClassifier requires api_key or client"
                )
            from google.genai import Client
            client = Client(api_key=api_key)
        self._client = client
        self._model = (
            model or os.environ.get("KB_CLASSIFIER_MODEL") or DEFAULT_GEMINI_MODEL
        )

    async def classify(self, *, text: str, file_name: str | None = None) -> str:
        if not (text or "").strip():
            return "unknown"
        vocab = sorted(known_doc_types())
        from google.genai import types

        prompt = (
            "Classify this document into EXACTLY ONE of these types "
            "(return the type string verbatim, or \"unknown\" if none fit):\n"
            + ", ".join(vocab) + ", unknown\n\n"
            + (f"Filename: {file_name}\n" if file_name else "")
            + "Document (first part):\n<doc>\n"
            + text[:_CLASSIFY_CHAR_BUDGET]
            + '\n</doc>\n\nReturn ONLY JSON: {"doc_type": "<type>"}'
        )
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    max_output_tokens=50,
                    response_mime_type="application/json",
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                ),
            )
        except Exception:
            # Classification is best-effort: a failure must not block chunking.
            # Returning 'unknown' degrades to the prior MIME/hier behaviour.
            return "unknown"

        raw = (getattr(response, "text", "") or "").strip()
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        try:
            dt = str(json.loads(raw).get("doc_type", "")).strip()
        except (json.JSONDecodeError, TypeError, AttributeError):
            return "unknown"
        # Guard against hallucinated labels — only accept known keys.
        return dt if dt in set(vocab) else "unknown"


def make_doc_type_classifier() -> DocTypeClassifier:
    """Pick a classifier from KB_CLASSIFIER (auto|gemini|identity).
    `auto` (default) probes KB_GEMINI_API_KEY → Identity."""
    selector = (os.environ.get("KB_CLASSIFIER") or "auto").lower()
    if selector == "auto":
        selector = "gemini" if os.environ.get("KB_GEMINI_API_KEY") else "identity"
    if selector == "gemini":
        api_key = os.environ.get("KB_GEMINI_API_KEY")
        if not api_key:
            raise ValueError("KB_CLASSIFIER=gemini requires KB_GEMINI_API_KEY")
        return GeminiDocTypeClassifier(api_key=api_key)
    if selector == "identity":
        return IdentityDocTypeClassifier()
    raise ValueError(
        f"Unknown KB_CLASSIFIER value: {selector!r} "
        f"(expected 'gemini', 'identity', or 'auto')"
    )
