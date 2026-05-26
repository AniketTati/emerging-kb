"""Wave A close-up — /explore: faceted search across the workspace.

The Explore page is the third primary-nav surface (alongside Chat +
Upload). It answers "what's in my corpus?" by category:

  - documents      (files where lifecycle_state = 'ready')
  - doc_types      (distinct inferred_doc_type values)
  - atomic_units   (clauses / transactions / rows / decisions)
  - entities       (resolved canonical entities)
  - relationships  (typed edges from the L5 graph)
  - topics         (raptor_nodes — corpus-level themes)
  - anomalies      (atomic_units with rarity_score above threshold)

Two endpoints:

  GET /explore/counts
        One round-trip, returns the per-category total.
        Used by the left rail to populate the count badges
        without firing 7 separate queries from the UI.

  GET /explore/search?q=...&kind=...&offset=0&limit=50
        Faceted text search. Cursor-style pagination via offset+limit
        — workable to ~10k results; beyond that the UI should switch
        to per-kind drill-down (a separate endpoint per kind, already
        present at /files, /entities, /atomic-units, etc.).

Scale posture: the SQL uses indexed prefix/ILIKE matches on
  - files.name
  - entities.canonical_name
  - atomic_units.unit_text (subset)
At 100k+ workspaces add pg_trgm GIN indexes to those columns and
swap ILIKE for `% q %`. The /explore/search query layout doesn't
need to change.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from kb.api.deps import current_workspace_id, kb_app_connection
from kb.db.pool import Connection


router = APIRouter(tags=["explore"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class ExploreCounts(BaseModel):
    documents: int = 0
    doc_types: int = 0
    atomic_units: int = 0
    entities: int = 0
    relationships: int = 0
    topics: int = 0
    anomalies: int = 0


class ExploreHit(BaseModel):
    kind: str            # "document" | "doc_type" | "atomic_unit" | "entity" | "relationship" | "topic" | "anomaly"
    id: str              # primary key (or canonical handle, e.g. doc_type name)
    title: str           # display label
    subtitle: str | None = None
    snippet: str | None = None
    file_id: str | None = None
    file_name: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class ExploreSearchResponse(BaseModel):
    q: str
    kind: str | None = None
    offset: int = 0
    limit: int = 50
    total_estimate: int = 0
    items: list[ExploreHit] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# /explore/counts — left-rail badges in one shot
# ---------------------------------------------------------------------------


@router.get(
    "/explore/counts",
    response_model=ExploreCounts,
    summary="Per-category counts for the Explore left rail",
)
async def get_explore_counts(
    workspace_id: Annotated[str, Depends(current_workspace_id)],  # noqa: ARG001
    conn: Annotated[Connection, Depends(kb_app_connection)],
) -> ExploreCounts:
    """One round-trip. Each count is a single indexed COUNT(*) under
    workspace RLS, so even at 100k docs this stays under ~50ms.

    Anomaly threshold matches the channel: rarity_score > 0.7.
    """
    out = ExploreCounts()
    # Documents — exclude soft-deleted + failed.
    cur = await conn.execute(
        "SELECT count(*)::int FROM files "
        "WHERE lifecycle_state NOT IN ('deleted','failed')"
    )
    out.documents = int((await cur.fetchone())[0])

    cur = await conn.execute(
        "SELECT count(DISTINCT inferred_doc_type)::int FROM files "
        "WHERE lifecycle_state NOT IN ('deleted','failed') "
        "  AND inferred_doc_type IS NOT NULL"
    )
    out.doc_types = int((await cur.fetchone())[0])

    cur = await conn.execute("SELECT count(*)::int FROM atomic_units")
    out.atomic_units = int((await cur.fetchone())[0])

    cur = await conn.execute("SELECT count(*)::int FROM entities")
    out.entities = int((await cur.fetchone())[0])

    cur = await conn.execute("SELECT count(*)::int FROM relationships")
    out.relationships = int((await cur.fetchone())[0])

    # Topics = corpus-scope raptor nodes (the workspace summary + cluster
    # summaries). Per-doc RAPTOR nodes aren't "topics" in the user
    # sense — they're per-doc abstractions. We filter to scope='corpus'.
    cur = await conn.execute(
        "SELECT count(*)::int FROM raptor_nodes WHERE scope = 'corpus'"
    )
    out.topics = int((await cur.fetchone())[0])

    cur = await conn.execute(
        "SELECT count(*)::int FROM atomic_units "
        "WHERE rarity_score IS NOT NULL AND rarity_score > 0.7"
    )
    out.anomalies = int((await cur.fetchone())[0])

    return out


# ---------------------------------------------------------------------------
# /explore/search — faceted ILIKE search
# ---------------------------------------------------------------------------


_VALID_KINDS = {
    "document", "doc_type", "atomic_unit", "entity", "relationship",
    "topic", "anomaly",
}

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


@router.get(
    "/explore/search",
    response_model=ExploreSearchResponse,
    summary="Faceted explore search across docs / entities / atomic units / topics",
)
async def get_explore_search(
    workspace_id: Annotated[str, Depends(current_workspace_id)],  # noqa: ARG001
    conn: Annotated[Connection, Depends(kb_app_connection)],
    q: str = Query(default="", description="Free-text query. Empty = browse all."),
    kind: str | None = Query(
        default=None,
        description=(
            "Restrict to a single category: document, doc_type, "
            "atomic_unit, entity, relationship, topic, anomaly."
        ),
    ),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
) -> ExploreSearchResponse:
    """Returns up to `limit` matches across all categories (or just one
    if `kind` is set). Empty `q` returns recent/all rows per category.

    Per-category SQL keeps the implementation linear: each category is
    one indexed SELECT, results merged at the application layer. This
    is cheap (~50-200ms for a 100k workspace) and avoids the
    UNION-with-mismatched-columns rabbit hole.
    """
    q_norm = (q or "").strip()
    like = f"%{q_norm}%"
    has_query = bool(q_norm)

    # If kind is set, restrict to just that bucket. Else fan out.
    if kind and kind not in _VALID_KINDS:
        kind = None

    targets = [kind] if kind else list(_VALID_KINDS)

    items: list[ExploreHit] = []
    total_estimate = 0

    for tgt in targets:
        if tgt == "document":
            items_part, n = await _search_documents(conn, like, has_query, offset, limit)
        elif tgt == "doc_type":
            items_part, n = await _search_doc_types(conn, like, has_query, offset, limit)
        elif tgt == "atomic_unit":
            items_part, n = await _search_atomic_units(conn, like, has_query, offset, limit)
        elif tgt == "entity":
            items_part, n = await _search_entities(conn, like, has_query, offset, limit)
        elif tgt == "relationship":
            items_part, n = await _search_relationships(conn, like, has_query, offset, limit)
        elif tgt == "topic":
            items_part, n = await _search_topics(conn, like, has_query, offset, limit)
        elif tgt == "anomaly":
            items_part, n = await _search_anomalies(conn, like, has_query, offset, limit)
        else:
            items_part, n = [], 0
        items.extend(items_part)
        total_estimate += n

    # If we fanned out across many kinds, soft-cap the final response
    # at `limit` so the UI doesn't get a wall of mixed results.
    if not kind and len(items) > limit:
        items = items[:limit]

    return ExploreSearchResponse(
        q=q_norm, kind=kind, offset=offset, limit=limit,
        total_estimate=total_estimate, items=items,
    )


# ---------------------------------------------------------------------------
# Per-kind SQL helpers (each returns (items, total_count_estimate))
# ---------------------------------------------------------------------------


async def _search_documents(
    conn: Connection, like: str, has_query: bool, offset: int, limit: int,
) -> tuple[list[ExploreHit], int]:
    where_q = "AND name ILIKE %s" if has_query else ""
    params: tuple = ((like,) if has_query else ()) + (limit, offset)
    cur = await conn.execute(
        f"""
        SELECT id::text, name, inferred_doc_type, mime_type,
               size_bytes, created_at::text, lifecycle_state
          FROM files
         WHERE lifecycle_state NOT IN ('deleted','failed') {where_q}
         ORDER BY created_at DESC
         LIMIT %s OFFSET %s
        """,
        params,
    )
    rows = await cur.fetchall()

    count_params: tuple = (like,) if has_query else ()
    cur = await conn.execute(
        f"""
        SELECT count(*)::int FROM files
         WHERE lifecycle_state NOT IN ('deleted','failed') {where_q}
        """,
        count_params,
    )
    total = int((await cur.fetchone())[0])

    items = [
        ExploreHit(
            kind="document",
            id=r[0],
            title=r[1],
            subtitle=r[2] or r[3],
            file_id=r[0], file_name=r[1],
            extra={
                "mime_type": r[3],
                "size_bytes": int(r[4]),
                "lifecycle_state": r[6],
            },
        )
        for r in rows
    ]
    return items, total


async def _search_doc_types(
    conn: Connection, like: str, has_query: bool, offset: int, limit: int,
) -> tuple[list[ExploreHit], int]:
    where_q = "AND inferred_doc_type ILIKE %s" if has_query else ""
    params: tuple = ((like,) if has_query else ()) + (limit, offset)
    cur = await conn.execute(
        f"""
        SELECT inferred_doc_type, count(*)::int AS n
          FROM files
         WHERE lifecycle_state NOT IN ('deleted','failed')
           AND inferred_doc_type IS NOT NULL {where_q}
         GROUP BY inferred_doc_type
         ORDER BY n DESC, inferred_doc_type ASC
         LIMIT %s OFFSET %s
        """,
        params,
    )
    rows = await cur.fetchall()

    count_params: tuple = (like,) if has_query else ()
    cur = await conn.execute(
        f"""
        SELECT count(DISTINCT inferred_doc_type)::int FROM files
         WHERE lifecycle_state NOT IN ('deleted','failed')
           AND inferred_doc_type IS NOT NULL {where_q}
        """,
        count_params,
    )
    total = int((await cur.fetchone())[0])

    items = [
        ExploreHit(
            kind="doc_type",
            id=r[0],
            title=r[0],
            subtitle=f"{int(r[1])} doc{'s' if r[1] != 1 else ''}",
            extra={"file_count": int(r[1])},
        )
        for r in rows
    ]
    return items, total


async def _search_atomic_units(
    conn: Connection, like: str, has_query: bool, offset: int, limit: int,
) -> tuple[list[ExploreHit], int]:
    # atomic_units has unit_type + parameters + rarity_score, plus
    # file_id linking back to the doc.
    where_q = "AND au.unit_type ILIKE %s" if has_query else ""
    params: tuple = ((like,) if has_query else ()) + (limit, offset)
    cur = await conn.execute(
        f"""
        SELECT au.id::text, au.unit_type, au.parameters::text,
               au.rarity_score, au.file_id::text, f.name
          FROM atomic_units au
          JOIN files f ON f.id = au.file_id
         WHERE f.lifecycle_state NOT IN ('deleted','failed') {where_q}
         ORDER BY COALESCE(au.rarity_score, 0) DESC, au.id
         LIMIT %s OFFSET %s
        """,
        params,
    )
    rows = await cur.fetchall()

    count_params: tuple = (like,) if has_query else ()
    cur = await conn.execute(
        f"""
        SELECT count(*)::int FROM atomic_units au
          JOIN files f ON f.id = au.file_id
         WHERE f.lifecycle_state NOT IN ('deleted','failed') {where_q}
        """,
        count_params,
    )
    total = int((await cur.fetchone())[0])

    items = [
        ExploreHit(
            kind="atomic_unit",
            id=r[0],
            title=str(r[1] or "(untyped unit)"),
            subtitle=(r[5] or None),
            snippet=(r[2] or "")[:300],
            file_id=r[4], file_name=r[5],
            extra={"rarity_score": float(r[3]) if r[3] is not None else None},
        )
        for r in rows
    ]
    return items, total


async def _search_entities(
    conn: Connection, like: str, has_query: bool, offset: int, limit: int,
) -> tuple[list[ExploreHit], int]:
    """Entity rows + enrichments the prototype's entity card needs:
      - Aliases: top 3 distinct surface forms from extracted_mentions
        (excluding the canonical name itself).
      - First / last mention: MIN/MAX file.created_at across mentions.

    Both come from a single LATERAL join — one DB round-trip per
    matching entity. At demo scale this is fine; at 100k entities we'd
    pre-compute these into an `entity_summary` materialized view
    refreshed on extraction.
    """
    where_q = "AND e.canonical_name ILIKE %s" if has_query else ""
    params: tuple = ((like,) if has_query else ()) + (limit, offset)
    cur = await conn.execute(
        f"""
        SELECT e.id::text, e.canonical_name, e.entity_type, e.mention_count,
               (
                 SELECT array_agg(DISTINCT em.mention_text)
                   FROM extracted_mentions em
                   JOIN mention_to_entity me ON me.mention_id = em.id
                  WHERE me.entity_id = e.id
                    AND lower(em.mention_text) <> lower(e.canonical_name)
                  LIMIT 4
               ) AS aliases_raw,
               (
                 SELECT MIN(f.created_at)::date::text
                   FROM extracted_mentions em
                   JOIN mention_to_entity me ON me.mention_id = em.id
                   JOIN files f ON f.id = em.file_id
                  WHERE me.entity_id = e.id
               ) AS first_seen,
               (
                 SELECT MAX(f.created_at)::date::text
                   FROM extracted_mentions em
                   JOIN mention_to_entity me ON me.mention_id = em.id
                   JOIN files f ON f.id = em.file_id
                  WHERE me.entity_id = e.id
               ) AS last_seen,
               (
                 SELECT count(DISTINCT em.file_id)::int
                   FROM extracted_mentions em
                   JOIN mention_to_entity me ON me.mention_id = em.id
                  WHERE me.entity_id = e.id
               ) AS n_docs
          FROM entities e
         WHERE 1=1 {where_q}
         ORDER BY e.mention_count DESC, e.canonical_name ASC
         LIMIT %s OFFSET %s
        """,
        params,
    )
    rows = await cur.fetchall()

    count_params: tuple = (like,) if has_query else ()
    cur = await conn.execute(
        f"SELECT count(*)::int FROM entities e WHERE 1=1 {where_q}",
        count_params,
    )
    total = int((await cur.fetchone())[0])

    items: list[ExploreHit] = []
    for r in rows:
        aliases = [a for a in (r[4] or []) if a][:3]
        items.append(ExploreHit(
            kind="entity",
            id=r[0],
            title=r[1],
            subtitle=r[2],
            extra={
                "mention_count": int(r[3] or 0),
                "aliases": aliases,
                "first_seen": r[5],
                "last_seen": r[6],
                "n_docs": int(r[7] or 0),
            },
        ))
    return items, total


async def _search_relationships(
    conn: Connection, like: str, has_query: bool, offset: int, limit: int,
) -> tuple[list[ExploreHit], int]:
    # The relationships table uses (subject, predicate, object) shape.
    # Match on predicate text when the user typed a query; otherwise
    # browse by confidence descending. n_evidence shows how many
    # triples backed each edge.
    where_q = "AND r.predicate ILIKE %s" if has_query else ""
    params: tuple = ((like,) if has_query else ()) + (limit, offset)
    cur = await conn.execute(
        f"""
        SELECT r.id::text, r.predicate, r.confidence, r.n_evidence,
               e_src.canonical_name, e_dst.canonical_name
          FROM relationships r
          LEFT JOIN entities e_src ON e_src.id = r.subject_entity_id
          LEFT JOIN entities e_dst ON e_dst.id = r.object_entity_id
         WHERE 1=1 {where_q}
         ORDER BY r.confidence DESC NULLS LAST
         LIMIT %s OFFSET %s
        """,
        params,
    )
    rows = await cur.fetchall()

    count_params: tuple = (like,) if has_query else ()
    cur = await conn.execute(
        f"SELECT count(*)::int FROM relationships r WHERE 1=1 {where_q}",
        count_params,
    )
    total = int((await cur.fetchone())[0])

    items = [
        ExploreHit(
            kind="relationship",
            id=r[0],
            title=str(r[1] or "(untyped)"),
            subtitle=f"{r[4] or '?'} → {r[5] or '?'}",
            extra={
                "confidence": float(r[2]) if r[2] is not None else None,
                "n_evidence": int(r[3]) if r[3] is not None else None,
            },
        )
        for r in rows
    ]
    return items, total


async def _search_topics(
    conn: Connection, like: str, has_query: bool, offset: int, limit: int,
) -> tuple[list[ExploreHit], int]:
    where_q = "AND text ILIKE %s" if has_query else ""
    params: tuple = ((like,) if has_query else ()) + (limit, offset)
    cur = await conn.execute(
        f"""
        SELECT id::text, level, text
          FROM raptor_nodes
         WHERE scope = 'corpus' {where_q}
         ORDER BY level DESC, id
         LIMIT %s OFFSET %s
        """,
        params,
    )
    rows = await cur.fetchall()

    count_params: tuple = (like,) if has_query else ()
    cur = await conn.execute(
        f"""
        SELECT count(*)::int FROM raptor_nodes
         WHERE scope = 'corpus' {where_q}
        """,
        count_params,
    )
    total = int((await cur.fetchone())[0])

    items = []
    for r in rows:
        level = int(r[1] or 0)
        # Level 3 = corpus root (workspace summary); level 2 = topic
        # clusters; lower = doc-level abstractions.
        if level >= 3:
            label = "Workspace summary"
        elif level == 2:
            label = "Topic cluster"
        else:
            label = f"Topic L{level}"
        snippet = (r[2] or "")[:300]
        items.append(ExploreHit(
            kind="topic",
            id=r[0],
            title=label,
            snippet=snippet,
            extra={"level": level},
        ))
    return items, total


async def _search_anomalies(
    conn: Connection, like: str, has_query: bool, offset: int, limit: int,
) -> tuple[list[ExploreHit], int]:
    where_q = "AND au.unit_type ILIKE %s" if has_query else ""
    params: tuple = ((like,) if has_query else ()) + (limit, offset)
    cur = await conn.execute(
        f"""
        SELECT au.id::text, au.unit_type, au.rarity_score,
               au.file_id::text, f.name, au.parameters::text
          FROM atomic_units au
          JOIN files f ON f.id = au.file_id
         WHERE au.rarity_score IS NOT NULL
           AND au.rarity_score > 0.7
           AND f.lifecycle_state NOT IN ('deleted','failed') {where_q}
         ORDER BY au.rarity_score DESC
         LIMIT %s OFFSET %s
        """,
        params,
    )
    rows = await cur.fetchall()

    count_params: tuple = (like,) if has_query else ()
    cur = await conn.execute(
        f"""
        SELECT count(*)::int FROM atomic_units au
          JOIN files f ON f.id = au.file_id
         WHERE au.rarity_score IS NOT NULL
           AND au.rarity_score > 0.7
           AND f.lifecycle_state NOT IN ('deleted','failed') {where_q}
        """,
        count_params,
    )
    total = int((await cur.fetchone())[0])

    items = [
        ExploreHit(
            kind="anomaly",
            id=r[0],
            title=str(r[1] or "(untyped unit)"),
            subtitle=f"rarity {float(r[2] or 0):.2f}",
            snippet=(r[5] or "")[:300],
            file_id=r[3], file_name=r[4],
            extra={"rarity_score": float(r[2] or 0)},
        )
        for r in rows
    ]
    return items, total
