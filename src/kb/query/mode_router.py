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

  E (entity lookup)    → resolve query → seed entity_ids; filter/boost
                          hits whose file mentions that entity. Degrades
                          to H if no seed resolves.

  F (field filter)     → apply field_filters as post-retrieval predicate
                          on hits whose schema_field values match.
                          Degrades to H if no filters or zero matches.

  S (scoped summarize) → boost RAPTOR per-doc summary nodes for the
                          file(s) named in the query. Pure summary nodes
                          lead; chunk tail kept short for grounding.

  D (doc metadata)     → filter hits by files.* predicates
                          (inferred_doc_type, mime_type, date range).
                          Degrades to H if filter wipes everything.

  M (mention search)   → boost hits whose extracted_mentions list
                          contains the user's target term. Re-ranks
                          by mention count + score.

  C (atomic-unit filter) → filter hits to extracted_entities rows of a
                          specific unit_type (e.g. only transactions,
                          only clauses). Boosts unit hits over chunks.

  A (anomaly)          → boost extracted_entities rows with high
                          rarity_score (top of the workspace's outlier
                          list for the unit_type the query is about).

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

    if mode == "E":
        return await _route_e_mode(plan, hits, conn, workspace_id=workspace_id, query=query)

    if mode == "F":
        return await _route_f_mode(plan, hits, conn, workspace_id=workspace_id)

    if mode == "S":
        return await _route_s_mode(plan, hits, conn, workspace_id=workspace_id, query=query)

    if mode == "D":
        return await _route_d_mode(plan, hits, conn, workspace_id=workspace_id)

    if mode == "M":
        return await _route_m_mode(plan, hits, conn, workspace_id=workspace_id, query=query)

    if mode == "C":
        return await _route_c_mode(plan, hits, conn, workspace_id=workspace_id)

    if mode == "A":
        return await _route_a_mode(plan, hits, conn, workspace_id=workspace_id)

    # Unknown mode (defensive) — pass-through with tag.
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


_VALID_CHAIN_VIEWS = ("current_version", "all_versions", "history_only")


async def _route_k_mode(
    plan: Plan, hits: list[Hit], conn: Any, *, workspace_id: str,
) -> list[Hit]:
    """Filter hits by doc-chain membership per plan.chain_view.

    chain_view defaults to 'current_version' when unset.

    Resilience contracts:
      * The Gemini planner occasionally fills `chain_view` with the
        doc_type (e.g. "postmortem", "invoice") instead of one of the
        three view enums. Without normalisation we'd silently drop
        every standalone hit (none match the synthetic view), then
        return zero hits, then refuse. Coerce unrecognised values back
        to 'current_version'.
      * Even with a valid chain_view, if K-mode filtering wipes out
        every hit (e.g. the targeted doc is genuinely chain-less but
        the planner picked K because of a temporal hint like "recent"),
        fall back to the unfiltered hit list so the generator still
        has something to answer with. Refusing in that case is
        strictly worse than pretending K was H.
    """
    chain_view_raw = (plan.chain_view or "").lower().strip()
    chain_view = chain_view_raw if chain_view_raw in _VALID_CHAIN_VIEWS else "current_version"

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

    # Fall-back: K filtered everything out but the unfiltered list was
    # non-empty — the planner over-triggered K (likely because of a
    # temporal/sequence hint in the query). Degrade to H rather than
    # refuse: tag the original hits with mode_applied=K so observability
    # still surfaces the planner's choice, but keep the candidate set.
    if not out and hits:
        return _annotate_chain(hits, {})
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
        # Planner didn't produce a Q payload. Two cases:
        #
        #   1. IdentityPlanner — by design can't emit SQL. We tell the
        #      user honestly: switch to gemini for aggregations.
        #
        #   2. GeminiPlanner — the second LLM call (q_payload_gen.py)
        #      already attempted to build the plan and failed; the
        #      reason is on plan.notes prefixed `q_payload_gen:`.
        #      Surface that to the user instead of the generic message
        #      so they can fix the question or know the catalog can't
        #      answer it.
        reason = "could not build a safe SQL plan for this aggregation"
        if plan.notes and "q_payload_gen:" in plan.notes:
            # Pull just the last q_payload_gen segment.
            tail = plan.notes.rsplit("q_payload_gen:", 1)[-1].strip()
            if tail.startswith("refuse:"):
                reason = tail[len("refuse:"):].strip()
            elif tail.startswith("no_llm:"):
                reason = (
                    "Q-mode requires KB_GEMINI_API_KEY to translate "
                    "the question into a SQL plan; the LLM planner is "
                    "not configured"
                )
            else:
                # parse_error / validation / llm_error — surface verbatim.
                reason = tail
        elif plan.model_id and "identity" in plan.model_id.lower():
            reason = (
                "Q-mode aggregations need an LLM planner; the Identity "
                "planner can't generate SQL. Set KB_PLANNER=gemini + "
                "KB_GEMINI_API_KEY."
            )
        return [_q_refusal_hit(reason)]

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


