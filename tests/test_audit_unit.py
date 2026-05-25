"""Phase 9 — GET /audit endpoint tests over testcontainers."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import psycopg
import pytest


pytestmark = pytest.mark.asyncio


@pytest.fixture
def test_workspace() -> str:
    return str(uuid.uuid4())


def headers(workspace: str) -> dict[str, str]:
    return {"X-Test-Workspace": workspace}


async def _seed_query_log(
    db_url: str, workspace: str, *, n: int = 1, query_prefix: str = "q",
    answer: str | None = None, refused: bool = False,
) -> list[str]:
    """Insert N rows into query_log under the given workspace and return ids
    in insertion order (oldest first)."""
    ids: list[str] = []
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        for i in range(n):
            # Small sleep to guarantee created_at NOW() is microsecond-distinct
            # across inserts — otherwise UUID4 secondary sort wins arbitrarily.
            if i > 0:
                await asyncio.sleep(0.005)
            qid = str(uuid.uuid4())
            await conn.execute(
                """
                INSERT INTO query_log (
                    id, workspace_id, query, mode, endpoint,
                    rewrites, hit_ids, crag_score,
                    refused, refusal_reason, answer, citations, model_id,
                    latency_ms, idempotency_key, created_at
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s::jsonb, %s::jsonb, %s,
                    %s, %s, %s, %s::jsonb, %s,
                    %s, %s, clock_timestamp()
                )
                """,
                (
                    qid, workspace, f"{query_prefix}-{i}", "H", "chat",
                    json.dumps({"original": f"{query_prefix}-{i}"}),
                    json.dumps([]), 0.7,
                    refused,
                    "no_hits" if refused else None,
                    answer if answer is not None else f"answer-{i}",
                    json.dumps([]),
                    "test-model",
                    42, None,
                ),
            )
            ids.append(qid)
    return ids


# ===========================================================================
# Empty + basic
# ===========================================================================


async def test_audit_returns_empty_list_on_empty_workspace(client, test_workspace):
    resp = await client.get("/audit", headers=headers(test_workspace))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["items"] == []
    assert body["next_cursor"] is None


async def test_audit_returns_recent_queries_newest_first(
    client, test_workspace, db_url_superuser,
):
    ids = await _seed_query_log(db_url_superuser, test_workspace, n=3)
    resp = await client.get("/audit", headers=headers(test_workspace))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 3
    # Newest first → reverse of insertion order.
    assert body["items"][0]["id"] == ids[2]
    assert body["items"][2]["id"] == ids[0]


async def test_audit_response_shape_matches_spec(
    client, test_workspace, db_url_superuser,
):
    await _seed_query_log(db_url_superuser, test_workspace, n=1)
    resp = await client.get("/audit", headers=headers(test_workspace))
    item = resp.json()["items"][0]
    for k in ("id", "created_at", "endpoint", "query", "mode",
              "crag_score", "refused", "refusal_reason", "answer",
              "latency_ms", "model_id"):
        assert k in item, f"missing field: {k}"


# ===========================================================================
# Pagination (decision #5)
# ===========================================================================


async def test_audit_respects_limit_param(
    client, test_workspace, db_url_superuser,
):
    await _seed_query_log(db_url_superuser, test_workspace, n=10)
    resp = await client.get("/audit?limit=3", headers=headers(test_workspace))
    body = resp.json()
    assert len(body["items"]) == 3
    assert body["next_cursor"] is not None


async def test_audit_rejects_oversize_limit(client, test_workspace):
    resp = await client.get("/audit?limit=201", headers=headers(test_workspace))
    # Pydantic le=200 → 422; both 400/422 are acceptable here.
    assert resp.status_code in (400, 422)


async def test_audit_cursor_pagination_walks_full_list(
    client, test_workspace, db_url_superuser,
):
    """Seed 7 rows, page 3 + 3 + 1, assert no overlap and full coverage."""
    ids = await _seed_query_log(db_url_superuser, test_workspace, n=7)
    seen_ids: set[str] = set()
    cursor: str | None = None
    pages = 0
    while True:
        url = f"/audit?limit=3" + (f"&cursor={cursor}" if cursor else "")
        resp = await client.get(url, headers=headers(test_workspace))
        assert resp.status_code == 200
        body = resp.json()
        for item in body["items"]:
            assert item["id"] not in seen_ids, "row returned twice"
            seen_ids.add(item["id"])
        cursor = body["next_cursor"]
        pages += 1
        if cursor is None:
            break
        if pages > 10:
            pytest.fail("pagination didn't terminate")
    assert seen_ids == set(ids)


# ===========================================================================
# Isolation
# ===========================================================================


async def test_audit_workspace_isolation(client, db_url_superuser):
    ws_a = str(uuid.uuid4())
    ws_b = str(uuid.uuid4())
    await _seed_query_log(db_url_superuser, ws_a, n=3, query_prefix="A")

    resp = await client.get("/audit", headers=headers(ws_b))
    assert resp.status_code == 200
    assert resp.json()["items"] == []


# ===========================================================================
# Answer truncation (decision #6)
# ===========================================================================


async def test_audit_answer_truncated_to_500_chars(
    client, test_workspace, db_url_superuser,
):
    long_answer = "x" * 1500
    await _seed_query_log(
        db_url_superuser, test_workspace, n=1, answer=long_answer,
    )
    resp = await client.get("/audit", headers=headers(test_workspace))
    item = resp.json()["items"][0]
    assert len(item["answer"]) == 500


async def test_audit_invalid_cursor_returns_400(client, test_workspace):
    resp = await client.get(
        "/audit?cursor=not-base64-json",
        headers=headers(test_workspace),
    )
    assert resp.status_code == 400


async def test_audit_includes_refusal_envelope_for_refused_chat(
    client, test_workspace, db_url_superuser,
):
    await _seed_query_log(
        db_url_superuser, test_workspace, n=1, refused=True, answer=None,
    )
    item = (await client.get("/audit", headers=headers(test_workspace))).json()["items"][0]
    assert item["refused"] is True
    assert item["refusal_reason"] == "no_hits"
