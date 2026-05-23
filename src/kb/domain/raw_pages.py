"""Raw-pages domain — repo for the immutable per-page output table.

Phase 2a. INSERTs go through `insert_raw_page` (called by the worker after
the parser returns ParsedDocument). UPDATEs are not exposed because the
table is immutable at the DB layer (REVOKE UPDATE, DELETE from kb_app).
"""

from __future__ import annotations

import json
from datetime import datetime

from pydantic import BaseModel

from kb.db.pool import Connection


class RawPageResponse(BaseModel):
    page_number: int
    text: str
    layout_json: dict
    content_sha: str
    created_at: str


class RawPageListResponse(BaseModel):
    items: list[RawPageResponse]
    total: int
    limit: int
    offset: int


def _iso(ts: datetime) -> str:
    return ts.astimezone().isoformat().replace("+00:00", "Z")


async def insert_raw_page(
    conn: Connection,
    *,
    file_id: str,
    workspace_id: str,
    page_number: int,
    text: str,
    layout_json: dict,
    content_sha: str,
) -> None:
    """INSERT one row. Idempotent on `(file_id, page_number)` UNIQUE via
    ON CONFLICT DO NOTHING — a replayed worker won't duplicate."""
    await conn.execute(
        "INSERT INTO raw_pages "
        "(file_id, workspace_id, page_number, text, layout_json, content_sha) "
        "VALUES (%s, %s, %s, %s, %s::jsonb, %s) "
        "ON CONFLICT (file_id, page_number) DO NOTHING",
        (file_id, workspace_id, page_number, text,
         json.dumps(layout_json), content_sha),
    )


async def list_raw_pages(
    conn: Connection, file_id: str, limit: int, offset: int
) -> RawPageListResponse:
    cur = await conn.execute(
        "SELECT page_number, text, layout_json, content_sha, created_at "
        "FROM raw_pages WHERE file_id = %s "
        "ORDER BY page_number ASC "
        "LIMIT %s OFFSET %s",
        (file_id, limit, offset),
    )
    rows = await cur.fetchall()

    cur = await conn.execute(
        "SELECT count(*) FROM raw_pages WHERE file_id = %s",
        (file_id,),
    )
    total = (await cur.fetchone())[0]

    return RawPageListResponse(
        items=[
            RawPageResponse(
                page_number=r[0],
                text=r[1],
                layout_json=r[2] if isinstance(r[2], dict) else json.loads(r[2]),
                content_sha=r[3],
                created_at=_iso(r[4]),
            )
            for r in rows
        ],
        total=total, limit=limit, offset=offset,
    )
