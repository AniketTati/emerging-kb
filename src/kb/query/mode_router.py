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
    predicate: Any = None,
) -> list[Hit]:
    """Apply mode-conditional routing to the candidate hit list. Pure
    side-effect-free transformation; returns a new list.

    `conn` may be None when running unit-tests that don't need DB lookups
    (K/T modes degrade gracefully). `predicate` (T3) is the turn's
    `ResolvedPredicate` — Q-mode reads its `row_filters` for the §6.11 row-level
    WHERE; other modes ignore it."""
    mode = (plan.mode or "H").upper()

    # Q + T own their full result (Q has its own answer path; T-mode does the
    # typed-KG traversal inline).
    if mode == "Q":
        return await _route_q_mode(
            plan, hits, conn,
            workspace_id=workspace_id, query=query, predicate=predicate,
        )
    if mode == "T":
        return await _route_t_mode(plan, hits, conn, workspace_id=workspace_id, query=query)

    if mode == "H":
        result = list(hits)
    elif mode == "K":
        result = await _route_k_mode(plan, hits, conn, workspace_id=workspace_id)
    elif mode == "G":
        result = await _route_g_mode(plan, hits, conn, workspace_id=workspace_id)
    elif mode == "E":
        result = await _route_e_mode(plan, hits, conn, workspace_id=workspace_id, query=query)
    elif mode == "F":
        result = await _route_f_mode(plan, hits, conn, workspace_id=workspace_id)
    elif mode == "S":
        result = await _route_s_mode(plan, hits, conn, workspace_id=workspace_id, query=query)
    elif mode == "D":
        result = await _route_d_mode(plan, hits, conn, workspace_id=workspace_id)
    elif mode == "M":
        result = await _route_m_mode(plan, hits, conn, workspace_id=workspace_id, query=query)
    elif mode == "C":
        result = await _route_c_mode(plan, hits, conn, workspace_id=workspace_id)
    elif mode == "A":
        result = await _route_a_mode(plan, hits, conn, workspace_id=workspace_id)
    else:
        # Unknown mode (defensive) — pass-through with tag.
        result = _tag_mode(hits, mode)

    # Phase 3a (§6.9) — opportunistically surface a typed KG answer for a
    # relationship-intent query the planner did NOT route to T-mode. No-op for
    # non-relationship queries (cheap predicate gate) and when no edge resolves.
    return await _maybe_kg_augment(
        plan, result, conn, workspace_id=workspace_id, query=query,
    )


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
    """Return seed entity_ids from the query via entities-table lookup.

    Wave A: case-insensitive lookup with prefix tolerance — so a query
    that says 'Vertex Industries' matches 'Vertex Industries Ltd.' in
    the entities table. We return ≤ 5 unique entity ids."""
    candidates = _candidate_mentions_from_query(query) or [query.strip()]
    return await _resolve_names_to_entity_ids(
        conn, workspace_id=workspace_id, names=candidates,
    )


