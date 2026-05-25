"""Phase 6 — extract_schema_entities_file_impl integration tests."""

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


async def _seed_file_at_entities_extracting(
    db_url: str,
    workspace_id: str,
    *,
    inferred_doc_type: str,
    label: str = "f",
) -> tuple[str, list[str]]:
    """Seed a file in `entities_extracting` state with 2 contextual_chunks.
    Returns (file_id, [contextual_chunk_id, ...])."""
    file_id = str(uuid.uuid4())
    sha = hashlib.sha256(f"e-{workspace_id}-{label}".encode()).hexdigest()
    cc_ids: list[str] = []

    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace_id,))
        await conn.execute(
            "INSERT INTO files (id, workspace_id, name, content_sha, object_key, "
            "mime_type, size_bytes, lifecycle_state, inferred_doc_type) "
            "VALUES (%s, %s, %s, %s, %s, 'application/pdf', 100, "
            "'entities_extracting', %s)",
            (file_id, workspace_id, f"e-{label}.pdf", sha, f"raw_files/{sha}",
             inferred_doc_type),
        )
        await conn.execute(
            "INSERT INTO raw_pages (id, file_id, workspace_id, page_number, text, "
            "layout_json, content_sha) "
            "VALUES (%s, %s, %s, 1, 'page text', '{}'::jsonb, %s)",
            (str(uuid.uuid4()), file_id, workspace_id, sha),
        )
        for i in range(2):
            chunk_id = str(uuid.uuid4())
            chunk_sha = hashlib.sha256(f"c-{workspace_id}-{label}-{i}".encode()).hexdigest()
            await conn.execute(
                "INSERT INTO chunks (id, file_id, workspace_id, chunk_index, text, "
                "source_page_numbers, token_count, content_sha) "
                "VALUES (%s, %s, %s, %s, %s, %s, 5, %s)",
                (chunk_id, file_id, workspace_id, i, f"chunk{i}", [1], chunk_sha),
            )
            cc_id = str(uuid.uuid4())
            await conn.execute(
                "INSERT INTO contextual_chunks (id, chunk_id, file_id, workspace_id, "
                "contextual_prefix, contextual_text, model_id, prefix_token_count, "
                "cache_creation_input_tokens, cache_read_input_tokens) "
                "VALUES (%s, %s, %s, %s, '', %s, 'identity', 0, 0, 0)",
                (cc_id, chunk_id, file_id, workspace_id,
                 f"chunk{i}: ACME Corp filed in 2024 for $1250"),
            )
            cc_ids.append(cc_id)
        await conn.commit()
    return file_id, cc_ids


async def _seed_active_schema(
    db_url: str,
    workspace_id: str,
    *,
    doc_type: str,
    fields: list[tuple[str, str, str]],  # [(name, type, description)]
) -> tuple[str, str]:
    """Create an active schema `auto:<doc_type>` with one entity (`Doc`) and
    the given fields. Returns (schema_id, schema_entity_id)."""
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace_id,))
        schema_id = str(uuid.uuid4())
        await conn.execute(
            "INSERT INTO schemas (id, workspace_id, name, description, lifecycle_state) "
            "VALUES (%s, %s, %s, 'test', 'active')",
            (schema_id, workspace_id, f"auto:{doc_type}"),
        )
        await conn.execute(
            "INSERT INTO schema_versions (schema_id, workspace_id, version_number, body, kind) "
            "VALUES (%s, %s, 1, %s::jsonb, 'post')",
            (schema_id, workspace_id, '{"name": "auto", "entities": [], "relationships": []}'),
        )
        await conn.execute(
            "UPDATE schemas SET current_version_id = ("
            "SELECT id FROM schema_versions WHERE schema_id = %s AND version_number = 1"
            ") WHERE id = %s",
            (schema_id, schema_id),
        )
        entity_id = str(uuid.uuid4())
        await conn.execute(
            "INSERT INTO schema_entities (id, schema_id, workspace_id, name, description, lifecycle_state) "
            "VALUES (%s, %s, %s, 'Doc', 'auto', 'active')",
            (entity_id, schema_id, workspace_id),
        )
        for fname, ftype, fdesc in fields:
            await conn.execute(
                "INSERT INTO schema_fields "
                "(entity_id, workspace_id, name, type, nl_description, lifecycle_state, auto_promoted) "
                "VALUES (%s, %s, %s, %s, %s, 'active', true)",
                (entity_id, workspace_id, fname, ftype, fdesc),
            )
        await conn.commit()
    return schema_id, entity_id


