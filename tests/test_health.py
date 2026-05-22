"""Phase 0 — GET /health contract tests (api_contracts §1.1).

RED at G3: imports point to modules that land at G4. Each test sketches the
assertion that will hold once G4 implements `kb.api.main.build_app()`.

Spec: tests/specs/phase_0.md §4.1.
"""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime, timedelta

import pytest


pytestmark = pytest.mark.asyncio


async def test_health_returns_200_with_documented_shape(client):
    """GET /health returns 200 with exactly the documented keys."""
    resp = await client.get("/health")
    assert resp.status_code == 200, "api_contracts §1.1: /health must return 200"
    body = resp.json()
    assert set(body.keys()) == {"status", "service", "version", "ts"}, (
        "api_contracts §1.1: body keys must be exactly {status, service, version, ts}"
    )


async def test_health_status_field_is_ok(client):
    """status field is the literal string 'ok'."""
    resp = await client.get("/health")
    assert resp.json()["status"] == "ok"


async def test_health_service_field_is_kb_api(client):
    """service field identifies the FastAPI process as 'kb-api'."""
    resp = await client.get("/health")
    assert resp.json()["service"] == "kb-api"


async def test_health_version_matches_pyproject(client):
    """version field equals the value read from pyproject.toml at startup."""
    from kb import __version__  # G4

    resp = await client.get("/health")
    assert resp.json()["version"] == __version__


async def test_health_ts_is_iso8601_utc_recent(client):
    """ts parses as ISO-8601 UTC and is within 5s of now()."""
    resp = await client.get("/health")
    ts = datetime.fromisoformat(resp.json()["ts"])
    assert ts.tzinfo is not None, "ts must include timezone (Z suffix)"
    assert abs(datetime.now(UTC) - ts) < timedelta(seconds=5)


async def test_health_does_not_depend_on_db(client, postgres_container):
    """Liveness ≠ readiness: pausing the DB must not break /health."""
    postgres_container.stop()
    try:
        resp = await client.get("/health")
        assert resp.status_code == 200, (
            "api_contracts §1.1 design note: /health must not depend on dependencies"
        )
    finally:
        postgres_container.start()


async def test_health_does_not_write_access_log(client, capsys):
    """Probe endpoints skip access logs (api_contracts §0.8)."""
    from kb.logging import capture_access_logs  # G4

    with capture_access_logs() as logs:
        for _ in range(10):
            await client.get("/health")
    assert len(logs) == 0, "api_contracts §0.8: probes must skip access logs"


async def test_health_responds_under_100ms_p99(client):
    """Loose p99 latency budget; tightens later."""
    latencies = []
    for _ in range(100):
        t0 = time.perf_counter()
        resp = await client.get("/health")
        assert resp.status_code == 200
        latencies.append(time.perf_counter() - t0)
    latencies.sort()
    p99 = latencies[98]
    assert p99 < 0.1, f"api_contracts §1.1 design note: p99 < 100ms; got {p99 * 1000:.1f}ms"
