"""Phase 3e — Corpus RAPTOR clustering + doc-root reader unit tests.

RED at G3: imports `kb.raptor.corpus.{cluster_embeddings_corpus,
read_doc_roots_for_workspace}` which don't exist yet — land at G4.

Spec: tests/specs/phase_3e.md §3 (decisions #1, #4, #6, #10).
"""

from __future__ import annotations

import hashlib
import math
import os
import uuid
from contextlib import contextmanager

import psycopg
import pytest


# Only the heterogeneous-kinds test is async (needs DB); the cluster
# functions are pure sync. Mark async tests explicitly instead of with
# a module-level pytestmark.


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


def _seeded_vectors(n: int, dim: int = 3072, *, seed: str = "raptor-corpus") -> list[list[float]]:
    """Deterministic pseudo-random unit-norm vectors."""
    vectors: list[list[float]] = []
    for i in range(n):
        raw = []
        for j in range(dim):
            h = hashlib.sha256(f"{seed}:{i}:{j}".encode("utf-8")).digest()
            raw.append((h[0] / 255.0) * 2.0 - 1.0)
        norm = math.sqrt(sum(v * v for v in raw)) or 1.0
        vectors.append([v / norm for v in raw])
    return vectors


# ===========================================================================
# §5.10.1 decision #1 — UMAP+GMM clustering returns labels
# ===========================================================================


def test_cluster_embeddings_corpus_returns_one_label_per_vector():
    """Output length must equal input length; labels are valid cluster ids
    in the range [0, n_clusters)."""
    from kb.raptor.corpus import cluster_embeddings_corpus

    # Need enough vectors for UMAP — n_neighbors default is 15, so N must be
    # at least n_neighbors + 1 to avoid UMAP degenerate cases. Use N=50.
    vectors = _seeded_vectors(50)
    labels = cluster_embeddings_corpus(vectors, branching_factor=8)

    assert len(labels) == 50
    n_clusters = max(labels) + 1
    # n_clusters = ceil(50/8) = 7
    assert n_clusters == 7
    assert all(0 <= label < n_clusters for label in labels)


def test_cluster_embeddings_corpus_branching_arithmetic():
    """n_clusters == ceil(n/branching) — same arithmetic as per-doc but with
    UMAP+GMM as the algorithm."""
    from kb.raptor.corpus import cluster_embeddings_corpus

    # n=100, branching=8 → ceil(100/8) = 13
    labels_100 = cluster_embeddings_corpus(_seeded_vectors(100), branching_factor=8)
    assert max(labels_100) + 1 == 13

    # n=80, branching=4 → ceil(80/4) = 20
    labels_80 = cluster_embeddings_corpus(_seeded_vectors(80), branching_factor=4)
    assert max(labels_80) + 1 == 20


# ===========================================================================
# §5.10.1 decision #10 — determinism via random_state
# ===========================================================================


def test_cluster_embeddings_corpus_is_deterministic():
    """Same input vectors → same cluster assignment. Required so corpus tree
    rebuilds produce stable structure for retrieval-citation stability across
    rebuilds when no new docs were added (decision #10)."""
    from kb.raptor.corpus import cluster_embeddings_corpus

    vectors = _seeded_vectors(40)
    labels_a = cluster_embeddings_corpus(vectors, branching_factor=8)
    labels_b = cluster_embeddings_corpus(vectors, branching_factor=8)
    assert labels_a == labels_b


# ===========================================================================
# §5.10.1 decision #6 — heterogeneous doc-root source
# ===========================================================================


@pytest.mark.asyncio
async def test_read_doc_roots_returns_heterogeneous_kinds(client, db_url_superuser):
    """For a workspace mixing multi-leaf files (have per-doc raptor_nodes
    roots) and singleton-leaf files (have only contextual_chunks), the
    reader returns BOTH kinds with a discriminator `root_kind` ∈ {'node', 'chunk'}.

    This is the input to corpus clustering — every doc must be represented,
    including singletons that the per-doc tree-builder correctly skipped.
    """
    from kb.raptor.corpus import read_doc_roots_for_workspace

    workspace = str(uuid.uuid4())

    # Seed via direct SQL: 2 multi-leaf files (each gets a fake L2 raptor_node
    # root) + 1 singleton-leaf file (just one contextual_chunks row, no
    # raptor_nodes).
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))

        for label in ("multi-1", "multi-2", "singleton-3"):
            file_id = str(uuid.uuid4())
            sha = hashlib.sha256(label.encode()).hexdigest()
            await conn.execute(
                "INSERT INTO files (id, workspace_id, name, content_sha, object_key, "
                "mime_type, size_bytes, lifecycle_state) "
                "VALUES (%s, %s, %s, %s, %s, 'application/pdf', 100, 'ready')",
                (file_id, workspace, label, sha, f"raw_files/{sha}"),
            )
            # Every file needs at least one raw_page (FK by file_lifecycle later
            # but not by chunks). Then 1 chunk + 1 contextual_chunk + 1
            # chunk_embedding (for the singleton, that's all we add).
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
            vec = [0.0] * 3072
            vec[0] = 1.0
            vec_literal = "[" + ",".join(repr(float(v)) for v in vec) + "]"
            await conn.execute(
                "INSERT INTO chunk_embeddings (contextual_chunk_id, file_id, "
                "workspace_id, embedding, model_id) "
                "VALUES (%s, %s, %s, %s::halfvec, 'test-mock')",
                (cc_id, file_id, workspace, vec_literal),
            )

            # For the multi-leaf files, add a fake L2 raptor_node (the per-doc
            # root that 3d would have written). The singleton file gets NO
            # raptor_nodes row — its doc-root IS its contextual_chunks row.
            if label.startswith("multi"):
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

        roots = await read_doc_roots_for_workspace(conn, workspace_id=workspace)

    # Expect 3 doc-roots: 2 with root_kind='node' + 1 with root_kind='chunk'.
    assert len(roots) == 3, f"expected 3 doc-roots; got {len(roots)}"
    kinds = sorted(r[3] for r in roots)  # (root_id, text, embedding, root_kind)
    assert kinds == ["chunk", "node", "node"], f"expected ['chunk','node','node']; got {kinds}"
