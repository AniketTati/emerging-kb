"""#19 / I4 — post-ingest identity reconcile sweep + the shared merge primitive.

`merge_entity_group` (kb.identity.merge) soft-merges duplicate canonical
entities: repoint mention_to_entity to the survivor, recompute mention_count,
stamp merged_into on losers. `reconcile_workspace_entities_impl` is the
automatic corpus-finalization sweep built on it (token-overlap blocking +
identity judge). The judge is injected as a fake here.
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
def _use_db(db_url: str):
    from kb.config import get_settings

    prior = os.environ.get("KB_DATABASE_URL")
    os.environ["KB_DATABASE_URL"] = db_url
    get_settings.cache_clear()
    try:
        yield
    finally:
        if prior is None:
            os.environ.pop("KB_DATABASE_URL", None)
        else:
            os.environ["KB_DATABASE_URL"] = prior
        get_settings.cache_clear()


async def _seed_entity_with_mentions(
    conn, *, workspace: str, file_id: str, cc_id: str,
    name: str, entity_type: str, n_mentions: int,
) -> str:
    """Create a canonical entity + n_mentions extracted_mentions linked to it.
    Returns the entity id."""
    from kb.domain.entities import (
        increment_mention_count,
        insert_entity,
        insert_mention_to_entity,
    )

    entity_id = await insert_entity(
        conn, workspace_id=workspace, canonical_name=name, entity_type=entity_type,
    )
    for _ in range(n_mentions):
        cur = await conn.execute(
            "INSERT INTO extracted_mentions (contextual_chunk_id, file_id, "
            "workspace_id, mention_text, mention_type, model_id) "
            "VALUES (%s, %s, %s, %s, %s, 'test-mock') RETURNING id::text",
            (cc_id, file_id, workspace, name, entity_type),
        )
        mention_id = (await cur.fetchone())[0]
        await insert_mention_to_entity(
            conn, mention_id=mention_id, entity_id=entity_id,
            workspace_id=workspace, confidence=1.0, resolved_method="embedding",
        )
        await increment_mention_count(conn, entity_id=entity_id)
    return entity_id


async def _seed_file_chunk(conn, *, workspace: str) -> tuple[str, str]:
    """Minimal file + chunk + contextual_chunk so mentions have valid FKs.
    Returns (file_id, contextual_chunk_id)."""
    file_id = str(uuid.uuid4())
    sha = hashlib.sha256(f"{workspace}-{file_id}".encode()).hexdigest()
    await conn.execute(
        "INSERT INTO files (id, workspace_id, name, content_sha, object_key, "
        "mime_type, size_bytes, lifecycle_state) "
        "VALUES (%s, %s, 'f', %s, %s, 'application/pdf', 100, 'ready')",
        (file_id, workspace, sha, f"raw_files/{sha}"),
    )
    chunk_id = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO chunks (id, file_id, workspace_id, chunk_index, text, "
        "source_page_numbers, token_count, content_sha) "
        "VALUES (%s, %s, %s, 0, 'chunk', %s, 5, %s)",
        (chunk_id, file_id, workspace, [1], sha),
    )
    cc_id = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO contextual_chunks (id, chunk_id, file_id, workspace_id, "
        "contextual_prefix, contextual_text, model_id, prefix_token_count, "
        "cache_creation_input_tokens, cache_read_input_tokens) "
        "VALUES (%s, %s, %s, %s, '', 'ctx', 'identity', 0, 0, 0)",
        (cc_id, chunk_id, file_id, workspace),
    )
    return file_id, cc_id


async def _entity_state(conn, entity_id: str) -> tuple[int, str | None]:
    cur = await conn.execute(
        "SELECT mention_count, merged_into::text FROM canonical_entities WHERE id = %s",
        (entity_id,),
    )
    row = await cur.fetchone()
    return int(row[0]), row[1]


async def test_merge_entity_group_repoints_and_tombstones(db_url_superuser):
    from kb.identity.merge import merge_entity_group

    workspace = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        file_id, cc_id = await _seed_file_chunk(conn, workspace=workspace)
        survivor = await _seed_entity_with_mentions(
            conn, workspace=workspace, file_id=file_id, cc_id=cc_id,
            name="Acme Corp", entity_type="ORG", n_mentions=2,
        )
        loser = await _seed_entity_with_mentions(
            conn, workspace=workspace, file_id=file_id, cc_id=cc_id,
            name="Acme Corporation", entity_type="ORG", n_mentions=1,
        )
        await conn.commit()

        async with conn.transaction():
            await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
            n = await merge_entity_group(
                conn, workspace_id=workspace, survivor_id=survivor, loser_ids=[loser],
            )

        assert n == 1
        s_count, s_merged = await _entity_state(conn, survivor)
        l_count, l_merged = await _entity_state(conn, loser)

    assert s_count == 3, f"survivor mention_count should sum to 3, got {s_count}"
    assert s_merged is None, "survivor must stay active"
    assert l_merged == survivor, "loser must be tombstoned into survivor"


async def test_reconcile_sweep_merges_confirmed_duplicates(db_url_superuser):
    from kb.workers.tasks import reconcile_workspace_entities_impl

    workspace = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        file_id, cc_id = await _seed_file_chunk(conn, workspace=workspace)
        survivor = await _seed_entity_with_mentions(
            conn, workspace=workspace, file_id=file_id, cc_id=cc_id,
            name="Acme Corp", entity_type="ORG", n_mentions=2,
        )
        loser = await _seed_entity_with_mentions(
            conn, workspace=workspace, file_id=file_id, cc_id=cc_id,
            name="Acme Corporation", entity_type="ORG", n_mentions=1,
        )
        await conn.commit()

    class _YesJudge:
        async def same_entity(self, *, text_a, type_a, text_b, type_b):
            return True

    with _use_db(db_url_superuser):
        summary = await reconcile_workspace_entities_impl(
            workspace_id=workspace, judge=_YesJudge(),
        )

    assert summary["merged"] == 1, summary
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        s_count, s_merged = await _entity_state(conn, survivor)
        _, l_merged = await _entity_state(conn, loser)
    assert s_merged is None and l_merged == survivor
    assert s_count == 3


async def test_reconcile_sweep_judge_veto_keeps_separate(db_url_superuser):
    from kb.workers.tasks import reconcile_workspace_entities_impl

    workspace = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        file_id, cc_id = await _seed_file_chunk(conn, workspace=workspace)
        e1 = await _seed_entity_with_mentions(
            conn, workspace=workspace, file_id=file_id, cc_id=cc_id,
            name="Acme Corp", entity_type="ORG", n_mentions=2,
        )
        e2 = await _seed_entity_with_mentions(
            conn, workspace=workspace, file_id=file_id, cc_id=cc_id,
            name="Acme Corporation", entity_type="ORG", n_mentions=1,
        )
        await conn.commit()

    class _NoJudge:
        async def same_entity(self, *, text_a, type_a, text_b, type_b):
            return False

    with _use_db(db_url_superuser):
        summary = await reconcile_workspace_entities_impl(
            workspace_id=workspace, judge=_NoJudge(),
        )

    assert summary["merged"] == 0, summary
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        _, m1 = await _entity_state(conn, e1)
        _, m2 = await _entity_state(conn, e2)
    assert m1 is None and m2 is None, "judge veto must leave both entities active"
