"""T2 Phase B — channel file_scope + confidence-weighted scope + relevance-widen.

Two layers:
  - DB tests: each retrieval channel honours `file_scope` (HARD pre-filter);
    the RAPTOR channels EXCLUDE corpus-level (file_id IS NULL) nodes under a
    scope (§6.8); a dense channel surfaces an in-scope chunk even when an
    out-of-scope chunk is the global nearest (the "broaden" mechanism).
  - Pure tests: Orchestrator._retrieve_with_scope hard / widen / soft logic,
    via a monkeypatched _retrieve_and_rerank (no real components / DB).
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

pytestmark = pytest.mark.asyncio


def _vec_literal(idx: int) -> str:
    v = [0.0] * 3072
    v[idx] = 1.0
    return "[" + ",".join(repr(float(x)) for x in v) + "]"


async def _seed_file_chunk(conn, ws: str, *, label: str, text: str) -> tuple[str, str]:
    """file → chunk → contextual_chunk; return (file_id, contextual_chunk_id)."""
    fid = str(uuid.uuid4())
    sha = (uuid.uuid4().hex * 2)[:64]
    await conn.execute(
        "INSERT INTO files (id, workspace_id, name, content_sha, object_key, "
        "mime_type, size_bytes, lifecycle_state) "
        "VALUES (%s, %s, %s, %s, %s, 'application/pdf', 100, 'ready')",
        (fid, ws, f"{label}.pdf", sha, f"raw_files/{sha}"),
    )
    chunk_id = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO chunks (id, file_id, workspace_id, chunk_index, text, "
        "source_page_numbers, token_count, content_sha) "
        "VALUES (%s, %s, %s, 0, %s, %s, 5, %s)",
        (chunk_id, fid, ws, text, [1], (uuid.uuid4().hex * 2)[:64]),
    )
    cc_id = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO contextual_chunks (id, chunk_id, file_id, workspace_id, "
        "contextual_prefix, contextual_text, model_id, prefix_token_count, "
        "cache_creation_input_tokens, cache_read_input_tokens) "
        "VALUES (%s, %s, %s, %s, '', %s, 'identity', 0, 0, 0)",
        (cc_id, chunk_id, fid, ws, text),
    )
    return fid, cc_id


# ===========================================================================
# Channel file_scope — DB
# ===========================================================================


async def test_bm25_chunks_channel_respects_file_scope(client, db_url_superuser):
    from kb.query.channels import bm25_chunks_channel
    ws = str(uuid.uuid4())
    marker = "zqxmarkertoken"
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (ws,))
        a, _ = await _seed_file_chunk(conn, ws, label="A", text=f"alpha {marker} body")
        b, _ = await _seed_file_chunk(conn, ws, label="B", text=f"beta {marker} body")
        await conn.commit()
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (ws,))
        # unscoped → both files
        unscoped = await bm25_chunks_channel(conn, workspace_id=ws, query=marker, limit=10)
        files_unscoped = {(h.metadata or {}).get("file_id") for h in unscoped}
        # scoped to A → only A
        scoped = await bm25_chunks_channel(
            conn, workspace_id=ws, query=marker, limit=10, file_scope={a},
        )
        files_scoped = {(h.metadata or {}).get("file_id") for h in scoped}
    assert {a, b} <= files_unscoped
    assert files_scoped == {a}


async def test_raptor_channel_excludes_corpus_null_under_scope(client, db_url_superuser):
    from kb.query.channels import bm25_raptor_channel
    ws = str(uuid.uuid4())
    marker = "zqxraptortoken"
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (ws,))
        fid, _ = await _seed_file_chunk(conn, ws, label="R", text="body")
        # per-doc raptor node (file_id set)
        await conn.execute(
            "INSERT INTO raptor_nodes (scope, file_id, workspace_id, level, text, "
            "embedding, cluster_id_in_level, summarizer_model_id, embedder_model_id) "
            "VALUES ('per_doc', %s, %s, 2, %s, %s::halfvec, 0, 'identity', 'mock')",
            (fid, ws, f"per-doc {marker} summary", _vec_literal(0)),
        )
        # corpus-level raptor node (file_id NULL)
        await conn.execute(
            "INSERT INTO raptor_nodes (scope, file_id, workspace_id, level, text, "
            "embedding, cluster_id_in_level, summarizer_model_id, embedder_model_id) "
            "VALUES ('corpus', NULL, %s, 3, %s, %s::halfvec, 0, 'identity', 'mock')",
            (ws, f"corpus {marker} summary", _vec_literal(0)),
        )
        await conn.commit()
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (ws,))
        unscoped = await bm25_raptor_channel(conn, workspace_id=ws, query=marker, limit=10)
        scoped = await bm25_raptor_channel(
            conn, workspace_id=ws, query=marker, limit=10, file_scope={fid},
        )
    # unscoped sees BOTH the per-doc and the corpus (NULL) node...
    assert any((h.metadata or {}).get("file_id") is None for h in unscoped)
    # ...but a doc-scope EXCLUDES the corpus-level NULL node (§6.8).
    assert scoped, "scoped raptor should still return the per-doc node"
    assert all((h.metadata or {}).get("file_id") == fid for h in scoped)


async def test_dense_chunks_scope_surfaces_in_scope_over_global_nearest(
    client, db_url_superuser,
):
    """The 'broaden' mechanism: an in-scope chunk is returned even when an
    out-of-scope chunk is the global nearest neighbour."""
    from kb.query.channels import dense_chunks_channel
    ws = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (ws,))
        a, cc_a = await _seed_file_chunk(conn, ws, label="A", text="alpha")
        b, cc_b = await _seed_file_chunk(conn, ws, label="B", text="beta")
        # Query is one-hot@0. B matches exactly (cosine 1.0) → global nearest.
        # A points in an orthogonal direction (one-hot@1, cosine 0) → globally
        # the WORST match, so it would never rank without the scope filter.
        await conn.execute(
            "INSERT INTO chunk_embeddings (contextual_chunk_id, file_id, workspace_id, "
            "embedding, model_id) VALUES (%s, %s, %s, %s::halfvec, 'mock')",
            (cc_b, b, ws, _vec_literal(0)),
        )
        await conn.execute(
            "INSERT INTO chunk_embeddings (contextual_chunk_id, file_id, workspace_id, "
            "embedding, model_id) VALUES (%s, %s, %s, %s::halfvec, 'mock')",
            (cc_a, a, ws, _vec_literal(1)),
        )
        await conn.commit()
    query_vec = [1.0] + [0.0] * 3071
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (ws,))
        unscoped = await dense_chunks_channel(conn, workspace_id=ws, query_vec=query_vec, limit=10)
        scoped = await dense_chunks_channel(
            conn, workspace_id=ws, query_vec=query_vec, limit=10, file_scope={a},
        )
    # Global nearest is B; scoping to A still surfaces A's chunk.
    assert unscoped and (unscoped[0].metadata or {}).get("file_id") == b
    assert scoped and all((h.metadata or {}).get("file_id") == a for h in scoped)


async def test_mentions_channel_respects_file_scope(client, db_url_superuser):
    from kb.query.channels import mentions_exact_channel
    ws = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (ws,))
        a, cc_a = await _seed_file_chunk(conn, ws, label="A", text="body")
        b, cc_b = await _seed_file_chunk(conn, ws, label="B", text="body")
        for fid, cc in ((a, cc_a), (b, cc_b)):
            await conn.execute(
                "INSERT INTO extracted_mentions (contextual_chunk_id, file_id, "
                "workspace_id, mention_text, mention_type, model_id) "
                "VALUES (%s, %s, %s, 'Globex Corp', 'ORG', 'identity')",
                (cc, fid, ws),
            )
        await conn.commit()
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (ws,))
        scoped = await mentions_exact_channel(
            conn, workspace_id=ws, query="Globex", limit=10, file_scope={a},
        )
    assert scoped and all((h.metadata or {}).get("file_id") == a for h in scoped)


# ===========================================================================
# Orchestrator._retrieve_with_scope — pure (monkeypatched retrieval)
# ===========================================================================


def _hit(cid: str, file_id: str, score: float):
    from kb.query.rrf import Hit
    return Hit(id=cid, kind="chunk", score=score, snippet="", metadata={"file_id": file_id})


def _bare_orchestrator(scoped_hits, unscoped_hits):
    from kb.query.orchestrator import Orchestrator
    o = Orchestrator.__new__(Orchestrator)

    async def fake_retrieve(*, query, rewrites, workspace_id, conn, emit=None, file_scope=None):
        return list(scoped_hits) if file_scope is not None else list(unscoped_hits)

    o._retrieve_and_rerank = fake_retrieve  # type: ignore[attr-defined]
    return o


def _rewrites():
    from kb.query.rewriter import Rewrites
    return Rewrites(original="q", step_back="", hyde="", query2doc="")


def _pred(scope, conf):
    from kb.query.structured_prefilter import ResolvedPredicate
    return ResolvedPredicate(
        file_scope=(None if scope is None else frozenset(scope)),
        overall_confidence=conf,
    )


async def test_scope_all_is_plain_retrieval():
    unscoped = [_hit(f"c{i}", "Z", 0.5) for i in range(6)]
    o = _bare_orchestrator(scoped_hits=[], unscoped_hits=unscoped)
    hits, mode = await o._retrieve_with_scope(
        query="q", rewrites=_rewrites(), workspace_id="w", conn=None,
        predicate=_pred(None, 0.0),
    )
    assert mode == "all" and len(hits) == 6


async def test_hard_scope_returns_scoped_when_strong():
    scoped = [_hit(f"a{i}", "A", 0.8) for i in range(6)]      # ≥ MIN, strong
    unscoped = [_hit(f"b{i}", "B", 0.9) for i in range(6)]
    o = _bare_orchestrator(scoped_hits=scoped, unscoped_hits=unscoped)
    hits, mode = await o._retrieve_with_scope(
        query="q", rewrites=_rewrites(), workspace_id="w", conn=None,
        predicate=_pred({"A"}, 0.9),
    )
    assert mode == "hard"
    assert {(h.metadata or {}).get("file_id") for h in hits} == {"A"}


async def test_hard_scope_widens_when_thin():
    scoped = [_hit("a0", "A", 0.8)]                            # < MIN_SCOPED_HITS
    unscoped = [_hit(f"b{i}", "B", 0.7) for i in range(6)]
    o = _bare_orchestrator(scoped_hits=scoped, unscoped_hits=unscoped)
    hits, mode = await o._retrieve_with_scope(
        query="q", rewrites=_rewrites(), workspace_id="w", conn=None,
        predicate=_pred({"A"}, 0.9),
    )
    assert mode == "hard_widened"
    files = {(h.metadata or {}).get("file_id") for h in hits}
    assert "A" in files and "B" in files                      # union, scoped kept


async def test_hard_scope_widens_when_weak_relevance():
    scoped = [_hit(f"a{i}", "A", 0.01) for i in range(6)]      # ≥ MIN but weak
    unscoped = [_hit(f"b{i}", "B", 0.9) for i in range(6)]
    o = _bare_orchestrator(scoped_hits=scoped, unscoped_hits=unscoped)
    hits, mode = await o._retrieve_with_scope(
        query="q", rewrites=_rewrites(), workspace_id="w", conn=None,
        predicate=_pred({"A"}, 0.9),
    )
    assert mode == "hard_widened"
    # The strong unscoped answer is now reachable (top of the merged set).
    assert (hits[0].metadata or {}).get("file_id") == "B"


async def test_soft_scope_boosts_in_scope_without_hiding():
    # B is globally top, but A (in scope) is close; the soft boost lifts A.
    unscoped = [_hit("b0", "B", 0.50), _hit("a0", "A", 0.48), _hit("c0", "C", 0.20)]
    o = _bare_orchestrator(scoped_hits=[], unscoped_hits=unscoped)
    hits, mode = await o._retrieve_with_scope(
        query="q", rewrites=_rewrites(), workspace_id="w", conn=None,
        predicate=_pred({"A"}, 0.50),                          # < SCOPE_CONF_HARD → soft
    )
    assert mode == "soft"
    assert (hits[0].metadata or {}).get("file_id") == "A"      # boosted to front
    assert {(h.metadata or {}).get("file_id") for h in hits} == {"A", "B", "C"}  # none hidden


async def test_union_hits_keeps_all_scoped():
    from kb.query.orchestrator import Orchestrator
    scoped = [_hit("a0", "A", 0.1), _hit("a1", "A", 0.2)]
    unscoped = [_hit("b0", "B", 0.9), _hit("a0", "A", 0.1)]    # a0 overlaps
    merged = Orchestrator._union_hits(scoped, unscoped)
    ids = [h.id for h in merged]
    assert "a0" in ids and "a1" in ids and "b0" in ids
    assert ids.count("a0") == 1                                # deduped by (id, kind)
    assert ids[0] == "b0"                                      # sorted by score desc
