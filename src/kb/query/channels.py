"""Phase 8b — 6-channel parallel retrieval.

Per build_tracker §5.15.2 (12 locked decisions). Architecture §6 step 7-8.

Each channel is an async function: `(conn, *, workspace_id, query|query_vec,
limit) -> list[Hit]`. Channels run in parallel via `asyncio.gather(...,
return_exceptions=True)` in `run_all_channels` — one channel failure
degrades to `[]` for that channel; never aborts the query (decision #4 + #12).

Channels (decision #1):
  - bm25_chunks         — pg_search over contextual_chunks.contextual_text
  - bm25_raptor         — pg_search over raptor_nodes.text
  - dense_chunks        — pgvector HNSW over chunk_embeddings.embedding
  - dense_raptor        — pgvector HNSW over raptor_nodes.embedding
  - mentions_exact      — case-insensitive substring over extracted_mentions
  - sub_entities_rarity — high-rarity atomic_units; unit_type filter when
                          query mentions clause/transaction/row (decision #8)

Skipped channels (Wave B/C):
  - HippoRAG PPR — needs graph
  - ColPali — Wave C
  - Doc-chain — Design 3 not built
  - Anomaly-filter (separate from atomic-unit rarity) — Wave B
"""

from __future__ import annotations

import asyncio
from typing import Any

from kb.query.rrf import Hit


# Decision #2: top-K per channel before RRF fusion.
TOP_K_PER_CHANNEL = 20

# Decision #11: snippet truncation for downstream rendering.
# Bumped 500→1500 (Q6 fix), then 1500→2500 (Q13 fix). Both bumps were
# motivated by the contextual_prefix + contextual_text envelope: BM25
# returns prefix(~200) + text(up to ~2000) = ~2200 chars for typical
# single-chunk business docs. The earlier 1500 ceiling lopped off the
# bottom 30% — the SKILLS section of resumes, the SLA table of pricing
# sheets, the line items of invoices. 2500 covers all single-chunk
# demo docs comfortably; prompt total stays well within Gemini's
# window (top-10 × 2500 ≈ 25k chars).
_SNIPPET_MAX = 2500


# R3-supporting fix — channel queries are SAVEPOINT-isolated so a single
# channel SQL failure (paradedb syntax error on a weird query, a missing
# index, an HNSW probe edge case, an empty pg_search lexer result) doesn't
# poison the request-level transaction the orchestrator uses downstream
# for citation enrichment + chat_turn persistence + query_log audit.
#
# Pre-fix bug: each channel had its own `try/except: return []`. That
# caught the Python exception but PostgreSQL still marked the txn
# aborted; every subsequent operation on the same connection raised
# InFailedSqlTransaction. The visible symptom was citation labels
# showing "document" instead of real filenames (because the file-meta
# fetch ran in the broken txn and silently returned {}).
#
# Same pattern as `kb.query.citations.fetch_file_metas`. Centralised
# here so all six channels share it.
async def _run_channel_query(
    conn: Any,
    savepoint_name: str,
    sql: str,
    params: tuple,
) -> list[tuple] | None:
    """Run a channel SELECT inside a SAVEPOINT. Returns the rows on
    success, `None` on any failure (the caller maps that to []).

    The savepoint name must be unique per call site (PostgreSQL allows
    duplicate names but rolling back to an older one with the same
    name nukes anything stacked above — we keep names per-channel
    and avoid that hazard entirely)."""
    sp_open = False
    try:
        await conn.execute(f"SAVEPOINT {savepoint_name}")
        sp_open = True
    except Exception:
        # Can't even open the savepoint — txn already aborted upstream.
        # Bail out — the unprotected SELECT would just fail the same way.
        return None

    try:
        cur = await conn.execute(sql, params)
        rows = await cur.fetchall()
        try:
            await conn.execute(f"RELEASE SAVEPOINT {savepoint_name}")
        except Exception:
            pass
        return rows
    except Exception:
        # On failure, ROLLBACK so the outer txn stays usable.
        if sp_open:
            try:
                await conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint_name}")
                await conn.execute(f"RELEASE SAVEPOINT {savepoint_name}")
            except Exception:
                pass
        return None


