"""Phase 5c — contract clause plugin.

LLM-based extraction. For each contract-like doc, asks Gemini to identify
clauses + their typed parameters. Output schema:

  {
    "clauses": [
      {
        "clause_type": "payment_terms",
        "parties": ["Vendor", "Customer"],
        "effective_date": "2024-01-01",
        "term_months": 12,
        "payment_due_days": 30,
        "summary": "Net 30 payment terms",
        "anchor_chunk_index": 3
      }
    ]
  }

`parameters` jsonb on each atomic_units row stores the per-clause dict.
The anomaly scorer (kb.extraction.anomaly) uses payment_due_days /
term_months / etc. as numeric params for per-corpus z-score rarity.
"""

from __future__ import annotations

import json
import os
from typing import Any

from kb.extraction.plugins import AtomicUnit, FileMeta


UNIT_TYPE = "clause"

_CONTRACT_DOC_TYPES = {
    "legal_contract", "contract", "agreement", "nda", "employment_letter",
    "service_agreement", "license_agreement", "lease",
}

_SYSTEM_PROMPT = (
    "You extract structured clauses from legal/commercial agreement documents. "
    "For each clause, identify: clause_type (snake_case label like payment_terms, "
    "termination, liability_cap, non_compete, indemnification, governing_law), "
    "the parties involved, the effective_date (ISO date if stated), term_months "
    "(if a term/duration is stated), payment_due_days (if applicable), and a "
    "short summary. Skip prose. Output JSON only."
)

_USER_TEMPLATE = (
    "Extract clauses from this document:\n\n<doc>\n{doc_text}\n</doc>\n\n"
    'Return JSON exactly: {{"clauses": [{{'
    '"clause_type": "payment_terms", '
    '"parties": ["X","Y"], '
    '"effective_date": "2024-01-01", '
    '"term_months": 12, '
    '"payment_due_days": 30, '
    '"summary": "Net 30 terms"'
    '}}]}}\n'
    "Omit any field you cannot determine. clause_type is required."
)
_MAX_OUTPUT_TOKENS = 8000


def _parse_clauses(raw: str) -> list[dict[str, Any]]:
    """Tolerant JSON parser — strips fences AND recovers truncated output
    so long contracts with 10+ clauses don't drop the entire list when
    Gemini hits the output cap."""
    from kb.extraction.json_recovery import parse_tolerant_array_in_object

    raw_list, truncated = parse_tolerant_array_in_object(raw, "clauses")
    if truncated:
        import logging
        logging.getLogger(__name__).warning(
            "clauses response was truncated; recovered %d clauses",
            len(raw_list),
        )
    out: list[dict[str, Any]] = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        ct = item.get("clause_type")
        if not isinstance(ct, str) or not ct.strip():
            continue
        clean = {"clause_type": ct.strip().lower().replace(" ", "_")}
        for k in ("parties", "effective_date", "term_months",
                  "payment_due_days", "summary"):
            v = item.get(k)
            if v is not None:
                clean[k] = v
        out.append(clean)
    return out


class ClausesPlugin:
    UNIT_TYPE = "clause"

    def matches(self, file_meta: FileMeta) -> bool:
        # E3 fix: gate on doc_type, not file format. A legal contract is a
        # legal contract whether it arrived as a PDF, plain text, or
        # markdown — the prototype demo corpus has the MSA as PDF and
        # the Amendment as .txt; both should produce clause atomic units.
        # Spreadsheets + emails are handled by other plugins.
        if not file_meta.inferred_doc_type:
            return False
        # Format guard — clause extraction needs prose. Skip mime types
        # where another plugin owns the unit shape.
        if (file_meta.mime_type or "").lower() in (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.ms-excel",
            "message/rfc822",
        ):
            return False
        dt = file_meta.inferred_doc_type.lower()
        if dt in _CONTRACT_DOC_TYPES:
            return True
        # heuristic match on common substrings
        return any(k in dt for k in ("contract", "agreement", "nda", "employment"))

    async def extract(
        self,
        *,
        file_meta: FileMeta,
        doc_text: str,
        raw_pages: list[tuple[int, str, dict]],
    ) -> list[AtomicUnit]:
        from kb.extraction.mentions import make_mention_extractor  # reuse the Gemini client probe
        # We use the field-extractor's Gemini client shape for consistency.
        # But it's cleanest to just construct our own google-genai call here
        # via the existing factory pattern. Simplest: reuse google-genai client.
        api_key = os.environ.get("KB_GEMINI_API_KEY")
        if not api_key:
            # No LLM available → no clauses extracted (graceful degrade).
            return []

        model = os.environ.get("KB_ATOMIC_UNIT_MODEL") or "gemini-2.5-flash"

        try:
            from google.genai import Client, types
            client = Client(api_key=api_key)
            config = types.GenerateContentConfig(
                system_instruction=_SYSTEM_PROMPT,
                max_output_tokens=_MAX_OUTPUT_TOKENS,
                response_mime_type="application/json",
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            )
            response = await client.aio.models.generate_content(
                model=model,
                contents=_USER_TEMPLATE.format(doc_text=(doc_text or "")[:16000]),
                config=config,
            )
        except Exception:
            return []

        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return []
        raw_text = ""
        content = getattr(candidates[0], "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            t = getattr(part, "text", None)
            if t:
                raw_text = t
                break

        clauses = _parse_clauses(raw_text)
        units: list[AtomicUnit] = []
        for c in clauses:
            units.append(AtomicUnit(
                unit_type=UNIT_TYPE,
                parameters=c,
            ))
        return units


PLUGIN = ClausesPlugin()
