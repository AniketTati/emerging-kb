"""B2 / WA-6 — HTTP + repo + integration tests over testcontainers.

Covers:
  - Migration shape (files.source_authority + reason + doc_status; CHECKs;
    fact_conflicts table; RLS forced; GRANTs)
  - Repo CRUD (kb.domain.conflicts): set_source_authority_override,
    set_doc_status, read_file_authority, insert_conflict, read_conflicts_*,
    mark_conflict_resolved, apply_source_authority_from_config
  - HTTP endpoints: GET /conflicts (+ resolution filter),
    GET /entities/{id}/conflicts, POST /conflicts/{id}/resolve,
    GET /files/{id}/authority, POST /files/{id}/source-authority,
    POST /files/{id}/doc-status
  - Workspace isolation (RLS)
  - Worker integration: extract_fields_file_impl sets source_authority
    after inferring doc_type (Strategy B — additive, non-gating)
  - Regression: a prior endpoint (GET /triples) still works
"""

from __future__ import annotations

import hashlib
import os
import uuid
from contextlib import contextmanager

import psycopg
import pytest

from kb.config import get_settings
from kb.domain.conflicts import (
    DOC_STATUSES,
    RESOLUTIONS,
    apply_source_authority_from_config,
    insert_conflict,
    mark_conflict_resolved,
    read_conflict_by_id,
    read_conflicts_for_entity,
    read_conflicts_for_workspace,
    read_file_authority,
    set_doc_status,
    set_source_authority_override,
)


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
# Seed helpers
# ===========================================================================


async def _seed_file(
    db_url: str,
    workspace: str,
    *,
    lifecycle_state: str = "ready",
    inferred_doc_type: str | None = None,
    source_authority: float | None = None,
    doc_status: str | None = None,
) -> str:
    file_id = str(uuid.uuid4())
    sha = hashlib.sha256(f"{workspace}-{file_id}".encode()).hexdigest()
    cols = [
        "id", "workspace_id", "name", "content_sha", "object_key",
        "mime_type", "size_bytes", "lifecycle_state",
    ]
    vals: list = [
        file_id, workspace, "test.pdf", sha, f"raw/{file_id}",
        "application/pdf", 100, lifecycle_state,
    ]
    if inferred_doc_type is not None:
        cols.append("inferred_doc_type")
        vals.append(inferred_doc_type)
    if source_authority is not None:
        cols.append("source_authority")
        vals.append(source_authority)
    if doc_status is not None:
        cols.append("doc_status")
        vals.append(doc_status)
    placeholders = ", ".join(["%s"] * len(vals))
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        await conn.execute(
            f"INSERT INTO files ({', '.join(cols)}) VALUES ({placeholders})",
            tuple(vals),
        )
    return file_id


async def _seed_entity(
    db_url: str, workspace: str, *, name: str = "ACME", entity_type: str = "ORG",
) -> str:
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


async def test_files_source_authority_columns_present(db_url_superuser):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'files' AND column_name IN "
            "('source_authority', 'source_authority_reason', 'doc_status')"
        )
        rows = {r[0]: (r[1], r[2]) for r in await cur.fetchall()}
        assert "source_authority" in rows
        # NUMERIC(3,2) → numeric
        assert rows["source_authority"][0] == "numeric"
        assert rows["source_authority"][1] == "NO"  # NOT NULL
        assert "source_authority_reason" in rows
        assert rows["source_authority_reason"][0] == "text"
        assert rows["source_authority_reason"][1] == "YES"
        assert "doc_status" in rows
        assert rows["doc_status"][0] == "text"
        assert rows["doc_status"][1] == "NO"


async def test_files_doc_status_check_constraint(db_url_superuser, test_workspace):
    """Only the 5 documented statuses are accepted."""
    # Valid: 'live' default is applied at insert time; we just verify
    # setting an invalid value raises.
    file_id = await _seed_file(db_url_superuser, test_workspace)
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        with pytest.raises(Exception) as ei:
            await conn.execute(
                "UPDATE files SET doc_status = 'bogus' WHERE id = %s", (file_id,),
            )
        assert "doc_status" in str(ei.value).lower() or "check" in str(ei.value).lower()


async def test_files_source_authority_check_range(db_url_superuser, test_workspace):
    """source_authority must be in [0, 1]."""
    file_id = await _seed_file(db_url_superuser, test_workspace)
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        with pytest.raises(Exception):
            await conn.execute(
                "UPDATE files SET source_authority = 1.5 WHERE id = %s", (file_id,),
            )


