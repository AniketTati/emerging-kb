"""WA-2 / Design 6 — vocabulary HTTP + repo tests over testcontainers."""

from __future__ import annotations

import uuid

import psycopg
import pytest


pytestmark = pytest.mark.asyncio


@pytest.fixture
def test_workspace() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def test_domain() -> str:
    # Domain IDs don't have to be UUIDs (the column is text). Use a
    # unique-ish string so tests don't trample each other.
    return f"test_domain_{uuid.uuid4().hex[:8]}"


def headers(workspace: str) -> dict[str, str]:
    return {"X-Test-Workspace": workspace}


# ===========================================================================
# Migration shape
# ===========================================================================


async def test_domain_vocabulary_table_exists_with_grants(db_url_superuser):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_name = 'domain_vocabulary'"
        )
        assert (await cur.fetchone())[0] == 1

        cur = await conn.execute(
            "SELECT privilege_type FROM information_schema.role_table_grants "
            "WHERE grantee = 'kb_app' AND table_name = 'domain_vocabulary'"
        )
        privs = {r[0] for r in await cur.fetchall()}
        assert privs == {"SELECT", "INSERT", "UPDATE", "DELETE"}


async def test_synonyms_gin_index_present(db_url_superuser):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename = 'domain_vocabulary' "
            "AND indexname = 'domain_vocabulary_synonyms_gin'"
        )
        assert (await cur.fetchone()) is not None


async def test_embedding_hnsw_partial_index_present(db_url_superuser):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT indexdef FROM pg_indexes "
            "WHERE indexname = 'domain_vocabulary_embedding_hnsw'"
        )
        row = await cur.fetchone()
        assert row is not None
        defn = row[0].lower()
        assert "hnsw" in defn
        assert "halfvec_cosine_ops" in defn
        assert "where" in defn  # partial — only rows with embedding


async def test_unique_index_case_insensitive(db_url_superuser, test_domain):
    """The unique index on (domain, lower(canonical_term)) means
    'Indemnification' and 'indemnification' collide."""
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "INSERT INTO domain_vocabulary (domain_id, canonical_term) "
            "VALUES (%s, 'Indemnification')",
            (test_domain,),
        )
        with pytest.raises(Exception) as ei:
            await conn.execute(
                "INSERT INTO domain_vocabulary (domain_id, canonical_term) "
                "VALUES (%s, 'indemnification')",
                (test_domain,),
            )
        assert "unique" in str(ei.value).lower()


# ===========================================================================
# POST /vocabulary (create + merge)
# ===========================================================================


