"""Schema-hierarchy domain layer — entities + fields + relationships +
subtree snapshot builder + name-resolved rollback restorer.

Phase 1c. api_contracts §4; build_tracker §5.4 decisions #1–#13.

Key responsibilities:
- Pydantic models + DB-level repo functions for the 3 new tables.
- `build_subtree_snapshot(conn, schema_id)` → the canonical jsonb body
  written into every new `schema_versions` row at 1c.
- `restore_subtree(conn, workspace_id, schema_id, snapshot)` → the rollback
  path: soft-delete current children, INSERT entities/fields/relationships
  from the snapshot. Relationships re-bind by NAME so re-created entities
  (which get new UUIDs) get correctly wired.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from kb.db.pool import Connection


# ---------------------------------------------------------------------------
# Pydantic — request bodies + response shapes
# ---------------------------------------------------------------------------


_FIELD_TYPES = ("string", "number", "boolean", "date", "datetime")
_REL_KINDS = ("contains", "part_of", "references", "associates", "attribute_link")
_CARDINALITIES = ("one_to_one", "one_to_many", "many_to_many")


class EntityCreate(BaseModel):
    """Body for POST/PUT /schemas/:id/entities."""

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=200)]
    description: Annotated[str, Field(max_length=10000)] = ""


EntityUpdate = EntityCreate


class EntityResponse(BaseModel):
    id: str
    name: str
    description: str
    lifecycle_state: str
    created_at: str
    updated_at: str


class EntityListResponse(BaseModel):
    items: list[EntityResponse]
    total: int
    limit: int
    offset: int


class FieldCreate(BaseModel):
    """Body for POST/PUT /schemas/:id/entities/:eid/fields."""

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=200)]
    type: Annotated[str, Field(pattern=r"^(string|number|boolean|date|datetime)$")]
    nl_description: Annotated[str, Field(max_length=10000)] = ""
    is_required: bool = False


FieldUpdate = FieldCreate


class FieldResponse(BaseModel):
    id: str
    name: str
    type: str
    nl_description: str
    is_required: bool
    lifecycle_state: str
    created_at: str
    updated_at: str


class FieldListResponse(BaseModel):
    items: list[FieldResponse]
    total: int
    limit: int
    offset: int


class RelationshipCreate(BaseModel):
    """Body for POST /schemas/:id/relationships."""

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=200)]
    from_entity_id: str
    to_entity_id: str
    kind: Annotated[str, Field(pattern=r"^(contains|part_of|references|associates|attribute_link)$")]
    cardinality: Annotated[str, Field(pattern=r"^(one_to_one|one_to_many|many_to_many)$")] = "one_to_many"
    cascade_delete: bool = False
    single_parent: bool = True


class RelationshipResponse(BaseModel):
    id: str
    name: str
    from_entity_id: str
    to_entity_id: str
    kind: str
    cardinality: str
    cascade_delete: bool
    single_parent: bool
    lifecycle_state: str
    created_at: str
    updated_at: str


class RelationshipListResponse(BaseModel):
    items: list[RelationshipResponse]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# Domain exceptions — translated to HTTP errors in the API layer
# ---------------------------------------------------------------------------


class EntityNotFoundError(Exception):
    """Entity not found / soft-deleted / wrong workspace / wrong schema."""


class FieldNotFoundError(Exception):
    """Field not found / soft-deleted / wrong entity."""


class RelationshipNotFoundError(Exception):
    """Relationship not found / soft-deleted / wrong schema."""


class EntityNameConflictError(Exception):
    """Another active entity with the same name exists in this schema."""


class FieldNameConflictError(Exception):
    """Another active field with the same name exists on this entity."""


class RelationshipNameConflictError(Exception):
    """Another active relationship with the same name exists in this schema."""


class InvalidCrossSchemaReferenceError(Exception):
    """A relationship's from_entity_id or to_entity_id is not in this schema."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso(ts: datetime) -> str:
    return ts.astimezone().isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Entities — repo functions
# ---------------------------------------------------------------------------


_ENTITY_COLS = "id, name, description, lifecycle_state, created_at, updated_at"


def _entity_row_to_response(row: tuple) -> EntityResponse:
    return EntityResponse(
        id=str(row[0]),
        name=row[1],
        description=row[2],
        lifecycle_state=row[3],
        created_at=_iso(row[4]),
        updated_at=_iso(row[5]),
    )


