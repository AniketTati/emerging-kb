"""AutoMergingRetriever unit tests — fake DB conn.

Validates the swap-to-parent rule: when ≥ merge_threshold of a parent's
children appear in the hit list, swap them for a synthesized parent
hit; otherwise leave them.
"""

from __future__ import annotations

import pytest

from kb.query.auto_merging import auto_merge_hits
from kb.query.rrf import Hit


pytestmark = pytest.mark.asyncio


def _hit(*, id, score=0.5, file_id="f1", contextual_chunk_id=None):
    md = {"file_id": file_id}
    if contextual_chunk_id:
        md["contextual_chunk_id"] = contextual_chunk_id
    return Hit(id=id, kind="chunk", score=score, snippet=f"text {id}", metadata=md)


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows
    async def fetchall(self):
        return self._rows


class _FakeConn:
    """Mocks the two SQL queries auto_merging makes:
      1. contextual_chunks → (chunk_id, parent_chunk_id, parent_level)
      2. chunks grouped by parent_chunk_id → sibling counts
    """
    def __init__(self, chunk_info_rows, sibling_count_rows, parent_chunk_text):
        self._chunk_info = chunk_info_rows
        self._siblings = sibling_count_rows
        self._parent_text = parent_chunk_text

    async def execute(self, sql, params=()):
        if "FROM contextual_chunks cc" in sql:
            return _FakeCursor(self._chunk_info)
        if "GROUP BY parent_chunk_id" in sql:
            return _FakeCursor(self._siblings)
        if "FROM chunks c WHERE c.id::text = %s" in sql:
            # Single-row fetchone-style: parent chunk lookup.
            parent_id = params[0]
            return _FakeOneRow((
                parent_id,
                self._parent_text.get(parent_id, "parent text"),
                512,
                "f1",
            ))
        return _FakeCursor([])


class _FakeOneRow:
    def __init__(self, row):
        self._row = row
    async def fetchone(self):
        return self._row


# ===========================================================================
# Happy path
# ===========================================================================


async def test_merges_when_threshold_crosses():
    """3 of 4 sibling leaves hit → swap to parent (75% ≥ 50%)."""
    h1 = _hit(id="cc1", score=0.9, contextual_chunk_id="cc1")
    h2 = _hit(id="cc2", score=0.8, contextual_chunk_id="cc2")
    h3 = _hit(id="cc3", score=0.7, contextual_chunk_id="cc3")
    other = Hit(
        id="other", kind="raptor_node", score=0.6, snippet="...",
        metadata={"file_id": "f1"},
    )
    conn = _FakeConn(
        chunk_info_rows=[
            ("cc1", "chunk_1", "parent_a", 1),
            ("cc2", "chunk_2", "parent_a", 1),
            ("cc3", "chunk_3", "parent_a", 1),
        ],
        sibling_count_rows=[("parent_a", 4)],
        parent_chunk_text={"parent_a": "merged parent text"},
    )
    merged, stats = await auto_merge_hits(
        [h1, h2, h3, other], conn=conn, workspace_id="ws-test",
    )
    # Parent replaces the 3 children; raptor_node passes through.
    chunk_hits = [h for h in merged if h.kind == "chunk"]
    assert len(chunk_hits) == 1
    parent_hit = chunk_hits[0]
    assert parent_hit.id == "parent_a"
    assert parent_hit.metadata["auto_merged"] is True
    assert parent_hit.metadata["merged_count"] == 3
    assert "merged parent text" in parent_hit.snippet
    assert stats.leaves_replaced == 3
    assert stats.merges_by_level == {1: 1}  # parent at level 1


async def test_does_not_merge_below_threshold():
    """Only 1 of 4 siblings hit → leave it as-is (25% < 50%)."""
    h1 = _hit(id="cc1", score=0.9, contextual_chunk_id="cc1")
    conn = _FakeConn(
        chunk_info_rows=[("cc1", "chunk_1", "parent_a", 1)],
        sibling_count_rows=[("parent_a", 4)],
        parent_chunk_text={},
    )
    merged, stats = await auto_merge_hits(
        [h1], conn=conn, workspace_id="ws-test",
    )
    assert len(merged) == 1
    assert merged[0].id == "cc1"
    assert "auto_merged" not in (merged[0].metadata or {})
    assert stats.leaves_replaced == 0


async def test_orphan_leaves_pass_through():
    """A leaf with parent_chunk_id NULL never gets merged."""
    h1 = _hit(id="cc1", score=0.9, contextual_chunk_id="cc1")
    conn = _FakeConn(
        chunk_info_rows=[("cc1", "chunk_1", None, 0)],
        sibling_count_rows=[],
        parent_chunk_text={},
    )
    merged, stats = await auto_merge_hits(
        [h1], conn=conn, workspace_id="ws-test",
    )
    assert len(merged) == 1
    assert merged[0].id == "cc1"
    assert stats.leaves_replaced == 0


async def test_no_conn_returns_unchanged():
    """When called without a DB conn (e.g. an upstream channel error
    aborted the txn), auto-merge is a pass-through."""
    h1 = _hit(id="cc1", contextual_chunk_id="cc1")
    merged, stats = await auto_merge_hits(
        [h1], conn=None, workspace_id="ws-test",
    )
    assert merged == [h1]
    assert stats.leaves_replaced == 0


async def test_non_chunk_hits_pass_through():
    """raptor_node / extracted_entity / aggregate hits never touch
    AutoMerging — they're not in the chunk hierarchy."""
    raptor = Hit(
        id="r1", kind="raptor_node", score=1.0, snippet="...",
        metadata={"file_id": "f1"},
    )
    aggregate = Hit(
        id="a1", kind="aggregate", score=1.0, snippet="...",
        metadata={"aggregate": True},
    )
    conn = _FakeConn(
        chunk_info_rows=[], sibling_count_rows=[], parent_chunk_text={},
    )
    merged, stats = await auto_merge_hits(
        [raptor, aggregate], conn=conn, workspace_id="ws-test",
    )
    assert {h.id for h in merged} == {"r1", "a1"}
    assert stats.leaves_replaced == 0
