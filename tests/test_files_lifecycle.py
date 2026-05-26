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


# ===========================================================================
# Forward-only lifecycle guard
# ===========================================================================
#
# 19 docs were left stuck mid-pipeline because a re-trigger of
# `extract_atomic_units_file` on a `ready` file clobbered its state
# back to `entities_extracting`. transition_lifecycle() unconditionally
# overwrote whatever was in the column; downstream chains then either
# raced, missed idempotency checks, or saw stale state.
#
# The fix (kb.domain.files._is_valid_transition) rejects any transition
# that walks the file backward in the pipeline order. These tests cover
# the rule directly so future refactors can't regress it.


def test_lifecycle_order_is_monotonic_for_happy_path():
    """Defined order must match the worker chain: queued → parsing →
    parsed → chunked → contextualized → embedded → raptor_building →
    mentions_extracting → fields_extracting → units_extracting →
    entities_extracting → identity_resolving → ready."""
    from kb.domain.files import _LIFECYCLE_ORDER as ORDER
    happy = [
        "queued", "parsing", "parsed", "chunked", "contextualized",
        "embedded", "raptor_building", "mentions_extracting",
        "fields_extracting", "units_extracting", "entities_extracting",
        "identity_resolving", "ready",
    ]
    ranks = [ORDER[s] for s in happy]
    assert ranks == sorted(ranks)


@pytest.mark.parametrize("frm,to,expected", [
    # Forward moves — allowed.
    ("queued", "parsing", True),
    ("parsed", "chunked", True),
    ("entities_extracting", "identity_resolving", True),
    ("identity_resolving", "ready", True),
    # Self-transitions — allowed (relationships_built / graph_built /
    # doc_chain_detected audit events on a ready file).
    ("ready", "ready", True),
    ("entities_extracting", "entities_extracting", True),
    # Terminal-on-entry — always allowed.
    ("ready", "failed", True),
    ("chunked", "failed", True),
    ("contextualized", "deleted", True),
    # Backward — refused. This IS the exact 19-stuck-doc bug pattern.
    ("ready", "entities_extracting", False),
    ("ready", "units_extracting", False),
    ("identity_resolving", "units_extracting", False),
    ("chunked", "parsing", False),
    # Terminals are sticky.
    ("failed", "ready", False),
    ("deleted", "parsing", False),
])
def test_is_valid_transition_enforces_forward_only(frm, to, expected):
    from kb.domain.files import _is_valid_transition
    assert _is_valid_transition(frm, to) is expected


async def test_transition_lifecycle_silently_rejects_backward_writes(
    client, test_workspace, db_url_superuser,
):
    """End-to-end: bring a file to `ready`, then ask transition_lifecycle
    to walk it back to `entities_extracting`. The function returns the
    current `ready` state, the column stays `ready`, no audit row lands."""
    from kb.db.pool import open_connection
    from kb.domain.files import transition_lifecycle

    fid = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        await conn.execute(
            "INSERT INTO files (id, workspace_id, name, mime_type, "
            "size_bytes, content_sha, object_key, lifecycle_state) "
            "VALUES (%s, %s, 'guard-test.pdf', 'application/pdf', "
            "0, repeat('a', 64), 'guard-test/key', 'ready')",
            (fid, test_workspace),
        )

    async with open_connection(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)",
            (test_workspace,),
        )
        async with conn.transaction():
            from_state = await transition_lifecycle(
                conn, workspace_id=test_workspace, file_id=fid,
                to_state="entities_extracting",
                event="backward_re_extract_attempt",
            )

    # Refuse signal: from_state echoed back as the unchanged current state.
    assert from_state == "ready"

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT lifecycle_state FROM files WHERE id = %s", (fid,),
        )
        assert (await cur.fetchone())[0] == "ready"
        # No audit-event row for the refused transition.
        cur = await conn.execute(
            "SELECT count(*)::int FROM file_lifecycle "
            "WHERE file_id = %s AND event = 'backward_re_extract_attempt'",
            (fid,),
        )
        assert (await cur.fetchone())[0] == 0
