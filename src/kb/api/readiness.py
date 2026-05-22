"""GET /ready — readiness probe.

api_contracts §1.2. Returns 200 when db + minio + migrations all ok;
503 otherwise. Checks run in parallel (asyncio.gather); per-check
timeouts: db 2s, minio 2s, migrations 1s. Overall response budget: 5s.

Each check is a small async function in a registry — later phases append
(worker queue, embedding API, rerank API) without editing the handler.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from kb.config import get_settings
from kb.db.pool import open_connection
from kb.storage import get_minio_client
import migrations.runner as migration_runner

router = APIRouter()

# Per-check timeouts (seconds). api_contracts §1.2 check table.
_TIMEOUTS = {"db": 2.0, "minio": 2.0, "migrations": 1.0}


async def check_db() -> dict[str, Any]:
    settings = get_settings()
    t0 = time.perf_counter()
    async with open_connection(settings.database_url) as conn:
        await conn.execute("SELECT 1")
    return {"status": "ok", "latency_ms": int((time.perf_counter() - t0) * 1000)}


async def check_minio() -> dict[str, Any]:
    client = get_minio_client()
    t0 = time.perf_counter()
    # `list_buckets` is the cheapest reachable call. Synchronous; run in a thread.
    await asyncio.to_thread(client.list_buckets)
    return {"status": "ok", "latency_ms": int((time.perf_counter() - t0) * 1000)}


async def check_migrations() -> dict[str, Any]:
    """Compare on-disk migration files to the schema_migrations table."""
    settings = get_settings()
    t0 = time.perf_counter()
    on_disk = sorted(p.name for p in migration_runner.MIGRATIONS_DIR.glob("*.sql"))
    async with open_connection(settings.database_url) as conn:
        rows = await conn.fetch("SELECT id FROM schema_migrations")
    applied = {r[0] for r in rows}
    pending = [f for f in on_disk if f not in applied]
    if pending:
        raise RuntimeError(f"pending migration: {pending[0]}")
    return {
        "status": "ok",
        "applied_count": len(applied),
        "latency_ms": int((time.perf_counter() - t0) * 1000),
    }


async def _run_check(name: str, fn) -> dict[str, Any]:
    """Run one check with its per-check timeout; never raise."""
    timeout = _TIMEOUTS.get(name, 2.0)
    try:
        return await asyncio.wait_for(fn(), timeout=timeout)
    except asyncio.TimeoutError:
        return {"status": "fail", "error": f"timeout after {timeout}s"}
    except Exception as exc:  # noqa: BLE001 — we want to capture anything
        return {"status": "fail", "error": str(exc)}


@router.get("/ready", tags=["lifecycle"])
async def ready() -> JSONResponse:
    checks_registry = {
        "db": check_db,
        "minio": check_minio,
        "migrations": check_migrations,
    }

    names = list(checks_registry.keys())
    results = await asyncio.gather(
        *(_run_check(name, checks_registry[name]) for name in names)
    )
    checks = dict(zip(names, results, strict=True))

    all_ok = all(c.get("status") == "ok" for c in checks.values())
    body = {
        "status": "ready" if all_ok else "not_ready",
        "ts": _utc_now_iso(),
        "checks": checks,
    }
    return JSONResponse(content=body, status_code=200 if all_ok else 503)


def _utc_now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
