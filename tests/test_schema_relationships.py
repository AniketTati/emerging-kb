"""Phase 1c — schema relationships CRUD tests (api_contracts §4.14–§4.17).

RED at G3: imports from kb.api.schema_hierarchy land at G4.

Spec: tests/specs/phase_1c.md §4.3.
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


async def create_schema_with_two_entities(client, workspace, *, schema_name="S"):
    s = await client.post(
        "/schemas",
        json={"name": schema_name, "description": ""},
        headers=headers(workspace, idempotency_key=str(uuid.uuid4())),
    )
    schema = s.json()
    e1 = await client.post(
        f"/schemas/{schema['id']}/entities",
        json={"name": "File", "description": ""},
        headers=headers(workspace, idempotency_key=str(uuid.uuid4())),
    )
    e2 = await client.post(
        f"/schemas/{schema['id']}/entities",
        json={"name": "Case", "description": ""},
        headers=headers(workspace, idempotency_key=str(uuid.uuid4())),
    )
    return schema, e1.json(), e2.json()


async def create_relationship(client, workspace, schema_id, *, name, from_id, to_id,
                              kind="contains", cardinality="one_to_many",
                              cascade_delete=True, single_parent=True):
    resp = await client.post(
        f"/schemas/{schema_id}/relationships",
        json={
            "name": name,
            "from_entity_id": from_id,
            "to_entity_id": to_id,
            "kind": kind,
            "cardinality": cardinality,
            "cascade_delete": cascade_delete,
            "single_parent": single_parent,
        },
        headers=headers(workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ===========================================================================
# §4.15 — POST /schemas/:id/relationships
# ===========================================================================


async def test_post_creates_relationship_with_documented_shape(client, test_workspace):
    s, e1, e2 = await create_schema_with_two_entities(client, test_workspace)
    resp = await client.post(
        f"/schemas/{s['id']}/relationships",
        json={
            "name": "file_contains_case",
            "from_entity_id": e1["id"],
            "to_entity_id": e2["id"],
            "kind": "contains",
            "cardinality": "one_to_many",
            "cascade_delete": True,
            "single_parent": True,
        },
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert set(body.keys()) == {
        "id", "name", "from_entity_id", "to_entity_id",
        "kind", "cardinality", "cascade_delete", "single_parent",
        "lifecycle_state", "created_at", "updated_at",
    }
    assert body["kind"] == "contains"
    assert body["cardinality"] == "one_to_many"
    assert body["cascade_delete"] is True
    assert body["single_parent"] is True


async def test_post_requires_idempotency_key(client, test_workspace):
    s, e1, e2 = await create_schema_with_two_entities(client, test_workspace)
    resp = await client.post(
        f"/schemas/{s['id']}/relationships",
        json={
            "name": "r", "from_entity_id": e1["id"], "to_entity_id": e2["id"],
            "kind": "contains",
        },
        headers={"X-Test-Workspace": test_workspace},
    )
    assert resp.status_code == 400
    assert resp.json()["type"].endswith("/missing-idempotency-key")


async def test_post_validation_rejects_invalid_kind(client, test_workspace):
    s, e1, e2 = await create_schema_with_two_entities(client, test_workspace)
    resp = await client.post(
        f"/schemas/{s['id']}/relationships",
        json={
            "name": "r", "from_entity_id": e1["id"], "to_entity_id": e2["id"],
            "kind": "ownership",  # not in the enum
        },
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 422
    assert resp.json()["type"].endswith("/validation-error")


async def test_post_validation_rejects_cross_schema_entities(client, test_workspace):
    """§4.14: from/to must reference entities in the SAME schema as the relationship."""
    s1, e1, _ = await create_schema_with_two_entities(client, test_workspace, schema_name="S1")
    s2, e2, _ = await create_schema_with_two_entities(client, test_workspace, schema_name="S2")
    # Post relationship on s1 with from=e1(s1) but to=e2(s2)
    resp = await client.post(
        f"/schemas/{s1['id']}/relationships",
        json={
            "name": "cross", "from_entity_id": e1["id"], "to_entity_id": e2["id"],
            "kind": "references",
        },
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 422


async def test_post_duplicate_name_returns_409(client, test_workspace):
    s, e1, e2 = await create_schema_with_two_entities(client, test_workspace)
    await create_relationship(
        client, test_workspace, s["id"],
        name="dup", from_id=e1["id"], to_id=e2["id"],
    )
    resp = await client.post(
        f"/schemas/{s['id']}/relationships",
        json={
            "name": "dup", "from_entity_id": e1["id"], "to_entity_id": e2["id"],
            "kind": "contains",
        },
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 409
    assert resp.json()["type"].endswith("/relationship-name-conflict")


# ===========================================================================
# §4.16 — GET /schemas/:id/relationships
# ===========================================================================


async def test_get_list_returns_paginated_relationships(client, test_workspace):
    s, e1, e2 = await create_schema_with_two_entities(client, test_workspace)
    for name in ("r1", "r2", "r3"):
        await create_relationship(
            client, test_workspace, s["id"],
            name=name, from_id=e1["id"], to_id=e2["id"],
        )
    resp = await client.get(
        f"/schemas/{s['id']}/relationships", headers=headers(test_workspace)
    )
    body = resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3


# ===========================================================================
# §4.17 — DELETE /schemas/:id/relationships/:rid
# ===========================================================================


async def test_delete_soft_deletes_relationship(client, test_workspace, db_superuser):
    s, e1, e2 = await create_schema_with_two_entities(client, test_workspace)
    r = await create_relationship(
        client, test_workspace, s["id"],
        name="bye", from_id=e1["id"], to_id=e2["id"],
    )
    resp = await client.delete(
        f"/schemas/{s['id']}/relationships/{r['id']}",
        headers=headers(test_workspace),
    )
    assert resp.status_code == 204
    row = await db_superuser.fetchrow(
        "SELECT lifecycle_state FROM schema_relationships WHERE id = %s",
        uuid.UUID(r["id"]),
    )
    assert row[0] == "deleted"


# ===========================================================================
# RLS isolation
# ===========================================================================


async def test_relationship_isolated_across_workspaces(client, test_workspace):
    workspace_b = str(uuid.uuid4())
    s, e1, e2 = await create_schema_with_two_entities(client, test_workspace)
    await create_relationship(
        client, test_workspace, s["id"],
        name="solo", from_id=e1["id"], to_id=e2["id"],
    )
    resp = await client.get(
        f"/schemas/{s['id']}/relationships", headers=headers(workspace_b)
    )
    assert resp.status_code == 404
