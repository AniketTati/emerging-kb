"""B5 / WA-11 — pure-function unit tests for the audit hash chain.

Covers the Python-side hash helpers that mirror the PL/pgSQL trigger.
Round-trip + DB-side parity is verified in test_b5_api.py.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest

from kb.domain.audit_chain import (
    _canonical_payload_text,
    _pg_timestamptz,
    compute_genesis_hash,
    compute_row_hash,
)


# ===========================================================================
# Genesis hash
# ===========================================================================


def test_genesis_hash_is_32_bytes():
    h = compute_genesis_hash(
        "00000000-0000-0000-0000-000000000001",
        "2026-05-25 10:00:00.000000+00:00",
    )
    assert len(h) == 32
    assert isinstance(h, bytes)


def test_genesis_hash_is_deterministic():
    a = compute_genesis_hash("ws-1", "2026-01-01 00:00:00+00:00")
    b = compute_genesis_hash("ws-1", "2026-01-01 00:00:00+00:00")
    assert a == b


def test_genesis_hash_changes_per_workspace():
    a = compute_genesis_hash("ws-1", "2026-01-01 00:00:00+00:00")
    b = compute_genesis_hash("ws-2", "2026-01-01 00:00:00+00:00")
    assert a != b


def test_genesis_hash_changes_per_timestamp():
    a = compute_genesis_hash("ws-1", "2026-01-01 00:00:00+00:00")
    b = compute_genesis_hash("ws-1", "2026-01-01 00:00:01+00:00")
    assert a != b


def test_genesis_hash_matches_explicit_sha256():
    ws = "11111111-2222-3333-4444-555555555555"
    ts = "2026-05-25 10:00:00.000000+00:00"
    expected = hashlib.sha256(
        f"workspace:{ws}:init:{ts}".encode("utf-8")
    ).digest()
    assert compute_genesis_hash(ws, ts) == expected


# ===========================================================================
# Row hash
# ===========================================================================


def test_row_hash_is_32_bytes():
    prev = b"\x00" * 32
    h = compute_row_hash(
        prev, "ws-1", "2026-01-01 00:00:00+00:00", {"k": "v"},
    )
    assert len(h) == 32


def test_row_hash_changes_when_payload_mutates():
    prev = b"\x00" * 32
    h1 = compute_row_hash(prev, "ws", "2026-01-01 00:00:00+00:00", {"a": 1})
    h2 = compute_row_hash(prev, "ws", "2026-01-01 00:00:00+00:00", {"a": 2})
    assert h1 != h2


def test_row_hash_changes_when_prev_hash_mutates():
    h1 = compute_row_hash(b"\x00" * 32, "ws", "2026-01-01", {"k": "v"})
    h2 = compute_row_hash(b"\x01" * 32, "ws", "2026-01-01", {"k": "v"})
    assert h1 != h2


def test_row_hash_handles_empty_payload():
    """None and {} both serialize to '{}' for hash purposes."""
    prev = b"\x00" * 32
    a = compute_row_hash(prev, "ws", "2026-01-01", None)
    b = compute_row_hash(prev, "ws", "2026-01-01", {})
    assert a == b


def test_row_hash_canonical_form_sorts_keys():
    """Same logical payload with keys in different insertion order →
    same hash (canonical sorting)."""
    prev = b"\x00" * 32
    a = compute_row_hash(prev, "ws", "2026-01-01", {"a": 1, "b": 2})
    b = compute_row_hash(prev, "ws", "2026-01-01", {"b": 2, "a": 1})
    assert a == b


def test_chain_step_can_be_verified_by_recompute():
    """Build a 3-link chain in Python and verify each link from
    scratch."""
    ws = "ws-1"
    ts1, ts2, ts3 = "2026-01-01", "2026-01-02", "2026-01-03"
    h0 = compute_genesis_hash(ws, ts1)
    h1 = compute_row_hash(h0, ws, ts1, {"x": 1})
    h2 = compute_row_hash(h1, ws, ts2, {"x": 2})
    h3 = compute_row_hash(h2, ws, ts3, {"x": 3})
    # Recompute from scratch.
    h0_again = compute_genesis_hash(ws, ts1)
    h1_again = compute_row_hash(h0_again, ws, ts1, {"x": 1})
    h2_again = compute_row_hash(h1_again, ws, ts2, {"x": 2})
    h3_again = compute_row_hash(h2_again, ws, ts3, {"x": 3})
    assert (h0, h1, h2, h3) == (h0_again, h1_again, h2_again, h3_again)


# ===========================================================================
# Helpers — _canonical_payload_text
# ===========================================================================


def test_canonical_payload_none_is_empty_object():
    assert _canonical_payload_text(None) == "{}"


def test_canonical_payload_dict_sorts_keys():
    out = _canonical_payload_text({"b": 1, "a": 2})
    # sort_keys=True puts 'a' before 'b'
    assert out.index("a") < out.index("b")


def test_canonical_payload_list_preserves_order():
    assert _canonical_payload_text([3, 1, 2]) == "[3, 1, 2]"


# ===========================================================================
# Helpers — _pg_timestamptz
# ===========================================================================


def test_pg_timestamptz_uses_space_separator():
    dt = datetime(2026, 5, 25, 10, 0, 0, tzinfo=timezone.utc)
    out = _pg_timestamptz(dt)
    assert "T" not in out
    assert " " in out
    assert out.startswith("2026-05-25 10:00:00")
