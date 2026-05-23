"""Phase 1c — schema entities CRUD tests (api_contracts §4.4–§4.8).

RED at G3: imports from `kb.api.schema_hierarchy` + extended `kb.domain.*`
land at G4.

Spec: tests/specs/phase_1c.md §4.1.
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


async def create_schema(client, workspace, *, name="HierarchySchema"):
    resp = await client.post(
        "/schemas",
        json={"name": name, "description": ""},
        headers=headers(workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def create_entity(client, workspace, schema_id, *, name="File", description=""):
    resp = await client.post(
        f"/schemas/{schema_id}/entities",
        json={"name": name, "description": description},
        headers=headers(workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ===========================================================================
# §4.5 — POST /schemas/:id/entities
# ===========================================================================


async def test_post_creates_entity_with_documented_shape(client, test_workspace):
    s = await create_schema(client, test_workspace)
    resp = await client.post(
        f"/schemas/{s['id']}/entities",
        json={"name": "File", "description": "Top-level case file."},
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert set(body.keys()) == {
        "id", "name", "description", "lifecycle_state",
        "created_at", "updated_at",
    }
    assert body["lifecycle_state"] == "active"
    assert "workspace_id" not in body
    assert "schema_id" not in body


async def test_post_requires_idempotency_key(client, test_workspace):
    s = await create_schema(client, test_workspace)
    resp = await client.post(
        f"/schemas/{s['id']}/entities",
        json={"name": "X"},
        headers={"X-Test-Workspace": test_workspace},  # no Idempotency-Key
    )
    assert resp.status_code == 400
    assert resp.json()["type"].endswith("/missing-idempotency-key")


async def test_post_validation_rejects_empty_name(client, test_workspace):
    s = await create_schema(client, test_workspace)
    resp = await client.post(
        f"/schemas/{s['id']}/entities",
        json={"name": ""},
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 422
    assert resp.json()["type"].endswith("/validation-error")


async def test_post_duplicate_name_returns_409(client, test_workspace):
    s = await create_schema(client, test_workspace)
    await create_entity(client, test_workspace, s["id"], name="Dup")
    resp = await client.post(
        f"/schemas/{s['id']}/entities",
        json={"name": "Dup"},
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 409
    assert resp.json()["type"].endswith("/entity-name-conflict")


async def test_post_404_for_unknown_schema(client, test_workspace):
    fake = str(uuid.uuid4())
    resp = await client.post(
        f"/schemas/{fake}/entities",
        json={"name": "Ghost"},
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 404


# ===========================================================================
# §4.6 — GET /schemas/:id/entities
# ===========================================================================


async def test_get_list_returns_paginated_entities(client, test_workspace):
    s = await create_schema(client, test_workspace)
    for n in ("File", "Case", "Note"):
        await create_entity(client, test_workspace, s["id"], name=n)
    resp = await client.get(
        f"/schemas/{s['id']}/entities", headers=headers(test_workspace)
    )
    body = resp.json()
    assert body["total"] == 3
    assert body["limit"] == 50
    assert body["offset"] == 0
    assert len(body["items"]) == 3


# ===========================================================================
# §4.7 — PUT /schemas/:id/entities/:eid
# ===========================================================================


async def test_put_updates_name_and_description(client, test_workspace):
    s = await create_schema(client, test_workspace)
    e = await create_entity(client, test_workspace, s["id"], name="OldName")
    resp = await client.put(
        f"/schemas/{s['id']}/entities/{e['id']}",
        json={"name": "NewName", "description": "renamed"},
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "NewName"
    assert body["description"] == "renamed"
    assert body["updated_at"] != e["updated_at"]


# ===========================================================================
# §4.8 — DELETE /schemas/:id/entities/:eid
# ===========================================================================


async def test_delete_soft_deletes_entity(client, test_workspace, db_superuser):
    s = await create_schema(client, test_workspace)
    e = await create_entity(client, test_workspace, s["id"], name="Doomed")
    resp = await client.delete(
        f"/schemas/{s['id']}/entities/{e['id']}",
        headers=headers(test_workspace),
    )
    assert resp.status_code == 204
    # Subsequent GET via list should not include it.
    listing = await client.get(
        f"/schemas/{s['id']}/entities", headers=headers(test_workspace)
    )
    names = [item["name"] for item in listing.json()["items"]]
    assert "Doomed" not in names

    # Superuser confirms the row is soft-deleted, not hard-deleted.
    row = await db_superuser.fetchrow(
        "SELECT lifecycle_state FROM schema_entities WHERE id = %s",
        uuid.UUID(e["id"]),
    )
    assert row is not None
    assert row[0] == "deleted"


async def test_delete_cascades_to_fields_and_relationships(
    client, test_workspace, db_superuser
):
    """§4.8 cascade: deleting an entity soft-deletes its fields + any relationships referencing it."""
    s = await create_schema(client, test_workspace)
    e1 = await create_entity(client, test_workspace, s["id"], name="Parent")
    e2 = await create_entity(client, test_workspace, s["id"], name="Other")
    # Two fields on e1
    for fname in ("field_a", "field_b"):
        await client.post(
            f"/schemas/{s['id']}/entities/{e1['id']}/fields",
            json={"name": fname, "type": "string", "nl_description": ""},
            headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
        )
    # A relationship pointing at e1 (e2 → e1)
    await client.post(
        f"/schemas/{s['id']}/relationships",
        json={
            "name": "rel_to_parent",
            "from_entity_id": e2["id"],
            "to_entity_id": e1["id"],
            "kind": "references",
        },
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    # Delete e1 — both fields + the relationship cascade-soft-delete.
    resp = await client.delete(
        f"/schemas/{s['id']}/entities/{e1['id']}",
        headers=headers(test_workspace),
    )
    assert resp.status_code == 204

    field_count = await db_superuser.fetchrow(
        "SELECT count(*) FROM schema_fields "
        "WHERE entity_id = %s AND lifecycle_state = 'active'",
        uuid.UUID(e1["id"]),
    )
    assert field_count[0] == 0

    rel_count = await db_superuser.fetchrow(
        "SELECT count(*) FROM schema_relationships "
        "WHERE (from_entity_id = %s OR to_entity_id = %s) AND lifecycle_state = 'active'",
        uuid.UUID(e1["id"]), uuid.UUID(e1["id"]),
    )
    assert rel_count[0] == 0


# ===========================================================================
# RLS — workspace isolation (§4.1 #1)
# ===========================================================================


async def test_entity_isolated_across_workspaces(client, test_workspace):
    workspace_b = str(uuid.uuid4())
    s = await create_schema(client, test_workspace)
    await create_entity(client, test_workspace, s["id"], name="Private")
    # Workspace B can't even see the parent schema → 404 on the entity list.
    resp = await client.get(
        f"/schemas/{s['id']}/entities", headers=headers(workspace_b)
    )
    assert resp.status_code == 404
