"""#19 / I2 — corpus field-convergence pass (converge_workspace_fields_impl).

After per-doc ingest settles, re-cluster proposed_fields across ALL docs of a
doc_type and converge variant field names (total_cost / total_amount) into one
canonical inferred_schema_field. The embedding + judge are injected as fakes
here; the live run uses the real Gemini embedder + field-merge judge.
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
def _use_db(db_url: str):
    """Point settings.database_url at the testcontainer DB (the impl reads
    get_settings().database_url, not a passed conn)."""
    from kb.config import get_settings

    prior = os.environ.get("KB_DATABASE_URL")
    os.environ["KB_DATABASE_URL"] = db_url
    get_settings.cache_clear()
    try:
        yield
    finally:
        if prior is None:
            os.environ.pop("KB_DATABASE_URL", None)
        else:
            os.environ["KB_DATABASE_URL"] = prior
        get_settings.cache_clear()


async def _seed_doc_with_field(
    db_url: str, *, workspace: str, doc_type: str, field_name: str,
    description: str = "the contract value", value: str = "100.00",
) -> str:
    """Insert one ready file of `doc_type` with a single proposed_field."""
    from kb.domain.fields import insert_proposed_field

    file_id = str(uuid.uuid4())
    sha = hashlib.sha256(f"{workspace}-{file_id}".encode()).hexdigest()
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        await conn.execute(
            "INSERT INTO files (id, workspace_id, name, content_sha, object_key, "
            "mime_type, size_bytes, lifecycle_state, inferred_doc_type) "
            "VALUES (%s, %s, %s, %s, %s, 'application/pdf', 100, 'ready', %s)",
            (file_id, workspace, f"f-{field_name}", sha, f"raw_files/{sha}", doc_type),
        )
        await insert_proposed_field(
            conn,
            file_id=file_id,
            workspace_id=workspace,
            inferred_doc_type=doc_type,
            field_name=field_name,
            field_description=description,
            value_text=value,
            value_type="number",
            is_pii=False,
            model_id="test-mock",
        )
        await conn.commit()
    return file_id


async def _read_inferred(db_url: str, workspace: str, doc_type: str) -> list[str]:
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        cur = await conn.execute(
            "SELECT canonical_name FROM inferred_schema_fields "
            "WHERE workspace_id = %s AND inferred_doc_type = %s ORDER BY canonical_name",
            (workspace, doc_type),
        )
        return [r[0] for r in await cur.fetchall()]


async def _fake_embed(texts):
    # All field names embed identically → cosine 1.0 ≥ blocking threshold,
    # so every pair becomes a judge candidate (the judge decides the merge).
    return [[1.0, 0.0, 0.0] for _ in texts]


async def test_convergence_merges_variant_field_names(db_url_superuser):
    workspace = str(uuid.uuid4())
    # total_cost in 2 docs (dominant), total_amount in 1 → 2 raw clusters.
    await _seed_doc_with_field(db_url_superuser, workspace=workspace, doc_type="contract", field_name="total_cost")
    await _seed_doc_with_field(db_url_superuser, workspace=workspace, doc_type="contract", field_name="total_cost")
    await _seed_doc_with_field(db_url_superuser, workspace=workspace, doc_type="contract", field_name="total_amount")

    from kb.workers.tasks import converge_workspace_fields_impl

    async def judge_merge(a, b):
        return {a.canonical_name, b.canonical_name} == {"total_cost", "total_amount"}

    with _use_db(db_url_superuser):
        summary = await converge_workspace_fields_impl(
            workspace_id=workspace, embed_fn=_fake_embed, judge_fn=judge_merge,
        )

    inferred = await _read_inferred(db_url_superuser, workspace, "contract")
    assert inferred == ["total_cost"], (
        f"variant field names should converge to the dominant canonical; got {inferred}"
    )
    assert summary["clusters_in"] == 2
    assert summary["clusters_out"] == 1
    assert summary["doc_types"] == 1


async def test_judge_veto_keeps_fields_separate(db_url_superuser):
    workspace = str(uuid.uuid4())
    await _seed_doc_with_field(db_url_superuser, workspace=workspace, doc_type="contract", field_name="total_cost")
    await _seed_doc_with_field(db_url_superuser, workspace=workspace, doc_type="contract", field_name="start_date", description="agreement start", value="2026-01-01")

    from kb.workers.tasks import converge_workspace_fields_impl

    async def judge_never(a, b):
        return False  # NoopFieldMergeJudge behavior

    with _use_db(db_url_superuser):
        summary = await converge_workspace_fields_impl(
            workspace_id=workspace, embed_fn=_fake_embed, judge_fn=judge_never,
        )

    inferred = await _read_inferred(db_url_superuser, workspace, "contract")
    assert inferred == ["start_date", "total_cost"], (
        f"with the judge vetoing, distinct fields must stay separate; got {inferred}"
    )
    assert summary["clusters_out"] == 2


async def test_no_proposed_fields_is_noop(db_url_superuser):
    """A workspace with a doc_type but no proposed_fields converges to nothing
    and doesn't error."""
    workspace = str(uuid.uuid4())
    # File with a doc_type but NO proposed_fields seeded.
    fid = str(uuid.uuid4())
    sha = hashlib.sha256(f"{workspace}-{fid}".encode()).hexdigest()
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        await conn.execute(
            "INSERT INTO files (id, workspace_id, name, content_sha, object_key, "
            "mime_type, size_bytes, lifecycle_state, inferred_doc_type) "
            "VALUES (%s, %s, 'empty', %s, %s, 'application/pdf', 100, 'ready', 'contract')",
            (fid, workspace, sha, f"raw_files/{sha}"),
        )
        await conn.commit()

    from kb.workers.tasks import converge_workspace_fields_impl

    with _use_db(db_url_superuser):
        summary = await converge_workspace_fields_impl(
            workspace_id=workspace, embed_fn=_fake_embed, judge_fn=lambda a, b: True,
        )
    assert summary["clusters_in"] == 0
    assert await _read_inferred(db_url_superuser, workspace, "contract") == []
