"""B1 / WA-5 — graph_edges repo (arch §5 stage 17, HippoRAG-2 PPR-ready).

Adjacency table is derived from three sources:
  - relationships          edge_kind='relationship'
  - mention co-occurrence  edge_kind='co_mention'
  - lineage parent/child   edge_kind='lineage'

Each (workspace, src, dst, kind) is unique. Weight accumulates evidence;
source_refs jsonb keeps audit trail per supporting object.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable

from kb.db.pool import Connection


@dataclass(frozen=True)
class GraphEdgeRecord:
    id: str
    workspace_id: str
    src_entity_id: str
    dst_entity_id: str
    edge_kind: str
    weight: float
    source_refs: list[Any]
    created_at: str
    updated_at: str


EDGE_KINDS: tuple[str, ...] = ("relationship", "co_mention", "lineage")


_SELECT_COLS = (
    "id, workspace_id, src_entity_id, dst_entity_id, edge_kind, weight, "
    "source_refs, created_at, updated_at"
)


def _row(row: tuple) -> GraphEdgeRecord:
    return GraphEdgeRecord(
        id=str(row[0]),
        workspace_id=str(row[1]),
        src_entity_id=str(row[2]),
        dst_entity_id=str(row[3]),
        edge_kind=str(row[4]),
        weight=float(row[5]),
        source_refs=list(row[6] or []),
        created_at=row[7].isoformat() if hasattr(row[7], "isoformat") else str(row[7]),
        updated_at=row[8].isoformat() if hasattr(row[8], "isoformat") else str(row[8]),
    )


# ---------------------------------------------------------------------------
# Writes — UPSERT semantics
# ---------------------------------------------------------------------------


async def upsert_edge(
    conn: Connection,
    *,
    workspace_id: str,
    src_entity_id: str,
    dst_entity_id: str,
    edge_kind: str,
    weight_delta: float = 1.0,
    source_ref: dict[str, Any] | None = None,
) -> tuple[str, bool]:
    """Add or accumulate. Returns (edge_id, was_inserted).

    `weight_delta` is added to any existing weight (additive accumulation).
    `source_ref`, if supplied, is appended to source_refs jsonb array."""
    if edge_kind not in EDGE_KINDS:
        raise ValueError(f"edge_kind must be one of {EDGE_KINDS}, got {edge_kind!r}")
    if src_entity_id == dst_entity_id:
        raise ValueError("src and dst entity ids must differ (CHECK rejects)")

    ref_json = json.dumps([source_ref]) if source_ref is not None else "[]"
    cur = await conn.execute(
        """
        INSERT INTO graph_edges (
            workspace_id, src_entity_id, dst_entity_id, edge_kind,
            weight, source_refs
        ) VALUES (%s, %s, %s, %s, %s, %s::jsonb)
        ON CONFLICT (workspace_id, src_entity_id, dst_entity_id, edge_kind)
        DO UPDATE SET
            weight = graph_edges.weight + EXCLUDED.weight,
            source_refs = graph_edges.source_refs || EXCLUDED.source_refs,
            updated_at = NOW()
        RETURNING id::text, (xmax = 0) AS inserted
        """,
        (
            workspace_id, src_entity_id, dst_entity_id, edge_kind,
            weight_delta, ref_json,
        ),
    )
    row = await cur.fetchone()
    assert row is not None
    return str(row[0]), bool(row[1])


# ---------------------------------------------------------------------------
# Reads — adjacency lookups for PPR + UI graph view
# ---------------------------------------------------------------------------


async def list_neighbors(
    conn: Connection,
    *,
    workspace_id: str,
    entity_id: str,
    direction: str = "both",  # 'out' | 'in' | 'both'
    edge_kind: str | None = None,
    limit: int = 200,
) -> list[GraphEdgeRecord]:
    """Adjacency lookup for one entity. 'out' = entity is src, 'in' = dst."""
    if edge_kind is not None and edge_kind not in EDGE_KINDS:
        raise ValueError(f"edge_kind must be one of {EDGE_KINDS}")

    kind_clause = "" if edge_kind is None else "AND edge_kind = %s"
    if direction == "out":
        where = "src_entity_id = %s"
    elif direction == "in":
        where = "dst_entity_id = %s"
    elif direction == "both":
        where = "(src_entity_id = %s OR dst_entity_id = %s)"
    else:
        raise ValueError(f"direction must be out/in/both, got {direction!r}")

    base_sql = (
        f"SELECT {_SELECT_COLS} FROM graph_edges "
        f"WHERE workspace_id = %s AND {where} {kind_clause} "
        "ORDER BY weight DESC LIMIT %s"
    )

    if direction == "both":
        params: tuple = (workspace_id, entity_id, entity_id)
    else:
        params = (workspace_id, entity_id)
    if edge_kind is not None:
        params = (*params, edge_kind)
    params = (*params, limit)

    cur = await conn.execute(base_sql, params)
    return [_row(r) for r in await cur.fetchall()]


async def list_edges_for_workspace(
    conn: Connection,
    *,
    workspace_id: str,
    edge_kind: str | None = None,
    limit: int = 500,
) -> list[GraphEdgeRecord]:
    """All edges in a workspace — used by the workspace-level PPR builder
    and by /workspace/graph/stats (WA-14)."""
    if edge_kind is None:
        cur = await conn.execute(
            f"SELECT {_SELECT_COLS} FROM graph_edges "
            "WHERE workspace_id = %s ORDER BY weight DESC LIMIT %s",
            (workspace_id, limit),
        )
    else:
        cur = await conn.execute(
            f"SELECT {_SELECT_COLS} FROM graph_edges "
            "WHERE workspace_id = %s AND edge_kind = %s "
            "ORDER BY weight DESC LIMIT %s",
            (workspace_id, edge_kind, limit),
        )
    return [_row(r) for r in await cur.fetchall()]


async def count_edges_for_workspace(
    conn: Connection, *, workspace_id: str,
) -> dict[str, int]:
    """Per-kind counts for the workspace. Used by /workspace/stats."""
    cur = await conn.execute(
        "SELECT edge_kind, COUNT(*) FROM graph_edges "
        "WHERE workspace_id = %s GROUP BY edge_kind",
        (workspace_id,),
    )
    rows = await cur.fetchall()
    return {str(r[0]): int(r[1]) for r in rows}


async def fetch_adjacency_for_ppr(
    conn: Connection, *, workspace_id: str,
) -> dict[str, list[tuple[str, float]]]:
    """Build the in-memory adjacency map PPR consumes. Returns
    {entity_id: [(neighbor_id, weight), ...]} — undirected (each edge
    contributes both directions)."""
    cur = await conn.execute(
        "SELECT src_entity_id, dst_entity_id, weight "
        "FROM graph_edges WHERE workspace_id = %s",
        (workspace_id,),
    )
    rows = await cur.fetchall()
    adj: dict[str, list[tuple[str, float]]] = {}
    for src, dst, w in rows:
        adj.setdefault(str(src), []).append((str(dst), float(w)))
        adj.setdefault(str(dst), []).append((str(src), float(w)))
    return adj