async def _resolve_names_to_entity_ids(
    conn: Any, *, workspace_id: str, names: list[str],
) -> list[str]:
    """Look up entity_ids for a list of name surface forms. Tolerant of
    suffix variation ('Vertex Industries' matches 'Vertex Industries
    Ltd.') via a prefix-or-equality predicate. Returns ≤ 5 unique ids.

    Inputs that already look like UUIDs are kept as-is (they were
    likely supplied by an upstream planner that already resolved them).
    """
    if not names or conn is None:
        return []

    uuid_re = re.compile(
        r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
    )
    ids: set[str] = set()
    name_candidates: list[str] = []
    for n in names:
        n = (n or "").strip()
        if not n:
            continue
        if uuid_re.match(n):
            ids.add(n)
        else:
            name_candidates.append(n.lower())

    if name_candidates:
        # ILIKE with both exact-equality AND prefix-match handles the
        # common case where extraction kept a trailing suffix
        # ('Ltd.', 'Inc.', 'LLC') that the user's query elided.
        clauses = []
        params: list[Any] = [workspace_id]
        for c in name_candidates:
            clauses.append(
                "lower(canonical_name) = %s "
                "OR lower(canonical_name) LIKE %s"
            )
            params.append(c)
            params.append(c + " %")  # prefix-with-word-boundary
        try:
            cur = await conn.execute(
                "SELECT DISTINCT id::text FROM canonical_entities "
                "WHERE workspace_id = %s "
                "AND (" + " OR ".join(clauses) + ") LIMIT 10",
                params,
            )
            for r in await cur.fetchall():
                ids.add(str(r[0]))
        except Exception:
            pass

    # Cap at 5 — PPR has diminishing returns past a handful of seeds.
    return sorted(ids)[:5]


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

    # Seeds may arrive as raw names (from planner.seed_entities) OR as
    # entity_ids (UUIDs). Always run them through the resolver so PPR
    # gets actual entity_ids — names alone would miss every adjacency
    # entry and yield zero PPR hits (the bug that made T silently
    # degrade in practice despite the graph being populated).
    raw_seeds = list(plan.seed_entities)
    if raw_seeds:
        seeds = await _resolve_names_to_entity_ids(
            conn, workspace_id=workspace_id, names=raw_seeds,
        )
    else:
        seeds = await _resolve_seed_entities(
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

    # Phase 3a (§6.9) — typed-relationship KG answer. When the graph holds typed
    # edges for the resolved seed, surface a STRUCTURED, cited answer (with
    # provenance + a lower-confidence flag) AHEAD of the PPR-boosted RAG hits.
    # No edges → just return the boosted hits (degrade to RAG, I2).
    try:
        from collections import Counter

        from kb.query.kg_relations import (
            build_kg_answer,
            detect_relation_intent,
            format_kg_snippet,
        )
        kg = await build_kg_answer(
            conn, workspace_id=workspace_id, seed_ids=seeds,
            intent=detect_relation_intent(query),
        )
    except Exception:  # noqa: BLE001
        kg = None
    if kg and kg.n_edges:
        kg_hit = await _kg_hit_with_verdict(conn, workspace_id, query, kg)
        return [kg_hit, *out[:_Q_SOURCE_HITS_CAP]]
    return out


def _synthesize_kg_hit(kg: Any) -> Hit:
    """Build the headline KG answer Hit from a `KgAnswer`: the structured typed
    edges in the snippet, provenance + lower-confidence flag in metadata, and a
    real evidence file_id so it cites a source doc via the default modality."""
    from collections import Counter

    from kb.query.kg_relations import format_kg_snippet

    ev_counter = Counter(f for e in kg.edges for f in e.evidence_file_ids)
    top_file = ev_counter.most_common(1)[0][0] if ev_counter else None
    return Hit(
        id=f"kg:{kg.seed_id}",
        kind="kg_relation",
        score=2.0,  # headline, above the boosted RAG hits
        snippet=format_kg_snippet(kg),
        metadata={
            "mode_applied": "T",
            "kg_relation": True,
            "file_id": top_file,
            "kg_seed": kg.seed_name,
            "kg_n_edges": kg.n_edges,
            "kg_n_single_evidence": kg.n_single_evidence,
            "kg_provenance_file_ids": kg.file_ids,
            "kg_notes": list(kg.notes),
        },
    )


async def _kg_hit_with_verdict(
    conn: Any, workspace_id: str, query: str, kg: Any,
) -> Hit:
    """Synthesize the KG answer hit and, for a yes/no question naming a
    counterpart, prepend the §6.3 existence verdict (YES / soft NO). Shared by
    BOTH the inline T-mode path and the opportunistic augmenter so an existence
    question is answered the same regardless of how the planner routed it."""
    kg_hit = _synthesize_kg_hit(kg)
    try:
        from kb.query.kg_relations import (
            existence_verdict,
            extract_asserted_object,
            is_existence_query,
        )
        if is_existence_query(query):
            obj_name = extract_asserted_object(query)
            if obj_name:
                obj_ids = {
                    i for i in await _resolve_names_to_entity_ids(
                        conn, workspace_id=workspace_id, names=[obj_name])
                    if i != kg.seed_id
                }
                verdict = existence_verdict(kg, obj_ids, obj_name) if obj_ids else None
                if verdict:
                    md = dict(kg_hit.metadata or {})
                    md["kg_existence_verdict"] = verdict
                    kg_hit = Hit(
                        id=kg_hit.id, kind=kg_hit.kind, score=kg_hit.score,
                        snippet=verdict + "\n" + kg_hit.snippet, metadata=md,
                    )
    except Exception:  # noqa: BLE001
        pass
    return kg_hit


async def _maybe_kg_augment(
    plan: Plan,
    hits: list[Hit],
    conn: Any,
    *,
    workspace_id: str,
    query: str,
) -> list[Hit]:
    """Surface a typed-relationship KG answer for a relationship-intent query
    even when the planner did NOT route to T-mode (it routes "where is X
    located" → E, "subsidiaries of Y" → H, etc. inconsistently). Cheap-gated on
    a detected relation predicate so only relationship questions pay the graph
    lookup; degrades to the original hits when no seed / edge resolves (I2)."""
    if conn is None:
        return hits
    try:
        from kb.query.kg_relations import build_kg_answer, detect_relation_intent

        intent = detect_relation_intent(query)
        if intent.predicate is None:        # not a relationship question
            return hits
        if any((h.metadata or {}).get("kg_relation") for h in hits):
            return hits                      # a mode handler already added it
        raw = list(plan.seed_entities)
        seeds = (
            await _resolve_names_to_entity_ids(
                conn, workspace_id=workspace_id, names=raw)
            if raw else
            await _resolve_seed_entities(
                conn, workspace_id=workspace_id, query=query)
        )
        kg = await build_kg_answer(
            conn, workspace_id=workspace_id, seed_ids=seeds, intent=intent,
            relax=False,   # opportunistic: only inject on a SPECIFIC match
        )
        if kg and kg.n_edges:
            kg_hit = await _kg_hit_with_verdict(conn, workspace_id, query, kg)
            kept = [h for h in hits if not (h.metadata or {}).get("kg_relation")]
            return [kg_hit, *kept[:_Q_SOURCE_HITS_CAP]]
    except Exception:  # noqa: BLE001
        pass
    return hits


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


# C1 — how many retrieved source-doc hits to keep alongside the aggregate
# result so an aggregation answer can cite the documents it was drawn from.
_Q_SOURCE_HITS_CAP = 5


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


async def _superseded_file_ids(conn: Any, workspace_id: str) -> list[str]:
    """File_ids that are OLDER versions in a doc chain (superseded by the chain's
    current version). Excluding them from an aggregate dedups an entity that
    appears in both an original and its amendment (§6.11 grain dedup).

    SAVEPOINT-guarded + best-effort: any error (or no chains) → [] (no dedup,
    today's behavior — never poisons the shared request txn)."""
    if conn is None:
        return []
    try:
        await conn.execute("SAVEPOINT q_dedup")
    except Exception:  # noqa: BLE001
        return []
    try:
        cur = await conn.execute(
            "SELECT m.doc_id::text FROM doc_chain_members m "
            "JOIN doc_chains c ON c.id = m.chain_id "
            "WHERE m.workspace_id = %s AND c.current_version_id IS NOT NULL "
            "  AND m.doc_id <> c.current_version_id",
            (workspace_id,),
        )
        rows = await cur.fetchall()
        try:
            await conn.execute("RELEASE SAVEPOINT q_dedup")
        except Exception:  # noqa: BLE001
            pass
        return [str(r[0]) for r in rows]
    except Exception:  # noqa: BLE001
        try:
            await conn.execute("ROLLBACK TO SAVEPOINT q_dedup")
            await conn.execute("RELEASE SAVEPOINT q_dedup")
        except Exception:  # noqa: BLE001
            pass
        return []


# Words dropped when deriving the "concept" of a summed field, so
# `principal_portion` and a stated `total_principal_outstanding` still match on
# the shared head noun `principal`.
_RECON_STOPWORDS: frozenset[str] = frozenset({
    "total", "totals", "sum", "of", "the", "amount", "amounts", "value",
    "values", "inr", "usd", "eur", "portion", "as", "at", "on", "fy", "q1",
    "q2", "q3", "q4", "net", "gross", "balance", "per", "and",
})
# A stated total field_name carries one of these markers.
_RECON_TOTAL_MARKERS: tuple[str, ...] = (
    "total", "aggregate", "outstanding", "grand", "overall", "cumulative",
)


def _concept_tokens(name: str) -> set[str]:
    return {
        t for t in re.split(r"[^a-z0-9]+", (name or "").lower())
        if len(t) >= 3 and t not in _RECON_STOPWORDS
    }


async def _reconcile_stated_total(
    conn: Any,
    *,
    workspace_id: str,
    parsed: Any,
    computed_value: float,
    summed_key: str,
) -> str | None:
    """§6.5(1) active stated-vs-computed reconciliation — for a single SUM over
    `extracted_entities` unit rows, look for a STATED total of the SAME concept
    in `proposed_fields` on the SAME source docs and compare.

    Returns a note to surface BOTH figures with provenance when they materially
    disagree (or confirm consistency), or None when there's no comparable stated
    total. Signal-gated + SAVEPOINT-isolated → never asserts which is right
    (§6.5(4): surface, don't silently pick), never breaks the query."""
    concept = _concept_tokens(summed_key)
    if not concept or conn is None:
        return None
    # Source docs of the summed unit rows (scope the stated-total search).
    unit_vals: list[str] = [
        str(v)
        for f in parsed.filters
        if f.field == "unit_type" and f.op in ("eq", "in")
        for v in (f.value if isinstance(f.value, list) else [f.value])
        if v is not None
    ]

    async def _q(sp: str, sql: str, p: tuple) -> list[tuple] | None:
        try:
            await conn.execute(f"SAVEPOINT {sp}")
        except Exception:  # noqa: BLE001
            return None
        try:
            cur = await conn.execute(sql, p)
            rows = await cur.fetchall()
            try:
                await conn.execute(f"RELEASE SAVEPOINT {sp}")
            except Exception:  # noqa: BLE001
                pass
            return rows
        except Exception:  # noqa: BLE001
            try:
                await conn.execute(f"ROLLBACK TO SAVEPOINT {sp}")
                await conn.execute(f"RELEASE SAVEPOINT {sp}")
            except Exception:  # noqa: BLE001
                pass
            return None

    if unit_vals:
        files = await _q(
            "q_recon_files",
            "SELECT DISTINCT file_id::text FROM extracted_entities "
            "WHERE workspace_id = %s AND unit_type = ANY(%s)",
            (workspace_id, unit_vals),
        )
        file_ids = [r[0] for r in (files or []) if r[0]]
    else:
        file_ids = []
    if not file_ids:
        return None

    stated = await _q(
        "q_recon_stated",
        "SELECT field_name, value_numeric, file_id::text FROM proposed_fields "
        "WHERE workspace_id = %s AND value_numeric IS NOT NULL "
        "  AND file_id = ANY(%s::uuid[])",
        (workspace_id, file_ids),
    )
    if not stated:
        return None

    # Best stated candidate: a "total"-marked field sharing the concept noun.
    best: tuple[float, str, float] | None = None  # (overlap, field_name, value)
    for field_name, value_numeric, _fid in stated:
        fn = str(field_name)
        if not any(m in fn.lower() for m in _RECON_TOTAL_MARKERS):
            continue
        overlap = len(concept & _concept_tokens(fn))
        if overlap <= 0:
            continue
        try:
            sval = float(value_numeric)
        except (TypeError, ValueError):
            continue
        if best is None or overlap > best[0]:
            best = (overlap, fn, sval)
    if best is None:
        return None

    _overlap, stated_name, stated_val = best
    try:
        tol = await _recon_tolerance(conn, workspace_id)
    except Exception:  # noqa: BLE001
        tol = 0.01
    return _reconcile_note(float(computed_value), stated_name, stated_val, tol)


def _reconcile_note(
    computed_value: float, stated_name: str, stated_val: float, tol: float,
) -> str:
    """Pure §6.5(1)/(4) verdict: on a gap > tol, surface BOTH figures with
    provenance (never silently pick); else note consistency. Split out so the
    same-quantity decision is unit-testable without a DB."""
    denom = max(abs(stated_val), 1.0)
    gap = abs(computed_value - stated_val) / denom
    if gap > tol:
        return (
            f"Reconciliation: the COMPUTED total ({computed_value:,.2f}, summed "
            f"from the rows) differs from a STATED total '{stated_name}' "
            f"({stated_val:,.2f}) by {gap * 100:.1f}%. Surface BOTH — verify "
            f"they cover the same basis (period / scope / tax) before trusting "
            f"either; do not silently pick one."
        )
    return (
        f"Reconciliation: the computed total ({computed_value:,.2f}) is "
        f"consistent with the stated total '{stated_name}' ({stated_val:,.2f}) "
        f"within tolerance."
    )


async def _recon_tolerance(conn: Any, workspace_id: str) -> float:
    from kb.query.config_thresholds import resolve_query_threshold
    return await resolve_query_threshold(
        conn, key="recon_tolerance", workspace_id=workspace_id, default=0.01,
    )


# Field-name fragments that signal a point-in-time / period variant — summing
# across these (mar-31 outstanding + jan-1 outstanding + original) is the live
# "606M" non-additive trap.
_PERIOD_SUFFIX_RE = re.compile(
    r"(_as_of_|_q[1-4]\b|q[1-4]_?fy|_fy_?\d{2,4}|_\d{4}\b|"
    r"_(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b|"
    r"opening|closing|_\d{1,2}_\d{4})",
    re.I,
)


def _heterogeneous_sum_caveat(parsed: Any) -> str | None:
    """§6.11 grain guard — a SUM that adds >=2 DISTINCT `field_name`s together is
    suspicious (you may be adding different as-of dates / metrics of the SAME
    item). Surface a caveat; never silently change the number (§6.6)."""
    if not any(
        (getattr(a, "op", "") or "").upper() == "SUM" for a in parsed.aggregations
    ):
        return None
    field_names: list[str] = []
    for f in parsed.filters:
        if f.field == "field_name" and f.op == "in" and isinstance(f.value, list):
            field_names = [str(v) for v in f.value]
    distinct = sorted(set(field_names))
    if len(distinct) < 2:
        return None
    period = [n for n in distinct if _PERIOD_SUFFIX_RE.search(n)]
    listed = ", ".join(distinct[:6]) + (" …" if len(distinct) > 6 else "")
    if period:
        return (
            f"Grain warning: this SUM adds {len(distinct)} DISTINCT fields "
            f"together ({listed}), several of which look like different points "
            f"in time / periods. Summing different as-of dates or period-"
            f"variants of one item is usually NOT meaningful — verify they are "
            f"additive (e.g. a true multi-period cumulative) before trusting it."
        )
    return (
        f"Grain warning: this SUM adds {len(distinct)} DISTINCT fields together "
        f"({listed}). Verify they measure the same additive quantity, not "
        f"different metrics of one item."
    )


def _multi_unit_type_caveat(parsed: Any) -> str | None:
    """§6.11 dedup guard — an additive aggregate unioning >=2 *semantically
    distinct* unit_types (collapsing spelling variants) may double-count if one
    is a highlighted subset of another (e.g. `major_transaction` inside
    `transaction_listing`). Caveat only — silent row-dedup would risk new
    wrongness without a reliable natural key."""
    from kb.q_planner.dynamic_catalog import _collapse

    unit_vals = [
        str(v)
        for f in parsed.filters
        if f.field == "unit_type" and f.op in ("eq", "in")
        for v in (f.value if isinstance(f.value, list) else [f.value])
        if v is not None
    ]
    canon = sorted({_collapse(u) for u in unit_vals if u})
    if len(canon) < 2:
        return None
    # Only additive aggregates double-count under overlap; MIN/MAX don't.
    if not any(
        (getattr(a, "op", "") or "").upper() in ("SUM", "COUNT", "COUNT_DISTINCT")
        for a in parsed.aggregations
    ):
        return None
    return (
        f"Overlap warning: this aggregate unions {len(canon)} DISTINCT row "
        f"types ({', '.join(canon)}). If one is a highlighted subset of another "
        f"(e.g. 'major transactions' also listed among all transactions), the "
        f"total may double-count — confirm the row types don't overlap."
    )


# Date phrases lifted from the query when the planner emitted no date_filter —
# the set `normalize_date_expression` understands.
_DATE_PHRASE_RE = re.compile(
    r"\b(today|yesterday|this month|last month|previous month|this year|"
    r"last year|previous year|ytd|year to date|this quarter|current quarter|"
    r"last quarter|previous quarter|last \d{1,3} (?:days?|months?|years?)|"
    r"q[1-4]\s*(?:fy)?\s*'?\d{2,4}|fy\s*'?\d{2,4}|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{4}|"
    r"\d{4}-\d{2}(?:-\d{2})?)\b",
    re.I,
)
# Key names that hold a per-row date.
_DATE_KEY_RE = re.compile(
    r"(^|_)(date|dated|day|datetime|timestamp|posting|value_date|txn_date)($|_)",
    re.I,
)


def _derive_date_row_filter(query: str, parsed: Any, live_catalog: Any, *, now):
    """§6.11 #12 — when the planner emitted NO date window, lift one from the
    query and the catalog's date key on the target unit_type, so "highest
    transaction last quarter" filters at row level instead of running all-time.

    Conservative + fail-open: only fires for an `extracted_entities` plan with a
    recognizable date phrase AND a real date key on the queried rows. If it
    over-narrows to zero rows, the §6.6 sanity check refuses honestly ("no rows
    matched") — which beats a confidently-wrong all-time number. Returns a
    RowFilter dict or None."""
    if parsed.from_table != "extracted_entities" or live_catalog is None:
        return None
    m = _DATE_PHRASE_RE.search(query or "")
    if not m:
        return None
    try:
        from kb.query.structured_prefilter import normalize_date_expression
        rng = normalize_date_expression(m.group(0), now=now)
    except Exception:  # noqa: BLE001
        return None
    if rng is None:
        return None
    from kb.q_planner.dynamic_catalog import _collapse

    unit_vals = [
        str(v)
        for f in parsed.filters
        if f.field == "unit_type" and f.op in ("eq", "in")
        for v in (f.value if isinstance(f.value, list) else [f.value])
        if v is not None
    ]
    candidates: list[str] = []
    if unit_vals:
        for ut in unit_vals:
            candidates.extend(live_catalog.unit_keys.get(_collapse(ut), {}).keys())
    else:
        for km in live_catalog.unit_keys.values():
            candidates.extend(km.keys())
    if "date" in candidates:
        date_key = "date"
    else:
        date_keys = sorted({k for k in candidates if _DATE_KEY_RE.search(k)})
        date_key = date_keys[0] if date_keys else None
    if date_key is None:
        return None
    return {
        "column": date_key, "op": "between", "value": rng.to_list(),
        "grain": "unit", "kind": "date", "_phrase": m.group(0),
    }


def _aggregate_all_null(group_by: tuple | list, cols: list, rows: list) -> bool:
    """True when EVERY aggregate cell (the columns after the group-by keys) is
    NULL — i.e. rows may have matched but nothing cast to a value. This is the
    §6.6 'computed nothing' case (e.g. SUM over 'INR 18,400/year' strings the
    cast can't parse) that must NOT ship as a number."""
    g = len(group_by or ())
    if not rows:
        return True
    for r in rows:
        for ci in range(g, len(cols)):
            if ci < len(r) and r[ci] is not None:
                return False
    return True


async def _count_contributing_rows(
    conn: Any,
    *,
    parsed: Any,
    workspace_id: str,
    row_filters: list[dict],
    exclude_ids: list[str],
    live_catalog: Any,
) -> int | None:
    """COUNT(*) over the SAME WHERE (filters + §6.11 row-filters + dedup) as the
    aggregate — the 'computed from N rows' figure (§6.6) and the sanity-check
    denominator. Reuses the compiler so the WHERE matches exactly. None on any
    failure (sanity then can't assert a contributing count, but won't crash)."""
    try:
        from kb.q_planner import (
            DEFAULT_ROW_CAP,
            DEFAULT_TIMEOUT_MS,
            compile_plan,
            execute,
            validate,
        )
        from kb.q_planner.grammar import Aggregation, QPlan

        count_plan = QPlan(
            from_table=parsed.from_table,
            filters=parsed.filters,
            group_by=(),
            aggregations=(Aggregation(op="COUNT", field="*", alias="n"),),
            order_by=(),
            limit=1,
        )
        validated = validate(count_plan, live_catalog=live_catalog)
        csql, cparams = compile_plan(
            validated, workspace_id=workspace_id, row_cap=DEFAULT_ROW_CAP,
            row_filters=row_filters, live_catalog=live_catalog,
            exclude_file_ids=exclude_ids,
        )
        cres = await execute(
            conn, csql, cparams,
            row_cap=DEFAULT_ROW_CAP, timeout_ms=DEFAULT_TIMEOUT_MS,
        )
        if cres.status == "ok" and cres.rows:
            return int(cres.rows[0][0])
    except Exception:  # noqa: BLE001
        return None
    return None


async def _route_q_mode(
    plan: Plan,
    hits: list[Hit],
    conn: Any,
    *,
    workspace_id: str,
    query: str,
    predicate: Any = None,
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

    # T3 §6.11 — the live, type-aware catalog: gates a numeric aggregation over a
    # text-only field at EXEC time too (defense-in-depth for HTTP / hand-built
    # plans) and lets the row-filter compiler skip a mis-named column. Best-effort
    # → None leaves the static-catalog behavior.
    live_catalog = None
    try:
        from kb.q_planner.dynamic_catalog import build_dynamic_catalog
        live_catalog = await build_dynamic_catalog(conn, workspace_id=workspace_id)
    except Exception:  # noqa: BLE001
        live_catalog = None

    # Row-level filters (date windows / value thresholds) carried by the
    # ResolvedPredicate (§6.11). Passed as dicts so the compiler stays
    # query-layer-free.
    row_filter_dicts: list[dict] = []
    if predicate is not None and getattr(predicate, "row_filters", None):
        try:
            row_filter_dicts = [rf.to_dict() for rf in predicate.row_filters]
        except Exception:  # noqa: BLE001
            row_filter_dicts = []

    # Layers 2 + 3 + 4 (grammar parse — operator / aggregation / set_op enums)
    try:
        parsed = parse_plan(plan.q_payload)
    except QPlanParseError as exc:
        return [_q_refusal_hit(f"plan parse error: {exc}")]

    # Layer 1 (catalog whitelist + type checks) + T3 value_type gate.
    try:
        validated = validate(parsed, live_catalog=live_catalog)
    except QPlanValidationError as exc:
        return [_q_refusal_hit(f"plan validation error: {exc}")]

    # T3 §6.11 grain dedup — superseded doc versions to drop from the aggregate
    # (an entity in both an original + amendment counted once). Signal-gated.
    exclude_ids = await _superseded_file_ids(conn, workspace_id)

    # Everything the user must be told about the number (repair / merge / cap /
    # reconciliation / sanity) — so a computed aggregate is never bare (§6.6).
    agg_notes: list[str] = []

    # T3 §6.11 #12 — if the planner emitted NO date window, derive one from the
    # query deterministically (the planner is unreliable at this); the §6.6
    # sanity check backstops an over-narrow to zero rows.
    if not row_filter_dicts:
        try:
            from datetime import datetime
            _derived = _derive_date_row_filter(
                query, parsed, live_catalog, now=datetime.now(),
            )
            if _derived:
                row_filter_dicts = [_derived]
                agg_notes.append(
                    f"applied a row-level date window for "
                    f"\"{_derived.get('_phrase')}\" = {_derived['value'][0]}.."
                    f"{_derived['value'][1]} (the planner emitted none); if no "
                    f"rows fall in it, there is no data for that period"
                )
        except Exception:  # noqa: BLE001
            pass

    # Layers 5-9 + §6.6 self-repair: compile + execute, peeling the ADDITIVE T3
    # layers (row-filters, then dedup) on a SQL *error* — ≤2 retries toward the
    # pre-T3 known-good base query — before a typed refusal. Never trust-or-crash.
    repair_plan: list[tuple[list, list]] = [
        (row_filter_dicts, exclude_ids),   # full T3
        ([], exclude_ids),                 # drop §6.11 row-filters
        ([], []),                          # base (pre-T3 known-good)
    ]
    result = None
    sql, params = "", []
    used_rf, used_ex = row_filter_dicts, exclude_ids
    for attempt, (rf, ex) in enumerate(repair_plan):
        try:
            sql, params = compile_plan(
                validated, workspace_id=workspace_id, row_cap=DEFAULT_ROW_CAP,
                row_filters=rf, live_catalog=live_catalog, exclude_file_ids=ex,
            )
        except Exception as exc:  # noqa: BLE001
            return [_q_refusal_hit(f"plan compile error: {exc}")]
        result = await execute(
            conn, sql, params,
            row_cap=DEFAULT_ROW_CAP, timeout_ms=DEFAULT_TIMEOUT_MS,
        )
        if result.status != "error":
            used_rf, used_ex = rf, ex
            if attempt == 1:
                agg_notes.append(
                    "self-repair: re-ran without the row-level date/value "
                    "filter after a SQL error"
                )
            elif attempt == 2:
                agg_notes.append(
                    "self-repair: re-ran the base aggregate (dropped row-filter "
                    "+ dedup) after a SQL error"
                )
            break
    if result is None:  # defensive — repair_plan is non-empty
        return [_q_refusal_hit("Q-mode could not execute the aggregate")]

    # T3 §6.11 — group-by canonicalization (fold spelling-variant entity keys:
    # HDFC / HDFC Bank / HDFC Ltd → one) + GROUPBY_CARDINALITY_CAP. Pure
    # post-processing over the result rows; `display_*` is what the user sees +
    # what the CSV/snippet carry. `agg_notes` accumulates everything we must
    # surface (merge / cap / reconciliation / sanity) so the number is never bare.
    display_cols: list = list(result.column_names)
    display_rows: list = list(result.rows)
    if result.status == "ok" and parsed.group_by:
        try:
            from kb.q_planner.group_by import (
                DEFAULT_GROUPBY_CARDINALITY_CAP,
                canonicalize_and_cap,
            )
            cap = DEFAULT_GROUPBY_CARDINALITY_CAP
            try:
                from kb.query.config_thresholds import resolve_query_threshold
                cap = int(await resolve_query_threshold(
                    conn, key="groupby_cardinality_cap",
                    workspace_id=workspace_id,
                    default=float(DEFAULT_GROUPBY_CARDINALITY_CAP),
                ))
            except Exception:  # noqa: BLE001
                pass
            display_cols, display_rows, gb_notes = canonicalize_and_cap(
                result.column_names, result.rows,
                group_by=parsed.group_by, aggregations=parsed.aggregations,
                cap=cap,
            )
            agg_notes.extend(gb_notes)
        except Exception:  # noqa: BLE001
            display_cols = list(result.column_names)
            display_rows = list(result.rows)

    # T3 §6.5(1) — active stated-vs-computed reconciliation for a single SUM
    # over unit rows: fetch a stated total of the same concept on the source
    # docs and surface BOTH on a material gap (never silently pick).
    if (
        result.status == "ok" and display_rows
        and parsed.from_table == "extracted_entities"
        and not parsed.group_by
        and len(parsed.aggregations) == 1
        and (parsed.aggregations[0].op or "").upper() == "SUM"
        and parsed.aggregations[0].jsonb_path is not None
    ):
        try:
            summed_key = parsed.aggregations[0].jsonb_path[1]  # (col, key, cast)
            computed = display_rows[0][0]
            if summed_key and computed is not None:
                recon_note = await _reconcile_stated_total(
                    conn, workspace_id=workspace_id, parsed=parsed,
                    computed_value=float(computed), summed_key=summed_key,
                )
                if recon_note:
                    agg_notes.append(recon_note)
        except Exception:  # noqa: BLE001
            pass

    # T3 §6.11 grain/overlap caveats (pure, surfaced — never silently change the
    # number): a SUM over heterogeneous fields, or an additive aggregate over
    # overlapping row types, may be non-additive / double-counted.
    if result.status == "ok" and display_rows:
        try:
            for _caveat in (
                _heterogeneous_sum_caveat(parsed),
                _multi_unit_type_caveat(parsed),
            ):
                if _caveat:
                    agg_notes.append(_caveat)
        except Exception:  # noqa: BLE001
            pass

    # T3 §6.6 — sanity check (the guard that REPLACES the bare faithfulness
    # exemption). A computed aggregate must clear: (a) rows actually contributed,
    # (b) a numeric aggregate isn't all-NULL (matched rows but nothing parsed —
    # the 'INR 18,400/year' case), (c) no non-finite magnitude. On failure the
    # number is NOT asserted (handled in the return below).
    n_contributing: int | None = None
    sanity_ok = True
    sanity_reasons: list[str] = []
    grain = "doc_root"
    if parsed.from_table == "extracted_entities" and any(
        f.field == "unit_type" for f in parsed.filters
    ):
        grain = "unit_type_row"
    if result.status == "ok":
        n_contributing = await _count_contributing_rows(
            conn, parsed=parsed, workspace_id=workspace_id,
            row_filters=used_rf, exclude_ids=used_ex, live_catalog=live_catalog,
        )
        has_numeric_agg = any(
            (getattr(a, "op", "") or "").upper() in ("SUM", "AVG", "MIN", "MAX")
            for a in parsed.aggregations
        )
        if not display_rows:
            sanity_ok = False
            sanity_reasons.append("the query returned no result rows")
        elif has_numeric_agg and n_contributing == 0:
            # SUM/AVG/MIN/MAX over zero rows → NULL: no number to assert. (A
            # COUNT of 0 is a VALID answer, so this only gates numeric aggs.)
            sanity_ok = False
            sanity_reasons.append("no rows matched the query — nothing to aggregate")
        elif has_numeric_agg and _aggregate_all_null(
            parsed.group_by, display_cols, display_rows,
        ):
            sanity_ok = False
            matched = n_contributing if n_contributing is not None else "some"
            sanity_reasons.append(
                f"matched {matched} row(s) but none held a parseable numeric "
                f"value to aggregate"
            )
        for r in display_rows:
            for ci in range(len(parsed.group_by or ()), len(display_cols)):
                v = r[ci] if ci < len(r) else None
                if isinstance(v, float) and (
                    v != v or v in (float("inf"), float("-inf"))
                ):
                    sanity_ok = False
                    sanity_reasons.append("aggregate produced a non-finite value")
                    break

    # Layer 10 — persist audit row (+ best-effort CSV artifact).
    # audit_queries is APPEND-ONLY (kb_app has SELECT+INSERT only), so we
    # can't UPDATE the csv_artifact_key after insert. Workflow:
    #   1. Pre-compute the audit_query_id (UUID) client-side.
    #   2. Upload the CSV under that key (best-effort; may fail silently).
    #   3. INSERT the row in one shot with the key already set.
    import uuid as _uuid
    audit_id = str(_uuid.uuid4())
    csv_key: str | None = None

    if result.status == "ok" and len(display_rows) > 0:
        csv_key = await persist_csv_artifact(
            workspace_id=workspace_id,
            audit_query_id=audit_id,
            column_names=display_cols,
            rows=display_rows,
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
            refusal_reason=(
                result.error_message
                or (None if sanity_ok else "; ".join(sanity_reasons))
            ),
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

    # T3 §6.6 — the audit envelope + "computed from N rows". The number is never
    # bare: it ships with its contributing-row count, the audited SQL id, grain,
    # and the sanity verdict.
    if n_contributing is not None and sanity_ok:
        agg_notes.append(
            f"computed from {n_contributing} contributing row(s); "
            f"audited SQL id={audit_id} (grain={grain})"
        )
    audit_envelope = {
        "n_contributing_rows": n_contributing,
        "audit_query_id": audit_id,
        "grain": grain,
        "sanity_ok": sanity_ok,
        "sanity_reasons": list(sanity_reasons),
    }

    # Source-doc hits from retrieval, kept for citation + grounding (C1). Capped
    # so the aggregate stays the headline and the prompt stays bounded.
    source_hits = [h for h in hits if h.kind != "aggregate"][:_Q_SOURCE_HITS_CAP]

    # §6.6 — sanity FAIL on an otherwise-'ok' execution: the SQL ran but produced
    # no trustworthy number (0 rows / all-NULL after cast / non-finite). Do NOT
    # assert the number — emit a typed, auditable refusal (carrying the envelope)
    # and keep the source docs. This is the guard that turns the old silent
    # "None"/bare-0 into an honest "couldn't compute" — system-enforced.
    if result.status == "ok" and not sanity_ok:
        reason = "; ".join(sanity_reasons) or "no reliable value"
        refusal = _q_refusal_hit(
            f"the aggregate could not be computed reliably: {reason}"
        )
        refusal.metadata.update({
            "audit_query_id": audit_id,
            "audit_envelope": audit_envelope,
        })
        return [refusal, *source_hits]

    # Synthesize the aggregate Hit, then KEEP the retrieved source-doc hits
    # behind it. C1: the aggregate Hit carries the computed number but no
    # source file_id, so on its own the answer cites a bare "document"
    # (label fallback) instead of the documents the number was drawn from.
    # Retrieval already surfaced those source docs (typically at rank 1)
    # with real file_ids — discarding them is what made aggregation answers
    # un-citable (and skipped the faithfulness gate). Returning the
    # aggregate result FIRST (primary answer) followed by the source-doc
    # hits lets the generator cite real documents and ground the count.
    if result.status == "ok":
        snippet = _format_aggregate_snippet(
            display_cols, display_rows,
            plan=plan.q_payload, extra_notes=agg_notes,
        )
        aggregate_hit = Hit(
            id=audit_id,
            kind="aggregate",
            score=1.0,
            snippet=snippet,
            metadata={
                "mode_applied": "Q",
                "aggregate": True,
                "audit_query_id": audit_id,
                "row_count": len(display_rows),
                "csv_artifact_key": csv_key,
                "Q_plan_id": audit_id,
                "column_names": list(display_cols),
                "agg_notes": list(agg_notes),
                "audit_envelope": audit_envelope,
                "n_contributing_rows": n_contributing,
                "sanity_ok": sanity_ok,
            },
        )
        return [aggregate_hit, *source_hits]

    # Non-ok status (timeout / row_cap / error after self-repair): refusal Hit.
    return [_q_refusal_hit(
        f"{result.status}: {result.error_message or 'no detail'}"
    )]


def _format_aggregate_snippet(
    column_names: tuple[str, ...] | list[str],
    rows: tuple[tuple, ...] | list[tuple],
    *,
    plan: dict[str, Any] | None = None,
    max_rows: int = 5,
    extra_notes: list[str] | None = None,
) -> str:
    """Human-readable rendering of the aggregate result for the generator.

    Includes the Q-plan's context (what was aggregated, doctype + field
    filters) so the generator knows the units/currency/semantics. Pre-fix
    the snippet was just `total=4418400` — the generator then had to
    GUESS whether that's USD, INR, or rupees, and which column matches
    the user's question. q033 wrong-rendered as `$4,418,400` from this
    ambiguity; q036 picked AVG when the user wanted MAX.

    Two improvements over the bare snippet:
      1. Surface the plan context (FROM, doctype, field, aggregations) so
         currency tag flows through (e.g. `total_cost_inr` → INR).
      2. Annotate column aliases with their aggregation op (`MAX(...)`,
         `AVG(...)`) so the generator can match the user's question to
         the right column ('peak' → MAX column, not AVG).
    """
    if not rows:
        return f"Aggregate query returned no rows. Columns: {list(column_names)}."
    cols = list(column_names)
    head_rows = rows[:max_rows]

    lines: list[str] = []

    if plan and isinstance(plan, dict):
        from_table = plan.get("from") or plan.get("from_table") or "?"
        # Build a one-line query summary so the LLM sees the semantic
        # context rather than just bare numbers.
        agg_strs = []
        for agg in plan.get("aggregations") or []:
            op = (agg or {}).get("op") or "?"
            field = (agg or {}).get("field") or "?"
            alias = (agg or {}).get("alias") or ""
            if alias:
                agg_strs.append(f"{op}({field}) AS {alias}")
            else:
                agg_strs.append(f"{op}({field})")
        filter_strs = []
        for f in plan.get("filters") or []:
            field = (f or {}).get("field") or "?"
            op = (f or {}).get("op") or "?"
            val = (f or {}).get("value")
            if isinstance(val, list):
                val_repr = "[" + ", ".join(repr(v) for v in val) + "]"
            else:
                val_repr = repr(val)
            filter_strs.append(f"{field} {op} {val_repr}")
        lines.append(
            f"Aggregate query: {', '.join(agg_strs) or '?'} "
            f"OVER {from_table}"
            + (f" WHERE {' AND '.join(filter_strs)}" if filter_strs else "")
        )

    lines.append(f"Result over {len(rows)} row(s):")
    for r in head_rows:
        pairs = []
        for c, v in zip(cols, r):
            pairs.append(f"{c}={v}")
        lines.append("  " + ", ".join(pairs))
    if len(rows) > max_rows:
        lines.append(f"  ... ({len(rows) - max_rows} more rows in CSV artifact)")

    # Hint the generator about column meanings — helps with peak vs avg etc.
    if plan and plan.get("aggregations"):
        hint_pairs = []
        for agg in plan.get("aggregations") or []:
            op = (agg or {}).get("op") or ""
            alias = (agg or {}).get("alias") or ""
            field = (agg or {}).get("field") or ""
            if alias and op:
                # Suggest semantic meaning from op.
                meaning = {
                    "MAX": "highest / peak / maximum",
                    "MIN": "lowest / earliest / minimum",
                    "AVG": "average / mean",
                    "SUM": "total / cumulative",
                    "COUNT": "count of rows",
                    "COUNT_DISTINCT": "count of distinct values",
                }.get(op, op.lower())
                hint_pairs.append(f"  {alias} = {meaning} of {field}")
        if hint_pairs:
            lines.append("Column meanings (use the right column for the question):")
            lines.extend(hint_pairs)

    # Currency / unit hint — proposed_fields stores `value_currency` per
    # row; when the aggregation hits an `*_inr` / `_usd` field_name the
    # generator should render with that currency, not invent $.
    if plan:
        field_filter = next(
            (f for f in (plan.get("filters") or [])
             if (f or {}).get("field") == "field_name"),
            None,
        )
        if field_filter:
            val = field_filter.get("value")
            field_names = [val] if isinstance(val, str) else (val or [])
            currency_hint = None
            for fn in field_names:
                fn_lower = str(fn).lower()
                if fn_lower.endswith("_inr") or "rupee" in fn_lower:
                    currency_hint = "INR (Indian Rupees) — use ₹ or 'INR', NEVER $"
                    break
                if fn_lower.endswith("_usd") or "dollar" in fn_lower:
                    currency_hint = "USD — use $"
                    break
                if fn_lower.endswith("_eur"):
                    currency_hint = "EUR — use €"
                    break
            if currency_hint:
                lines.append(f"Currency: {currency_hint}")

    # T3 §6.6/§6.11 — surface the audit envelope (merge / cap / reconciliation /
    # sanity). These MUST reach the user so a computed number is never bare.
    if extra_notes:
        lines.append("Notes (surface these to the user):")
        for n in extra_notes:
            lines.append(f"  - {n}")
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
    canonical entity_ids. Delegates to `_resolve_names_to_entity_ids`
    which handles UUIDs, exact match, and prefix-with-word-boundary
    suffix tolerance."""
    return await _resolve_names_to_entity_ids(
        conn, workspace_id=workspace_id, names=seeds,
    )


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

    # T1 — a manual rename is a display pointer (the stored key keeps the system
    # canonical name). Map the user's display label back to the canonical key so
    # a renamed field is still matched by the predicate below. Best-effort:
    # absence of custom labels (or a read error) leaves behavior unchanged.
    display_map: dict[str, str] = {}
    try:
        from kb.domain.fields import read_field_display_map
        raw_display = await read_field_display_map(conn, workspace_id=workspace_id)
        display_map = {_normalize_field_key(d): c for d, c in raw_display.items()}
    except Exception:
        display_map = {}

    for fid, fields in rows:
        if not isinstance(fields, dict):
            continue
        if all(_field_predicate_holds(fields, f, display_map) for f in filters):
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


def _normalize_field_key(s: str) -> str:
    """Fold a field name to a comparison key: lowercase, every run of
    non-alphanumeric chars → one underscore, strip edge underscores.
    'Interest Rate' / 'interest-rate' / 'interest_rate' → 'interest_rate'."""
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")


def _resolve_field_name(
    name: Any, fields: dict, display_map: dict[str, str] | None = None,
) -> str | None:
    """Map a planner-emitted field name to the actual stored key in this
    entity's `fields` dict — the query half of FIX 4.

    F-mode matches exactly against canonical stored keys (anchored to the
    user-declared schema names at ingest), but the LLM emits surface
    variants ('interest rate', 'rate'). Resolution order:
      0. T1 — if `display_map` (normalized user display label → canonical key)
         is given and the query name matches a user-assigned display label,
         translate it to the canonical key FIRST, then resolve that against the
         stored keys. This is how a MANUAL rename (a display pointer, not a key
         rewrite) becomes queryable without touching any stored data.
      1. exact key hit
      2. normalized equality (case / space / hyphen / underscore folded)
      3. UNAMBIGUOUS token-subset — the query's tokens are a subset of
         exactly ONE stored key's tokens ('rate' → 'interest_rate' iff it
         is the only *_rate key). >1 candidate (e.g. 'amount' vs both
         principal_amount + emi_amount) → None, so we never silently guess.
    Returns the stored key, or None (the predicate then fails closed)."""
    if not isinstance(name, str) or not name or not isinstance(fields, dict):
        return None
    # Display-label → canonical translation (manual-rename pointer).
    if display_map:
        canon = display_map.get(_normalize_field_key(name))
        if canon is not None:
            name = canon
    if name in fields:
        return name
    norm_to_key: dict[str, str] = {}
    for k in fields:
        norm_to_key.setdefault(_normalize_field_key(k), k)
    qn = _normalize_field_key(name)
    if qn in norm_to_key:
        return norm_to_key[qn]
    q_tokens = {t for t in qn.split("_") if t}
    if not q_tokens:
        return None
    candidates = [
        k for k in fields
        if q_tokens.issubset({t for t in _normalize_field_key(k).split("_") if t})
    ]
    return candidates[0] if len(candidates) == 1 else None


def _field_predicate_holds(
    fields: dict, f: dict, display_map: dict[str, str] | None = None,
) -> bool:
    """Evaluate one field predicate against an extracted_entity.fields
    dict. Recognised ops: eq, ne, lt, le, gt, ge, like, in. Unknown ops
    fail-open (return True) so we don't accidentally drop hits.

    The filter's `field` is resolved to the stored key via
    `_resolve_field_name` (canonical-name mapping + T1 display-label pointer)
    before lookup."""
    op = (f.get("op") or "eq").lower()
    val = f.get("value")
    resolved = _resolve_field_name(f.get("field"), fields, display_map)
    if resolved is None:
        return False
    actual = fields[resolved]
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
        # Fallback: for short docs that produce only one contextual_chunk
        # the per-doc RAPTOR builder has nothing to cluster and emits no
        # nodes. Surface the contextual_chunks themselves — they carry
        # Anthropic-style context prefixes, so they're partially
        # summary-like and serve as a useful S-mode surface until the
        # raptor builder is taught to emit level=0 leaves.
        raptor_hits = await _fetch_per_doc_contextual_chunks(
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


async def _fetch_per_doc_contextual_chunks(
    conn: Any, *, workspace_id: str, file_ids: list[str],
) -> list[Hit]:
    """Fallback for S-mode when no per_doc raptor_nodes exist.

    Materializes each file's `contextual_chunks` rows as Hits. The
    contextual_text already carries a context prefix generated by the
    contextualization phase, so it reads like a partial summary even
    without RAPTOR clustering on top.
    """
    if conn is None or not file_ids:
        return []
    try:
        cur = await conn.execute(
            """
            SELECT cc.id::text, cc.contextual_text, cc.file_id::text
              FROM contextual_chunks cc
              JOIN chunks c ON c.id = cc.chunk_id
             WHERE cc.workspace_id = %s
               AND cc.file_id::text = ANY(%s)
             ORDER BY c.chunk_index ASC
            """,
            (workspace_id, file_ids),
        )
        rows = await cur.fetchall()
    except Exception:
        return []
    out: list[Hit] = []
    for cc_id, text, file_id in rows:
        out.append(Hit(
            id=str(cc_id),
            kind="raptor_node",  # share modality so the citation
                                 # builder / generator prompt treat it
                                 # the same as a real raptor summary
            score=1.0,
            snippet=text or "",
            metadata={
                "level": 0,
                "scope": "per_doc_fallback",
                "file_id": file_id,
                "channel": "s_mode_fallback_contextual_chunk",
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
