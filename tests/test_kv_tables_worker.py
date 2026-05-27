"""Phase 5d — extract_kv_tables_file_impl integration tests.

Covers the KV+Tables collapse worker task: scalars promotion through
proposed_fields → inferred_schema_fields → schema_fields, and tables
landing as atomic_units with rarity_score + source positions, with
lifecycle jumping fields_extracting → entities_extracting.
"""

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


async def _seed_file_at_fields_extracting(
    db_url: str, workspace_id: str, *, label: str = "f",
) -> tuple[str, list[str], list[str]]:
    """Seed a file at `fields_extracting` with 2 chunks + their contextual
    counterparts. Returns (file_id, [chunks.id, ...], [contextual_chunks.id, ...])
    in chunk_index order.

    After the atomic_units drop, KV+Tables writes:
      - proposed_fields.source_chunk_id → chunks.id (chunk_ids)
      - extracted_entities.source_chunk_id → contextual_chunks.id (cc_ids)
    Tests can assert against whichever FK target the row points at.
    """
    file_id = str(uuid.uuid4())
    sha = hashlib.sha256(f"kvt-{workspace_id}-{label}".encode()).hexdigest()
    chunk_ids: list[str] = []
    cc_ids: list[str] = []

    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (workspace_id,),
        )
        await conn.execute(
            "INSERT INTO files (id, workspace_id, name, content_sha, "
            "object_key, mime_type, size_bytes, lifecycle_state) "
            "VALUES (%s, %s, %s, %s, %s, 'application/pdf', 100, "
            "'fields_extracting')",
            (file_id, workspace_id, f"kvt-{label}.pdf", sha,
             f"raw_files/{sha}"),
        )
        await conn.execute(
            "INSERT INTO raw_pages (id, file_id, workspace_id, page_number, "
            "text, layout_json, content_sha) "
            "VALUES (%s, %s, %s, 1, 'page text', '{}'::jsonb, %s)",
            (str(uuid.uuid4()), file_id, workspace_id, sha),
        )
        for i in range(2):
            chunk_id = str(uuid.uuid4())
            chunk_sha = hashlib.sha256(
                f"kvt-c-{workspace_id}-{label}-{i}".encode()
            ).hexdigest()
            await conn.execute(
                "INSERT INTO chunks (id, file_id, workspace_id, chunk_index, "
                "text, source_page_numbers, token_count, content_sha) "
                "VALUES (%s, %s, %s, %s, %s, %s, 5, %s)",
                (chunk_id, file_id, workspace_id, i, f"chunk{i} text",
                 [1], chunk_sha),
            )
            cc_id = str(uuid.uuid4())
            await conn.execute(
                "INSERT INTO contextual_chunks (id, chunk_id, file_id, "
                "workspace_id, contextual_prefix, contextual_text, "
                "model_id, prefix_token_count, cache_creation_input_tokens, "
                "cache_read_input_tokens) "
                "VALUES (%s, %s, %s, %s, '', %s, 'identity', 0, 0, 0)",
                (cc_id, chunk_id, file_id, workspace_id,
                 f"chunk{i}: bank statement excerpt"),
            )
            chunk_ids.append(chunk_id)
            cc_ids.append(cc_id)
        await conn.commit()
    return file_id, chunk_ids, cc_ids


def _fake_kv_extractor(*, doc_type, scalars, tables, model_id="fake-kv"):
    """Build an IdentityKVTablesExtractor-shaped object that returns the
    fixed payload regardless of input. Used to drive the worker without a
    real LLM."""
    from kb.extraction.kv_tables import (
        KVColumn, KVRow, KVScalar, KVTable, KVTablesPayload,
    )

    payload_obj = KVTablesPayload(
        doc_type=doc_type,
        scalars=[KVScalar(**s) for s in scalars],
        tables=[
            KVTable(
                name=t["name"],
                description=t.get("description", ""),
                cardinality=t.get("cardinality", "many"),
                columns=[KVColumn(**c) for c in t.get("columns", [])],
                rows=[KVRow(**r) for r in t.get("rows", [])],
            )
            for t in tables
        ],
        model_id=model_id,
        input_token_count=1000,
        output_token_count=400,
    )

    class FakeExtractor:
        async def extract(
            self, *, chunk_indexed_text, doc_type_hint=None,
            existing_sub_entity_hints=None,
        ):
            return payload_obj

    return lambda: FakeExtractor()


