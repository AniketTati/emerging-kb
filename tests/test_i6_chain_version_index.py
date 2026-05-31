"""I6 — doc-chain version_index correctness (the "all members share index 1" bug).

Two fixes, exercised against finance's ACTUAL frontmatter shapes:
  - loan chain declares `chain:` (alias of chain_id) + chain_role + parent_doc,
    NO chain_version → must still get distinct, ordered indices;
  - complaint chain declares `chain_id:` + parent_doc, no chain_role.

`next_version_index` assigns max(existing)+1 so a loan original + 2 addenda
land at 0/1/2 instead of 0/1/1.
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


async def _seed_chain_doc(
    conn, *, workspace: str, doc_type: str, doc_id_ref: str,
    chain_field: str, chain_value: str,
    role: str | None = None, parent_ref: str | None = None,
) -> str:
    """Seed a ready file + the chain-declaring proposed_fields the worker's
    explicit path reads (doc_id, chain/chain_id, chain_role, parent_doc)."""
    from kb.domain.fields import insert_proposed_field

    file_id = str(uuid.uuid4())
    sha = hashlib.sha256(f"{workspace}-{doc_id_ref}".encode()).hexdigest()
    await conn.execute(
        "INSERT INTO files (id, workspace_id, name, content_sha, object_key, "
        "mime_type, size_bytes, lifecycle_state, inferred_doc_type) "
        "VALUES (%s, %s, %s, %s, %s, 'text/markdown', 100, 'ready', %s)",
        (file_id, workspace, doc_id_ref, sha, f"raw_files/{sha}", doc_type),
    )
    fields = [("doc_id", doc_id_ref), (chain_field, chain_value)]
    if role is not None:
        fields.append(("chain_role", role))
    if parent_ref is not None:
        fields.append(("parent_doc", parent_ref))
    for name, val in fields:
        await insert_proposed_field(
            conn, file_id=file_id, workspace_id=workspace,
            inferred_doc_type=doc_type, field_name=name, field_description="",
            value_text=val, value_type="text", is_pii=False, model_id="test-mock",
        )
    return file_id


async def _members(conn, workspace: str, doc_id: str):
    """Return [(doc_name, version_index, role)] for the chain `doc_id` is in."""
    await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
    cur = await conn.execute(
        "SELECT m.chain_id FROM doc_chain_members m WHERE m.doc_id = %s", (doc_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return []
    chain_id = row[0]
    cur = await conn.execute(
        "SELECT f.name, m.version_index, m.role FROM doc_chain_members m "
        "JOIN files f ON f.id = m.doc_id WHERE m.chain_id = %s "
        "ORDER BY m.version_index",
        (chain_id,),
    )
    return [(r[0], r[1], r[2]) for r in await cur.fetchall()]


async def test_next_version_index_helper(db_url_superuser):
    from kb.domain.doc_chains import add_member, next_version_index, upsert_chain

    workspace = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        chain_id = await upsert_chain(
            conn, workspace_id=workspace, chain_type="contract_chain",
            title="t", chain_key="explicit:c1", detection_confidence=1.0,
            current_version_id=None,
        )
        assert await next_version_index(conn, chain_id=chain_id) == 0
        f1, f2 = str(uuid.uuid4()), str(uuid.uuid4())
        for fid in (f1, f2):
            sha = hashlib.sha256(fid.encode()).hexdigest()
            await conn.execute(
                "INSERT INTO files (id, workspace_id, name, content_sha, object_key, "
                "mime_type, size_bytes, lifecycle_state) VALUES "
                "(%s, %s, %s, %s, %s, 'text/markdown', 1, 'ready')",
                (fid, workspace, fid[:8], sha, f"raw/{sha}"),
            )
        await add_member(conn, chain_id=chain_id, doc_id=f1, workspace_id=workspace, version_index=0, role="original")
        assert await next_version_index(conn, chain_id=chain_id) == 1
        await add_member(conn, chain_id=chain_id, doc_id=f2, workspace_id=workspace, version_index=1, role="amendment")
        assert await next_version_index(conn, chain_id=chain_id) == 2


async def test_loan_chain_chain_alias_distinct_indices(db_url_superuser):
    """Loan original + 2 addenda declaring `chain:` (no chain_version) → one
    chain, version_index 0/1/2 (not 0/1/1), original + 2 amendments."""
    from kb.workers.tasks import detect_doc_chain_file_impl

    workspace = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        orig = await _seed_chain_doc(
            conn, workspace=workspace, doc_type="loan_agreement",
            doc_id_ref="loan-original", chain_field="chain",
            chain_value="chain_acme_hdfc_loan", role="original",
        )
        add1 = await _seed_chain_doc(
            conn, workspace=workspace, doc_type="loan_agreement",
            doc_id_ref="loan-addendum-1", chain_field="chain",
            chain_value="chain_acme_hdfc_loan", role="amendment",
            parent_ref="loan-original",
        )
        add2 = await _seed_chain_doc(
            conn, workspace=workspace, doc_type="loan_agreement",
            doc_id_ref="loan-addendum-2", chain_field="chain",
            chain_value="chain_acme_hdfc_loan", role="amendment",
            parent_ref="loan-original",
        )
        await conn.commit()

    with _use_db(db_url_superuser):
        for fid in (orig, add1, add2):
            await detect_doc_chain_file_impl(fid)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        members = await _members(conn, workspace, orig)

    assert len(members) == 3, f"all 3 loan docs should be one chain; got {members}"
    indices = sorted(m[1] for m in members)
    assert indices == [0, 1, 2], f"version_index must be distinct 0/1/2; got {indices}"
    roles = {m[0]: m[2] for m in members}
    assert roles["loan-original"] == "original"
    assert roles["loan-addendum-1"] == "amendment"
    assert roles["loan-addendum-2"] == "amendment"


async def test_complaint_chain_chain_id_distinct_indices(db_url_superuser):
    """Complaint initial + resolution declaring `chain_id:` (parent_doc drives
    role) → one chain with distinct indices."""
    from kb.workers.tasks import detect_doc_chain_file_impl

    workspace = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        initial = await _seed_chain_doc(
            conn, workspace=workspace, doc_type="customer_complaint",
            doc_id_ref="complaint-initial", chain_field="chain_id",
            chain_value="chain_complaint",
        )
        resolution = await _seed_chain_doc(
            conn, workspace=workspace, doc_type="customer_complaint",
            doc_id_ref="complaint-resolution", chain_field="chain_id",
            chain_value="chain_complaint", parent_ref="complaint-initial",
        )
        await conn.commit()

    with _use_db(db_url_superuser):
        for fid in (initial, resolution):
            await detect_doc_chain_file_impl(fid)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        members = await _members(conn, workspace, initial)

    assert len(members) == 2, f"both complaint docs should be one chain; got {members}"
    indices = sorted(m[1] for m in members)
    assert indices == [0, 1], f"version_index must be distinct 0/1; got {indices}"
