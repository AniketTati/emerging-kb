"""Phase 3d — RAPTOR worker integration tests (testcontainers DB + real worker).

RED at G3: imports `kb.workers.tasks.raptor_build_file_impl` +
`kb.domain.raptor` + migration 0012 + the widened `files.lifecycle_state`
CHECK including `'raptor_building'` all land at G4.

Spec: tests/specs/phase_3d.md §3 (decisions #9, #10, #12, #13, #14).
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager

import psycopg
import pytest

from tests.test_files_crud import _TINY_PDF


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


@pytest.fixture
def test_workspace() -> str:
    return str(uuid.uuid4())


def headers(workspace: str, *, idempotency_key: str | None = None) -> dict[str, str]:
    h = {"X-Test-Workspace": workspace}
    if idempotency_key is not None:
        h["Idempotency-Key"] = idempotency_key
    return h


async def _post_parse_chunk_contextualize_embed(client, workspace: str) -> str:
    """POST tiny.pdf, drive it through to lifecycle_state='embedded', return id."""
    from kb.workers.tasks import (
        chunk_file_impl,
        contextualize_file_impl,
        embed_file_impl,
        parse_file_impl,
    )

    resp = await client.post(
        "/files",
        files={"file": ("raptor-input.pdf", _TINY_PDF, "application/pdf")},
        headers=headers(workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 201, resp.text
    fid = resp.json()["id"]
    await parse_file_impl(fid)
    await chunk_file_impl(fid)
    with _env(KB_ANTHROPIC_API_KEY=None):
        await contextualize_file_impl(fid)
    with _env(KB_GEMINI_API_KEY=None):
        await embed_file_impl(fid)
    return fid


# ===========================================================================
# §5.10 decision #9, #10 — L2 nodes + discriminated edge FKs
# ===========================================================================


async def test_raptor_build_file_impl_writes_l2_nodes_and_edges(
    client, test_workspace, db_url_superuser
):
    """End-to-end: a file at lifecycle_state='embedded' → raptor_build →
    raptor_nodes rows at level >= 2 + raptor_edges linking to contextual_chunks."""
    from kb.workers.tasks import raptor_build_file_impl

    fid = await _post_parse_chunk_contextualize_embed(client, test_workspace)
    with _env(KB_GEMINI_API_KEY=None, KB_ANTHROPIC_API_KEY=None):
        # Identity Summarizer + DeterministicMockEmbedder
        await raptor_build_file_impl(fid)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        # At minimum one L2 node (the root for tiny.pdf's small chunk count).
        cur = await conn.execute(
            "SELECT count(*) FROM raptor_nodes WHERE file_id = %s AND level >= 2", (fid,)
        )
        (l2_plus_count,) = await cur.fetchone()
        assert l2_plus_count >= 1, "expected at least one raptor_nodes L2+ row"

        # L1 leaves are NOT denormalized into raptor_nodes (decision #9).
        cur = await conn.execute(
            "SELECT count(*) FROM raptor_nodes WHERE file_id = %s AND level = 1", (fid,)
        )
        (l1_count,) = await cur.fetchone()
        assert l1_count == 0, "raptor_nodes must NOT contain L1 — leaves stay in contextual_chunks"

        # Edges: L2 nodes point at contextual_chunks via child_contextual_chunk_id.
        cur = await conn.execute(
            """
            SELECT count(*) FROM raptor_edges e
            JOIN raptor_nodes n ON e.parent_node_id = n.id
            WHERE n.file_id = %s
              AND n.level = 2
              AND e.child_contextual_chunk_id IS NOT NULL
              AND e.child_node_id IS NULL
            """,
            (fid,),
        )
        (l2_edges,) = await cur.fetchone()
        assert l2_edges >= 1, (
            "expected L2 nodes to have edges with child_contextual_chunk_id set "
            "(not child_node_id) — decision #10 discriminated FK"
        )

        # All raptor_nodes for this file have scope='per_doc' (decision #16).
        cur = await conn.execute(
            "SELECT DISTINCT scope FROM raptor_nodes WHERE file_id = %s", (fid,)
        )
        scopes = [row[0] for row in await cur.fetchall()]
        assert scopes == ["per_doc"], f"expected scope=per_doc; got {scopes}"


# ===========================================================================
# §5.10 decision #12 — embedded → raptor_building → ready lifecycle
# ===========================================================================


async def test_raptor_build_writes_raptor_build_done_lifecycle_event(
    client, test_workspace
):
    """Lifecycle history must show the full chain including the intermediate
    raptor_building state. Both events (`raptor_build_started`,
    `raptor_build_done`) get appended."""
    from kb.workers.tasks import raptor_build_file_impl

    fid = await _post_parse_chunk_contextualize_embed(client, test_workspace)
    with _env(KB_GEMINI_API_KEY=None, KB_ANTHROPIC_API_KEY=None):
        await raptor_build_file_impl(fid)

    resp = await client.get(f"/files/{fid}", headers=headers(test_workspace))
    body = resp.json()
    assert body["lifecycle_state"] == "ready"

    events = body["lifecycle"]
    event_pairs = [(e["from_state"], e["to_state"], e["event"]) for e in events]

    # The chain must include both intermediate-state transitions.
    assert ("embedded", "raptor_building", "raptor_build_started") in event_pairs, (
        f"missing raptor_build_started; got {event_pairs}"
    )
    assert ("raptor_building", "ready", "raptor_build_done") in event_pairs, (
        f"missing raptor_build_done; got {event_pairs}"
    )

    # raptor_build_done payload shape per §5.10 plan + api_contracts §5.3.
    done_event = next(e for e in events if e["event"] == "raptor_build_done")
    payload = done_event["payload"]
    for key in ("leaf_count", "levels_built", "summarizer_model_id", "embedder_model_id"):
        assert key in payload, f"missing {key!r} in raptor_build_done payload"


# ===========================================================================
# §5.10 decisions #11 + #12 — idempotency on already-ready
# ===========================================================================


async def test_raptor_build_is_idempotent_on_already_ready(
    client, test_workspace, db_url_superuser
):
    """Re-running raptor_build_file_impl on a file already at lifecycle_state='ready'
    is a no-op: no duplicate raptor_build_done event, no duplicate raptor_nodes."""
    from kb.workers.tasks import raptor_build_file_impl

    fid = await _post_parse_chunk_contextualize_embed(client, test_workspace)
    with _env(KB_GEMINI_API_KEY=None, KB_ANTHROPIC_API_KEY=None):
        await raptor_build_file_impl(fid)
        # Capture state after the first build.
        async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
            cur = await conn.execute(
                "SELECT count(*) FROM raptor_nodes WHERE file_id = %s", (fid,)
            )
            (nodes_after_first,) = await cur.fetchone()

        # Replay.
        await raptor_build_file_impl(fid)

        # No duplicate nodes.
        async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
            cur = await conn.execute(
                "SELECT count(*) FROM raptor_nodes WHERE file_id = %s", (fid,)
            )
            (nodes_after_replay,) = await cur.fetchone()
        assert nodes_after_replay == nodes_after_first

    # No duplicate raptor_build_done event.
    resp = await client.get(f"/files/{fid}", headers=headers(test_workspace))
    done_events = [e for e in resp.json()["lifecycle"] if e["event"] == "raptor_build_done"]
    assert len(done_events) == 1, f"expected 1 raptor_build_done; got {len(done_events)}"


# ===========================================================================
# §5.10 decision #13 — embed_file_impl chains raptor_build_file via defer
# ===========================================================================


async def test_embed_file_impl_chains_raptor_build_via_defer(
    client, test_workspace, db_url_superuser
):
    """embed_file_impl's success path must defer raptor_build_file in a
    SEPARATE Procrastinate transaction (matching the 3a→3b, 3b→3c pattern).
    After embed_file_impl returns, there must be exactly one queued
    raptor_build_file job for this file_id."""
    from kb.workers.tasks import (
        chunk_file_impl,
        contextualize_file_impl,
        embed_file_impl,
        parse_file_impl,
    )

    resp = await client.post(
        "/files",
        files={"file": ("chain-input.pdf", _TINY_PDF, "application/pdf")},
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    fid = resp.json()["id"]
    await parse_file_impl(fid)
    await chunk_file_impl(fid)
    with _env(KB_ANTHROPIC_API_KEY=None):
        await contextualize_file_impl(fid)
    with _env(KB_GEMINI_API_KEY=None):
        await embed_file_impl(fid)

    # Procrastinate stores deferred jobs in procrastinate_jobs.
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            """
            SELECT count(*) FROM procrastinate_jobs
            WHERE task_name = 'raptor_build_file'
              AND args ->> 'file_id' = %s
              AND status IN ('todo', 'doing', 'succeeded')
            """,
            (fid,),
        )
        (raptor_jobs,) = await cur.fetchone()
        assert raptor_jobs == 1, (
            f"embed_file_impl must chain exactly one raptor_build_file defer "
            f"for {fid}; found {raptor_jobs}"
        )


# ===========================================================================
# §5.10 decision #14 — failure mode (raptor_building → failed)
# ===========================================================================


async def test_raptor_build_failure_writes_failed_event(
    client, test_workspace, monkeypatch
):
    """Inject a failure in the Summarizer; assert raptor_building→failed
    transition with event='raptor_build_failed' + error_class in payload."""
    from kb.workers.tasks import raptor_build_file_impl

    fid = await _post_parse_chunk_contextualize_embed(client, test_workspace)

    # Force the Summarizer factory to return an object whose summarize()
    # raises. The worker must catch + write raptor_building→failed.
    class _ExplodingSummarizer:
        async def summarize(self, *, texts, doc_context=None):
            raise RuntimeError("simulated summarizer outage")

    monkeypatch.setattr(
        "kb.summarization.make_summarizer",
        lambda: _ExplodingSummarizer(),
    )

    with _env(KB_GEMINI_API_KEY=None):
        await raptor_build_file_impl(fid)

    resp = await client.get(f"/files/{fid}", headers=headers(test_workspace))
    body = resp.json()
    assert body["lifecycle_state"] == "failed"

    last_event = body["lifecycle"][-1]
    assert last_event["from_state"] == "raptor_building"
    assert last_event["to_state"] == "failed"
    assert last_event["event"] == "raptor_build_failed"
    payload = last_event["payload"]
    assert "error_class" in payload
    assert "message" in payload