async def create_entity(
    conn: Connection, workspace_id: str, schema_id: str, body: EntityCreate
) -> EntityResponse:
    """INSERT an entity. Raises EntityNameConflictError on (schema_id, name) collision."""
    import psycopg

    try:
        cur = await conn.execute(
            f"INSERT INTO schema_entities (schema_id, workspace_id, name, description) "
            f"VALUES (%s, %s, %s, %s) RETURNING {_ENTITY_COLS}",
            (schema_id, workspace_id, body.name, body.description),
        )
        row = await cur.fetchone()
    except psycopg.errors.UniqueViolation as exc:
        raise EntityNameConflictError(body.name) from exc
    return _entity_row_to_response(row)


async def list_entities(
    conn: Connection, schema_id: str, limit: int, offset: int
) -> EntityListResponse:
    cur = await conn.execute(
        f"SELECT {_ENTITY_COLS} FROM schema_entities "
        f"WHERE schema_id = %s AND lifecycle_state = 'active' "
        f"ORDER BY created_at DESC, id DESC "
        f"LIMIT %s OFFSET %s",
        (schema_id, limit, offset),
    )
    rows = await cur.fetchall()

    cur = await conn.execute(
        "SELECT count(*) FROM schema_entities "
        "WHERE schema_id = %s AND lifecycle_state = 'active'",
        (schema_id,),
    )
    total = (await cur.fetchone())[0]

    return EntityListResponse(
        items=[_entity_row_to_response(r) for r in rows],
        total=total, limit=limit, offset=offset,
    )


async def get_entity(conn: Connection, schema_id: str, entity_id: str) -> EntityResponse:
    cur = await conn.execute(
        f"SELECT {_ENTITY_COLS} FROM schema_entities "
        f"WHERE id = %s AND schema_id = %s AND lifecycle_state = 'active'",
        (entity_id, schema_id),
    )
    row = await cur.fetchone()
    if row is None:
        raise EntityNotFoundError(f"schema={schema_id} entity={entity_id}")
    return _entity_row_to_response(row)


async def update_entity(
    conn: Connection, schema_id: str, entity_id: str, body: EntityUpdate
) -> EntityResponse:
    import psycopg

    try:
        cur = await conn.execute(
            f"UPDATE schema_entities SET name = %s, description = %s, updated_at = now() "
            f"WHERE id = %s AND schema_id = %s AND lifecycle_state = 'active' "
            f"RETURNING {_ENTITY_COLS}",
            (body.name, body.description, entity_id, schema_id),
        )
        row = await cur.fetchone()
    except psycopg.errors.UniqueViolation as exc:
        raise EntityNameConflictError(body.name) from exc

    if row is None:
        raise EntityNotFoundError(f"schema={schema_id} entity={entity_id}")
    return _entity_row_to_response(row)


async def soft_delete_entity(conn: Connection, schema_id: str, entity_id: str) -> None:
    """Soft-delete entity AND cascade-soft-delete its fields + any relationships
    referencing it (§4.8 contract; application-level cascade in one tx)."""
    # Cascade: fields on this entity
    await conn.execute(
        "UPDATE schema_fields SET lifecycle_state = 'deleted', updated_at = now() "
        "WHERE entity_id = %s AND lifecycle_state = 'active'",
        (entity_id,),
    )
    # Cascade: relationships referencing this entity
    await conn.execute(
        "UPDATE schema_relationships SET lifecycle_state = 'deleted', updated_at = now() "
        "WHERE (from_entity_id = %s OR to_entity_id = %s) AND lifecycle_state = 'active'",
        (entity_id, entity_id),
    )
    # Finally: the entity itself
    cur = await conn.execute(
        "UPDATE schema_entities SET lifecycle_state = 'deleted', updated_at = now() "
        "WHERE id = %s AND schema_id = %s AND lifecycle_state = 'active' "
        "RETURNING id",
        (entity_id, schema_id),
    )
    if await cur.fetchone() is None:
        raise EntityNotFoundError(f"schema={schema_id} entity={entity_id}")


# ---------------------------------------------------------------------------
# Fields — repo functions
# ---------------------------------------------------------------------------


_FIELD_COLS = "id, name, type, nl_description, is_required, lifecycle_state, created_at, updated_at"


def _field_row_to_response(row: tuple) -> FieldResponse:
    return FieldResponse(
        id=str(row[0]),
        name=row[1],
        type=row[2],
        nl_description=row[3],
        is_required=row[4],
        lifecycle_state=row[5],
        created_at=_iso(row[6]),
        updated_at=_iso(row[7]),
    )


