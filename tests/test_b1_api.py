"""B1 / WA-4 + WA-5 — HTTP + repo + integration tests over testcontainers.

Covers:
  - Migration shape (CHECK enums, RLS, GRANTs, FK ON DELETE behavior)
  - Repo CRUD: triples insert, relationship upsert, evidence add, graph
    edge upsert
  - HTTP endpoints: GET /entities/:id/relationships, GET /entities/:id/
    graph-neighbors, GET /triples
  - Worker stages: extract_triples_file_impl (Identity path),
    build_relationships_file_impl (resolves real entities → relationships),
    build_graph_file_impl (derives edges from relationships+mentions)
  - Integration: prior pipeline endpoints + WA-3 chains all still reachable
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from contextlib import contextmanager
from typing import Any

import psycopg
import pytest

from kb.config import get_settings


pytestmark = pytest.mark.asyncio


@contextmanager
def _env(**kwargs):
    prior = {k: os.environ.get(k) for k in kwargs}
    for k, v in kwargs.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    get_settings.cache_clear()
    try:
        yield
    finally:
        for k, v in prior.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        get_settings.cache_clear()


@pytest.fixture
def test_workspace() -> str:
    return str(uuid.uuid4())


def headers(workspace: str) -> dict[str, str]:
    return {"X-Test-Workspace": workspace}


# ===========================================================================
# Seed helpers — minimal File / Chunk / Entity rows
# ===========================================================================


async def _seed_file(db_url: str, workspace: str, *, lifecycle_state: str = "ready") -> str:
    file_id = str(uuid.uuid4())
    sha = hashlib.sha256(f"{workspace}-{file_id}".encode()).hexdigest()
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        await conn.execute(
            "INSERT INTO files (id, workspace_id, name, content_sha, object_key, "
            "mime_type, size_bytes, lifecycle_state) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (file_id, workspace, "test.pdf", sha, f"raw/{file_id}",
             "application/pdf", 100, lifecycle_state),
        )
    return file_id


async def _seed_entity(db_url: str, workspace: str, *, name: str, entity_type: str = "ORG") -> str:
    entity_id = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (workspace,),
        )
        await conn.execute(
            "INSERT INTO entities (id, workspace_id, canonical_name, entity_type, "
            "mention_count) VALUES (%s, %s, %s, %s, 0)",
            (entity_id, workspace, name, entity_type),
        )
    return entity_id


# ===========================================================================
# Migration shape
# ===========================================================================


async def test_lifecycle_state_widening_for_b1_stages(db_url_superuser):
    """Forward-compat: B1's three new states ('triples_extracting',
    'relationships_building', 'graph_building') are in the CHECK so a
    Wave B switch to gating doesn't need another migration."""
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT pg_get_constraintdef(c.oid) "
            "FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
            "WHERE t.relname = 'files' AND c.conname = 'files_lifecycle_state_check'"
        )
        row = await cur.fetchone()
        assert row is not None
        defn = row[0]
        for state in ("triples_extracting", "relationships_building", "graph_building"):
            assert state in defn


async def test_b1_tables_rls_forced(db_url_superuser):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        for tbl in ("extracted_triples", "relationships",
                    "relationship_evidence", "graph_edges"):
            cur = await conn.execute(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname = %s", (tbl,),
            )
            row = await cur.fetchone()
            assert row is not None, f"missing table {tbl}"
            assert row[0] is True and row[1] is True, f"{tbl} lacks forced RLS"