async def test_fact_conflicts_table_rls_forced(db_url_superuser):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname = 'fact_conflicts'"
        )
        row = await cur.fetchone()
        assert row is not None
        assert row[0] is True and row[1] is True


async def test_fact_conflicts_grants(db_url_superuser):
    """kb_app has full CRUD (admin resolution + dashboard list + cleanup)."""
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT privilege_type FROM information_schema.role_table_grants "
            "WHERE grantee = 'kb_app' AND table_name = 'fact_conflicts'"
        )
        privs = {r[0] for r in await cur.fetchall()}
        assert {"SELECT", "INSERT", "UPDATE", "DELETE"}.issubset(privs)


async def test_fact_conflicts_resolution_check(db_url_superuser, test_workspace):
    """Only the 6 documented resolution values are accepted."""
    ent_id = await _seed_entity(db_url_superuser, test_workspace)
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        with pytest.raises(Exception):
            await conn.execute(
                "INSERT INTO fact_conflicts (workspace_id, entity_id, predicate, "
                "evidence, resolution) VALUES (%s, %s, %s, %s::jsonb, 'bogus')",
                (test_workspace, ent_id, "p", "[]"),
            )


async def test_fact_conflicts_predicate_not_empty(db_url_superuser, test_workspace):
    ent_id = await _seed_entity(db_url_superuser, test_workspace)
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        with pytest.raises(Exception):
            await conn.execute(
                "INSERT INTO fact_conflicts (workspace_id, entity_id, predicate, "
                "evidence) VALUES (%s, %s, '', %s::jsonb)",
                (test_workspace, ent_id, "[]"),
            )


# ===========================================================================
# Repo: file authority + doc_status
# ===========================================================================


async def test_set_source_authority_override_and_read(
    db_url_superuser, test_workspace,
):
    file_id = await _seed_file(db_url_superuser, test_workspace)
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        ok = await set_source_authority_override(
            conn, file_id=file_id, authority=0.85, reason="admin override",
        )
        assert ok is True

        info = await read_file_authority(conn, file_id=file_id)
        assert info is not None
        authority, reason, status = info
        assert authority == 0.85
        assert reason == "admin override"
        assert status == "live"  # default


async def test_set_source_authority_override_rejects_out_of_range():
    """Pure-function level — repo helper guards range before SQL."""
    with pytest.raises(ValueError):
        await set_source_authority_override(  # type: ignore[arg-type]
            conn=None, file_id="x", authority=1.5, reason="bad",
        )


async def test_set_doc_status_validates_enum_and_updates(
    db_url_superuser, test_workspace,
):
    file_id = await _seed_file(db_url_superuser, test_workspace)
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        with pytest.raises(ValueError):
            await set_doc_status(conn, file_id=file_id, new_status="bogus")

        ok = await set_doc_status(conn, file_id=file_id, new_status="superseded")
        assert ok is True

        info = await read_file_authority(conn, file_id=file_id)
        assert info is not None and info[2] == "superseded"


async def test_read_file_authority_returns_none_for_missing(db_url_superuser):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        info = await read_file_authority(conn, file_id=str(uuid.uuid4()))
        assert info is None


# ===========================================================================
# Repo: fact_conflicts CRUD
# ===========================================================================


async def test_insert_and_read_conflict(db_url_superuser, test_workspace):
    ent_id = await _seed_entity(db_url_superuser, test_workspace, name="ACME Corp")
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        cid = await insert_conflict(
            conn,
            workspace_id=test_workspace,
            entity_id=ent_id,
            predicate="indemnification_cap",
            evidence=[
                {"doc_id": "d1", "value": "$25M", "authority": 0.9},
                {"doc_id": "d2", "value": "$50M", "authority": 0.4},
            ],
        )
        assert cid

        rows = await read_conflicts_for_workspace(
            conn, workspace_id=test_workspace,
        )
        assert any(r.id == cid for r in rows)
        rec = await read_conflict_by_id(conn, conflict_id=cid)
        assert rec is not None
        assert rec.predicate == "indemnification_cap"
        assert rec.resolution == "unresolved"
        assert len(rec.evidence) == 2


async def test_insert_conflict_rejects_bad_resolution(
    db_url_superuser, test_workspace,
):
    ent_id = await _seed_entity(db_url_superuser, test_workspace)
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        with pytest.raises(ValueError):
            await insert_conflict(
                conn, workspace_id=test_workspace, entity_id=ent_id,
                predicate="p", evidence=[], resolution="bogus",
            )


