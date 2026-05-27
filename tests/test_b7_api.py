"""B7 / WA-14 — UI page backend tests.

Covers:
  - GET /dashboard/summary: aggregate counts (files by lifecycle/doc_type/
    doc_status; queries by mode/verdict; conflicts; corrections; sessions;
    audit_log; regressions)
  - GET /dashboard/needs-attention: unified list across conflicts +
    corrections + low_confidence chats + low_authority files
  - GET /settings/overrides: lists active config overrides
  - GET /schemas/inferred-fields: lists inferred_schema_fields with
    filters (doc_type, only_promotable)
  - Workspace isolation on all 4 endpoints
  - Regression: prior endpoints still work
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from contextlib import contextmanager

import psycopg
import pytest

from kb.config import get_settings
from kb.domain.audit_chain import insert_audit_event
from kb.domain.chat_memory import create_session
from kb.domain.corrections import insert_correction
from kb.layered_config import insert_override


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


async def _seed_file(
    db_url: str,
    workspace: str,
    *,
    lifecycle: str = "ready",
    doc_type: str | None = "contract",
    doc_status: str = "live",
    source_authority: float = 0.8,
    name: str | None = None,
) -> str:
    file_id = str(uuid.uuid4())
    sha = hashlib.sha256(f"{workspace}-{file_id}".encode()).hexdigest()
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        await conn.execute(
            "INSERT INTO files (id, workspace_id, name, content_sha, "
            "object_key, mime_type, size_bytes, lifecycle_state, "
            "inferred_doc_type, doc_status, source_authority) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                file_id, workspace, name or f"doc_{file_id[:8]}.pdf",
                sha, f"raw/{file_id}",
                "application/pdf", 100, lifecycle, doc_type, doc_status,
                source_authority,
            ),
        )
    return file_id


async def _seed_query_log(
    db_url: str,
    workspace: str,
    *,
    mode: str = "H",
    verdict: str | None = "pass",
    refused: bool = False,
) -> str:
    qid = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (workspace,),
        )
        await conn.execute(
            "INSERT INTO query_log (id, workspace_id, query, mode, endpoint, "
            "refused, faithfulness_verdict) "
            "VALUES (%s, %s, 'q', %s, 'chat', %s, %s)",
            (qid, workspace, mode, refused, verdict),
        )
    return qid


async def _seed_conflict(db_url: str, workspace: str) -> str:
    cid = str(uuid.uuid4())
    ent_id = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (workspace,),
        )
        # Seed entity (required by FK).
        await conn.execute(
            "INSERT INTO canonical_entities (id, workspace_id, canonical_name, "
            "entity_type, mention_count) VALUES (%s, %s, 'ent', 'ORG', 0)",
            (ent_id, workspace),
        )
        await conn.execute(
            "INSERT INTO fact_conflicts (id, workspace_id, entity_id, "
            "predicate, evidence, resolution) "
            "VALUES (%s, %s, %s, 'cap', %s::jsonb, 'unresolved')",
            (cid, workspace, ent_id, "[]"),
        )
    return cid


# ===========================================================================
# GET /dashboard/summary
# ===========================================================================


async def test_dashboard_summary_empty_workspace(client, test_workspace):
    resp = await client.get(
        "/dashboard/summary", headers=headers(test_workspace),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["workspace_id"] == test_workspace
    assert body["files_total"] == 0
    assert body["queries_total"] == 0
    assert body["conflicts_open"] == 0


async def test_dashboard_summary_counts_files(
    client, test_workspace, db_url_superuser,
):
    # 3 contract files, 2 invoice files, 1 with low authority.
    for _ in range(3):
        await _seed_file(db_url_superuser, test_workspace, doc_type="contract")
    for _ in range(2):
        await _seed_file(db_url_superuser, test_workspace, doc_type="invoice")
    await _seed_file(
        db_url_superuser, test_workspace, source_authority=0.3,
    )

    resp = await client.get(
        "/dashboard/summary", headers=headers(test_workspace),
    )
    body = resp.json()
    assert body["files_total"] == 6
    assert body["files_low_authority"] == 1

    by_doc_type = {b["label"]: b["count"] for b in body["files_by_doc_type"]}
    assert by_doc_type["contract"] == 4    # 3 plus the low-authority one
    assert by_doc_type["invoice"] == 2

    by_lifecycle = {b["label"]: b["count"] for b in body["files_by_lifecycle"]}
    assert by_lifecycle["ready"] == 6


async def test_dashboard_summary_counts_queries(
    client, test_workspace, db_url_superuser,
):
    await _seed_query_log(db_url_superuser, test_workspace, mode="H")
    await _seed_query_log(db_url_superuser, test_workspace, mode="H")
    await _seed_query_log(db_url_superuser, test_workspace, mode="T")
    await _seed_query_log(
        db_url_superuser, test_workspace, verdict="refused", refused=True,
    )
    await _seed_query_log(
        db_url_superuser, test_workspace, verdict="low_confidence",
    )

    resp = await client.get(
        "/dashboard/summary", headers=headers(test_workspace),
    )
    body = resp.json()
    assert body["queries_total"] == 5
    assert body["queries_refused"] == 1
    assert body["queries_low_confidence"] == 1

    by_mode = {b["label"]: b["count"] for b in body["queries_by_mode"]}
    assert by_mode["H"] == 4
    assert by_mode["T"] == 1


async def test_dashboard_summary_counts_conflicts_corrections(
    client, test_workspace, db_url_superuser,
):
    await _seed_conflict(db_url_superuser, test_workspace)
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        await insert_correction(
            conn, workspace_id=test_workspace, scope="answer", target={},
        )
        await insert_correction(
            conn, workspace_id=test_workspace, scope="extraction",
            target={"doc_id": str(uuid.uuid4())},
            status="fixing",
        )

    resp = await client.get(
        "/dashboard/summary", headers=headers(test_workspace),
    )
    body = resp.json()
    assert body["conflicts_open"] == 1
    assert body["corrections_open"] == 1
    assert body["corrections_fixing"] == 1


async def test_dashboard_summary_counts_audit_log(
    client, test_workspace, db_url_superuser,
):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        for i in range(4):
            await insert_audit_event(
                conn, workspace_id=test_workspace,
                actor="x", action=f"a_{i}", payload={"i": i},
            )

    resp = await client.get(
        "/dashboard/summary", headers=headers(test_workspace),
    )
    body = resp.json()
    assert body["audit_log_total_rows"] == 4


async def test_dashboard_summary_counts_active_sessions(
    client, test_workspace, db_url_superuser,
):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        await create_session(conn, workspace_id=test_workspace)
        await create_session(conn, workspace_id=test_workspace)
    resp = await client.get(
        "/dashboard/summary", headers=headers(test_workspace),
    )
    body = resp.json()
    assert body["sessions_active_24h"] == 2


async def test_dashboard_summary_workspace_isolation(client, db_url_superuser):
    ws_a = str(uuid.uuid4())
    ws_b = str(uuid.uuid4())
    await _seed_file(db_url_superuser, ws_a)
    resp = await client.get("/dashboard/summary", headers=headers(ws_b))
    body = resp.json()
    assert body["files_total"] == 0


# ===========================================================================
# GET /dashboard/needs-attention
# ===========================================================================


async def test_needs_attention_empty_workspace(client, test_workspace):
    resp = await client.get(
        "/dashboard/needs-attention", headers=headers(test_workspace),
    )
    assert resp.status_code == 200
    assert resp.json()["items"] == []


async def test_needs_attention_includes_conflicts(
    client, test_workspace, db_url_superuser,
):
    await _seed_conflict(db_url_superuser, test_workspace)
    resp = await client.get(
        "/dashboard/needs-attention", headers=headers(test_workspace),
    )
    items = resp.json()["items"]
    kinds = {i["kind"] for i in items}
    assert "conflict" in kinds


async def test_needs_attention_includes_corrections(
    client, test_workspace, db_url_superuser,
):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        await insert_correction(
            conn, workspace_id=test_workspace, scope="answer",
            target={}, reason="missed citation", severity="blocker",
        )
    resp = await client.get(
        "/dashboard/needs-attention", headers=headers(test_workspace),
    )
    items = resp.json()["items"]
    correction_items = [i for i in items if i["kind"] == "correction"]
    assert len(correction_items) == 1
    assert correction_items[0]["severity"] == "blocker"


async def test_needs_attention_includes_low_authority_files(
    client, test_workspace, db_url_superuser,
):
    await _seed_file(
        db_url_superuser, test_workspace,
        name="suspicious.pdf", source_authority=0.2,
    )
    await _seed_file(db_url_superuser, test_workspace, source_authority=0.95)
    resp = await client.get(
        "/dashboard/needs-attention", headers=headers(test_workspace),
    )
    items = resp.json()["items"]
    low_auth = [i for i in items if i["kind"] == "low_authority_file"]
    assert len(low_auth) == 1
    assert "suspicious.pdf" in low_auth[0]["title"]


async def test_needs_attention_includes_low_confidence_chats(
    client, test_workspace, db_url_superuser,
):
    await _seed_query_log(
        db_url_superuser, test_workspace, verdict="low_confidence",
    )
    resp = await client.get(
        "/dashboard/needs-attention", headers=headers(test_workspace),
    )
    items = resp.json()["items"]
    low_conf = [i for i in items if i["kind"] == "low_confidence_chat"]
    assert len(low_conf) == 1


async def test_needs_attention_unified_across_kinds(
    client, test_workspace, db_url_superuser,
):
    await _seed_conflict(db_url_superuser, test_workspace)
    await _seed_file(
        db_url_superuser, test_workspace,
        name="low.pdf", source_authority=0.1,
    )
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        await insert_correction(
            conn, workspace_id=test_workspace, scope="answer", target={},
        )
    resp = await client.get(
        "/dashboard/needs-attention", headers=headers(test_workspace),
    )
    items = resp.json()["items"]
    kinds = {i["kind"] for i in items}
    assert kinds == {"conflict", "correction", "low_authority_file"}


async def test_needs_attention_workspace_isolation(
    client, db_url_superuser,
):
    ws_a = str(uuid.uuid4())
    ws_b = str(uuid.uuid4())
    await _seed_conflict(db_url_superuser, ws_a)
    resp = await client.get(
        "/dashboard/needs-attention", headers=headers(ws_b),
    )
    assert resp.json()["items"] == []


# ===========================================================================
# GET /settings/overrides
# ===========================================================================


async def test_get_overrides_empty(client, test_workspace):
    resp = await client.get(
        "/settings/overrides", headers=headers(test_workspace),
    )
    assert resp.status_code == 200
    assert resp.json()["items"] == []


async def test_get_overrides_lists_active(
    client, test_workspace, db_url_superuser,
):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        await insert_override(
            conn,
            workspace_id=test_workspace,
            scope_kind="workspace",
            scope_id=test_workspace,
            config_key="ingestion.chunk_size",
            config_value=512,
            reason="prefer smaller chunks",
            set_by="admin",
        )
    resp = await client.get(
        "/settings/overrides", headers=headers(test_workspace),
    )
    body = resp.json()
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["scope_kind"] == "workspace"
    assert item["config_key"] == "ingestion.chunk_size"
    assert item["config_value"] == 512
    assert item["active"] is True


async def test_get_overrides_workspace_isolation(
    client, db_url_superuser,
):
    ws_a = str(uuid.uuid4())
    ws_b = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (ws_a,),
        )
        await insert_override(
            conn, workspace_id=ws_a, scope_kind="workspace",
            scope_id=ws_a, config_key="x.y", config_value=1, reason="r",
        )
    resp = await client.get(
        "/settings/overrides", headers=headers(ws_b),
    )
    assert resp.json()["items"] == []


# ===========================================================================
# GET /schemas/inferred-fields
# ===========================================================================


async def _seed_inferred_field(
    db_url: str,
    workspace: str,
    *,
    doc_type: str = "contract",
    canonical_name: str = "cap",
    prevalence: float = 0.9,
    stability: float = 0.85,
    is_promoted: bool = False,
) -> str:
    fid = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (workspace,),
        )
        await conn.execute(
            "INSERT INTO inferred_schema_fields (id, workspace_id, "
            "inferred_doc_type, canonical_name, description, value_type, "
            "n_docs_observed, prevalence, stability, value_type_confidence, "
            "is_promoted) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                fid, workspace, doc_type, canonical_name,
                f"desc for {canonical_name}", "number",
                10, prevalence, stability, 0.9, is_promoted,
            ),
        )
    return fid


async def test_get_inferred_fields_empty(client, test_workspace):
    resp = await client.get(
        "/schemas/inferred-fields", headers=headers(test_workspace),
    )
    assert resp.status_code == 200
    assert resp.json()["items"] == []


async def test_get_inferred_fields_lists_rows(
    client, test_workspace, db_url_superuser,
):
    await _seed_inferred_field(
        db_url_superuser, test_workspace, canonical_name="cap",
    )
    await _seed_inferred_field(
        db_url_superuser, test_workspace, canonical_name="term_years",
        doc_type="contract", prevalence=0.6, stability=0.5,
    )
    resp = await client.get(
        "/schemas/inferred-fields", headers=headers(test_workspace),
    )
    body = resp.json()
    assert len(body["items"]) == 2
    names = {i["canonical_name"] for i in body["items"]}
    assert names == {"cap", "term_years"}


async def test_get_inferred_fields_filter_by_doc_type(
    client, test_workspace, db_url_superuser,
):
    await _seed_inferred_field(
        db_url_superuser, test_workspace,
        doc_type="contract", canonical_name="cap",
    )
    await _seed_inferred_field(
        db_url_superuser, test_workspace,
        doc_type="invoice", canonical_name="line_total",
    )
    resp = await client.get(
        "/schemas/inferred-fields?doc_type=invoice",
        headers=headers(test_workspace),
    )
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["canonical_name"] == "line_total"


async def test_get_inferred_fields_only_promotable(
    client, test_workspace, db_url_superuser,
):
    # Above threshold + not promoted.
    await _seed_inferred_field(
        db_url_superuser, test_workspace,
        canonical_name="ready_field", prevalence=0.9, stability=0.85,
        is_promoted=False,
    )
    # Above threshold but already promoted.
    await _seed_inferred_field(
        db_url_superuser, test_workspace,
        canonical_name="already_promoted", prevalence=0.95, stability=0.9,
        is_promoted=True,
    )
    # Below threshold.
    await _seed_inferred_field(
        db_url_superuser, test_workspace,
        canonical_name="not_yet", prevalence=0.3, stability=0.4,
    )
    resp = await client.get(
        "/schemas/inferred-fields?only_promotable=true",
        headers=headers(test_workspace),
    )
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["canonical_name"] == "ready_field"


async def test_get_inferred_fields_workspace_isolation(
    client, db_url_superuser,
):
    ws_a = str(uuid.uuid4())
    ws_b = str(uuid.uuid4())
    await _seed_inferred_field(db_url_superuser, ws_a)
    resp = await client.get(
        "/schemas/inferred-fields", headers=headers(ws_b),
    )
    assert resp.json()["items"] == []


# ===========================================================================
# Regression
# ===========================================================================


async def test_b6b_corrections_still_works(client, test_workspace):
    resp = await client.get("/corrections", headers=headers(test_workspace))
    assert resp.status_code == 200


async def test_b6a_sessions_still_works(client, test_workspace):
    resp = await client.get("/sessions", headers=headers(test_workspace))
    assert resp.status_code == 200


async def test_settings_effective_config_still_works(client, test_workspace):
    resp = await client.get(
        "/settings/effective-config", headers=headers(test_workspace),
    )
    assert resp.status_code == 200


async def test_schemas_list_still_works(client, test_workspace):
    resp = await client.get("/schemas", headers=headers(test_workspace))
    assert resp.status_code == 200
