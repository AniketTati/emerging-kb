"""WA-1 / Design 9 — Settings HTTP endpoint tests over testcontainers.

Covers `GET /settings/effective-config`, `GET /settings/models`,
`POST /settings/overrides`, `DELETE /settings/overrides`, and the
`config_overrides` migration shape + RLS isolation."""

from __future__ import annotations

import uuid

import psycopg
import pytest


pytestmark = pytest.mark.asyncio


@pytest.fixture
def test_workspace() -> str:
    return str(uuid.uuid4())


def headers(workspace: str) -> dict[str, str]:
    return {"X-Test-Workspace": workspace}


# ===========================================================================
# Migration shape
# ===========================================================================


async def test_config_overrides_table_with_rls_and_grants(db_url_superuser):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname='config_overrides'"
        )
        row = await cur.fetchone()
        assert row is not None
        assert row[0] is True and row[1] is True

        cur = await conn.execute(
            "SELECT privilege_type FROM information_schema.role_table_grants "
            "WHERE grantee='kb_app' AND table_name='config_overrides'"
        )
        privs = {r[0] for r in await cur.fetchall()}
        assert privs == {"SELECT", "INSERT", "UPDATE"}, (
            "Design 9 §Data model — kb_app may read/write/toggle but not "
            "delete (history preserved). Got: " + str(sorted(privs))
        )


async def test_scope_kind_check_constraint_rejects_garbage(db_url_superuser, test_workspace):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        with pytest.raises(Exception) as ei:
            await conn.execute(
                "INSERT INTO config_overrides (workspace_id, scope_kind, scope_id, "
                "config_key, config_value) VALUES (%s, 'bogus', 's', 'k', '\"v\"'::jsonb)",
                (test_workspace,),
            )
        # CHECK violation.
        assert "config_overrides_scope_kind_check" in str(ei.value) or "violates check" in str(ei.value).lower()


# ===========================================================================
# GET /settings/effective-config
# ===========================================================================


async def test_effective_config_returns_default_values(client, test_workspace):
    resp = await client.get(
        "/settings/effective-config",
        headers=headers(test_workspace),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "entries" in body
    by_key = {e["key"]: e for e in body["entries"]}
    # Sample one defaults-layer entry that we know is in config/defaults.yaml.
    rt = by_key.get("extraction.l3.rarity_threshold")
    assert rt is not None
    assert rt["value"] == 0.95
    assert rt["layer"] == "defaults"


async def test_effective_config_with_domain_returns_domain_overrides(client, test_workspace):
    resp = await client.get(
        "/settings/effective-config?domain=mixed_demo",
        headers=headers(test_workspace),
    )
    assert resp.status_code == 200
    by_key = {e["key"]: e for e in resp.json()["entries"]}
    # mixed_demo overrides min_doc_count → 5 (from defaults 20).
    entry = by_key.get("extraction.l2b.auto_promotion.min_doc_count")
    assert entry is not None
    assert entry["value"] == 5
    assert entry["layer"] == "domain"
    assert entry["scope_id"] == "mixed_demo"


# ===========================================================================
# GET /settings/models
# ===========================================================================


async def test_get_models_returns_defaults(client, test_workspace):
    resp = await client.get("/settings/models", headers=headers(test_workspace))
    assert resp.status_code == 200
    body = resp.json()
    # config/defaults.yaml values:
    assert body["extraction_llm"] == "gemini-2.5-flash"
    assert body["reranker"] == "cohere-rerank-3.5"
    assert body["embedder"] == "gemini-embedding-001"


# ===========================================================================
# POST + DELETE /settings/overrides
# ===========================================================================


async def test_post_override_then_resolved_via_effective_config(
    client, test_workspace,
):
    # Set a workspace-scope override on rerank.top_k.
    post_resp = await client.post(
        "/settings/overrides",
        headers=headers(test_workspace),
        json={
            "scope_kind": "workspace",
            "scope_id": test_workspace,
            "config_key": "retrieval.rerank.top_k",
            "config_value": 75,
            "reason": "demo eval needs more recall",
            "set_by": "admin@test",
        },
    )
    assert post_resp.status_code == 201, post_resp.text
    body = post_resp.json()
    assert body["id"] is not None

    # Effective config now reflects layer='workspace' + value 75.
    ec = await client.get(
        "/settings/effective-config",
        headers=headers(test_workspace),
    )
    by_key = {e["key"]: e for e in ec.json()["entries"]}
    entry = by_key.get("retrieval.rerank.top_k")
    assert entry is not None
    assert entry["layer"] == "workspace"
    assert entry["value"] == 75
    assert entry["scope_id"] == test_workspace


async def test_post_override_replaces_existing_active_row(
    client, test_workspace, db_url_superuser,
):
    # First set.
    await client.post(
        "/settings/overrides",
        headers=headers(test_workspace),
        json={
            "scope_kind": "workspace",
            "scope_id": test_workspace,
            "config_key": "retrieval.rerank.top_k",
            "config_value": 75,
        },
    )
    # Second set — should deactivate first, insert second.
    await client.post(
        "/settings/overrides",
        headers=headers(test_workspace),
        json={
            "scope_kind": "workspace",
            "scope_id": test_workspace,
            "config_key": "retrieval.rerank.top_k",
            "config_value": 90,
        },
    )

    # Exactly one ACTIVE row exists; second active row would have collided
    # with the unique partial index.
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM config_overrides "
            "WHERE workspace_id = %s AND active = true",
            (test_workspace,),
        )
        active_count = (await cur.fetchone())[0]
        assert active_count == 1

        cur = await conn.execute(
            "SELECT count(*) FROM config_overrides WHERE workspace_id = %s",
            (test_workspace,),
        )
        total_count = (await cur.fetchone())[0]
        # Both rows preserved (history) — only one is active.
        assert total_count == 2


