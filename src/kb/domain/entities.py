"""Phase 7 — entities + mention_to_entity repo."""

from __future__ import annotations

from typing import Any

from kb.db.pool import Connection


# ---------------------------------------------------------------------------
# entities
# ---------------------------------------------------------------------------


async def find_entity_deterministic(
    conn: Connection, *, workspace_id: str, name: str, entity_type: str,
) -> str | None:
    """Stage 1: exact lowercased name + type match."""
    cur = await conn.execute(
        "SELECT id::text FROM entities "
        "WHERE workspace_id = %s AND lower(canonical_name) = lower(%s) "
        "AND entity_type = %s LIMIT 1",
        (workspace_id, name, entity_type),
    )
    row = await cur.fetchone()
    return row[0] if row else None


async def find_entity_by_embedding(
    conn: Connection,
    *,
    workspace_id: str,
    entity_type: str,
    embedding: list[float],
    limit: int = 5,
) -> list[tuple[str, str, float]]:
    """Stage 2: nearest-neighbor by cosine similarity. Returns
    [(entity_id, canonical_name, cosine_sim)] ordered desc.

    cosine_sim = 1 - cosine_distance (pgvector's <=> is cosine distance for
    halfvec_cosine_ops).
    """
    if not embedding:
        return []
    vec_literal = "[" + ",".join(repr(float(v)) for v in embedding) + "]"
    cur = await conn.execute(
        "SELECT id::text, canonical_name, "
        "(1.0 - (embedding <=> %s::halfvec))::float AS sim "
        "FROM entities "
        "WHERE workspace_id = %s AND entity_type = %s AND embedding IS NOT NULL "
        "ORDER BY embedding <=> %s::halfvec LIMIT %s",
        (vec_literal, workspace_id, entity_type, vec_literal, limit),
    )
    rows = await cur.fetchall()
    return [(r[0], r[1], float(r[2])) for r in rows]


async def insert_entity(
    conn: Connection,
    *,
    workspace_id: str,
    canonical_name: str,
    entity_type: str,
    embedding: list[float] | None = None,
) -> str:
    vec_literal = None
    if embedding:
        vec_literal = "[" + ",".join(repr(float(v)) for v in embedding) + "]"
    cur = await conn.execute(
        "INSERT INTO entities (workspace_id, canonical_name, entity_type, embedding) "
        "VALUES (%s, %s, %s, %s::halfvec) "
        "ON CONFLICT (workspace_id, lower(canonical_name), entity_type) "
        "DO UPDATE SET updated_at = now() "
        "RETURNING id::text",
        (workspace_id, canonical_name, entity_type, vec_literal),
    )
    return (await cur.fetchone())[0]


async def increment_mention_count(
    conn: Connection, *, entity_id: str, by: int = 1,
) -> None:
    await conn.execute(
        "UPDATE entities SET mention_count = mention_count + %s, "
        "updated_at = now() WHERE id = %s",
        (by, entity_id),
    )


# ---------------------------------------------------------------------------
# mention_to_entity
# ---------------------------------------------------------------------------


async def delete_mention_to_entity_for_file(
    conn: Connection, *, file_id: str,
) -> int:
    cur = await conn.execute(
        "DELETE FROM mention_to_entity "
        "WHERE mention_id IN (SELECT id FROM extracted_mentions WHERE file_id = %s)",
        (file_id,),
    )
    return cur.rowcount or 0


async def insert_mention_to_entity(
    conn: Connection,
    *,
    mention_id: str,
    entity_id: str,
    workspace_id: str,
    confidence: float,
    resolved_method: str,
) -> None:
    await conn.execute(
        "INSERT INTO mention_to_entity "
        "(mention_id, entity_id, workspace_id, confidence, resolved_method) "
        "VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (mention_id) DO UPDATE SET "
        "  entity_id = EXCLUDED.entity_id, "
        "  confidence = EXCLUDED.confidence, "
        "  resolved_method = EXCLUDED.resolved_method",
        (mention_id, entity_id, workspace_id, confidence, resolved_method),
    )


async def read_mentions_for_file(
    conn: Connection, *, file_id: str,
) -> list[tuple[str, str, str]]:
    """[(mention_id, mention_text, mention_type)] for a file."""
    cur = await conn.execute(
        "SELECT id::text, mention_text, mention_type FROM extracted_mentions "
        "WHERE file_id = %s ORDER BY created_at",
        (file_id,),
    )
    rows = await cur.fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


async def count_entities(conn: Connection, *, workspace_id: str) -> int:
    cur = await conn.execute(
        "SELECT count(*) FROM entities WHERE workspace_id = %s",
        (workspace_id,),
    )
    return (await cur.fetchone())[0]


async def count_mention_links(conn: Connection, *, workspace_id: str) -> int:
    cur = await conn.execute(
        "SELECT count(*) FROM mention_to_entity WHERE workspace_id = %s",
        (workspace_id,),
    )
    return (await cur.fetchone())[0]
