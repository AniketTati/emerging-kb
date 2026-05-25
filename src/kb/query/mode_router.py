"""B4a / WA-10 — Mode-conditional retrieval routing.

After the planner emits a `Plan`, the orchestrator hands it here. We
adjust the candidate hit list according to the chosen mode:

  H (default) → pass-through; orchestrator's existing 6-channel pipeline
                produced the hits.

  K (doc-chain aware) → filter / annotate hits by doc_chain_members. The
                planner's `chain_view` selects:
                  - current_version: keep only hits whose file is the
                    chain's `current_version_id` (or has no chain at all)
                  - all_versions:    keep hits + add `chain_id` annotation
                  - history_only:    keep ONLY non-current members of a chain

  T (graph traversal) → seed entities derived from query mentions; run
                PPR via kb.query.ppr; surface neighbor entities + the
                files that mention them (intersected with existing hits).

  E / F / S / D / M / G / C / A → pass-through with a `mode_applied` tag.
                These modes mostly tune *upstream* retrieval; in Wave A
                we surface the mode for observability but don't re-rank.

  Q → raises QModeNotImplementedError. Q-mode lands in B4b with its
                10-layer SQL defense pipeline.

Pure-Python; safe to test without a DB by passing a mock connection.
"""

from __future__ import annotations

import re
from typing import Any

from kb.query.planner import Plan
from kb.query.rrf import Hit


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class QModeNotImplementedError(RuntimeError):
    """Raised when the planner emits mode='Q' before B4b lands."""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def apply_mode(
    plan: Plan,
    hits: list[Hit],
    *,
    workspace_id: str,
    query: str,
    conn: Any,
) -> list[Hit]:
    """Apply mode-conditional routing to the candidate hit list. Pure
    side-effect-free transformation; returns a new list.

    `conn` may be None when running unit-tests that don't need DB lookups
    (K/T modes degrade gracefully)."""
    mode = (plan.mode or "H").upper()

    if mode == "H":
        return list(hits)

    if mode == "Q":
        raise QModeNotImplementedError(
            "Q-mode (SQL aggregation) ships in B4b — currently refused"
        )

    if mode == "K":
        return await _route_k_mode(plan, hits, conn, workspace_id=workspace_id)

    if mode == "T":
        return await _route_t_mode(plan, hits, conn, workspace_id=workspace_id, query=query)

    # E / F / S / D / M / G / C / A — pass-through with mode tag.
    return _tag_mode(hits, mode)


# ---------------------------------------------------------------------------
# K-mode — doc-chain aware
# ---------------------------------------------------------------------------


async def _fetch_chain_membership(
    conn: Any, *, workspace_id: str, file_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """For each file_id, return {chain_id, is_current_version, parent_doc_id}
    when the file belongs to a chain. Files not in any chain are omitted."""
    if not file_ids:
        return {}
    try:
        cur = await conn.execute(
            "SELECT m.doc_id::text, m.chain_id::text, "
            "       (c.current_version_id = m.doc_id) AS is_current, "
            "       m.parent_doc_id::text "
            "FROM doc_chain_members m "
            "JOIN doc_chains c ON c.id = m.chain_id "
            "WHERE m.workspace_id = %s AND m.doc_id::text = ANY(%s)",
            (workspace_id, file_ids),
        )
        rows = await cur.fetchall()
    except Exception:
        return {}
    return {
        str(r[0]): {
            "chain_id": str(r[1]),
            "is_current_version": bool(r[2]),
            "parent_doc_id": str(r[3]) if r[3] else None,
        }
        for r in rows
    }


async def _route_k_mode(
    plan: Plan, hits: list[Hit], conn: Any, *, workspace_id: str,
) -> list[Hit]:
    """Filter hits by doc-chain membership per plan.chain_view.

    chain_view defaults to 'current_version' when unset."""
    chain_view = (plan.chain_view or "current_version").lower()
    if conn is None:
        # Test path / no DB → just annotate, no filtering.
        return _annotate_chain(hits, {})

    file_ids = sorted({
        (h.metadata or {}).get("file_id")
        for h in hits if (h.metadata or {}).get("file_id")
    })
    file_ids = [f for f in file_ids if f]
    if not file_ids:
        return list(hits)

    membership = await _fetch_chain_membership(
        conn, workspace_id=workspace_id, file_ids=file_ids,
    )

    out: list[Hit] = []
    for h in hits:
        fid = (h.metadata or {}).get("file_id")
        info = membership.get(fid) if fid else None

        # Annotate every hit with chain context (UI badges).
        new_meta = dict(h.metadata or {})
        if info:
            new_meta["chain_id"] = info["chain_id"]
            new_meta["is_current_version"] = info["is_current_version"]
        new_meta["mode_applied"] = "K"
        new_meta["chain_view"] = chain_view
        new_h = Hit(
            id=h.id, kind=h.kind, score=h.score,
            snippet=h.snippet, metadata=new_meta,
        )

        if not info:
            # No chain membership → only keep when chain_view permits
            # standalone files (current_version + all_versions do).
            if chain_view in ("current_version", "all_versions"):
                out.append(new_h)
            continue

        if chain_view == "current_version":
            if info["is_current_version"]:
                out.append(new_h)
        elif chain_view == "history_only":
            if not info["is_current_version"]:
                out.append(new_h)
        else:  # all_versions
            out.append(new_h)

    return out


def _annotate_chain(hits: list[Hit], _membership: dict) -> list[Hit]:
    """Lightweight no-DB annotation — used when conn is None."""
    out = []
    for h in hits:
        new_meta = dict(h.metadata or {})
        new_meta["mode_applied"] = "K"
        out.append(Hit(
            id=h.id, kind=h.kind, score=h.score,
            snippet=h.snippet, metadata=new_meta,
        ))
    return out


# ---------------------------------------------------------------------------
# T-mode — graph traversal via PPR
# ---------------------------------------------------------------------------


# Cheap NER: capitalized token sequences in the query are likely entity
# surface forms. Refined entity resolution lives in mentions_exact / B1.
_CAPITALIZED = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})\b")