async def _assert_entity_active(conn: Connection, schema_id: str, entity_id: str) -> None:
    """404-gate via the parent entity."""
    cur = await conn.execute(
        "SELECT 1 FROM schema_entities "
        "WHERE id = %s AND schema_id = %s AND lifecycle_state = 'active'",
        (entity_id, schema_id),
    )
    if await cur.fetchone() is None:
        raise EntityNotFoundError(f"schema={schema_id} entity={entity_id}")


async def create_field(
    conn: Connection,
    workspace_id: str,
    schema_id: str,
    entity_id: str,
    body: FieldCreate,
) -> FieldResponse:
    import psycopg

    await _assert_entity_active(conn, schema_id, entity_id)

    try:
        cur = await conn.execute(
            f"INSERT INTO schema_fields "
            f"(entity_id, workspace_id, name, type, nl_description, is_required) "
            f"VALUES (%s, %s, %s, %s, %s, %s) RETURNING {_FIELD_COLS}",
            (entity_id, workspace_id, body.name, body.type,
             body.nl_description, body.is_required),
        )
        row = await cur.fetchone()
    except psycopg.errors.UniqueViolation as exc:
        raise FieldNameConflictError(body.name) from exc
    return _field_row_to_response(row)


async def list_fields(
    conn: Connection, schema_id: str, entity_id: str, limit: int, offset: int
) -> FieldListResponse:
    await _assert_entity_active(conn, schema_id, entity_id)

    cur = await conn.execute(
        f"SELECT {_FIELD_COLS} FROM schema_fields "
        f"WHERE entity_id = %s AND lifecycle_state = 'active' "
        f"ORDER BY created_at DESC, id DESC "
        f"LIMIT %s OFFSET %s",
        (entity_id, limit, offset),
    )
    rows = await cur.fetchall()

    cur = await conn.execute(
        "SELECT count(*) FROM schema_fields "
        "WHERE entity_id = %s AND lifecycle_state = 'active'",
        (entity_id,),
    )
    total = (await cur.fetchone())[0]

    return FieldListResponse(
        items=[_field_row_to_response(r) for r in rows],
        total=total, limit=limit, offset=offset,
    )


async def update_field(
    conn: Connection, schema_id: str, entity_id: str, field_id: str, body: FieldUpdate
) -> FieldResponse:
    import psycopg

    await _assert_entity_active(conn, schema_id, entity_id)

    try:
        cur = await conn.execute(
            f"UPDATE schema_fields "
            f"SET name = %s, type = %s, nl_description = %s, is_required = %s, "
            f"    updated_at = now() "
            f"WHERE id = %s AND entity_id = %s AND lifecycle_state = 'active' "
            f"RETURNING {_FIELD_COLS}",
            (body.name, body.type, body.nl_description, body.is_required,
             field_id, entity_id),
        )
        row = await cur.fetchone()
    except psycopg.errors.UniqueViolation as exc:
        raise FieldNameConflictError(body.name) from exc

    if row is None:
        raise FieldNotFoundError(f"entity={entity_id} field={field_id}")
    return _field_row_to_response(row)


async def soft_delete_field(
    conn: Connection, schema_id: str, entity_id: str, field_id: str
) -> None:
    await _assert_entity_active(conn, schema_id, entity_id)
    cur = await conn.execute(
        "UPDATE schema_fields SET lifecycle_state = 'deleted', updated_at = now() "
        "WHERE id = %s AND entity_id = %s AND lifecycle_state = 'active' "
        "RETURNING id",
        (field_id, entity_id),
    )
    if await cur.fetchone() is None:
        raise FieldNotFoundError(f"entity={entity_id} field={field_id}")


# ---------------------------------------------------------------------------
# Relationships — repo functions
# ---------------------------------------------------------------------------


_REL_COLS = (
    "id, name, from_entity_id, to_entity_id, kind, cardinality, "
    "cascade_delete, single_parent, lifecycle_state, created_at, updated_at"
)


def _rel_row_to_response(row: tuple) -> RelationshipResponse:
    return RelationshipResponse(
        id=str(row[0]),
        name=row[1],
        from_entity_id=str(row[2]),
        to_entity_id=str(row[3]),
        kind=row[4],
        cardinality=row[5],
        cascade_delete=row[6],
        single_parent=row[7],
        lifecycle_state=row[8],
        created_at=_iso(row[9]),
        updated_at=_iso(row[10]),
    )