# ---------------------------------------------------------------------------
# E-mode — entity lookup
# ---------------------------------------------------------------------------


async def _route_e_mode(
    plan: Plan,
    hits: list[Hit],
    conn: Any,
    *,
    workspace_id: str,
    query: str,
) -> list[Hit]:
    """Resolve query → seed entity_ids, then BOOST hits whose file mentions
    any of them. Falls back to H-mode pass-through when no entity resolves.

    The boost is intentionally gentler than T (1.3x vs 1.5x) — E is a
    single-entity ask, so the file-level mention is informative but not
    as strong a signal as a PPR-derived multi-hop neighborhood.
    """
    seeds = list(plan.seed_entities) or await _resolve_seed_entities(
        conn, workspace_id=workspace_id, query=query,
    )
    if not seeds or conn is None:
        return _tag_mode(hits, "E")

    # Resolve any name surface forms to actual entity ids (the planner
    # may have stashed bare strings rather than DB ids).
    resolved_ids = await _resolve_entity_ids_from_seeds(
        conn, workspace_id=workspace_id, seeds=seeds,
    )
    if not resolved_ids:
        return _tag_mode(hits, "E")

    # Pull file_ids that mention any seed entity.
    mention_files: set[str] = set()
    try:
        cur = await conn.execute(
            "SELECT DISTINCT em.file_id::text "
            "FROM extracted_mentions em "
            "JOIN mention_to_entity me ON me.mention_id = em.id "
            "WHERE me.workspace_id = %s AND me.entity_id::text = ANY(%s)",
            (workspace_id, sorted(resolved_ids)),
        )
        mention_files = {str(r[0]) for r in await cur.fetchall()}
    except Exception:
        mention_files = set()

    out: list[Hit] = []
    boosted_any = False
    for h in hits:
        fid = (h.metadata or {}).get("file_id")
        new_meta = dict(h.metadata or {})
        new_meta["mode_applied"] = "E"
        new_meta["entity_seeds"] = list(resolved_ids)
        if fid and fid in mention_files:
            new_meta["entity_match"] = True
            boosted_score = h.score * 1.3
            boosted_any = True
        else:
            boosted_score = h.score
        out.append(Hit(
            id=h.id, kind=h.kind, score=boosted_score,
            snippet=h.snippet, metadata=new_meta,
        ))
    if not boosted_any:
        # Seed resolved but no candidate hit mentioned it — degrade to H
        # so the user still sees something. Tag preserved for audit.
        return out
    out.sort(key=lambda x: x.score, reverse=True)
    return out


async def _resolve_entity_ids_from_seeds(
    conn: Any, *, workspace_id: str, seeds: list[str],
) -> list[str]:
    """Translate a list of seeds (which may be entity names OR ids) into
    canonical entity_ids. Names are matched case-insensitively against
    `entities.canonical_name`."""
    if not seeds or conn is None:
        return []
    # Heuristic: if a seed looks like a UUID, treat it as an id; otherwise
    # treat as a name and resolve.
    uuid_re = re.compile(
        r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
    )
    ids: set[str] = set()
    names: list[str] = []
    for s in seeds:
        if uuid_re.match(s):
            ids.add(s)
        else:
            names.append(s.lower())
    if names:
        try:
            cur = await conn.execute(
                "SELECT id::text FROM entities "
                "WHERE workspace_id = %s "
                "AND lower(canonical_name) = ANY(%s) LIMIT 20",
                (workspace_id, names),
            )
            for r in await cur.fetchall():
                ids.add(str(r[0]))
        except Exception:
            pass
    return sorted(ids)


