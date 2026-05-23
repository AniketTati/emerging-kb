"""Phase 1a — schemas CRUD tests (api_contracts §2.2–§2.6).

RED at G3: imports from kb.api.schemas + kb.domain.schemas land at G4.

Spec: tests/specs/phase_1a.md §4.1.
"""

from __future__ import annotations

import uuid

import pytest


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Per-test workspace fixture — fresh UUID; HTTP calls pass via X-Test-Workspace.
# ---------------------------------------------------------------------------


@pytest.fixture
def test_workspace() -> str:
    return str(uuid.uuid4())


def headers(workspace: str, *, idempotency_key: str | None = None) -> dict[str, str]:
    h = {"X-Test-Workspace": workspace}
    if idempotency_key is not None:
        h["Idempotency-Key"] = idempotency_key
    return h


async def create_schema(client, workspace, *, name="TestSchema", description=""):
    """Helper: POST /schemas; returns parsed body."""
    resp = await client.post(
        "/schemas",
        json={"name": name, "description": description},
        headers=headers(workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# POST
# ---------------------------------------------------------------------------


async def test_post_creates_schema_with_documented_shape(client, test_workspace):
    """api_contracts §2.1: response has exactly the documented keys."""
    resp = await client.post(
        "/schemas",
        json={"name": "ContractV1", "description": "Vendor agreements."},
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert set(body.keys()) == {
        "id", "name", "description", "lifecycle_state",
        "created_at", "updated_at",
    }, f"unexpected keys: {body.keys()}"
    assert body["lifecycle_state"] == "active"
    assert "workspace_id" not in body, "api_contracts §2.1: workspace_id must not appear"


async def test_post_id_is_uuid(client, test_workspace):
    s = await create_schema(client, test_workspace)
    uuid.UUID(s["id"])  # raises if invalid


async def test_post_without_idempotency_key_returns_400(client, test_workspace):
    resp = await client.post(
        "/schemas",
        json={"name": "x", "description": ""},
        headers={"X-Test-Workspace": test_workspace},  # no Idempotency-Key
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["type"].endswith("missing-idempotency-key")


async def test_post_validation_rejects_empty_name(client, test_workspace):
    resp = await client.post(
        "/schemas",
        json={"name": "", "description": ""},
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 422


async def test_post_validation_rejects_too_long_name(client, test_workspace):
    resp = await client.post(
        "/schemas",
        json={"name": "a" * 201, "description": ""},
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 422


async def test_post_validation_accepts_max_length_name(client, test_workspace):
    resp = await client.post(
        "/schemas",
        json={"name": "a" * 200, "description": ""},
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 201


async def test_post_duplicate_name_returns_409(client, test_workspace):
    await create_schema(client, test_workspace, name="Dup")
    resp = await client.post(
        "/schemas",
        json={"name": "Dup", "description": ""},
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 409
    assert resp.json()["type"].endswith("schema-name-conflict")


async def test_post_after_delete_allows_name_reuse(client, test_workspace):
    """Partial unique index excludes lifecycle_state='deleted' rows."""
    s1 = await create_schema(client, test_workspace, name="Reusable")
    await client.delete(f"/schemas/{s1['id']}", headers=headers(test_workspace))
    resp = await client.post(
        "/schemas",
        json={"name": "Reusable", "description": ""},
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# GET (list)
# ---------------------------------------------------------------------------


async def test_get_list_returns_workspace_schemas_paginated(client, test_workspace):
    for i in range(3):
        await create_schema(client, test_workspace, name=f"S{i}")
    resp = await client.get("/schemas", headers=headers(test_workspace))
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"items", "total", "limit", "offset"}
    assert body["total"] == 3
    assert len(body["items"]) == 3
    assert body["limit"] == 50
    assert body["offset"] == 0


async def test_get_list_pagination_offset_and_limit(client, test_workspace):
    for i in range(5):
        await create_schema(client, test_workspace, name=f"S{i:02d}")
    resp = await client.get("/schemas?limit=2&offset=2", headers=headers(test_workspace))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2
    assert body["limit"] == 2
    assert body["offset"] == 2


async def test_get_list_rejects_limit_over_200(client, test_workspace):
    resp = await client.get("/schemas?limit=201", headers=headers(test_workspace))
    assert resp.status_code == 400
    assert resp.json()["type"].endswith("bad-request")


async def test_get_list_sorted_by_created_at_desc(client, test_workspace):
    names = ["First", "Second", "Third"]
    for n in names:
        await create_schema(client, test_workspace, name=n)
    resp = await client.get("/schemas", headers=headers(test_workspace))
    listed = [item["name"] for item in resp.json()["items"]]
    assert listed == list(reversed(names))


# ---------------------------------------------------------------------------
# GET (one)
# ---------------------------------------------------------------------------


async def test_get_one_returns_schema(client, test_workspace):
    s = await create_schema(client, test_workspace, name="One")
    resp = await client.get(f"/schemas/{s['id']}", headers=headers(test_workspace))
    assert resp.status_code == 200
    assert resp.json()["id"] == s["id"]


async def test_get_one_nonexistent_returns_404(client, test_workspace):
    resp = await client.get(f"/schemas/{uuid.uuid4()}", headers=headers(test_workspace))
    assert resp.status_code == 404
    assert resp.json()["type"].endswith("not-found")


async def test_get_one_after_delete_returns_404(client, test_workspace):
    s = await create_schema(client, test_workspace, name="ToDelete")
    await client.delete(f"/schemas/{s['id']}", headers=headers(test_workspace))
    resp = await client.get(f"/schemas/{s['id']}", headers=headers(test_workspace))
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PUT
# ---------------------------------------------------------------------------


async def test_put_updates_name_and_description(client, test_workspace):
    s = await create_schema(client, test_workspace, name="Old", description="old")
    resp = await client.put(
        f"/schemas/{s['id']}",
        json={"name": "New", "description": "new"},
        headers=headers(test_workspace),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "New"
    assert body["description"] == "new"
    assert body["updated_at"] >= s["updated_at"]


async def test_put_nonexistent_returns_404(client, test_workspace):
    resp = await client.put(
        f"/schemas/{uuid.uuid4()}",
        json={"name": "X", "description": ""},
        headers=headers(test_workspace),
    )
    assert resp.status_code == 404


async def test_put_name_collision_returns_409(client, test_workspace):
    await create_schema(client, test_workspace, name="A")
    b = await create_schema(client, test_workspace, name="B")
    resp = await client.put(
        f"/schemas/{b['id']}",
        json={"name": "A", "description": ""},
        headers=headers(test_workspace),
    )
    assert resp.status_code == 409
    assert resp.json()["type"].endswith("schema-name-conflict")


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------


async def test_delete_soft_deletes_schema(client, test_workspace, db_superuser):
    """DELETE returns 204; row stays in DB with lifecycle_state='deleted'."""
    s = await create_schema(client, test_workspace, name="ToDelete")
    resp = await client.delete(f"/schemas/{s['id']}", headers=headers(test_workspace))
    assert resp.status_code == 204
    assert resp.text == ""

    # Verify via superuser (bypasses RLS): row exists with lifecycle_state='deleted'.
    row = await db_superuser.fetchrow(
        "SELECT lifecycle_state FROM schemas WHERE id = %s", uuid.UUID(s["id"])
    )
    assert row is not None
    assert row[0] == "deleted"


async def test_delete_already_deleted_returns_404(client, test_workspace):
    s = await create_schema(client, test_workspace, name="Twice")
    r1 = await client.delete(f"/schemas/{s['id']}", headers=headers(test_workspace))
    assert r1.status_code == 204
    r2 = await client.delete(f"/schemas/{s['id']}", headers=headers(test_workspace))
    assert r2.status_code == 404, (
        "second DELETE without an Idempotency-Key should reflect the current state (deleted = not-found), "
        "not replay the prior 204"
    )
