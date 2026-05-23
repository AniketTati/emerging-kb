"""Phase 1c — coarse-grained versioning + full-subtree snapshot + nested diff
+ rollback restores entities/fields/relationships (api_contracts §4.1–§4.3, §4.8).

RED at G3: imports from kb.api.schema_hierarchy + extended kb.domain.schema_versions
land at G4.

Spec: tests/specs/phase_1c.md §4.4.
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


async def post_schema(client, ws, *, name="HSchema"):
    resp = await client.post(
        "/schemas",
        json={"name": name, "description": ""},
        headers=headers(ws, idempotency_key=str(uuid.uuid4())),
    )
    return resp.json()


async def post_entity(client, ws, sid, *, name, description=""):
    resp = await client.post(
        f"/schemas/{sid}/entities",
        json={"name": name, "description": description},
        headers=headers(ws, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 201
    return resp.json()


async def post_field(client, ws, sid, eid, *, name, type="string", nl_description=""):
    resp = await client.post(
        f"/schemas/{sid}/entities/{eid}/fields",
        json={"name": name, "type": type, "nl_description": nl_description},
        headers=headers(ws, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 201
    return resp.json()


async def post_relationship(client, ws, sid, *, name, from_id, to_id, kind="contains"):
    resp = await client.post(
        f"/schemas/{sid}/relationships",
        json={"name": name, "from_entity_id": from_id, "to_entity_id": to_id,
              "kind": kind},
        headers=headers(ws, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 201
    return resp.json()


async def get_current_version(client, ws, sid):
    resp = await client.get(f"/schemas/{sid}", headers=headers(ws))
    return resp.json()["current_version"]


# ===========================================================================
# §4.1 #3 — coarse-grained versioning: every nested mutation bumps current_version
# ===========================================================================


async def test_entity_post_bumps_schemas_current_version(client, test_workspace):
    s = await post_schema(client, test_workspace)
    assert s["current_version"] == 1
    await post_entity(client, test_workspace, s["id"], name="File")
    assert await get_current_version(client, test_workspace, s["id"]) == 2


async def test_field_post_bumps_schemas_current_version(client, test_workspace):
    s = await post_schema(client, test_workspace)
    e = await post_entity(client, test_workspace, s["id"], name="File")
    # POST schema=v1; POST entity=v2; POST field=v3
    await post_field(client, test_workspace, s["id"], e["id"], name="title")
    assert await get_current_version(client, test_workspace, s["id"]) == 3


async def test_relationship_post_bumps_schemas_current_version(client, test_workspace):
    s = await post_schema(client, test_workspace)
    e1 = await post_entity(client, test_workspace, s["id"], name="A")
    e2 = await post_entity(client, test_workspace, s["id"], name="B")
    # v1=schema, v2=A, v3=B, v4=relationship
    await post_relationship(
        client, test_workspace, s["id"],
        name="r", from_id=e1["id"], to_id=e2["id"],
    )
    assert await get_current_version(client, test_workspace, s["id"]) == 4


# ===========================================================================
# §4.2 — snapshot body shape includes entities w/ fields + relationships w/ names
# ===========================================================================


async def test_snapshot_body_includes_entities_with_fields(client, test_workspace):
    s = await post_schema(client, test_workspace, name="SnapShape")
    e = await post_entity(client, test_workspace, s["id"], name="File", description="desc")
    await post_field(client, test_workspace, s["id"], e["id"], name="title", type="string")
    await post_field(client, test_workspace, s["id"], e["id"], name="opened", type="date")
    cv = await get_current_version(client, test_workspace, s["id"])
    resp = await client.get(
        f"/schemas/{s['id']}/versions/{cv}", headers=headers(test_workspace)
    )
    body = resp.json()["body"]
    assert body["name"] == "SnapShape"
    # entities[0] = "File" with both fields sorted by name (opened before title)
    assert len(body["entities"]) == 1
    file_entity = body["entities"][0]
    assert file_entity["name"] == "File"
    field_names = [f["name"] for f in file_entity["fields"]]
    assert sorted(field_names) == ["opened", "title"]


async def test_snapshot_body_includes_relationships_with_names(client, test_workspace):
    """§4.1 #5: snapshot references entities by name, not UUID."""
    s = await post_schema(client, test_workspace)
    e1 = await post_entity(client, test_workspace, s["id"], name="File")
    e2 = await post_entity(client, test_workspace, s["id"], name="Case")
    await post_relationship(
        client, test_workspace, s["id"],
        name="file_to_case", from_id=e1["id"], to_id=e2["id"], kind="contains",
    )
    cv = await get_current_version(client, test_workspace, s["id"])
    resp = await client.get(
        f"/schemas/{s['id']}/versions/{cv}", headers=headers(test_workspace)
    )
    body = resp.json()["body"]
    rel = body["relationships"][0]
    # Name-resolved, NOT uuid-resolved:
    assert rel["from"] == "File"
    assert rel["to"] == "Case"
    assert "from_entity_id" not in rel
    assert "to_entity_id" not in rel


# ===========================================================================
# §4.1 #5 + §3.9 — rollback restores entities / fields / relationships
# ===========================================================================


