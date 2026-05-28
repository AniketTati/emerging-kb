"""Unit tests for kb.extraction.value_normalize.

Covers the Bug D Tier-1 #3 normalizer that parses messy value_text into
a clean (numeric, currency) tuple so Q-mode SUM/AVG stops silently
dropping rows formatted with currency symbols, magnitude words,
accounting negatives, or Indian comma grouping.

Tests run with plain pytest — no fixtures, no DB:

    uv run pytest tests/test_value_normalize_unit.py -q
"""

from __future__ import annotations

import pytest

from kb.extraction.value_normalize import NormalizedValue, normalize_value


# ---------------------------------------------------------------------------
# Bare numeric forms — the easy cases the cast already handles.
# ---------------------------------------------------------------------------


class TestBareNumeric:
    def test_integer(self):
        nv = normalize_value("2200000")
        assert nv is not None
        assert nv.numeric == 2_200_000
        assert nv.currency is None

    def test_float(self):
        nv = normalize_value("5.3")
        assert nv is not None
        assert nv.numeric == pytest.approx(5.3)
        assert nv.currency is None

    def test_us_comma_grouping(self):
        nv = normalize_value("1,234,567")
        assert nv is not None
        assert nv.numeric == 1_234_567

    def test_indian_comma_grouping(self):
        nv = normalize_value("22,00,000")
        assert nv is not None
        assert nv.numeric == 2_200_000

    def test_space_grouping(self):
        nv = normalize_value("1 234 567")
        assert nv is not None
        assert nv.numeric == 1_234_567

    def test_swiss_apostrophe_grouping(self):
        nv = normalize_value("1'234'567")
        assert nv is not None
        assert nv.numeric == 1_234_567


# ---------------------------------------------------------------------------
# Magnitude words — Indian + international.
# ---------------------------------------------------------------------------


class TestMagnitudes:
    def test_indian_lakh(self):
        nv = normalize_value("22 lakh")
        assert nv is not None
        assert nv.numeric == 2_200_000

    def test_indian_lakhs_plural(self):
        nv = normalize_value("5 lakhs")
        assert nv is not None
        assert nv.numeric == 500_000

    def test_indian_crore_word(self):
        nv = normalize_value("1.28 crore")
        assert nv is not None
        assert nv.numeric == pytest.approx(12_800_000)

    def test_indian_cr_short(self):
        nv = normalize_value("1.28 cr")
        assert nv is not None
        assert nv.numeric == pytest.approx(12_800_000)

    def test_us_million_short_no_space(self):
        nv = normalize_value("$5.3M")
        assert nv is not None
        assert nv.numeric == pytest.approx(5_300_000)
        assert nv.currency == "USD"

    def test_us_million_long(self):
        nv = normalize_value("5.3 million")
        assert nv is not None
        assert nv.numeric == pytest.approx(5_300_000)

    def test_billion(self):
        nv = normalize_value("2.5 billion")
        assert nv is not None
        assert nv.numeric == pytest.approx(2_500_000_000)


# ---------------------------------------------------------------------------
# Currency tags — symbols + ISO codes + long forms.
# ---------------------------------------------------------------------------


class TestCurrency:
    def test_inr_symbol_prefix(self):
        nv = normalize_value("₹22 lakh")
        assert nv is not None
        assert nv.numeric == 2_200_000
        assert nv.currency == "INR"

    def test_inr_iso_prefix(self):
        nv = normalize_value("INR 22,00,000")
        assert nv is not None
        assert nv.numeric == 2_200_000
        assert nv.currency == "INR"

    def test_usd_symbol(self):
        nv = normalize_value("$1,000")
        assert nv is not None
        assert nv.numeric == 1_000
        assert nv.currency == "USD"

    def test_usd_iso_suffix(self):
        nv = normalize_value("22 lakh INR")
        assert nv is not None
        assert nv.numeric == 2_200_000
        assert nv.currency == "INR"

    def test_eur_symbol(self):
        nv = normalize_value("€500")
        assert nv is not None
        assert nv.numeric == 500
        assert nv.currency == "EUR"


# ---------------------------------------------------------------------------
# Accounting negatives + Unicode minus.
# ---------------------------------------------------------------------------


class TestSigns:
    def test_paren_negative(self):
        nv = normalize_value("(500)")
        assert nv is not None
        assert nv.numeric == -500

    def test_paren_negative_with_grouping(self):
        nv = normalize_value("(1,234)")
        assert nv is not None
        assert nv.numeric == -1_234

    def test_unicode_minus(self):
        nv = normalize_value("−5.3")
        assert nv is not None
        assert nv.numeric == pytest.approx(-5.3)


# ---------------------------------------------------------------------------
# Non-numeric inputs — must return None so the column stays NULL.
# Q-mode SUM/AVG correctly skip NULL rows.
# ---------------------------------------------------------------------------


class TestRejects:
    def test_pure_name(self):
        assert normalize_value("Mahalaxmi Infrastructure") is None

    def test_email(self):
        assert normalize_value("alice@example.com") is None

    def test_empty_string(self):
        assert normalize_value("") is None

    def test_none_input(self):
        assert normalize_value(None) is None

    def test_whitespace_only(self):
        assert normalize_value("   ") is None

    def test_free_text(self):
        assert normalize_value("subject to court approval") is None
