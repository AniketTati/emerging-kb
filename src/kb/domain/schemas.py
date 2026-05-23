"""Schema domain layer — pydantic models + DB-level repo functions.

Phase 1a + 1b:
- POST creates v1 atomically in `schema_versions` (decision #3).
- PUT serializes per-schema via `SELECT ... FOR UPDATE`, allocates the next
  monotonic version_number, inserts a new `schema_versions` row, and bumps
  `schemas.current_version_id` — all in one tx (decision #12).
- Soft delete (1a) leaves versions in place but unreachable via the API.
- Rollback lives in `kb.api.schema_versions` (router-level) since it bridges
  schemas + schema_versions; the version INSERT goes through `insert_version`.

`current_version` (int) is surfaced in every read/write response by joining
the schemas row to its current `schema_versions` row.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from kb.db.pool import Connection
from kb.domain.schema_versions import insert_version
# Phase 1c — coarse-grained versioning helpers (decision #7)
from kb.domain.schema_hierarchy import build_subtree_snapshot, restore_subtree


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
    `current_version` added in Phase 1b (api_contracts §3.2).
    """

    id: str
    name: str
    description: str
    lifecycle_state: str
    current_version: int
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
    """(id, name, description, lifecycle_state, created_at, updated_at, current_version) → SchemaResponse."""
    return SchemaResponse(
        id=str(row[0]),
        name=row[1],
        description=row[2],
        lifecycle_state=row[3],
        created_at=_iso(row[4]),
        updated_at=_iso(row[5]),
        current_version=row[6],
    )


# JOIN against schema_versions so every read includes current_version.
# Phase 1b invariant: schemas.current_version_id is NEVER NULL after any
# successful mutation (decision #3); an INNER JOIN makes this an enforced
# read-side check — a corrupt row with NULL pointer would simply be invisible.
_SELECT_COLUMNS = (
    "s.id, s.name, s.description, s.lifecycle_state, "
    "s.created_at, s.updated_at, v.version_number"
)
_FROM_JOIN = (
    "FROM schemas s "
    "JOIN schema_versions v ON s.current_version_id = v.id"
)


async def create_schema(
    conn: Connection, workspace_id: str, body: SchemaCreate
) -> SchemaResponse:
    """INSERT a new active schema + v1 atomically.

    Decision #3: POST creates v1 in the same tx. The schema row's
    `current_version_id` is updated to point at v1 before this returns.
    Raises `DuplicateNameError` on (workspace, name) collision.
    """
    import psycopg

    try:
        cur = await conn.execute(
            "INSERT INTO schemas (workspace_id, name, description) "
            "VALUES (%s, %s, %s) "
            "RETURNING id, name, description, lifecycle_state, created_at, updated_at",
            (workspace_id, body.name, body.description),
        )
        row = await cur.fetchone()
    except psycopg.errors.UniqueViolation as exc:
        raise DuplicateNameError(body.name) from exc

    schema_id = str(row[0])
    # Phase 1c: snapshot includes empty entities/relationships arrays so
    # the body shape is uniform across all versions (Phase 1c diff handles
    # empty → non-empty cleanly).
    snapshot = {
        "name": body.name,
        "description": body.description,
        "entities": [],
        "relationships": [],
    }

    version_id = await insert_version(
        conn,
        schema_id=schema_id,
        workspace_id=workspace_id,
        version_number=1,
        body=snapshot,
        parent_version_number=None,
        kind="post",
    )

    await conn.execute(
        "UPDATE schemas SET current_version_id = %s WHERE id = %s",
        (version_id, schema_id),
    )

    return SchemaResponse(
        id=schema_id,
        name=row[1],
        description=row[2],
        lifecycle_state=row[3],
        created_at=_iso(row[4]),
        updated_at=_iso(row[5]),
        current_version=1,
    )


async def list_schemas(
    conn: Connection, limit: int, offset: int
) -> SchemaListResponse:
    """List active schemas in the workspace (RLS auto-filters), sorted created_at DESC."""
    cur = await conn.execute(
        f"SELECT {_SELECT_COLUMNS} {_FROM_JOIN} "
        f"WHERE s.lifecycle_state = 'active' "
        f"ORDER BY s.created_at DESC, s.id DESC "
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
        f"SELECT {_SELECT_COLUMNS} {_FROM_JOIN} "
        f"WHERE s.id = %s AND s.lifecycle_state = 'active'",
        (schema_id,),
    )
    row = await cur.fetchone()
    if row is None:
        raise NotFoundError(schema_id)
    return _row_to_response(row)


