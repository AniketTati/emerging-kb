"""Phase 1a — workspace isolation via the schemas endpoints.

RED at G3: imports from kb.api.schemas land at G4.

Spec: tests/specs/phase_1a.md §4.2.

These tests prove that the WorkspaceMiddleware + PG RLS policy combined
mean a request authenticated as workspace B can never see workspace A's
rows — even by URL probe (404 not 403 hides existence).
"""

from __future__ import annotations

import uuid

import pytest


pytestmark = pytest.mark.asyncio


WS_A = "11111111-1111-1111-1111-111111111111"
WS_B = "22222222-2222-2222-2222-222222222222"


def headers(workspace: str, *, idempotency_key: str | None = None) -> dict[str, str]:
    h = {"X-Test-Workspace": workspace}
    if idempotency_key is not None:
        h["Idempotency-Key"] = idempotency_key
    return h


async def _create_in(client, workspace, name="Iso"):
    r = await client.post(
        "/schemas",
        json={"name": name, "description": ""},
        headers=headers(workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_list_isolated_across_workspaces(client):
    """A's schema is invisible when listing as B (RLS filters)."""
    await _create_in(client, WS_A, name="VisibleToA")
    resp = await client.get("/schemas", headers=headers(WS_B))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["items"] == []


async def test_get_one_returns_404_for_wrong_workspace(client):
    """Probing A's id while authenticated as B → 404 (not 403)."""
    a = await _create_in(client, WS_A, name="HiddenFromB")
    resp = await client.get(f"/schemas/{a['id']}", headers=headers(WS_B))
    assert resp.status_code == 404, (
        "api_contracts §2.4: must be 404 (existence leak avoided), never 403"
    )


async def test_put_returns_404_for_wrong_workspace(client):
    a = await _create_in(client, WS_A, name="PutTarget")
    resp = await client.put(
        f"/schemas/{a['id']}",
        json={"name": "renamed", "description": ""},
        headers=headers(WS_B),
    )
    assert resp.status_code == 404


async def test_delete_returns_404_for_wrong_workspace(client):
    a = await _create_in(client, WS_A, name="DelTarget")
    resp = await client.delete(f"/schemas/{a['id']}", headers=headers(WS_B))
    assert resp.status_code == 404


async def test_duplicate_name_across_workspaces_is_allowed(client):
    """Workspaces are independent name namespaces; both POSTs succeed."""
    a = await _create_in(client, WS_A, name="Shared")
    b = await _create_in(client, WS_B, name="Shared")
    assert a["id"] != b["id"]
