"""I3 — corpus-finalization phase: auto-trigger after per-doc ingest settles.

The corpus-RAPTOR build used to be manual-only (POST /corpus/raptor/rebuild).
I3 wires it to fire automatically once per-doc ingest has *settled*, via
`finalize_corpus_impl`, which self-gates on `count_inflight_files`:

  - while any file in the workspace is still in-flight (non-terminal
    lifecycle_state) → no-op, a later file's completion re-fires it;
  - when every file is terminal (ready/failed/deleted) → run the build once.

These tests exercise the settle-gate directly (the per-doc chain wiring that
defers `finalize_corpus` on file → ready is integration-validated at the
finance ingest). Mirrors tests/test_raptor_corpus_worker.py for seeding.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from contextlib import contextmanager

import psycopg
import pytest

from tests.test_raptor_corpus_worker import _seed_workspace_with_doc_roots


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


async def _insert_bare_file(
    db_url: str, *, workspace: str, lifecycle_state: str
) -> str:
    """Insert a single files row in `lifecycle_state` with no doc-root — just
    enough to register as in-flight (or terminal) for the settle count."""
    file_id = str(uuid.uuid4())
    sha = hashlib.sha256(f"{workspace}-{file_id}".encode()).hexdigest()
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        await conn.execute(
            "INSERT INTO files (id, workspace_id, name, content_sha, object_key, "
            "mime_type, size_bytes, lifecycle_state) "
            "VALUES (%s, %s, %s, %s, %s, 'application/pdf', 100, %s)",
            (file_id, workspace, f"bare-{lifecycle_state}", sha,
             f"raw_files/{sha}", lifecycle_state),
        )
        await conn.commit()
    return file_id


def _corpus_env(db_url: str) -> dict:
    """Disable real LLM/embedder keys so make_summarizer/make_embedder fall
    back to the identity/mock implementations (same as the 3e worker tests)."""
    return dict(
        KB_GEMINI_API_KEY=None, KB_ANTHROPIC_API_KEY=None,
        KB_DATABASE_URL=db_url,
    )


# ===========================================================================
# count_inflight_files — only non-terminal files count
# ===========================================================================


async def test_count_inflight_files_counts_only_non_terminal(db_url_superuser):
    """ready/failed/deleted are terminal and must NOT count; every other
    lifecycle_state is in-flight and must count."""
    from kb.domain.files import count_inflight_files

    workspace = str(uuid.uuid4())
    # 3 terminal (don't count) + 2 in-flight (count).
    for state in ("ready", "failed", "deleted"):
        await _insert_bare_file(db_url_superuser, workspace=workspace, lifecycle_state=state)
    for state in ("embedded", "identity_resolving"):
        await _insert_bare_file(db_url_superuser, workspace=workspace, lifecycle_state=state)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        n = await count_inflight_files(conn, workspace_id=workspace)

    assert n == 2, f"expected 2 in-flight files, got {n}"


async def test_count_inflight_files_zero_when_all_terminal(db_url_superuser):
    """All files terminal → settled → count 0."""
    from kb.domain.files import count_inflight_files

    workspace = str(uuid.uuid4())
    await _seed_workspace_with_doc_roots(
        db_url_superuser, workspace=workspace, n_files=4,
    )  # seeds all at 'ready'

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        n = await count_inflight_files(conn, workspace_id=workspace)

    assert n == 0, f"expected 0 in-flight files, got {n}"


# ===========================================================================
# finalize_corpus_impl — settle-gate
# ===========================================================================


async def test_finalize_corpus_skips_when_not_settled(db_url_superuser):
    """With a file still in-flight, finalize_corpus_impl must NOT build the
    corpus tree (it returns early at the settle gate)."""
    from kb.config import get_settings
    from kb.workers.tasks import finalize_corpus_impl

    workspace = str(uuid.uuid4())
    # Enough ready doc-roots that a build WOULD produce corpus nodes if it ran.
    await _seed_workspace_with_doc_roots(
        db_url_superuser, workspace=workspace, n_files=6,
    )
    # One straggler still in-flight → not settled.
    await _insert_bare_file(
        db_url_superuser, workspace=workspace, lifecycle_state="embedded",
    )

    with _env(**_corpus_env(db_url_superuser)):
        get_settings.cache_clear()
        await finalize_corpus_impl(workspace_id=workspace)
    get_settings.cache_clear()

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM raptor_nodes WHERE workspace_id = %s AND scope = 'corpus'",
            (workspace,),
        )
        (corpus_node_count,) = await cur.fetchone()

    assert corpus_node_count == 0, (
        f"finalize must skip while ingest is in-flight; built {corpus_node_count} nodes"
    )


async def test_finalize_corpus_builds_when_settled(db_url_superuser):
    """With every file terminal, finalize_corpus_impl runs the corpus build —
    scope='corpus' nodes appear with no manual /corpus/raptor/rebuild call
    (the I3 done-when)."""
    from kb.config import get_settings
    from kb.workers.tasks import finalize_corpus_impl

    workspace = str(uuid.uuid4())
    await _seed_workspace_with_doc_roots(
        db_url_superuser, workspace=workspace, n_files=6,
    )  # all 'ready' → settled

    with _env(**_corpus_env(db_url_superuser)):
        get_settings.cache_clear()
        await finalize_corpus_impl(workspace_id=workspace)
    get_settings.cache_clear()

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM raptor_nodes WHERE workspace_id = %s AND scope = 'corpus'",
            (workspace,),
        )
        (corpus_node_count,) = await cur.fetchone()

    assert corpus_node_count >= 1, (
        "finalize should build the corpus tree once ingest has settled"
    )