def _fake_entity_extractor_factory(instances_to_return: list[dict]):
    """Make a fake SchemaDrivenExtractor that always returns the given instances.
    Each item: {fields: {...}, citations: {field_name: chunk_index}}."""
    from kb.extraction.entities import (
        ExtractedInstance, SchemaExtractionResult,
    )

    class Fake:
        async def extract(self, *, request):
            return SchemaExtractionResult(
                instances=[ExtractedInstance(**i) for i in instances_to_return],
                model_id="fake-mock",
            )
    return lambda: Fake()


# ===========================================================================
# State machine
# ===========================================================================


async def test_extract_entities_skips_non_entities_extracting(client, db_url_superuser):
    """Decision #10 idempotency: skip if state is past entities_extracting."""
    from kb.workers.tasks import extract_schema_entities_file_impl

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

    with _env(KB_DATABASE_URL=db_url_superuser, KB_ENTITY_EXTRACTOR="identity"):
        from kb.config import get_settings
        get_settings.cache_clear()
        await extract_schema_entities_file_impl(file_id)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM extracted_entities WHERE file_id = %s", (file_id,),
        )
        assert (await cur.fetchone())[0] == 0


async def test_extract_entities_no_matching_schema_advances_to_ready(
    client, db_url_superuser,
):
    """Decision #4: no schema for inferred_doc_type → no-op advance to ready."""
    from kb.workers.tasks import extract_schema_entities_file_impl

    workspace = str(uuid.uuid4())
    file_id, _ = await _seed_file_at_entities_extracting(
        db_url_superuser, workspace, inferred_doc_type="unknown",
    )

    with _env(KB_DATABASE_URL=db_url_superuser, KB_ENTITY_EXTRACTOR="identity"):
        from kb.config import get_settings
        get_settings.cache_clear()
        await extract_schema_entities_file_impl(file_id)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        cur = await conn.execute(
            "SELECT lifecycle_state FROM files WHERE id = %s", (file_id,),
        )
        assert (await cur.fetchone())[0] == "identity_resolving"
        cur = await conn.execute(
            "SELECT count(*) FROM extracted_entities WHERE file_id = %s", (file_id,),
        )
        assert (await cur.fetchone())[0] == 0


async def test_extract_entities_writes_rows_with_citations(
    client, db_url_superuser, monkeypatch,
):
    """End-to-end: file at entities_extracting + matching schema + mock LLM
    returning 2 instances → 2 extracted_entities rows with field values +
    citations resolved to contextual_chunk_id."""
    from kb.workers.tasks import extract_schema_entities_file_impl

    workspace = str(uuid.uuid4())
    file_id, cc_ids = await _seed_file_at_entities_extracting(
        db_url_superuser, workspace, inferred_doc_type="vendor_record",
    )
    schema_id, entity_id = await _seed_active_schema(
        db_url_superuser, workspace,
        doc_type="vendor_record",
        fields=[
            ("vendor_name", "string", "Vendor name"),
            ("amount", "number", "Total amount"),
        ],
    )

    import kb.extraction.entities as entities_mod
    monkeypatch.setattr(entities_mod, "make_schema_driven_extractor",
        _fake_entity_extractor_factory([
            {"fields": {"vendor_name": "ACME", "amount": 1250},
             "citations": {"vendor_name": 0, "amount": 1}},
            {"fields": {"vendor_name": "XYZ", "amount": 500},
             "citations": {"vendor_name": 1}},
        ]))

    with _env(KB_DATABASE_URL=db_url_superuser):
        from kb.config import get_settings
        get_settings.cache_clear()
        await extract_schema_entities_file_impl(file_id)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))

        cur = await conn.execute(
            "SELECT lifecycle_state FROM files WHERE id = %s", (file_id,),
        )
        assert (await cur.fetchone())[0] == "identity_resolving"

        cur = await conn.execute(
            "SELECT fields, citations, schema_entity_id::text FROM extracted_entities "
            "WHERE file_id = %s ORDER BY (fields->>'vendor_name')",
            (file_id,),
        )
        rows = await cur.fetchall()
        assert len(rows) == 2
        # First row (alphabetical by vendor_name): ACME
        acme_fields, acme_citations, acme_se_id = rows[0]
        assert acme_fields["vendor_name"] == "ACME"
        assert acme_fields["amount"] == 1250
        assert acme_citations["vendor_name"] == cc_ids[0]
        assert acme_citations["amount"] == cc_ids[1]
        assert acme_se_id == entity_id

        # lifecycle event recorded
        cur = await conn.execute(
            "SELECT count(*) FROM file_lifecycle "
            "WHERE file_id = %s AND event = 'schema_entities_extracted'",
            (file_id,),
        )
        assert (await cur.fetchone())[0] == 1


