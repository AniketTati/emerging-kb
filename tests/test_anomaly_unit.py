"""Anomaly / rarity scoring unit tests — pure-function tests on the JIT
centroid scorer used by extract_kv_tables_file_impl.

These tests previously lived in test_atomic_units_unit.py alongside
plugin-dispatch tests; after the KV+Tables collapse killed the plugins
they got promoted into their own file so the anomaly scorer stays
covered even as the surrounding scaffolding goes away.
"""

from __future__ import annotations

import pytest

from kb.extraction.anomaly import (
    compute_centroid,
    score_unit,
    score_units_jit,
)


def test_compute_centroid_numeric_and_categorical():
    units = [
        {"amount": 100.0, "currency": "USD"},
        {"amount": 200.0, "currency": "USD"},
        {"amount": 150.0, "currency": "EUR"},
    ]
    numeric, categorical = compute_centroid(units)
    assert "amount" in numeric
    mean, std = numeric["amount"]
    assert mean == 150.0
    assert std > 0  # 3 distinct values
    assert categorical["currency"] == {"USD": 2, "EUR": 1}


def test_score_unit_high_zscore_for_outlier():
    historical = [
        {"payment_due_days": 30},
        {"payment_due_days": 30},
        {"payment_due_days": 30},
        {"payment_due_days": 30},
        {"payment_due_days": 35},
    ]
    numeric, categorical = compute_centroid(historical)
    outlier = {"payment_due_days": 0.17}  # 4 hours ≈ 0.17 days
    score = score_unit(outlier, numeric, categorical)
    assert score is not None
    assert score > 1.0, f"expected outlier to have score > 1.0; got {score}"


def test_score_unit_categorical_new_value_high_score():
    historical = [
        {"clause_type": "payment_terms"},
        {"clause_type": "payment_terms"},
        {"clause_type": "termination"},
    ]
    numeric, categorical = compute_centroid(historical)
    score = score_unit(
        {"clause_type": "indemnification"}, numeric, categorical,
    )
    assert score == 1.0  # never seen → 1 - 0/3 = 1.0


def test_score_unit_returns_none_for_no_overlap():
    """Unit has no parameters in common with historical → score = None."""
    historical = [{"a": 1}, {"a": 2}]
    numeric, categorical = compute_centroid(historical)
    score = score_unit({"b": 1}, numeric, categorical)
    assert score is None


def test_score_units_jit_handles_empty_history():
    """First-ever unit: historical empty → all scores None."""
    new = [{"x": 1}]
    scores = score_units_jit(new, [])
    assert scores == [None]
