"""Phase 5c — extract_atomic_units_file_impl integration tests."""

from __future__ import annotations

import hashlib
import os
import uuid
from contextlib import contextmanager

import psycopg
import pytest


pytestmark = pytest.mark.asyncio


@contextmanager
def _env(**kwargs):
    prior = {k: os.environ.get(k) for k in kwargs}
    for k, v in kwargs.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in prior.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


async def _seed_xlsx_file_to_units_extracting(
    db_url: str, workspace_id: str,
) -> str:
    file_id = str(uuid.uuid4())
    sha = hashlib.sha256(f"units-xlsx-{workspace_id}".encode()).hexdigest()

    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace_id,))
        await conn.execute(
            "INSERT INTO files (id, workspace_id, name, content_sha, object_key, "
            "mime_type, size_bytes, lifecycle_state, inferred_doc_type) "
            "VALUES (%s, %s, 'vendors.xlsx', %s, %s, "
            "'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', "
            "100, 'units_extracting', 'vendor_spreadsheet')",
            (file_id, workspace_id, sha, f"raw_files/{sha}"),
        )
        # One sheet with 3 vendor rows.
        await conn.execute(
            "INSERT INTO raw_pages (id, file_id, workspace_id, page_number, text, "
            "layout_json, content_sha) "
            "VALUES (%s, %s, %s, 1, %s, %s::jsonb, %s)",
            (
                str(uuid.uuid4()), file_id, workspace_id,
                "# Sheet: Vendors\nname\taddress\tphone\n"
                "ACME\t123 Main\t555-1234\n"
                "XYZ Co\t456 Oak\t555-5678\n"
                "Globex\t789 Pine\t555-9999",
                '{"sheet_name":"Vendors","rows":4,"cols":3}',
                sha,
            ),
        )
        await conn.commit()
    return file_id


async def _seed_pdf_file_to_units_extracting(
    db_url: str, workspace_id: str, *, doc_type: str = "unknown",
) -> str:
    """Generic PDF (no plugin matches when doc_type='unknown') — to test
    the no-plugin-fallback path."""
    file_id = str(uuid.uuid4())
    sha = hashlib.sha256(f"units-pdf-{workspace_id}-{doc_type}".encode()).hexdigest()

    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace_id,))
        await conn.execute(
            "INSERT INTO files (id, workspace_id, name, content_sha, object_key, "
            "mime_type, size_bytes, lifecycle_state, inferred_doc_type) "
            "VALUES (%s, %s, 'doc.pdf', %s, %s, 'application/pdf', 100, "
            "'units_extracting', %s)",
            (file_id, workspace_id, sha, f"raw_files/{sha}", doc_type),
        )
        await conn.execute(
            "INSERT INTO raw_pages (id, file_id, workspace_id, page_number, text, "
            "layout_json, content_sha) "
            "VALUES (%s, %s, %s, 1, 'some doc text', '{}'::jsonb, %s)",
            (str(uuid.uuid4()), file_id, workspace_id, sha),
        )
        await conn.commit()
    return file_id


async def test_extract_atomic_units_xlsx_writes_rows(client, db_url_superuser):
    """End-to-end: xlsx → rows plugin → 3 atomic_units of type 'row' →
    lifecycle advances units_extracting → ready."""
    from kb.workers.tasks import extract_atomic_units_file_impl

    workspace = str(uuid.uuid4())
    file_id = await _seed_xlsx_file_to_units_extracting(db_url_superuser, workspace)

    with _env(KB_DATABASE_URL=db_url_superuser):
        from kb.config import get_settings
        get_settings.cache_clear()
        await extract_atomic_units_file_impl(file_id)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        cur = await conn.execute(
            "SELECT lifecycle_state FROM files WHERE id = %s", (file_id,),
        )
        assert (await cur.fetchone())[0] == "ready"

        cur = await conn.execute(
            "SELECT count(*), unit_type FROM atomic_units "
            "WHERE file_id = %s GROUP BY unit_type", (file_id,),
        )
        rows = await cur.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == 3
        assert rows[0][1] == "row"


async def test_extract_atomic_units_unknown_doctype_advances_with_no_units(
    client, db_url_superuser
):
    """Decision #2: doc-types with no matching plugin produce 0 units but
    still advance lifecycle (don't block ingestion)."""
    from kb.workers.tasks import extract_atomic_units_file_impl

    workspace = str(uuid.uuid4())
    file_id = await _seed_pdf_file_to_units_extracting(
        db_url_superuser, workspace, doc_type="handwritten_note",
    )

    with _env(KB_DATABASE_URL=db_url_superuser):
        from kb.config import get_settings
        get_settings.cache_clear()
        await extract_atomic_units_file_impl(file_id)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        cur = await conn.execute(
            "SELECT lifecycle_state FROM files WHERE id = %s", (file_id,),
        )
        assert (await cur.fetchone())[0] == "ready"

        cur = await conn.execute(
            "SELECT count(*) FROM atomic_units WHERE file_id = %s", (file_id,),
        )
        assert (await cur.fetchone())[0] == 0


async def test_extract_atomic_units_skips_non_units_extracting_state(
    client, db_url_superuser
):
    """Decision #8 idempotency: already 'ready' → no-op."""
    from kb.workers.tasks import extract_atomic_units_file_impl

    workspace = str(uuid.uuid4())
    file_id = str(uuid.uuid4())
    sha = hashlib.sha256(f"already-ready-{workspace}".encode()).hexdigest()

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        await conn.execute(
            "INSERT INTO files (id, workspace_id, name, content_sha, object_key, "
            "mime_type, size_bytes, lifecycle_state) "
            "VALUES (%s, %s, 'r.pdf', %s, %s, 'application/pdf', 100, 'ready')",
            (file_id, workspace, sha, f"raw_files/{sha}"),
        )
        await conn.commit()

    with _env(KB_DATABASE_URL=db_url_superuser):
        from kb.config import get_settings
        get_settings.cache_clear()
        await extract_atomic_units_file_impl(file_id)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        cur = await conn.execute(
            "SELECT count(*) FROM atomic_units WHERE file_id = %s", (file_id,),
        )
        assert (await cur.fetchone())[0] == 0  # no rows touched


async def test_extract_atomic_units_re_run_is_idempotent_via_delete_then_insert(
    client, db_url_superuser
):
    """Decision #8: re-running deletes existing + reinserts. Count stays
    stable across re-runs."""
    from kb.workers.tasks import extract_atomic_units_file_impl

    workspace = str(uuid.uuid4())
    file_id = await _seed_xlsx_file_to_units_extracting(db_url_superuser, workspace)

    with _env(KB_DATABASE_URL=db_url_superuser):
        from kb.config import get_settings
        get_settings.cache_clear()
        await extract_atomic_units_file_impl(file_id)

        # Reset to units_extracting so we can call again.
        async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
            await conn.execute(
                "UPDATE files SET lifecycle_state = 'units_extracting' WHERE id = %s",
                (file_id,),
            )
            await conn.commit()

        await extract_atomic_units_file_impl(file_id)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        cur = await conn.execute(
            "SELECT count(*) FROM atomic_units WHERE file_id = %s", (file_id,),
        )
        # 3 vendor rows × 1 run = 3 (stable, not 6)
        assert (await cur.fetchone())[0] == 3
