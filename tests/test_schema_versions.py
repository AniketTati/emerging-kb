"""Phase 1b — schemas versioning tests (api_contracts §3.7 + §3.8 + §3.9).

RED at G3: imports from `kb.api.schema_versions` + extended `kb.domain.schemas`
land at G4.

Spec: tests/specs/phase_1b.md §4.1.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Per-test workspace fixture + helpers (mirror the 1a pattern).
# ---------------------------------------------------------------------------


@pytest.fixture
def test_workspace() -> str:
    return str(uuid.uuid4())


def headers(workspace: str, *, idempotency_key: str | None = None) -> dict[str, str]:
    h = {"X-Test-Workspace": workspace}
    if idempotency_key is not None:
        h["Idempotency-Key"] = idempotency_key
    return h


async def post_schema(client, workspace, *, name="VTest", description=""):
    """POST /schemas; return parsed body."""
    resp = await client.post(
        "/schemas",
        json={"name": name, "description": description},
        headers=headers(workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def put_schema(client, workspace, schema_id, *, name, description=""):
    """PUT /schemas/:id; return parsed body."""
    resp = await client.put(
        f"/schemas/{schema_id}",
        json={"name": name, "description": description},
        headers=headers(workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def rollback(client, workspace, schema_id, version, *, idempotency_key=None):
    """POST /schemas/:id/versions/:v/rollback; return (status, body)."""
    h = headers(workspace, idempotency_key=idempotency_key or str(uuid.uuid4()))
    resp = await client.post(
        f"/schemas/{schema_id}/versions/{version}/rollback",
        json={},
        headers=h,
    )
    return resp.status_code, resp.json() if resp.content else None


# ===========================================================================
# §3.7 — GET /schemas/:id/versions (list)
# ===========================================================================


async def test_list_returns_only_v1_after_post(client, test_workspace):
    s = await post_schema(client, test_workspace, name="Solo")
    resp = await client.get(
        f"/schemas/{s['id']}/versions", headers=headers(test_workspace)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["version"] == 1
    assert item["kind"] == "post"
    assert item["parent_version"] is None


async def test_list_returns_newest_first(client, test_workspace):
    s = await post_schema(client, test_workspace, name="Stack")
    await put_schema(client, test_workspace, s["id"], name="Stack", description="v2")
    await put_schema(client, test_workspace, s["id"], name="Stack", description="v3")
    resp = await client.get(
        f"/schemas/{s['id']}/versions", headers=headers(test_workspace)
    )
    versions = [item["version"] for item in resp.json()["items"]]
    assert versions == [3, 2, 1]


async def test_list_items_have_summary_shape(client, test_workspace):
    """List items expose summary fields only — no `body`, no `diff_from_prior`."""
    s = await post_schema(client, test_workspace, name="Shape")
    resp = await client.get(
        f"/schemas/{s['id']}/versions", headers=headers(test_workspace)
    )
    item = resp.json()["items"][0]
    assert set(item.keys()) == {"version", "kind", "parent_version", "created_at"}


async def test_list_pagination_offset_and_limit(client, test_workspace):
    s = await post_schema(client, test_workspace, name="Paginate")
    for i in range(4):  # PUTs v2..v5
        await put_schema(
            client, test_workspace, s["id"], name="Paginate", description=f"v{i+2}"
        )
    resp = await client.get(
        f"/schemas/{s['id']}/versions?limit=2&offset=1",
        headers=headers(test_workspace),
    )
    body = resp.json()
    assert body["total"] == 5
    assert body["limit"] == 2
    assert body["offset"] == 1
    versions = [item["version"] for item in body["items"]]
    assert versions == [4, 3]  # newest-first, skip v5, take 2


async def test_list_rejects_limit_over_200(client, test_workspace):
    s = await post_schema(client, test_workspace, name="Cap")
    resp = await client.get(
        f"/schemas/{s['id']}/versions?limit=201", headers=headers(test_workspace)
    )
    assert resp.status_code == 400
    assert resp.json()["type"].endswith("/bad-request")


async def test_list_404_for_unknown_schema(client, test_workspace):
    fake = str(uuid.uuid4())
    resp = await client.get(f"/schemas/{fake}/versions", headers=headers(test_workspace))
    assert resp.status_code == 404
    assert resp.json()["type"].endswith("/not-found")


async def test_list_404_for_soft_deleted_schema(client, test_workspace):
    s = await post_schema(client, test_workspace, name="Doomed")
    await client.delete(f"/schemas/{s['id']}", headers=headers(test_workspace))
    resp = await client.get(
        f"/schemas/{s['id']}/versions", headers=headers(test_workspace)
    )
    assert resp.status_code == 404


async def test_list_isolated_across_workspaces(client, test_workspace):
    """RLS: GET versions as workspace B for A's schema → 404 (NOT 403)."""
    workspace_b = str(uuid.uuid4())
    s = await post_schema(client, test_workspace, name="OnlyMine")
    resp = await client.get(
        f"/schemas/{s['id']}/versions", headers=headers(workspace_b)
    )
    assert resp.status_code == 404


