"""B5 / WA-11 — Hash-chained audit_log helpers.

The hash chain itself is computed by the BEFORE INSERT trigger in
migrations/sql/0028_audit_chain.sql. This module provides:

  - `compute_genesis_hash`    — mirrors the trigger's genesis formula
                                (for tests + offline verification)
  - `compute_row_hash`        — mirrors the trigger's chain step formula
  - `insert_audit_event`      — append-only helper that callers use
                                instead of raw INSERT
  - `read_audit_log`          — paginated read for `/audit-log`
  - `walk_chain`              — integrity walker; calls the SQL helper
                                `audit_log_recompute_chain()` and surfaces
                                the first divergence

Threat model: the trigger is the only path that writes prev_hash + hash.
The kb_app role has SELECT + INSERT only (no UPDATE/DELETE), and the
trigger overwrites NEW.prev_hash / NEW.hash regardless of what the
client passed. The walker is the auditor's tool to detect direct DB
tampering by a privileged actor (superuser bypass of RLS/grants).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from kb.db.pool import Connection


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


_HASH_DELIM = "|"   # delimiter in the canonical payload bytes


# ---------------------------------------------------------------------------
# Pure-function hash helpers (mirror the PL/pgSQL trigger)
# ---------------------------------------------------------------------------


def compute_genesis_hash(workspace_id: str, created_at: datetime | str) -> bytes:
    """SHA-256("workspace:" + ws_id + ":init:" + created_at). Bytes
    exactly match what the trigger computes for the first row in a
    workspace."""
    ts = created_at if isinstance(created_at, str) else _pg_timestamptz(created_at)
    payload = f"workspace:{workspace_id}:init:{ts}".encode("utf-8")
    return hashlib.sha256(payload).digest()


def compute_row_hash(
    prev_hash: bytes,
    workspace_id: str,
    created_at: datetime | str,
    payload: Any,
) -> bytes:
    """Compute the audit_log.hash for a row given its predecessor.

    Mirrors the trigger:
       hash = sha256(prev_hash || '|' || workspace_id || '|'
                                || created_at || '|' || payload_json)
    """
    ts = created_at if isinstance(created_at, str) else _pg_timestamptz(created_at)
    payload_text = _canonical_payload_text(payload)
    delim_block = (
        f"{_HASH_DELIM}{workspace_id}{_HASH_DELIM}{ts}{_HASH_DELIM}{payload_text}"
    ).encode("utf-8")
    return hashlib.sha256(prev_hash + delim_block).digest()


def _canonical_payload_text(payload: Any) -> str:
    """Render a JSON payload exactly the way PG's jsonb::text would.

    For test reproducibility, we expect callers to pass primitives + dicts
    + lists. We sort keys to keep this deterministic across Python +
    Postgres jsonb stringification differences. The trigger uses
    jsonb's own text representation, which sorts keys lexicographically
    at the top level — sort_keys=True matches that closely enough for
    a SHA-256 to verify."""
    if payload is None:
        return "{}"
    if isinstance(payload, (dict, list)):
        return json.dumps(payload, sort_keys=True, separators=(", ", ": "))
    return str(payload)


def _pg_timestamptz(dt: datetime) -> str:
    """Format a datetime the way PG renders timestamptz::text by default.

    PG default: "YYYY-MM-DD HH:MM:SS.ffffff+TZ". Python's isoformat is
    "YYYY-MM-DDTHH:MM:SS.ffffff+TZ" — the only difference is the 'T'
    separator. PG accepts both for input but stringifies with a space.
    Hash inputs must match exactly, so we use the space form."""
    return dt.isoformat(sep=" ")


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuditLogEntry:
    id: str
    workspace_id: str
    created_at: str
    actor: str
    action: str
    entity_type: str | None
    entity_id: str | None
    query_id: str | None
    payload: dict
    prev_hash: str  # hex
    hash: str       # hex


@dataclass(frozen=True)
class ChainWalkResult:
    """Output of `walk_chain`. `ok=True` when no divergence found."""
    ok: bool
    total_rows: int
    workspace_id: str
    broken_at_row_id: str | None = None
    broken_at_position: int | None = None
    expected_hash: str | None = None   # hex
    actual_hash: str | None = None     # hex
    notes: str | None = None


# ---------------------------------------------------------------------------
# Repo: insert + read
# ---------------------------------------------------------------------------


async def insert_audit_event(
    conn: Connection,
    *,
    workspace_id: str,
    actor: str,
    action: str,
    payload: dict,
    entity_type: str | None = None,
    entity_id: str | None = None,
    query_id: str | None = None,
) -> str:
    """Append a new audit_log row. The BEFORE INSERT trigger overwrites
    any prev_hash / hash the caller passes — so we don't pass them.
    Returns the new row's id (UUID string)."""
    cur = await conn.execute(
        """
        INSERT INTO audit_log
            (workspace_id, actor, action, entity_type, entity_id,
             query_id, payload)
        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
        RETURNING id::text
        """,
        (
            workspace_id, actor, action, entity_type, entity_id,
            query_id, json.dumps(payload or {}),
        ),
    )
    row = await cur.fetchone()
    assert row is not None
    return str(row[0])