async def _assert_entities_in_same_schema(
    conn: Connection, schema_id: str, from_id: str, to_id: str
) -> None:
    """§4.14 — both endpoints must reference active entities in this schema."""
    cur = await conn.execute(
        "SELECT count(*) FROM schema_entities "
        "WHERE id = ANY(%s) AND schema_id = %s AND lifecycle_state = 'active'",
        ([from_id, to_id], schema_id),
    )
    count = (await cur.fetchone())[0]
    # Two distinct UUIDs both in this schema → count=2. Self-references (from=to)
    # are allowed (e.g., a "references" edge from a type to itself), counted once.
    expected = 1 if from_id == to_id else 2
    if count != expected:
        raise InvalidCrossSchemaReferenceError(
            f"from={from_id} to={to_id} not both in schema={schema_id}"
        )


async def create_relationship(
    conn: Connection, workspace_id: str, schema_id: str, body: RelationshipCreate
) -> RelationshipResponse:
    import psycopg

    await _assert_entities_in_same_schema(
        conn, schema_id, body.from_entity_id, body.to_entity_id
    )

    try:
        cur = await conn.execute(
            f"INSERT INTO schema_relationships "
            f"(schema_id, workspace_id, name, from_entity_id, to_entity_id, "
            f" kind, cardinality, cascade_delete, single_parent) "
            f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
            f"RETURNING {_REL_COLS}",
            (schema_id, workspace_id, body.name, body.from_entity_id,
             body.to_entity_id, body.kind, body.cardinality,
             body.cascade_delete, body.single_parent),
        )
        row = await cur.fetchone()
    except psycopg.errors.UniqueViolation as exc:
        raise RelationshipNameConflictError(body.name) from exc

    return _rel_row_to_response(row)


async def list_relationships(
    conn: Connection, schema_id: str, limit: int, offset: int
) -> RelationshipListResponse:
    cur = await conn.execute(
        f"SELECT {_REL_COLS} FROM schema_relationships "
        f"WHERE schema_id = %s AND lifecycle_state = 'active' "
        f"ORDER BY created_at DESC, id DESC "
        f"LIMIT %s OFFSET %s",
        (schema_id, limit, offset),
    )
    rows = await cur.fetchall()

    cur = await conn.execute(
        "SELECT count(*) FROM schema_relationships "
        "WHERE schema_id = %s AND lifecycle_state = 'active'",
        (schema_id,),
    )
    total = (await cur.fetchone())[0]

    return RelationshipListResponse(
        items=[_rel_row_to_response(r) for r in rows],
        total=total, limit=limit, offset=offset,
    )


async def soft_delete_relationship(
    conn: Connection, schema_id: str, rel_id: str
) -> None:
    cur = await conn.execute(
        "UPDATE schema_relationships SET lifecycle_state = 'deleted', updated_at = now() "
        "WHERE id = %s AND schema_id = %s AND lifecycle_state = 'active' "
        "RETURNING id",
        (rel_id, schema_id),
    )
    if await cur.fetchone() is None:
        raise RelationshipNotFoundError(f"schema={schema_id} relationship={rel_id}")


# ---------------------------------------------------------------------------
# Subtree snapshot builder — the canonical body for a schema_versions row
# ---------------------------------------------------------------------------


