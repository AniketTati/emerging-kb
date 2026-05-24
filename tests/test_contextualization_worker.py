"""Phase 3b — contextualization worker integration tests.

RED at G3: imports from `kb.workers.tasks.contextualize_file_impl` +
`kb.domain.contextual_chunks` + migration 0010 + the 'contextualized'
lifecycle CHECK widening all land at G4.

Spec: tests/specs/phase_3b.md §4.2.
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


async def _post_parse_chunk(client, workspace: str) -> str:
    """Helper: POST tiny.pdf, run parse + chunk, return file_id."""
    from kb.workers.tasks import chunk_file_impl, parse_file_impl  # G4

    resp = await client.post(
        "/files",
        files={"file": ("ctx-input.pdf", _TINY_PDF, "application/pdf")},
        headers=headers(workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 201, resp.text
    fid = resp.json()["id"]
    await parse_file_impl(fid)
    await chunk_file_impl(fid)
    return fid


# ===========================================================================
# §5.8 decision #12 — chunked → contextualized via mock Anthropic adapter
# ===========================================================================


async def test_contextualize_file_impl_reads_chunks_and_writes_contextual_rows(
    client, test_workspace, db_url_superuser
):
    from kb.workers.tasks import contextualize_file_impl  # G4

    fid = await _post_parse_chunk(client, test_workspace)
    # KB_ANTHROPIC_API_KEY unset → IdentityContextualizer; lifecycle still advances.
    with _env(KB_ANTHROPIC_API_KEY=None):
        await contextualize_file_impl(fid)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM contextual_chunks WHERE file_id = %s", (fid,)
        )
        (cnt,) = await cur.fetchone()
        assert cnt >= 1

        cur = await conn.execute(
            "SELECT lifecycle_state FROM files WHERE id = %s", (fid,)
        )
        (state,) = await cur.fetchone()
        assert state == "contextualized"


async def test_contextualize_file_impl_writes_contextualized_lifecycle_event(
    client, test_workspace
):
    from kb.workers.tasks import contextualize_file_impl

    fid = await _post_parse_chunk(client, test_workspace)
    with _env(KB_ANTHROPIC_API_KEY=None):
        await contextualize_file_impl(fid)

    resp = await client.get(f"/files/{fid}", headers=headers(test_workspace))
    last_event = resp.json()["lifecycle"][-1]
    assert last_event["from_state"] == "chunked"
    assert last_event["to_state"] == "contextualized"
    assert last_event["event"] == "contextualization_done"
    payload = last_event["payload"]
    assert "prefix_count" in payload
    assert "total_cache_creation_tokens" in payload
    assert "total_cache_read_tokens" in payload
    assert "model_id" in payload


# ===========================================================================
# Idempotency
# ===========================================================================


async def test_contextualize_file_impl_is_idempotent_on_already_contextualized(
    client, test_workspace, db_url_superuser
):
    from kb.workers.tasks import contextualize_file_impl

    fid = await _post_parse_chunk(client, test_workspace)
    with _env(KB_ANTHROPIC_API_KEY=None):
        await contextualize_file_impl(fid)
        await contextualize_file_impl(fid)  # second run = no-op

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM contextual_chunks WHERE file_id = %s", (fid,)
        )
        (cnt,) = await cur.fetchone()

        cur2 = await conn.execute(
            "SELECT count(*) FROM file_lifecycle "
            "WHERE file_id = %s AND event = 'contextualization_done'",
            (fid,),
        )
        (event_cnt,) = await cur2.fetchone()
        assert event_cnt == 1
        # And chunk rows weren't duplicated either.
        cur3 = await conn.execute(
            "SELECT count(DISTINCT chunk_id) FROM contextual_chunks WHERE file_id = %s",
            (fid,),
        )
        (uniq,) = await cur3.fetchone()
        assert cnt == uniq, "duplicate contextual_chunks rows for the same chunk"


# ===========================================================================
# §5.8 decision #13 — chunk_file chains contextualize_file via defer
# ===========================================================================


async def test_chunk_file_impl_chains_contextualize_file_via_defer(
    client, test_workspace, db_url_superuser
):
    from kb.workers.tasks import chunk_file_impl, parse_file_impl

    resp = await client.post(
        "/files",
        files={"file": ("chain-ctx.pdf", _TINY_PDF, "application/pdf")},
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    fid = resp.json()["id"]
    await parse_file_impl(fid)
    await chunk_file_impl(fid)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM procrastinate_jobs "
            "WHERE task_name = 'contextualize_file' "
            "AND args::text LIKE %s",
            (f"%{fid}%",),
        )
        (count,) = await cur.fetchone()
        assert count >= 1, (
            "chunk_file_impl should defer a contextualize_file job"
        )


# ===========================================================================
# §5.8 decision #6 — IdentityContextualizer fallback
# ===========================================================================


async def test_contextualize_file_impl_identity_fallback_when_no_api_key(
    client, test_workspace, db_url_superuser
):
    from kb.workers.tasks import contextualize_file_impl

    fid = await _post_parse_chunk(client, test_workspace)

    with _env(KB_ANTHROPIC_API_KEY=None):
        await contextualize_file_impl(fid)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT cc.model_id, cc.contextual_text, c.text "
            "FROM contextual_chunks cc JOIN chunks c ON cc.chunk_id = c.id "
            "WHERE cc.file_id = %s",
            (fid,),
        )
        rows = await cur.fetchall()
        assert len(rows) >= 1
        for model_id, contextual_text, chunk_text in rows:
            assert model_id == "identity"
            assert contextual_text == chunk_text  # byte-for-byte


# ===========================================================================
# §5.8 decision #10 — REVOKE UPDATE on contextual_chunks
# ===========================================================================


async def test_contextual_chunks_table_rejects_update_via_kb_app(
    client, test_workspace, db_url_kb_app
):
    from kb.workers.tasks import contextualize_file_impl

    fid = await _post_parse_chunk(client, test_workspace)
    with _env(KB_ANTHROPIC_API_KEY=None):
        await contextualize_file_impl(fid)

    async with await psycopg.AsyncConnection.connect(db_url_kb_app) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)",
            (test_workspace,),
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await conn.execute(
                "UPDATE contextual_chunks SET contextual_prefix = 'tampered' "
                "WHERE file_id = %s",
                (fid,),
            )
