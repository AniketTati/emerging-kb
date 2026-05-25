"""B1 / WA-4 — relationships + relationship_evidence repo (arch §5 stage 16).

UPSERT semantics on `(workspace, subj, obj, predicate)`:
  - first insert: create row with n_evidence=1, confidence as supplied
  - subsequent: bump n_evidence, MAX confidence
Evidence rows are append-only (one per supporting triple).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from kb.db.pool import Connection


@dataclass(frozen=True)
class RelationshipRecord:
    id: str
    workspace_id: str
    subject_entity_id: str
    object_entity_id: str
    predicate: str
    confidence: float
    n_evidence: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class RelationshipEvidenceRecord:
    id: str
    relationship_id: str
    triple_id: str | None
    file_id: str | None
    chunk_id: str | None
    confidence: float
    created_at: str


_REL_COLS = (
    "id, workspace_id, subject_entity_id, object_entity_id, predicate, "
    "confidence, n_evidence, created_at, updated_at"
)
_EV_COLS = (
    "id, relationship_id, triple_id, file_id, chunk_id, confidence, created_at"
)


def _rel_row(row: tuple) -> RelationshipRecord:
    return RelationshipRecord(
        id=str(row[0]),
        workspace_id=str(row[1]),
        subject_entity_id=str(row[2]),
        object_entity_id=str(row[3]),
        predicate=str(row[4]),
        confidence=float(row[5]),
        n_evidence=int(row[6]),
        created_at=row[7].isoformat() if hasattr(row[7], "isoformat") else str(row[7]),
        updated_at=row[8].isoformat() if hasattr(row[8], "isoformat") else str(row[8]),
    )


def _ev_row(row: tuple) -> RelationshipEvidenceRecord:
    return RelationshipEvidenceRecord(
        id=str(row[0]),
        relationship_id=str(row[1]),
        triple_id=str(row[2]) if row[2] is not None else None,
        file_id=str(row[3]) if row[3] is not None else None,
        chunk_id=str(row[4]) if row[4] is not None else None,
        confidence=float(row[5]),
        created_at=row[6].isoformat() if hasattr(row[6], "isoformat") else str(row[6]),
    )


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


async def upsert_relationship(
    conn: Connection,
    *,
    workspace_id: str,
    subject_entity_id: str,
    object_entity_id: str,
    predicate: str,
    confidence: float = 0.5,
) -> tuple[str, bool]:
    """Find-or-create on (workspace, subj, obj, predicate). Returns
    (relationship_id, was_inserted). On existing row: bump n_evidence + 1,
    MAX(confidence)."""
    if subject_entity_id == object_entity_id:
        raise ValueError("subject and object entity ids must differ")
    cur = await conn.execute(
        """
        INSERT INTO relationships (
            workspace_id, subject_entity_id, object_entity_id, predicate,
            confidence, n_evidence
        ) VALUES (%s, %s, %s, %s, %s, 1)
        ON CONFLICT (workspace_id, subject_entity_id, object_entity_id, predicate)
        DO UPDATE SET
            n_evidence = relationships.n_evidence + 1,
            confidence = GREATEST(relationships.confidence, EXCLUDED.confidence),
            updated_at = NOW()
        RETURNING id::text, (xmax = 0) AS inserted
        """,
        (workspace_id, subject_entity_id, object_entity_id, predicate, confidence),
    )
    row = await cur.fetchone()
    assert row is not None
    return str(row[0]), bool(row[1])


async def add_evidence(
    conn: Connection,
    *,
    workspace_id: str,
    relationship_id: str,
    triple_id: str | None = None,
    file_id: str | None = None,
    chunk_id: str | None = None,
    confidence: float = 0.5,
) -> str:
    cur = await conn.execute(
        """
        INSERT INTO relationship_evidence (
            workspace_id, relationship_id, triple_id, file_id, chunk_id, confidence
        ) VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id::text
        """,
        (workspace_id, relationship_id, triple_id, file_id, chunk_id, confidence),
    )
    row = await cur.fetchone()
    assert row is not None
    return str(row[0])


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


async def list_relationships_for_entity(
    conn: Connection,
    *,
    workspace_id: str,
    entity_id: str,
    direction: str = "both",  # 'subject' | 'object' | 'both'
    limit: int = 200,
) -> list[RelationshipRecord]:
    if direction == "subject":
        where = "subject_entity_id = %s"
        params: tuple = (workspace_id, entity_id, limit)
    elif direction == "object":
        where = "object_entity_id = %s"
        params = (workspace_id, entity_id, limit)
    elif direction == "both":
        where = "(subject_entity_id = %s OR object_entity_id = %s)"
        params = (workspace_id, entity_id, entity_id, limit)
    else:
        raise ValueError(f"direction must be subject/object/both, got {direction!r}")
    cur = await conn.execute(
        f"SELECT {_REL_COLS} FROM relationships "
        f"WHERE workspace_id = %s AND {where} "
        "ORDER BY n_evidence DESC, confidence DESC LIMIT %s",
        params,
    )
    return [_rel_row(r) for r in await cur.fetchall()]


async def list_relationships_for_workspace(
    conn: Connection,
    *,
    workspace_id: str,
    predicate: str | None = None,
    limit: int = 200,
) -> list[RelationshipRecord]:
    if predicate is None:
        cur = await conn.execute(
            f"SELECT {_REL_COLS} FROM relationships "
            "WHERE workspace_id = %s "
            "ORDER BY n_evidence DESC, confidence DESC LIMIT %s",
            (workspace_id, limit),
        )
    else:
        cur = await conn.execute(
            f"SELECT {_REL_COLS} FROM relationships "
            "WHERE workspace_id = %s AND lower(predicate) = lower(%s) "
            "ORDER BY n_evidence DESC, confidence DESC LIMIT %s",
            (workspace_id, predicate, limit),
        )
    return [_rel_row(r) for r in await cur.fetchall()]


async def read_evidence_for_relationship(
    conn: Connection, *, relationship_id: str, limit: int = 50,
) -> list[RelationshipEvidenceRecord]:
    cur = await conn.execute(
        f"SELECT {_EV_COLS} FROM relationship_evidence "
        "WHERE relationship_id = %s ORDER BY created_at ASC LIMIT %s",
        (relationship_id, limit),
    )
    return [_ev_row(r) for r in await cur.fetchall()]


async def count_relationships_for_workspace(
    conn: Connection, *, workspace_id: str,
) -> int:
    cur = await conn.execute(
        "SELECT COUNT(*) FROM relationships WHERE workspace_id = %s",
        (workspace_id,),
    )
    row = await cur.fetchone()
    return int(row[0]) if row else 0
