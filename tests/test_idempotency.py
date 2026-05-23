"""Phase 1a — cross-cutting Idempotency-Key behavior.

RED at G3: depends on kb.api.idempotency (G4).

Spec: tests/specs/phase_1a.md §4.3.

These tests prove that the Phase 0 idempotency_keys table actually backs
the Idempotency-Key header contract: same (workspace_id, key) → cached
response replayed verbatim. Cross-workspace isolation: same key in
different workspaces are independent rows.
"""

from __future__ import annotations

import uuid

import pytest


pytestmark = pytest.mark.asyncio


@pytest.fixture
def test_workspace() -> str:
    return str(uuid.uuid4())


def headers(workspace: str, *, idempotency_key: str | None = None) -> dict[str, str]:
    h = {"X-Test-Workspace": workspace}
    if idempotency_key is not None:
        h["Idempotency-Key"] = idempotency_key
    return h


async def test_post_with_same_idempotency_key_replays_cached_response(
    client, test_workspace, db_superuser
):
    """Two POSTs with the same key + body → identical 201, single row in DB."""
    key = str(uuid.uuid4())
    payload = {"name": "OnceOnly", "description": ""}

    r1 = await client.post("/schemas", json=payload, headers=headers(test_workspace, idempotency_key=key))
    assert r1.status_code == 201
    r2 = await client.post("/schemas", json=payload, headers=headers(test_workspace, idempotency_key=key))
    assert r2.status_code == 201
    assert r1.json() == r2.json(), "replay must return the exact original body"

    # Verify single row in DB (no duplicate).
    rows = await db_superuser.fetch(
        "SELECT id FROM schemas WHERE workspace_id = %s AND name = 'OnceOnly'",
        uuid.UUID(test_workspace),
    )
    assert len(rows) == 1


async def test_post_idempotency_key_isolated_per_workspace(client):
    """Same key in workspace A and workspace B are independent (PK is (workspace_id, key))."""
    key = str(uuid.uuid4())
    payload = {"name": "PerWorkspace", "description": ""}
    ws_a = str(uuid.uuid4())
    ws_b = str(uuid.uuid4())

    r_a = await client.post("/schemas", json=payload, headers=headers(ws_a, idempotency_key=key))
    r_b = await client.post("/schemas", json=payload, headers=headers(ws_b, idempotency_key=key))

    assert r_a.status_code == 201
    assert r_b.status_code == 201
    assert r_a.json()["id"] != r_b.json()["id"], "different workspaces → different schemas, same key allowed"


async def test_put_with_idempotency_key_replays(client, test_workspace):
    """PUT with the same idempotency key returns the cached body."""
    create_key = str(uuid.uuid4())
    create = await client.post(
        "/schemas",
        json={"name": "Initial", "description": ""},
        headers=headers(test_workspace, idempotency_key=create_key),
    )
    sid = create.json()["id"]

    put_key = str(uuid.uuid4())
    payload = {"name": "Renamed", "description": "v2"}

    r1 = await client.put(f"/schemas/{sid}", json=payload, headers=headers(test_workspace, idempotency_key=put_key))
    assert r1.status_code == 200
    r2 = await client.put(f"/schemas/{sid}", json=payload, headers=headers(test_workspace, idempotency_key=put_key))
    assert r2.status_code == 200
    assert r1.json() == r2.json()


async def test_delete_with_idempotency_key_replays(client, test_workspace):
    """DELETE with the same idempotency key returns 204 (cached), NOT 404.

    This is the difference between idempotent-key replay semantics and
    second-call-sees-deleted-state. Without the key, the second DELETE
    sees a deleted row and returns 404. With the key, the cached 204
    is replayed verbatim.
    """
    create = await client.post(
        "/schemas",
        json={"name": "ToDelete", "description": ""},
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    sid = create.json()["id"]
    delete_key = str(uuid.uuid4())

    r1 = await client.delete(f"/schemas/{sid}", headers=headers(test_workspace, idempotency_key=delete_key))
    assert r1.status_code == 204
    r2 = await client.delete(f"/schemas/{sid}", headers=headers(test_workspace, idempotency_key=delete_key))
    assert r2.status_code == 204, (
        "with the same Idempotency-Key, second DELETE should replay the cached 204; "
        "without the key, it would be 404 (already-deleted)"
    )
