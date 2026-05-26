"""B4a + B4b / WA-10 — Mode-conditional retrieval routing.

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

  Q → B4b structured-query handler. Compiles plan.q_payload via the
                kb.q_planner pipeline, executes, and emits ONE synthesized
                Hit carrying the aggregate result (rendered as a snippet
                + an 'aggregate' modality marker for citation polymorphism).
                If plan.q_payload is missing, the handler refuses cleanly
                with a synthetic refusal Hit so the orchestrator + generator
                downstream can produce a useful refusal message.

Pure-Python (with DB calls for K/T/Q); safe to test by passing a mock
connection.
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
    """Legacy — kept for back-compat. With B4b shipped, the Q handler no
    longer raises this; the orchestrator now receives a synthesized Hit
    that carries the aggregate result. Tests that assert the legacy
    behavior should be updated to inspect the returned Hit instead."""


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
        return await _route_q_mode(
            plan, hits, conn,
            workspace_id=workspace_id, query=query,
        )

    if mode == "K":
        return await _route_k_mode(plan, hits, conn, workspace_id=workspace_id)

    if mode == "T":
        return await _route_t_mode(plan, hits, conn, workspace_id=workspace_id, query=query)

    if mode == "G":
        return await _route_g_mode(plan, hits, conn, workspace_id=workspace_id)

    # E / F / S / D / M / C / A — pass-through with mode tag.
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


# ---------------------------------------------------------------------------
# G-mode — global/thematic summary
# ---------------------------------------------------------------------------

# How many chunk-level hits to keep below the RAPTOR summary nodes.
# Smaller than the default top-10 because summary nodes are very token-
# dense (each LLM-generated summary is 200-500 tokens). Keeping the cap
# tight prevents the generator's input from blowing past max_input_tokens.
_G_MODE_CHUNK_TAIL = 5


async def _route_g_mode(
    plan: Plan,
    hits: list[Hit],
    conn: Any,
    *,
    workspace_id: str,
) -> list[Hit]:
    """For global/thematic queries (\"summarize the workspace\", \"give me an
    overview\"), boost corpus-level RAPTOR nodes to the top of the hit list.

    The 6 retrieval channels return chunk-level matches whose scoring is
    keyword-driven — for a meta-query, that surfaces a tightly-clustered
    set of docs sharing common terms rather than a workspace-representative
    sample. RAPTOR's L3 corpus root and L2 cluster summaries ARE workspace
    summaries by construction (LLM-generated synthesis of clusters), so
    they're the right input for the generator.

    Strategy:
      1. Pull every RAPTOR node with scope='corpus' for the workspace
      2. Sort descending by level (L3 root first, then L2 clusters)
      3. Prepend them to the hit list as `kind='raptor_node'` hits
      4. Truncate the chunk-level tail to `_G_MODE_CHUNK_TAIL` to keep
         the generator's input bounded
      5. Tag everything with `mode_applied='G'` for observability

    Fail-safe: when there are NO corpus RAPTOR nodes (e.g. corpus build
    never triggered, or only 1 file), fall through to the pass-through
    tag so the generator sees the same chunk hits as mode H.
    """
    raptor_hits = await _fetch_corpus_raptor_hits(conn, workspace_id=workspace_id)
    if not raptor_hits:
        return _tag_mode(hits, "G")

    # Keep a short tail of chunk-level hits for grounding / per-claim
    # citation. The summary nodes lead; chunks support specific facts.
    chunk_tail = _tag_mode(hits[:_G_MODE_CHUNK_TAIL], "G")
    raptor_tagged = _tag_mode(raptor_hits, "G")
    return raptor_tagged + chunk_tail


async def _fetch_corpus_raptor_hits(
    conn: Any, *, workspace_id: str,
) -> list[Hit]:
    """Read all `scope='corpus'` raptor_nodes for the workspace and
    materialise them as Hit objects ready for the generator's prompt.

    Ordering: L3 (root) first, then L2 cluster summaries. The L3 node
    is a true workspace synthesis — single most informative chunk for a
    \"summarize everything\" ask. The L2 nodes cluster the workspace by
    natural topic groupings (medical, financial, legal, …) so the
    generator can structure its answer per cluster.

    Returns [] when no corpus tree exists yet (corpus RAPTOR is built
    via the explicit POST /corpus/raptor/rebuild endpoint).
    """
    if conn is None:
        return []
    try:
        cur = await conn.execute(
            """
            SELECT id::text, text, level, file_id::text
              FROM raptor_nodes
             WHERE workspace_id = %s AND scope = 'corpus'
             ORDER BY level DESC, id ASC
            """,
            (workspace_id,),
        )
        rows = await cur.fetchall()
    except Exception:
        return []

    out: list[Hit] = []
    for row in rows:
        node_id, text, level, file_id = row
        out.append(Hit(
            # Score is purely synthetic — give the L3 root the highest
            # score, then L2 nodes, so RRF/rerank downstream don't
            # accidentally drop them. 1.0 + level/10 keeps them above
            # every BM25/dense score we see in practice.
            id=str(node_id),
            kind="raptor_node",
            score=1.0 + (level or 0) / 10.0,
            snippet=text or "",
            metadata={
                "level": level,
                "scope": "corpus",
                "file_id": file_id,
                "channel": "g_mode_boost",
            },
        ))
    return out


# ---------------------------------------------------------------------------
# Q-mode — structured aggregate query (B4b)
# ---------------------------------------------------------------------------


def _q_refusal_hit(reason: str) -> Hit:
    """Build a synthetic Hit representing a Q-mode refusal. The orchestrator
    surfaces this through the normal citation pipeline; the generator's
    refusal logic kicks in when it sees `metadata.q_refused=True`."""
    return Hit(
        id="q-mode-refusal",
        kind="aggregate",
        score=0.0,
        snippet=f"Q-mode refused: {reason}",
        metadata={
            "mode_applied": "Q",
            "aggregate": True,
            "q_refused": True,
            "q_refusal_reason": reason,
        },
    )


async def _route_q_mode(
    plan: Plan,
    hits: list[Hit],
    conn: Any,
    *,
    workspace_id: str,
    query: str,
) -> list[Hit]:
    """Compile + execute the planner's Q payload. Returns a single
    synthesized Hit carrying the aggregate result (or a refusal).

    The Hit's kind='aggregate' + metadata.aggregate=True makes the Design 5
    polymorphic citation builder pick the 'aggregate' modality, so the
    citation carries `audit_query_id` and `row_count` automatically."""
    if not plan.q_payload:
        # Planner didn't produce a Q payload — Identity planner emits
        # mode='Q' from heuristics ("how many") but doesn't know how to
        # build the SQL. The Gemini planner is required for real Q
        # queries; this branch trips on Identity + an aggregation query.
        return [_q_refusal_hit(
            "no Q payload — the Identity planner cannot emit SQL; "
            "configure KB_PLANNER=gemini for aggregations"
        )]

    if conn is None:
        return [_q_refusal_hit("Q-mode requires a database connection")]

    from kb.domain.audit_queries import insert_audit_query
    from kb.q_planner import (
        DEFAULT_ROW_CAP,
        DEFAULT_TIMEOUT_MS,
        QPlanValidationError,
        compile_plan,
        execute,
        parse_plan,
        validate,
    )
    from kb.q_planner.artifact import persist_csv_artifact
    from kb.q_planner.grammar import QPlanParseError

    # Layers 2 + 3 + 4 (grammar parse — operator / aggregation / set_op enums)
    try:
        parsed = parse_plan(plan.q_payload)
    except QPlanParseError as exc:
        return [_q_refusal_hit(f"plan parse error: {exc}")]

    # Layer 1 (catalog whitelist + type checks)
    try:
        validated = validate(parsed)
    except QPlanValidationError as exc:
        return [_q_refusal_hit(f"plan validation error: {exc}")]

    # Layers 5 + 6 (compile to parameterized SQL — no escape hatch)
    try:
        sql, params = compile_plan(
            validated, workspace_id=workspace_id, row_cap=DEFAULT_ROW_CAP,
        )
    except Exception as exc:  # noqa: BLE001
        return [_q_refusal_hit(f"plan compile error: {exc}")]

    # Layers 7 + 8 + 9 (execute with read_only + timeout + row cap)
    result = await execute(
        conn, sql, params,
        row_cap=DEFAULT_ROW_CAP,
        timeout_ms=DEFAULT_TIMEOUT_MS,
    )

    # Layer 10 — persist audit row (+ best-effort CSV artifact).
    # audit_queries is APPEND-ONLY (kb_app has SELECT+INSERT only), so we
    # can't UPDATE the csv_artifact_key after insert. Workflow:
    #   1. Pre-compute the audit_query_id (UUID) client-side.
    #   2. Upload the CSV under that key (best-effort; may fail silently).
    #   3. INSERT the row in one shot with the key already set.
    import uuid as _uuid
    audit_id = str(_uuid.uuid4())
    csv_key: str | None = None

    if result.status == "ok" and result.row_count > 0:
        csv_key = await persist_csv_artifact(
            workspace_id=workspace_id,
            audit_query_id=audit_id,
            column_names=result.column_names,
            rows=result.rows,
        )

    try:
        await insert_audit_query(
            conn,
            workspace_id=workspace_id,
            query_log_id=None,
            plan=plan.q_payload,
            compiled_sql=sql,
            params=list(params),
            row_count=result.row_count,
            runtime_ms=result.runtime_ms,
            status=result.status,
            refusal_reason=result.error_message,
            csv_artifact_key=csv_key,
            audit_query_id=audit_id,
        )
    except Exception as exc:  # noqa: BLE001
        # Audit insert failure must not block the answer.
        import logging
        logging.getLogger(__name__).warning(
            "Q-mode audit_query insert failed: %s", exc,
        )
        audit_id = "audit-insert-failed"

    # Synthesize the single aggregate Hit.
    if result.status == "ok":
        snippet = _format_aggregate_snippet(result.column_names, result.rows)
        return [Hit(
            id=audit_id,
            kind="aggregate",
            score=1.0,
            snippet=snippet,
            metadata={
                "mode_applied": "Q",
                "aggregate": True,
                "audit_query_id": audit_id,
                "row_count": result.row_count,
                "csv_artifact_key": csv_key,
                "Q_plan_id": audit_id,
                "column_names": list(result.column_names),
            },
        )]

    # Non-ok status: refusal Hit.
    return [_q_refusal_hit(
        f"{result.status}: {result.error_message or 'no detail'}"
    )]


def _format_aggregate_snippet(
    column_names: tuple[str, ...] | list[str],
    rows: tuple[tuple, ...] | list[tuple],
    *,
    max_rows: int = 5,
) -> str:
    """Human-readable rendering of the aggregate result. Powers the
    generator's answer template ("Across 18 contracts, total cap $X")
    when KB_QUERY_LLM=identity. The Gemini generator gets the same
    snippet + can paraphrase."""
    if not rows:
        return f"Aggregate query returned no rows. Columns: {list(column_names)}."
    cols = list(column_names)
    head_rows = rows[:max_rows]
    lines = [f"Aggregate result over {len(rows)} row(s):"]
    for r in head_rows:
        pairs = []
        for c, v in zip(cols, r):
            pairs.append(f"{c}={v}")
        lines.append("  " + ", ".join(pairs))
    if len(rows) > max_rows:
        lines.append(f"  ... ({len(rows) - max_rows} more rows in CSV artifact)")
    return "\n".join(lines)