# ===========================================================================
# Lifecycle / state guards
# ===========================================================================


async def test_extract_kv_tables_identity_advances_lifecycle(
    client, db_url_superuser,
):
    """With Identity (no key), lifecycle still advances to
    entities_extracting; doc_type='unknown'; no scalars, no atomic_units."""
    from kb.workers.tasks import extract_kv_tables_file_impl

    workspace = str(uuid.uuid4())
    file_id, _, _ = await _seed_file_at_fields_extracting(
        db_url_superuser, workspace,
    )

    with _env(
        KB_DATABASE_URL=db_url_superuser,
        KB_KV_TABLES_EXTRACTOR="identity",
        KB_FIELD_EXTRACTOR=None,
        KB_GEMINI_API_KEY=None,
        KB_ANTHROPIC_API_KEY=None,
    ):
        from kb.config import get_settings
        get_settings.cache_clear()
        await extract_kv_tables_file_impl(file_id)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (workspace,),
        )
        cur = await conn.execute(
            "SELECT lifecycle_state, inferred_doc_type FROM files WHERE id = %s",
            (file_id,),
        )
        state, doc_type = await cur.fetchone()
        assert state == "entities_extracting"
        assert doc_type == "unknown"

        cur = await conn.execute(
            "SELECT count(*) FROM proposed_fields WHERE file_id = %s",
            (file_id,),
        )
        assert (await cur.fetchone())[0] == 0

        cur = await conn.execute(
            "SELECT count(*) FROM extracted_entities WHERE file_id = %s",
            (file_id,),
        )
        assert (await cur.fetchone())[0] == 0


