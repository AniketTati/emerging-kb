"""FIX 10 — re-extraction is EXPLICIT ("apply changes"), not auto-fired.

bump_schema_version no longer enqueues a re-extract on every field edit (that
caused an edit storm + a defer inside the request txn + a dropped-while-running
edit). Instead the user triggers ONE re-extract via POST /schemas/{id}/re-extract.
This suite verifies the doc_type-scope derivation, that bump stays clean, and
that the explicit endpoint enqueues the right scoped re-extract.
"""

from __future__ import annotations

import uuid

import pytest

from kb.db.pool import open_connection
from kb.domain.schemas import bump_schema_version, reextract_doc_type_for_schema
from kb.extraction.promotion import ensure_auto_schema_entity


pytestmark = pytest.mark.asyncio


class _Deferrer:
    def __init__(self, rec):
        self.rec = rec

    async def defer_async(self, **kw):
        self.rec.update(kw)


class _FakeProcApp:
    """Captures the enqueue so the explicit-apply endpoint is testable without
    a live procrastinate connection (defers don't land in the local test DB)."""
    def __init__(self):
        self.rec: dict = {}

    def configure_task(self, name, **kw):
        self.rec["task"] = name
        self.rec.update(kw)
        return _Deferrer(self.rec)


async def test_explicit_reextract_endpoint_scopes_to_doctype(
    client, db_url_superuser, monkeypatch,
):
    """POST /schemas/{id}/re-extract on an auto:<doc_type> schema enqueues a
    reextract_workspace_files job scoped to that doc_type, and returns 202."""
    workspace = str(uuid.uuid4())
    async with open_connection(db_url_superuser) as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)", (workspace,),
            )
            schema_id, _ = await ensure_auto_schema_entity(
                conn, workspace_id=workspace, doc_type="loan_agreement",
            )

    fake = _FakeProcApp()
    import kb.workers.tasks as tasks_mod
    monkeypatch.setattr(tasks_mod, "procrastinate_app", fake)

    resp = await client.post(
        f"/schemas/{schema_id}/re-extract",
        headers={"X-Test-Workspace": workspace},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["scope"] == "loan_agreement"
    assert body["status"] == "queued"
    # The enqueue was scoped to the doc_type and the workspace.
    assert fake.rec["task"] == "reextract_workspace_files"
    assert fake.rec["doc_type"] == "loan_agreement"
    assert fake.rec["workspace_id"] == workspace


def test_reextract_doc_type_for_schema_derivation():
    # auto:<doc_type> → that doc_type (re-extract scoped to it)
    assert reextract_doc_type_for_schema("auto:loan_agreement") == "loan_agreement"
    assert reextract_doc_type_for_schema("auto:invoice") == "invoice"
    # user-declared (arbitrary name) → None → whole-workspace re-extract
    assert reextract_doc_type_for_schema("Finance Domain") is None
    assert reextract_doc_type_for_schema("auto:") is None
    assert reextract_doc_type_for_schema(None) is None


async def test_bump_schema_version_runs_clean_with_reextract_trigger(db_url_superuser):
    """The enqueue path is exercised inside a real bump and must never fail
    the schema mutation (best-effort), even when procrastinate isn't open."""
    workspace = str(uuid.uuid4())
    async with open_connection(db_url_superuser) as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)", (workspace,),
            )
            schema_id, _doc_root = await ensure_auto_schema_entity(
                conn, workspace_id=workspace, doc_type="loan_agreement",
            )
            # Must return the new version and not raise from the trigger.
            v = await bump_schema_version(conn, workspace, schema_id, kind="put")
            assert isinstance(v, int) and v >= 1
            # A second bump still succeeds (coalescing is best-effort).
            v2 = await bump_schema_version(conn, workspace, schema_id, kind="put")
            assert v2 == v + 1
