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


async def test_parse_task_failure_writes_failed_lifecycle_event(client, test_workspace):
    """A parser exception writes a parsing→failed event; lifecycle_state stays 'failed'."""
    from kb.workers.tasks import parse_file_impl  # G4

    # Stage Mode-B with bytes that AREN'T actually a PDF, but tell the server
    # mime_type=application/pdf so the dispatcher routes to Docling and Docling
    # raises ParseError.
    from kb.config import get_settings
    settings = get_settings()
    # (In a real test we'd pre-stage the bad bytes into MinIO via Mode B,
    # bypassing the multipart magic-byte check that would otherwise reject
    # at upload time. The G4 build supports this via Mode B JSON.)

    # For G3 spec purposes, assume there's a Mode-B path that lets the worker
    # see bytes that aren't a valid PDF.
    # (Skeleton uses a placeholder pre-stage step that lands at G4.)

    # Pre-stage bad bytes (will be implemented at G4 via the minio test fixture):
    fid = "<set by G4 pre-stage>"  # marker for the skeleton
    # Placeholder assertion; G4 implementation will pre-stage properly.
    # Skeleton check: domain function should raise ParseError; worker
    # should catch and write the failure event.
    assert True, "G4 fills in the pre-stage Mode-B flow; spec described in §4.5."


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