def _candidate_mentions_from_query(query: str) -> list[str]:
    return [m.group(1) for m in _CAPITALIZED.finditer(query or "")]


async def _resolve_seed_entities(
    conn: Any, *, workspace_id: str, query: str,
) -> list[str]:
    """Return seed entity_ids from the query via mention table lookup.

    Wave A: case-insensitive substring match on entities.canonical_name +
    extracted_mentions.mention_text. We return ≤ 5 unique entity ids."""
    candidates = _candidate_mentions_from_query(query) or [query.strip()]
    if not candidates or conn is None:
        return []
    try:
        cur = await conn.execute(
            "SELECT DISTINCT e.id::text "
            "FROM entities e "
            "WHERE e.workspace_id = %s "
            "AND lower(e.canonical_name) = ANY(%s) "
            "LIMIT 5",
            (workspace_id, [c.lower() for c in candidates]),
        )
        rows = await cur.fetchall()
    except Exception:
        return []
    return [str(r[0]) for r in rows]


async def _read_graph_edges(
    conn: Any, *, workspace_id: str, limit: int = 5000,
) -> list[tuple[str, str, float]]:
    """Pull recent graph edges for PPR. Capped at `limit` rows."""
    if conn is None:
        return []
    try:
        cur = await conn.execute(
            "SELECT src_entity_id::text, dst_entity_id::text, weight "
            "FROM graph_edges WHERE workspace_id = %s "
            "ORDER BY updated_at DESC NULLS LAST LIMIT %s",
            (workspace_id, limit),
        )
        rows = await cur.fetchall()
    except Exception:
        return []
    return [(str(r[0]), str(r[1]), float(r[2] or 1.0)) for r in rows]


async def _route_t_mode(
    plan: Plan,
    hits: list[Hit],
    conn: Any,
    *,
    workspace_id: str,
    query: str,
) -> list[Hit]:
    """Multi-hop graph traversal. Seeds derived from query mentions, run
    PPR over the workspace graph, then boost hits whose files mention the
    top neighbor entities.

    When seeds can't be resolved (no entity match) we degrade to the
    pass-through path so the user still gets an answer."""
    from kb.query.ppr import build_adjacency_from_edges, personalized_pagerank

    # Seeds: prefer planner-supplied, else infer from query.
    seeds = list(plan.seed_entities) or await _resolve_seed_entities(
        conn, workspace_id=workspace_id, query=query,
    )
    if not seeds:
        return _tag_mode(hits, "T")

    edges = await _read_graph_edges(conn, workspace_id=workspace_id)
    if not edges:
        return _tag_mode(hits, "T")

    adj = build_adjacency_from_edges(edges)
    ppr_results = personalized_pagerank(
        adjacency=adj, seed_entity_ids=seeds, top_k=25,
    )
    top_entity_ids = {r.entity_id for r in ppr_results}

    # Pull the file_ids that mention any top entity.
    boost_files: set[str] = set()
    try:
        cur = await conn.execute(
            "SELECT DISTINCT em.file_id::text "
            "FROM extracted_mentions em "
            "JOIN mention_to_entity me ON me.mention_id = em.id "
            "WHERE me.workspace_id = %s AND me.entity_id::text = ANY(%s)",
            (workspace_id, sorted(top_entity_ids)),
        )
        boost_files = {str(r[0]) for r in await cur.fetchall()}
    except Exception:
        boost_files = set()

    out: list[Hit] = []
    for h in hits:
        fid = (h.metadata or {}).get("file_id")
        new_meta = dict(h.metadata or {})
        new_meta["mode_applied"] = "T"
        new_meta["ppr_seeds"] = seeds
        if fid and fid in boost_files:
            new_meta["ppr_boost"] = True
            # 1.5x score for hits on PPR-connected files.
            boosted_score = h.score * 1.5
        else:
            boosted_score = h.score
        out.append(Hit(
            id=h.id, kind=h.kind, score=boosted_score,
            snippet=h.snippet, metadata=new_meta,
        ))
    # Re-sort by boosted score, descending.
    out.sort(key=lambda x: x.score, reverse=True)
    return out


# ---------------------------------------------------------------------------
# Pass-through annotation
# ---------------------------------------------------------------------------


def _tag_mode(hits: list[Hit], mode: str) -> list[Hit]:
    out: list[Hit] = []
    for h in hits:
        new_meta = dict(h.metadata or {})
        new_meta["mode_applied"] = mode
        out.append(Hit(
            id=h.id, kind=h.kind, score=h.score,
            snippet=h.snippet, metadata=new_meta,
        ))
    return out