async def test_extract_entities_re_run_is_idempotent_via_delete_then_insert(
    client, db_url_superuser, monkeypatch,
):
    """Decision #10: re-running deletes + reinserts; counts stay stable."""
    from kb.workers.tasks import extract_schema_entities_file_impl

    workspace = str(uuid.uuid4())
    file_id, cc_ids = await _seed_file_at_entities_extracting(
        db_url_superuser, workspace, inferred_doc_type="vendor_record",
    )
    await _seed_active_schema(
        db_url_superuser, workspace,
        doc_type="vendor_record",
        fields=[("vendor_name", "string", "")],
    )

    import kb.extraction.entities as entities_mod
    monkeypatch.setattr(entities_mod, "make_schema_driven_extractor",
        _fake_entity_extractor_factory([
            {"fields": {"vendor_name": "ACME"}, "citations": {"vendor_name": 0}},
        ]))

    with _env(KB_DATABASE_URL=db_url_superuser):
        from kb.config import get_settings
        get_settings.cache_clear()
        await extract_schema_entities_file_impl(file_id)

        # Reset state and re-run.
        async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
            await conn.execute(
                "UPDATE files SET lifecycle_state = 'entities_extracting' WHERE id = %s",
                (file_id,),
            )
            await conn.commit()

        await extract_schema_entities_file_impl(file_id)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        cur = await conn.execute(
            "SELECT count(*) FROM extracted_entities WHERE file_id = %s", (file_id,),
        )
        # Still 1 (stable, not 2)
        assert (await cur.fetchone())[0] == 1


