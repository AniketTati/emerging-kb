"""Phase 0 test fixtures — RED at G3 (modules land at G4).

Fixture strategy: testcontainers spins up a fresh Postgres (ParadeDB image)
and MinIO per test session. Migrations are applied as superuser in the
session-scoped `db_migrated` fixture. Tests then drop to the `kb_app` role
so RLS actually applies.

See `tests/specs/phase_0.md` §2 (Fixture strategy) for the full design.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

if TYPE_CHECKING:
    from httpx import AsyncClient


# ---------------------------------------------------------------------------
# Session-scoped: containers + migrations
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def postgres_container():
    """ParadeDB Postgres in a testcontainer for the whole session.

    Yields the container; teardown stops + removes it.
    """
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("paradedb/paradedb:latest-pg17") as container:
        yield container


@pytest.fixture(scope="session")
def minio_container():
    """MinIO testcontainer for the whole session."""
    from testcontainers.minio import MinioContainer

    with MinioContainer() as container:
        yield container


def _strip_sqlalchemy_driver(url: str) -> str:
    """Testcontainers returns `postgresql+psycopg2://...` (SQLAlchemy form);
    psycopg3 wants plain `postgresql://...`.
    """
    return url.replace("+psycopg2", "").replace("+psycopg", "")


@pytest.fixture(scope="session")
def db_url_superuser(postgres_container) -> str:
    """Connection URL for the superuser. Used by migrations + admin tests."""
    return _strip_sqlalchemy_driver(postgres_container.get_connection_url())


@pytest.fixture(scope="session")
def db_url_kb_app(postgres_container, _kb_app_password) -> str:
    """Connection URL for the non-superuser `kb_app` role. Default for tests.

    0001 creates the role; the migration runner sets the password from
    KB_APP_PASSWORD (see `_kb_app_password` below). We swap credentials on
    the testcontainer URL.
    """
    from urllib.parse import urlparse, urlunparse

    base = _strip_sqlalchemy_driver(postgres_container.get_connection_url())
    parsed = urlparse(base)
    host = parsed.hostname or "localhost"
    port = parsed.port or 5432
    path = parsed.path or "/test"
    netloc = f"kb_app:{_kb_app_password}@{host}:{port}"
    return urlunparse(parsed._replace(netloc=netloc, path=path))


@pytest.fixture(scope="session")
def _kb_app_password() -> str:
    """Set KB_APP_PASSWORD on the env so migrations pick it up; return the value."""
    password = "kb-app-test-password"
    os.environ["KB_APP_PASSWORD"] = password
    return password


@pytest.fixture(scope="session", autouse=True)
def db_migrated(db_url_superuser: str, _kb_app_password: str) -> None:
    """Apply all migrations once per session, before any test runs.

    Depends on `_kb_app_password` so KB_APP_PASSWORD is set in env *before*
    the migration runner's `_set_app_role_password` step reads it. Without
    this dependency, kb_app ships passwordless and every test that connects
    as kb_app fails scram auth.
    """
    from migrations.runner import run_migrations  # G4

    run_migrations(db_url_superuser)


# ---------------------------------------------------------------------------
# Per-test: connection + workspace context
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_session(db_url_kb_app: str):
    """Per-test psycopg async connection as kb_app; rolled back at teardown."""
    from kb.db.pool import open_connection  # G4

    async with open_connection(db_url_kb_app) as conn:
        async with conn.transaction(force_rollback=True):
            yield conn


@pytest_asyncio.fixture
async def db_superuser(db_url_superuser: str):
    """Per-test psycopg async connection as superuser. RLS-bypassing."""
    from kb.db.pool import open_connection  # G4

    async with open_connection(db_url_superuser) as conn:
        async with conn.transaction(force_rollback=True):
            yield conn


# ---------------------------------------------------------------------------
# FastAPI test client
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client(db_url_kb_app, minio_container) -> AsyncIterator["AsyncClient"]:
    """httpx.AsyncClient over the FastAPI app via ASGITransport.

    Sets all KB_* env vars so the lru-cached `get_settings()` + `get_minio_client()`
    factories pick up the actual testcontainer endpoints + credentials.
    Cache is cleared at the top so a previous test's cached values don't leak.
    """
    from httpx import ASGITransport, AsyncClient

    from kb.api.main import build_app  # G4
    from kb.config import get_settings
    from kb.storage import get_minio_client

    cfg = minio_container.get_config()
    os.environ["KB_DB_URL"] = db_url_kb_app
    os.environ["KB_MINIO_ENDPOINT"] = cfg["endpoint"]
    os.environ["KB_MINIO_ACCESS_KEY"] = cfg["access_key"]
    os.environ["KB_MINIO_SECRET_KEY"] = cfg["secret_key"]
    os.environ["KB_MINIO_SECURE"] = "false"

    # Both factories are @lru_cache'd; clear so this build_app reads fresh env.
    get_settings.cache_clear()
    get_minio_client.cache_clear()

    app = build_app()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