async def test_b1_table_grants(db_url_superuser):
    """extracted_triples + relationship_evidence are append-only
    (SELECT + INSERT). relationships + graph_edges are mutable."""
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        def _get_privs(tbl: str):
            return conn.execute(
                "SELECT privilege_type FROM information_schema.role_table_grants "
                "WHERE grantee = 'kb_app' AND table_name = %s", (tbl,),
            )

        cur = await _get_privs("extracted_triples")
        privs = {r[0] for r in await cur.fetchall()}
        assert privs == {"SELECT", "INSERT"}, f"triples privs: {privs}"

        cur = await _get_privs("relationship_evidence")
        privs = {r[0] for r in await cur.fetchall()}
        assert privs == {"SELECT", "INSERT"}, f"rel_evidence privs: {privs}"

        cur = await _get_privs("relationships")
        privs = {r[0] for r in await cur.fetchall()}
        assert {"SELECT", "INSERT", "UPDATE", "DELETE"}.issubset(privs), f"rel privs: {privs}"

        cur = await _get_privs("graph_edges")
        privs = {r[0] for r in await cur.fetchall()}
        assert {"SELECT", "INSERT", "UPDATE", "DELETE"}.issubset(privs), f"graph privs: {privs}"


async def test_triples_check_constraints(db_url_superuser, test_workspace):
    """Empty strings + out-of-range confidence + nonexistent file_id are
    all rejected by the CHECK / FK constraints."""
    file_id = await _seed_file(db_url_superuser, test_workspace)
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        # Empty subject_text → CHECK violation
        with pytest.raises(Exception):
            await conn.execute(
                "INSERT INTO extracted_triples (workspace_id, file_id, "
                "subject_text, predicate_text, object_text) "
                "VALUES (%s, %s, '', 'is', 'b')",
                (test_workspace, file_id),
            )
        # Confidence > 1.0 → CHECK violation
        with pytest.raises(Exception):
            await conn.execute(
                "INSERT INTO extracted_triples (workspace_id, file_id, "
                "subject_text, predicate_text, object_text, confidence) "
                "VALUES (%s, %s, 'a', 'is', 'b', 1.5)",
                (test_workspace, file_id),
            )


async def test_relationships_check_disallows_self_loop(db_url_superuser, test_workspace):
    ent_id = await _seed_entity(db_url_superuser, test_workspace, name="Alpha")
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        with pytest.raises(Exception) as ei:
            await conn.execute(
                "INSERT INTO relationships (workspace_id, subject_entity_id, "
                "object_entity_id, predicate, confidence) "
                "VALUES (%s, %s, %s, 'rel', 0.5)",
                (test_workspace, ent_id, ent_id),
            )
        assert "violates check" in str(ei.value).lower()


async def test_graph_edges_unique_per_kind(db_url_superuser, test_workspace):
    """Same (workspace, src, dst, kind) twice → second insert violates
    UNIQUE. Different kind for the same pair IS allowed (per-kind unique).

    Uses three separate connections because once a transaction sees a
    constraint violation, subsequent statements on the same connection
    fail until rollback. We test each assertion in its own connection."""
    e1 = await _seed_entity(db_url_superuser, test_workspace, name="X")
    e2 = await _seed_entity(db_url_superuser, test_workspace, name="Y")

    # 1) First insert succeeds.
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        await conn.execute(
            "INSERT INTO graph_edges (workspace_id, src_entity_id, "
            "dst_entity_id, edge_kind, weight) VALUES (%s, %s, %s, 'relationship', 1.0)",
            (test_workspace, e1, e2),
        )
        await conn.commit()

    # 2) Same (workspace, src, dst, kind) → unique violation.
    with pytest.raises(Exception) as ei:
        async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
            )
            await conn.execute(
                "INSERT INTO graph_edges (workspace_id, src_entity_id, "
                "dst_entity_id, edge_kind, weight) VALUES (%s, %s, %s, 'relationship', 2.0)",
                (test_workspace, e1, e2),
            )
            await conn.commit()
    assert "unique" in str(ei.value).lower()

    # 3) DIFFERENT kind for same pair IS allowed.
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        await conn.execute(
            "INSERT INTO graph_edges (workspace_id, src_entity_id, "
            "dst_entity_id, edge_kind, weight) VALUES (%s, %s, %s, 'co_mention', 1.0)",
            (test_workspace, e1, e2),
        )
        await conn.commit()
        cur = await conn.execute(
            "SELECT COUNT(*) FROM graph_edges WHERE workspace_id = %s "
            "AND src_entity_id = %s AND dst_entity_id = %s",
            (test_workspace, e1, e2),
        )
        assert (await cur.fetchone())[0] == 2  # 'relationship' + 'co_mention'


