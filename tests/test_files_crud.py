"""Phase 2a — files upload + dedup + read + delete + RLS (api_contracts §5.5–§5.9).

RED at G3: imports from `kb.api.files` + `kb.domain.files` land at G4.

Spec: tests/specs/phase_2a.md §4.1.
"""

from __future__ import annotations

import hashlib
import uuid

import pytest


pytestmark = pytest.mark.asyncio


# A minimal valid PDF byte string (≈ 500 bytes) for upload tests.
# A real fixture file at tests/fixtures/tiny.pdf lands at G4; this in-memory
# stub is enough to exercise the HTTP contract (mime detection + content_sha +
# dedup). Parser tests use the fixture file.
_TINY_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
    b"2 0 obj << /Type /Pages /Count 1 /Kids [3 0 R] >> endobj\n"
    b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
    b"/Contents 4 0 R >> endobj\n"
    b"4 0 obj << /Length 44 >> stream\n"
    b"BT /F1 12 Tf 50 700 Td (Hello, World!) Tj ET\n"
    b"endstream\nendobj\n"
    b"xref\n0 5\n0000000000 65535 f\n0000000009 00000 n\n"
    b"0000000056 00000 n\n0000000109 00000 n\n0000000180 00000 n\n"
    b"trailer << /Size 5 /Root 1 0 R >>\nstartxref\n280\n%%EOF\n"
)


@pytest.fixture
def test_workspace() -> str:
    return str(uuid.uuid4())


def headers(workspace: str, *, idempotency_key: str | None = None) -> dict[str, str]:
    h = {"X-Test-Workspace": workspace}
    if idempotency_key is not None:
        h["Idempotency-Key"] = idempotency_key
    return h


# ===========================================================================
# §5.5 — POST /files (two modes + idempotency + validation)
# ===========================================================================


