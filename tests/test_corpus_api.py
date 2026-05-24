"""Phase 3e — Corpus RAPTOR API tests.

RED at G3: the `kb.api.corpus` router doesn't exist yet; the
POST /corpus/raptor/rebuild route is not mounted in kb.api.main —
both land at G4.

Spec: tests/specs/phase_3e.md §3 (decisions #11, #12 + API error cases).
"""

from __future__ import annotations

import hashlib
import uuid

import psycopg
import pytest


pytestmark = pytest.mark.asyncio


@pytest.fixture
def test_workspace() -> str:
    return str(uuid.uuid4())


def headers(workspace: str) -> dict[str, str]:
    return {"X-Test-Workspace": workspace}


async def _seed_ready_file(db_url: str, workspace: str) -> str:
    """Seed a single file at lifecycle_state='ready' so a corpus rebuild has
    at least one doc to cluster (avoids the 400 corpus-rebuild-no-input
    error path)."""
    file_id = str(uuid.uuid4())
    sha = hashlib.sha256(f"{workspace}-{file_id}".encode()).hexdigest()
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        await conn.execute(
            "INSERT INTO files (id, workspace_id, name, content_sha, object_key, "
            "mime_type, size_bytes, lifecycle_state) "
            "VALUES (%s, %s, %s, %s, %s, 'application/pdf', 100, 'ready')",
            (file_id, workspace, "seed.pdf", sha, f"raw_files/{sha}"),
        )
        await conn.commit()
    return file_id


# ===========================================================================
# §5.10.1 decision #11 + #12 — POST returns 202 with task_id
# ===========================================================================


async def test_post_corpus_rebuild_returns_202_with_task_id(
    client, test_workspace, db_url_superuser
):
    """Happy path: workspace has ≥1 file at lifecycle_state='ready', POST
    returns 202 Accepted with body {workspace_id, task_id, status, message}.
    Defers a Procrastinate `raptor_build_corpus` job."""
    await _seed_ready_file(db_url_superuser, test_workspace)

    resp = await client.post("/corpus/raptor/rebuild", json={}, headers=headers(test_workspace))

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["workspace_id"] == test_workspace
    assert body["status"] == "queued"
    assert "task_id" in body
    assert "message" in body

    # Procrastinate job got deferred.
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM procrastinate_jobs "
            "WHERE task_name = 'raptor_build_corpus' AND status IN ('todo', 'doing')"
        )
        (count,) = await cur.fetchone()
        assert count >= 1, "expected at least one queued raptor_build_corpus job"


# ===========================================================================
# §5.10.1 G2 §6.3 — 400 corpus-rebuild-no-input when workspace is empty
# ===========================================================================


async def test_post_corpus_rebuild_rejects_empty_workspace(client, test_workspace):
    """A workspace with zero files at lifecycle_state='ready' has nothing to
    cluster. The endpoint must return 400 corpus-rebuild-no-input."""
    # No seed → workspace has 0 ready files.
    resp = await client.post("/corpus/raptor/rebuild", json={}, headers=headers(test_workspace))

    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["type"].endswith("/corpus-rebuild-no-input")


# ===========================================================================
# §5.10.1 G2 §6.3 — 503 corpus-rebuild-in-flight when a job is already queued
# ===========================================================================


async def test_post_corpus_rebuild_rejects_when_job_already_queued(
    client, test_workspace, db_url_superuser
):
    """If a raptor_build_corpus job is already in procrastinate_jobs with
    status 'todo' or 'doing' for this workspace, a second POST must return
    503 corpus-rebuild-in-flight."""
    await _seed_ready_file(db_url_superuser, test_workspace)

    # First POST — succeeds, queues the job.
    first = await client.post("/corpus/raptor/rebuild", json={}, headers=headers(test_workspace))
    assert first.status_code == 202

    # Second POST while the job is still queued — must 503.
    second = await client.post("/corpus/raptor/rebuild", json={}, headers=headers(test_workspace))
    assert second.status_code == 503, second.text
    body = second.json()
    assert body["type"].endswith("/corpus-rebuild-in-flight")
