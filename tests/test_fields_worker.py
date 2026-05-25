"""Phase 5b — extract_fields_file_impl integration tests."""

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


async def _seed_file_to_fields_extracting(
    db_url: str, workspace_id: str, *, label: str = "f", n_pages: int = 1,
    inferred_doc_type: str | None = None,
) -> str:
    """Seed a file in 'fields_extracting' state with raw_pages.text content
    the proposer can chew on."""
    file_id = str(uuid.uuid4())
    sha = hashlib.sha256(f"fields-{workspace_id}-{label}".encode()).hexdigest()

    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace_id,))
        await conn.execute(
            "INSERT INTO files (id, workspace_id, name, content_sha, object_key, "
            "mime_type, size_bytes, lifecycle_state, inferred_doc_type) "
            "VALUES (%s, %s, %s, %s, %s, 'application/pdf', 100, 'fields_extracting', %s)",
            (file_id, workspace_id, f"fields-{label}.pdf", sha, f"raw_files/{sha}",
             inferred_doc_type),
        )
        for i in range(n_pages):
            await conn.execute(
                "INSERT INTO raw_pages (id, file_id, workspace_id, page_number, text, "
                "layout_json, content_sha) "
                "VALUES (%s, %s, %s, %s, %s, '{}'::jsonb, %s)",
                (str(uuid.uuid4()), file_id, workspace_id, i + 1,
                 f"Page {i+1} of {label}: Vendor ACME, Amount $100, Date 2024-01-15",
                 sha),
            )
        await conn.commit()
    return file_id


def _fake_extractor_factory(doc_type: str, fields: list[dict]):
    """Build a FieldExtractor mock that always returns the given doc_type
    + fields list."""
    from kb.extraction.fields import (
        DocTypeResult, FieldProposalResult, ProposedField,
    )

    class FakeExtractor:
        async def classify(self, *, doc_text):
            return DocTypeResult(doc_type=doc_type, model_id="fake")
        async def propose(self, *, doc_text):
            return FieldProposalResult(
                fields=[ProposedField(**f) for f in fields],
                model_id="fake",
            )
    return lambda: FakeExtractor()


async def test_extract_fields_identity_advances_lifecycle(client, db_url_superuser):
    """Decision #8: even with Identity (no fields), lifecycle advances."""
    from kb.workers.tasks import extract_fields_file_impl

    workspace = str(uuid.uuid4())
    file_id = await _seed_file_to_fields_extracting(db_url_superuser, workspace)

    with _env(KB_DATABASE_URL=db_url_superuser, KB_FIELD_EXTRACTOR="identity"):
        from kb.config import get_settings
        get_settings.cache_clear()
        await extract_fields_file_impl(file_id)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        cur = await conn.execute(
            "SELECT lifecycle_state, inferred_doc_type FROM files WHERE id = %s",
            (file_id,),
        )
        state, doc_type = await cur.fetchone()
        assert state == "units_extracting"
        assert doc_type == "unknown"  # Identity returns 'unknown'

        cur = await conn.execute(
            "SELECT count(*) FROM proposed_fields WHERE file_id = %s",
            (file_id,),
        )
        assert (await cur.fetchone())[0] == 0