async def update_schema(
    conn: Connection, workspace_id: str, schema_id: str, body: SchemaUpdate
) -> SchemaResponse:
    """Full-replace name + description AND insert a new version row, all in one tx.

    Decision #12: server serializes per-schema by taking
    `SELECT ... FOR UPDATE` on the schemas row first, so concurrent PUTs
    can't race the `(schema_id, version_number)` UNIQUE constraint.
    Decision #4: response carries the bumped `current_version`.
    """
    import psycopg

    # Lock the schema row + ensure it's active and visible (RLS).
    cur = await conn.execute(
        "SELECT id FROM schemas WHERE id = %s AND lifecycle_state = 'active' FOR UPDATE",
        (schema_id,),
    )
    if await cur.fetchone() is None:
        raise NotFoundError(schema_id)

    # Allocate the next version_number under the row lock.
    cur = await conn.execute(
        "SELECT COALESCE(max(version_number), 0) FROM schema_versions WHERE schema_id = %s",
        (schema_id,),
    )
    prior_version = (await cur.fetchone())[0]
    new_version = prior_version + 1

    # Apply the update to schemas (catches name collision under the same lock).
    try:
        cur = await conn.execute(
            "UPDATE schemas SET name = %s, description = %s, updated_at = now() "
            "WHERE id = %s AND lifecycle_state = 'active' "
            "RETURNING id, name, description, lifecycle_state, created_at, updated_at",
            (body.name, body.description, schema_id),
        )
        row = await cur.fetchone()
    except psycopg.errors.UniqueViolation as exc:
        raise DuplicateNameError(body.name) from exc

    if row is None:
        # Should be impossible — we held FOR UPDATE — but defensive.
        raise NotFoundError(schema_id)

    # Phase 1c: snapshot includes the full subtree (entities + fields +
    # relationships) so a future rollback can restore the hierarchy.
    subtree = await build_subtree_snapshot(conn, schema_id)
    snapshot = {
        "name": body.name,
        "description": body.description,
        "entities": subtree["entities"],
        "relationships": subtree["relationships"],
    }
    version_id = await insert_version(
        conn,
        schema_id=schema_id,
        workspace_id=workspace_id,
        version_number=new_version,
        body=snapshot,
        parent_version_number=prior_version,
        kind="put",
    )

    await conn.execute(
        "UPDATE schemas SET current_version_id = %s WHERE id = %s",
        (version_id, schema_id),
    )

    return SchemaResponse(
        id=str(row[0]),
        name=row[1],
        description=row[2],
        lifecycle_state=row[3],
        created_at=_iso(row[4]),
        updated_at=_iso(row[5]),
        current_version=new_version,
    )


async def lock_and_assert_active_schema(conn: Connection, schema_id: str) -> None:
    """SELECT FOR UPDATE on the schemas row; raises NotFoundError if not
    active (or wrong workspace via RLS). Used by every nested CRUD endpoint
    (decisions #12 + §4.1 #4) to serialize concurrent mutations per-schema.
    """
    cur = await conn.execute(
        "SELECT id FROM schemas WHERE id = %s AND lifecycle_state = 'active' FOR UPDATE",
        (schema_id,),
    )
    if await cur.fetchone() is None:
        raise NotFoundError(schema_id)


async def bump_schema_version(
    conn: Connection,
    workspace_id: str,
    schema_id: str,
    *,
    kind: str = "put",
) -> int:
    """Write a new schema_versions row whose body is the current subtree snapshot.

    Coarse-grained versioning (decision #7): every nested entity / field /
    relationship CRUD calls this AFTER its row mutation completes (inside
    the same tx). Returns the new monotonic `version_number`.

    Caller must already hold `SELECT ... FOR UPDATE` on the schemas row
    (via `lock_and_assert_active_schema`).
    """
    cur = await conn.execute(
        "SELECT name, description FROM schemas WHERE id = %s",
        (schema_id,),
    )
    schema_row = await cur.fetchone()
    if schema_row is None:
        raise NotFoundError(schema_id)
    name, description = schema_row

    cur = await conn.execute(
        "SELECT COALESCE(max(version_number), 0) FROM schema_versions WHERE schema_id = %s",
        (schema_id,),
    )
    prior = (await cur.fetchone())[0]
    new_version = prior + 1

    subtree = await build_subtree_snapshot(conn, schema_id)
    snapshot = {
        "name": name,
        "description": description,
        "entities": subtree["entities"],
        "relationships": subtree["relationships"],
    }

    version_id = await insert_version(
        conn,
        schema_id=schema_id,
        workspace_id=workspace_id,
        version_number=new_version,
        body=snapshot,
        parent_version_number=prior if prior >= 1 else None,
        kind=kind,
    )

    await conn.execute(
        "UPDATE schemas SET current_version_id = %s, updated_at = now() WHERE id = %s",
        (version_id, schema_id),
    )
    return new_version


