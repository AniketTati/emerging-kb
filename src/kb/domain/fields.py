"""Phase 5b — proposed_fields + inferred_schema_fields repo."""

from __future__ import annotations

from kb.db.pool import Connection


# ---------------------------------------------------------------------------
# proposed_fields — raw per-doc LLM output
# ---------------------------------------------------------------------------


async def delete_proposed_fields_for_file(
    conn: Connection, *, file_id: str
) -> int:
    cur = await conn.execute(
        "DELETE FROM proposed_fields WHERE file_id = %s", (file_id,),
    )
    return cur.rowcount or 0


async def insert_proposed_field(
    conn: Connection,
    *,
    file_id: str,
    workspace_id: str,
    inferred_doc_type: str | None,
    field_name: str,
    field_description: str,
    value_text: str | None,
    value_type: str,
    is_pii: bool,
    model_id: str,
    source_chunk_id: str | None = None,
    source_char_start: int | None = None,
    source_char_end: int | None = None,
) -> str:
    cur = await conn.execute(
        "INSERT INTO proposed_fields "
        "(file_id, workspace_id, inferred_doc_type, field_name, field_description, "
        "value_text, value_type, is_pii, model_id, "
        "source_chunk_id, source_char_start, source_char_end) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "RETURNING id::text",
        (
            file_id, workspace_id, inferred_doc_type, field_name, field_description,
            value_text, value_type, is_pii, model_id,
            source_chunk_id, source_char_start, source_char_end,
        ),
    )
    return (await cur.fetchone())[0]


async def read_proposed_fields_for_doctype(
    conn: Connection,
    *,
    workspace_id: str,
    inferred_doc_type: str,
) -> dict[str, list[dict]]:
    """Return {file_id: [proposed_field_dict, ...]} for all docs of this type
    in the workspace. Used by cross-doc clustering."""
    cur = await conn.execute(
        "SELECT file_id::text, field_name, field_description, value_type "
        "FROM proposed_fields "
        "WHERE workspace_id = %s AND inferred_doc_type = %s",
        (workspace_id, inferred_doc_type),
    )
    rows = await cur.fetchall()
    by_file: dict[str, list[dict]] = {}
    for fid, name, desc, vt in rows:
        by_file.setdefault(fid, []).append({
            "field_name": name,
            "field_description": desc,
            "value_type": vt,
        })
    return by_file


async def count_docs_of_doctype(
    conn: Connection,
    *,
    workspace_id: str,
    inferred_doc_type: str,
) -> int:
    """Count files in workspace whose inferred_doc_type matches.
    Used as the denominator for prevalence."""
    cur = await conn.execute(
        "SELECT count(*) FROM files "
        "WHERE workspace_id = %s AND inferred_doc_type = %s "
        "AND lifecycle_state NOT IN ('deleted','failed')",
        (workspace_id, inferred_doc_type),
    )
    row = await cur.fetchone()
    return row[0] if row else 0


# ---------------------------------------------------------------------------
# inferred_schema_fields — clustered + promotion-ready
# ---------------------------------------------------------------------------


async def upsert_inferred_schema_field(
    conn: Connection,
    *,
    workspace_id: str,
    inferred_doc_type: str,
    canonical_name: str,
    description: str,
    value_type: str,
    n_docs_observed: int,
    prevalence: float,
    stability: float,
    value_type_confidence: float,
) -> str:
    """UPSERT on (workspace_id, inferred_doc_type, canonical_name).
    Returns the inferred_schema_fields.id."""
    cur = await conn.execute(
        "INSERT INTO inferred_schema_fields "
        "(workspace_id, inferred_doc_type, canonical_name, description, value_type, "
        "n_docs_observed, prevalence, stability, value_type_confidence) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (workspace_id, inferred_doc_type, canonical_name) "
        "DO UPDATE SET "
        "  description = EXCLUDED.description, "
        "  value_type = EXCLUDED.value_type, "
        "  n_docs_observed = EXCLUDED.n_docs_observed, "
        "  prevalence = EXCLUDED.prevalence, "
        "  stability = EXCLUDED.stability, "
        "  value_type_confidence = EXCLUDED.value_type_confidence, "
        "  updated_at = now() "
        "RETURNING id::text",
        (
            workspace_id, inferred_doc_type, canonical_name, description, value_type,
            n_docs_observed, prevalence, stability, value_type_confidence,
        ),
    )
    return (await cur.fetchone())[0]


async def mark_inferred_field_promoted(
    conn: Connection,
    *,
    inferred_field_id: str,
    promoted_schema_field_id: str,
) -> None:
    await conn.execute(
        "UPDATE inferred_schema_fields "
        "SET is_promoted = true, promoted_at = now(), promoted_schema_field_id = %s "
        "WHERE id = %s",
        (promoted_schema_field_id, inferred_field_id),
    )


async def count_proposed_fields_for_file(
    conn: Connection, *, file_id: str,
) -> int:
    cur = await conn.execute(
        "SELECT count(*) FROM proposed_fields WHERE file_id = %s", (file_id,),
    )
    return (await cur.fetchone())[0]


async def update_file_inferred_doc_type(
    conn: Connection, *, file_id: str, doc_type: str,
) -> None:
    await conn.execute(
        "UPDATE files SET inferred_doc_type = %s, updated_at = now() WHERE id = %s",
        (doc_type, file_id),
    )
