"""WA-3 / Design 3 — doc_chains + doc_chain_members repo.

Read + write API used by the detector worker stage (kb.extraction.doc_chains)
and the HTTP surface (kb.api.doc_chains).

DB contract:
- doc_chains and doc_chain_members are workspace-scoped + RLS-forced.
  Callers MUST have set `app.workspace_id` (handled by the worker's
  `SET LOCAL app.workspace_id = ...` and the API's WorkspaceMiddleware).
- (workspace_id, type, chain_key) is the unique key on doc_chains —
  same chain_key in the same type re-uses the existing chain row.
- (chain_id, doc_id) is the PK on doc_chain_members. Member-insert is
  ON CONFLICT DO NOTHING idempotent.
- member_count on the parent chain is recomputed from doc_chain_members
  after every insert.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from kb.db.pool import Connection


# Mirror the migration CHECKs as Python constants. Keeps importers + the
# UI hint endpoints honest about which strings the DB will accept.
CHAIN_TYPES: tuple[str, ...] = (
    "email_thread", "contract_chain", "drawing_revisions",
    "circular_chain", "patient_chart", "other",
)
MEMBER_ROLES: tuple[str, ...] = (
    "original", "amendment", "side_letter", "superseded",
    "reply", "forward", "revision", "corrigendum",
    "encounter", "lab", "discharge", "other",
)


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DocChainRecord:
    id: str
    workspace_id: str
    type: str
    title: str | None
    current_version_id: str | None
    chain_key: str | None
    member_count: int
    detection_confidence: float
    created_at: str


@dataclass(frozen=True)
class DocChainMemberRecord:
    chain_id: str
    doc_id: str
    workspace_id: str
    version_index: int
    role: str
    parent_doc_id: str | None
    added_at: str


@dataclass(frozen=True)
class ChainWithMembers:
    """API-shaped composite — one chain plus its members."""
    chain: DocChainRecord
    members: list[DocChainMemberRecord]


def _chain_row(row: tuple) -> DocChainRecord:
    return DocChainRecord(
        id=str(row[0]),
        workspace_id=str(row[1]),
        type=str(row[2]),
        title=row[3],
        current_version_id=str(row[4]) if row[4] is not None else None,
        chain_key=row[5],
        member_count=int(row[6]),
        detection_confidence=float(row[7]),
        created_at=row[8].isoformat() if hasattr(row[8], "isoformat") else str(row[8]),
    )


def _member_row(row: tuple) -> DocChainMemberRecord:
    return DocChainMemberRecord(
        chain_id=str(row[0]),
        doc_id=str(row[1]),
        workspace_id=str(row[2]),
        version_index=int(row[3]),
        role=str(row[4]),
        parent_doc_id=str(row[5]) if row[5] is not None else None,
        added_at=row[6].isoformat() if hasattr(row[6], "isoformat") else str(row[6]),
    )


_CHAIN_COLS = (
    "id, workspace_id, type, title, current_version_id, chain_key, "
    "member_count, detection_confidence, created_at"
)
_MEMBER_COLS = (
    "chain_id, doc_id, workspace_id, version_index, role, parent_doc_id, added_at"
)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


async def find_chain_by_key(
    conn: Connection,
    *,
    workspace_id: str,
    chain_type: str,
    chain_key: str,
) -> DocChainRecord | None:
    """Idempotency hot path: re-running the detector on a new file in an
    existing chain should join, not create a duplicate."""
    cur = await conn.execute(
        f"SELECT {_CHAIN_COLS} FROM doc_chains "
        "WHERE workspace_id = %s AND type = %s AND chain_key = %s "
        "LIMIT 1",
        (workspace_id, chain_type, chain_key),
    )
    row = await cur.fetchone()
    return _chain_row(row) if row else None


async def get_chain(
    conn: Connection,
    *,
    chain_id: str,
) -> DocChainRecord | None:
    cur = await conn.execute(
        f"SELECT {_CHAIN_COLS} FROM doc_chains WHERE id = %s LIMIT 1",
        (chain_id,),
    )
    row = await cur.fetchone()
    return _chain_row(row) if row else None


async def list_chains(
    conn: Connection,
    *,
    workspace_id: str,
    chain_type: str | None = None,
    limit: int = 100,
) -> list[DocChainRecord]:
    if chain_type is None:
        sql = (
            f"SELECT {_CHAIN_COLS} FROM doc_chains "
            "WHERE workspace_id = %s ORDER BY created_at DESC LIMIT %s"
        )
        params: tuple = (workspace_id, limit)
    else:
        sql = (
            f"SELECT {_CHAIN_COLS} FROM doc_chains "
            "WHERE workspace_id = %s AND type = %s "
            "ORDER BY created_at DESC LIMIT %s"
        )
        params = (workspace_id, chain_type, limit)
    cur = await conn.execute(sql, params)
    rows = await cur.fetchall()
    return [_chain_row(r) for r in rows]


async def read_members(
    conn: Connection,
    *,
    chain_id: str,
) -> list[DocChainMemberRecord]:
    cur = await conn.execute(
        f"SELECT {_MEMBER_COLS} FROM doc_chain_members "
        "WHERE chain_id = %s ORDER BY version_index ASC, added_at ASC",
        (chain_id,),
    )
    rows = await cur.fetchall()
    return [_member_row(r) for r in rows]


async def find_chain_for_doc(
    conn: Connection,
    *,
    doc_id: str,
) -> tuple[DocChainRecord, DocChainMemberRecord] | None:
    """Reverse lookup — what chain does this file belong to?"""
    cur = await conn.execute(
        f"SELECT m.chain_id, m.doc_id, m.workspace_id, m.version_index, "
        f"m.role, m.parent_doc_id, m.added_at, "
        f"c.id, c.workspace_id, c.type, c.title, c.current_version_id, "
        f"c.chain_key, c.member_count, c.detection_confidence, c.created_at "
        f"FROM doc_chain_members m JOIN doc_chains c ON c.id = m.chain_id "
        f"WHERE m.doc_id = %s LIMIT 1",
        (doc_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    member = _member_row(row[:7])
    chain = _chain_row(row[7:])
    return (chain, member)


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


async def upsert_chain(
    conn: Connection,
    *,
    workspace_id: str,
    chain_type: str,
    title: str | None,
    chain_key: str | None,
    detection_confidence: float,
    current_version_id: str | None = None,
) -> str:
    """Find-or-create a chain by (workspace, type, chain_key). Returns id.

    When the key matches an existing chain, the existing row is returned
    untouched — title / current_version aren't overwritten here (use the
    explicit setters below to update). When chain_key is NULL, always
    creates a new row (e.g. low-confidence detection that we don't want
    to merge).
    """
    if chain_type not in CHAIN_TYPES:
        raise ValueError(f"chain_type={chain_type!r} not in {CHAIN_TYPES}")

    if chain_key is not None:
        existing = await find_chain_by_key(
            conn,
            workspace_id=workspace_id,
            chain_type=chain_type,
            chain_key=chain_key,
        )
        if existing is not None:
            return existing.id

    cur = await conn.execute(
        """
        INSERT INTO doc_chains (
            workspace_id, type, title, current_version_id,
            chain_key, member_count, detection_confidence
        ) VALUES (%s, %s, %s, %s, %s, 0, %s)
        RETURNING id::text
        """,
        (
            workspace_id, chain_type, title, current_version_id,
            chain_key, detection_confidence,
        ),
    )
    row = await cur.fetchone()
    assert row is not None
    return str(row[0])


async def add_member(
    conn: Connection,
    *,
    chain_id: str,
    doc_id: str,
    workspace_id: str,
    version_index: int,
    role: str,
    parent_doc_id: str | None = None,
) -> bool:
    """Add a doc to a chain. Returns True if a new row was inserted, False
    if it was already a member (ON CONFLICT DO NOTHING). Recomputes the
    parent chain's member_count after success."""
    if role not in MEMBER_ROLES:
        raise ValueError(f"role={role!r} not in {MEMBER_ROLES}")
    cur = await conn.execute(
        """
        INSERT INTO doc_chain_members (
            chain_id, doc_id, workspace_id, version_index, role, parent_doc_id
        ) VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (chain_id, doc_id) DO NOTHING
        """,
        (chain_id, doc_id, workspace_id, version_index, role, parent_doc_id),
    )
    inserted = getattr(cur, "rowcount", 0) > 0
    if inserted:
        await _refresh_member_count(conn, chain_id=chain_id)
    return inserted


