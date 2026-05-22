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


@pytest.fixture(scope="session")
def db_url_superuser(postgres_container) -> str:
    """Connection URL for the superuser. Used by migrations + admin tests."""
    return postgres_container.get_connection_url()


@pytest.fixture(scope="session")
def db_url_kb_app(postgres_container) -> str:
    """Connection URL for the non-superuser `kb_app` role. Default for tests."""
    base = postgres_container.get_connection_url()
    # G4: rewrite credentials to use kb_app role created by migration 0001.
    raise NotImplementedError("G4: derive kb_app connection URL")


@pytest.fixture(scope="session", autouse=True)
def db_migrated(db_url_superuser: str) -> None:
    """Apply all migrations once per session, before any test runs."""
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
    """httpx.AsyncClient over the FastAPI app via ASGITransport."""
    from httpx import ASGITransport, AsyncClient

    # G4: build_app reads env, wires db pool + minio client + middleware.
    from kb.api.main import build_app  # G4

    os.environ["KB_DB_URL"] = db_url_kb_app
    os.environ["KB_MINIO_ENDPOINT"] = minio_container.get_config()["endpoint"]
    app = build_app()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
