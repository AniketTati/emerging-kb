"""Phase 0 — GET /ready contract tests (api_contracts §1.2).

RED at G3: imports point to modules that land at G4.

Spec: tests/specs/phase_0.md §4.2.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta

import pytest


pytestmark = pytest.mark.asyncio


async def test_ready_returns_200_when_all_deps_ok(client):
    """Fresh stack: every check passes; status code 200; body status=='ready'."""
    resp = await client.get("/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    for name, check in body["checks"].items():
        assert check["status"] == "ok", f"check {name} failed: {check}"
        assert isinstance(check["latency_ms"], int)


async def test_ready_check_set_matches_phase_0_contract(client):
    """Checks keys are exactly {db, minio, migrations} — no more, no less."""
    resp = await client.get("/ready")
    assert set(resp.json()["checks"].keys()) == {"db", "minio", "migrations"}


async def test_ready_ts_is_iso8601_utc_recent(client):
    """ts parses as ISO-8601 UTC and is within 5s of now()."""
    resp = await client.get("/ready")
    ts = datetime.fromisoformat(resp.json()["ts"])
    assert ts.tzinfo is not None
    assert abs(datetime.now(UTC) - ts) < timedelta(seconds=5)


async def test_ready_returns_503_when_db_down(client, monkeypatch):
    """db check fails → /ready returns 503; db reports fail with error string.

    Monkey-patches the check function rather than stopping the container —
    container restarts assign a new host port and break every later test
    in the session.
    """
    from kb.api import readiness

    async def db_unreachable(*_a, **_k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(readiness, "check_db", db_unreachable)
    resp = await client.get("/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["db"]["status"] == "fail"
    assert isinstance(body["checks"]["db"]["error"], str)
    assert "latency_ms" not in body["checks"]["db"]


async def test_ready_returns_503_when_minio_down(client, monkeypatch):
    """minio check fails → /ready returns 503; minio reports fail."""
    from kb.api import readiness

    async def minio_unreachable(*_a, **_k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(readiness, "check_minio", minio_unreachable)
    resp = await client.get("/ready")
    assert resp.status_code == 503
    assert resp.json()["checks"]["minio"]["status"] == "fail"


async def test_ready_returns_503_when_migration_pending(
    client, db_superuser, tmp_path, monkeypatch
):
    """Disk has a migration file that's not yet recorded → /ready returns 503."""
    from migrations.runner import MIGRATIONS_DIR  # G4

    # Drop a fake pending migration into the migrations directory.
    fake = tmp_path / "9999_pending.sql"
    fake.write_text("SELECT 1;")
    monkeypatch.setattr("migrations.runner.MIGRATIONS_DIR", tmp_path)

    resp = await client.get("/ready")
    assert resp.status_code == 503
    err = resp.json()["checks"]["migrations"]["error"]
    assert "9999_pending.sql" in err


async def test_ready_response_uses_json_on_failure(client, monkeypatch):
    """Failure body uses Content-Type: application/json (not problem+json — /ready is a typed probe)."""
    from kb.api import readiness

    async def fail(*_a, **_k):
        raise RuntimeError("simulated")

    monkeypatch.setattr(readiness, "check_db", fail)
    resp = await client.get("/ready")
    assert resp.headers["content-type"].startswith("application/json")


async def test_ready_checks_run_in_parallel(client, monkeypatch):
    """Each check sleeps 0.5s; total response time ≈ 0.5s (parallel), not 1.5s (serial).

    Sleep < tightest per-check timeout (migrations = 1.0s) so the parallelism
    test doesn't accidentally trip a per-check timeout.
    """
    from kb.api import readiness  # G4

    async def slow_check(*args, **kwargs):
        await asyncio.sleep(0.5)
        return {"status": "ok", "latency_ms": 500}

    monkeypatch.setattr(readiness, "check_db", slow_check)
    monkeypatch.setattr(readiness, "check_minio", slow_check)
    monkeypatch.setattr(readiness, "check_migrations", slow_check)

    t0 = time.perf_counter()
    resp = await client.get("/ready")
    elapsed = time.perf_counter() - t0

    assert resp.status_code == 200, f"checks returned: {resp.json()}"
    assert elapsed < 1.0, (
        f"api_contracts §1.2 design note: checks must run in parallel via asyncio.gather; "
        f"elapsed {elapsed:.2f}s implies serial execution"
    )


async def test_ready_overall_budget_is_5s(client, monkeypatch):
    """Slow check exceeds 5s; that check reports timeout; endpoint returns within 5.5s."""
    from kb.api import readiness  # G4

    async def too_slow(*args, **kwargs):
        await asyncio.sleep(10.0)
        return {"status": "ok", "latency_ms": 10000}

    monkeypatch.setattr(readiness, "check_db", too_slow)

    t0 = time.perf_counter()
    resp = await client.get("/ready")
    elapsed = time.perf_counter() - t0

    assert elapsed < 5.5, f"overall budget exceeded: {elapsed:.2f}s"
    assert resp.status_code == 503
    assert "timeout" in resp.json()["checks"]["db"]["error"].lower()


async def test_ready_db_check_times_out_at_2s(client, monkeypatch):
    """api_contracts §1.2 check table: db check timeout = 2s.

    If only the overall 5s budget existed (no per-check timeouts), a 3s-slow
    db check would silently succeed within the overall budget — drift from the
    documented contract. This test prevents that.
    """
    from kb.api import readiness  # G4

    async def three_second_check(*args, **kwargs):
        await asyncio.sleep(3.0)
        return {"status": "ok", "latency_ms": 3000}

    monkeypatch.setattr(readiness, "check_db", three_second_check)

    resp = await client.get("/ready")
    assert resp.status_code == 503
    err = resp.json()["checks"]["db"]["error"].lower()
    assert "timeout" in err, f"per-check 2s timeout not enforced; got error={err!r}"


async def test_ready_minio_check_times_out_at_2s(client, monkeypatch):
    """api_contracts §1.2 check table: minio check timeout = 2s."""
    from kb.api import readiness  # G4

    async def three_second_check(*args, **kwargs):
        await asyncio.sleep(3.0)
        return {"status": "ok", "latency_ms": 3000}

    monkeypatch.setattr(readiness, "check_minio", three_second_check)

    resp = await client.get("/ready")
    assert resp.status_code == 503
    err = resp.json()["checks"]["minio"]["error"].lower()
    assert "timeout" in err


async def test_ready_migrations_check_times_out_at_1s(client, monkeypatch):
    """api_contracts §1.2 check table: migrations check timeout = 1s (tighter than db/minio)."""
    from kb.api import readiness  # G4

    async def slow_migrations_check(*args, **kwargs):
        await asyncio.sleep(1.5)
        return {"status": "ok", "applied_count": 4, "latency_ms": 1500}

    monkeypatch.setattr(readiness, "check_migrations", slow_migrations_check)

    resp = await client.get("/ready")
    assert resp.status_code == 503
    err = resp.json()["checks"]["migrations"]["error"].lower()
    assert "timeout" in err


async def test_ready_does_not_write_access_log(client):
    """Probe endpoints skip access logs (api_contracts §0.8)."""
    from kb.logging import capture_access_logs  # G4

    with capture_access_logs() as logs:
        for _ in range(10):
            await client.get("/ready")
    assert len(logs) == 0


async def test_ready_no_auth_required(client):
    """Phase 0: /ready is unauthenticated."""
    resp = await client.get("/ready")  # no Authorization header
    assert resp.status_code in (200, 503)  # never 401/403
