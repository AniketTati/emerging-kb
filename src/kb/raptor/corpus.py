"""Phase 3e — Corpus-level RAPTOR clustering + tree-build.

Per build_tracker §5.10.1 decisions:
  #1 UMAP + sklearn GaussianMixture (replaces AC since O(N²) infeasible at N=100K)
  #2 UMAP n_components=10
  #3 UMAP n_neighbors=15
  #4 GMM n_components = ceil(N / BRANCHING_FACTOR)
  #6 Heterogeneous doc-root source (per-doc roots + singleton contextual_chunks)
  #10 random_state for determinism (retrieval-citation stability)

Three surfaces:
  - `cluster_embeddings_corpus(vectors, branching_factor)` — pure function;
    UMAP reduces 3072 → 10 dim, GMM soft-clusters then takes argmax.
  - `read_doc_roots_for_workspace(conn, workspace_id)` — returns mixed list
    of (root_id, root_text, root_embedding, root_kind ∈ {'node', 'chunk'}).
    For multi-leaf files: highest per-doc raptor_nodes row.
    For singleton-leaf files: the contextual_chunks row directly.
  - `build_corpus_tree(...)` — orchestrator with same shape as 3d's
    `_build_in_memory` but using the corpus clustering function +
    heterogeneous doc-root source.

DB-bound orchestration lives in `kb.workers.tasks.raptor_build_corpus_impl`
(which provides the connection + transaction management).
"""

from __future__ import annotations

import math
import os
from typing import Awaitable, Callable

import numpy as np
from sklearn.mixture import GaussianMixture

from kb.db.pool import Connection


DEFAULT_UMAP_DIM = 10
DEFAULT_UMAP_NEIGHBORS = 15
DEFAULT_RANDOM_STATE = 42


# ---------------------------------------------------------------------------
# Pure-function corpus clustering (decision #1, #2, #3, #4, #10)
# ---------------------------------------------------------------------------


def cluster_embeddings_corpus(
    vectors: list[list[float]],
    *,
    branching_factor: int = 8,
    umap_dim: int | None = None,
    umap_neighbors: int | None = None,
    random_state: int | None = None,
) -> list[int]:
    """UMAP + GMM clustering for corpus-level (large-N) RAPTOR.

    Replaces 3d's AgglomerativeClustering since AC is O(N²) — infeasible at
    N=100K corpus roots. UMAP reduces 3072 → ~10 dim (avoids curse-of-dim
    for GMM in high-dim space); GMM soft-clusters in low-dim, hard-assigns
    each vector to its highest-probability cluster.

    Deterministic given random_state (decision #10) — retrieval citations
    must remain stable across corpus rebuilds with no new docs.

    Edge case: n=1 → returns [0]. UMAP refuses n < umap_neighbors+1, so for
    small N we skip UMAP and pass embeddings straight to GMM.
    """
    n = len(vectors)
    if n == 0:
        return []
    if n == 1:
        return [0]

    umap_dim = umap_dim if umap_dim is not None else int(
        os.environ.get("KB_RAPTOR_CORPUS_UMAP_DIM") or DEFAULT_UMAP_DIM
    )
    umap_neighbors = umap_neighbors if umap_neighbors is not None else int(
        os.environ.get("KB_RAPTOR_CORPUS_UMAP_NEIGHBORS") or DEFAULT_UMAP_NEIGHBORS
    )
    random_state = random_state if random_state is not None else int(
        os.environ.get("KB_RAPTOR_CORPUS_GMM_SEED") or DEFAULT_RANDOM_STATE
    )

    n_clusters = max(1, math.ceil(n / branching_factor))
    if n_clusters >= n:
        # Each vector its own cluster — trivial.
        return list(range(n))

    X = np.asarray(vectors, dtype=np.float32)

    # UMAP refuses n_neighbors > n_samples - 1, so cap accordingly.
    effective_neighbors = min(umap_neighbors, n - 1)

    if n > umap_dim + 1 and effective_neighbors >= 2:
        # Only run UMAP when N is large enough for it to be meaningful.
        # For small N (e.g., test fixtures with 10-50 vectors), UMAP can
        # produce degenerate results; pass embeddings straight to GMM in
        # those cases (the per-doc AC-cosine path already covers small N
        # via the 3d code path, so this is a hot path only at corpus scale).
        try:
            import umap
            reducer = umap.UMAP(
                n_components=umap_dim,
                n_neighbors=effective_neighbors,
                random_state=random_state,
                # UMAP's default metric=euclidean works on normalized halfvec
                # cosine-similar vectors (cosine ≈ euclidean on unit-norm).
            )
            X_reduced = reducer.fit_transform(X)
        except Exception:
            # UMAP can fail on some pathological inputs (e.g., all-identical
            # vectors or n too small). Fall back to GMM on raw embeddings.
            X_reduced = X
    else:
        X_reduced = X

    gmm = GaussianMixture(
        n_components=n_clusters,
        random_state=random_state,
        # `n_init=1` is fine given fixed random_state.
        n_init=1,
        covariance_type="full",
        max_iter=100,
    )
    gmm.fit(X_reduced)
    labels = gmm.predict(X_reduced)
    return [int(label) for label in labels]