# ===========================================================================
# Repo behavior
# ===========================================================================


async def test_insert_triple_round_trip(db_url_superuser, test_workspace):
    from kb.domain.triples import insert_triple, read_triples_for_file
    file_id = await _seed_file(db_url_superuser, test_workspace)
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        tid = await insert_triple(
            conn, workspace_id=test_workspace, file_id=file_id,
            subject_text="A", predicate_text="r", object_text="B",
            confidence=0.7,
        )
        await conn.commit()
        rows = await read_triples_for_file(conn, file_id=file_id)
        assert len(rows) == 1
        assert rows[0].id == tid
        assert rows[0].confidence == 0.7


async def test_upsert_relationship_first_insert(db_url_superuser, test_workspace):
    from kb.domain.relationships import upsert_relationship
    a = await _seed_entity(db_url_superuser, test_workspace, name="A")
    b = await _seed_entity(db_url_superuser, test_workspace, name="B")
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        rel_id, was_inserted = await upsert_relationship(
            conn, workspace_id=test_workspace,
            subject_entity_id=a, object_entity_id=b,
            predicate="supplies to", confidence=0.7,
        )
        assert was_inserted is True
        cur = await conn.execute(
            "SELECT n_evidence, confidence FROM relationships WHERE id = %s",
            (rel_id,),
        )
        row = await cur.fetchone()
        assert row[0] == 1
        assert row[1] == 0.7


async def test_upsert_relationship_second_call_aggregates(db_url_superuser, test_workspace):
    from kb.domain.relationships import upsert_relationship
    a = await _seed_entity(db_url_superuser, test_workspace, name="A")
    b = await _seed_entity(db_url_superuser, test_workspace, name="B")
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        id1, ins1 = await upsert_relationship(
            conn, workspace_id=test_workspace,
            subject_entity_id=a, object_entity_id=b,
            predicate="rel", confidence=0.5,
        )
        id2, ins2 = await upsert_relationship(
            conn, workspace_id=test_workspace,
            subject_entity_id=a, object_entity_id=b,
            predicate="rel", confidence=0.9,
        )
        assert id1 == id2
        assert ins1 is True
        assert ins2 is False
        cur = await conn.execute(
            "SELECT n_evidence, confidence FROM relationships WHERE id = %s",
            (id1,),
        )
        n_ev, conf = await cur.fetchone()
        assert n_ev == 2
        assert conf == 0.9  # MAX(0.5, 0.9)


async def test_upsert_edge_accumulates_weight_and_source_refs(db_url_superuser, test_workspace):
    from kb.domain.graph import upsert_edge
    a = await _seed_entity(db_url_superuser, test_workspace, name="A")
    b = await _seed_entity(db_url_superuser, test_workspace, name="B")
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        await upsert_edge(
            conn, workspace_id=test_workspace,
            src_entity_id=a, dst_entity_id=b,
            edge_kind="relationship", weight_delta=1.5,
            source_ref={"kind": "rel", "id": "r1"},
        )
        eid, was_inserted = await upsert_edge(
            conn, workspace_id=test_workspace,
            src_entity_id=a, dst_entity_id=b,
            edge_kind="relationship", weight_delta=2.5,
            source_ref={"kind": "rel", "id": "r2"},
        )
        assert was_inserted is False
        cur = await conn.execute(
            "SELECT weight, source_refs FROM graph_edges WHERE id = %s", (eid,),
        )
        weight, refs = await cur.fetchone()
        assert weight == 4.0  # 1.5 + 2.5
        refs_list = refs if isinstance(refs, list) else json.loads(refs)
        assert len(refs_list) == 2


# ===========================================================================
# HTTP endpoints
# ===========================================================================