async def test_post_creates_new_entry(client, test_workspace, test_domain):
    resp = await client.post(
        "/vocabulary",
        headers=headers(test_workspace),
        json={
            "domain_id": test_domain,
            "canonical_term": "indemnification",
            "synonyms": ["hold harmless", "save harmless"],
            "definition": "Promise to cover losses from a defined risk.",
            "source": "user_defined",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["canonical_term"] == "indemnification"
    assert set(body["synonyms"]) == {"hold harmless", "save harmless"}
    assert body["source"] == "user_defined"
    assert body["active"] is True
    assert body["id"]


async def test_post_with_same_term_merges_synonyms(
    client, test_workspace, test_domain,
):
    """Upsert semantics: second POST adds new synonyms to the existing row."""
    first = await client.post(
        "/vocabulary",
        headers=headers(test_workspace),
        json={
            "domain_id": test_domain,
            "canonical_term": "non_compete",
            "synonyms": ["non_competition"],
        },
    )
    first_id = first.json()["id"]

    second = await client.post(
        "/vocabulary",
        headers=headers(test_workspace),
        json={
            "domain_id": test_domain,
            "canonical_term": "non_compete",
            "synonyms": ["restrictive_covenant"],
        },
    )
    assert second.json()["id"] == first_id  # same row, merged
    assert set(second.json()["synonyms"]) == {"non_competition", "restrictive_covenant"}


async def test_post_acronym_then_lookup(
    client, test_workspace, test_domain, db_url_superuser,
):
    """Acronym entry stores expansion + the per-domain expand_acronym
    repo function returns it."""
    resp = await client.post(
        "/vocabulary",
        headers=headers(test_workspace),
        json={
            "domain_id": test_domain,
            "canonical_term": "GST",
            "acronym_of": "Goods and Services Tax",
            "expansion": "Goods and Services Tax",
        },
    )
    assert resp.status_code == 201

    from kb.domain.vocabulary import expand_acronym
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        v = await expand_acronym(
            conn, domain_id=test_domain, short_form="gst",
        )
        assert v == "Goods and Services Tax"


async def test_post_rejects_unknown_source(client, test_workspace, test_domain):
    resp = await client.post(
        "/vocabulary",
        headers=headers(test_workspace),
        json={
            "domain_id": test_domain,
            "canonical_term": "term",
            "source": "made_up",
        },
    )
    assert resp.status_code == 400


# ===========================================================================
# GET /vocabulary
# ===========================================================================


async def test_get_lists_entries_for_domain(client, test_workspace, test_domain):
    for term in ("alpha_term", "beta_term", "gamma_term"):
        await client.post(
            "/vocabulary",
            headers=headers(test_workspace),
            json={"domain_id": test_domain, "canonical_term": term},
        )
    resp = await client.get(
        f"/vocabulary?domain_id={test_domain}",
        headers=headers(test_workspace),
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    terms = sorted(i["canonical_term"] for i in items)
    assert "alpha_term" in terms and "beta_term" in terms and "gamma_term" in terms


async def test_get_by_id(client, test_workspace, test_domain):
    create = await client.post(
        "/vocabulary",
        headers=headers(test_workspace),
        json={"domain_id": test_domain, "canonical_term": "specific_one"},
    )
    vid = create.json()["id"]
    resp = await client.get(
        f"/vocabulary/{vid}", headers=headers(test_workspace),
    )
    assert resp.status_code == 200
    assert resp.json()["canonical_term"] == "specific_one"


async def test_get_unknown_id_returns_404(client, test_workspace):
    fake = str(uuid.uuid4())
    resp = await client.get(
        f"/vocabulary/{fake}", headers=headers(test_workspace),
    )
    assert resp.status_code == 404


# ===========================================================================
# PUT + deactivate + reactivate
# ===========================================================================


async def test_put_updates_definition(client, test_workspace, test_domain):
    create = await client.post(
        "/vocabulary",
        headers=headers(test_workspace),
        json={"domain_id": test_domain, "canonical_term": "term_def"},
    )
    vid = create.json()["id"]
    resp = await client.put(
        f"/vocabulary/{vid}",
        headers=headers(test_workspace),
        json={"definition": "Updated definition"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["definition"] == "Updated definition"


async def test_deactivate_hides_from_active_list(
    client, test_workspace, test_domain,
):
    create = await client.post(
        "/vocabulary",
        headers=headers(test_workspace),
        json={"domain_id": test_domain, "canonical_term": "to_hide"},
    )
    vid = create.json()["id"]

    await client.post(
        f"/vocabulary/{vid}/deactivate", headers=headers(test_workspace),
    )

    # Default GET excludes inactive.
    active = await client.get(
        f"/vocabulary?domain_id={test_domain}",
        headers=headers(test_workspace),
    )
    terms = [i["canonical_term"] for i in active.json()["items"]]
    assert "to_hide" not in terms

    # include_inactive=true returns it.
    all_items = await client.get(
        f"/vocabulary?domain_id={test_domain}&include_inactive=true",
        headers=headers(test_workspace),
    )
    terms = [i["canonical_term"] for i in all_items.json()["items"]]
    assert "to_hide" in terms


async def test_reactivate_restores(client, test_workspace, test_domain):
    create = await client.post(
        "/vocabulary",
        headers=headers(test_workspace),
        json={"domain_id": test_domain, "canonical_term": "toggle_me"},
    )
    vid = create.json()["id"]
    await client.post(f"/vocabulary/{vid}/deactivate", headers=headers(test_workspace))
    resp = await client.post(
        f"/vocabulary/{vid}/reactivate", headers=headers(test_workspace),
    )
    assert resp.status_code == 200
    assert resp.json()["active"] is True


# ===========================================================================
# Repo lookup functions
# ===========================================================================


async def test_resolve_synonyms_for_term(client, test_workspace, test_domain, db_url_superuser):
    await client.post(
        "/vocabulary",
        headers=headers(test_workspace),
        json={
            "domain_id": test_domain,
            "canonical_term": "indemnification",
            "synonyms": ["hold harmless", "save harmless"],
        },
    )
    from kb.domain.vocabulary import resolve_synonyms_for_term
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        out = await resolve_synonyms_for_term(
            conn, domain_id=test_domain, term="indemnification",
        )
        assert set(out) == {"hold harmless", "save harmless"}


async def test_resolve_synonyms_unknown_term_returns_empty(
    db_url_superuser, test_domain,
):
    from kb.domain.vocabulary import resolve_synonyms_for_term
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        out = await resolve_synonyms_for_term(
            conn, domain_id=test_domain, term="nonexistent_term",
        )
        assert out == []


# ===========================================================================
# OpenAPI surface
# ===========================================================================


async def test_openapi_includes_vocabulary_routes(client):
    resp = await client.get("/openapi.json")
    paths = set(resp.json()["paths"].keys())
    assert "/vocabulary" in paths
    assert "/vocabulary/{vocab_id}" in paths
    assert "/vocabulary/{vocab_id}/deactivate" in paths
    assert "/vocabulary/{vocab_id}/reactivate" in paths
