"""Tests for the Wave-A close-out `POST /eval/run` + GETs.

The worker task (`run_eval_suite`) is monkeypatched at the
`defer_async` boundary so tests don't actually drive 45 LLM calls;
the row's lifecycle is then exercised by direct DB writes that
simulate what the worker would have done.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest


pytestmark = pytest.mark.asyncio


@pytest.fixture
def test_workspace() -> str:
    return str(uuid.uuid4())


def headers(
    workspace: str, *, idempotency_key: str | None = None,
) -> dict[str, str]:
    h = {"X-Test-Workspace": workspace}
    if idempotency_key:
        h["Idempotency-Key"] = idempotency_key
    return h


# ---------------------------------------------------------------------------
# POST /eval/run — happy path, idempotency, in-flight collision
# ---------------------------------------------------------------------------


async def test_post_eval_run_202_with_stubbed_defer(
    client, test_workspace, monkeypatch,
):
    """Happy path — POST returns 202 + run_id; row lands in DB as queued."""

    deferred: list[dict] = []

    class _FakeApp:
        def configure_task(self, name):  # noqa: ARG002
            return self

        async def defer_async(self, **kwargs):
            deferred.append(kwargs)
            return 1  # job id

    # Stub the Procrastinate task — the endpoint imports it inside the
    # try block, so monkeypatch via the kb.workers.tasks module.
    from kb.workers import tasks as _tasks_mod
    original = _tasks_mod.run_eval_suite

    class _FakeTask:
        async def defer_async(self, **kwargs):
            deferred.append(kwargs)
            return 1

    monkeypatch.setattr(_tasks_mod, "run_eval_suite", _FakeTask())
    try:
        resp = await client.post(
            "/eval/run",
            json={"ragas": False, "hhem": False, "concurrency": 2},
            headers=headers(test_workspace),
        )
    finally:
        monkeypatch.setattr(_tasks_mod, "run_eval_suite", original)

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "queued"
    assert body["workspace_id"] == test_workspace
    assert body["enable_ragas"] is False
    assert body["enable_hhem"] is False
    assert body["concurrency"] == 2
    assert len(deferred) == 1
    assert deferred[0]["run_id"] == body["id"]
    assert deferred[0]["workspace_id"] == test_workspace


async def test_post_eval_run_503_when_already_in_flight(
    client, test_workspace, db_url_superuser, monkeypatch,
):
    """Second POST while a prior run is still queued/running → 503."""
    # Seed a queued row directly so we don't need the worker stub.
    existing_id = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)",
            (test_workspace,),
        )
        await conn.execute(
            "INSERT INTO eval_runs (id, workspace_id, status) "
            "VALUES (%s, %s, 'queued')",
            (existing_id, test_workspace),
        )
        await conn.commit()

    resp = await client.post(
        "/eval/run",
        json={},
        headers=headers(test_workspace),
    )
    assert resp.status_code == 503, resp.text
    assert existing_id in resp.text or "in-flight" in resp.text.lower()


async def test_post_eval_run_idempotent_replay(
    client, test_workspace, monkeypatch,
):
    """Same Idempotency-Key returns 200 + the previously inserted row,
    not a new run."""
    key = str(uuid.uuid4())

    class _FakeTask:
        async def defer_async(self, **kwargs):  # noqa: ARG002
            return 1

    from kb.workers import tasks as _tasks_mod
    monkeypatch.setattr(_tasks_mod, "run_eval_suite", _FakeTask())

    first = await client.post(
        "/eval/run", json={},
        headers=headers(test_workspace, idempotency_key=key),
    )
    assert first.status_code == 202
    first_id = first.json()["id"]

    second = await client.post(
        "/eval/run", json={},
        headers=headers(test_workspace, idempotency_key=key),
    )
    assert second.status_code == 200
    assert second.json()["id"] == first_id
    assert second.headers.get("X-Idempotent-Replay") == "true"


# ---------------------------------------------------------------------------
# GET endpoints
# ---------------------------------------------------------------------------


async def test_get_eval_runs_lists_newest_first(
    client, test_workspace, db_url_superuser,
):
    older_id = str(uuid.uuid4())
    newer_id = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)",
            (test_workspace,),
        )
        await conn.execute(
            "INSERT INTO eval_runs (id, workspace_id, status, "
            "  started_at) VALUES "
            "  (%s, %s, 'succeeded', NOW() - interval '1 hour'), "
            "  (%s, %s, 'succeeded', NOW())",
            (older_id, test_workspace, newer_id, test_workspace),
        )
        await conn.commit()

    resp = await client.get(
        "/eval/runs?limit=10", headers=headers(test_workspace),
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    ids = [it["id"] for it in items]
    assert ids[0] == newer_id  # newest first
    assert older_id in ids


async def test_get_eval_run_404_when_missing(
    client, test_workspace,
):
    resp = await client.get(
        f"/eval/runs/{uuid.uuid4()}", headers=headers(test_workspace),
    )
    assert resp.status_code == 404


async def test_get_eval_run_returns_summary_for_succeeded(
    client, test_workspace, db_url_superuser,
):
    """Worker would set status='succeeded' + summary jsonb; GET returns
    the summary blob inline."""
    run_id = str(uuid.uuid4())
    summary = {
        "total": 45, "overall_lexical_avg": 0.82,
        "ragas_faithfulness_avg": 0.91, "hhem_pass_rate": 0.88,
    }
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)",
            (test_workspace,),
        )
        await conn.execute(
            "INSERT INTO eval_runs (id, workspace_id, status, "
            "  enable_ragas, enable_hhem, summary, finished_at) "
            "VALUES (%s, %s, 'succeeded', true, true, %s::jsonb, NOW())",
            (run_id, test_workspace,
             __import__("json").dumps(summary)),
        )
        await conn.commit()

    resp = await client.get(
        f"/eval/runs/{run_id}", headers=headers(test_workspace),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "succeeded"
    assert body["enable_ragas"] is True
    assert body["enable_hhem"] is True
    assert body["summary"]["total"] == 45
    assert body["summary"]["ragas_faithfulness_avg"] == 0.91


async def test_get_eval_run_results_paginated(
    client, test_workspace, db_url_superuser,
):
    """Per-question payloads paginated. We seed 3 rows and ask for
    limit=2 → returns 2 items + total=3."""
    run_id = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)",
            (test_workspace,),
        )
        await conn.execute(
            "INSERT INTO eval_runs (id, workspace_id, status) "
            "VALUES (%s, %s, 'succeeded')",
            (run_id, test_workspace),
        )
        for i in range(3):
            await conn.execute(
                "INSERT INTO eval_run_results "
                "(run_id, workspace_id, question_id, payload) "
                "VALUES (%s, %s, %s, %s::jsonb)",
                (run_id, test_workspace, f"q{i}",
                 __import__("json").dumps({"i": i})),
            )
        await conn.commit()

    resp = await client.get(
        f"/eval/runs/{run_id}/results?limit=2",
        headers=headers(test_workspace),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2
    assert body["items"][0]["question_id"] == "q0"
    assert body["items"][0]["payload"] == {"i": 0}