async def test_rollback_restores_entities(client, test_workspace, db_superuser):
    """Add 2 entities, then rollback to v1 (schema-only) — both entities soft-deleted."""
    s = await post_schema(client, test_workspace)
    await post_entity(client, test_workspace, s["id"], name="A")
    await post_entity(client, test_workspace, s["id"], name="B")
    # Rollback to v1
    resp = await client.post(
        f"/schemas/{s['id']}/versions/1/rollback",
        json={}, headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 200
    # Live list is empty (entities soft-deleted as part of rollback).
    listing = await client.get(
        f"/schemas/{s['id']}/entities", headers=headers(test_workspace)
    )
    assert listing.json()["total"] == 0
    # Superuser confirms rows still exist but lifecycle_state='deleted'.
    row = await db_superuser.fetchrow(
        "SELECT count(*) FROM schema_entities WHERE schema_id = %s",
        uuid.UUID(s["id"]),
    )
    assert row[0] == 2  # both rows still in DB


async def test_rollback_restores_fields_under_entities(client, test_workspace):
    """Add field, snapshot, delete it, rollback — field is restored (new UUID, same name)."""
    s = await post_schema(client, test_workspace)
    e = await post_entity(client, test_workspace, s["id"], name="E")
    f = await post_field(client, test_workspace, s["id"], e["id"], name="restored",
                         type="string", nl_description="original")
    target_v = await get_current_version(client, test_workspace, s["id"])
    # Delete the field
    await client.delete(
        f"/schemas/{s['id']}/entities/{e['id']}/fields/{f['id']}",
        headers=headers(test_workspace),
    )
    # Rollback to the snapshot that had the field
    rb = await client.post(
        f"/schemas/{s['id']}/versions/{target_v}/rollback",
        json={}, headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert rb.status_code == 200
    # GET fields list should have one field with name="restored" (new UUID).
    fields_resp = await client.get(
        f"/schemas/{s['id']}/entities/{e['id']}/fields",
        headers=headers(test_workspace),
    )
    fields = fields_resp.json()["items"]
    assert len(fields) == 1
    assert fields[0]["name"] == "restored"
    assert fields[0]["nl_description"] == "original"


async def test_rollback_restores_relationships_by_name_resolution(client, test_workspace):
    """§4.1 #5: rollback resolves relationship from/to by entity name, not UUID."""
    s = await post_schema(client, test_workspace)
    e1 = await post_entity(client, test_workspace, s["id"], name="A")
    e2 = await post_entity(client, test_workspace, s["id"], name="B")
    r = await post_relationship(
        client, test_workspace, s["id"],
        name="ab", from_id=e1["id"], to_id=e2["id"],
    )
    target_v = await get_current_version(client, test_workspace, s["id"])
    # Delete the relationship
    await client.delete(
        f"/schemas/{s['id']}/relationships/{r['id']}",
        headers=headers(test_workspace),
    )
    # Rollback
    rb = await client.post(
        f"/schemas/{s['id']}/versions/{target_v}/rollback",
        json={}, headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert rb.status_code == 200
    # GET relationships should have one row with from_entity_id=e1, to_entity_id=e2
    rel_resp = await client.get(
        f"/schemas/{s['id']}/relationships", headers=headers(test_workspace)
    )
    rels = rel_resp.json()["items"]
    assert len(rels) == 1
    assert rels[0]["name"] == "ab"
    assert rels[0]["from_entity_id"] == e1["id"]
    assert rels[0]["to_entity_id"] == e2["id"]


# ===========================================================================
# §4.3 — diff format extension (nested dotted paths)
# ===========================================================================


async def test_diff_for_added_entity_uses_nested_path(client, test_workspace):
    s = await post_schema(client, test_workspace)
    await post_entity(client, test_workspace, s["id"], name="File")
    # v2 diff should show entities.File as added.
    resp = await client.get(
        f"/schemas/{s['id']}/versions/2", headers=headers(test_workspace)
    )
    diff = resp.json()["diff_from_prior"]
    added_paths = [item["path"] for item in diff["added"]]
    assert "entities.File" in added_paths


async def test_diff_for_changed_field_type_uses_nested_path(client, test_workspace):
    s = await post_schema(client, test_workspace)
    e = await post_entity(client, test_workspace, s["id"], name="E")
    f = await post_field(client, test_workspace, s["id"], e["id"], name="x", type="string")
    # PUT to change type from string → datetime
    await client.put(
        f"/schemas/{s['id']}/entities/{e['id']}/fields/{f['id']}",
        json={"name": "x", "type": "datetime", "nl_description": "", "is_required": False},
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    cv = await get_current_version(client, test_workspace, s["id"])
    resp = await client.get(
        f"/schemas/{s['id']}/versions/{cv}", headers=headers(test_workspace)
    )
    diff = resp.json()["diff_from_prior"]
    changed = diff["changed"]
    # Expect a change entry at path entities.E.fields.x.type
    matching = [c for c in changed if c["path"] == "entities.E.fields.x.type"]
    assert len(matching) == 1
    assert matching[0]["old"] == "string"
    assert matching[0]["new"] == "datetime"