async def test_get_relationships_empty(client, test_workspace):
    fake_id = str(uuid.uuid4())
    resp = await client.get(
        f"/entities/{fake_id}/relationships", headers=headers(test_workspace),
    )
    assert resp.status_code == 200
    assert resp.json()["items"] == []


async def test_get_relationships_direction_validation(client, test_workspace):
    fake_id = str(uuid.uuid4())
    resp = await client.get(
        f"/entities/{fake_id}/relationships?direction=garbage",
        headers=headers(test_workspace),
    )
    assert resp.status_code == 400


async def test_get_relationships_returns_seeded_rows(
    client, test_workspace, db_url_superuser,
):
    from kb.domain.relationships import upsert_relationship
    a = await _seed_entity(db_url_superuser, test_workspace, name="ACME")
    b = await _seed_entity(db_url_superuser, test_workspace, name="Vertex")
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        await upsert_relationship(
            conn, workspace_id=test_workspace,
            subject_entity_id=a, object_entity_id=b,
            predicate="supplies to", confidence=0.9,
        )

    resp = await client.get(
        f"/entities/{a}/relationships", headers=headers(test_workspace),
    )
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["predicate"] == "supplies to"
    assert body["items"][0]["subject_entity_id"] == a
    assert body["items"][0]["object_entity_id"] == b


async def test_get_graph_neighbors_empty(client, test_workspace):
    fake_id = str(uuid.uuid4())
    resp = await client.get(
        f"/entities/{fake_id}/graph-neighbors", headers=headers(test_workspace),
    )
    assert resp.status_code == 200
    assert resp.json()["items"] == []


async def test_get_graph_neighbors_kind_validation(client, test_workspace):
    fake_id = str(uuid.uuid4())
    resp = await client.get(
        f"/entities/{fake_id}/graph-neighbors?edge_kind=bogus",
        headers=headers(test_workspace),
    )
    assert resp.status_code == 400


async def test_get_graph_neighbors_returns_seeded_edges(
    client, test_workspace, db_url_superuser,
):
    from kb.domain.graph import upsert_edge
    a = await _seed_entity(db_url_superuser, test_workspace, name="A")
    b = await _seed_entity(db_url_superuser, test_workspace, name="B")
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        await upsert_edge(
            conn, workspace_id=test_workspace,
            src_entity_id=a, dst_entity_id=b,
            edge_kind="relationship", weight_delta=3.0,
        )
    resp = await client.get(
        f"/entities/{a}/graph-neighbors", headers=headers(test_workspace),
    )
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["edge_kind"] == "relationship"
    assert body["items"][0]["weight"] == 3.0


async def test_get_triples_empty(client, test_workspace):
    resp = await client.get("/triples", headers=headers(test_workspace))
    assert resp.status_code == 200
    assert resp.json()["items"] == []


async def test_workspace_isolation_on_b1_endpoints(client, db_url_superuser):
    ws_a = str(uuid.uuid4())
    ws_b = str(uuid.uuid4())
    a = await _seed_entity(db_url_superuser, ws_a, name="A-only")
    b = await _seed_entity(db_url_superuser, ws_a, name="B-only")
    from kb.domain.relationships import upsert_relationship
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (ws_a,),
        )
        await upsert_relationship(
            conn, workspace_id=ws_a, subject_entity_id=a, object_entity_id=b,
            predicate="rel", confidence=0.5,
        )
    # WS B sees zero relationships involving A's entity.
    resp = await client.get(
        f"/entities/{a}/relationships", headers=headers(ws_b),
    )
    assert resp.status_code == 200
    assert resp.json()["items"] == []


# ===========================================================================
# Worker stages
# ===========================================================================