async def test_delete_override_revokes_and_falls_back_to_default(
    client, test_workspace,
):
    # Set then revoke.
    await client.post(
        "/settings/overrides",
        headers=headers(test_workspace),
        json={
            "scope_kind": "workspace",
            "scope_id": test_workspace,
            "config_key": "retrieval.rerank.top_k",
            "config_value": 75,
        },
    )
    del_resp = await client.request(
        "DELETE",
        "/settings/overrides",
        headers=headers(test_workspace),
        json={
            "scope_kind": "workspace",
            "scope_id": test_workspace,
            "config_key": "retrieval.rerank.top_k",
        },
    )
    assert del_resp.status_code == 200, del_resp.text
    assert del_resp.json()["revoked"] is True

    # Should now resolve to the defaults value (10).
    ec = await client.get(
        "/settings/effective-config",
        headers=headers(test_workspace),
    )
    by_key = {e["key"]: e for e in ec.json()["entries"]}
    entry = by_key.get("retrieval.rerank.top_k")
    assert entry["layer"] == "defaults"
    assert entry["value"] == 10


async def test_post_override_rejects_bad_scope_kind(client, test_workspace):
    resp = await client.post(
        "/settings/overrides",
        headers=headers(test_workspace),
        json={
            "scope_kind": "bogus",
            "scope_id": "x",
            "config_key": "models.extraction_llm",
            "config_value": "claude-opus-4-7",
        },
    )
    # BadRequestError → 400 bad-request via the main.py handler.
    assert resp.status_code == 400


async def test_workspace_isolation_across_overrides(client, db_url_superuser):
    ws_a = str(uuid.uuid4())
    ws_b = str(uuid.uuid4())

    # WS A sets an override.
    await client.post(
        "/settings/overrides",
        headers=headers(ws_a),
        json={
            "scope_kind": "workspace",
            "scope_id": ws_a,
            "config_key": "retrieval.rerank.top_k",
            "config_value": 100,
        },
    )

    # WS B reads effective config — should see DEFAULTS, not A's value.
    resp = await client.get(
        "/settings/effective-config", headers=headers(ws_b),
    )
    entry = next(
        e for e in resp.json()["entries"]
        if e["key"] == "retrieval.rerank.top_k"
    )
    assert entry["layer"] == "defaults"
    assert entry["value"] == 10


# ===========================================================================
# OpenAPI surface
# ===========================================================================


async def test_openapi_includes_settings_routes(client):
    resp = await client.get("/openapi.json")
    paths = set(resp.json()["paths"].keys())
    assert "/settings/effective-config" in paths
    assert "/settings/models" in paths
    assert "/settings/overrides" in paths
