"""Pass B — /explore endpoints (counts + search + entity profile)."""

from __future__ import annotations

import uuid

import psycopg
import pytest


pytestmark = pytest.mark.asyncio


@pytest.fixture
def test_workspace() -> str:
    return str(uuid.uuid4())


def headers(workspace: str) -> dict[str, str]:
    return {"X-Test-Workspace": workspace}


async def test_explore_counts_returns_all_seven_categories(
    client, test_workspace,
):
    """GET /explore/counts returns the 7 left-rail buckets even when
    every count is 0 (fresh workspace)."""
    resp = await client.get(
        "/explore/counts", headers=headers(test_workspace),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) >= {
        "documents", "doc_types", "atomic_units", "entities",
        "relationships", "topics", "anomalies",
    }
    # Fresh workspace — all 0.
    for k in ("documents", "doc_types", "entities"):
        assert body[k] == 0


async def test_explore_search_empty_workspace_returns_no_items(
    client, test_workspace,
):
    """No data → 200 + empty items + total_estimate=0."""
    resp = await client.get(
        "/explore/search?limit=20", headers=headers(test_workspace),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total_estimate"] == 0


async def test_explore_search_has_chain_filter_drops_unchained_files(
    client, test_workspace, db_url_superuser,
):
    """`has_chain=true` should only return files that are members of a
    doc_chain. Two seeded files, one in a chain, one not — only the
    chained one comes back."""
    chain_id = str(uuid.uuid4())
    chained_file = str(uuid.uuid4())
    standalone_file = str(uuid.uuid4())

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        # Seed two files. content_sha must be exactly 64 hex chars
        # (the table's CHECK constraint).
        chained_sha = "a" * 63 + "0"
        standalone_sha = "a" * 63 + "1"
        for fid, name, sha in [
            (chained_file, "chained.pdf", chained_sha),
            (standalone_file, "standalone.pdf", standalone_sha),
        ]:
            await conn.execute(
                "INSERT INTO files (id, workspace_id, name, mime_type, "
                "size_bytes, content_sha, object_key, lifecycle_state) "
                "VALUES (%s, %s, %s, 'application/pdf', 0, "
                "        %s, %s, 'ready')",
                (fid, test_workspace, name, sha, f"k/{fid}"),
            )
        # Seed a chain + a member row.
        await conn.execute(
            "INSERT INTO doc_chains "
            "  (id, workspace_id, type, chain_key, detection_confidence) "
            "VALUES (%s, %s, 'contract_chain', %s, 0.9)",
            (chain_id, test_workspace, f"key-{chain_id}"),
        )
        await conn.execute(
            "INSERT INTO doc_chain_members "
            "  (chain_id, doc_id, workspace_id, version_index, role) "
            "VALUES (%s, %s, %s, 0, 'original')",
            (chain_id, chained_file, test_workspace),
        )
        await conn.commit()

    # Without filter → both come back.
    resp = await client.get(
        "/explore/search?kind=document&limit=20",
        headers=headers(test_workspace),
    )
    assert resp.status_code == 200
    all_ids = {h["id"] for h in resp.json()["items"]}
    assert chained_file in all_ids
    assert standalone_file in all_ids

    # With has_chain=true → only the chained one.
    resp = await client.get(
        "/explore/search?kind=document&has_chain=true&limit=20",
        headers=headers(test_workspace),
    )
    assert resp.status_code == 200
    chained_only_ids = {h["id"] for h in resp.json()["items"]}
    assert chained_file in chained_only_ids
    assert standalone_file not in chained_only_ids


async def test_explore_entity_profile_404_for_missing_id(
    client, test_workspace,
):
    """GET /explore/entity/{nonexistent}/profile → 404."""
    resp = await client.get(
        f"/explore/entity/{uuid.uuid4()}/profile",
        headers=headers(test_workspace),
    )
    assert resp.status_code == 404


async def test_explore_entity_profile_returns_buckets_for_seeded_entity(
    client, test_workspace, db_url_superuser,
):
    """Seed entity + a file + a mention + mention_to_entity link, then
    confirm the profile rollup names the file's doc_type bucket."""
    entity_id = str(uuid.uuid4())
    file_id = str(uuid.uuid4())
    chunk_id = str(uuid.uuid4())
    mention_id = str(uuid.uuid4())

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        await conn.execute(
            "INSERT INTO entities "
            "  (id, workspace_id, canonical_name, entity_type, mention_count) "
            "VALUES (%s, %s, 'Acme Corp', 'ORG', 1)",
            (entity_id, test_workspace),
        )
        await conn.execute(
            "INSERT INTO files (id, workspace_id, name, mime_type, "
            "size_bytes, content_sha, object_key, lifecycle_state, "
            "inferred_doc_type) "
            "VALUES (%s, %s, 'msa.pdf', 'application/pdf', 0, "
            "        repeat('a', 64), 'k/msa', 'ready', "
            "        'master_services_agreement')",
            (file_id, test_workspace),
        )
        # Need a chunk + contextual_chunk to satisfy the mentions FK chain.
        await conn.execute(
            "INSERT INTO chunks (id, workspace_id, file_id, chunk_index, "
            "                    text, token_count, content_sha) "
            "VALUES (%s, %s, %s, 0, 'seed', 1, repeat('b', 64))",
            (chunk_id, test_workspace, file_id),
        )
        # contextual_chunks: chunk_id is the parent FK
        ctx_id = str(uuid.uuid4())
        await conn.execute(
            "INSERT INTO contextual_chunks "
            "  (id, chunk_id, file_id, workspace_id, "
            "   contextual_prefix, contextual_text, model_id, "
            "   prefix_token_count) "
            "VALUES (%s, %s, %s, %s, '', 'seed text', 'test', 0)",
            (ctx_id, chunk_id, file_id, test_workspace),
        )
        await conn.execute(
            "INSERT INTO extracted_mentions "
            "  (id, contextual_chunk_id, file_id, workspace_id, "
            "   mention_text, mention_type, start_offset, end_offset, "
            "   confidence, model_id) "
            "VALUES (%s, %s, %s, %s, 'Acme Corp', 'ORG', 0, 9, 1.0, 'test')",
            (mention_id, ctx_id, file_id, test_workspace),
        )
        await conn.execute(
            "INSERT INTO mention_to_entity "
            "  (mention_id, entity_id, workspace_id, "
            "   confidence, resolved_method) "
            "VALUES (%s, %s, %s, 1.0, 'deterministic')",
            (mention_id, entity_id, test_workspace),
        )
        await conn.commit()

    resp = await client.get(
        f"/explore/entity/{entity_id}/profile",
        headers=headers(test_workspace),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["canonical_name"] == "Acme Corp"
    assert body["entity_type"] == "ORG"
    assert body["n_docs"] == 1
    # MSA bucket: doc_type = master_services_agreement → "contracts" group.
    bucket_keys = {b["key"] for b in body["related"]}
    assert "contracts" in bucket_keys