async def test_extract_fields_writes_proposed_and_inferred_rows(
    client, db_url_superuser, monkeypatch
):
    """Decision #3 + #4: proposed_fields rows written; inferred_schema_fields
    UPSERTed with metrics."""
    from kb.workers.tasks import extract_fields_file_impl

    workspace = str(uuid.uuid4())
    file_id = await _seed_file_to_fields_extracting(db_url_superuser, workspace, label="x")

    import kb.extraction.fields as fields_mod
    monkeypatch.setattr(fields_mod, "make_field_extractor", _fake_extractor_factory(
        doc_type="vendor_record",
        fields=[
            {"field_name": "vendor_name", "value_text": "ACME",
             "value_type": "text", "is_pii": False, "field_description": "Name"},
            {"field_name": "amount", "value_text": "$100",
             "value_type": "number", "is_pii": False, "field_description": "Total"},
        ],
    ))

    with _env(KB_DATABASE_URL=db_url_superuser):
        from kb.config import get_settings
        get_settings.cache_clear()
        await extract_fields_file_impl(file_id)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        cur = await conn.execute(
            "SELECT lifecycle_state, inferred_doc_type FROM files WHERE id = %s",
            (file_id,),
        )
        state, doc_type = await cur.fetchone()
        assert state == "units_extracting"
        assert doc_type == "vendor_record"

        cur = await conn.execute(
            "SELECT field_name, value_type FROM proposed_fields WHERE file_id = %s "
            "ORDER BY field_name",
            (file_id,),
        )
        rows = await cur.fetchall()
        assert [(r[0], r[1]) for r in rows] == [
            ("amount", "number"),
            ("vendor_name", "text"),
        ]

        cur = await conn.execute(
            "SELECT canonical_name, n_docs_observed, prevalence, is_promoted "
            "FROM inferred_schema_fields "
            "WHERE workspace_id = %s AND inferred_doc_type = 'vendor_record' "
            "ORDER BY canonical_name",
            (workspace,),
        )
        rows = await cur.fetchall()
        assert len(rows) == 2
        # PR5: default min_docs lowered to 1 so single-doc demo corpora
        # exercise the L4 closed-world path. With n=1 and prevalence=1.0
        # the fields auto-promote. The threshold-not-met case is now
        # covered by `test_extract_fields_does_not_promote_below_threshold`
        # which explicitly sets KB_PROMOTION_MIN_DOCS=20.
        for r in rows:
            assert r[1] == 1  # n_docs_observed
            assert r[2] == pytest.approx(1.0)
            assert r[3] is True  # was False under the old min_docs=5


async def test_extract_fields_promotes_when_threshold_crosses(
    client, db_url_superuser, monkeypatch
):
    """Decision #6 + #7: when n_docs_observed crosses min_docs and
    prevalence/stability hold, auto-promote → schema_fields row with
    auto_promoted=true + inferred_schema_fields.is_promoted=true."""
    from kb.workers.tasks import extract_fields_file_impl

    workspace = str(uuid.uuid4())
    # Seed 5 files at fields_extracting with same doc_type already set.
    import kb.extraction.fields as fields_mod
    monkeypatch.setattr(fields_mod, "make_field_extractor", _fake_extractor_factory(
        doc_type="vendor_record",
        fields=[{
            "field_name": "vendor_name", "value_text": "ACME",
            "value_type": "text", "is_pii": False, "field_description": "Name"
        }],
    ))

    with _env(KB_DATABASE_URL=db_url_superuser, KB_PROMOTION_MIN_DOCS="5"):
        from kb.config import get_settings
        get_settings.cache_clear()
        for i in range(5):
            fid = await _seed_file_to_fields_extracting(
                db_url_superuser, workspace, label=f"doc{i}",
            )
            await extract_fields_file_impl(fid)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))

        # inferred_schema_fields shows is_promoted=true for vendor_name
        cur = await conn.execute(
            "SELECT n_docs_observed, prevalence, is_promoted, promoted_schema_field_id "
            "FROM inferred_schema_fields "
            "WHERE workspace_id = %s AND canonical_name = 'vendor_name'",
            (workspace,),
        )
        row = await cur.fetchone()
        assert row is not None, "vendor_name should be in inferred_schema_fields"
        n_docs, prevalence, is_promoted, promoted_id = row
        assert n_docs == 5
        assert prevalence == pytest.approx(1.0)
        assert is_promoted is True
        assert promoted_id is not None

        # schema_fields row exists with auto_promoted=true
        cur = await conn.execute(
            "SELECT name, type, auto_promoted FROM schema_fields "
            "WHERE id = %s", (promoted_id,),
        )
        row = await cur.fetchone()
        assert row is not None
        assert row[0] == "vendor_name"
        assert row[1] == "string"  # value_type 'text' → schema_fields.type 'string'
        assert row[2] is True

        # Schema auto-created with name 'auto:vendor_record'
        cur = await conn.execute(
            "SELECT count(*) FROM schemas "
            "WHERE workspace_id = %s AND name = 'auto:vendor_record' "
            "AND lifecycle_state = 'active'",
            (workspace,),
        )
        assert (await cur.fetchone())[0] == 1
