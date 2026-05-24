"""Phase 6 — extracted_entities repo."""

from __future__ import annotations

import json
from typing import Any

from kb.db.pool import Connection


async def delete_extracted_entities_for_file(
    conn: Connection, *, file_id: str,
) -> int:
    cur = await conn.execute(
        "DELETE FROM extracted_entities WHERE file_id = %s", (file_id,),
    )
    return cur.rowcount or 0


async def insert_extracted_entity(
    conn: Connection,
    *,
    schema_entity_id: str,
    file_id: str,
    workspace_id: str,
    fields: dict[str, Any],
    citations: dict[str, str],  # field_name → contextual_chunk_id (str uuid)
    model_id: str,
) -> str:
    """INSERT one extracted_entities row (without lineage_path — set later
    via update_lineage). Returns the new row's id."""
    cur = await conn.execute(
        "INSERT INTO extracted_entities "
        "(schema_entity_id, file_id, workspace_id, fields, citations, model_id) "
        "VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s) "
        "RETURNING id::text",
        (
            schema_entity_id, file_id, workspace_id,
            json.dumps(fields), json.dumps(citations), model_id,
        ),
    )
    return (await cur.fetchone())[0]


async def update_lineage(
    conn: Connection,
    *,
    entity_id: str,
    parent_entity_id: str | None,
    lineage_path: str,
) -> None:
    """UPDATE parent_entity_id + lineage_path (the only mutable columns per
    the 0017 GRANT)."""
    await conn.execute(
        "UPDATE extracted_entities "
        "SET parent_entity_id = %s, lineage_path = %s::ltree "
        "WHERE id = %s",
        (parent_entity_id, lineage_path, entity_id),
    )


async def read_active_schemas_for_doctype(
    conn: Connection,
    *,
    workspace_id: str,
    inferred_doc_type: str,
) -> list[tuple[str, str]]:
    """Return [(schema_id, schema_name)] for active schemas in this workspace
    whose name matches `auto:<inferred_doc_type>` OR is a user-created schema
    (we accept ALL active schemas in the workspace as candidates — schema
    routing is a Wave A simplification; Phase 7+ adds doc-type↔schema mapping).
    """
    cur = await conn.execute(
        "SELECT id::text, name FROM schemas "
        "WHERE workspace_id = %s AND lifecycle_state = 'active' "
        "AND (name = %s OR name NOT LIKE 'auto:%%')",
        (workspace_id, f"auto:{inferred_doc_type}"),
    )
    return [(r[0], r[1]) for r in await cur.fetchall()]


async def read_schema_entities_with_fields(
    conn: Connection,
    *,
    schema_id: str,
) -> list[dict[str, Any]]:
    """Return [{entity_id, entity_name, entity_description, field_defs}].

    field_defs = [{name, type, nl_description}] for each active schema_field
    under each active schema_entity."""
    cur = await conn.execute(
        "SELECT id::text, name, description FROM schema_entities "
        "WHERE schema_id = %s AND lifecycle_state = 'active' "
        "ORDER BY created_at",
        (schema_id,),
    )
    entities = [
        {"entity_id": r[0], "entity_name": r[1], "entity_description": r[2]}
        for r in await cur.fetchall()
    ]
    for e in entities:
        cur = await conn.execute(
            "SELECT name, type, nl_description FROM schema_fields "
            "WHERE entity_id = %s AND lifecycle_state = 'active' "
            "ORDER BY created_at",
            (e["entity_id"],),
        )
        e["field_defs"] = [
            {"name": r[0], "type": r[1], "nl_description": r[2]}
            for r in await cur.fetchall()
        ]
    return [e for e in entities if e["field_defs"]]


async def read_contextual_chunks_for_extraction(
    conn: Connection, *, file_id: str,
) -> list[tuple[str, str]]:
    """[(contextual_chunk_id, contextual_text)] in chunk_index order."""
    cur = await conn.execute(
        "SELECT cc.id::text, cc.contextual_text "
        "FROM contextual_chunks cc JOIN chunks c ON c.id = cc.chunk_id "
        "WHERE cc.file_id = %s ORDER BY c.chunk_index ASC",
        (file_id,),
    )
    return [(r[0], r[1]) for r in await cur.fetchall()]


async def count_extracted_entities_for_file(
    conn: Connection, *, file_id: str,
) -> int:
    cur = await conn.execute(
        "SELECT count(*) FROM extracted_entities WHERE file_id = %s", (file_id,),
    )
    return (await cur.fetchone())[0]