# ---------------------------------------------------------------------------
# F-mode — schema field filter
# ---------------------------------------------------------------------------


async def _route_f_mode(
    plan: Plan,
    hits: list[Hit],
    conn: Any,
    *,
    workspace_id: str,
) -> list[Hit]:
    """Apply plan.field_filters as a post-retrieval predicate on each
    hit's extracted_entities row. When no filters are set or the filter
    drops every hit, degrade to H-style pass-through.

    Each filter is a dict with `{field, op, value}` keys — same shape as
    Q-mode filters but applied in-process against the file's
    extracted_entities.fields jsonb.
    """
    filters = list(plan.field_filters or ())
    if not filters or conn is None:
        return _tag_mode(hits, "F")

    file_ids = sorted({
        (h.metadata or {}).get("file_id")
        for h in hits if (h.metadata or {}).get("file_id")
    })
    file_ids = [f for f in file_ids if f]
    if not file_ids:
        return _tag_mode(hits, "F")

    # Pull extracted_entities.fields jsonb for the candidate files.
    matching_files: set[str] = set()
    try:
        cur = await conn.execute(
            "SELECT file_id::text, fields FROM extracted_entities "
            "WHERE workspace_id = %s AND file_id::text = ANY(%s)",
            (workspace_id, file_ids),
        )
        rows = await cur.fetchall()
    except Exception:
        return _tag_mode(hits, "F")

    for fid, fields in rows:
        if not isinstance(fields, dict):
            continue
        if all(_field_predicate_holds(fields, f) for f in filters):
            matching_files.add(str(fid))

    if not matching_files:
        # F filter wiped everything — degrade rather than refuse.
        return _tag_mode(hits, "F")

    out: list[Hit] = []
    for h in hits:
        fid = (h.metadata or {}).get("file_id")
        if fid in matching_files:
            new_meta = dict(h.metadata or {})
            new_meta["mode_applied"] = "F"
            new_meta["field_filters"] = list(filters)
            out.append(Hit(
                id=h.id, kind=h.kind, score=h.score,
                snippet=h.snippet, metadata=new_meta,
            ))
    if not out:
        return _tag_mode(hits, "F")
    return out


def _field_predicate_holds(fields: dict, f: dict) -> bool:
    """Evaluate one field predicate against an extracted_entity.fields
    dict. Recognised ops: eq, ne, lt, le, gt, ge, like, in. Unknown ops
    fail-open (return True) so we don't accidentally drop hits."""
    name = f.get("field")
    op = (f.get("op") or "eq").lower()
    val = f.get("value")
    if name is None or name not in fields:
        return False
    actual = fields[name]
    try:
        if op == "eq":
            return actual == val
        if op == "ne":
            return actual != val
        if op == "lt":
            return float(actual) < float(val)
        if op == "le":
            return float(actual) <= float(val)
        if op == "gt":
            return float(actual) > float(val)
        if op == "ge":
            return float(actual) >= float(val)
        if op == "like":
            return isinstance(actual, str) and str(val).lower() in actual.lower()
        if op == "in":
            return actual in (val if isinstance(val, (list, tuple)) else [val])
    except (TypeError, ValueError):
        return False
    return True


# ---------------------------------------------------------------------------
# S-mode — scoped summarize
# ---------------------------------------------------------------------------


