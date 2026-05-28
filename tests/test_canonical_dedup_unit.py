"""Unit tests for the candidate-clustering heuristics in
scripts/dedup_canonical_entities.py.

Just the pure-function pieces — no DB, no LLM calls. The LLM-judge
step is exercised by integration runs against demo workspaces.
"""

from __future__ import annotations

import sys
from pathlib import Path

# scripts/ isn't on sys.path by default — add the repo root so the
# script's pure-Python helpers can be imported.
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from scripts.dedup_canonical_entities import (  # noqa: E402
    _cluster_candidates,
    _normalize_for_cluster,
    _token_overlap,
)


class TestNormalize:
    def test_strips_legal_suffix(self):
        assert _normalize_for_cluster("Mahalaxmi Infrastructure Pvt Ltd") == (
            "mahalaxmi", "infrastructure",
        )

    def test_strips_punctuation(self):
        assert _normalize_for_cluster("Acme, Inc.") == ("acme",)

    def test_dedups_tokens(self):
        # "Acme" repeated should collapse to one token (noise words 'the'
        # are stripped, then dedup runs).
        assert _normalize_for_cluster("The Acme Acme") == ("acme",)

    def test_handles_domain_form(self):
        # Token regex strips non-word chars including '.', so the URL-style
        # 'Mahalaxmiinfra.in' becomes a single concatenated token. That's
        # the right behavior — keeps the LLM judge from treating an email/
        # domain as several separate noise tokens.
        assert _normalize_for_cluster("Mahalaxmiinfra.in") == ("mahalaxmiinfrain",)

    def test_empty_after_normalization(self):
        assert _normalize_for_cluster("The & of") == ()


class TestTokenOverlap:
    def test_identical(self):
        a = ("foo", "bar")
        assert _token_overlap(a, a) == 1.0

    def test_disjoint(self):
        assert _token_overlap(("a",), ("b",)) == 0.0

    def test_partial(self):
        # {a,b} vs {b,c} → intersection 1, union 3 → 1/3
        assert abs(_token_overlap(("a", "b"), ("b", "c")) - 1 / 3) < 1e-9

    def test_empty_inputs(self):
        assert _token_overlap((), ("a",)) == 0.0


class TestCluster:
    def _rows(self, *names: str) -> list[dict]:
        return [
            {"id": f"id-{i}", "canonical_name": n, "mention_count": 10}
            for i, n in enumerate(names)
        ]

    def test_mahalaxmi_cluster(self):
        rows = self._rows(
            "Mahalaxmi",                            # bare — first-token mismatch (only one token)
            "Mahalaxmi Infrastructure Pvt Ltd",
            "Mahalaxmi Infrastructure",
            "Mahalaxmi Infra",
        )
        clusters = _cluster_candidates(rows, min_jaccard=0.4)
        # Bare 'Mahalaxmi' overlaps with the multi-token ones at 1/2 = 0.5,
        # so it should land in the same cluster.
        flat = {r["canonical_name"] for c in clusters for r in c}
        assert "Mahalaxmi Infrastructure" in flat
        assert "Mahalaxmi Infrastructure Pvt Ltd" in flat
        assert "Mahalaxmi Infra" in flat

    def test_unrelated_stay_separate(self):
        rows = self._rows(
            "Mahalaxmi Infrastructure",
            "Phoenix Tower",
            "Bandra Worli Sealink",
        )
        clusters = _cluster_candidates(rows, min_jaccard=0.5)
        # No pair shares tokens → no clusters
        assert clusters == []

    def test_first_token_mismatch_blocks_merge(self):
        # Different first significant tokens — pre-filter rejects
        # even though there's some downstream overlap.
        rows = self._rows("Acme Infrastructure", "Beta Infrastructure")
        clusters = _cluster_candidates(rows, min_jaccard=0.4)
        assert clusters == []
