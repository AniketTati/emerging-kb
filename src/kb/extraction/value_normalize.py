"""Numeric value normalization for proposed_fields.value_text.

Same conceptual value can land in proposed_fields as wildly different
strings depending on how the source doc formatted it:
   "2200000"        — raw integer
   "INR 22 lakh"    — Indian format, magnitude word
   "₹22,00,000"     — Indian comma grouping
   "$5.3M"          — US million
   "(1,234)"        — accounting-style negative
   "1.28 cr"        — Indian crore
   "USD 5.3 million" — long form

Q-mode aggregations on `value_text::numeric` then return NULL for
everything except the bare-numeric form, silently dropping rows.
This module parses the messy forms into a single canonical numeric
+ currency tuple so the downstream layer can SUM/AVG/MIN/MAX
across docs that formatted the same field differently.

This is pure-code — no LLM call. Conservative: returns (None, None)
when the input isn't clearly a number, rather than guessing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# Magnitudes — multipliers we apply when a magnitude word follows the
# numeric part. Indian conventions: lakh = 100K, crore = 10M.
_MAGNITUDES: dict[str, int] = {
    # Indian
    "lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "lacs": 100_000,
    "l": 100_000,                                  # 22L
    "crore": 10_000_000, "crores": 10_000_000,
    "cr": 10_000_000,                              # 1.28 cr
    # International / SI
    "thousand": 1_000, "k": 1_000,                 # 50K
    "million": 1_000_000, "m": 1_000_000, "mn": 1_000_000,  # 5.3M
    "billion": 1_000_000_000, "b": 1_000_000_000, "bn": 1_000_000_000,
    "trillion": 1_000_000_000_000, "t": 1_000_000_000_000,
}

# Currencies — preserved as metadata. Map symbol/code to ISO 4217.
_CURRENCY_SYMBOLS: dict[str, str] = {
    "₹": "INR", "rs": "INR", "rs.": "INR", "inr": "INR",
    "rupees": "INR", "rupee": "INR",
    "$": "USD", "us$": "USD", "usd": "USD",
    "€": "EUR", "eur": "EUR", "euros": "EUR", "euro": "EUR",
    "£": "GBP", "gbp": "GBP", "pounds": "GBP", "pound": "GBP",
    "¥": "JPY", "jpy": "JPY", "yen": "JPY",
}

# Match a numeric body: optional negative sign, digits with optional
# comma/space grouping, optional decimal.
# Allowed grouping: "1,234,567" or "12,34,567" (Indian) or "1 234 567".
_NUMERIC_RE = re.compile(
    r"^[\-−]?[0-9][0-9,\s']*(?:\.[0-9]+)?$"
)

# Strip these chars from value_text before parsing the numeric body.
# Note: `(` and `)` are handled separately (accounting negative).
_STRIP_CHARS = "   "  # ascii space, nbsp, narrow nbsp


@dataclass(frozen=True)
class NormalizedValue:
    numeric: float
    currency: str | None      # ISO 4217 if detected, else None
    raw: str                  # original input (for display + audit)


def _strip_currency_prefix(s: str) -> tuple[str, str | None]:
    """Detect and strip a leading currency token. Returns (rest, iso_code|None)."""
    lc = s.strip().lower()
    # Try multi-char first so "Rs." beats "R"
    for key in sorted(_CURRENCY_SYMBOLS.keys(), key=len, reverse=True):
        # Must match as standalone token or prefix on a number/word
        if lc.startswith(key):
            tail = s.strip()[len(key):]
            # Avoid false hit: "lacs" starts with "l" which is also a
            # magnitude marker. Only accept if next char isn't a letter
            # (so "₹22 lakh" works but "lakh" alone doesn't trigger).
            if tail and tail[0].isalpha() and key in ("l", "k", "m", "b", "t"):
                # ambiguous — not currency, skip
                continue
            return tail.lstrip(), _CURRENCY_SYMBOLS[key]
    return s, None


def _strip_currency_suffix(s: str) -> tuple[str, str | None]:
    """Same as prefix but trailing — e.g. '22 lakh INR'."""
    s_stripped = s.rstrip()
    lc = s_stripped.lower()
    for key in sorted(_CURRENCY_SYMBOLS.keys(), key=len, reverse=True):
        if lc.endswith(" " + key) or lc.endswith(key):
            # Word-boundary check
            if lc.endswith(" " + key):
                head = s_stripped[: -len(key) - 1]
            else:
                # No-space suffix — require previous char NOT alpha
                if len(s_stripped) > len(key) and s_stripped[-len(key) - 1].isalpha():
                    continue
                head = s_stripped[: -len(key)]
            return head.rstrip(), _CURRENCY_SYMBOLS[key]
    return s, None


def _peel_magnitude(s: str) -> tuple[str, int]:
    """If a magnitude word trails the number, return (rest, multiplier).
    Multiplier defaults to 1. E.g. '22 lakh' → ('22', 100000)."""
    s = s.strip().rstrip(".")
    # Try multi-word first
    lc = s.lower()
    for key in sorted(_MAGNITUDES.keys(), key=len, reverse=True):
        if lc.endswith(" " + key):
            head = s[: -len(key) - 1]
            return head.rstrip(), _MAGNITUDES[key]
        elif lc.endswith(key):
            # No-space suffix (e.g. "22L", "5.3M") — only valid when
            # previous char is digit / dot / closing paren.
            prev_idx = len(s) - len(key) - 1
            if prev_idx >= 0 and s[prev_idx] in "0123456789.)":
                head = s[: -len(key)]
                return head.rstrip(), _MAGNITUDES[key]
    return s, 1


def _parse_numeric_body(s: str) -> float | None:
    """Parse the cleaned numeric body. Handles:
    - "1,234,567"    → 1234567 (US comma)
    - "12,34,567"    → 1234567 (Indian comma)
    - "1 234 567"    → 1234567 (space grouping)
    - "(500)"        → -500 (accounting negative)
    - "−5.3"         → -5.3 (Unicode minus)
    - "5.3"          → 5.3
    Returns None if not parseable."""
    s = s.strip()
    if not s:
        return None
    is_neg = False
    if s.startswith("(") and s.endswith(")"):
        is_neg = True
        s = s[1:-1].strip()
    elif s.startswith("−"):  # Unicode minus
        s = "-" + s[1:]
    # Strip all commas, spaces, apostrophe (Swiss format).
    cleaned = s.replace(",", "").replace("'", "")
    cleaned = re.sub(r"\s+", "", cleaned)
    # Validate: must look like a number now.
    if not re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?", cleaned):
        return None
    try:
        v = float(cleaned)
    except ValueError:
        return None
    return -v if is_neg and v >= 0 else v


def normalize_value(value_text: str | None) -> NormalizedValue | None:
    """Parse a string into (numeric, currency, raw). Returns None when
    the input clearly isn't a number (e.g. names, dates, free text)."""
    if not value_text:
        return None
    raw = value_text
    s = raw.strip()
    if not s:
        return None
    # Reject anything that's clearly text (>3 letters in a row that
    # isn't a magnitude word).
    # Quick gate: must contain at least one digit.
    if not any(c.isdigit() for c in s):
        return None

    # Strip currency from either end.
    s2, currency_pre = _strip_currency_prefix(s)
    s3, currency_suf = _strip_currency_suffix(s2)
    currency = currency_pre or currency_suf

    # Strip magnitude word from end.
    s4, multiplier = _peel_magnitude(s3)

    # Now s4 should be a pure numeric body.
    numeric = _parse_numeric_body(s4)
    if numeric is None:
        return None

    return NormalizedValue(
        numeric=numeric * multiplier,
        currency=currency,
        raw=raw,
    )