async def soft_delete_schema(conn: Connection, schema_id: str) -> None:
    """Set lifecycle_state='deleted'. Raises `NotFoundError` if already deleted or missing.

    Phase 1b note: versions remain in the table (immutable) but become
    unreachable via the API — `get_schema` filters by `lifecycle_state='active'`
    so any version lookup that joins through the parent will 404.
    """
    cur = await conn.execute(
        "UPDATE schemas SET lifecycle_state = 'deleted', updated_at = now() "
        "WHERE id = %s AND lifecycle_state = 'active' "
        "RETURNING id",
        (schema_id,),
    )
    row = await cur.fetchone()
    if row is None:
        raise NotFoundError(schema_id)


async def rollback_to_version(
    conn: Connection,
    workspace_id: str,
    schema_id: str,
    target_version: int,
) -> SchemaResponse:
    """Clone-forward rollback (decision #5).

    Locks the schemas row (FOR UPDATE) so concurrent PUTs serialize behind
    this rollback. If `target_version == current_version` raises
    `RollbackNoopError` (decision #13). Returns the updated schema with
    the bumped `current_version`.
    """
    from kb.domain.schema_versions import RollbackNoopError, VersionNotFoundError

    # Lock parent schema row + confirm active + visible.
    cur = await conn.execute(
        "SELECT s.id, v.version_number "
        "FROM schemas s "
        "JOIN schema_versions v ON s.current_version_id = v.id "
        "WHERE s.id = %s AND s.lifecycle_state = 'active' FOR UPDATE OF s",
        (schema_id,),
    )
    row = await cur.fetchone()
    if row is None:
        raise NotFoundError(schema_id)
    current_version = row[1]

    if target_version == current_version:
        raise RollbackNoopError(
            f"schema={schema_id} target={target_version} is the current version"
        )

    # Read the target version's body.
    cur = await conn.execute(
        "SELECT body FROM schema_versions WHERE schema_id = %s AND version_number = %s",
        (schema_id, target_version),
    )
    target_row = await cur.fetchone()
    if target_row is None:
        raise VersionNotFoundError(f"schema={schema_id} version={target_version}")
    target_body = target_row[0]
    # psycopg returns jsonb as dict, but be defensive
    if isinstance(target_body, str):
        import json

        target_body = json.loads(target_body)

    new_version = current_version + 1

    # Phase 1c — restore the subtree from the snapshot BEFORE writing the
    # new version row. The restorer soft-deletes current entities/fields/
    # relationships and re-inserts from the snapshot (relationship from/to
    # bind by NAME per §4.1 #5).
    await restore_subtree(conn, workspace_id, schema_id, target_body)

    # Update schemas row with the cloned snapshot's name/description.
    try:
        cur = await conn.execute(
            "UPDATE schemas SET name = %s, description = %s, updated_at = now() "
            "WHERE id = %s AND lifecycle_state = 'active' "
            "RETURNING id, name, description, lifecycle_state, created_at, updated_at",
            (target_body["name"], target_body["description"], schema_id),
        )
        updated_row = await cur.fetchone()
    except Exception:
        # A name collision on rollback is the same shape as PUT — surface
        # via DuplicateNameError. (Tests don't currently exercise this; it
        # arises when a deleted-then-recreated workspace name was reused
        # in the interim.)
        raise

    # The body of the new (rollback) version is the snapshot AS IT STANDS
    # AFTER the restore (i.e., same shape as `target_body`, but built from
    # the freshly restored rows so any data drift in `target_body` is
    # corrected). Build it explicitly rather than trusting `target_body`.
    new_subtree = await build_subtree_snapshot(conn, schema_id)

    # Insert the new (rollback) version.
    version_id = await insert_version(
        conn,
        schema_id=schema_id,
        workspace_id=workspace_id,
        version_number=new_version,
        body=new_subtree,
        parent_version_number=current_version,
        kind="rollback",
    )

    await conn.execute(
        "UPDATE schemas SET current_version_id = %s WHERE id = %s",
        (version_id, schema_id),
    )

    return SchemaResponse(
        id=str(updated_row[0]),
        name=updated_row[1],
        description=updated_row[2],
        lifecycle_state=updated_row[3],
        created_at=_iso(updated_row[4]),
        updated_at=_iso(updated_row[5]),
        current_version=new_version,
    )
