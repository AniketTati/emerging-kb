"""#19 — cold-start schema-entity re-extraction (force mode).

On a new domain the first docs are extracted before the schema converges, so
their structured extracted_entities are thin. The corpus-finalization phase
re-extracts ALL ready docs against the converged schema in *force* mode:
delete-then-insert as usual, but stay `ready` and DON'T re-chain identity
resolution (so it can't un-settle the workspace and re-fire finalize_corpus).
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from tests.test_entities_worker import (
    _env,
    _fake_entity_extractor_factory,
    _seed_active_schema,
    _seed_file_at_entities_extracting,
)


pytestmark = pytest.mark.asyncio


async def _set_ready(db_url: str, workspace: str, file_id: str) -> None:
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        await conn.execute(
            "UPDATE files SET lifecycle_state = 'ready' WHERE id = %s", (file_id,),
        )
        await conn.commit()


async def test_force_reextract_keeps_file_ready(client, db_url_superuser, monkeypatch):
    """Force re-extraction on a READY file writes entities, records a
    'schema_entities_reextracted' audit event, and leaves the file `ready`
    (does NOT transition to identity_resolving)."""
    from kb.config import get_settings
    from kb.workers.tasks import extract_schema_entities_file_impl

    workspace = str(uuid.uuid4())
    file_id, cc_ids = await _seed_file_at_entities_extracting(
        db_url_superuser, workspace, inferred_doc_type="vendor_record",
    )
    await _seed_active_schema(
        db_url_superuser, workspace, doc_type="vendor_record",
        fields=[("vendor_name", "string", "Vendor name"), ("amount", "number", "Total")],
    )
    await _set_ready(db_url_superuser, workspace, file_id)

    import kb.extraction.entities as entities_mod
    monkeypatch.setattr(entities_mod, "make_schema_driven_extractor",
        _fake_entity_extractor_factory([
            {"fields": {"vendor_name": "ACME", "amount": 1250}, "citations": {"vendor_name": 0}},
        ]))

    with _env(KB_DATABASE_URL=db_url_superuser):
        get_settings.cache_clear()
        await extract_schema_entities_file_impl(file_id, force=True)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        cur = await conn.execute("SELECT lifecycle_state FROM files WHERE id = %s", (file_id,))
        assert (await cur.fetchone())[0] == "ready", "force re-extract must keep the file ready"

        cur = await conn.execute(
            "SELECT count(*) FROM extracted_entities WHERE file_id = %s", (file_id,),
        )
        assert (await cur.fetchone())[0] >= 1, "force re-extract should write entities"

        cur = await conn.execute(
            "SELECT count(*) FROM file_lifecycle "
            "WHERE file_id = %s AND event = 'schema_entities_reextracted'",
            (file_id,),
        )
        assert (await cur.fetchone())[0] == 1, "force re-extract should record its audit event"

        # And it must NOT have emitted a normal (non-force) transition event.
        cur = await conn.execute(
            "SELECT count(*) FROM file_lifecycle "
            "WHERE file_id = %s AND event = 'schema_entities_extracted'",
            (file_id,),
        )
        assert (await cur.fetchone())[0] == 0


async def test_non_force_on_ready_is_noop(client, db_url_superuser, monkeypatch):
    """Without force, a ready file is skipped (the per-stage idempotency guard)
    — no re-extract event, proving force is what unlocks the re-run."""
    from kb.config import get_settings
    from kb.workers.tasks import extract_schema_entities_file_impl

    workspace = str(uuid.uuid4())
    file_id, _ = await _seed_file_at_entities_extracting(
        db_url_superuser, workspace, inferred_doc_type="vendor_record",
    )
    await _seed_active_schema(
        db_url_superuser, workspace, doc_type="vendor_record",
        fields=[("vendor_name", "string", "Vendor name")],
    )
    await _set_ready(db_url_superuser, workspace, file_id)

    import kb.extraction.entities as entities_mod
    monkeypatch.setattr(entities_mod, "make_schema_driven_extractor",
        _fake_entity_extractor_factory([{"fields": {"vendor_name": "ACME"}, "citations": {}}]))

    with _env(KB_DATABASE_URL=db_url_superuser):
        get_settings.cache_clear()
        await extract_schema_entities_file_impl(file_id)  # force=False

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        cur = await conn.execute(
            "SELECT count(*) FROM extracted_entities WHERE file_id = %s", (file_id,),
        )
        assert (await cur.fetchone())[0] == 0, "non-force on a ready file must be a no-op"


async def test_reextract_workspace_orchestration(client, db_url_superuser, monkeypatch):
    """The workspace sweep re-extracts every ready, real-doc_type file."""
    from kb.config import get_settings
    from kb.workers.tasks import reextract_workspace_schema_entities_impl

    workspace = str(uuid.uuid4())
    f1, _ = await _seed_file_at_entities_extracting(
        db_url_superuser, workspace, inferred_doc_type="vendor_record", label="a",
    )
    f2, _ = await _seed_file_at_entities_extracting(
        db_url_superuser, workspace, inferred_doc_type="vendor_record", label="b",
    )
    await _seed_active_schema(
        db_url_superuser, workspace, doc_type="vendor_record",
        fields=[("vendor_name", "string", "Vendor name")],
    )
    await _set_ready(db_url_superuser, workspace, f1)
    await _set_ready(db_url_superuser, workspace, f2)

    import kb.extraction.entities as entities_mod
    monkeypatch.setattr(entities_mod, "make_schema_driven_extractor",
        _fake_entity_extractor_factory([{"fields": {"vendor_name": "ACME"}, "citations": {}}]))

    with _env(KB_DATABASE_URL=db_url_superuser):
        get_settings.cache_clear()
        summary = await reextract_workspace_schema_entities_impl(workspace_id=workspace)

    assert summary == {"files": 2, "reextracted": 2}, summary

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        cur = await conn.execute(
            "SELECT count(*) FROM files WHERE workspace_id = %s AND lifecycle_state = 'ready'",
            (workspace,),
        )
        assert (await cur.fetchone())[0] == 2, "all files stay ready after the sweep"


async def test_force_reextract_empty_result_preserves_existing_parents(
    client, db_url_superuser, monkeypatch,
):
    """#19 safety: a degraded/empty force re-extraction (e.g. identity extractor
    on a transient Gemini miss) must NOT delete the doc's existing doc_root
    parent entities — otherwise the doc drops to 0 entities (observed live on
    complaint-005 / wire-005)."""
    from kb.config import get_settings
    from kb.workers.tasks import extract_schema_entities_file_impl

    workspace = str(uuid.uuid4())
    file_id, _ = await _seed_file_at_entities_extracting(
        db_url_superuser, workspace, inferred_doc_type="vendor_record",
    )
    await _seed_active_schema(
        db_url_superuser, workspace, doc_type="vendor_record",
        fields=[("vendor_name", "string", "Vendor name")],
    )

    import kb.extraction.entities as entities_mod
    # 1) Normal extraction creates one doc_root parent.
    monkeypatch.setattr(entities_mod, "make_schema_driven_extractor",
        _fake_entity_extractor_factory([{"fields": {"vendor_name": "ACME"}, "citations": {}}]))
    with _env(KB_DATABASE_URL=db_url_superuser):
        get_settings.cache_clear()
        await extract_schema_entities_file_impl(file_id)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        cur = await conn.execute("SELECT count(*) FROM extracted_entities WHERE file_id = %s", (file_id,))
        before = (await cur.fetchone())[0]
    assert before >= 1
    await _set_ready(db_url_superuser, workspace, file_id)

    # 2) Force re-extraction that yields NOTHING (empty extractor) must preserve.
    monkeypatch.setattr(entities_mod, "make_schema_driven_extractor",
        _fake_entity_extractor_factory([]))
    with _env(KB_DATABASE_URL=db_url_superuser):
        get_settings.cache_clear()
        await extract_schema_entities_file_impl(file_id, force=True)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        cur = await conn.execute("SELECT count(*) FROM extracted_entities WHERE file_id = %s", (file_id,))
        after = (await cur.fetchone())[0]
    assert after == before, f"empty re-extract must NOT wipe parents (before={before} after={after})"
