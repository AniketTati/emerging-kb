"""Phase 1c — schema fields CRUD tests (api_contracts §4.9–§4.13).

RED at G3: imports from kb.api.schema_hierarchy land at G4.

Spec: tests/specs/phase_1c.md §4.2.
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


async def create_schema_with_entity(client, workspace, *, schema_name="S", entity_name="E"):
    """Helper: POST schema + POST entity; return (schema_body, entity_body)."""
    s = await client.post(
        "/schemas",
        json={"name": schema_name, "description": ""},
        headers=headers(workspace, idempotency_key=str(uuid.uuid4())),
    )
    schema = s.json()
    e = await client.post(
        f"/schemas/{schema['id']}/entities",
        json={"name": entity_name, "description": ""},
        headers=headers(workspace, idempotency_key=str(uuid.uuid4())),
    )
    entity = e.json()
    return schema, entity


async def create_field(client, workspace, schema_id, entity_id, *, name, type="string",
                       nl_description="", is_required=False):
    resp = await client.post(
        f"/schemas/{schema_id}/entities/{entity_id}/fields",
        json={"name": name, "type": type, "nl_description": nl_description,
              "is_required": is_required},
        headers=headers(workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ===========================================================================
# §4.10 — POST /schemas/:id/entities/:eid/fields
# ===========================================================================


async def test_post_creates_field_with_documented_shape(client, test_workspace):
    s, e = await create_schema_with_entity(client, test_workspace)
    resp = await client.post(
        f"/schemas/{s['id']}/entities/{e['id']}/fields",
        json={
            "name": "title",
            "type": "string",
            "nl_description": "Document title from the header",
            "is_required": True,
        },
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert set(body.keys()) == {
        "id", "name", "type", "nl_description", "is_required",
        "lifecycle_state", "created_at", "updated_at",
    }
    assert body["type"] == "string"
    assert body["nl_description"] == "Document title from the header"
    assert body["is_required"] is True


async def test_post_requires_idempotency_key(client, test_workspace):
    s, e = await create_schema_with_entity(client, test_workspace)
    resp = await client.post(
        f"/schemas/{s['id']}/entities/{e['id']}/fields",
        json={"name": "x", "type": "string", "nl_description": ""},
        headers={"X-Test-Workspace": test_workspace},
    )
    assert resp.status_code == 400
    assert resp.json()["type"].endswith("/missing-idempotency-key")


async def test_post_validation_rejects_invalid_type(client, test_workspace):
    s, e = await create_schema_with_entity(client, test_workspace)
    resp = await client.post(
        f"/schemas/{s['id']}/entities/{e['id']}/fields",
        json={"name": "weird", "type": "json", "nl_description": ""},
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 422
    assert resp.json()["type"].endswith("/validation-error")


async def test_post_duplicate_name_returns_409(client, test_workspace):
    s, e = await create_schema_with_entity(client, test_workspace)
    await create_field(client, test_workspace, s["id"], e["id"], name="dup")
    resp = await client.post(
        f"/schemas/{s['id']}/entities/{e['id']}/fields",
        json={"name": "dup", "type": "string", "nl_description": ""},
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 409
    assert resp.json()["type"].endswith("/field-name-conflict")


async def test_post_404_for_unknown_entity(client, test_workspace):
    s, _ = await create_schema_with_entity(client, test_workspace)
    fake_entity = str(uuid.uuid4())
    resp = await client.post(
        f"/schemas/{s['id']}/entities/{fake_entity}/fields",
        json={"name": "x", "type": "string", "nl_description": ""},
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 404


# ===========================================================================
# §4.11 — GET /schemas/:id/entities/:eid/fields
# ===========================================================================


async def test_get_list_returns_paginated_fields(client, test_workspace):
    s, e = await create_schema_with_entity(client, test_workspace)
    for n in ("a", "b", "c", "d"):
        await create_field(client, test_workspace, s["id"], e["id"], name=n)
    resp = await client.get(
        f"/schemas/{s['id']}/entities/{e['id']}/fields",
        headers=headers(test_workspace),
    )
    body = resp.json()
    assert body["total"] == 4
    assert len(body["items"]) == 4


# ===========================================================================
# §4.12 — PUT /schemas/:id/entities/:eid/fields/:fid
# ===========================================================================


async def test_put_updates_type_and_nl_description(client, test_workspace):
    s, e = await create_schema_with_entity(client, test_workspace)
    f = await create_field(client, test_workspace, s["id"], e["id"], name="opened",
                           type="string", nl_description="old prompt")
    resp = await client.put(
        f"/schemas/{s['id']}/entities/{e['id']}/fields/{f['id']}",
        json={"name": "opened", "type": "datetime",
              "nl_description": "new prompt", "is_required": True},
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "datetime"
    assert body["nl_description"] == "new prompt"
    assert body["is_required"] is True


# ===========================================================================
# §4.13 — DELETE /schemas/:id/entities/:eid/fields/:fid
# ===========================================================================


async def test_delete_soft_deletes_field(client, test_workspace, db_superuser):
    s, e = await create_schema_with_entity(client, test_workspace)
    f = await create_field(client, test_workspace, s["id"], e["id"], name="goner")
    resp = await client.delete(
        f"/schemas/{s['id']}/entities/{e['id']}/fields/{f['id']}",
        headers=headers(test_workspace),
    )
    assert resp.status_code == 204
    row = await db_superuser.fetchrow(
        "SELECT lifecycle_state FROM schema_fields WHERE id = %s",
        uuid.UUID(f["id"]),
    )
    assert row[0] == "deleted"
