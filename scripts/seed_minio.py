#!/usr/bin/env python3
"""Restore source-file blobs into MinIO via the S3 API, keyed by content sha.

The blob store keys every object as `raw_files/<sha256(bytes)>` — the same key
kb.storage.files and GET /files/:id/blob (the source viewer) use. Because the
key is just the content hash, blobs are reconstructed directly from the
committed source corpus — no separate blob artifact, no dependence on MinIO's
on-disk format. Run inside a container that has `kb` importable, pointed at the
seed corpus copied in via `docker cp`:

    python scripts/seed_minio.py /tmp/seedsrc

Walks the dir recursively, uploads each document file keyed by its sha256.
Idempotent: objects that already exist are skipped.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from kb.storage import get_minio_client
from kb.storage.files import KB_BUCKET, RAW_PREFIX, ensure_bucket

DOC_EXTS = {".md", ".eml", ".pdf", ".xlsx", ".xls", ".csv", ".txt", ".docx", ".pptx"}


def main(src: str) -> int:
    root = Path(src)
    if not root.is_dir():
        print(f"[seed_minio] no source dir at {src} — nothing to restore")
        return 0
    ensure_bucket()
    client = get_minio_client()
    files = sorted(
        p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in DOC_EXTS
    )
    uploaded = skipped = 0
    for p in files:
        sha = hashlib.sha256(p.read_bytes()).hexdigest()
        key = f"{RAW_PREFIX}{sha}"
        try:
            client.stat_object(KB_BUCKET, key)
            skipped += 1
            continue
        except Exception:  # noqa: BLE001 — stat raises when the object is absent
            pass
        client.fput_object(KB_BUCKET, key, str(p))
        uploaded += 1
    print(f"[seed_minio] uploaded={uploaded} skipped(existing)={skipped} scanned={len(files)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/seedsrc"))