# ---------------------------------------------------------------------------
# Doc-root reader — heterogeneous source (decision #6)
# ---------------------------------------------------------------------------


async def read_doc_roots_for_workspace(
    conn: Connection, *, workspace_id: str,
) -> list[tuple[str, str, list[float], str]]:
    """Return list of (root_id, root_text, root_embedding, root_kind) for
    every file in the workspace.

    For multi-leaf files (have per-doc raptor_nodes): root = highest-level
    raptor_nodes row for that file, root_kind='node'.
    For singleton-leaf files (no raptor_nodes): root = the single
    contextual_chunks row, root_kind='chunk' (embedding comes from
    chunk_embeddings JOIN).

    Returns rows in a stable order (by file_id then by root_id) so corpus
    clustering is deterministic across rebuilds.
    """
    # Multi-leaf files: their highest-level per-doc raptor_nodes row.
    cur = await conn.execute(
        """
        SELECT
            rn.id::text,
            rn.text,
            rn.embedding::text,
            'node' AS root_kind
        FROM raptor_nodes rn
        INNER JOIN (
            SELECT file_id, max(level) AS max_level
            FROM raptor_nodes
            WHERE scope = 'per_doc' AND workspace_id = %s
            GROUP BY file_id
        ) max_per_file
            ON rn.file_id = max_per_file.file_id
           AND rn.level = max_per_file.max_level
        WHERE rn.scope = 'per_doc' AND rn.workspace_id = %s
        ORDER BY rn.file_id ASC, rn.id ASC
        """,
        (workspace_id, workspace_id),
    )
    node_rows = await cur.fetchall()

    # Files that are NOT in raptor_nodes (singleton-leaf): take their
    # single contextual_chunks row (joined with chunk_embeddings).
    cur = await conn.execute(
        """
        SELECT
            cc.id::text,
            cc.contextual_text,
            ce.embedding::text,
            'chunk' AS root_kind
        FROM contextual_chunks cc
        INNER JOIN chunk_embeddings ce ON ce.contextual_chunk_id = cc.id
        WHERE cc.workspace_id = %s
          AND cc.file_id NOT IN (
              SELECT DISTINCT file_id FROM raptor_nodes
              WHERE scope = 'per_doc' AND workspace_id = %s
                AND file_id IS NOT NULL
          )
        ORDER BY cc.file_id ASC, cc.id ASC
        """,
        (workspace_id, workspace_id),
    )
    chunk_rows = await cur.fetchall()

    out: list[tuple[str, str, list[float], str]] = []
    for row in list(node_rows) + list(chunk_rows):
        root_id, text, emb_text, kind = row
        vec_str = emb_text.strip()
        if vec_str.startswith("[") and vec_str.endswith("]"):
            vec_str = vec_str[1:-1]
        vector = [float(x) for x in vec_str.split(",") if x.strip()]
        out.append((root_id, text, vector, kind))
    return out


# ---------------------------------------------------------------------------
# Tree-build orchestrator — DB-aware (decisions #4, #7, #9)
# ---------------------------------------------------------------------------


async def delete_corpus_rows_for_workspace(
    conn: Connection, *, workspace_id: str,
) -> int:
    """DELETE all scope='corpus' rows for this workspace (raptor_nodes +
    raptor_edges cascade automatically via ON DELETE CASCADE on FKs).
    Returns the count of raptor_nodes deleted.

    Must be called inside the same tx that INSERTs the new tree (decision
    #9 — atomic rebuild semantics). Uses superuser-equivalent connection
    so REVOKE DELETE on kb_app doesn't block.
    """
    cur = await conn.execute(
        "DELETE FROM raptor_nodes WHERE workspace_id = %s AND scope = 'corpus' RETURNING id",
        (workspace_id,),
    )
    rows = await cur.fetchall()
    return len(rows)
