"""Phase 7 — direct unit tests for kb.domain.entities repo.

Per Phase 5/6 convention each new table gets a focused repo test file.
These tests exercise the repo functions directly (not through the worker
orchestration) so a repo regression surfaces immediately."""

from __future__ import annotations

import hashlib
import uuid

import psycopg
import pytest


pytestmark = pytest.mark.asyncio


async def _set_workspace(conn, workspace_id: str) -> None:
    await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace_id,))


async def test_insert_entity_returns_id(db_url_superuser):
    from kb.domain.entities import insert_entity
    workspace = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await _set_workspace(conn, workspace)
        eid = await insert_entity(
            conn, workspace_id=workspace, canonical_name="Acme", entity_type="ORG",
        )
        await conn.commit()
        assert eid
        assert len(eid) == 36  # uuid string


async def test_insert_entity_conflict_returns_existing_id(db_url_superuser):
    """ON CONFLICT (workspace_id, lower(canonical_name), entity_type) → DO UPDATE.
    Re-INSERT of same (workspace, name, type) returns the SAME id."""
    from kb.domain.entities import insert_entity
    workspace = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await _set_workspace(conn, workspace)
        eid1 = await insert_entity(
            conn, workspace_id=workspace, canonical_name="Beta", entity_type="ORG",
        )
        eid2 = await insert_entity(
            conn, workspace_id=workspace, canonical_name="BETA", entity_type="ORG",
        )
        await conn.commit()
    assert eid1 == eid2  # lowercased name match → upsert returns same id


async def test_find_entity_deterministic_case_insensitive(db_url_superuser):
    """Stage (a): case-insensitive name match within workspace."""
    from kb.domain.entities import find_entity_deterministic, insert_entity
    workspace = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await _set_workspace(conn, workspace)
        eid = await insert_entity(
            conn, workspace_id=workspace, canonical_name="ACME Corp", entity_type="ORG",
        )
        await conn.commit()
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await _set_workspace(conn, workspace)
        found = await find_entity_deterministic(
            conn, workspace_id=workspace, name="acme corp", entity_type="ORG",
        )
    assert found == eid


async def test_find_entity_deterministic_returns_none_on_miss(db_url_superuser):
    from kb.domain.entities import find_entity_deterministic
    workspace = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await _set_workspace(conn, workspace)
        result = await find_entity_deterministic(
            conn, workspace_id=workspace, name="nonexistent", entity_type="ORG",
        )
    assert result is None


async def test_find_entity_deterministic_filters_by_type(db_url_superuser):
    """Same name but different entity_type → no match."""
    from kb.domain.entities import find_entity_deterministic, insert_entity
    workspace = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await _set_workspace(conn, workspace)
        await insert_entity(
            conn, workspace_id=workspace, canonical_name="Apple", entity_type="ORG",
        )
        await conn.commit()
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await _set_workspace(conn, workspace)
        found = await find_entity_deterministic(
            conn, workspace_id=workspace, name="Apple", entity_type="PRODUCT",
        )
    assert found is None  # type mismatch


async def test_increment_mention_count_updates_in_place(db_url_superuser):
    from kb.domain.entities import increment_mention_count, insert_entity
    workspace = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await _set_workspace(conn, workspace)
        eid = await insert_entity(
            conn, workspace_id=workspace, canonical_name="X", entity_type="PERSON",
        )
        await increment_mention_count(conn, entity_id=eid, by=3)
        await conn.commit()
        cur = await conn.execute(
            "SELECT mention_count FROM entities WHERE id = %s", (eid,),
        )
        assert (await cur.fetchone())[0] == 4  # 1 (default) + 3


async def test_find_entity_by_embedding_orders_by_cosine_distance(db_url_superuser):
    """Stage (b): nearest-neighbor returns rows sorted by cosine sim DESC."""
    from kb.domain.entities import find_entity_by_embedding, insert_entity
    workspace = str(uuid.uuid4())
    # Three entities with different embeddings.
    e1_vec = [1.0] + [0.0] * 3071  # most similar to query [1,0,...]
    e2_vec = [0.0, 1.0] + [0.0] * 3070
    e3_vec = [0.0, 0.0, 1.0] + [0.0] * 3069

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await _set_workspace(conn, workspace)
        eid1 = await insert_entity(
            conn, workspace_id=workspace, canonical_name="Closest", entity_type="ORG",
            embedding=e1_vec,
        )
        await insert_entity(
            conn, workspace_id=workspace, canonical_name="Middle", entity_type="ORG",
            embedding=e2_vec,
        )
        await insert_entity(
            conn, workspace_id=workspace, canonical_name="Far", entity_type="ORG",
            embedding=e3_vec,
        )
        await conn.commit()
    query_vec = [1.0] + [0.0] * 3071
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await _set_workspace(conn, workspace)
        hits = await find_entity_by_embedding(
            conn, workspace_id=workspace, entity_type="ORG",
            embedding=query_vec, limit=3,
        )
    assert len(hits) == 3
    # The closest (e1_vec = query_vec exactly) should be first
    assert hits[0][0] == eid1
    assert hits[0][2] == pytest.approx(1.0, abs=0.01)
    # Scores should be monotonically descending
    sims = [h[2] for h in hits]
    assert sims == sorted(sims, reverse=True)


