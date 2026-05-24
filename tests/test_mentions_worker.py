"""Phase 5a — extract_mentions_file_impl integration tests against testcontainers.

Covers §5.12.1 decisions #5/#6/#7/#8.
"""

from __future__ import annotations

import hashlib
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


async def _seed_file_to_mentions_extracting(
    db_url: str, workspace_id: str
) -> tuple[str, list[str]]:
    """Seed a file in 'mentions_extracting' state with 2 contextual_chunks.

    Returns (file_id, [contextual_chunk_id, ...]).
    """
    file_id = str(uuid.uuid4())
    sha = hashlib.sha256(f"mentions-{workspace_id}".encode()).hexdigest()
    cc_ids: list[str] = []

    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace_id,))
        # files row in mentions_extracting state
        await conn.execute(
            "INSERT INTO files (id, workspace_id, name, content_sha, object_key, "
            "mime_type, size_bytes, lifecycle_state) "
            "VALUES (%s, %s, 'mentions-test.pdf', %s, %s, "
            "'application/pdf', 100, 'mentions_extracting')",
            (file_id, workspace_id, sha, f"raw_files/{sha}"),
        )
        # one raw_page (FK requires it for chunks)
        await conn.execute(
            "INSERT INTO raw_pages (id, file_id, workspace_id, page_number, text, "
            "layout_json, content_sha) "
            "VALUES (%s, %s, %s, 1, 'page text', '{}'::jsonb, %s)",
            (str(uuid.uuid4()), file_id, workspace_id, sha),
        )
        # 2 chunks + 2 contextual_chunks
        for i in range(2):
            chunk_id = str(uuid.uuid4())
            chunk_sha = hashlib.sha256(f"chunk-{workspace_id}-{i}".encode()).hexdigest()
            await conn.execute(
                "INSERT INTO chunks (id, file_id, workspace_id, chunk_index, text, "
                "source_page_numbers, token_count, content_sha) "
                "VALUES (%s, %s, %s, %s, %s, %s, 5, %s)",
                (chunk_id, file_id, workspace_id, i, f"chunk {i} text", [1], chunk_sha),
            )
            cc_id = str(uuid.uuid4())
            await conn.execute(
                "INSERT INTO contextual_chunks (id, chunk_id, file_id, workspace_id, "
                "contextual_prefix, contextual_text, model_id, prefix_token_count, "
                "cache_creation_input_tokens, cache_read_input_tokens) "
                "VALUES (%s, %s, %s, %s, '', %s, 'identity', 0, 0, 0)",
                (cc_id, chunk_id, file_id, workspace_id,
                 f"contextual chunk {i} with Acme Corp filed in 2024-01-15"),
            )
            cc_ids.append(cc_id)
        await conn.commit()
    return file_id, cc_ids


async def test_extract_mentions_skips_non_mentions_extracting_state(
    client, db_url_superuser
):
    """Decision #8 idempotency: skip if file is past mentions_extracting."""
    from kb.workers.tasks import extract_mentions_file_impl

    workspace = str(uuid.uuid4())
    file_id = str(uuid.uuid4())
    sha = hashlib.sha256(f"already-ready-{workspace}".encode()).hexdigest()

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        await conn.execute(
            "INSERT INTO files (id, workspace_id, name, content_sha, object_key, "
            "mime_type, size_bytes, lifecycle_state) "
            "VALUES (%s, %s, 'ar.pdf', %s, %s, 'application/pdf', 100, 'ready')",
            (file_id, workspace, sha, f"raw_files/{sha}"),
        )
        await conn.commit()

    # Should return immediately without error.
    with _env(KB_DATABASE_URL=db_url_superuser, KB_MENTIONS_EXTRACTOR="identity"):
        from kb.config import get_settings
        get_settings.cache_clear()
        await extract_mentions_file_impl(file_id)

    # Lifecycle state still 'ready' — no mentions inserted (Identity returns []).
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT lifecycle_state FROM files WHERE id = %s", (file_id,),
        )
        row = await cur.fetchone()
        assert row[0] == "ready"


async def test_extract_mentions_identity_path_advances_lifecycle(
    client, db_url_superuser
):
    """Decision #3 + #8: Identity extractor returns []; lifecycle still
    advances mentions_extracting → fields_extracting."""
    from kb.workers.tasks import extract_mentions_file_impl

    workspace = str(uuid.uuid4())
    file_id, cc_ids = await _seed_file_to_mentions_extracting(db_url_superuser, workspace)

    with _env(KB_DATABASE_URL=db_url_superuser, KB_MENTIONS_EXTRACTOR="identity"):
        from kb.config import get_settings
        get_settings.cache_clear()
        await extract_mentions_file_impl(file_id)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        cur = await conn.execute(
            "SELECT lifecycle_state FROM files WHERE id = %s", (file_id,),
        )
        assert (await cur.fetchone())[0] == "fields_extracting"

        # Identity returns [] mentions — but the lifecycle event was written.
        cur = await conn.execute(
            "SELECT count(*) FROM extracted_mentions WHERE file_id = %s",
            (file_id,),
        )
        assert (await cur.fetchone())[0] == 0

        # Lifecycle event recorded
        cur = await conn.execute(
            "SELECT count(*) FROM file_lifecycle "
            "WHERE file_id = %s AND event = 'mentions_extracted'",
            (file_id,),
        )
        assert (await cur.fetchone())[0] == 1