async def _route_s_mode(
    plan: Plan,
    hits: list[Hit],
    conn: Any,
    *,
    workspace_id: str,
    query: str,
) -> list[Hit]:
    """Scoped summary — pull RAPTOR `scope='per_doc'` summary nodes for
    the candidate file(s) and prepend them to the hit list. The result
    is similar in shape to G-mode but scoped to specific docs.

    Candidate file resolution order:
      1. plan.file_ids if the planner pinned them
      2. Top file_ids appearing in the existing hit list (top-3)
    """
    target_file_ids: list[str] = list(plan.file_ids)
    if not target_file_ids:
        # Take the top-3 distinct file_ids from the hit list.
        seen: list[str] = []
        for h in hits:
            fid = (h.metadata or {}).get("file_id")
            if fid and fid not in seen:
                seen.append(fid)
            if len(seen) >= 3:
                break
        target_file_ids = seen
    if not target_file_ids or conn is None:
        return _tag_mode(hits, "S")

    raptor_hits = await _fetch_per_doc_raptor_hits(
        conn, workspace_id=workspace_id, file_ids=target_file_ids,
    )
    if not raptor_hits:
        return _tag_mode(hits, "S")

    chunk_tail = _tag_mode(hits[:_G_MODE_CHUNK_TAIL], "S")
    raptor_tagged = _tag_mode(raptor_hits, "S")
    return raptor_tagged + chunk_tail


async def _fetch_per_doc_raptor_hits(
    conn: Any, *, workspace_id: str, file_ids: list[str],
) -> list[Hit]:
    """Read scope='per_doc' raptor_nodes for the requested files."""
    if conn is None or not file_ids:
        return []
    try:
        cur = await conn.execute(
            """
            SELECT id::text, text, level, file_id::text
              FROM raptor_nodes
             WHERE workspace_id = %s
               AND scope = 'per_doc'
               AND file_id::text = ANY(%s)
             ORDER BY level DESC, id ASC
            """,
            (workspace_id, file_ids),
        )
        rows = await cur.fetchall()
    except Exception:
        return []
    out: list[Hit] = []
    for row in rows:
        node_id, text, level, file_id = row
        out.append(Hit(
            id=str(node_id),
            kind="raptor_node",
            score=1.0 + (level or 0) / 10.0,
            snippet=text or "",
            metadata={
                "level": level,
                "scope": "per_doc",
                "file_id": file_id,
                "channel": "s_mode_boost",
            },
        ))
    return out


# ---------------------------------------------------------------------------
# D-mode — doc metadata filter
# ---------------------------------------------------------------------------


async def _route_d_mode(
    plan: Plan,
    hits: list[Hit],
    conn: Any,
    *,
    workspace_id: str,
) -> list[Hit]:
    """Filter hits by file-level predicates (inferred_doc_type, etc.).

    Wave-A scope: only `doc_types` is applied as a filter — the planner
    surfaces these from the query (or LLM-supplied). Future passes add
    mime_type / created_at / source_authority filters.

    Degrades to H-style pass-through if the filter wipes everything or
    if no doc_types are specified.
    """
    doc_types = list(plan.doc_types or ())
    if not doc_types or conn is None:
        return _tag_mode(hits, "D")

    file_ids = sorted({
        (h.metadata or {}).get("file_id")
        for h in hits if (h.metadata or {}).get("file_id")
    })
    file_ids = [f for f in file_ids if f]
    if not file_ids:
        return _tag_mode(hits, "D")

    matching: set[str] = set()
    try:
        cur = await conn.execute(
            "SELECT id::text FROM files "
            "WHERE workspace_id = %s "
            "AND id::text = ANY(%s) "
            "AND inferred_doc_type = ANY(%s)",
            (workspace_id, file_ids, doc_types),
        )
        matching = {str(r[0]) for r in await cur.fetchall()}
    except Exception:
        return _tag_mode(hits, "D")

    if not matching:
        return _tag_mode(hits, "D")

    out: list[Hit] = []
    for h in hits:
        fid = (h.metadata or {}).get("file_id")
        if fid in matching:
            new_meta = dict(h.metadata or {})
            new_meta["mode_applied"] = "D"
            new_meta["doc_types"] = list(doc_types)
            out.append(Hit(
                id=h.id, kind=h.kind, score=h.score,
                snippet=h.snippet, metadata=new_meta,
            ))
    if not out:
        return _tag_mode(hits, "D")
    return out


# ---------------------------------------------------------------------------
# M-mode — mention search
# ---------------------------------------------------------------------------


