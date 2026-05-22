"""Phase 0 — middleware tests (api_contracts §0.8 + build_tracker §5.1 decision #6).

RED at G3: imports point to middleware that lands at G4.

Covers:
- X-Request-Id middleware (generation, propagation, uniqueness).
- Workspace context middleware (SET LOCAL app.workspace_id per request).
- Structured logging binds request_id + workspace_id.

Spec: tests/specs/phase_0.md §4.5.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest


pytestmark = pytest.mark.asyncio


async def test_response_has_x_request_id_header(client):
    """Every response (including 200) sets X-Request-Id."""
    resp = await client.get("/health")
    assert "x-request-id" in {k.lower() for k in resp.headers}


async def test_x_request_id_is_uuidv7(client):
    """X-Request-Id value parses as UUIDv7 (version field = 7)."""
    resp = await client.get("/health")
    value = resp.headers["x-request-id"]
    u = uuid.UUID(value)
    assert u.version == 7, f"expected UUIDv7, got version={u.version}"


async def test_x_request_id_is_unique_per_request(client):
    """Two consecutive requests get different X-Request-Id values."""
    r1 = await client.get("/health")
    r2 = await client.get("/health")
    assert r1.headers["x-request-id"] != r2.headers["x-request-id"]


async def test_x_request_id_propagates_from_client_when_provided(client):
    """Client sends X-Request-Id → response echoes the same value (distributed tracing)."""
    rid = str(uuid.uuid4())
    resp = await client.get("/health", headers={"X-Request-Id": rid})
    assert resp.headers["x-request-id"] == rid


async def test_x_request_id_present_on_error_responses(client):
    """X-Request-Id is set even when the endpoint returns 404 / 500."""
    resp = await client.get("/this/path/does/not/exist")
    assert resp.status_code == 404
    assert "x-request-id" in {k.lower() for k in resp.headers}


async def test_workspace_context_set_per_request(client):
    """Endpoint sees app.workspace_id matching the request's resolved workspace."""
    from kb.api.main import build_app  # G4

    # G4 wires a test-only debug endpoint that reads current_setting('app.workspace_id').
    resp = await client.get("/_debug/workspace")
    body = resp.json()
    assert "workspace_id" in body
    # Phase 0 default — no auth resolution yet.
    assert body["workspace_id"] == "default"


async def test_workspace_context_isolated_between_concurrent_requests(client):
    """Concurrent requests with different workspace IDs see only their own."""
    # G4: endpoint accepts X-Test-Workspace header (test-only) and echoes back what it observes.
    async def hit(ws: str) -> str:
        resp = await client.get("/_debug/workspace", headers={"X-Test-Workspace": ws})
        return resp.json()["workspace_id"]

    a, b = await asyncio.gather(
        hit("11111111-1111-1111-1111-111111111111"),
        hit("22222222-2222-2222-2222-222222222222"),
    )
    assert a == "11111111-1111-1111-1111-111111111111"
    assert b == "22222222-2222-2222-2222-222222222222"


async def test_workspace_defaults_to_default_when_unauthenticated(client):
    """Phase 0 has no auth; middleware sets app.workspace_id = 'default'."""
    resp = await client.get("/_debug/workspace")
    assert resp.json()["workspace_id"] == "default"


async def test_structlog_binds_request_id_and_workspace_id(client):
    """Logs emitted during a request include request_id and workspace_id."""
    from kb.logging import capture_structlog  # G4

    with capture_structlog() as records:
        await client.get("/health")

    # At least one record should be the (potentially internal) startup-or-shutdown log,
    # but the request-scoped one we care about includes both fields.
    request_scoped = [r for r in records if r.get("event_context") == "request"]
    assert request_scoped, "no request-scoped log records captured"
    for r in request_scoped:
        assert "request_id" in r
        assert "workspace_id" in r
