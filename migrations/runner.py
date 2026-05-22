"""Migration runner: applies migrations/sql/*.sql in lexical order.

Connects as superuser (DDL needs it; superuser also bypasses RLS so policies
don't block table creation). Tracks applied files in `schema_migrations`.
Idempotent — re-running with no new files is a no-op. Each file applies
inside a single transaction; on error the file is rolled back and the
runner exits non-zero.

Usage:
    python -m migrations.runner

Env vars:
    KB_DATABASE_URL — superuser connection string (e.g. postgres://kb:...@db:5432/kb).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import psycopg

logger = logging.getLogger(__name__)

# Module-level so tests can monkeypatch with a tmp_path.
MIGRATIONS_DIR: Path = Path(__file__).parent / "sql"

_BOOTSTRAP_FILE = "0002_schema_migrations.sql"


def run_migrations(database_url: str) -> None:
    """Apply every .sql file under MIGRATIONS_DIR not already recorded.

    Raises whatever psycopg raises on bad SQL; on failure the offending
    file is NOT recorded as applied, and prior successful files remain
    applied (each runs in its own transaction).
    """
    with psycopg.connect(database_url, autocommit=True) as conn:
        _bootstrap_schema_migrations(conn)
        applied = _get_applied(conn)
        for path in _list_migration_files():
            if path.name in applied:
                logger.debug("skip already-applied migration: %s", path.name)
                continue
            _apply_one(conn, path)


def _bootstrap_schema_migrations(conn: psycopg.Connection) -> None:
    """Create schema_migrations if it doesn't exist yet.

    The runner needs schema_migrations to know what's been applied; but
    schema_migrations is itself a migration. So we run 0002's content
    unconditionally here (idempotent via CREATE TABLE IF NOT EXISTS).
    The file then re-applies normally in the main loop, which inserts
    its own row in schema_migrations.
    """
    bootstrap_path = MIGRATIONS_DIR / _BOOTSTRAP_FILE
    if not bootstrap_path.is_file():
        raise RuntimeError(
            f"bootstrap file not found: {bootstrap_path}. "
            f"MIGRATIONS_DIR is {MIGRATIONS_DIR}"
        )
    with conn.transaction():
        conn.execute(bootstrap_path.read_text())


def _get_applied(conn: psycopg.Connection) -> set[str]:
    cur = conn.execute("SELECT id FROM schema_migrations")
    return {row[0] for row in cur.fetchall()}


def _list_migration_files() -> list[Path]:
    return sorted(p for p in MIGRATIONS_DIR.glob("*.sql") if p.is_file())


def _apply_one(conn: psycopg.Connection, path: Path) -> None:
    """Apply a single .sql file inside a transaction; record on success."""
    sql = path.read_text()
    logger.info("applying migration: %s", path.name)
    with conn.transaction():
        conn.execute(sql)
        conn.execute(
            "INSERT INTO schema_migrations (id) VALUES (%s) ON CONFLICT (id) DO NOTHING",
            (path.name,),
        )


def _main() -> int:
    logging.basicConfig(
        level=os.environ.get("KB_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    url = os.environ.get("KB_DATABASE_URL")
    if not url:
        print("KB_DATABASE_URL not set", file=sys.stderr)
        return 1
    try:
        run_migrations(url)
    except Exception as exc:
        logger.exception("migration failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_main())
