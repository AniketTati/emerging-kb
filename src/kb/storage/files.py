"""MinIO file-blob helpers — put / get / key derivation.

Phase 2a. Per build_tracker §5.5 decision #1: MinIO holds bytes under
`raw_files/<sha256>`; Postgres holds metadata.
"""

from __future__ import annotations

import hashlib
from io import BytesIO

from kb.storage import get_minio_client


KB_BUCKET = "kb-files"
RAW_PREFIX = "raw_files/"


def key_for_sha(content_sha: str) -> str:
    """Canonical object key for raw file bytes."""
    return f"{RAW_PREFIX}{content_sha}"


def sha256_hex(file_bytes: bytes) -> str:
    return hashlib.sha256(file_bytes).hexdigest()


def ensure_bucket() -> None:
    """Idempotently create the `kb-files` bucket if missing."""
    client = get_minio_client()
    if not client.bucket_exists(KB_BUCKET):
        client.make_bucket(KB_BUCKET)


def put_file_bytes(content_sha: str, file_bytes: bytes, *, mime_type: str) -> str:
    """Upload raw bytes to MinIO under `raw_files/<sha>`. Returns the object key.

    Idempotent: if the object already exists, MinIO PUT overwrites with the
    same content (sha-keyed, so byte-identical).
    """
    ensure_bucket()
    client = get_minio_client()
    key = key_for_sha(content_sha)
    client.put_object(
        KB_BUCKET, key, BytesIO(file_bytes),
        length=len(file_bytes), content_type=mime_type,
    )
    return key


def get_file_bytes(object_key: str) -> bytes:
    """Fetch raw bytes from MinIO. Raises on missing."""
    client = get_minio_client()
    resp = client.get_object(KB_BUCKET, object_key)
    try:
        return resp.read()
    finally:
        resp.close()
        resp.release_conn()


def object_exists(object_key: str) -> bool:
    """True iff `object_key` exists in the KB bucket. Used by Mode-B uploads
    to validate the caller's referenced key resolves to real bytes."""
    client = get_minio_client()
    try:
        client.stat_object(KB_BUCKET, object_key)
        return True
    except Exception:
        return False