# ---------------------------------------------------------------------------
# BM25 channels
# ---------------------------------------------------------------------------


# paradedb's @@@ query parser rejects bare apostrophes, question marks,
# and a few other punctuation chars — `project's` raises:
#   could not parse query string 'contextual_text:(project's target …)'
# Worse, the parse failure aborts the surrounding txn savepoint, which
# silently kills the OTHER channels (dense, etc.) that run in the same
# scope. We get a global no_hits. Strip the problem chars before
# handing the query to pg_search. Keep tokens that BM25 cares about
# (letters, digits, spaces, hyphens, underscores, periods between
# numbers); convert everything else to a space.
import re as _re
_BM25_SAFE_RE = _re.compile(r"[^A-Za-z0-9 _.\-]")
_BM25_RUNS_RE = _re.compile(r"\s+")


def _sanitize_bm25_query(q: str) -> str:
    """Make `q` safe for paradedb's @@@ operator.

    Replaces apostrophes, question marks, colons, commas, etc with
    spaces. Collapses runs of whitespace. Returns "" for queries that
    end up empty (caller should short-circuit then)."""
    if not q:
        return ""
    cleaned = _BM25_SAFE_RE.sub(" ", q)
    cleaned = _BM25_RUNS_RE.sub(" ", cleaned).strip()
    return cleaned


async def bm25_chunks_channel(
    conn: Any,
    *,
    workspace_id: str,
    query: str,
    limit: int = TOP_K_PER_CHANNEL,
) -> list[Hit]:
    """BM25 over contextual_chunks via pg_search `@@@` operator."""
    safe_query = _sanitize_bm25_query(query)
    if not safe_query:
        return []
    # Filter out chunks belonging to soft-deleted files. Otherwise re-
    # uploads (which dedupe by content_sha and soft-delete the loser)
    # leak ghost chunks into retrieval — citations point at deleted
    # rows + R1's superseded-tagging can't match by file_id.
    rows = await _run_channel_query(
        conn, "ch_bm25_chunks",
        "SELECT cc.id::text, cc.contextual_text, "
        "  paradedb.score(cc.id) AS sc, c.file_id::text "
        "FROM contextual_chunks cc "
        "JOIN chunks c ON c.id = cc.chunk_id "
        "JOIN files f ON f.id = c.file_id AND f.lifecycle_state <> 'deleted' "
        "WHERE cc.workspace_id = %s AND cc.contextual_text @@@ %s "
        "ORDER BY sc DESC LIMIT %s",
        (workspace_id, safe_query, limit),
    )
    if rows is None:
        return []
    return [
        Hit(
            id=str(r[0]),
            kind="chunk",
            score=float(r[2]),
            snippet=(r[1] or "")[:_SNIPPET_MAX],
            metadata={
                "file_id": str(r[3]),
                "level": 1,
                "channel": "bm25_chunks",
            },
        )
        for r in rows
    ]