async def test_find_entity_by_embedding_filters_by_type(db_url_superuser):
    """Embedding search scoped by entity_type — won't return cross-type matches."""
    from kb.domain.entities import find_entity_by_embedding, insert_entity
    workspace = str(uuid.uuid4())
    vec = [1.0] + [0.0] * 3071
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await _set_workspace(conn, workspace)
        await insert_entity(
            conn, workspace_id=workspace, canonical_name="P1", entity_type="PERSON",
            embedding=vec,
        )
        await conn.commit()
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await _set_workspace(conn, workspace)
        hits = await find_entity_by_embedding(
            conn, workspace_id=workspace, entity_type="ORG", embedding=vec, limit=5,
        )
    assert hits == []  # wrong type


async def test_find_entity_by_embedding_returns_empty_for_empty_vec(db_url_superuser):
    from kb.domain.entities import find_entity_by_embedding
    workspace = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await _set_workspace(conn, workspace)
        hits = await find_entity_by_embedding(
            conn, workspace_id=workspace, entity_type="ORG", embedding=[], limit=5,
        )
    assert hits == []


async def test_delete_mention_to_entity_for_file_cascades_only_file_mentions(
    db_url_superuser,
):
    """DELETE scoped to mention_ids belonging to ONE file — doesn't touch
    other files' links in the same workspace."""
    from kb.domain.entities import (
        delete_mention_to_entity_for_file,
        insert_entity,
        insert_mention_to_entity,
    )

    workspace = str(uuid.uuid4())
    sha_a = hashlib.sha256(f"a-{workspace}".encode()).hexdigest()
    sha_b = hashlib.sha256(f"b-{workspace}".encode()).hexdigest()
    file_a = str(uuid.uuid4())
    file_b = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await _set_workspace(conn, workspace)
        for fid, sha in ((file_a, sha_a), (file_b, sha_b)):
            await conn.execute(
                "INSERT INTO files (id, workspace_id, name, content_sha, object_key, "
                "mime_type, size_bytes, lifecycle_state) "
                "VALUES (%s, %s, 'x.pdf', %s, %s, 'application/pdf', 100, 'ready')",
                (fid, workspace, sha, f"raw_files/{sha}"),
            )
            await conn.execute(
                "INSERT INTO raw_pages (id, file_id, workspace_id, page_number, text, "
                "layout_json, content_sha) "
                "VALUES (%s, %s, %s, 1, 'x', '{}'::jsonb, %s)",
                (str(uuid.uuid4()), fid, workspace, sha),
            )
            chunk_id = str(uuid.uuid4())
            chunk_sha = hashlib.sha256(f"c-{workspace}-{fid}".encode()).hexdigest()
            await conn.execute(
                "INSERT INTO chunks (id, file_id, workspace_id, chunk_index, text, "
                "source_page_numbers, token_count, content_sha) "
                "VALUES (%s, %s, %s, 0, 'c', %s, 5, %s)",
                (chunk_id, fid, workspace, [1], chunk_sha),
            )
            cc_id = str(uuid.uuid4())
            await conn.execute(
                "INSERT INTO contextual_chunks (id, chunk_id, file_id, workspace_id, "
                "contextual_prefix, contextual_text, model_id, prefix_token_count, "
                "cache_creation_input_tokens, cache_read_input_tokens) "
                "VALUES (%s, %s, %s, %s, '', 'c', 'identity', 0, 0, 0)",
                (cc_id, chunk_id, fid, workspace),
            )
            mid = str(uuid.uuid4())
            await conn.execute(
                "INSERT INTO extracted_mentions "
                "(id, contextual_chunk_id, file_id, workspace_id, mention_text, mention_type, model_id) "
                "VALUES (%s, %s, %s, %s, %s, 'PERSON', 'identity')",
                (mid, cc_id, fid, workspace, f"mention-{fid}"),
            )
            eid = await insert_entity(
                conn, workspace_id=workspace, canonical_name=f"E-{fid}", entity_type="PERSON",
            )
            await insert_mention_to_entity(
                conn, mention_id=mid, entity_id=eid, workspace_id=workspace,
                confidence=1.0, resolved_method="deterministic",
            )
        await conn.commit()

    # Both rows exist.
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await _set_workspace(conn, workspace)
        cur = await conn.execute("SELECT count(*) FROM mention_to_entity WHERE workspace_id = %s", (workspace,))
        assert (await cur.fetchone())[0] == 2

    # Delete file_a's links only.
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await _set_workspace(conn, workspace)
        n = await delete_mention_to_entity_for_file(conn, file_id=file_a)
        await conn.commit()
    assert n == 1  # only file_a's row deleted

    # File_b's row still there.
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await _set_workspace(conn, workspace)
        cur = await conn.execute("SELECT count(*) FROM mention_to_entity WHERE workspace_id = %s", (workspace,))
        assert (await cur.fetchone())[0] == 1
