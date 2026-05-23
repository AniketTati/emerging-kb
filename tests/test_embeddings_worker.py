"""Phase 3c — embedding worker integration tests.

RED at G3: imports from `kb.workers.tasks.embed_file_impl` +
`kb.domain.chunk_embeddings` + migration 0011 all land at G4.

Spec: tests/specs/phase_3c.md §4.2.
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


async def _post_parse_chunk_contextualize(client, workspace: str) -> str:
    """POST tiny.pdf, run parse + chunk + contextualize, return file_id."""
    from kb.workers.tasks import (  # G4
        chunk_file_impl,
        contextualize_file_impl,
        parse_file_impl,
    )

    resp = await client.post(
        "/files",
        files={"file": ("emb-input.pdf", _TINY_PDF, "application/pdf")},
        headers=headers(workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 201, resp.text
    fid = resp.json()["id"]
    await parse_file_impl(fid)
    await chunk_file_impl(fid)
    with _env(KB_ANTHROPIC_API_KEY=None):
        await contextualize_file_impl(fid)
    return fid


# ===========================================================================
# §5.9 decision #10 — contextualized → embedded
# ===========================================================================


async def test_embed_file_impl_reads_contextual_chunks_and_writes_embedding_rows(
    client, test_workspace, db_url_superuser
):
    from kb.workers.tasks import embed_file_impl  # G4

    fid = await _post_parse_chunk_contextualize(client, test_workspace)
    with _env(KB_GEMINI_API_KEY=None):
        await embed_file_impl(fid)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM chunk_embeddings WHERE file_id = %s", (fid,)
        )
        (cnt,) = await cur.fetchone()
        assert cnt >= 1

        cur = await conn.execute(
            "SELECT lifecycle_state FROM files WHERE id = %s", (fid,)
        )
        (state,) = await cur.fetchone()
        assert state == "embedded"


async def test_embed_file_impl_writes_embedding_done_lifecycle_event(
    client, test_workspace
):
    from kb.workers.tasks import embed_file_impl

    fid = await _post_parse_chunk_contextualize(client, test_workspace)
    with _env(KB_GEMINI_API_KEY=None):
        await embed_file_impl(fid)

    resp = await client.get(f"/files/{fid}", headers=headers(test_workspace))
    last_event = resp.json()["lifecycle"][-1]
    assert last_event["from_state"] == "contextualized"
    assert last_event["to_state"] == "embedded"
    assert last_event["event"] == "embedding_done"
    payload = last_event["payload"]
    assert "embedding_count" in payload
    assert "dim" in payload
    assert payload["dim"] == 3072
    assert "model_id" in payload


# ===========================================================================
# §5.9 decision #12 — idempotency
# ===========================================================================


async def test_embed_file_impl_is_idempotent_on_already_embedded(
    client, test_workspace, db_url_superuser
):
    from kb.workers.tasks import embed_file_impl

    fid = await _post_parse_chunk_contextualize(client, test_workspace)
    with _env(KB_GEMINI_API_KEY=None):
        await embed_file_impl(fid)
        await embed_file_impl(fid)  # second run = no-op

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM file_lifecycle "
            "WHERE file_id = %s AND event = 'embedding_done'",
            (fid,),
        )
        (event_cnt,) = await cur.fetchone()
        assert event_cnt == 1

        cur2 = await conn.execute(
            "SELECT count(DISTINCT contextual_chunk_id) FROM chunk_embeddings "
            "WHERE file_id = %s",
            (fid,),
        )
        (uniq,) = await cur2.fetchone()
        cur3 = await conn.execute(
            "SELECT count(*) FROM chunk_embeddings WHERE file_id = %s",
            (fid,),
        )
        (total,) = await cur3.fetchone()
        assert uniq == total, "duplicate chunk_embeddings rows for same chunk"


# ===========================================================================
# §5.9 decision #11 — contextualize_file chains embed_file via defer
# ===========================================================================


async def test_contextualize_file_impl_chains_embed_file_via_defer(
    client, test_workspace, db_url_superuser
):
    from kb.workers.tasks import (
        chunk_file_impl,
        contextualize_file_impl,
        parse_file_impl,
    )

    resp = await client.post(
        "/files",
        files={"file": ("chain-emb.pdf", _TINY_PDF, "application/pdf")},
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    fid = resp.json()["id"]
    await parse_file_impl(fid)
    await chunk_file_impl(fid)
    with _env(KB_ANTHROPIC_API_KEY=None):
        await contextualize_file_impl(fid)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM procrastinate_jobs "
            "WHERE task_name = 'embed_file' "
            "AND args::text LIKE %s",
            (f"%{fid}%",),
        )
        (count,) = await cur.fetchone()
        assert count >= 1, (
            "contextualize_file_impl should defer an embed_file job"
        )


# ===========================================================================
# §5.9 decision #4 — DeterministicMockEmbedder fallback
# ===========================================================================


async def test_embed_file_impl_uses_mock_when_no_api_key(
    client, test_workspace, db_url_superuser
):
    from kb.workers.tasks import embed_file_impl

    fid = await _post_parse_chunk_contextualize(client, test_workspace)
    with _env(KB_GEMINI_API_KEY=None):
        await embed_file_impl(fid)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT DISTINCT model_id FROM chunk_embeddings WHERE file_id = %s",
            (fid,),
        )
        rows = await cur.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "mock-deterministic-v1"


# ===========================================================================
# §5.9 decision #8 — REVOKE UPDATE on chunk_embeddings
# ===========================================================================


async def test_chunk_embeddings_table_rejects_update_via_kb_app(
    client, test_workspace, db_url_kb_app
):
    from kb.workers.tasks import embed_file_impl

    fid = await _post_parse_chunk_contextualize(client, test_workspace)
    with _env(KB_GEMINI_API_KEY=None):
        await embed_file_impl(fid)

    async with await psycopg.AsyncConnection.connect(db_url_kb_app) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)",
            (test_workspace,),
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await conn.execute(
                "UPDATE chunk_embeddings SET model_id = 'tampered' "
                "WHERE file_id = %s",
                (fid,),
            )
