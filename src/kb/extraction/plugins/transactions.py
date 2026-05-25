"""Phase 5c — bank-statement transaction plugin.

LLM-based extraction. For each bank statement, asks Gemini to identify
transactions. Output schema:

  {
    "transactions": [
      {
        "date": "2024-01-15",
        "description": "ACME Corp invoice",
        "amount": 1250.00,
        "currency": "USD",
        "counterparty": "ACME Corp",
        "type": "debit"
      }
    ]
  }
"""

from __future__ import annotations

import json
import os
from typing import Any

from kb.extraction.plugins import AtomicUnit, FileMeta


UNIT_TYPE = "transaction"

_BANK_DOC_TYPES = {
    "bank_statement", "statement", "account_statement",
    "credit_card_statement", "transaction_log",
}

_SYSTEM_PROMPT = (
    "You extract individual transactions from bank-statement / account-statement "
    "documents. Each transaction has a date, description, signed amount (positive "
    "= credit, negative = debit), currency, counterparty (if identifiable), and "
    "type ('debit' or 'credit'). Output JSON only. Skip non-transaction text."
)

_USER_TEMPLATE = (
    "Extract transactions from this statement:\n\n<doc>\n{doc_text}\n</doc>\n\n"
    'Return JSON exactly: {{"transactions": [{{'
    '"date": "2024-01-15", '
    '"description": "ACME invoice", '
    '"amount": -1250.00, '
    '"currency": "USD", '
    '"counterparty": "ACME Corp", '
    '"type": "debit"'
    '}}]}}'
)
_MAX_OUTPUT_TOKENS = 6000  # statements can have many transactions


def _parse_transactions(raw: str) -> list[dict[str, Any]]:
    """Tolerant of truncation — bank statements with 50+ transactions
    overflow even the 6000-token cap on occasion."""
    from kb.extraction.json_recovery import parse_tolerant_array_in_object

    raw_list, truncated = parse_tolerant_array_in_object(raw, "transactions")
    if truncated:
        import logging
        logging.getLogger(__name__).warning(
            "transactions response was truncated; recovered %d rows",
            len(raw_list),
        )
    out: list[dict[str, Any]] = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        clean: dict[str, Any] = {}
        for k in ("date", "description", "amount", "currency", "counterparty", "type"):
            v = item.get(k)
            if v is not None:
                clean[k] = v
        if "amount" in clean and not isinstance(clean["amount"], (int, float)):
            try:
                clean["amount"] = float(clean["amount"])
            except (TypeError, ValueError):
                continue
        if not clean:
            continue
        out.append(clean)
    return out


class TransactionsPlugin:
    UNIT_TYPE = "transaction"

    def matches(self, file_meta: FileMeta) -> bool:
        if not file_meta.inferred_doc_type:
            return False
        dt = file_meta.inferred_doc_type.lower()
        if dt in _BANK_DOC_TYPES:
            return True
        return any(k in dt for k in ("bank_statement", "statement", "transaction"))

    async def extract(
        self,
        *,
        file_meta: FileMeta,
        doc_text: str,
        raw_pages: list[tuple[int, str, dict]],
    ) -> list[AtomicUnit]:
        api_key = os.environ.get("KB_GEMINI_API_KEY")
        if not api_key:
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
        txns = _parse_transactions(raw_text)
        return [AtomicUnit(unit_type=UNIT_TYPE, parameters=t) for t in txns]


PLUGIN = TransactionsPlugin()