# ===========================================================================
# §3.8 — GET /schemas/:id/versions/:v (read one, with diff)
# ===========================================================================


async def test_read_v1_has_null_diff_from_prior(client, test_workspace):
    s = await post_schema(client, test_workspace, name="Solo", description="initial")
    resp = await client.get(
        f"/schemas/{s['id']}/versions/1", headers=headers(test_workspace)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == 1
    assert body["parent_version"] is None
    assert body["diff_from_prior"] is None


async def test_read_v1_body_matches_post_body(client, test_workspace):
    s = await post_schema(client, test_workspace, name="Snap", description="initial")
    resp = await client.get(
        f"/schemas/{s['id']}/versions/1", headers=headers(test_workspace)
    )
    body = resp.json()["body"]
    assert body == {"name": "Snap", "description": "initial"}


async def test_read_v2_diff_reflects_changed_description(client, test_workspace):
    s = await post_schema(client, test_workspace, name="Diff", description="old")
    await put_schema(client, test_workspace, s["id"], name="Diff", description="new")
    resp = await client.get(
        f"/schemas/{s['id']}/versions/2", headers=headers(test_workspace)
    )
    diff = resp.json()["diff_from_prior"]
    assert diff == {
        "added": [],
        "removed": [],
        "changed": [{"path": "description", "old": "old", "new": "new"}],
    }


async def test_read_v2_diff_reflects_changed_name(client, test_workspace):
    s = await post_schema(client, test_workspace, name="OldName", description="x")
    await put_schema(client, test_workspace, s["id"], name="NewName", description="x")
    resp = await client.get(
        f"/schemas/{s['id']}/versions/2", headers=headers(test_workspace)
    )
    diff = resp.json()["diff_from_prior"]
    assert diff == {
        "added": [],
        "removed": [],
        "changed": [{"path": "name", "old": "OldName", "new": "NewName"}],
    }


async def test_read_v2_kind_is_put(client, test_workspace):
    s = await post_schema(client, test_workspace, name="Kind")
    await put_schema(client, test_workspace, s["id"], name="Kind", description="bump")
    resp = await client.get(
        f"/schemas/{s['id']}/versions/2", headers=headers(test_workspace)
    )
    assert resp.json()["kind"] == "put"


async def test_read_404_for_unknown_schema(client, test_workspace):
    fake = str(uuid.uuid4())
    resp = await client.get(
        f"/schemas/{fake}/versions/1", headers=headers(test_workspace)
    )
    assert resp.status_code == 404


async def test_read_404_for_unknown_version(client, test_workspace):
    s = await post_schema(client, test_workspace, name="ExistsV1")
    resp = await client.get(
        f"/schemas/{s['id']}/versions/999", headers=headers(test_workspace)
    )
    assert resp.status_code == 404


async def test_read_422_for_non_positive_int_version(client, test_workspace):
    """`v` must be a positive integer (≥1) per §3.8."""
    s = await post_schema(client, test_workspace, name="Bounds")
    resp = await client.get(
        f"/schemas/{s['id']}/versions/0", headers=headers(test_workspace)
    )
    assert resp.status_code == 422


async def test_read_isolated_across_workspaces(client, test_workspace):
    workspace_b = str(uuid.uuid4())
    s = await post_schema(client, test_workspace, name="Yours")
    resp = await client.get(
        f"/schemas/{s['id']}/versions/1", headers=headers(workspace_b)
    )
    assert resp.status_code == 404


# ===========================================================================
# §3.9 — POST /schemas/:id/versions/:v/rollback
# ===========================================================================


async def test_rollback_creates_new_version_with_target_body(client, test_workspace):
    """Rollback to v1 from v2 produces v3 with v1's body (clone-forward)."""
    s = await post_schema(client, test_workspace, name="Roll", description="original")
    await put_schema(client, test_workspace, s["id"], name="Roll", description="changed")
    status, _ = await rollback(client, test_workspace, s["id"], 1)
    assert status == 200
    # v3 exists; its body equals v1's
    resp = await client.get(
        f"/schemas/{s['id']}/versions/3", headers=headers(test_workspace)
    )
    assert resp.json()["body"] == {"name": "Roll", "description": "original"}


async def test_rollback_response_bumps_current_version(client, test_workspace):
    s = await post_schema(client, test_workspace, name="Bump", description="original")
    await put_schema(client, test_workspace, s["id"], name="Bump", description="other")
    status, body = await rollback(client, test_workspace, s["id"], 1)
    assert status == 200
    assert body["current_version"] == 3
    assert body["description"] == "original"  # v1's value restored


async def test_rollback_kind_is_rollback(client, test_workspace):
    s = await post_schema(client, test_workspace, name="Kind")
    await put_schema(client, test_workspace, s["id"], name="Kind", description="v2")
    await rollback(client, test_workspace, s["id"], 1)
    resp = await client.get(
        f"/schemas/{s['id']}/versions/3", headers=headers(test_workspace)
    )
    v3 = resp.json()
    assert v3["kind"] == "rollback"
    assert v3["parent_version"] == 2


async def test_rollback_409_when_target_is_current(client, test_workspace):
    """Decision #13: rollback to v == current_version → 409 rollback-noop."""
    s = await post_schema(client, test_workspace, name="Noop")
    status, body = await rollback(client, test_workspace, s["id"], 1)
    assert status == 409
    assert body["type"].endswith("/rollback-noop")


async def test_rollback_404_for_unknown_target_version(client, test_workspace):
    s = await post_schema(client, test_workspace, name="Miss")
    status, _ = await rollback(client, test_workspace, s["id"], 999)
    assert status == 404


async def test_rollback_requires_idempotency_key(client, test_workspace):
    s = await post_schema(client, test_workspace, name="NoKey")
    await put_schema(client, test_workspace, s["id"], name="NoKey", description="v2")
    # No Idempotency-Key header.
    resp = await client.post(
        f"/schemas/{s['id']}/versions/1/rollback",
        json={},
        headers={"X-Test-Workspace": test_workspace},
    )
    assert resp.status_code == 400
    assert resp.json()["type"].endswith("/missing-idempotency-key")


async def test_rollback_isolated_across_workspaces(client, test_workspace):
    workspace_b = str(uuid.uuid4())
    s = await post_schema(client, test_workspace, name="Iso")
    await put_schema(client, test_workspace, s["id"], name="Iso", description="v2")
    status, _ = await rollback(client, workspace_b, s["id"], 1)
    assert status == 404


# ===========================================================================
# §3.4 — concurrent PUT serialization (invariant #4 + decision #12)
# ===========================================================================


async def test_concurrent_puts_allocate_contiguous_version_numbers(
    client, test_workspace
):
    """5 PUTs fired concurrently → 6 contiguous versions, no UNIQUE-violation 500s.

    Asserts decision #12: `SELECT ... FOR UPDATE` on the parent serializes
    `version_number` allocation, so concurrent writers don't race the
    `(schema_id, version_number)` UNIQUE constraint.
    """
    s = await post_schema(client, test_workspace, name="Race", description="v1")
    schema_id = s["id"]

    async def one_put(i: int) -> int:
        body = await put_schema(
            client, test_workspace, schema_id, name="Race", description=f"v{i+2}"
        )
        return body["current_version"]

    results = await asyncio.gather(*(one_put(i) for i in range(5)))

    # The allocation order is non-deterministic, but the SET of returned
    # current_versions must be exactly {2,3,4,5,6} — i.e., contiguous,
    # no duplicates, no skips.
    assert sorted(results) == [2, 3, 4, 5, 6]

    # And the version list must have exactly 6 rows (v1..v6).
    resp = await client.get(
        f"/schemas/{schema_id}/versions?limit=100", headers=headers(test_workspace)
    )
    assert resp.json()["total"] == 6
    versions = sorted(item["version"] for item in resp.json()["items"])
    assert versions == [1, 2, 3, 4, 5, 6]