async def test_read_conflicts_filter_by_resolution(
    db_url_superuser, test_workspace,
):
    ent_id = await _seed_entity(db_url_superuser, test_workspace)
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        # one unresolved + one resolved
        await insert_conflict(
            conn, workspace_id=test_workspace, entity_id=ent_id,
            predicate="x", evidence=[],
        )
        cid_resolved = await insert_conflict(
            conn, workspace_id=test_workspace, entity_id=ent_id,
            predicate="y", evidence=[],
            resolution="authority", resolved_value="$25M",
        )

        unresolved = await read_conflicts_for_workspace(
            conn, workspace_id=test_workspace, resolution="unresolved",
        )
        assert {r.predicate for r in unresolved} == {"x"}

        authority = await read_conflicts_for_workspace(
            conn, workspace_id=test_workspace, resolution="authority",
        )
        assert {r.id for r in authority} == {cid_resolved}


async def test_read_conflicts_for_entity(db_url_superuser, test_workspace):
    ent_a = await _seed_entity(db_url_superuser, test_workspace, name="A")
    ent_b = await _seed_entity(db_url_superuser, test_workspace, name="B")
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        await insert_conflict(
            conn, workspace_id=test_workspace, entity_id=ent_a,
            predicate="a-pred", evidence=[],
        )
        await insert_conflict(
            conn, workspace_id=test_workspace, entity_id=ent_b,
            predicate="b-pred", evidence=[],
        )
        rows_a = await read_conflicts_for_entity(
            conn, workspace_id=test_workspace, entity_id=ent_a,
        )
        assert {r.predicate for r in rows_a} == {"a-pred"}


async def test_mark_conflict_resolved_sets_fields(db_url_superuser, test_workspace):
    ent_id = await _seed_entity(db_url_superuser, test_workspace)
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        cid = await insert_conflict(
            conn, workspace_id=test_workspace, entity_id=ent_id,
            predicate="p", evidence=[],
        )
        ok = await mark_conflict_resolved(
            conn, conflict_id=cid, resolution="user",
            resolved_value="$25M", resolved_doc_id=None,
            resolved_by="admin@example.com", notes="manual",
        )
        assert ok is True
        rec = await read_conflict_by_id(conn, conflict_id=cid)
        assert rec is not None
        assert rec.resolution == "user"
        assert rec.resolved_value == "$25M"
        assert rec.resolved_by == "admin@example.com"
        assert rec.resolved_at is not None
        assert rec.notes == "manual"


async def test_mark_conflict_resolved_returns_false_for_missing(db_url_superuser):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        ok = await mark_conflict_resolved(
            conn, conflict_id=str(uuid.uuid4()), resolution="user",
            resolved_value=None, resolved_doc_id=None,
        )
        assert ok is False


# ===========================================================================
# Repo: apply_source_authority_from_config (fallback path)
# ===========================================================================


async def test_apply_source_authority_unknown_doc_type_falls_back(
    db_url_superuser, test_workspace,
):
    """No doc-type config + no defaults config → final 0.5 fallback with
    "authority not assessed" reason."""
    file_id = await _seed_file(db_url_superuser, test_workspace)
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        authority, reason = await apply_source_authority_from_config(
            conn,
            file_id=file_id,
            workspace_id=test_workspace,
            inferred_doc_type=None,
        )
        assert authority == 0.5
        assert reason is not None and "not assessed" in reason

        info = await read_file_authority(conn, file_id=file_id)
        assert info is not None
        assert info[0] == 0.5
        assert info[1] == reason


# ===========================================================================
# HTTP endpoints
# ===========================================================================


async def test_get_conflicts_empty(client, test_workspace):
    resp = await client.get("/conflicts", headers=headers(test_workspace))
    assert resp.status_code == 200
    assert resp.json()["items"] == []


async def test_get_conflicts_bad_resolution_filter(client, test_workspace):
    resp = await client.get(
        "/conflicts?resolution=bogus", headers=headers(test_workspace),
    )
    assert resp.status_code == 400


async def test_get_conflicts_limit_validation(client, test_workspace):
    # FastAPI Query(le=500) → 422
    resp = await client.get(
        "/conflicts?limit=999", headers=headers(test_workspace),
    )
    assert resp.status_code == 422