async def test_extract_entities_assigns_lineage_for_contains_relationship(
    client, db_url_superuser, monkeypatch,
):
    """Decision #7: when schema_relationships defines 'contains' (Doc → Clause),
    extracted Clauses get parent_entity_id + lineage_path pointing at their Doc."""
    from kb.workers.tasks import extract_schema_entities_file_impl

    workspace = str(uuid.uuid4())
    file_id, _ = await _seed_file_at_entities_extracting(
        db_url_superuser, workspace, inferred_doc_type="legal_contract",
    )

    # Build a schema with Doc + Clause entities + a contains relationship.
    schema_id = str(uuid.uuid4())
    doc_eid = str(uuid.uuid4())
    clause_eid = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        await conn.execute(
            "INSERT INTO schemas (id, workspace_id, name, lifecycle_state) "
            "VALUES (%s, %s, %s, 'active')",
            (schema_id, workspace, "auto:legal_contract"),
        )
        await conn.execute(
            "INSERT INTO schema_versions (schema_id, workspace_id, version_number, body, kind) "
            "VALUES (%s, %s, 1, '{}'::jsonb, 'post')",
            (schema_id, workspace),
        )
        await conn.execute(
            "UPDATE schemas SET current_version_id = ("
            "SELECT id FROM schema_versions WHERE schema_id = %s LIMIT 1"
            ") WHERE id = %s",
            (schema_id, schema_id),
        )
        # Doc entity
        await conn.execute(
            "INSERT INTO schema_entities (id, schema_id, workspace_id, name, lifecycle_state) "
            "VALUES (%s, %s, %s, 'Doc', 'active')",
            (doc_eid, schema_id, workspace),
        )
        await conn.execute(
            "INSERT INTO schema_fields (entity_id, workspace_id, name, type, lifecycle_state, auto_promoted) "
            "VALUES (%s, %s, 'title', 'string', 'active', true)",
            (doc_eid, workspace),
        )
        # Clause entity
        await conn.execute(
            "INSERT INTO schema_entities (id, schema_id, workspace_id, name, lifecycle_state) "
            "VALUES (%s, %s, %s, 'Clause', 'active')",
            (clause_eid, schema_id, workspace),
        )
        await conn.execute(
            "INSERT INTO schema_fields (entity_id, workspace_id, name, type, lifecycle_state, auto_promoted) "
            "VALUES (%s, %s, 'clause_type', 'string', 'active', true)",
            (clause_eid, workspace),
        )
        # contains relationship: Doc → Clause
        await conn.execute(
            "INSERT INTO schema_relationships "
            "(schema_id, workspace_id, name, from_entity_id, to_entity_id, kind, lifecycle_state) "
            "VALUES (%s, %s, 'doc_contains_clauses', %s, %s, 'contains', 'active')",
            (schema_id, workspace, doc_eid, clause_eid),
        )
        await conn.commit()

    # Mock extractor returns 1 Doc + 1 Clause across the 2 schema_entities.
    # The worker calls extract() once per entity, in DB order (Doc first since
    # it was inserted first).
    from kb.extraction.entities import (
        ExtractedInstance, SchemaExtractionResult,
    )
    call_count = {"n": 0}

    class FakeMixedExtractor:
        async def extract(self, *, request):
            call_count["n"] += 1
            if request.schema_entity_name == "Doc":
                return SchemaExtractionResult(
                    instances=[ExtractedInstance(
                        fields={"title": "MSA 2024"},
                        citations={"title": 0},
                    )],
                    model_id="fake-mock",
                )
            elif request.schema_entity_name == "Clause":
                return SchemaExtractionResult(
                    instances=[ExtractedInstance(
                        fields={"clause_type": "payment_terms"},
                        citations={"clause_type": 1},
                    )],
                    model_id="fake-mock",
                )
            return SchemaExtractionResult(instances=[], model_id="fake-mock")

    import kb.extraction.entities as entities_mod
    monkeypatch.setattr(entities_mod, "make_schema_driven_extractor",
        lambda: FakeMixedExtractor())

    with _env(KB_DATABASE_URL=db_url_superuser):
        from kb.config import get_settings
        get_settings.cache_clear()
        await extract_schema_entities_file_impl(file_id)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))

        # Doc has no parent (root)
        cur = await conn.execute(
            "SELECT id::text, parent_entity_id::text, lineage_path::text "
            "FROM extracted_entities WHERE file_id = %s AND schema_entity_id = %s",
            (file_id, doc_eid),
        )
        doc_row = await cur.fetchone()
        doc_id, doc_parent, doc_lineage = doc_row
        assert doc_parent is None
        # Lineage path is single segment (uuid with hyphens → underscores)
        assert doc_lineage == doc_id.replace("-", "_")

        # Clause has parent = Doc
        cur = await conn.execute(
            "SELECT id::text, parent_entity_id::text, lineage_path::text "
            "FROM extracted_entities WHERE file_id = %s AND schema_entity_id = %s",
            (file_id, clause_eid),
        )
        clause_row = await cur.fetchone()
        clause_id, clause_parent, clause_lineage = clause_row
        assert clause_parent == doc_id
        # Clause's lineage = doc_lineage + "." + clause_label
        assert clause_lineage == f"{doc_lineage}.{clause_id.replace('-', '_')}"
