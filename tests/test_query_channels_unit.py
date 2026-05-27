"""Phase 8b — 6-channel retrieval unit tests against testcontainers."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

import psycopg
import pytest


pytestmark = pytest.mark.asyncio


def _sha64(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


async def _seed_file_chain(
    conn,
    workspace_id: str,
    *,
    label: str,
    contextual_text: str = "marker zxqvbnm content",
) -> tuple[str, str, str]:
    """Seed file → raw_page → chunk → contextual_chunk in workspace.
    Returns (file_id, chunk_id, contextual_chunk_id)."""
    await conn.execute(
        "SELECT set_config('app.workspace_id', %s, true)", (workspace_id,),
    )
    file_id = str(uuid.uuid4())
    sha = _sha64(f"{label}-{workspace_id}")
    await conn.execute(
        "INSERT INTO files (id, workspace_id, name, content_sha, object_key, "
        "mime_type, size_bytes, lifecycle_state) "
        "VALUES (%s, %s, %s, %s, %s, 'application/pdf', 100, 'ready')",
        (file_id, workspace_id, f"{label}.pdf", sha, f"raw_files/{sha}"),
    )
    await conn.execute(
        "INSERT INTO raw_pages (id, file_id, workspace_id, page_number, text, "
        "layout_json, content_sha) "
        "VALUES (%s, %s, %s, 1, %s, '{}'::jsonb, %s)",
        (str(uuid.uuid4()), file_id, workspace_id, "page text", sha),
    )
    chunk_id = str(uuid.uuid4())
    chunk_sha = _sha64(f"chunk-{label}-{workspace_id}")
    await conn.execute(
        "INSERT INTO chunks (id, file_id, workspace_id, chunk_index, text, "
        "source_page_numbers, token_count, content_sha) "
        "VALUES (%s, %s, %s, 0, %s, %s, 5, %s)",
        (chunk_id, file_id, workspace_id, "chunk text", [1], chunk_sha),
    )
    cc_id = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO contextual_chunks (id, chunk_id, file_id, workspace_id, "
        "contextual_prefix, contextual_text, model_id, prefix_token_count, "
        "cache_creation_input_tokens, cache_read_input_tokens) "
        "VALUES (%s, %s, %s, %s, '', %s, 'identity', 0, 0, 0)",
        (cc_id, chunk_id, file_id, workspace_id, contextual_text),
    )
    return file_id, chunk_id, cc_id


# ===========================================================================
# Channels — one happy-path test each
# ===========================================================================


async def test_bm25_chunks_channel_returns_keyword_match(client, db_url_superuser):
    from kb.query.channels import bm25_chunks_channel

    workspace = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await _seed_file_chain(
            conn, workspace, label="bm25c",
            contextual_text="this chunk talks about zxqvbnm-unique-marker in detail",
        )
        await conn.commit()

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (workspace,),
        )
        hits = await bm25_chunks_channel(
            conn, workspace_id=workspace, query="zxqvbnm-unique-marker", limit=5,
        )
    assert len(hits) >= 1
    assert hits[0].kind == "chunk"
    assert "zxqvbnm" in hits[0].snippet
    assert hits[0].metadata.get("channel") == "bm25_chunks"
    assert hits[0].metadata.get("level") == 1


async def test_bm25_raptor_channel_returns_keyword_match(client, db_url_superuser):
    from kb.query.channels import bm25_raptor_channel

    workspace = str(uuid.uuid4())
    file_id = str(uuid.uuid4())
    sha = _sha64(f"r-{workspace}")
    vec = [0.0] * 3072
    vec[0] = 1.0
    vec_literal = "[" + ",".join(repr(float(v)) for v in vec) + "]"
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        await conn.execute(
            "INSERT INTO files (id, workspace_id, name, content_sha, object_key, "
            "mime_type, size_bytes, lifecycle_state) "
            "VALUES (%s, %s, 'r.pdf', %s, %s, 'application/pdf', 100, 'ready')",
            (file_id, workspace, sha, f"raw_files/{sha}"),
        )
        await conn.execute(
            "INSERT INTO raptor_nodes (scope, file_id, workspace_id, level, text, "
            "embedding, cluster_id_in_level, summarizer_model_id, embedder_model_id) "
            "VALUES ('per_doc', %s, %s, 2, %s, %s::halfvec, 0, 'identity', 'mock')",
            (file_id, workspace, "raptor summary mentioning yyqlmnop-special-token", vec_literal),
        )
        await conn.commit()

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        hits = await bm25_raptor_channel(
            conn, workspace_id=workspace, query="yyqlmnop-special-token", limit=5,
        )
    assert len(hits) >= 1
    assert hits[0].kind == "raptor_node"
    assert hits[0].metadata.get("channel") == "bm25_raptor"
    assert hits[0].metadata.get("level") == 2


async def test_dense_chunks_channel_returns_cosine_match(client, db_url_superuser):
    from kb.query.channels import dense_chunks_channel

    workspace = str(uuid.uuid4())
    one_hot = [0.0] * 3072
    one_hot[0] = 1.0
    vec_literal = "[" + ",".join(repr(float(v)) for v in one_hot) + "]"
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        _, _, cc_id = await _seed_file_chain(conn, workspace, label="dc")
        # Add chunk_embedding with one-hot
        await conn.execute(
            "INSERT INTO chunk_embeddings (contextual_chunk_id, file_id, "
            "workspace_id, embedding, model_id) "
            "VALUES (%s, (SELECT file_id FROM contextual_chunks WHERE id = %s), %s, %s::halfvec, 'mock')",
            (cc_id, cc_id, workspace, vec_literal),
        )
        await conn.commit()

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        hits = await dense_chunks_channel(
            conn, workspace_id=workspace, query_vec=one_hot, limit=5,
        )
    assert len(hits) >= 1
    assert hits[0].kind == "chunk"
    assert hits[0].id == cc_id
    assert hits[0].metadata.get("channel") == "dense_chunks"
    assert hits[0].score == pytest.approx(1.0, abs=0.01)  # cosine 1.0


async def test_dense_raptor_channel_returns_cosine_match(client, db_url_superuser):
    from kb.query.channels import dense_raptor_channel

    workspace = str(uuid.uuid4())
    one_hot = [0.0] * 3072
    one_hot[0] = 1.0
    vec_literal = "[" + ",".join(repr(float(v)) for v in one_hot) + "]"
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        file_id = str(uuid.uuid4())
        sha = _sha64(f"dr-{workspace}")
        await conn.execute(
            "INSERT INTO files (id, workspace_id, name, content_sha, object_key, "
            "mime_type, size_bytes, lifecycle_state) "
            "VALUES (%s, %s, 'r.pdf', %s, %s, 'application/pdf', 100, 'ready')",
            (file_id, workspace, sha, f"raw_files/{sha}"),
        )
        await conn.execute(
            "INSERT INTO raptor_nodes (scope, file_id, workspace_id, level, text, "
            "embedding, cluster_id_in_level, summarizer_model_id, embedder_model_id) "
            "VALUES ('per_doc', %s, %s, 2, 'summary', %s::halfvec, 0, 'identity', 'mock')",
            (file_id, workspace, vec_literal),
        )
        await conn.commit()

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        hits = await dense_raptor_channel(
            conn, workspace_id=workspace, query_vec=one_hot, limit=5,
        )
    assert len(hits) >= 1
    assert hits[0].kind == "raptor_node"
    assert hits[0].metadata.get("channel") == "dense_raptor"


async def test_mentions_exact_channel_returns_chunk_kind_hit(client, db_url_superuser):
    from kb.query.channels import mentions_exact_channel

    workspace = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        _, _, cc_id = await _seed_file_chain(conn, workspace, label="me")
        await conn.execute(
            "INSERT INTO extracted_mentions "
            "(contextual_chunk_id, file_id, workspace_id, mention_text, mention_type, model_id) "
            "VALUES (%s, (SELECT file_id FROM contextual_chunks WHERE id = %s), %s, 'Aakash Constructions', 'ORG', 'identity')",
            (cc_id, cc_id, workspace),
        )
        await conn.commit()

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        # Case-insensitive substring
        hits = await mentions_exact_channel(
            conn, workspace_id=workspace, query="aakash", limit=5,
        )
    assert len(hits) >= 1
    # Decision #7: kind='chunk' (resolves to contextual_chunk_id of the mention)
    assert hits[0].kind == "chunk"
    assert hits[0].id == cc_id
    assert hits[0].metadata.get("channel") == "mentions_exact"
    assert "Aakash Constructions" in hits[0].metadata.get("matched_mention", "")


async def _seed_sub_entity(
    conn: Any,
    *,
    file_id: str,
    workspace_id: str,
    unit_type: str,
    rarity_score: float,
    fields: dict | None = None,
) -> str:
    """Insert a sub_entity extracted_entities row for channel-test seeds.

    Replaces direct atomic_units INSERTs after the nested-entities
    refactor (atomic_units stays as transient staging; the channel
    now reads from extracted_entities filtered by `unit_type IS NOT
    NULL`). Creates a minimal sub_entity schema_entity on demand.
    """
    cur = await conn.execute(
        "INSERT INTO schemas (workspace_id, name, lifecycle_state) "
        "VALUES (%s, %s, 'active') "
        "ON CONFLICT DO NOTHING "
        "RETURNING id",
        (workspace_id, f"auto:test:{unit_type}"),
    )
    row = await cur.fetchone()
    if row is None:
        cur = await conn.execute(
            "SELECT id FROM schemas WHERE workspace_id = %s AND name = %s",
            (workspace_id, f"auto:test:{unit_type}"),
        )
        row = await cur.fetchone()
    schema_id = row[0]
    # Create a doc_root + sub_entity pair so the CHECK constraint
    # passes (`kind='sub_entity'` requires parent_type_id IS NOT NULL).
    cur = await conn.execute(
        "INSERT INTO schema_entities "
        "  (schema_id, workspace_id, name, lifecycle_state, kind) "
        "VALUES (%s, %s, %s, 'active', 'doc_root') "
        "RETURNING id",
        (schema_id, workspace_id, f"Doc_{unit_type}"),
    )
    doc_root_id = (await cur.fetchone())[0]
    cur = await conn.execute(
        "INSERT INTO schema_entities "
        "  (schema_id, workspace_id, name, lifecycle_state, kind, parent_type_id) "
        "VALUES (%s, %s, %s, 'active', 'sub_entity', %s) "
        "RETURNING id",
        (schema_id, workspace_id, unit_type.capitalize(), doc_root_id),
    )
    se_id = (await cur.fetchone())[0]
    cur = await conn.execute(
        "INSERT INTO extracted_entities "
        "  (schema_entity_id, file_id, workspace_id, fields, citations, "
        "   model_id, rarity_score, unit_type) "
        "VALUES (%s, %s, %s, %s::jsonb, '{}'::jsonb, 'mock', %s, %s) "
        "RETURNING id",
        (se_id, file_id, workspace_id, json.dumps(fields or {}),
         rarity_score, unit_type),
    )
    return (await cur.fetchone())[0]


async def test_sub_entities_rarity_channel_filters_by_unit_type_keyword(
    client, db_url_superuser,
):
    from kb.query.channels import sub_entities_rarity_channel

    workspace = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        file_id, _, _ = await _seed_file_chain(conn, workspace, label="au")
        # Sub_entity seeds in extracted_entities — channel reads from
        # there after the nested-entities refactor.
        await _seed_sub_entity(
            conn, file_id=file_id, workspace_id=workspace,
            unit_type="clause", rarity_score=0.9,
            fields={"clause_type": "indemnification"},
        )
        await _seed_sub_entity(
            conn, file_id=file_id, workspace_id=workspace,
            unit_type="transaction", rarity_score=0.95,
            fields={"amount": 1250},
        )
        await conn.commit()

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        # Query mentions 'clause' → filter to clause type
        hits = await sub_entities_rarity_channel(
            conn, workspace_id=workspace, query="any clause questions", limit=5,
        )
    assert len(hits) >= 1
    assert all(h.metadata.get("unit_type") == "clause" for h in hits)


async def test_sub_entities_rarity_channel_no_keyword_returns_all_types(
    client, db_url_superuser,
):
    """Query has no unit_type keyword → returns top across all unit_types."""
    from kb.query.channels import sub_entities_rarity_channel

    workspace = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        file_id, _, _ = await _seed_file_chain(conn, workspace, label="au2")
        await _seed_sub_entity(
            conn, file_id=file_id, workspace_id=workspace,
            unit_type="clause", rarity_score=0.9,
        )
        await _seed_sub_entity(
            conn, file_id=file_id, workspace_id=workspace,
            unit_type="transaction", rarity_score=0.95,
        )
        await conn.commit()

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        hits = await sub_entities_rarity_channel(
            conn, workspace_id=workspace, query="generic question", limit=5,
        )
    types = {h.metadata.get("unit_type") for h in hits}
    assert types == {"clause", "transaction"}


async def test_channels_filter_out_soft_deleted_files(client, db_url_superuser):
    """Soft-deleted files (lifecycle_state='deleted') must not surface in
    retrieval.

    Regression: when a content_sha dedup landed (duplicate upload), the
    losing file got soft-deleted but its derived rows (chunks /
    contextual_chunks / embeddings / mentions / atomic_units) stayed in
    place. Pre-fix every channel returned hits for those ghost rows,
    which broke citation labels (file_id pointed at a gone file) AND
    silently undermined R1's superseded-tagging (the loser the
    detector wanted to mark wasn't the file_id the retriever returned).
    """
    from kb.query.channels import (
        bm25_chunks_channel,
        dense_chunks_channel,
    )

    workspace = str(uuid.uuid4())
    one_hot = [0.0] * 3072
    one_hot[0] = 1.0

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        # Seed two files with identical-marker chunks. Then soft-delete
        # one. BM25 + dense should only return the live one.
        file_live, _, _ = await _seed_file_chain(
            conn, workspace, label="live",
            contextual_text="ghost-marker-zxqvbnm appears in both files",
        )
        file_dead, _, cc_dead = await _seed_file_chain(
            conn, workspace, label="dead",
            contextual_text="ghost-marker-zxqvbnm appears in both files",
        )
        # Embed both contextual_chunks at the same one-hot vector so
        # dense channel can find both pre-filter.
        for cc_id in (
            await _fetch_cc_id_for_file(conn, file_live),
            cc_dead,
        ):
            await _insert_chunk_embedding(conn, workspace, cc_id, one_hot)
        # Soft-delete the dead file.
        await conn.execute(
            "UPDATE files SET lifecycle_state = 'deleted' WHERE id = %s",
            (file_dead,),
        )
        await conn.commit()

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (workspace,),
        )
        bm25_hits = await bm25_chunks_channel(
            conn, workspace_id=workspace, query="ghost-marker-zxqvbnm", limit=10,
        )
        dense_hits = await dense_chunks_channel(
            conn, workspace_id=workspace, query_vec=one_hot, limit=10,
        )

    bm25_files = {h.metadata.get("file_id") for h in bm25_hits}
    dense_files = {h.metadata.get("file_id") for h in dense_hits}
    assert file_dead not in bm25_files, "bm25 leaked deleted file"
    assert file_dead not in dense_files, "dense leaked deleted file"
    # The live file must still be returned by both.
    assert file_live in bm25_files
    assert file_live in dense_files


async def _fetch_cc_id_for_file(conn, file_id: str) -> str:
    cur = await conn.execute(
        "SELECT id::text FROM contextual_chunks WHERE file_id = %s LIMIT 1",
        (file_id,),
    )
    row = await cur.fetchone()
    assert row is not None
    return row[0]


async def _insert_chunk_embedding(
    conn, workspace_id: str, cc_id: str, vec: list[float],
) -> None:
    """chunk_embeddings.file_id is NOT NULL (per migration 0014)."""
    cur = await conn.execute(
        "SELECT file_id::text FROM contextual_chunks WHERE id = %s",
        (cc_id,),
    )
    file_id = (await cur.fetchone())[0]
    vec_lit = "[" + ",".join(repr(float(v)) for v in vec) + "]"
    await conn.execute(
        "INSERT INTO chunk_embeddings "
        "  (id, contextual_chunk_id, file_id, workspace_id, model_id, embedding) "
        "VALUES (%s, %s, %s, %s, 'identity', %s::halfvec)",
        (str(uuid.uuid4()), cc_id, file_id, workspace_id, vec_lit),
    )


async def test_channels_respect_workspace_isolation(client, db_url_superuser):
    """Decision #10: every channel filters by workspace_id."""
    from kb.query.channels import bm25_chunks_channel

    ws_a = str(uuid.uuid4())
    ws_b = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await _seed_file_chain(
            conn, ws_a, label="iso",
            contextual_text="iso-marker present in workspace A",
        )
        await conn.commit()

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (ws_b,))
        hits = await bm25_chunks_channel(
            conn, workspace_id=ws_b, query="iso-marker", limit=5,
        )
    assert hits == []  # workspace B sees nothing


