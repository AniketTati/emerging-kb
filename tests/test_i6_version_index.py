"""I6 — chain member version_index must increase monotonically, not collide.

Pre-fix the worker hardcoded version_index=1 for every amendment, so a 3+
doc chain (original→amend1→amend2) had two members at index 1.
`next_version_index` assigns existing-or-(max+1).
"""

from __future__ import annotations

import pytest

from kb.domain.doc_chains import next_version_index


class _FakeCur:
    def __init__(self, row):
        self._row = row

    async def fetchone(self):
        return self._row


class _FakeConn:
    """Minimal conn double: scripts responses for the two queries
    next_version_index issues (member-lookup, then MAX)."""

    def __init__(self, *, existing_index=None, current_max=None):
        self._existing_index = existing_index
        self._current_max = current_max

    async def execute(self, sql, params):
        if "doc_id = %s" in sql:  # member-lookup query
            return _FakeCur((self._existing_index,)
                            if self._existing_index is not None else None)
        # MAX(version_index) query
        return _FakeCur((self._current_max,))


@pytest.mark.asyncio
async def test_first_amendment_after_original_is_1():
    # chain has only the original at index 0 → next is 1.
    conn = _FakeConn(existing_index=None, current_max=0)
    assert await next_version_index(conn, chain_id="c", file_id="f") == 1


@pytest.mark.asyncio
async def test_second_amendment_is_2_not_collide():
    # chain has original(0) + amend1(1) → next is 2 (the bug produced 1).
    conn = _FakeConn(existing_index=None, current_max=1)
    assert await next_version_index(conn, chain_id="c", file_id="f") == 2


@pytest.mark.asyncio
async def test_existing_member_keeps_its_index_idempotent():
    # re-ingest of a doc already at index 1 must return 1, not bump.
    conn = _FakeConn(existing_index=1, current_max=3)
    assert await next_version_index(conn, chain_id="c", file_id="f") == 1


@pytest.mark.asyncio
async def test_empty_chain_starts_at_0():
    # COALESCE(MAX, -1) → -1, +1 = 0.
    conn = _FakeConn(existing_index=None, current_max=-1)
    assert await next_version_index(conn, chain_id="c", file_id="f") == 0