async def build_subtree_snapshot(
    conn: Connection, schema_id: str
) -> dict[str, Any]:
    """Build the {name, description, entities[], relationships[]} snapshot
    for the current state of a schema.

    Items in each array are sorted by name (deterministic snapshots).
    Relationship cross-refs use entity NAMES (not UUIDs) per §4.1 #5 so a
    rollback that re-creates entities re-binds correctly.
    """
    # Schema head
    cur = await conn.execute(
        "SELECT name, description FROM schemas "
        "WHERE id = %s AND lifecycle_state = 'active'",
        (schema_id,),
    )
    schema_row = await cur.fetchone()
    if schema_row is None:
        # The caller is responsible for the 404; this is a defensive empty
        # snapshot (used during rollback we read the OLD body, not this).
        return {"name": "", "description": "", "entities": [], "relationships": []}

    name, description = schema_row

    # Entities + their fields
    cur = await conn.execute(
        "SELECT id, name, description FROM schema_entities "
        "WHERE schema_id = %s AND lifecycle_state = 'active' ORDER BY name",
        (schema_id,),
    )
    entity_rows = await cur.fetchall()

    entities: list[dict[str, Any]] = []
    entity_id_to_name: dict[str, str] = {}
    for e_row in entity_rows:
        eid, e_name, e_desc = str(e_row[0]), e_row[1], e_row[2]
        entity_id_to_name[eid] = e_name

        cur = await conn.execute(
            "SELECT name, type, nl_description, is_required FROM schema_fields "
            "WHERE entity_id = %s AND lifecycle_state = 'active' ORDER BY name",
            (eid,),
        )
        field_rows = await cur.fetchall()
        fields = [
            {
                "name": f_row[0],
                "type": f_row[1],
                "nl_description": f_row[2],
                "is_required": f_row[3],
            }
            for f_row in field_rows
        ]
        entities.append({
            "name": e_name,
            "description": e_desc,
            "fields": fields,
        })

    # Relationships — resolve from/to to entity NAMES (decision #5/§4.1 #5)
    cur = await conn.execute(
        "SELECT name, from_entity_id, to_entity_id, kind, cardinality, "
        "       cascade_delete, single_parent "
        "FROM schema_relationships "
        "WHERE schema_id = %s AND lifecycle_state = 'active' ORDER BY name",
        (schema_id,),
    )
    rel_rows = await cur.fetchall()
    relationships = [
        {
            "name": r_row[0],
            "from": entity_id_to_name.get(str(r_row[1]), ""),
            "to": entity_id_to_name.get(str(r_row[2]), ""),
            "kind": r_row[3],
            "cardinality": r_row[4],
            "cascade_delete": r_row[5],
            "single_parent": r_row[6],
        }
        for r_row in rel_rows
    ]

    return {
        "name": name,
        "description": description,
        "entities": entities,
        "relationships": relationships,
    }


# ---------------------------------------------------------------------------
# Subtree restorer — used by rollback
# ---------------------------------------------------------------------------


