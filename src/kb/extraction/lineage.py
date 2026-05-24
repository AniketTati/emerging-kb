"""Phase 6 — lineage path assignment (Design 7 ltree).

Per build_tracker §5.13 decision #7.

For each extracted_entity, walk `schema_relationships.kind='contains'` to
find its parent schema_entity. Look up the most-recently-created
extracted_entity in the SAME file whose schema_entity_id matches the
relationship's `from_entity_id`. Compute lineage_path as ltree:

    lineage_path = parent.lineage_path || self.id

If no parent found (root entity), lineage_path = self_id (single-segment).

Wave A simplification: most-recently-created parent in same file. Wave B /
Phase 7 add proper resolution via identity-resolved entities.

ltree label format: uuids have hyphens which aren't valid ltree label chars.
Per Postgres docs: ltree labels must match `[A-Za-z0-9_]+` and be ≤ 1000
chars. We convert UUIDs to underscored form (`_` instead of `-`) for ltree;
the underscored form maps 1:1 back to UUID for query result rendering.
"""

from __future__ import annotations

from typing import Any

from kb.db.pool import Connection


def uuid_to_label(uuid: str) -> str:
    """Convert a UUID string to an ltree-safe label (replace hyphens with underscores)."""
    return uuid.replace("-", "_")


def label_to_uuid(label: str) -> str:
    """Inverse of uuid_to_label."""
    return label.replace("_", "-")


async def find_parent_schema_entity_id(
    conn: Connection,
    *,
    workspace_id: str,
    child_schema_entity_id: str,
) -> str | None:
    """Return the schema_entity_id of the parent (via kind='contains'), or
    None if no contains-relationship targets the given schema_entity."""
    cur = await conn.execute(
        "SELECT from_entity_id::text FROM schema_relationships "
        "WHERE to_entity_id = %s AND workspace_id = %s "
        "AND kind = 'contains' AND lifecycle_state = 'active' "
        "LIMIT 1",
        (child_schema_entity_id, workspace_id),
    )
    row = await cur.fetchone()
    return row[0] if row else None


async def find_parent_extracted_entity_id(
    conn: Connection,
    *,
    workspace_id: str,
    file_id: str,
    parent_schema_entity_id: str,
) -> str | None:
    """Find the most-recently-created extracted_entity in the same file with
    the given parent schema_entity_id."""
    cur = await conn.execute(
        "SELECT id::text FROM extracted_entities "
        "WHERE file_id = %s AND workspace_id = %s "
        "AND schema_entity_id = %s "
        "ORDER BY created_at DESC LIMIT 1",
        (file_id, workspace_id, parent_schema_entity_id),
    )
    row = await cur.fetchone()
    return row[0] if row else None


def compute_lineage_path(
    *,
    entity_id: str,
    parent_lineage_path: str | None,
) -> str:
    """Compose lineage_path = parent_lineage_path || uuid_to_label(entity_id).

    If parent_lineage_path is None (root), returns the single label.
    """
    self_label = uuid_to_label(entity_id)
    if parent_lineage_path:
        return f"{parent_lineage_path}.{self_label}"
    return self_label


async def assign_lineage_for_entity(
    conn: Connection,
    *,
    workspace_id: str,
    file_id: str,
    entity_id: str,
    schema_entity_id: str,
) -> tuple[str | None, str]:
    """Resolve parent (if any) + compute lineage_path. Returns
    (parent_entity_id, lineage_path).

    Pure helper — caller UPDATEs the row with the returned values.
    """
    parent_schema_entity_id = await find_parent_schema_entity_id(
        conn,
        workspace_id=workspace_id,
        child_schema_entity_id=schema_entity_id,
    )
    parent_entity_id: str | None = None
    parent_lineage: str | None = None
    if parent_schema_entity_id is not None:
        parent_entity_id = await find_parent_extracted_entity_id(
            conn,
            workspace_id=workspace_id,
            file_id=file_id,
            parent_schema_entity_id=parent_schema_entity_id,
        )
        if parent_entity_id is not None:
            cur = await conn.execute(
                "SELECT lineage_path::text FROM extracted_entities WHERE id = %s",
                (parent_entity_id,),
            )
            r = await cur.fetchone()
            parent_lineage = r[0] if r and r[0] else None

    return parent_entity_id, compute_lineage_path(
        entity_id=entity_id, parent_lineage_path=parent_lineage,
    )
