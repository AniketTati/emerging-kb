"""B1 / WA-4 — extracted_triples repo (architecture §5 stage 13).

INSERT-only audit table. Reads are by file_id (for the relationship
builder) and by workspace_id (for /triples debug endpoint).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from kb.db.pool import Connection


@dataclass(frozen=True)
class TripleRecord:
    id: str
    workspace_id: str
    file_id: str
    chunk_id: str | None
    subject_text: str
    predicate_text: str
    object_text: str
    confidence: float
    model_id: str
    created_at: str


_SELECT_COLS = (
    "id, workspace_id, file_id, chunk_id, subject_text, predicate_text, "
    "object_text, confidence, model_id, created_at"
)


def _row_to_record(row: tuple) -> TripleRecord:
    return TripleRecord(
        id=str(row[0]),
        workspace_id=str(row[1]),
        file_id=str(row[2]),
        chunk_id=str(row[3]) if row[3] is not None else None,
        subject_text=str(row[4]),
        predicate_text=str(row[5]),
        object_text=str(row[6]),
        confidence=float(row[7]),
        model_id=str(row[8]),
        created_at=row[9].isoformat() if hasattr(row[9], "isoformat") else str(row[9]),
    )


async def insert_triple(
    conn: Connection,
    *,
    workspace_id: str,
    file_id: str,
    subject_text: str,
    predicate_text: str,
    object_text: str,
    chunk_id: str | None = None,
    confidence: float = 0.5,
    model_id: str = "identity",
    subject_char_start: int | None = None,
    subject_char_end: int | None = None,
    object_char_start: int | None = None,
    object_char_end: int | None = None,
) -> str:
    cur = await conn.execute(
        """
        INSERT INTO extracted_triples (
            workspace_id, file_id, chunk_id, subject_text, predicate_text,
            object_text, confidence, model_id,
            subject_char_start, subject_char_end,
            object_char_start, object_char_end
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id::text
        """,
        (
            workspace_id, file_id, chunk_id, subject_text, predicate_text,
            object_text, confidence, model_id,
            subject_char_start, subject_char_end,
            object_char_start, object_char_end,
        ),
    )
    row = await cur.fetchone()
    assert row is not None
    return str(row[0])


async def insert_triples_batch(
    conn: Connection,
    *,
    workspace_id: str,
    file_id: str,
    model_id: str,
    triples: Iterable[tuple],
) -> list[str]:
    """Bulk insert. Each tuple is (subject, predicate, object, confidence,
    chunk_id|None) OR (subject, predicate, object, confidence, chunk_id|None,
    subject_char_start, subject_char_end, object_char_start, object_char_end).
    Returns the new row ids in order."""
    out: list[str] = []
    for t in triples:
        if len(t) == 5:
            subj, pred, obj, conf, chunk_id = t
            s_start = s_end = o_start = o_end = None
        elif len(t) == 9:
            (subj, pred, obj, conf, chunk_id,
             s_start, s_end, o_start, o_end) = t
        else:
            continue
        if not subj or not pred or not obj:
            continue  # skip empties — CHECK would reject
        new_id = await insert_triple(
            conn,
            workspace_id=workspace_id,
            file_id=file_id,
            subject_text=subj,
            predicate_text=pred,
            object_text=obj,
            chunk_id=chunk_id,
            confidence=conf,
            model_id=model_id,
            subject_char_start=s_start,
            subject_char_end=s_end,
            object_char_start=o_start,
            object_char_end=o_end,
        )
        out.append(new_id)
    return out


async def read_triples_for_file(
    conn: Connection, *, file_id: str,
) -> list[TripleRecord]:
    cur = await conn.execute(
        f"SELECT {_SELECT_COLS} FROM extracted_triples WHERE file_id = %s "
        "ORDER BY created_at ASC",
        (file_id,),
    )
    return [_row_to_record(r) for r in await cur.fetchall()]


async def read_triples_for_workspace(
    conn: Connection, *, workspace_id: str, limit: int = 200,
) -> list[TripleRecord]:
    cur = await conn.execute(
        f"SELECT {_SELECT_COLS} FROM extracted_triples "
        "WHERE workspace_id = %s ORDER BY created_at DESC LIMIT %s",
        (workspace_id, limit),
    )
    return [_row_to_record(r) for r in await cur.fetchall()]


async def count_triples_for_file(conn: Connection, *, file_id: str) -> int:
    cur = await conn.execute(
        "SELECT COUNT(*) FROM extracted_triples WHERE file_id = %s",
        (file_id,),
    )
    row = await cur.fetchone()
    return int(row[0]) if row else 0
