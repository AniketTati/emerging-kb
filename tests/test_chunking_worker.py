"""Phase 3a — chunker worker integration tests.

RED at G3: imports from `kb.workers.tasks.chunk_file_impl` + `kb.domain.chunks`
+ migration 0009 + the 'chunked' lifecycle CHECK widening all land at G4.

Spec: tests/specs/phase_3a.md §4.2.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from tests.test_files_crud import _TINY_PDF


pytestmark = pytest.mark.asyncio


@pytest.fixture
def test_workspace() -> str:
    return str(uuid.uuid4())


def headers(workspace: str, *, idempotency_key: str | None = None) -> dict[str, str]:
    h = {"X-Test-Workspace": workspace}
    if idempotency_key is not None:
        h["Idempotency-Key"] = idempotency_key
    return h


async def _post_and_parse(client, workspace: str) -> str:
    """Helper: POST tiny.pdf, run parse_file_impl directly, return file_id."""
    from kb.workers.tasks import parse_file_impl  # G4

    resp = await client.post(
        "/files",
        files={"file": ("chunk-input.pdf", _TINY_PDF, "application/pdf")},
        headers=headers(workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 201, resp.text
    fid = resp.json()["id"]
    await parse_file_impl(fid)
    return fid


# ===========================================================================
# §5.7 decisions #6, #8 — chunks land + lifecycle transitions to 'chunked'
# ===========================================================================


async def test_chunk_file_impl_reads_raw_pages_and_writes_chunks(
    client, test_workspace, db_url_superuser
):
    from kb.workers.tasks import chunk_file_impl  # G4

    fid = await _post_and_parse(client, test_workspace)
    await chunk_file_impl(fid)

    # Confirm chunks present + lifecycle_state advanced.
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM chunks WHERE file_id = %s", (fid,)
        )
        (chunk_count,) = await cur.fetchone()
        assert chunk_count >= 1

        cur = await conn.execute(
            "SELECT lifecycle_state FROM files WHERE id = %s", (fid,)
        )
        (state,) = await cur.fetchone()
        assert state == "chunked"


async def test_chunk_file_impl_writes_chunked_lifecycle_event(
    client, test_workspace
):
    from kb.workers.tasks import chunk_file_impl

    fid = await _post_and_parse(client, test_workspace)
    await chunk_file_impl(fid)

    resp = await client.get(f"/files/{fid}", headers=headers(test_workspace))
    body = resp.json()
    last_event = body["lifecycle"][-1]
    assert last_event["from_state"] == "parsed"
    assert last_event["to_state"] == "chunked"
    assert last_event["event"] == "chunking_done"
    assert "chunk_count" in last_event["payload"]
    assert "total_tokens" in last_event["payload"]


# ===========================================================================
# §5.7 decision #10 — idempotency on already-chunked
# ===========================================================================


async def test_chunk_file_impl_is_idempotent_on_already_chunked(
    client, test_workspace, db_url_superuser
):
    from kb.workers.tasks import chunk_file_impl

    fid = await _post_and_parse(client, test_workspace)
    await chunk_file_impl(fid)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM chunks WHERE file_id = %s", (fid,)
        )
        (first_count,) = await cur.fetchone()

    # Re-run — must be a no-op.
    await chunk_file_impl(fid)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM chunks WHERE file_id = %s", (fid,)
        )
        (second_count,) = await cur.fetchone()
        assert first_count == second_count

        # Only ONE chunking_done event in file_lifecycle.
        cur = await conn.execute(
            "SELECT count(*) FROM file_lifecycle "
            "WHERE file_id = %s AND event = 'chunking_done'",
            (fid,),
        )
        (event_count,) = await cur.fetchone()
        assert event_count == 1


# ===========================================================================
# §5.7 decision #11 — empty raw_pages → ChunkingError → failed
# ===========================================================================


async def test_chunk_file_impl_empty_raw_pages_marks_failed(
    client, test_workspace, db_url_superuser
):
    """Force a file into 'parsed' state with no raw_pages → chunk_file marks failed."""
    from kb.workers.tasks import chunk_file_impl

    # POST a file but DON'T parse it; we'll hand-craft state via superuser.
    resp = await client.post(
        "/files",
        files={"file": ("empty.pdf", _TINY_PDF, "application/pdf")},
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    fid = resp.json()["id"]

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        async with conn.transaction():
            # Force lifecycle to 'parsed' without raw_pages
            await conn.execute(
                "UPDATE files SET lifecycle_state = 'parsed' WHERE id = %s",
                (fid,),
            )
            await conn.execute(
                "INSERT INTO file_lifecycle "
                "(file_id, workspace_id, from_state, to_state, event, payload) "
                "VALUES (%s, %s, 'queued', 'parsed', 'forced_for_test', '{}'::jsonb)",
                (fid, test_workspace),
            )

    # chunk_file_impl should mark this failed, not raise.
    await chunk_file_impl(fid)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT lifecycle_state FROM files WHERE id = %s", (fid,)
        )
        (state,) = await cur.fetchone()
        assert state == "failed"

        cur = await conn.execute(
            "SELECT event FROM file_lifecycle "
            "WHERE file_id = %s ORDER BY created_at DESC LIMIT 1",
            (fid,),
        )
        (event,) = await cur.fetchone()
        assert event == "chunking_failed"


# ===========================================================================
# §5.7 decision #9 — parse_file_impl chains chunk_file via defer
# ===========================================================================


async def test_parse_file_impl_chains_chunk_file_via_defer(
    client, test_workspace, db_url_superuser
):
    """After parse_file_impl, a chunk_file job is queued in procrastinate_jobs."""
    from kb.workers.tasks import parse_file_impl

    resp = await client.post(
        "/files",
        files={"file": ("chain.pdf", _TINY_PDF, "application/pdf")},
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    fid = resp.json()["id"]
    await parse_file_impl(fid)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM procrastinate_jobs "
            "WHERE task_name = 'chunk_file' "
            "AND args::text LIKE %s",
            (f"%{fid}%",),
        )
        (count,) = await cur.fetchone()
        assert count >= 1, (
            "parse_file_impl should have deferred a chunk_file job"
        )


# ===========================================================================
# §5.7 decision #7 — REVOKE UPDATE, DELETE on chunks
# ===========================================================================


async def test_chunks_table_rejects_update_via_kb_app(
    client, test_workspace, db_url_kb_app
):
    """kb_app role gets InsufficientPrivilege on chunks UPDATE."""
    from kb.workers.tasks import chunk_file_impl

    fid = await _post_and_parse(client, test_workspace)
    await chunk_file_impl(fid)

    async with await psycopg.AsyncConnection.connect(db_url_kb_app) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)",
            (test_workspace,),
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await conn.execute(
                "UPDATE chunks SET text = 'tampered' WHERE file_id = %s",
                (fid,),
            )


# ===========================================================================
# §5.7 — RLS isolation on chunks (workspace-scoped table)
# ===========================================================================


async def test_chunks_isolated_across_workspaces(
    client, db_url_kb_app
):
    """Chunks created in workspace A are invisible from workspace B."""
    from kb.workers.tasks import chunk_file_impl

    ws_a = str(uuid.uuid4())
    ws_b = str(uuid.uuid4())

    fid_a = await _post_and_parse(client, ws_a)
    await chunk_file_impl(fid_a)

    async with await psycopg.AsyncConnection.connect(db_url_kb_app) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)",
            (ws_b,),
        )
        cur = await conn.execute(
            "SELECT count(*) FROM chunks WHERE file_id = %s", (fid_a,)
        )
        (count,) = await cur.fetchone()
        assert count == 0, "workspace B should not see workspace A's chunks"