async def test_run_all_channels_returns_dict_with_all_6_keys(client, db_url_superuser):
    from kb.query.channels import run_all_channels

    workspace = str(uuid.uuid4())
    one_hot = [0.0] * 3072
    one_hot[0] = 1.0
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        result = await run_all_channels(
            conn, workspace_id=workspace, query="anything", query_vec=one_hot,
        )
    assert set(result.keys()) == {
        "bm25_chunks", "bm25_raptor", "dense_chunks", "dense_raptor",
        "mentions_exact", "sub_entities_rarity",
    }


async def test_run_all_channels_swallows_channel_exception(client, db_url_superuser, monkeypatch):
    """Decision #4: if one channel raises, others still run; failed channel
    gets empty list."""
    from kb.query import channels as channels_mod

    workspace = str(uuid.uuid4())

    async def _broken_channel(conn, **kwargs):
        raise RuntimeError("simulated channel failure")

    monkeypatch.setattr(channels_mod, "bm25_chunks_channel", _broken_channel)

    one_hot = [0.0] * 3072
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        result = await channels_mod.run_all_channels(
            conn, workspace_id=workspace, query="x", query_vec=one_hot,
        )
    # Failed channel returns [], others return [] too (empty workspace) — but
    # no exception propagated.
    assert "bm25_chunks" in result
    assert result["bm25_chunks"] == []