async def _route_m_mode(
    plan: Plan,
    hits: list[Hit],
    conn: Any,
    *,
    workspace_id: str,
    query: str,
) -> list[Hit]:
    """Find hits whose underlying chunk contains an extracted_mention
    matching one of the seed entity names from the query.

    Seeds come from either:
      - plan.seed_entities (preferred — planner-resolved entity ids/names)
      - capitalized tokens extracted from the query
    """
    seeds = list(plan.seed_entities) or _candidate_mentions_from_query(query)
    if not seeds or conn is None:
        return _tag_mode(hits, "M")

    # Build a substring filter against extracted_mentions.mention_text.
    file_ids = sorted({
        (h.metadata or {}).get("file_id")
        for h in hits if (h.metadata or {}).get("file_id")
    })
    file_ids = [f for f in file_ids if f]
    if not file_ids:
        return _tag_mode(hits, "M")

    # Pull (file_id, mention_count) for each file whose mentions contain
    # any of the seed terms (case-insensitive).
    counts: dict[str, int] = {}
    try:
        cur = await conn.execute(
            "SELECT em.file_id::text, count(*) "
            "FROM extracted_mentions em "
            "WHERE em.workspace_id = %s "
            "AND em.file_id::text = ANY(%s) "
            "AND lower(em.mention_text) = ANY(%s) "
            "GROUP BY em.file_id",
            (workspace_id, file_ids, [s.lower() for s in seeds]),
        )
        for fid, n in await cur.fetchall():
            counts[str(fid)] = int(n)
    except Exception:
        return _tag_mode(hits, "M")

    if not counts:
        return _tag_mode(hits, "M")

    out: list[Hit] = []
    for h in hits:
        fid = (h.metadata or {}).get("file_id")
        n = counts.get(fid, 0) if fid else 0
        new_meta = dict(h.metadata or {})
        new_meta["mode_applied"] = "M"
        new_meta["mention_seeds"] = list(seeds)
        if n > 0:
            new_meta["mention_count"] = n
            # Boost by mention density — log scale keeps a 100-mention
            # doc from completely dominating a 5-mention doc.
            import math
            boost = 1.0 + math.log(1 + n) / 4.0
            boosted_score = h.score * boost
        else:
            boosted_score = h.score
        out.append(Hit(
            id=h.id, kind=h.kind, score=boosted_score,
            snippet=h.snippet, metadata=new_meta,
        ))
    out.sort(key=lambda x: x.score, reverse=True)
    return out


# ---------------------------------------------------------------------------
# C-mode — atomic-unit filter (typed sub-entity rows)
# ---------------------------------------------------------------------------


async def _route_c_mode(
    plan: Plan,
    hits: list[Hit],
    conn: Any,
    *,
    workspace_id: str,
) -> list[Hit]:
    """Surface extracted_entities rows of a specific unit_type
    (transaction, clause, line_item, …) as first-class Hits, prepended
    to the existing chunk hits. Filters by plan.unit_types when set.

    Each surfaced entity becomes a `kind='extracted_entity'` Hit whose
    snippet is a one-line summary of its fields. The citation builder
    handles the entity modality via Design 5.
    """
    unit_types = list(plan.unit_types or ())
    if not unit_types or conn is None:
        return _tag_mode(hits, "C")

    file_ids = sorted({
        (h.metadata or {}).get("file_id")
        for h in hits if (h.metadata or {}).get("file_id")
    })
    file_ids = [f for f in file_ids if f]
    # Pull matching entities. If hits gave us no file scope, query the
    # whole workspace (capped).
    try:
        if file_ids:
            cur = await conn.execute(
                """
                SELECT id::text, file_id::text, unit_type, fields,
                       rarity_score
                  FROM extracted_entities
                 WHERE workspace_id = %s
                   AND unit_type = ANY(%s)
                   AND file_id::text = ANY(%s)
                 ORDER BY rarity_score DESC NULLS LAST
                 LIMIT 30
                """,
                (workspace_id, unit_types, file_ids),
            )
        else:
            cur = await conn.execute(
                """
                SELECT id::text, file_id::text, unit_type, fields,
                       rarity_score
                  FROM extracted_entities
                 WHERE workspace_id = %s
                   AND unit_type = ANY(%s)
                 ORDER BY rarity_score DESC NULLS LAST
                 LIMIT 30
                """,
                (workspace_id, unit_types),
            )
        rows = await cur.fetchall()
    except Exception:
        return _tag_mode(hits, "C")

    if not rows:
        return _tag_mode(hits, "C")

    unit_hits = [
        Hit(
            id=str(eid),
            kind="extracted_entity",
            # Synthetic score above typical BM25 — rarity_score is the
            # secondary sort signal so rare units lead.
            score=1.0 + (float(rarity or 0) / 10.0),
            snippet=_format_unit_snippet(unit_type, fields),
            metadata={
                "mode_applied": "C",
                "file_id": file_id,
                "unit_type": unit_type,
                "rarity_score": float(rarity) if rarity is not None else None,
                "channel": "c_mode_boost",
            },
        )
        for eid, file_id, unit_type, fields, rarity in rows
    ]
    chunk_tail = _tag_mode(hits[:_G_MODE_CHUNK_TAIL], "C")
    return unit_hits + chunk_tail


