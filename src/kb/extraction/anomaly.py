"""Phase 5c — per-type rarity / anomaly scoring.

Per build_tracker §5.12.3 decision #4.

JIT centroid approach: at end of extract_atomic_units_file_impl, read all
existing atomic_units WHERE workspace_id=X AND unit_type=Y, compute per-
numeric-parameter (mean, std) and per-categorical-parameter (value
frequency). Score each NEW unit as the max z-score (numeric) or 1-frequency
(categorical) across all its parameters. Higher = rarer.

Wave A: in-memory aggregation; no persistent centroid table. Acceptable at
~100-doc demo scale. Wave B introduces persistent centroids + REINDEX-like
weekly rebuild.

Score semantics:
  - 0.0 = identical to corpus centroid (most common values).
  - 1.0+ = ≥1 standard deviation from centroid on at least one numeric param
    (or a categorical value not seen before).
  - score=NULL is allowed (e.g., for the first unit of a type — no centroid).

Phase 8 retrieval uses `WHERE rarity_score > threshold` to surface needles.
"""

from __future__ import annotations

import math
import statistics
from typing import Any


def _is_numeric(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _flatten_params(parameters: dict[str, Any]) -> dict[str, Any]:
    """Lift one level of dict nesting; drop lists (use only scalar params for
    centroid). Wave A: this is sufficient for clauses/transactions parameters
    which are mostly flat."""
    flat: dict[str, Any] = {}
    for k, v in parameters.items():
        if isinstance(v, (int, float, str, bool)):
            flat[k] = v
        # else (list, dict): skip — Wave A doesn't aggregate nested structures.
    return flat


def compute_centroid(
    existing_units: list[dict[str, Any]],
) -> tuple[dict[str, tuple[float, float]], dict[str, dict[str, int]]]:
    """Aggregate per-parameter stats across all existing units.

    Returns:
      - numeric_stats: param_name → (mean, stdev). stdev = 0 if only 1 sample.
      - categorical_stats: param_name → (value_string → count).
    """
    numeric_values: dict[str, list[float]] = {}
    categorical_values: dict[str, dict[str, int]] = {}

    for unit in existing_units:
        params = _flatten_params(unit)
        for k, v in params.items():
            if _is_numeric(v):
                numeric_values.setdefault(k, []).append(float(v))
            else:
                s = str(v)
                inner = categorical_values.setdefault(k, {})
                inner[s] = inner.get(s, 0) + 1

    numeric_stats: dict[str, tuple[float, float]] = {}
    for k, vals in numeric_values.items():
        if len(vals) == 1:
            numeric_stats[k] = (vals[0], 0.0)
        else:
            numeric_stats[k] = (statistics.mean(vals), statistics.stdev(vals))

    return numeric_stats, categorical_values


def score_unit(
    unit_parameters: dict[str, Any],
    numeric_stats: dict[str, tuple[float, float]],
    categorical_stats: dict[str, dict[str, int]],
) -> float | None:
    """Return rarity_score for one unit, or None if the centroid has no
    parameters this unit shares (insufficient context).

    Numeric param: z-score = |v - mean| / stdev (capped at 10).
    Categorical param: 1 - (count[value] / total_count). New value → 1.0.
    Final score = max across all parameters.
    """
    params = _flatten_params(unit_parameters)
    scores: list[float] = []

    for k, v in params.items():
        if _is_numeric(v) and k in numeric_stats:
            mean, std = numeric_stats[k]
            if std <= 0.0:
                # No spread; either identical (z=0) or unmeasurable.
                scores.append(0.0 if float(v) == mean else 1.0)
            else:
                z = abs((float(v) - mean) / std)
                scores.append(min(z, 10.0))
        elif not _is_numeric(v) and k in categorical_stats:
            counts = categorical_stats[k]
            total = sum(counts.values()) or 1
            count_for_v = counts.get(str(v), 0)
            scores.append(1.0 - (count_for_v / total))

    if not scores:
        return None
    return max(scores)


def score_units_jit(
    new_units: list[dict[str, Any]],
    historical_units: list[dict[str, Any]],
) -> list[float | None]:
    """Compute rarity_score for each new unit given a list of historical
    unit parameters. Both inputs are lists of `parameters` dicts (the jsonb
    payloads). New units that share parameters with the historical set get a
    z-score / categorical rarity; units with no shared params get None.

    JIT means historical_units typically includes the new_units themselves —
    that's intentional: new units score against the corpus they belong to.
    Self-contribution is small for N>1.
    """
    if not historical_units:
        return [None] * len(new_units)
    numeric_stats, categorical_stats = compute_centroid(historical_units)
    return [
        score_unit(u, numeric_stats, categorical_stats) for u in new_units
    ]