async def restore_subtree(
    conn: Connection,
    workspace_id: str,
    schema_id: str,
    snapshot: dict[str, Any],
) -> None:
    """Reconcile current schema state to match the snapshot.

    By NAME, not by UUID — preserves UUIDs of rows that exist in both the
    snapshot and the current state (so a rollback that doesn't actually
    change those rows leaves them untouched).

    Per §4.1 #5: relationships reference entities by name in the snapshot;
    we resolve them to (possibly preserved, possibly newly-created) entity
    UUIDs at restore time.

    Called by `kb.domain.schemas.rollback_to_version` at Phase 1c.
    Does NOT touch the schemas row itself (caller updates name/description).
    """
    snap_entities_by_name: dict[str, dict[str, Any]] = {
        e["name"]: e for e in snapshot.get("entities", [])
    }
    snap_rels_by_name: dict[str, dict[str, Any]] = {
        r["name"]: r for r in snapshot.get("relationships", [])
    }

    # --- Entities -----------------------------------------------------------

    cur = await conn.execute(
        "SELECT id, name FROM schema_entities "
        "WHERE schema_id = %s AND lifecycle_state = 'active'",
        (schema_id,),
    )
    current_entities: dict[str, str] = {
        row[1]: str(row[0]) for row in await cur.fetchall()
    }

    # Soft-delete entities (with cascade to their fields + relationships)
    # that are no longer in the snapshot.
    for ent_name, ent_id in current_entities.items():
        if ent_name not in snap_entities_by_name:
            # Cascade-soft-delete the entity's fields
            await conn.execute(
                "UPDATE schema_fields SET lifecycle_state = 'deleted', updated_at = now() "
                "WHERE entity_id = %s AND lifecycle_state = 'active'",
                (ent_id,),
            )
            # Cascade-soft-delete any relationships referencing this entity
            await conn.execute(
                "UPDATE schema_relationships SET lifecycle_state = 'deleted', updated_at = now() "
                "WHERE (from_entity_id = %s OR to_entity_id = %s) "
                "  AND schema_id = %s AND lifecycle_state = 'active'",
                (ent_id, ent_id, schema_id),
            )
            await conn.execute(
                "UPDATE schema_entities SET lifecycle_state = 'deleted', updated_at = now() "
                "WHERE id = %s",
                (ent_id,),
            )

    # Build name → UUID for the final state (preserved + newly-created entities).
    name_to_id: dict[str, str] = {}
    for ent_name, ent_snap in snap_entities_by_name.items():
        if ent_name in current_entities:
            # Preserve UUID; update description if needed.
            eid = current_entities[ent_name]
            await conn.execute(
                "UPDATE schema_entities "
                "SET description = %s, updated_at = now() WHERE id = %s",
                (ent_snap.get("description", ""), eid),
            )
            name_to_id[ent_name] = eid
        else:
            cur = await conn.execute(
                "INSERT INTO schema_entities "
                "(schema_id, workspace_id, name, description) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (schema_id, workspace_id, ent_name,
                 ent_snap.get("description", "")),
            )
            new_eid = str((await cur.fetchone())[0])
            name_to_id[ent_name] = new_eid

    # --- Fields (per entity) ------------------------------------------------

    for ent_name, ent_snap in snap_entities_by_name.items():
        eid = name_to_id[ent_name]
        snap_fields_by_name: dict[str, dict[str, Any]] = {
            f["name"]: f for f in ent_snap.get("fields", [])
        }

        cur = await conn.execute(
            "SELECT id, name FROM schema_fields "
            "WHERE entity_id = %s AND lifecycle_state = 'active'",
            (eid,),
        )
        current_fields: dict[str, str] = {
            row[1]: str(row[0]) for row in await cur.fetchall()
        }

        # Soft-delete fields no longer in snapshot
        for f_name, f_id in current_fields.items():
            if f_name not in snap_fields_by_name:
                await conn.execute(
                    "UPDATE schema_fields "
                    "SET lifecycle_state = 'deleted', updated_at = now() "
                    "WHERE id = %s",
                    (f_id,),
                )

        # Update existing fields + create missing ones
        for f_name, f_snap in snap_fields_by_name.items():
            if f_name in current_fields:
                await conn.execute(
                    "UPDATE schema_fields "
                    "SET type = %s, nl_description = %s, is_required = %s, "
                    "    updated_at = now() WHERE id = %s",
                    (f_snap["type"], f_snap.get("nl_description", ""),
                     f_snap.get("is_required", False), current_fields[f_name]),
                )
            else:
                await conn.execute(
                    "INSERT INTO schema_fields "
                    "(entity_id, workspace_id, name, type, nl_description, is_required) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (eid, workspace_id, f_name, f_snap["type"],
                     f_snap.get("nl_description", ""),
                     f_snap.get("is_required", False)),
                )

    # --- Relationships ------------------------------------------------------

    cur = await conn.execute(
        "SELECT id, name FROM schema_relationships "
        "WHERE schema_id = %s AND lifecycle_state = 'active'",
        (schema_id,),
    )
    current_rels: dict[str, str] = {
        row[1]: str(row[0]) for row in await cur.fetchall()
    }

    # Soft-delete relationships no longer in snapshot
    for r_name, r_id in current_rels.items():
        if r_name not in snap_rels_by_name:
            await conn.execute(
                "UPDATE schema_relationships "
                "SET lifecycle_state = 'deleted', updated_at = now() "
                "WHERE id = %s",
                (r_id,),
            )

    # Update existing relationships + create missing ones (resolve from/to by name)
    for r_name, r_snap in snap_rels_by_name.items():
        from_id = name_to_id.get(r_snap.get("from", ""))
        to_id = name_to_id.get(r_snap.get("to", ""))
        if from_id is None or to_id is None:
            # Snapshot references an entity that's no longer resolvable —
            # skip rather than fail (permissive on snapshot data).
            continue
        if r_name in current_rels:
            await conn.execute(
                "UPDATE schema_relationships "
                "SET from_entity_id = %s, to_entity_id = %s, kind = %s, "
                "    cardinality = %s, cascade_delete = %s, single_parent = %s, "
                "    updated_at = now() WHERE id = %s",
                (from_id, to_id, r_snap["kind"],
                 r_snap.get("cardinality", "one_to_many"),
                 r_snap.get("cascade_delete", False),
                 r_snap.get("single_parent", True),
                 current_rels[r_name]),
            )
        else:
            await conn.execute(
                "INSERT INTO schema_relationships "
                "(schema_id, workspace_id, name, from_entity_id, to_entity_id, "
                " kind, cardinality, cascade_delete, single_parent) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (schema_id, workspace_id, r_name, from_id, to_id,
                 r_snap["kind"], r_snap.get("cardinality", "one_to_many"),
                 r_snap.get("cascade_delete", False),
                 r_snap.get("single_parent", True)),
            )