async def bm25_raptor_channel(
    conn: Any,
    *,
    workspace_id: str,
    query: str,
    limit: int = TOP_K_PER_CHANNEL,
) -> list[Hit]:
    """BM25 over raptor_nodes.text — covers per_doc + corpus summaries."""
    safe_query = _sanitize_bm25_query(query)
    if not safe_query:
        return []
    # Live-files-only filter. raptor_nodes.file_id is nullable for
    # corpus-level summary rows (no per-file scope) — keep those by
    # using NOT EXISTS so a null file_id passes through.
    rows = await _run_channel_query(
        conn, "ch_bm25_raptor",
        "SELECT id::text, text, paradedb.score(id) AS sc, "
        "  level, scope, file_id::text "
        "FROM raptor_nodes rn "
        "WHERE workspace_id = %s AND text @@@ %s "
        "  AND (rn.file_id IS NULL OR NOT EXISTS ("
        "    SELECT 1 FROM files f "
        "     WHERE f.id = rn.file_id AND f.lifecycle_state = 'deleted')) "
        "ORDER BY sc DESC LIMIT %s",
        (workspace_id, safe_query, limit),
    )
    if rows is None:
        return []
    return [
        Hit(
            id=str(r[0]),
            kind="raptor_node",
            score=float(r[2]),
            snippet=(r[1] or "")[:_SNIPPET_MAX],
            metadata={
                "level": int(r[3]),
                "scope": r[4],
                "file_id": str(r[5]) if r[5] else None,
                "channel": "bm25_raptor",
            },
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Dense (HNSW) channels
# ---------------------------------------------------------------------------


def _vec_literal(query_vec: list[float]) -> str:
    return "[" + ",".join(repr(float(v)) for v in query_vec) + "]"


async def dense_chunks_channel(
    conn: Any,
    *,
    workspace_id: str,
    query_vec: list[float],
    limit: int = TOP_K_PER_CHANNEL,
) -> list[Hit]:
    """HNSW cosine over chunk_embeddings.embedding. Joins back to
    contextual_chunks so Hit.id == contextual_chunks.id (same dedup key as
    BM25 channel — same chunk surfaced by both → single fused Hit)."""
    if not query_vec:
        return []
    vec = _vec_literal(query_vec)
    rows = await _run_channel_query(
        conn, "ch_dense_chunks",
        "SELECT cc.id::text, cc.contextual_text, "
        "  (1.0 - (ce.embedding <=> %s::halfvec))::float AS sim, "
        "  cc.file_id::text "
        "FROM chunk_embeddings ce "
        "JOIN contextual_chunks cc ON cc.id = ce.contextual_chunk_id "
        "JOIN files f ON f.id = cc.file_id AND f.lifecycle_state <> 'deleted' "
        "WHERE ce.workspace_id = %s "
        "ORDER BY ce.embedding <=> %s::halfvec LIMIT %s",
        (vec, workspace_id, vec, limit),
    )
    if rows is None:
        return []
    return [
        Hit(
            id=str(r[0]),
            kind="chunk",
            score=float(r[2]),
            snippet=(r[1] or "")[:_SNIPPET_MAX],
            metadata={
                "file_id": str(r[3]),
                "level": 1,
                "channel": "dense_chunks",
            },
        )
        for r in rows
    ]


async def dense_raptor_channel(
    conn: Any,
    *,
    workspace_id: str,
    query_vec: list[float],
    limit: int = TOP_K_PER_CHANNEL,
) -> list[Hit]:
    """HNSW cosine over raptor_nodes.embedding."""
    if not query_vec:
        return []
    vec = _vec_literal(query_vec)
    rows = await _run_channel_query(
        conn, "ch_dense_raptor",
        "SELECT id::text, text, "
        "  (1.0 - (embedding <=> %s::halfvec))::float AS sim, "
        "  level, scope, file_id::text "
        "FROM raptor_nodes rn "
        "WHERE workspace_id = %s "
        "  AND (rn.file_id IS NULL OR NOT EXISTS ("
        "    SELECT 1 FROM files f "
        "     WHERE f.id = rn.file_id AND f.lifecycle_state = 'deleted')) "
        "ORDER BY embedding <=> %s::halfvec LIMIT %s",
        (vec, workspace_id, vec, limit),
    )
    if rows is None:
        return []
    return [
        Hit(
            id=str(r[0]),
            kind="raptor_node",
            score=float(r[2]),
            snippet=(r[1] or "")[:_SNIPPET_MAX],
            metadata={
                "level": int(r[3]),
                "scope": r[4],
                "file_id": str(r[5]) if r[5] else None,
                "channel": "dense_raptor",
            },
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Mentions channel (decision #7)
# ---------------------------------------------------------------------------


async def mentions_exact_channel(
    conn: Any,
    *,
    workspace_id: str,
    query: str,
    limit: int = TOP_K_PER_CHANNEL,
) -> list[Hit]:
    """Case-insensitive substring match on extracted_mentions.mention_text.

    Returns Hit.kind='chunk' (the contextual_chunk_id that contains the
    matched mention) so the result dedups against BM25/dense channels at
    RRF time. metadata carries `matched_mention` + `matched_type` for
    explainability in the citation envelope.
    """
    if not query.strip():
        return []
    # R2 — same source-position columns as the atomic-unit channel so
    # the citation envelope can highlight the exact mention span
    # inside the chunk. extracted_mentions.source_chunk_id is the
    # raw chunks.id (not contextual_chunks.id); UI consumes the same
    # /chunks/:id endpoint either way.
    rows = await _run_channel_query(
        conn, "ch_mentions_exact",
        "SELECT em.contextual_chunk_id::text, em.mention_text, em.mention_type, "
        "  em.file_id::text, cc.contextual_text, "
        "  em.source_chunk_id::text, em.source_char_start, em.source_char_end "
        "FROM extracted_mentions em "
        "JOIN contextual_chunks cc ON cc.id = em.contextual_chunk_id "
        "JOIN files f ON f.id = em.file_id AND f.lifecycle_state <> 'deleted' "
        "WHERE em.workspace_id = %s "
        "AND lower(em.mention_text) LIKE lower(%s) "
        "LIMIT %s",
        (workspace_id, f"%{query}%", limit),
    )
    if rows is None:
        return []
    return [
        Hit(
            id=str(r[0]),
            kind="chunk",
            score=1.0,  # deterministic substring match
            snippet=(r[4] or "")[:_SNIPPET_MAX],
            metadata={
                "file_id": str(r[3]),
                "level": 1,
                "channel": "mentions_exact",
                "matched_mention": r[1],
                "matched_type": r[2],
                # R2 — char-range location of the matched mention in
                # the source chunk. Present when the PR2 resolver
                # found it at index time.
                "source_chunk_id": r[5] if r[5] else None,
                "source_char_start": r[6] if r[6] is not None else None,
                "source_char_end": r[7] if r[7] is not None else None,
            },
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Atomic-units rarity channel (decision #8) — now reads from
# extracted_entities since the nested-entities refactor promoted each
# atomic_unit row into a typed sub_entity row.
# ---------------------------------------------------------------------------


async def sub_entities_rarity_channel(
    conn: Any,
    *,
    workspace_id: str,
    query: str,
    limit: int = TOP_K_PER_CHANNEL,
) -> list[Hit]:
    """High-rarity sub_entity rows for needle-finding scenarios.

    Decision #8: if query mentions a unit_type keyword (clause /
    transaction / row), filter to that type. Else: return top across
    all types by rarity.

    Post-refactor: sub_entity rows live in `extracted_entities` with
    `unit_type` + `rarity_score` columns (migration 0037). The
    `fields` jsonb replaces the old `parameters` jsonb; source
    positions (chunk_id + char range) live on the same row now that
    they're columns on extracted_entities too.
    """
    q_low = (query or "").lower()
    unit_filter = ""
    if "clause" in q_low:
        unit_filter = "AND ee.unit_type = 'clause'"
    elif "transaction" in q_low:
        unit_filter = "AND ee.unit_type = 'transaction'"
    elif "row" in q_low:
        unit_filter = "AND ee.unit_type = 'row'"
    rows = await _run_channel_query(
        conn, "ch_sub_entities_rarity",
        f"SELECT ee.id::text, ee.fields::text, ee.file_id::text, "
        f"  ee.unit_type, COALESCE(ee.rarity_score, 0) AS rscore, "
        f"  ee.source_chunk_id::text, ee.source_char_start, "
        f"  ee.source_char_end "
        f"FROM extracted_entities ee "
        f"JOIN files f ON f.id = ee.file_id AND f.lifecycle_state <> 'deleted' "
        f"WHERE ee.workspace_id = %s "
        f"  AND ee.unit_type IS NOT NULL "  # sub_entity rows only
        f"  {unit_filter} "
        f"ORDER BY rscore DESC NULLS LAST LIMIT %s",
        (workspace_id, limit),
    )
    if rows is None:
        return []
    return [
        Hit(
            id=str(r[0]),
            kind="sub_entity",  # kind label preserved for back-compat
                                  # with downstream renderers; underlying
                                  # row is now an extracted_entity.
            score=float(r[4]),
            snippet=(r[1] or "")[:_SNIPPET_MAX],
            metadata={
                "file_id": str(r[2]),
                "unit_type": r[3],
                "channel": "sub_entities_rarity",
                # Source positions present when the source-resolver
                # located the verbatim text in the source chunk at
                # index time. UI uses these to slice exact snippets.
                "source_chunk_id": r[5] if r[5] else None,
                "source_char_start": r[6] if r[6] is not None else None,
                "source_char_end": r[7] if r[7] is not None else None,
            },
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


async def run_all_channels(
    conn: Any,
    *,
    workspace_id: str,
    query: str,
    query_vec: list[float],
    limit: int = TOP_K_PER_CHANNEL,
    bm25_query: str | None = None,
) -> dict[str, list[Hit]]:
    """Run all 6 channels in parallel via asyncio.gather. Returns
    {channel_name: hits}. Failed channels degrade to [] (decision #4 + #12).

    Caller (Phase 8f orchestrator) feeds the values() into rrf_fuse.

    `bm25_query` is the optional vocabulary-expanded form of `query`
    used ONLY for the BM25 channels (Design 6 §"Pipeline integration").
    Dense channels keep `query_vec` (built from the original) so vector
    space stays clean — augmenting with OR-of-synonyms would pollute
    the embedding. When None, BM25 channels also use `query`.

    NOTE: this function looks up the 6 channel functions by name at
    runtime via module globals so monkeypatch of any one channel (e.g.,
    `monkeypatch.setattr(channels, "bm25_chunks_channel", broken)`) is
    honored. Direct function references would freeze the original at import.
    """
    import kb.query.channels as _self  # local alias for monkeypatch lookup

    # Define call shapes — each gets its appropriate kwargs.
    text_channels = {
        "bm25_chunks": "bm25_chunks_channel",
        "bm25_raptor": "bm25_raptor_channel",
        "mentions_exact": "mentions_exact_channel",
        "sub_entities_rarity": "sub_entities_rarity_channel",
    }
    vec_channels = {
        "dense_chunks": "dense_chunks_channel",
        "dense_raptor": "dense_raptor_channel",
    }

    # BM25 channels get the (optionally) vocab-expanded query. Other
    # text channels (mentions_exact, sub_entities_rarity) use the
    # original — they don't benefit from OR-of-synonyms since they
    # match against entity tables, not free-text.
    bm25_text = bm25_query or query
    tasks: dict[str, Any] = {}
    for name, attr in text_channels.items():
        fn = getattr(_self, attr)
        text_for_channel = bm25_text if name.startswith("bm25_") else query
        tasks[name] = fn(
            conn, workspace_id=workspace_id,
            query=text_for_channel, limit=limit,
        )
    for name, attr in vec_channels.items():
        fn = getattr(_self, attr)
        tasks[name] = fn(conn, workspace_id=workspace_id, query_vec=query_vec, limit=limit)

    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    out: dict[str, list[Hit]] = {}
    for name, result in zip(tasks.keys(), results, strict=True):
        if isinstance(result, BaseException):
            out[name] = []
        else:
            out[name] = result
    return out