async def test_get_conflicts_returns_seeded_rows(
    client, test_workspace, db_url_superuser,
):
    ent_id = await _seed_entity(db_url_superuser, test_workspace, name="ACME")
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        cid = await insert_conflict(
            conn, workspace_id=test_workspace, entity_id=ent_id,
            predicate="indemnification_cap",
            evidence=[{"doc_id": "d1", "value": "$25M"}],
        )

    resp = await client.get("/conflicts", headers=headers(test_workspace))
    body = resp.json()
    assert any(item["id"] == cid for item in body["items"])
    item = next(item for item in body["items"] if item["id"] == cid)
    assert item["predicate"] == "indemnification_cap"
    assert item["resolution"] == "unresolved"
    assert item["evidence"][0]["doc_id"] == "d1"


async def test_get_entity_conflicts(client, test_workspace, db_url_superuser):
    ent_id = await _seed_entity(db_url_superuser, test_workspace, name="Vertex")
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        await insert_conflict(
            conn, workspace_id=test_workspace, entity_id=ent_id,
            predicate="cap", evidence=[],
        )

    resp = await client.get(
        f"/entities/{ent_id}/conflicts", headers=headers(test_workspace),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["entity_id"] == ent_id


async def test_post_resolve_conflict_updates_and_returns_fresh(
    client, test_workspace, db_url_superuser,
):
    ent_id = await _seed_entity(db_url_superuser, test_workspace)
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        cid = await insert_conflict(
            conn, workspace_id=test_workspace, entity_id=ent_id,
            predicate="p", evidence=[],
        )

    resp = await client.post(
        f"/conflicts/{cid}/resolve",
        headers=headers(test_workspace),
        json={
            "resolution": "user",
            "resolved_value": "$25M",
            "resolved_by": "admin",
            "notes": "manual",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["resolution"] == "user"
    assert body["resolved_value"] == "$25M"
    assert body["resolved_by"] == "admin"
    assert body["resolved_at"] is not None


async def test_post_resolve_conflict_404_on_missing(client, test_workspace):
    resp = await client.post(
        f"/conflicts/{uuid.uuid4()}/resolve",
        headers=headers(test_workspace),
        json={"resolution": "user", "resolved_value": "$25M"},
    )
    assert resp.status_code == 404


async def test_post_resolve_conflict_400_on_bad_resolution(
    client, test_workspace, db_url_superuser,
):
    ent_id = await _seed_entity(db_url_superuser, test_workspace)
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        cid = await insert_conflict(
            conn, workspace_id=test_workspace, entity_id=ent_id,
            predicate="p", evidence=[],
        )
    resp = await client.post(
        f"/conflicts/{cid}/resolve",
        headers=headers(test_workspace),
        json={"resolution": "bogus"},
    )
    assert resp.status_code == 400


async def test_get_file_authority_returns_defaults_for_seeded(
    client, test_workspace, db_url_superuser,
):
    file_id = await _seed_file(db_url_superuser, test_workspace)
    resp = await client.get(
        f"/files/{file_id}/authority", headers=headers(test_workspace),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["source_authority"] == 0.5
    assert body["doc_status"] == "live"


async def test_get_file_authority_404_on_missing(client, test_workspace):
    resp = await client.get(
        f"/files/{uuid.uuid4()}/authority", headers=headers(test_workspace),
    )
    assert resp.status_code == 404


async def test_post_set_source_authority_requires_reason(
    client, test_workspace, db_url_superuser,
):
    file_id = await _seed_file(db_url_superuser, test_workspace)
    resp = await client.post(
        f"/files/{file_id}/source-authority",
        headers=headers(test_workspace),
        json={"authority": 0.8, "reason": "  "},
    )
    assert resp.status_code == 400


async def test_post_set_source_authority_persists_and_returns(
    client, test_workspace, db_url_superuser,
):
    file_id = await _seed_file(db_url_superuser, test_workspace)
    resp = await client.post(
        f"/files/{file_id}/source-authority",
        headers=headers(test_workspace),
        json={"authority": 0.92, "reason": "admin override: original signed PDF"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["source_authority"] == 0.92
    assert "original signed PDF" in body["source_authority_reason"]


async def test_post_set_source_authority_pydantic_range(
    client, test_workspace, db_url_superuser,
):
    file_id = await _seed_file(db_url_superuser, test_workspace)
    resp = await client.post(
        f"/files/{file_id}/source-authority",
        headers=headers(test_workspace),
        json={"authority": 1.5, "reason": "bad"},
    )
    # Pydantic Field(ge=0, le=1) → 422
    assert resp.status_code == 422


async def test_post_set_doc_status_validates_enum(
    client, test_workspace, db_url_superuser,
):
    file_id = await _seed_file(db_url_superuser, test_workspace)
    resp = await client.post(
        f"/files/{file_id}/doc-status",
        headers=headers(test_workspace),
        json={"doc_status": "bogus"},
    )
    assert resp.status_code == 400


async def test_post_set_doc_status_persists(
    client, test_workspace, db_url_superuser,
):
    file_id = await _seed_file(db_url_superuser, test_workspace)
    resp = await client.post(
        f"/files/{file_id}/doc-status",
        headers=headers(test_workspace),
        json={"doc_status": "superseded"},
    )
    assert resp.status_code == 200
    assert resp.json()["doc_status"] == "superseded"


# ===========================================================================
# Workspace isolation (RLS)
# ===========================================================================


async def test_workspace_isolation_on_conflicts(client, db_url_superuser):
    ws_a = str(uuid.uuid4())
    ws_b = str(uuid.uuid4())
    ent = await _seed_entity(db_url_superuser, ws_a, name="A-Ent")
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (ws_a,),
        )
        await insert_conflict(
            conn, workspace_id=ws_a, entity_id=ent,
            predicate="cap", evidence=[],
        )

    # WS B does NOT see WS A's conflicts.
    resp = await client.get("/conflicts", headers=headers(ws_b))
    assert resp.status_code == 200
    assert resp.json()["items"] == []


async def test_workspace_isolation_on_entity_conflicts(client, db_url_superuser):
    ws_a = str(uuid.uuid4())
    ws_b = str(uuid.uuid4())
    ent = await _seed_entity(db_url_superuser, ws_a, name="A-Ent")
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (ws_a,),
        )
        await insert_conflict(
            conn, workspace_id=ws_a, entity_id=ent,
            predicate="cap", evidence=[],
        )
    resp = await client.get(
        f"/entities/{ent}/conflicts", headers=headers(ws_b),
    )
    assert resp.status_code == 200
    assert resp.json()["items"] == []


# ===========================================================================
# Worker integration — Strategy B (additive, non-gating)
# ===========================================================================


async def test_extract_fields_sets_source_authority_after_doc_type(
    client, db_url_superuser, test_workspace,
):
    """When extract_fields_file_impl runs, the worker calls
    apply_source_authority_from_config. With no config overrides for the
    classified doc_type, the file lands on the 0.5 unknown_default with the
    "not assessed" reason — proving the call wired through without blocking
    the pipeline."""
    from kb.workers.tasks import extract_fields_file_impl

    file_id = await _seed_file(
        db_url_superuser, test_workspace,
        lifecycle_state="fields_extracting",
    )
    # Seed a single raw_pages row so the classifier has input.
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        page_sha = hashlib.sha256(b"Some doc text").hexdigest()
        await conn.execute(
            "INSERT INTO raw_pages (workspace_id, file_id, page_number, text, content_sha) "
            "VALUES (%s, %s, 1, %s, %s)",
            (test_workspace, file_id, "Some doc text", page_sha),
        )

    # Force the identity classifier path so we don't need an LLM.
    with _env(
        KB_DATABASE_URL=db_url_superuser,
        KB_FIELD_EXTRACTOR="identity",
    ):
        await extract_fields_file_impl(file_id)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        info = await read_file_authority(conn, file_id=file_id)
        assert info is not None
        authority, reason, status = info
        # The hook fired — reason is now set (either an "unknown" fallback
        # or a heuristic default), never None.
        assert reason is not None
        assert 0.0 <= authority <= 1.0


# ===========================================================================
# Regression — prior endpoints unaffected
# ===========================================================================


async def test_b1_endpoint_still_works(client, test_workspace):
    """GET /triples (B1) is still mounted after B2's include_router add."""
    resp = await client.get("/triples", headers=headers(test_workspace))
    assert resp.status_code == 200
    assert resp.json()["items"] == []


async def test_module_constants_match_migration():
    """Sanity: domain constants line up with the CHECK enums in the migration."""
    assert set(DOC_STATUSES) == {
        "live", "superseded", "draft", "archived", "retracted",
    }
    assert set(RESOLUTIONS) == {
        "chain", "status", "authority", "recency", "unresolved", "user",
    }
