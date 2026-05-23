"""Schema domain layer — pydantic models + DB-level repo functions.

Phase 1a scope. Phase 1b will reuse these by wrapping `update_schema` with the
"always create a new version" trigger and adding version-table queries.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from kb.db.pool import Connection


# ---------------------------------------------------------------------------
# Pydantic models — request bodies + response shapes
# ---------------------------------------------------------------------------


class SchemaCreate(BaseModel):
    """Body for POST /schemas (and PUT — full replace)."""

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=200)]
    description: Annotated[str, Field(max_length=10000)] = ""


# PUT body has the same shape as POST (full-replace semantics).
SchemaUpdate = SchemaCreate


class SchemaResponse(BaseModel):
    """Schema object as returned by every read/write endpoint.

    NB: no `workspace_id` field — api_contracts §2.1 design call.
    """

    id: str
    name: str
    description: str
    lifecycle_state: str
    created_at: str
    updated_at: str


class SchemaListResponse(BaseModel):
    items: list[SchemaResponse]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# Domain exceptions — translated to HTTP errors in the API layer
# ---------------------------------------------------------------------------


class DuplicateNameError(Exception):
    """A schema with this name already exists (active) in the workspace."""


class NotFoundError(Exception):
    """Schema does not exist, is soft-deleted, or belongs to a different workspace."""


# ---------------------------------------------------------------------------
# Repo functions — talk to PG via the per-request kb_app connection
# ---------------------------------------------------------------------------


def _iso(ts: datetime) -> str:
    """ISO-8601 UTC with Z suffix (api_contracts §0.2)."""
    return ts.astimezone().isoformat().replace("+00:00", "Z")


def _row_to_response(row: tuple) -> SchemaResponse:
    """(id, name, description, lifecycle_state, created_at, updated_at) → SchemaResponse."""
    return SchemaResponse(
        id=str(row[0]),
        name=row[1],
        description=row[2],
        lifecycle_state=row[3],
        created_at=_iso(row[4]),
        updated_at=_iso(row[5]),
    )


_COLUMNS = "id, name, description, lifecycle_state, created_at, updated_at"


async def create_schema(
    conn: Connection, workspace_id: str, body: SchemaCreate
) -> SchemaResponse:
    """INSERT a new active schema. Raises `DuplicateNameError` on (workspace, name) collision."""
    import psycopg

    try:
        cur = await conn.execute(
            f"INSERT INTO schemas (workspace_id, name, description) "
            f"VALUES (%s, %s, %s) "
            f"RETURNING {_COLUMNS}",
            (workspace_id, body.name, body.description),
        )
        row = await cur.fetchone()
    except psycopg.errors.UniqueViolation as exc:
        raise DuplicateNameError(body.name) from exc
    return _row_to_response(row)


async def list_schemas(
    conn: Connection, limit: int, offset: int
) -> SchemaListResponse:
    """List active schemas in the workspace (RLS auto-filters), sorted created_at DESC."""
    cur = await conn.execute(
        f"SELECT {_COLUMNS} FROM schemas "
        f"WHERE lifecycle_state = 'active' "
        f"ORDER BY created_at DESC, id DESC "
        f"LIMIT %s OFFSET %s",
        (limit, offset),
    )
    items_rows = await cur.fetchall()

    cur = await conn.execute(
        "SELECT count(*) FROM schemas WHERE lifecycle_state = 'active'"
    )
    total_row = await cur.fetchone()

    return SchemaListResponse(
        items=[_row_to_response(r) for r in items_rows],
        total=total_row[0],
        limit=limit,
        offset=offset,
    )


async def get_schema(conn: Connection, schema_id: str) -> SchemaResponse:
    """GET one. Raises `NotFoundError` if missing / deleted / wrong workspace.

    Wrong-workspace is 404 because RLS hides the row — the API layer can't
    tell "exists but you can't see it" from "doesn't exist" (api_contracts §2.4).
    """
    cur = await conn.execute(
        f"SELECT {_COLUMNS} FROM schemas "
        f"WHERE id = %s AND lifecycle_state = 'active'",
        (schema_id,),
    )
    row = await cur.fetchone()
    if row is None:
        raise NotFoundError(schema_id)
    return _row_to_response(row)


async def update_schema(
    conn: Connection, schema_id: str, body: SchemaUpdate
) -> SchemaResponse:
    """Full-replace name + description. Bumps updated_at. Phase 1b wraps this
    with the version-creation trigger."""
    import psycopg

    try:
        cur = await conn.execute(
            f"UPDATE schemas SET name = %s, description = %s, updated_at = now() "
            f"WHERE id = %s AND lifecycle_state = 'active' "
            f"RETURNING {_COLUMNS}",
            (body.name, body.description, schema_id),
        )
        row = await cur.fetchone()
    except psycopg.errors.UniqueViolation as exc:
        raise DuplicateNameError(body.name) from exc

    if row is None:
        raise NotFoundError(schema_id)
    return _row_to_response(row)


async def soft_delete_schema(conn: Connection, schema_id: str) -> None:
    """Set lifecycle_state='deleted'. Raises `NotFoundError` if already deleted or missing."""
    cur = await conn.execute(
        "UPDATE schemas SET lifecycle_state = 'deleted', updated_at = now() "
        "WHERE id = %s AND lifecycle_state = 'active' "
        "RETURNING id",
        (schema_id,),
    )
    row = await cur.fetchone()
    if row is None:
        raise NotFoundError(schema_id)
