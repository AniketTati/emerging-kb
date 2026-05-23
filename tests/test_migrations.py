"""Phase 0 — migration runner tests (build_tracker §5.1 "Migration runner behaviour").

RED at G3: imports point to `migrations.runner` which lands at G4.

Spec: tests/specs/phase_0.md §4.3.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest


pytestmark = pytest.mark.asyncio


async def test_runner_bootstraps_schema_migrations_on_empty_db(db_url_superuser):
    """Empty DB → runner creates schema_migrations table and proceeds."""
    from migrations.runner import run_migrations  # G4
    import psycopg

    # Drop the table first (simulate truly empty DB).
    with psycopg.connect(db_url_superuser, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS schema_migrations CASCADE")

    run_migrations(db_url_superuser)

    with psycopg.connect(db_url_superuser) as conn:
        cur = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = 'schema_migrations'"
        )
        assert cur.fetchone() == (1,)


async def test_runner_applies_all_files_in_lexical_order(db_url_superuser):
    """After fresh apply: schema_migrations has Phase 0's files (first 4) in order.

    Phase 0 fixed-set assertion was made open-ended in Phase 1a — later phases
    append migrations, and Phase 0's invariant is only that ITS migrations are
    present and ordered, not that nothing else exists.
    """
    import psycopg

    with psycopg.connect(db_url_superuser) as conn:
        rows = conn.execute(
            "SELECT id FROM schema_migrations ORDER BY applied_at, id"
        ).fetchall()
    ids = [r[0] for r in rows]
    phase_0_files = [
        "0001_extensions.sql",
        "0002_schema_migrations.sql",
        "0003_audit_log.sql",
        "0004_idempotency_keys.sql",
    ]
    assert ids[:4] == phase_0_files, f"Phase 0 migrations missing or out of order: {ids}"


async def test_runner_is_idempotent_when_rerun(db_url_superuser):
    """Apply twice; second run is a no-op."""
    from migrations.runner import run_migrations  # G4
    import psycopg

    with psycopg.connect(db_url_superuser) as conn:
        before = conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0]

    run_migrations(db_url_superuser)

    with psycopg.connect(db_url_superuser) as conn:
        after = conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0]

    assert before == after, "Re-run inserted new rows; runner is not idempotent"


async def test_runner_rolls_back_on_failed_migration(db_url_superuser, tmp_path, monkeypatch):
    """Bad SQL → runner aborts that file, does NOT record it, exits non-zero."""
    from migrations.runner import run_migrations  # G4
    import psycopg

    bad = tmp_path / "9999_bad.sql"
    bad.write_text("THIS IS NOT VALID SQL;")
    monkeypatch.setattr("migrations.runner.MIGRATIONS_DIR", tmp_path)

    with pytest.raises(Exception):
        run_migrations(db_url_superuser)

    with psycopg.connect(db_url_superuser) as conn:
        cur = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE id = '9999_bad.sql'"
        )
        assert cur.fetchone() is None


async def test_runner_applies_extensions_first(db_url_superuser):
    """After fresh apply: vector and pg_search extensions are installed."""
    import psycopg

    with psycopg.connect(db_url_superuser) as conn:
        rows = conn.execute(
            "SELECT extname FROM pg_extension WHERE extname IN ('vector', 'pg_search')"
        ).fetchall()
    assert {r[0] for r in rows} == {"vector", "pg_search"}


async def test_runner_creates_kb_app_role(db_url_superuser):
    """After fresh apply: kb_app role exists."""
    import psycopg

    with psycopg.connect(db_url_superuser) as conn:
        cur = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = 'kb_app'")
        assert cur.fetchone() == (1,)


async def test_runner_creates_initial_audit_log_partitions(db_url_superuser):
    """audit_log_2026_05 and audit_log_2026_06 partitions exist."""
    import psycopg

    with psycopg.connect(db_url_superuser) as conn:
        rows = conn.execute(
            "SELECT inhrelid::regclass::text FROM pg_inherits "
            "WHERE inhparent = 'audit_log'::regclass"
        ).fetchall()
    partitions = {r[0] for r in rows}
    assert "audit_log_2026_05" in partitions
    assert "audit_log_2026_06" in partitions


async def test_runner_records_filename_and_applied_at(db_url_superuser):
    """Each schema_migrations row has id=filename and applied_at ≈ now()."""
    import psycopg

    with psycopg.connect(db_url_superuser) as conn:
        rows = conn.execute(
            "SELECT id, applied_at FROM schema_migrations"
        ).fetchall()
    for filename, applied_at in rows:
        assert filename.endswith(".sql")
        # Allow generous skew since this runs after session-scoped migration.
        assert abs(datetime.now(UTC) - applied_at) < timedelta(hours=1)


async def test_runner_runs_as_superuser(db_url_superuser):
    """Migrations succeed despite RLS being enabled on workspace-scoped tables."""
    import psycopg

    with psycopg.connect(db_url_superuser) as conn:
        rows = conn.execute(
            "SELECT relname, relrowsecurity FROM pg_class "
            "WHERE relname IN ('audit_log', 'idempotency_keys')"
        ).fetchall()
    by_name = dict(rows)
    assert by_name["audit_log"] is True
    assert by_name["idempotency_keys"] is True