async def test_extract_kv_tables_writes_scalars_and_tables(
    client, db_url_superuser, monkeypatch,
):
    """End-to-end: KV+Tables payload → proposed_fields + atomic_units +
    inferred_schema_fields all populated; lifecycle advances; source
    positions preserved."""
    from kb.workers.tasks import extract_kv_tables_file_impl

    workspace = str(uuid.uuid4())
    file_id, chunk_ids, cc_ids = await _seed_file_at_fields_extracting(
        db_url_superuser, workspace, label="bs",
    )

    import kb.extraction.kv_tables as kv_mod
    monkeypatch.setattr(kv_mod, "make_kv_tables_extractor", _fake_kv_extractor(
        doc_type="bank_statement",
        scalars=[
            {"name": "account_holder", "description": "Holder name",
             "value": "Jane Doe", "value_type": "text", "is_pii": True,
             "source_chunk": 0},
            {"name": "total_debits", "description": "Sum debits",
             "value": "1250.00", "value_type": "number", "is_pii": False,
             "source_chunk": 1},
        ],
        tables=[
            {
                "name": "transactions",
                "description": "Bank transactions",
                "cardinality": "many",
                "columns": [
                    {"name": "date", "value_type": "date"},
                    {"name": "amount", "value_type": "number"},
                ],
                "rows": [
                    {"values": {"date": "2024-01-15", "amount": "4.50"},
                     "source_chunk": 0},
                    {"values": {"date": "2024-01-16", "amount": "1245.50"},
                     "source_chunk": 1},
                ],
            },
        ],
    ))

    with _env(KB_DATABASE_URL=db_url_superuser):
        from kb.config import get_settings
        get_settings.cache_clear()
        await extract_kv_tables_file_impl(file_id)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (workspace,),
        )

        # Lifecycle + doc_type
        cur = await conn.execute(
            "SELECT lifecycle_state, inferred_doc_type FROM files WHERE id = %s",
            (file_id,),
        )
        state, doc_type = await cur.fetchone()
        assert state == "entities_extracting"
        assert doc_type == "bank_statement"

        # Scalars → proposed_fields
        cur = await conn.execute(
            "SELECT field_name, value_text, value_type, is_pii, "
            "source_chunk_id::text "
            "FROM proposed_fields WHERE file_id = %s ORDER BY field_name",
            (file_id,),
        )
        rows = await cur.fetchall()
        assert len(rows) == 2
        names = {r[0] for r in rows}
        assert names == {"account_holder", "total_debits"}
        # Source-chunk mapping: scalar source_chunk=0 → chunk_ids[0]
        by_name = {r[0]: r for r in rows}
        assert by_name["account_holder"][1] == "Jane Doe"
        assert by_name["account_holder"][3] is True  # is_pii
        assert by_name["account_holder"][4] == chunk_ids[0]
        assert by_name["total_debits"][4] == chunk_ids[1]

        # Tables → extracted_entities children. The atomic_units
        # staging table is gone; KV+Tables now writes the canonical
        # storage shape directly. source_chunk_id is a
        # contextual_chunks.id (FK from migration 0037).
        cur = await conn.execute(
            "SELECT unit_type, fields, source_chunk_id::text "
            "FROM extracted_entities WHERE file_id = %s "
            "AND unit_type IS NOT NULL "
            "ORDER BY (fields->>'date')",
            (file_id,),
        )
        units = await cur.fetchall()
        assert len(units) == 2
        # Plural table name "transactions" → singularized to "transaction"
        # at write time so Q-mode + retrieval channels stay aligned.
        assert all(u[0] == "transaction" for u in units)
        assert units[0][1] == {"date": "2024-01-15", "amount": "4.50"}
        assert units[0][2] == cc_ids[0]
        assert units[1][1] == {"date": "2024-01-16", "amount": "1245.50"}
        assert units[1][2] == cc_ids[1]

        # Schema layer: bootstrap should have created BankStatement
        # (doc_root) + Transactions (sub_entity) types with a
        # contains relationship.
        cur = await conn.execute(
            "SELECT name, kind FROM schema_entities "
            "WHERE workspace_id = %s AND lifecycle_state = 'active' "
            "ORDER BY kind, name",
            (workspace,),
        )
        type_rows = await cur.fetchall()
        type_names = {r[0]: r[1] for r in type_rows}
        # The doc_root carries the PascalCase doc_type name.
        assert any(k == "doc_root" for k in type_names.values())
        # And a sub_entity exists per table.
        assert any(k == "sub_entity" for k in type_names.values())

        # Inferred schema fields written (cluster + promote happens too)
        cur = await conn.execute(
            "SELECT canonical_name FROM inferred_schema_fields "
            "WHERE workspace_id = %s AND inferred_doc_type = 'bank_statement' "
            "ORDER BY canonical_name",
            (workspace,),
        )
        inferred = [r[0] for r in await cur.fetchall()]
        assert set(inferred) == {"account_holder", "total_debits"}


async def test_extract_kv_tables_state_guard_skips_post_fields_state(
    client, db_url_superuser,
):
    """If a file is already at entities_extracting (e.g. retry after the
    impl ran), the task no-ops cleanly."""
    from kb.workers.tasks import extract_kv_tables_file_impl

    workspace = str(uuid.uuid4())
    file_id, _, _ = await _seed_file_at_fields_extracting(
        db_url_superuser, workspace, label="advance",
    )

    # Push it past the fields_extracting state.
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (workspace,),
        )
        await conn.execute(
            "UPDATE files SET lifecycle_state = 'entities_extracting' "
            "WHERE id = %s",
            (file_id,),
        )
        await conn.commit()

    with _env(
        KB_DATABASE_URL=db_url_superuser,
        KB_KV_TABLES_EXTRACTOR="identity",
        KB_GEMINI_API_KEY=None,
        KB_ANTHROPIC_API_KEY=None,
    ):
        from kb.config import get_settings
        get_settings.cache_clear()
        await extract_kv_tables_file_impl(file_id)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (workspace,),
        )
        # No proposed_fields / atomic_units written by the no-op task.
        cur = await conn.execute(
            "SELECT count(*) FROM proposed_fields WHERE file_id = %s",
            (file_id,),
        )
        assert (await cur.fetchone())[0] == 0
        cur = await conn.execute(
            "SELECT count(*) FROM extracted_entities WHERE file_id = %s",
            (file_id,),
        )
        assert (await cur.fetchone())[0] == 0