async def test_post_creates_file_via_multipart(client, test_workspace):
    resp = await client.post(
        "/files",
        files={"file": ("acme.pdf", _TINY_PDF, "application/pdf")},
        data={"name": "acme contract"},
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert set(body.keys()) == {
        "id", "name", "content_sha", "mime_type", "size_bytes",
        "doc_type", "lifecycle_state",
        "created_at", "updated_at",
    }
    assert body["mime_type"] == "application/pdf"
    assert body["size_bytes"] == len(_TINY_PDF)
    assert body["content_sha"] == hashlib.sha256(_TINY_PDF).hexdigest()
    assert body["lifecycle_state"] == "queued"
    assert body["doc_type"] is None
    assert "workspace_id" not in body
    assert "object_key" not in body


async def test_post_creates_file_via_json_minio_key(client, test_workspace, minio_container):
    """Mode B — pre-staged object in MinIO referenced by key.

    Test pre-stages the file via the test-MinIO client, then POST /files with
    the object_key reference.
    """
    from minio import Minio

    cfg = minio_container.get_config()
    minio_client = Minio(
        cfg["endpoint"], access_key=cfg["access_key"],
        secret_key=cfg["secret_key"], secure=False,
    )
    sha = hashlib.sha256(_TINY_PDF).hexdigest()
    bucket = "kb-files"
    if not minio_client.bucket_exists(bucket):
        minio_client.make_bucket(bucket)
    from io import BytesIO
    minio_client.put_object(
        bucket, f"raw_files/{sha}", BytesIO(_TINY_PDF), length=len(_TINY_PDF),
        content_type="application/pdf",
    )

    resp = await client.post(
        "/files",
        json={"minio_object_key": f"raw_files/{sha}", "name": "preuploaded.pdf"},
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "preuploaded.pdf"
    assert body["content_sha"] == sha


async def test_post_files_accepts_parser_gemini_query_param(client, test_workspace):
    """Phase 2c §5.6.1 #11: POST /files?parser=gemini accepted → 201; the
    forced parser value is persisted into the worker task arg so the
    dispatcher routes to Gemini OCR regardless of `KB_PARSER_STRATEGY`."""
    resp = await client.post(
        "/files?parser=gemini",
        files={"file": ("scanned.pdf", _TINY_PDF, "application/pdf")},
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["lifecycle_state"] == "queued"
    # The response shape is unchanged, but the lifecycle history's initial
    # 'upload' event (or a follow-up worker event) must record the override.
    fid = body["id"]
    get_resp = await client.get(f"/files/{fid}", headers=headers(test_workspace))
    upload_event = get_resp.json()["lifecycle"][0]
    assert upload_event["payload"].get("forced_parser") == "gemini", (
        f"expected forced_parser=gemini in upload event payload; got {upload_event}"
    )


async def test_post_files_rejects_bogus_parser_value(client, test_workspace):
    """Phase 2c §5.6.1 #11: invalid `?parser=` value → 400 invalid-parser-override.
    Valid values are: auto, docling, gemini."""
    resp = await client.post(
        "/files?parser=bogus",
        files={"file": ("x.pdf", _TINY_PDF, "application/pdf")},
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["type"].endswith("/invalid-parser-override")


async def test_post_requires_idempotency_key(client, test_workspace):
    resp = await client.post(
        "/files",
        files={"file": ("a.pdf", _TINY_PDF, "application/pdf")},
        headers={"X-Test-Workspace": test_workspace},  # no Idempotency-Key
    )
    assert resp.status_code == 400
    assert resp.json()["type"].endswith("/missing-idempotency-key")


async def test_post_rejects_payload_too_large(client, test_workspace, monkeypatch):
    """KB_MAX_UPLOAD_BYTES lets tests trigger the 413 path with a tiny budget."""
    monkeypatch.setenv("KB_MAX_UPLOAD_BYTES", "256")
    # Re-clear settings so the env override takes effect
    from kb.config import get_settings
    get_settings.cache_clear()

    big = b"x" * 1024  # 1 KB > 256 byte limit
    resp = await client.post(
        "/files",
        files={"file": ("big.pdf", big, "application/pdf")},
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 413
    assert resp.json()["type"].endswith("/payload-too-large")


async def test_post_rejects_unsupported_mime(client, test_workspace):
    """Phase 2a accepts only application/pdf; .txt → 415."""
    resp = await client.post(
        "/files",
        files={"file": ("notes.txt", b"hello", "text/plain")},
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 415
    assert resp.json()["type"].endswith("/unsupported-media-type")


async def test_post_content_hash_dedup_returns_existing(client, test_workspace):
    """Same content_sha twice → 200 (not 201) with the SAME id + X-Dedup-Reason header."""
    r1 = await client.post(
        "/files",
        files={"file": ("a.pdf", _TINY_PDF, "application/pdf")},
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert r1.status_code == 201
    original_id = r1.json()["id"]

    r2 = await client.post(
        "/files",
        files={"file": ("a-renamed.pdf", _TINY_PDF, "application/pdf")},
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert r2.status_code == 200, (
        "content-hash dedup must return 200 (not 201, not 409)"
    )
    assert r2.json()["id"] == original_id
    assert r2.headers.get("X-Dedup-Reason") == "content-hash"


# ===========================================================================
# §5.6 — GET /files (list)
# ===========================================================================


async def test_get_list_returns_workspace_files(client, test_workspace):
    for i in range(3):
        await client.post(
            "/files",
            files={"file": (f"f{i}.pdf", _TINY_PDF + bytes([i]), "application/pdf")},
            headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
        )
    resp = await client.get("/files", headers=headers(test_workspace))
    body = resp.json()
    assert body["total"] == 3


# ===========================================================================
# §5.7 — GET /files/:id (with lifecycle history)
# ===========================================================================


async def test_get_one_includes_lifecycle_history(client, test_workspace):
    create = await client.post(
        "/files",
        files={"file": ("hist.pdf", _TINY_PDF, "application/pdf")},
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    fid = create.json()["id"]
    resp = await client.get(f"/files/{fid}", headers=headers(test_workspace))
    body = resp.json()
    assert "lifecycle" in body
    assert len(body["lifecycle"]) >= 1
    first = body["lifecycle"][0]
    assert first["to_state"] == "queued"
    assert first["event"] == "upload"


# ===========================================================================
# §5.9 — DELETE soft + RLS
# ===========================================================================


async def test_delete_soft_deletes(client, test_workspace, db_superuser):
    create = await client.post(
        "/files",
        files={"file": ("doomed.pdf", _TINY_PDF, "application/pdf")},
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    fid = create.json()["id"]
    r = await client.delete(f"/files/{fid}", headers=headers(test_workspace))
    assert r.status_code == 204
    # Subsequent GET → 404
    g = await client.get(f"/files/{fid}", headers=headers(test_workspace))
    assert g.status_code == 404
    # Superuser confirms row still exists with lifecycle_state='deleted'
    row = await db_superuser.fetchrow(
        "SELECT lifecycle_state FROM files WHERE id = %s", uuid.UUID(fid),
    )
    assert row[0] == "deleted"


async def test_files_isolated_across_workspaces(client, test_workspace):
    workspace_b = str(uuid.uuid4())
    await client.post(
        "/files",
        files={"file": ("private.pdf", _TINY_PDF, "application/pdf")},
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    resp = await client.get("/files", headers=headers(workspace_b))
    assert resp.json()["total"] == 0


# ---------------------------------------------------------------------------
# Phase 2b additions — xlsx + email mime whitelist + magic sniff
# (api_contracts §5.5 415 row widened, build_tracker §5.6 decisions #6, #10, #11)
# ---------------------------------------------------------------------------


from pathlib import Path

_FIXTURE_DIR = Path(__file__).parent / "fixtures"

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


async def test_post_xlsx_creates_file(client, test_workspace):
    """POST tiny.xlsx with the right Content-Type → 201 + queued."""
    xlsx_bytes = (_FIXTURE_DIR / "tiny.xlsx").read_bytes()
    resp = await client.post(
        "/files",
        files={"file": ("test.xlsx", xlsx_bytes, _XLSX_MIME)},
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["mime_type"] == _XLSX_MIME
    assert body["lifecycle_state"] == "queued"


async def test_post_email_creates_file(client, test_workspace):
    """POST tiny.eml with message/rfc822 → 201 + queued."""
    eml_bytes = (_FIXTURE_DIR / "tiny.eml").read_bytes()
    resp = await client.post(
        "/files",
        files={"file": ("test.eml", eml_bytes, "message/rfc822")},
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["mime_type"] == "message/rfc822"


# ===========================================================================
# POST /files/:id/re-extract  (Upload "Re-extract" / failed-row recovery)
# ===========================================================================


async def test_re_extract_404_for_missing_file(client, test_workspace):
    fake_id = str(uuid.uuid4())
    resp = await client.post(
        f"/files/{fake_id}/re-extract",
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    # get_file raises an HTTPException with 404 when the row is absent.
    assert resp.status_code == 404


async def test_re_extract_default_stage_enqueues_kv_tables(
    client, test_workspace, monkeypatch,
):
    """`stage` defaults to 'extraction' → defers extract_kv_tables_file
    (the post-collapse replacement for the legacy extract_fields_file +
    extract_atomic_units_file pair). The downstream chain
    (kv_tables → schema_entities → identities → ready) runs
    automatically. Returns 202 + the deferred task name."""
    # Seed a file via the normal POST path so RLS + lifecycle are real.
    resp = await client.post(
        "/files",
        files={"file": ("re.pdf", _TINY_PDF, "application/pdf")},
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 201, resp.text
    file_id = resp.json()["id"]

    # Stub procrastinate.configure_task → defer_async so the test
    # doesn't require a running worker.
    deferred: list[str] = []

    class _FakeDeferred:
        def __init__(self, name: str) -> None:
            self.name = name

        async def defer_async(self, **kwargs):  # noqa: ARG002
            deferred.append(self.name)
            return 1  # job id

    class _FakeApp:
        def configure_task(self, name: str):
            return _FakeDeferred(name)

    from kb.workers import tasks as _tasks_mod
    monkeypatch.setattr(_tasks_mod, "procrastinate_app", _FakeApp())

    resp = await client.post(
        f"/files/{file_id}/re-extract",
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["file_id"] == file_id
    assert body["stage"] == "extraction"
    # Post-KV+Tables collapse: only extract_kv_tables_file is
    # explicitly deferred. The chain (kv_tables → schema_entities →
    # identities → ready) runs automatically once the lifecycle is
    # rolled back to 'fields_extracting'.
    assert set(body["deferred"]) == {"extract_kv_tables_file"}
    assert set(deferred) == {"extract_kv_tables_file"}


async def test_re_extract_stage_parsing_enqueues_parse_only(
    client, test_workspace, monkeypatch,
):
    """`stage=parsing` re-runs the whole pipeline from parse_file —
    used by the Upload "Re-parse from scratch" recovery action."""
    resp = await client.post(
        "/files",
        files={"file": ("rp.pdf", _TINY_PDF, "application/pdf")},
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    file_id = resp.json()["id"]

    deferred: list[str] = []

    class _FakeDeferred:
        def __init__(self, name: str) -> None:
            self.name = name

        async def defer_async(self, **kwargs):  # noqa: ARG002
            deferred.append(self.name)
            return 1

    class _FakeApp:
        def configure_task(self, name: str):
            return _FakeDeferred(name)

    from kb.workers import tasks as _tasks_mod
    monkeypatch.setattr(_tasks_mod, "procrastinate_app", _FakeApp())

    resp = await client.post(
        f"/files/{file_id}/re-extract?stage=parsing",
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 202
    assert resp.json()["stage"] == "parsing"
    assert resp.json()["deferred"] == ["parse_file"]
    assert deferred == ["parse_file"]


async def test_re_extract_rejects_unknown_stage(
    client, test_workspace,
):
    """`stage=garbage` is a 422 from FastAPI's Query pattern match."""
    fake_id = str(uuid.uuid4())
    resp = await client.post(
        f"/files/{fake_id}/re-extract?stage=garbage",
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 422


async def test_post_octet_stream_xlsx_detected_via_magic(client, test_workspace):
    """POST xlsx with Content-Type=application/octet-stream → magic-sniff
    routes to the xlsx parser; response mime_type normalized to the xlsx mime
    (decision #6)."""
    xlsx_bytes = (_FIXTURE_DIR / "tiny.xlsx").read_bytes()
    resp = await client.post(
        "/files",
        files={"file": ("blob.bin", xlsx_bytes, "application/octet-stream")},
        headers=headers(test_workspace, idempotency_key=str(uuid.uuid4())),
    )
    assert resp.status_code == 201, resp.text
    # Server-side magic sniff should re-classify to xlsx mime
    assert resp.json()["mime_type"] == _XLSX_MIME