async def test_extract_mentions_writes_rows_for_mock_llm(
    client, db_url_superuser, monkeypatch
):
    """Decision #5: extracted_mentions rows written; one row per LLM-returned
    mention; lifecycle advances mentions_extracting → fields_extracting."""
    from kb.workers.tasks import extract_mentions_file_impl
    from kb.extraction.mentions import Mention, MentionExtractionResult

    workspace = str(uuid.uuid4())
    file_id, cc_ids = await _seed_file_to_mentions_extracting(db_url_superuser, workspace)

    # Mock the factory to return a fake extractor that emits 2 mentions per chunk.
    class FakeExtractor:
        async def extract(self, *, doc_text, chunk_text):
            return MentionExtractionResult(
                mentions=[
                    Mention(mention_text="Acme Corp", mention_type="ORG",
                            start_offset=0, end_offset=9, confidence=0.95),
                    Mention(mention_text="2024-01-15", mention_type="DATE"),
                ],
                model_id="fake-mock",
                input_token_count=100,
                output_token_count=50,
            )

    monkeypatch.setattr(
        "kb.workers.tasks.make_mention_extractor",
        lambda: FakeExtractor(),
        raising=False,
    )
    # The import happens inside the worker function body — patch the
    # module's attribute the worker function will look up.
    import kb.extraction.mentions as mentions_mod
    monkeypatch.setattr(mentions_mod, "make_mention_extractor", lambda: FakeExtractor())

    with _env(KB_DATABASE_URL=db_url_superuser, KB_MENTIONS_EXTRACTOR="identity"):
        from kb.config import get_settings
        get_settings.cache_clear()
        await extract_mentions_file_impl(file_id)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        cur = await conn.execute(
            "SELECT count(*) FROM extracted_mentions WHERE file_id = %s",
            (file_id,),
        )
        # 2 chunks × 2 mentions each = 4
        assert (await cur.fetchone())[0] == 4

        cur = await conn.execute(
            "SELECT mention_type, mention_text, model_id FROM extracted_mentions "
            "WHERE file_id = %s ORDER BY mention_type, mention_text LIMIT 1",
            (file_id,),
        )
        mt, mtext, mid = await cur.fetchone()
        assert mt == "DATE"
        assert mtext == "2024-01-15"
        assert mid == "fake-mock"

        cur = await conn.execute(
            "SELECT lifecycle_state FROM files WHERE id = %s", (file_id,),
        )
        assert (await cur.fetchone())[0] == "fields_extracting"


async def test_extract_mentions_re_run_is_idempotent_via_delete_then_insert(
    client, db_url_superuser, monkeypatch
):
    """Decision #8: re-running the task DELETEs existing mentions and INSERTs
    new ones — count stays stable across re-runs."""
    from kb.workers.tasks import extract_mentions_file_impl
    from kb.extraction.mentions import Mention, MentionExtractionResult

    workspace = str(uuid.uuid4())
    file_id, _ = await _seed_file_to_mentions_extracting(db_url_superuser, workspace)

    class FakeExtractor:
        async def extract(self, *, doc_text, chunk_text):
            return MentionExtractionResult(
                mentions=[Mention(mention_text="X", mention_type="ORG")],
                model_id="fake-mock",
            )

    import kb.extraction.mentions as mentions_mod
    monkeypatch.setattr(mentions_mod, "make_mention_extractor", lambda: FakeExtractor())

    with _env(KB_DATABASE_URL=db_url_superuser):
        from kb.config import get_settings
        get_settings.cache_clear()
        # First run
        await extract_mentions_file_impl(file_id)

        # Reset lifecycle so we can call again (manual since the worker normally
        # only runs when state == 'mentions_extracting').
        async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
            await conn.execute(
                "UPDATE files SET lifecycle_state = 'mentions_extracting' WHERE id = %s",
                (file_id,),
            )
            await conn.commit()

        # Second run — DELETE existing + INSERT new
        await extract_mentions_file_impl(file_id)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        cur = await conn.execute(
            "SELECT count(*) FROM extracted_mentions WHERE file_id = %s",
            (file_id,),
        )
        # 2 chunks × 1 mention each = 2 (stable across re-runs, not 4)
        assert (await cur.fetchone())[0] == 2