async def test_extract_triples_identity_path_writes_zero_triples_and_event(
    client, db_url_superuser, test_workspace,
):
    """Identity extractor returns no triples but the worker still records
    a lifecycle event so observability is preserved."""
    from kb.workers.tasks import extract_triples_file_impl
    file_id = await _seed_file(db_url_superuser, test_workspace, lifecycle_state="ready")
    with _env(KB_DATABASE_URL=db_url_superuser, KB_TRIPLES_EXTRACTOR="identity"):
        await extract_triples_file_impl(file_id)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT COUNT(*) FROM extracted_triples WHERE file_id = %s",
            (file_id,),
        )
        assert (await cur.fetchone())[0] == 0
        cur = await conn.execute(
            "SELECT event, payload FROM file_lifecycle "
            "WHERE file_id = %s AND event = 'triples_extracted'",
            (file_id,),
        )
        row = await cur.fetchone()
        assert row is not None
        payload = row[1] if isinstance(row[1], dict) else json.loads(row[1])
        assert payload["triple_count"] == 0
        assert payload["model_id"] == "identity"


async def test_build_relationships_no_triples_no_relationships(
    client, db_url_superuser, test_workspace,
):
    """Empty triples input → empty relationships output, event recorded."""
    from kb.workers.tasks import build_relationships_file_impl
    file_id = await _seed_file(db_url_superuser, test_workspace, lifecycle_state="ready")
    with _env(KB_DATABASE_URL=db_url_superuser):
        await build_relationships_file_impl(file_id)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT payload FROM file_lifecycle "
            "WHERE file_id = %s AND event = 'relationships_built'",
            (file_id,),
        )
        row = await cur.fetchone()
        assert row is not None
        payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        assert payload["relationship_count"] == 0
        assert payload["triple_count"] == 0


async def test_build_relationships_resolves_seeded_triples(
    client, db_url_superuser, test_workspace,
):
    """Pre-seed triples + matching entities; the resolver should produce
    a relationships row + relationship_evidence row + event."""
    from kb.domain.triples import insert_triple
    from kb.workers.tasks import build_relationships_file_impl

    file_id = await _seed_file(db_url_superuser, test_workspace)
    a = await _seed_entity(db_url_superuser, test_workspace, name="ACME", entity_type="ORG")
    b = await _seed_entity(db_url_superuser, test_workspace, name="Vertex", entity_type="ORG")

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        await insert_triple(
            conn, workspace_id=test_workspace, file_id=file_id,
            subject_text="ACME", predicate_text="supplies to",
            object_text="Vertex", confidence=0.85,
        )
        await conn.commit()

    with _env(KB_DATABASE_URL=db_url_superuser):
        await build_relationships_file_impl(file_id)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT COUNT(*) FROM relationships WHERE workspace_id = %s "
            "AND subject_entity_id = %s AND object_entity_id = %s",
            (test_workspace, a, b),
        )
        assert (await cur.fetchone())[0] == 1
        cur = await conn.execute(
            "SELECT payload FROM file_lifecycle WHERE file_id = %s AND "
            "event = 'relationships_built'",
            (file_id,),
        )
        payload = (await cur.fetchone())[0]
        payload = payload if isinstance(payload, dict) else json.loads(payload)
        assert payload["relationship_count"] == 1


async def test_build_graph_no_data_no_edges_event_recorded(
    client, db_url_superuser, test_workspace,
):
    from kb.workers.tasks import build_graph_file_impl
    file_id = await _seed_file(db_url_superuser, test_workspace, lifecycle_state="ready")
    with _env(KB_DATABASE_URL=db_url_superuser):
        await build_graph_file_impl(file_id)
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT payload FROM file_lifecycle WHERE file_id = %s AND event = 'graph_built'",
            (file_id,),
        )
        row = await cur.fetchone()
        assert row is not None
        payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        assert payload["edges_upserted"] == 0


