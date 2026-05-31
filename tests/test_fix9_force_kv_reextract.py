"""FIX 9 — force re-extract re-runs KV+Tables on a ready file (cached chunks).

Pre-fix the corpus re-extract only re-ran schema-entities, so it could never
discover NEW body fields. Now extract_kv_tables_file_impl(force=True) re-runs
the open-vocab pass against cached chunks, stays `ready`, and is
non-destructive on a transient-empty re-run.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from kb.extraction.kv_tables import KVScalar, KVTable, KVTablesPayload
from tests.test_kv_tables_worker import _seed_file_at_fields_extracting


pytestmark = pytest.mark.asyncio


def _fake_kv(payload: KVTablesPayload):
    """A correctly-typed fake KV+Tables extractor (full extract() signature)."""
    class _Fake:
        async def extract(
            self, *, chunk_indexed_text, doc_type_hint=None,
            existing_sub_entity_hints=None, existing_scalar_hints=None,
            existing_sub_entity_column_hints=None,
        ):
            return payload
    return lambda: _Fake()


async def _set_ready(db_url: str, ws: str, file_id: str) -> None:
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (ws,))
        await conn.execute(
            "UPDATE files SET lifecycle_state = 'ready' WHERE id = %s", (file_id,),
        )
        await conn.commit()


async def _proposed_field_names(db_url: str, ws: str, file_id: str) -> set[str]:
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (ws,))
        cur = await conn.execute(
            "SELECT field_name FROM proposed_fields WHERE file_id = %s", (file_id,),
        )
        return {r[0] for r in await cur.fetchall()}


async def _lifecycle_state(db_url: str, ws: str, file_id: str) -> str:
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (ws,))
        cur = await conn.execute(
            "SELECT lifecycle_state FROM files WHERE id = %s", (file_id,),
        )
        return (await cur.fetchone())[0]


async def test_force_reextract_rediscovers_body_fields_and_stays_ready(
    db_url_superuser, monkeypatch,
):
    workspace = str(uuid.uuid4())
    file_id, _chunks, _cc = await _seed_file_at_fields_extracting(
        db_url_superuser, workspace, label="loan",
    )
    await _set_ready(db_url_superuser, workspace, file_id)

    import kb.extraction.kv_tables as kv_mod
    payload = KVTablesPayload(
        doc_type="loan_agreement",
        scalars=[KVScalar(name="interest_rate", value="9.4",
                          value_type="number", source_chunk=0)],
        tables=[],
        model_id="fake",
    )
    monkeypatch.setattr(kv_mod, "make_kv_tables_extractor", _fake_kv(payload))

    from kb.workers.tasks import extract_kv_tables_file_impl
    monkeypatch.setenv("KB_DATABASE_URL", db_url_superuser)
    await extract_kv_tables_file_impl(file_id, force=True)

    # Body field re-discovered, file remains ready (not transitioned).
    assert "interest_rate" in await _proposed_field_names(
        db_url_superuser, workspace, file_id)
    assert await _lifecycle_state(db_url_superuser, workspace, file_id) == "ready"


async def test_force_reextract_empty_is_non_destructive(
    db_url_superuser, monkeypatch,
):
    workspace = str(uuid.uuid4())
    file_id, _chunks, _cc = await _seed_file_at_fields_extracting(
        db_url_superuser, workspace, label="loan2",
    )

    import kb.extraction.kv_tables as kv_mod
    from kb.workers.tasks import extract_kv_tables_file_impl
    monkeypatch.setenv("KB_DATABASE_URL", db_url_superuser)

    # First a good extraction (normal flow) writes a body field.
    good = KVTablesPayload(
        doc_type="loan_agreement",
        scalars=[KVScalar(name="interest_rate", value="9.4",
                          value_type="number", source_chunk=0)],
        model_id="fake",
    )
    monkeypatch.setattr(kv_mod, "make_kv_tables_extractor", _fake_kv(good))
    await extract_kv_tables_file_impl(file_id)  # fields_extracting → real write
    await _set_ready(db_url_superuser, workspace, file_id)
    assert "interest_rate" in await _proposed_field_names(
        db_url_superuser, workspace, file_id)

    # Now a transient-empty force re-run must NOT wipe the existing field.
    monkeypatch.setattr(
        kv_mod, "make_kv_tables_extractor",
        _fake_kv(KVTablesPayload(doc_type="loan_agreement", model_id="identity")),
    )
    await extract_kv_tables_file_impl(file_id, force=True)
    assert "interest_rate" in await _proposed_field_names(
        db_url_superuser, workspace, file_id)
    assert await _lifecycle_state(db_url_superuser, workspace, file_id) == "ready"
