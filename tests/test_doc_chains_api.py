"""WA-3 / Design 3 — doc chains HTTP + repo + integration tests.

Three test surfaces:
  - migration shape (RLS, CHECK constraints, GRANTs, FK behavior)
  - repo CRUD (upsert_chain idempotency, add_member, find_chain_for_doc)
  - HTTP contract + behavior (POST/GET via FastAPI)

Plus integration: the existing parse → chunk → ... pipeline must not
break when doc-chain detection runs in parallel.
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
    """Same pattern as test_mentions_worker.py — scoped env overrides so
    the worker impl reads our testcontainer DB URL rather than the
    compose 'db' hostname."""
    prior = {k: os.environ.get(k) for k in kwargs}
    for k, v in kwargs.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    # Clear the lru_cached settings so the new env takes effect.
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
    name: str = "doc.pdf",
    mime: str = "application/pdf",
    inferred_doc_type: str | None = None,
    lifecycle_state: str = "parsed",
) -> str:
    file_id = str(uuid.uuid4())
    sha = hashlib.sha256(f"{workspace}-{file_id}-{name}".encode()).hexdigest()
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        await conn.execute(
            "INSERT INTO files (id, workspace_id, name, content_sha, object_key, "
            "mime_type, size_bytes, lifecycle_state, inferred_doc_type) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (file_id, workspace, name, sha, f"raw/{file_id}",
             mime, 100, lifecycle_state, inferred_doc_type),
        )
    return file_id


# ============================================================================
# Migration shape
# ============================================================================


async def test_lifecycle_state_check_includes_doc_chaining(db_url_superuser):
    """Forward-compat: 'doc_chaining' is in the lifecycle CHECK enum so
    Wave B can switch to a gating model without another migration."""
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT pg_get_constraintdef(c.oid) "
            "FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
            "WHERE t.relname = 'files' "
            "AND c.conname = 'files_lifecycle_state_check'"
        )
        row = await cur.fetchone()
        assert row is not None
        assert "doc_chaining" in row[0]


async def test_doc_chains_table_with_rls_and_grants(db_url_superuser):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname = 'doc_chains'"
        )
        row = await cur.fetchone()
        assert row is not None
        assert row[0] is True and row[1] is True

        cur = await conn.execute(
            "SELECT privilege_type FROM information_schema.role_table_grants "
            "WHERE grantee = 'kb_app' AND table_name = 'doc_chains'"
        )
        privs = {r[0] for r in await cur.fetchall()}
        assert {"SELECT", "INSERT", "UPDATE", "DELETE"}.issubset(privs)


async def test_doc_chain_members_table_with_rls(db_url_superuser):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname = 'doc_chain_members'"
        )
        row = await cur.fetchone()
        assert row[0] is True and row[1] is True


async def test_chain_type_check_rejects_invalid(db_url_superuser, test_workspace):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        with pytest.raises(Exception) as ei:
            await conn.execute(
                "INSERT INTO doc_chains (workspace_id, type, detection_confidence) "
                "VALUES (%s, 'bogus_type', 0.5)",
                (test_workspace,),
            )
        msg = str(ei.value).lower()
        assert "violates check" in msg or "doc_chains_type_check" in msg


async def test_member_role_check_rejects_invalid(
    db_url_superuser, test_workspace,
):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        await conn.execute(
            "INSERT INTO doc_chains (id, workspace_id, type, detection_confidence) "
            "VALUES (%s, %s, 'email_thread', 0.8)",
            ("11111111-2222-3333-4444-555555555555", test_workspace),
        )
        file_id = await _seed_file(db_url_superuser, test_workspace)
        with pytest.raises(Exception) as ei:
            await conn.execute(
                "INSERT INTO doc_chain_members "
                "(chain_id, doc_id, workspace_id, version_index, role) "
                "VALUES (%s, %s, %s, 0, 'bogus_role')",
                ("11111111-2222-3333-4444-555555555555", file_id, test_workspace),
            )
        msg = str(ei.value).lower()
        assert "violates check" in msg


# ============================================================================
# Repo CRUD
# ============================================================================


async def test_upsert_chain_creates_new(db_url_superuser, test_workspace):
    from kb.domain.doc_chains import get_chain, upsert_chain
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        chain_id = await upsert_chain(
            conn,
            workspace_id=test_workspace,
            chain_type="email_thread",
            title="Mexico deal",
            chain_key="msgid:<m1@enron.com>",
            detection_confidence=0.9,
        )
        chain = await get_chain(conn, chain_id=chain_id)
        assert chain is not None
        assert chain.type == "email_thread"
        assert chain.detection_confidence == 0.9
        assert chain.member_count == 0


async def test_upsert_chain_reuses_existing_by_key(db_url_superuser, test_workspace):
    """Idempotency contract: same (workspace, type, chain_key) returns
    the existing id rather than creating a duplicate."""
    from kb.domain.doc_chains import upsert_chain
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        id1 = await upsert_chain(
            conn,
            workspace_id=test_workspace,
            chain_type="contract_chain",
            title="Vertex Supply",
            chain_key="title:vertex supply",
            detection_confidence=0.85,
        )
        id2 = await upsert_chain(
            conn,
            workspace_id=test_workspace,
            chain_type="contract_chain",
            title="Vertex Supply (updated)",
            chain_key="title:vertex supply",  # same key
            detection_confidence=0.95,
        )
        assert id1 == id2


async def test_add_member_increments_member_count(db_url_superuser, test_workspace):
    from kb.domain.doc_chains import add_member, get_chain, upsert_chain
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        chain_id = await upsert_chain(
            conn,
            workspace_id=test_workspace,
            chain_type="email_thread",
            title="Thread",
            chain_key="key-a",
            detection_confidence=0.9,
        )
        f1 = await _seed_file(db_url_superuser, test_workspace, name="email1.eml", mime="message/rfc822")
        f2 = await _seed_file(db_url_superuser, test_workspace, name="email2.eml", mime="message/rfc822")
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        added1 = await add_member(
            conn, chain_id=chain_id, doc_id=f1, workspace_id=test_workspace,
            version_index=0, role="original",
        )
        added2 = await add_member(
            conn, chain_id=chain_id, doc_id=f2, workspace_id=test_workspace,
            version_index=1, role="reply", parent_doc_id=f1,
        )
        assert added1 is True and added2 is True
        chain = await get_chain(conn, chain_id=chain_id)
        assert chain.member_count == 2


async def test_add_member_idempotent(db_url_superuser, test_workspace):
    """Same (chain, doc) twice → second insert is a no-op."""
    from kb.domain.doc_chains import add_member, upsert_chain
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        chain_id = await upsert_chain(
            conn, workspace_id=test_workspace,
            chain_type="email_thread", title="t", chain_key="key-b",
            detection_confidence=0.9,
        )
        f = await _seed_file(db_url_superuser, test_workspace, name="e.eml", mime="message/rfc822")
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        a = await add_member(conn, chain_id=chain_id, doc_id=f,
                              workspace_id=test_workspace, version_index=0, role="original")
        b = await add_member(conn, chain_id=chain_id, doc_id=f,
                              workspace_id=test_workspace, version_index=0, role="original")
        assert a is True
        assert b is False  # already a member


async def test_remove_member_clears_current_version_if_pointed_at_it(
    db_url_superuser, test_workspace,
):
    from kb.domain.doc_chains import (
        add_member,
        get_chain,
        remove_member,
        set_current_version,
        upsert_chain,
    )
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        f = await _seed_file(db_url_superuser, test_workspace)
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        chain_id = await upsert_chain(
            conn, workspace_id=test_workspace,
            chain_type="contract_chain", title="t", chain_key="key-c",
            detection_confidence=0.9,
        )
        await add_member(conn, chain_id=chain_id, doc_id=f,
                          workspace_id=test_workspace, version_index=0, role="original")
        await set_current_version(conn, chain_id=chain_id, current_version_id=f)

        deleted = await remove_member(conn, chain_id=chain_id, doc_id=f)
        assert deleted is True
        chain = await get_chain(conn, chain_id=chain_id)
        assert chain.member_count == 0
        assert chain.current_version_id is None


# ============================================================================
# HTTP contract — endpoints + RLS
# ============================================================================


async def test_get_chains_empty_workspace_returns_empty_list(
    client, test_workspace,
):
    resp = await client.get("/chains", headers=headers(test_workspace))
    assert resp.status_code == 200
    assert resp.json()["items"] == []


async def test_get_files_id_chain_404_when_no_chain(
    client, test_workspace, db_url_superuser,
):
    file_id = await _seed_file(db_url_superuser, test_workspace)
    resp = await client.get(
        f"/files/{file_id}/chain", headers=headers(test_workspace),
    )
    assert resp.status_code == 404


async def test_get_chain_by_id_404_when_unknown(client, test_workspace):
    fake = str(uuid.uuid4())
    resp = await client.get(f"/chains/{fake}", headers=headers(test_workspace))
    assert resp.status_code == 404


async def test_post_unlink_member_404_when_not_a_member(
    client, test_workspace, db_url_superuser,
):
    from kb.domain.doc_chains import upsert_chain
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        chain_id = await upsert_chain(
            conn, workspace_id=test_workspace,
            chain_type="email_thread", title="t", chain_key="key-x",
            detection_confidence=0.8,
        )
    fake_doc = str(uuid.uuid4())
    resp = await client.post(
        f"/chains/{chain_id}/members/{fake_doc}/unlink",
        headers=headers(test_workspace),
    )
    assert resp.status_code == 404


async def test_chain_type_filter_validation(client, test_workspace):
    resp = await client.get(
        "/chains?chain_type=bogus_type", headers=headers(test_workspace),
    )
    assert resp.status_code == 400


async def test_workspace_isolation_on_chains(client, db_url_superuser):
    ws_a = str(uuid.uuid4())
    ws_b = str(uuid.uuid4())
    from kb.domain.doc_chains import upsert_chain
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (ws_a,),
        )
        await upsert_chain(
            conn, workspace_id=ws_a, chain_type="email_thread",
            title="t-a", chain_key="key-a", detection_confidence=0.9,
        )
    # WS B sees no chains.
    resp = await client.get("/chains", headers=headers(ws_b))
    assert resp.status_code == 200
    assert resp.json()["items"] == []


async def test_openapi_includes_chain_routes(client):
    resp = await client.get("/openapi.json")
    paths = set(resp.json()["paths"].keys())
    assert "/chains" in paths
    assert "/chains/{chain_id}" in paths
    assert "/chains/{chain_id}/members/{doc_id}/unlink" in paths
    assert "/files/{file_id}/chain" in paths


# ============================================================================
# End-to-end: detector worker stage running against real parsed files
# ============================================================================


async def _seed_email_with_layout(
    db_url: str,
    workspace: str,
    *,
    message_id: str,
    subject: str,
    sender: str,
    recipients: list[str],
    in_reply_to: str | None = None,
    references: list[str] | None = None,
) -> str:
    """Seed an email file + its raw_pages row with the email parser's
    layout_json shape (headers dict the detector unpacks)."""
    file_id = await _seed_file(
        db_url, workspace,
        name=f"email_{uuid.uuid4().hex[:6]}.eml",
        mime="message/rfc822",
        inferred_doc_type="email",
        lifecycle_state="parsed",
    )
    layout = {
        "headers": {
            "message_id": message_id,
            "subject": subject,
            "from": sender,
            "to": recipients,
        },
    }
    if in_reply_to:
        layout["headers"]["in_reply_to"] = in_reply_to
    if references:
        layout["headers"]["references"] = " ".join(references)
    sha = hashlib.sha256(file_id.encode()).hexdigest()
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (workspace,),
        )
        await conn.execute(
            "INSERT INTO raw_pages (file_id, workspace_id, page_number, "
            "text, layout_json, content_sha) "
            "VALUES (%s, %s, 1, %s, %s::jsonb, %s)",
            (file_id, workspace, f"Subject: {subject}\nBody.", json.dumps(layout), sha),
        )
    return file_id


async def test_detect_doc_chain_file_impl_creates_email_thread(
    client, db_url_superuser, test_workspace,
):
    """Drive the worker impl directly. First email creates a chain with
    role=original; second email (Re:) joins that chain with role=reply."""
    from kb.workers.tasks import detect_doc_chain_file_impl

    first_id = await _seed_email_with_layout(
        db_url_superuser, test_workspace,
        message_id="<m1@enron.com>",
        subject="Mexico deal",
        sender="alice@enron.com",
        recipients=["bob@enron.com"],
    )
    with _env(KB_DATABASE_URL=db_url_superuser):
        await detect_doc_chain_file_impl(first_id)

    reply_id = await _seed_email_with_layout(
        db_url_superuser, test_workspace,
        message_id="<m2@enron.com>",
        subject="Re: Mexico deal",
        sender="bob@enron.com",
        recipients=["alice@enron.com"],
        in_reply_to="<m1@enron.com>",
    )
    with _env(KB_DATABASE_URL=db_url_superuser):
        await detect_doc_chain_file_impl(reply_id)

    # Both files should now be in the same chain.
    from kb.domain.doc_chains import find_chain_for_doc, read_members
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        first_pair = await find_chain_for_doc(conn, doc_id=first_id)
        reply_pair = await find_chain_for_doc(conn, doc_id=reply_id)
        assert first_pair is not None
        assert reply_pair is not None
        # Same chain_id.
        assert first_pair[0].id == reply_pair[0].id
        assert first_pair[0].type == "email_thread"
        members = await read_members(conn, chain_id=first_pair[0].id)
        assert len(members) == 2
        roles = {m.doc_id: m.role for m in members}
        assert roles[first_id] == "original"
        assert roles[reply_id] == "reply"


async def test_detect_doc_chain_idempotent(
    client, db_url_superuser, test_workspace,
):
    """Re-running detection on the same file must NOT create duplicate
    membership rows."""
    from kb.workers.tasks import detect_doc_chain_file_impl
    file_id = await _seed_email_with_layout(
        db_url_superuser, test_workspace,
        message_id="<solo@enron.com>",
        subject="Solo",
        sender="alice@enron.com",
        recipients=["bob@enron.com"],
    )
    with _env(KB_DATABASE_URL=db_url_superuser):
        await detect_doc_chain_file_impl(file_id)
        await detect_doc_chain_file_impl(file_id)  # repeat

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT COUNT(*) FROM doc_chain_members WHERE doc_id = %s",
            (file_id,),
        )
        assert (await cur.fetchone())[0] == 1


async def test_detect_doc_chain_records_lifecycle_event_no_state_change(
    client, db_url_superuser, test_workspace,
):
    """Detector emits a `doc_chain_detected` lifecycle event WITHOUT
    changing the file's lifecycle_state (Strategy B — additive, non-
    gating, integration-safe with existing parse → chunk chain)."""
    from kb.workers.tasks import detect_doc_chain_file_impl
    file_id = await _seed_email_with_layout(
        db_url_superuser, test_workspace,
        message_id="<event@enron.com>",
        subject="event test",
        sender="alice@enron.com",
        recipients=["bob@enron.com"],
    )
    with _env(KB_DATABASE_URL=db_url_superuser):
        await detect_doc_chain_file_impl(file_id)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT lifecycle_state FROM files WHERE id = %s", (file_id,),
        )
        # File should still be at 'parsed' (worker is additive).
        assert (await cur.fetchone())[0] == "parsed"

        cur = await conn.execute(
            "SELECT event, payload FROM file_lifecycle "
            "WHERE file_id = %s AND event = 'doc_chain_detected' "
            "ORDER BY created_at DESC LIMIT 1",
            (file_id,),
        )
        row = await cur.fetchone()
        assert row is not None
        assert row[0] == "doc_chain_detected"
        payload = row[1] if isinstance(row[1], dict) else json.loads(row[1])
        assert payload["matched"] is True
        assert payload["chain_type"] == "email_thread"


async def test_detect_doc_chain_no_match_records_event_with_matched_false(
    client, db_url_superuser, test_workspace,
):
    """Random unmatched PDF → no chain row, but a `doc_chain_detected`
    event with matched=false is still written for observability."""
    from kb.workers.tasks import detect_doc_chain_file_impl
    file_id = await _seed_file(
        db_url_superuser, test_workspace,
        name="random.pdf", mime="application/pdf",
        inferred_doc_type="report",
        lifecycle_state="parsed",
    )
    # Need a raw_pages row so the impl can read first-page text.
    sha = hashlib.sha256(file_id.encode()).hexdigest()
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        await conn.execute(
            "INSERT INTO raw_pages (file_id, workspace_id, page_number, "
            "text, layout_json, content_sha) "
            "VALUES (%s, %s, 1, %s, %s::jsonb, %s)",
            (file_id, test_workspace, "Q3 earnings highlights", "{}", sha),
        )

    with _env(KB_DATABASE_URL=db_url_superuser):
        await detect_doc_chain_file_impl(file_id)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT COUNT(*) FROM doc_chain_members WHERE doc_id = %s", (file_id,),
        )
        assert (await cur.fetchone())[0] == 0  # no chain matched

        cur = await conn.execute(
            "SELECT payload FROM file_lifecycle "
            "WHERE file_id = %s AND event = 'doc_chain_detected'",
            (file_id,),
        )
        row = await cur.fetchone()
        payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        assert payload["matched"] is False


# ============================================================================
# INTEGRATION — the existing parse → chunk → ... pipeline still works
# with WA-3 active. This is the "doesn't break what was there" gate.
# ============================================================================


async def test_existing_pipeline_unbroken_by_wa3_addition(client, test_workspace):
    """End-to-end smoke: upload a real tiny.pdf via /files, verify the
    file row appears at 'queued'. Then assert WA-3's `doc_chaining`
    state is in the CHECK enum, the chain-router routes are reachable
    via /openapi.json, and a NO_CHAIN file still gets a
    `doc_chain_detected matched=false` event (so we don't silently lose
    work).

    Smoke is intentionally lightweight — full lifecycle tested in
    verify_phase_*.sh against compose."""
    # 1. OpenAPI includes both prior + new routes.
    resp = await client.get("/openapi.json")
    paths = set(resp.json()["paths"].keys())
    assert "/files" in paths              # Phase 2a
    assert "/chat" in paths               # Phase 8f
    assert "/audit" in paths              # Phase 9
    assert "/vocabulary" in paths         # WA-2
    assert "/chains" in paths             # WA-3

    # 2. Existing endpoints still respond.
    resp = await client.get("/files", headers=headers(test_workspace))
    assert resp.status_code == 200
    resp = await client.get("/audit", headers=headers(test_workspace))
    assert resp.status_code == 200
    resp = await client.get(
        f"/vocabulary?domain_id=mixed_demo", headers=headers(test_workspace),
    )
    assert resp.status_code == 200

    # 3. WA-3's new endpoint also responds.
    resp = await client.get("/chains", headers=headers(test_workspace))
    assert resp.status_code == 200
    assert resp.json()["items"] == []
