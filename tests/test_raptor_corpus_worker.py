"""Phase 3e — Corpus RAPTOR worker integration tests.

RED at G3: imports `kb.workers.tasks.raptor_build_corpus_impl` which
doesn't exist yet — lands at G4.

Spec: tests/specs/phase_3e.md §3 (decisions #7, #9, #10, #13).
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from contextlib import contextmanager

import psycopg
import pytest


pytestmark = pytest.mark.asyncio


@contextmanager
def _env(**kwargs):
    prior = {k: os.environ.get(k) for k in kwargs}
    for k, v in kwargs.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in prior.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


async def _seed_workspace_with_doc_roots(
    db_url: str, *, workspace: str, n_files: int, mix_singleton: bool = False,
) -> list[str]:
    """Fabricate N files in a workspace, each at lifecycle_state='ready' with
    a fake per-doc raptor root (or a singleton contextual_chunks row if
    `mix_singleton` and i % 3 == 0).

    Returns the list of file_ids."""
    file_ids: list[str] = []
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))

        for i in range(n_files):
            file_id = str(uuid.uuid4())
            file_ids.append(file_id)
            label = f"file-{i}"
            sha = hashlib.sha256(f"{workspace}-{label}".encode()).hexdigest()
            await conn.execute(
                "INSERT INTO files (id, workspace_id, name, content_sha, object_key, "
                "mime_type, size_bytes, lifecycle_state) "
                "VALUES (%s, %s, %s, %s, %s, 'application/pdf', 100, 'ready')",
                (file_id, workspace, label, sha, f"raw_files/{sha}"),
            )
            rp_id = str(uuid.uuid4())
            await conn.execute(
                "INSERT INTO raw_pages (id, file_id, workspace_id, page_number, text, "
                "layout_json, content_sha) VALUES (%s, %s, %s, 1, %s, '{}'::jsonb, %s)",
                (rp_id, file_id, workspace, f"text-{label}", sha),
            )
            chunk_id = str(uuid.uuid4())
            await conn.execute(
                "INSERT INTO chunks (id, file_id, workspace_id, chunk_index, text, "
                "source_page_numbers, token_count, content_sha) "
                "VALUES (%s, %s, %s, 0, %s, %s, 5, %s)",
                (chunk_id, file_id, workspace, f"chunk-{label}", [1], sha),
            )
            cc_id = str(uuid.uuid4())
            await conn.execute(
                "INSERT INTO contextual_chunks (id, chunk_id, file_id, workspace_id, "
                "contextual_prefix, contextual_text, model_id, prefix_token_count, "
                "cache_creation_input_tokens, cache_read_input_tokens) "
                "VALUES (%s, %s, %s, %s, '', %s, 'identity', 0, 0, 0)",
                (cc_id, chunk_id, file_id, workspace, f"ctx-{label}"),
            )
            # Vectors with structure for clustering (bias by i % 3 to seed
            # 3 themes).
            vec = [0.0] * 3072
            vec[0] = (i % 3) * 0.5
            vec[1] = (i % 3) * 0.5
            vec[2] = 1.0 - (i % 3) * 0.5
            norm = (sum(v * v for v in vec) or 1.0) ** 0.5
            vec = [v / norm for v in vec]
            vec_literal = "[" + ",".join(repr(float(v)) for v in vec) + "]"
            await conn.execute(
                "INSERT INTO chunk_embeddings (contextual_chunk_id, file_id, "
                "workspace_id, embedding, model_id) "
                "VALUES (%s, %s, %s, %s::halfvec, 'test-mock')",
                (cc_id, file_id, workspace, vec_literal),
            )

            # Per-doc raptor_node root (skip for some files to test the
            # heterogeneous case).
            is_singleton = mix_singleton and (i % 3 == 0)
            if not is_singleton:
                rn_id = str(uuid.uuid4())
                await conn.execute(
                    "INSERT INTO raptor_nodes (id, scope, file_id, workspace_id, "
                    "level, text, embedding, cluster_id_in_level, "
                    "summarizer_model_id, embedder_model_id) "
                    "VALUES (%s, 'per_doc', %s, %s, 2, %s, %s::halfvec, 0, "
                    "'identity', 'test-mock')",
                    (rn_id, file_id, workspace, f"summary-{label}", vec_literal),
                )

        await conn.commit()
    return file_ids


# ===========================================================================
# §5.10.1 decision #7, #9 — writes scope='corpus' nodes + cross-scope edges
# ===========================================================================


async def test_raptor_build_corpus_writes_scope_corpus_nodes_and_cross_scope_edges(
    db_url_superuser,
):
    """Build a corpus tree over N=10 mixed files (mix of per-doc roots +
    singleton chunks). Assert:
      - At least one raptor_nodes row with scope='corpus' is written.
      - raptor_edges link corpus L2 nodes to BOTH raptor_nodes (multi-leaf doc
        roots) AND contextual_chunks (singleton doc roots) via the
        discriminated FK columns."""
    from kb.config import get_settings
    from kb.workers.tasks import raptor_build_corpus_impl

    workspace = str(uuid.uuid4())
    await _seed_workspace_with_doc_roots(
        db_url_superuser, workspace=workspace, n_files=10, mix_singleton=True,
    )

    with _env(
        KB_GEMINI_API_KEY=None, KB_ANTHROPIC_API_KEY=None,
        KB_DATABASE_URL=db_url_superuser,
    ):
        get_settings.cache_clear()
        await raptor_build_corpus_impl(workspace_id=workspace)
    get_settings.cache_clear()

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM raptor_nodes "
            "WHERE workspace_id = %s AND scope = 'corpus'",
            (workspace,),
        )
        (corpus_node_count,) = await cur.fetchone()
        assert corpus_node_count >= 1, "expected at least one scope='corpus' node"

        # Cross-scope edges: corpus L2 nodes pointing at per-doc raptor_nodes
        # (multi-leaf doc roots).
        cur = await conn.execute(
            "SELECT count(*) FROM raptor_edges e "
            "JOIN raptor_nodes parent ON e.parent_node_id = parent.id "
            "JOIN raptor_nodes child ON e.child_node_id = child.id "
            "WHERE parent.scope = 'corpus' AND parent.workspace_id = %s "
            "  AND child.scope = 'per_doc'",
            (workspace,),
        )
        (corpus_to_perdoc_edges,) = await cur.fetchone()
        assert corpus_to_perdoc_edges >= 1, (
            "expected corpus L2 → per-doc raptor_nodes edges (multi-leaf doc roots)"
        )

        # Cross-scope edges: corpus L2 nodes pointing at contextual_chunks
        # (singleton doc roots).
        cur = await conn.execute(
            "SELECT count(*) FROM raptor_edges e "
            "JOIN raptor_nodes parent ON e.parent_node_id = parent.id "
            "WHERE parent.scope = 'corpus' AND parent.workspace_id = %s "
            "  AND e.child_contextual_chunk_id IS NOT NULL",
            (workspace,),
        )
        (corpus_to_chunk_edges,) = await cur.fetchone()
        assert corpus_to_chunk_edges >= 1, (
            "expected corpus L2 → contextual_chunks edges (singleton doc roots)"
        )


# ===========================================================================
# §5.10.1 decision #9 — atomic rebuild replaces old rows
# ===========================================================================


async def test_raptor_build_corpus_atomic_rebuild_replaces_old_rows(
    db_url_superuser,
):
    """Running raptor_build_corpus_impl twice on the same workspace must
    DELETE existing scope='corpus' rows before INSERTing new ones — total
    count after second run equals the count after first run (not doubled)."""
    from kb.config import get_settings
    from kb.workers.tasks import raptor_build_corpus_impl

    workspace = str(uuid.uuid4())
    await _seed_workspace_with_doc_roots(
        db_url_superuser, workspace=workspace, n_files=10,
    )

    with _env(
        KB_GEMINI_API_KEY=None, KB_ANTHROPIC_API_KEY=None,
        KB_DATABASE_URL=db_url_superuser,
    ):
        get_settings.cache_clear()
        await raptor_build_corpus_impl(workspace_id=workspace)
        async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
            cur = await conn.execute(
                "SELECT count(*) FROM raptor_nodes WHERE workspace_id = %s AND scope = 'corpus'",
                (workspace,),
            )
            (after_first,) = await cur.fetchone()

        # Replay.
        await raptor_build_corpus_impl(workspace_id=workspace)
        async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
            cur = await conn.execute(
                "SELECT count(*) FROM raptor_nodes WHERE workspace_id = %s AND scope = 'corpus'",
                (workspace,),
            )
            (after_replay,) = await cur.fetchone()
    get_settings.cache_clear()

    assert after_replay == after_first, (
        f"atomic rebuild should not double rows; first={after_first} replay={after_replay}"
    )


# ===========================================================================
# §5.10.1 decision #13 — skip when N <= 1
# ===========================================================================


async def test_raptor_build_corpus_skips_when_only_one_doc(db_url_superuser):
    """A workspace with 0 or 1 doc-roots should NOT produce a corpus tree
    (no clustering makes sense for N≤1). The worker should return cleanly
    without writing any scope='corpus' rows."""
    from kb.config import get_settings
    from kb.workers.tasks import raptor_build_corpus_impl

    workspace = str(uuid.uuid4())
    await _seed_workspace_with_doc_roots(
        db_url_superuser, workspace=workspace, n_files=1,
    )

    with _env(
        KB_GEMINI_API_KEY=None, KB_ANTHROPIC_API_KEY=None,
        KB_DATABASE_URL=db_url_superuser,
    ):
        get_settings.cache_clear()
        await raptor_build_corpus_impl(workspace_id=workspace)
    get_settings.cache_clear()

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM raptor_nodes WHERE workspace_id = %s AND scope = 'corpus'",
            (workspace,),
        )
        (corpus_node_count,) = await cur.fetchone()
        assert corpus_node_count == 0, (
            f"N=1 workspace should produce no corpus tree; got {corpus_node_count} rows"
        )


# ===========================================================================
# §5.10.1 decision #10 — deterministic rebuild produces stable structure
# ===========================================================================


async def test_raptor_build_corpus_is_deterministic_across_rebuilds(
    db_url_superuser,
):
    """Re-running the corpus build on the same inputs produces a tree with
    the same structural shape: same number of nodes per level, same cluster
    sizes. (We don't assert identical node IDs since DEFAULT gen_random_uuid()
    generates new IDs; structural equivalence is what matters for
    retrieval-citation stability across rebuilds.)"""
    from kb.config import get_settings
    from kb.workers.tasks import raptor_build_corpus_impl

    workspace = str(uuid.uuid4())
    await _seed_workspace_with_doc_roots(
        db_url_superuser, workspace=workspace, n_files=12,
    )

    with _env(
        KB_GEMINI_API_KEY=None, KB_ANTHROPIC_API_KEY=None,
        KB_DATABASE_URL=db_url_superuser,
    ):
        get_settings.cache_clear()
        await raptor_build_corpus_impl(workspace_id=workspace)
        async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
            cur = await conn.execute(
                "SELECT level, count(*) FROM raptor_nodes "
                "WHERE workspace_id = %s AND scope = 'corpus' GROUP BY level ORDER BY level",
                (workspace,),
            )
            shape_after_first = await cur.fetchall()

        await raptor_build_corpus_impl(workspace_id=workspace)
        async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
            cur = await conn.execute(
                "SELECT level, count(*) FROM raptor_nodes "
                "WHERE workspace_id = %s AND scope = 'corpus' GROUP BY level ORDER BY level",
                (workspace,),
            )
            shape_after_replay = await cur.fetchall()

    get_settings.cache_clear()

    assert shape_after_first == shape_after_replay, (
        f"corpus tree shape unstable across rebuilds; "
        f"first={shape_after_first} replay={shape_after_replay}"
    )