async def read_audit_log(
    conn: Connection,
    *,
    workspace_id: str,
    limit: int = 100,
) -> list[AuditLogEntry]:
    cur = await conn.execute(
        """
        SELECT id::text, workspace_id::text, created_at, actor, action,
               entity_type, entity_id, query_id::text, payload,
               encode(prev_hash, 'hex'), encode(hash, 'hex')
          FROM audit_log
         WHERE workspace_id = %s
      ORDER BY created_at DESC, id DESC
         LIMIT %s
        """,
        (workspace_id, limit),
    )
    rows = await cur.fetchall()
    return [
        AuditLogEntry(
            id=str(r[0]),
            workspace_id=str(r[1]),
            created_at=r[2].isoformat() if hasattr(r[2], "isoformat") else str(r[2]),
            actor=str(r[3]),
            action=str(r[4]),
            entity_type=r[5],
            entity_id=r[6],
            query_id=r[7],
            payload=r[8] if isinstance(r[8], dict) else (
                json.loads(r[8]) if r[8] else {}
            ),
            prev_hash=str(r[9] or ""),
            hash=str(r[10] or ""),
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Integrity walker
# ---------------------------------------------------------------------------


async def walk_chain(
    conn: Connection,
    *,
    workspace_id: str,
    limit: int = 5000,
) -> ChainWalkResult:
    """Verify the chain integrity for a workspace. Returns the first
    divergence (if any) or ok=True with total_rows.

    Uses the SQL helper `audit_log_recompute_chain` (created in
    migration 0028) so the recompute runs server-side and we don't have
    to ship every row to Python."""
    try:
        cur = await conn.execute(
            "SELECT row_id::text, chain_position, "
            "       encode(stored_hash, 'hex'), encode(expected_hash, 'hex'), "
            "       encode(stored_prev_hash, 'hex'), encode(expected_prev_hash, 'hex') "
            "FROM audit_log_recompute_chain(%s::uuid, %s)",
            (workspace_id, limit),
        )
        rows = await cur.fetchall()
    except Exception as exc:  # noqa: BLE001
        return ChainWalkResult(
            ok=False, total_rows=0, workspace_id=workspace_id,
            notes=f"recompute function failed: {exc}",
        )

    for r in rows:
        row_id, pos, stored_hash, expected_hash, stored_prev, expected_prev = r
        if stored_hash != expected_hash:
            return ChainWalkResult(
                ok=False,
                total_rows=len(rows),
                workspace_id=workspace_id,
                broken_at_row_id=str(row_id),
                broken_at_position=int(pos),
                expected_hash=expected_hash,
                actual_hash=stored_hash,
                notes="hash mismatch — row payload or chain ordering was tampered with",
            )
        if stored_prev != expected_prev:
            return ChainWalkResult(
                ok=False,
                total_rows=len(rows),
                workspace_id=workspace_id,
                broken_at_row_id=str(row_id),
                broken_at_position=int(pos),
                expected_hash=expected_prev,
                actual_hash=stored_prev,
                notes="prev_hash mismatch — chain link was broken",
            )

    return ChainWalkResult(
        ok=True, total_rows=len(rows), workspace_id=workspace_id,
    )
