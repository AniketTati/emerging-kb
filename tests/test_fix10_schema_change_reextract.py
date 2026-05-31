"""FIX 10 — a schema change triggers re-extraction (no re-parse).

bump_schema_version (called by every schema CRUD + import) now defers a
reextract_workspace_files job, scoped to the schema's doc_type, coalesced via
a queueing_lock. The defer itself is best-effort (caught when procrastinate is
not connected), and procrastinate defers don't land in procrastinate_jobs in
this local test env (the pre-existing raptor defer test fails the same way),
so this suite verifies the testable contract: the doc_type-scope derivation,
and that the enqueue path runs cleanly inside a real bump without raising.
"""

from __future__ import annotations

import uuid

import pytest

from kb.db.pool import open_connection
from kb.domain.schemas import bump_schema_version, reextract_doc_type_for_schema
from kb.extraction.promotion import ensure_auto_schema_entity


pytestmark = pytest.mark.asyncio


def test_reextract_doc_type_for_schema_derivation():
    # auto:<doc_type> → that doc_type (re-extract scoped to it)
    assert reextract_doc_type_for_schema("auto:loan_agreement") == "loan_agreement"
    assert reextract_doc_type_for_schema("auto:invoice") == "invoice"
    # user-declared (arbitrary name) → None → whole-workspace re-extract
    assert reextract_doc_type_for_schema("Finance Domain") is None
    assert reextract_doc_type_for_schema("auto:") is None
    assert reextract_doc_type_for_schema(None) is None


async def test_bump_schema_version_runs_clean_with_reextract_trigger(db_url_superuser):
    """The enqueue path is exercised inside a real bump and must never fail
    the schema mutation (best-effort), even when procrastinate isn't open."""
    workspace = str(uuid.uuid4())
    async with open_connection(db_url_superuser) as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)", (workspace,),
            )
            schema_id, _doc_root = await ensure_auto_schema_entity(
                conn, workspace_id=workspace, doc_type="loan_agreement",
            )
            # Must return the new version and not raise from the trigger.
            v = await bump_schema_version(conn, workspace, schema_id, kind="put")
            assert isinstance(v, int) and v >= 1
            # A second bump still succeeds (coalescing is best-effort).
            v2 = await bump_schema_version(conn, workspace, schema_id, kind="put")
            assert v2 == v + 1