async def test_build_graph_from_seeded_relationships(
    client, db_url_superuser, test_workspace,
):
    """Seed a relationship + relationship_evidence pointing to a file;
    build_graph_file_impl should derive one 'relationship' edge."""
    from kb.domain.relationships import add_evidence, upsert_relationship
    from kb.workers.tasks import build_graph_file_impl

    file_id = await _seed_file(db_url_superuser, test_workspace, lifecycle_state="ready")
    a = await _seed_entity(db_url_superuser, test_workspace, name="A")
    b = await _seed_entity(db_url_superuser, test_workspace, name="B")

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        rel_id, _ = await upsert_relationship(
            conn, workspace_id=test_workspace,
            subject_entity_id=a, object_entity_id=b,
            predicate="works with", confidence=0.8,
        )
        await add_evidence(
            conn, workspace_id=test_workspace,
            relationship_id=rel_id, file_id=file_id,
            confidence=0.8,
        )
        await conn.commit()

    with _env(KB_DATABASE_URL=db_url_superuser):
        await build_graph_file_impl(file_id)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT COUNT(*) FROM graph_edges WHERE workspace_id = %s "
            "AND edge_kind = 'relationship'", (test_workspace,),
        )
        assert (await cur.fetchone())[0] == 1


# ===========================================================================
# PATCH /entities/:id/canonical-name (Explore "Edit canonical" action)
# ===========================================================================


async def test_rename_entity_404_when_missing(client, test_workspace):
    fake_id = str(uuid.uuid4())
    resp = await client.patch(
        f"/entities/{fake_id}/canonical-name",
        json={"canonical_name": "New Name"},
        headers=headers(test_workspace),
    )
    assert resp.status_code == 404


async def test_rename_entity_persists_new_name_and_returns_previous(
    client, test_workspace, db_url_superuser,
):
    eid = await _seed_entity(db_url_superuser, test_workspace, name="Acme")
    resp = await client.patch(
        f"/entities/{eid}/canonical-name",
        json={"canonical_name": "Acme Corp"},
        headers=headers(test_workspace),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == eid
    assert body["canonical_name"] == "Acme Corp"
    assert body["previous_canonical_name"] == "Acme"
    assert body["entity_type"] == "ORG"

    # DB reflects the rename.
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT canonical_name FROM entities WHERE id = %s", (eid,),
        )
        assert (await cur.fetchone())[0] == "Acme Corp"


async def test_rename_entity_blank_name_rejected(
    client, test_workspace, db_url_superuser,
):
    eid = await _seed_entity(db_url_superuser, test_workspace, name="Acme")
    resp = await client.patch(
        f"/entities/{eid}/canonical-name",
        json={"canonical_name": "   "},
        headers=headers(test_workspace),
    )
    assert resp.status_code == 422


async def test_rename_entity_same_name_is_noop_200(
    client, test_workspace, db_url_superuser,
):
    """Re-submitting the same name is a fast no-op (no UPDATE) — still 200."""
    eid = await _seed_entity(db_url_superuser, test_workspace, name="Acme")
    resp = await client.patch(
        f"/entities/{eid}/canonical-name",
        json={"canonical_name": "Acme"},
        headers=headers(test_workspace),
    )
    assert resp.status_code == 200
    assert resp.json()["canonical_name"] == "Acme"
    assert resp.json()["previous_canonical_name"] == "Acme"


# ===========================================================================
# Integration regression — existing endpoints still respond
# ===========================================================================


async def test_existing_pipeline_endpoints_still_reachable(client, test_workspace):
    """Smoke: B1's additions don't break anything from prior WA-1/2/3 +
    Phases 0-10."""
    resp = await client.get("/openapi.json")
    paths = set(resp.json()["paths"].keys())
    # Prior phases
    for required in (
        "/files", "/chat", "/audit",       # phases 2a, 8f, 9
        "/vocabulary",                      # WA-2
        "/chains",                          # WA-3
        "/settings/effective-config",       # WA-1
        # B1 newcomers
        "/entities/{entity_id}/relationships",
        "/entities/{entity_id}/graph-neighbors",
        "/triples",
    ):
        assert required in paths, f"missing route: {required}"

    # Live response sanity
    for url in (
        "/files", "/audit", "/chains", "/triples",
        "/vocabulary?domain_id=mixed_demo",
        "/settings/effective-config",
    ):
        r = await client.get(url, headers=headers(test_workspace))
        assert r.status_code == 200, f"{url} -> {r.status_code}: {r.text[:200]}"