async def _refresh_member_count(conn: Connection, *, chain_id: str) -> None:
    await conn.execute(
        "UPDATE doc_chains SET member_count = "
        "(SELECT COUNT(*) FROM doc_chain_members WHERE chain_id = %s) "
        "WHERE id = %s",
        (chain_id, chain_id),
    )


async def set_current_version(
    conn: Connection,
    *,
    chain_id: str,
    current_version_id: str | None,
) -> None:
    await conn.execute(
        "UPDATE doc_chains SET current_version_id = %s WHERE id = %s",
        (current_version_id, chain_id),
    )


async def remove_member(
    conn: Connection,
    *,
    chain_id: str,
    doc_id: str,
) -> bool:
    """User unlinks a doc from a chain (Design 3 §"Failure modes" — false
    chain). Cascades to refresh member_count + clears
    current_version_id if it pointed at the removed doc."""
    cur = await conn.execute(
        "DELETE FROM doc_chain_members WHERE chain_id = %s AND doc_id = %s",
        (chain_id, doc_id),
    )
    deleted = getattr(cur, "rowcount", 0) > 0
    if deleted:
        await conn.execute(
            "UPDATE doc_chains SET "
            "current_version_id = CASE WHEN current_version_id = %s THEN NULL "
            "ELSE current_version_id END, "
            "member_count = (SELECT COUNT(*) FROM doc_chain_members WHERE chain_id = %s) "
            "WHERE id = %s",
            (doc_id, chain_id, chain_id),
        )
    return deleted
