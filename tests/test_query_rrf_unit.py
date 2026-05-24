"""Phase 8b — RRF fusion unit tests (pure-function, no DB)."""

from __future__ import annotations

import pytest

from kb.query.rrf import DEFAULT_K, Hit, rrf_fuse


def test_default_k_constant():
    assert DEFAULT_K == 60  # Cormack-Clarke-Buettcher 2009


def test_hit_dataclass_shape():
    h = Hit(id="x", kind="chunk", score=0.5, snippet="text",
            metadata={"file_id": "f1", "level": 1})
    assert h.id == "x"
    assert h.kind == "chunk"
    assert h.score == 0.5
    assert h.snippet == "text"
    assert h.metadata["file_id"] == "f1"


def test_rrf_fuse_empty_input_returns_empty():
    assert rrf_fuse([]) == []


def test_rrf_fuse_single_channel_passthrough_with_rescored():
    """One channel of 3 hits → fused has same 3 hits, scores = 1/(k+rank+1).
    The ORDER stays the same."""
    channel = [
        Hit(id="a", kind="chunk", score=99.9, snippet="a"),
        Hit(id="b", kind="chunk", score=88.8, snippet="b"),
        Hit(id="c", kind="chunk", score=77.7, snippet="c"),
    ]
    fused = rrf_fuse([channel])
    assert [h.id for h in fused] == ["a", "b", "c"]
    # Scores are reciprocal-rank, NOT the original scores
    assert fused[0].score == pytest.approx(1.0 / (DEFAULT_K + 1))
    assert fused[1].score == pytest.approx(1.0 / (DEFAULT_K + 2))
    assert fused[2].score == pytest.approx(1.0 / (DEFAULT_K + 3))


def test_rrf_fuse_dedupes_same_id_kind_across_channels():
    """Same (id, kind) appearing in 2 channels → 1 fused hit with summed score."""
    ch1 = [Hit(id="shared", kind="chunk", score=99, snippet="from ch1")]
    ch2 = [
        Hit(id="other", kind="chunk", score=88, snippet="other"),
        Hit(id="shared", kind="chunk", score=77, snippet="from ch2"),
    ]
    fused = rrf_fuse([ch1, ch2])
    # 2 unique items: "shared" appears in both channels
    assert len(fused) == 2
    shared = next(h for h in fused if h.id == "shared")
    # Score = 1/(k+1) [from ch1 rank 0] + 1/(k+2) [from ch2 rank 1]
    expected = 1.0 / (DEFAULT_K + 1) + 1.0 / (DEFAULT_K + 2)
    assert shared.score == pytest.approx(expected)


def test_rrf_fuse_preserves_first_seen_metadata_for_duplicates():
    """First-seen snippet + metadata persists when same item appears across
    channels (RRF only sums scores; doesn't fight over snippet content)."""
    ch1 = [Hit(id="x", kind="chunk", score=1, snippet="first-snippet",
               metadata={"channel": "bm25_chunks"})]
    ch2 = [Hit(id="x", kind="chunk", score=2, snippet="second-snippet",
               metadata={"channel": "dense_chunks"})]
    fused = rrf_fuse([ch1, ch2])
    assert len(fused) == 1
    # Implementation: keeps the FIRST occurrence's snippet/metadata.
    assert fused[0].snippet == "first-snippet"


def test_rrf_fuse_distinguishes_same_id_different_kind():
    """A 'chunk' and a 'raptor_node' with the same id (unlikely but possible
    in a deformed schema) must not collide — keyed by (id, kind)."""
    ch1 = [Hit(id="abc", kind="chunk", score=1, snippet="c")]
    ch2 = [Hit(id="abc", kind="raptor_node", score=2, snippet="r")]
    fused = rrf_fuse([ch1, ch2])
    assert len(fused) == 2


def test_rrf_fuse_order_is_score_descending():
    """Final order is RRF-score descending."""
    ch1 = [
        Hit(id="rank1", kind="chunk", score=0, snippet=""),
        Hit(id="rank2", kind="chunk", score=0, snippet=""),
    ]
    ch2 = [
        Hit(id="rank2", kind="chunk", score=0, snippet=""),  # bumps rank2 above
        Hit(id="rank3", kind="chunk", score=0, snippet=""),
    ]
    fused = rrf_fuse([ch1, ch2])
    # rank2 appears in both → highest fused score → first
    assert fused[0].id == "rank2"


def test_rrf_uses_default_k_60_in_formula():
    """Sanity: a single rank-0 hit gets score 1/(60+1) = 1/61."""
    fused = rrf_fuse([[Hit(id="x", kind="chunk", score=999, snippet="")]])
    assert fused[0].score == pytest.approx(1.0 / 61.0)


def test_rrf_fuse_accepts_custom_k():
    """k=10 → rank-0 score = 1/(10+1) = 1/11."""
    fused = rrf_fuse([[Hit(id="x", kind="chunk", score=0, snippet="")]], k=10)
    assert fused[0].score == pytest.approx(1.0 / 11.0)