def _format_unit_snippet(unit_type: str, fields: Any) -> str:
    """One-line rendering of a typed sub-entity for the answer prompt."""
    if not isinstance(fields, dict):
        return f"[{unit_type}] (no fields)"
    pairs = []
    for k, v in fields.items():
        if v is None:
            continue
        pairs.append(f"{k}={v}")
        if len(pairs) >= 6:
            break
    if not pairs:
        return f"[{unit_type}] (empty)"
    return f"[{unit_type}] " + " · ".join(pairs)


# ---------------------------------------------------------------------------
# A-mode — anomaly
# ---------------------------------------------------------------------------


async def _route_a_mode(
    plan: Plan,
    hits: list[Hit],
    conn: Any,
    *,
    workspace_id: str,
) -> list[Hit]:
    """Surface high-rarity extracted_entities rows as Hits. Optionally
    constrained to specific unit_types from plan.unit_types.

    Threshold: rarity_score >= 1.0 (units more than 1σ from the cohort
    centroid). Returns up to 25 rows sorted by rarity_score descending.
    """
    if conn is None:
        return _tag_mode(hits, "A")

    unit_types = list(plan.unit_types or ())

    try:
        if unit_types:
            cur = await conn.execute(
                """
                SELECT id::text, file_id::text, unit_type, fields,
                       rarity_score
                  FROM extracted_entities
                 WHERE workspace_id = %s
                   AND unit_type = ANY(%s)
                   AND rarity_score >= 1.0
                 ORDER BY rarity_score DESC NULLS LAST
                 LIMIT 25
                """,
                (workspace_id, unit_types),
            )
        else:
            cur = await conn.execute(
                """
                SELECT id::text, file_id::text, unit_type, fields,
                       rarity_score
                  FROM extracted_entities
                 WHERE workspace_id = %s
                   AND rarity_score >= 1.0
                 ORDER BY rarity_score DESC NULLS LAST
                 LIMIT 25
                """,
                (workspace_id,),
            )
        rows = await cur.fetchall()
    except Exception:
        return _tag_mode(hits, "A")

    if not rows:
        # No anomalies above threshold — degrade to the original hits
        # tagged A so the user gets context rather than an empty refusal.
        return _tag_mode(hits, "A")

    anomaly_hits = [
        Hit(
            id=str(eid),
            kind="extracted_entity",
            score=2.0 + (float(rarity or 0) / 5.0),
            snippet=(
                f"[anomaly · rarity={float(rarity or 0):.2f}] "
                + _format_unit_snippet(unit_type, fields)
            ),
            metadata={
                "mode_applied": "A",
                "file_id": file_id,
                "unit_type": unit_type,
                "rarity_score": float(rarity) if rarity is not None else None,
                "anomaly": True,
                "channel": "a_mode_boost",
            },
        )
        for eid, file_id, unit_type, fields, rarity in rows
    ]
    chunk_tail = _tag_mode(hits[:_G_MODE_CHUNK_TAIL], "A")
    return anomaly_hits + chunk_tail
