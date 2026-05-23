"""Phase 2a — raw_pages read endpoints + DB-layer immutability.

RED at G3: imports from `kb.api.files` + the worker task land at G4.

Spec: tests/specs/phase_2a.md §4.4.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from tests.test_files_crud import _TINY_PDF


pytestmark = pytest.mark.asyncio


@pytest.fixture
def test_workspace() -> str:
    return str(uuid.uuid4())


def headers(workspace: str, *, idempotency_key: str | None = None) -> dict[str, str]:
    h = {"X-Test-Workspace": workspace}
    if idempotency_key is not None:
        h["Idempotency-Key"] = idempotency_key
    return h


async def _post_and_parse(client, ws):
    """Helper: POST a PDF, run the worker in-process, return file_id."""
    from kb.workers.tasks import parse_file_impl  # G4 — direct impl, bypassing Procrastinate queue

    resp = await client.post(
        "/files",
        files={"file": ("doc.pdf", _TINY_PDF, "application/pdf")},
        headers=headers(ws, idempotency_key=str(uuid.uuid4())),
    )
    fid = resp.json()["id"]
    await parse_file_impl(fid)
    return fid


# ===========================================================================
# §5.8 — GET /files/:id/pages
# ===========================================================================


async def test_get_pages_after_parse_returns_text(client, test_workspace):
    fid = await _post_and_parse(client, test_workspace)
    resp = await client.get(f"/files/{fid}/pages", headers=headers(test_workspace))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    page = body["items"][0]
    assert set(page.keys()) >= {"page_number", "text", "layout_json", "content_sha"}
    assert page["page_number"] == 1
    assert isinstance(page["text"], str)


async def test_get_pages_returns_empty_while_queued(client, test_workspace):
    """File posted but worker not run → pages list is empty."""
    resp = await client.post(
        "/files",
        files={"file": ("queued.pdf", _TINY_PDF, "application/pdf")},
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    fid = resp.json()["id"]
    pages = await client.get(f"/files/{fid}/pages", headers=headers(test_workspace))
    assert pages.json()["total"] == 0


async def test_get_pages_404_for_unknown_file(client, test_workspace):
    fake = str(uuid.uuid4())
    resp = await client.get(f"/files/{fake}/pages", headers=headers(test_workspace))
    assert resp.status_code == 404


async def test_get_pages_pagination(client, test_workspace):
    """Pagination still works — caller can request offset/limit."""
    fid = await _post_and_parse(client, test_workspace)
    resp = await client.get(
        f"/files/{fid}/pages?limit=2&offset=0", headers=headers(test_workspace)
    )
    body = resp.json()
    assert body["limit"] == 2
    assert body["offset"] == 0
    # total is the global count, items[] is bounded by limit
    assert len(body["items"]) <= 2


# ===========================================================================
# §5.1 #4 — DB-layer immutability of raw_pages
# ===========================================================================


async def test_raw_pages_table_rejects_update_via_kb_app(
    client, test_workspace, db_url_kb_app, db_url_superuser
):
    """raw_pages has REVOKE UPDATE,DELETE from kb_app — UPDATE attempt fails."""
    fid = await _post_and_parse(client, test_workspace)

    # As kb_app, UPDATE should fail with InsufficientPrivilege.
    with psycopg.connect(db_url_kb_app) as conn:
        conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE raw_pages SET text = 'hacked' WHERE file_id = %s",
                         (fid,))
        conn.rollback()

    # As superuser, the same UPDATE succeeds — proving the constraint is the
    # GRANT, not a CHECK or trigger.
    with psycopg.connect(db_url_superuser) as conn:
        conn.execute(
            "UPDATE raw_pages SET text = 'admin override' WHERE file_id = %s "
            "RETURNING 1",
            (fid,),
        )
        conn.commit()
