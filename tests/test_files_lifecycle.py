"""Phase 2a — file lifecycle state machine + audit trail + idempotency.

RED at G3: imports from `kb.api.files` + `kb.workers.tasks` land at G4.

Spec: tests/specs/phase_2a.md §4.5.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from tests.test_files_crud import _TINY_PDF


pytestmark = pytest.mark.asyncio


@pytest.fixture
def test_workspace() -> str:
    return str(uuid.uuid4())


def headers(workspace: str, *, idempotency_key: str | None = None) -> dict[str, str]:
    h = {"X-Test-Workspace": workspace}
    if idempotency_key is not None:
        h["Idempotency-Key"] = idempotency_key
    return h


# ===========================================================================
# §5.3 — lifecycle history events
# ===========================================================================


async def test_post_creates_initial_lifecycle_event(client, test_workspace):
    """POST writes one file_lifecycle row: from=null, to='queued', event='upload'."""
    resp = await client.post(
        "/files",
        files={"file": ("init.pdf", _TINY_PDF, "application/pdf")},
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    fid = resp.json()["id"]
    get = await client.get(f"/files/{fid}", headers=headers(test_workspace))
    lifecycle = get.json()["lifecycle"]
    assert len(lifecycle) == 1
    assert lifecycle[0]["from_state"] is None
    assert lifecycle[0]["to_state"] == "queued"
    assert lifecycle[0]["event"] == "upload"


async def test_parse_task_transitions_queued_to_parsing_to_parsed(client, test_workspace):
    """After running worker: lifecycle has [null→queued, queued→parsing, parsing→parsed]."""
    from kb.workers.tasks import parse_file_impl  # G4

    resp = await client.post(
        "/files",
        files={"file": ("good.pdf", _TINY_PDF, "application/pdf")},
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    fid = resp.json()["id"]

    await parse_file_impl(fid)

    get = await client.get(f"/files/{fid}", headers=headers(test_workspace))
    body = get.json()
    transitions = [
        (e["from_state"], e["to_state"]) for e in body["lifecycle"]
    ]
    assert transitions == [
        (None, "queued"),
        ("queued", "parsing"),
        ("parsing", "parsed"),
    ]
    assert body["lifecycle_state"] == "parsed"


async def test_parse_task_failure_writes_failed_lifecycle_event(
    client, test_workspace, minio_container
):
    """A parser exception writes a parsing→failed event; lifecycle_state='failed'.

    Pre-stages bytes that start with the PDF magic header (so the upload's
    mime detection accepts them) but aren't valid PDF content (so Docling's
    actual parse raises ParseError).
    """
    import hashlib
    import uuid as _uuid
    from io import BytesIO

    from minio import Minio

    from kb.workers.tasks import parse_file_impl

    cfg = minio_container.get_config()
    minio_client = Minio(
        cfg["endpoint"], access_key=cfg["access_key"],
        secret_key=cfg["secret_key"], secure=False,
    )
    # Corrupt PDF: magic header present + complete garbage after.
    bad_pdf = b"%PDF-1.4\nthis is not actually a valid pdf body - corrupt\n%%EOF\n"
    sha = hashlib.sha256(bad_pdf).hexdigest()
    bucket = "kb-files"
    if not minio_client.bucket_exists(bucket):
        minio_client.make_bucket(bucket)
    minio_client.put_object(
        bucket, f"raw_files/{sha}", BytesIO(bad_pdf), length=len(bad_pdf),
        content_type="application/pdf",
    )

    # POST via Mode B
    resp = await client.post(
        "/files",
        json={"minio_object_key": f"raw_files/{sha}", "name": "corrupt.pdf"},
        headers=headers(test_workspace, idempotency_key=str(_uuid.uuid4())),
    )
    assert resp.status_code == 201, resp.text
    fid = resp.json()["id"]

    # Run the worker — Docling will fail
    await parse_file_impl(fid)

    # Inspect lifecycle
    get = await client.get(f"/files/{fid}", headers=headers(test_workspace))
    body = get.json()
    assert body["lifecycle_state"] == "failed"
    last = body["lifecycle"][-1]
    assert last["from_state"] == "parsing"
    assert last["to_state"] == "failed"
    assert last["event"] == "parse_failed"
    assert "error_class" in last["payload"]


async def test_parse_task_idempotent_when_already_parsed(client, test_workspace):
    """Replay of parse_file_impl on already-parsed file is a no-op (no extra event)."""
    from kb.workers.tasks import parse_file_impl  # G4

    resp = await client.post(
        "/files",
        files={"file": ("once.pdf", _TINY_PDF, "application/pdf")},
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    fid = resp.json()["id"]

    await parse_file_impl(fid)
    g1 = await client.get(f"/files/{fid}", headers=headers(test_workspace))
    count_before = len(g1.json()["lifecycle"])

    # Replay
    await parse_file_impl(fid)
    g2 = await client.get(f"/files/{fid}", headers=headers(test_workspace))
    count_after = len(g2.json()["lifecycle"])

    assert count_before == count_after == 3, (
        "replayed parse on already-parsed file must not write new lifecycle events"
    )


# ===========================================================================
# §5.1 #4 — DB-layer immutability of file_lifecycle
# ===========================================================================


async def test_file_lifecycle_table_rejects_update_via_kb_app(
    client, test_workspace, db_url_kb_app, db_url_superuser
):
    """file_lifecycle has REVOKE UPDATE,DELETE from kb_app — UPDATE fails."""
    resp = await client.post(
        "/files",
        files={"file": ("immut.pdf", _TINY_PDF, "application/pdf")},
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    fid = resp.json()["id"]

    # As kb_app: UPDATE rejected.
    with psycopg.connect(db_url_kb_app) as conn:
        conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "UPDATE file_lifecycle SET event = 'tampered' WHERE file_id = %s",
                (fid,),
            )
        conn.rollback()

    # As superuser: same UPDATE succeeds — proves the constraint is the GRANT.
    with psycopg.connect(db_url_superuser) as conn:
        conn.execute(
            "UPDATE file_lifecycle SET event = 'admin override' WHERE file_id = %s",
            (fid,),
        )
        conn.commit()
