"""I1 (regression) — chunk_file_impl must CLASSIFY before chunking so a
bank_statement routes to the row-per-leaf chunker (one chunk per transaction
row), not MIME/hierarchical fixed-size windows.

This guards the wiring that was silently missing (the classifier existed +
was unit-tested, but chunk_file_impl never called it, so markdown bank
statements were chunked hierarchically — caught by a live finance smoke).
"""

from __future__ import annotations

import hashlib
import uuid

import psycopg
import pytest


pytestmark = pytest.mark.asyncio


@pytest.fixture
def test_workspace() -> str:
    return str(uuid.uuid4())


_STMT_MD = "\n".join([
    "# HDFC Bank Statement — Acme Corp",
    "| Date | Description | Debit | Credit | Balance |",
    "|---|---|---|---|---|",
    "| 01-Jan-2025 | NEFT from Vertex | — | 100 | 1100 |",
    "| 02-Jan-2025 | RTGS to HDFC EMI | 200 | — | 900 |",
    "| 03-Jan-2025 | Wire to Nimbus | 300 | — | 600 |",
])
_TXN_LINES = [
    "| 01-Jan-2025 | NEFT from Vertex | — | 100 | 1100 |",
    "| 02-Jan-2025 | RTGS to HDFC EMI | 200 | — | 900 |",
    "| 03-Jan-2025 | Wire to Nimbus | 300 | — | 600 |",
]


class _FakeBankStatementClassifier:
    async def classify(self, *, text: str, file_name: str | None = None) -> str:
        return "bank_statement"


async def _seed_parsed_md(db_url: str, workspace: str, text: str) -> str:
    """Insert a file at lifecycle_state='parsed' (markdown) with one raw_page."""
    file_id = str(uuid.uuid4())
    sha = hashlib.sha256(f"{workspace}-{file_id}".encode()).hexdigest()
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        await conn.execute(
            "INSERT INTO files (id, workspace_id, name, content_sha, object_key, "
            "mime_type, size_bytes, lifecycle_state) "
            "VALUES (%s, %s, 'stmt.md', %s, %s, 'text/markdown', 100, 'parsed')",
            (file_id, workspace, sha, f"raw_files/{sha}"),
        )
        await conn.execute(
            "INSERT INTO raw_pages (id, file_id, workspace_id, page_number, text, "
            "layout_json, content_sha) VALUES (%s, %s, %s, 1, %s, '{}'::jsonb, %s)",
            (str(uuid.uuid4()), file_id, workspace, text, sha),
        )
        await conn.commit()
    return file_id


async def test_chunk_file_impl_classifies_then_routes_to_row_chunker(
    client, test_workspace, db_url_superuser, monkeypatch,
):
    from kb.workers.tasks import chunk_file_impl

    file_id = await _seed_parsed_md(db_url_superuser, test_workspace, _STMT_MD)

    # The classifier is imported inside chunk_file_impl as
    # `from kb.classification import make_doc_type_classifier`; patch there.
    import kb.classification as classification_mod
    monkeypatch.setattr(
        classification_mod, "make_doc_type_classifier",
        lambda: _FakeBankStatementClassifier(),
    )

    await chunk_file_impl(file_id)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (test_workspace,))
        # 1) classify ran + persisted the chunk-time doc_type.
        cur = await conn.execute(
            "SELECT inferred_doc_type FROM files WHERE id = %s", (file_id,),
        )
        assert (await cur.fetchone())[0] == "bank_statement"

        # 2) row-per-leaf engaged: each transaction is its OWN leaf chunk
        #    (hierarchical/MIME windows would merge/split rows arbitrarily).
        cur = await conn.execute(
            "SELECT text FROM chunks WHERE file_id = %s AND node_level = 0", (file_id,),
        )
        leaves = {t.strip() for (t,) in await cur.fetchall()}
        for txn in _TXN_LINES:
            assert txn in leaves, f"transaction row not a standalone leaf: {txn!r}"


async def test_chunk_file_impl_unknown_doc_type_does_not_row_chunk(
    client, test_workspace, db_url_superuser, monkeypatch,
):
    """Control: when the classifier returns 'unknown', markdown falls back to
    hierarchical — transactions are NOT each their own leaf. Confirms the row
    routing is genuinely driven by the classification."""
    from kb.workers.tasks import chunk_file_impl

    file_id = await _seed_parsed_md(db_url_superuser, test_workspace, _STMT_MD)

    class _Unknown:
        async def classify(self, *, text, file_name=None):
            return "unknown"

    import kb.classification as classification_mod
    monkeypatch.setattr(classification_mod, "make_doc_type_classifier", lambda: _Unknown())

    await chunk_file_impl(file_id)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (test_workspace,))
        cur = await conn.execute(
            "SELECT text FROM chunks WHERE file_id = %s AND node_level = 0", (file_id,),
        )
        leaves = {t.strip() for (t,) in await cur.fetchall()}
    # Not every transaction is a standalone leaf under hierarchical routing.
    assert not all(txn in leaves for txn in _TXN_LINES)
