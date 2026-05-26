"""R5 — re-parse PDFs whose raw_pages.layout_json is missing the
per-element `elements` list (parsed before R5 captured layout).

Idempotent: files that already have `elements` set are skipped. Pulls
the original blob from MinIO via the existing parser path. Works in
batches of 1 file at a time (Docling is CPU-heavy) — typical demo
workspace (8 PDFs) takes ~2-3 minutes.

Usage:
    source scripts/dev_env.sh
    uv run python scripts/backfill_pdf_layout.py [--dry-run] [--file=NAME]
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import psycopg

from kb.parsers.docling_parser import DoclingParser


async def list_candidates(conn) -> list[tuple[str, str]]:
    """Return [(file_id, name), ...] for PDFs whose first raw_page is
    missing layout_json.elements (or has an empty elements list)."""
    cur = await conn.execute(
        """
        SELECT DISTINCT ON (f.id) f.id::text, f.name
          FROM files f
          JOIN raw_pages rp ON rp.file_id = f.id
         WHERE f.mime_type = 'application/pdf'
           AND f.lifecycle_state NOT IN ('failed', 'deleted', 'queued', 'parsing')
           AND (
             rp.layout_json IS NULL
             OR NOT (rp.layout_json ? 'elements')
             OR jsonb_array_length(rp.layout_json -> 'elements') = 0
           )
         ORDER BY f.id, rp.page_number
        """
    )
    return [(r[0], r[1]) for r in await cur.fetchall()]


async def fetch_blob(conn, file_id: str) -> bytes | None:
    """Pull the raw file bytes from MinIO via the object_key."""
    cur = await conn.execute(
        "SELECT object_key FROM files WHERE id = %s",
        (file_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    object_key = row[0]
    from kb.storage.files import get_file_bytes

    return get_file_bytes(object_key)


async def update_page_layout(
    conn, file_id: str, page_number: int, layout: dict,
) -> None:
    """Merge new `elements` into raw_pages.layout_json without clobbering
    other existing keys (size, ocr_model, etc.)."""
    await conn.execute(
        """
        UPDATE raw_pages
           SET layout_json = COALESCE(layout_json, '{}'::jsonb) || %s::jsonb
         WHERE file_id = %s AND page_number = %s
        """,
        (json.dumps({"elements": layout.get("elements", [])}), file_id, page_number),
    )


async def main() -> None:
    dry_run = "--dry-run" in sys.argv
    only = None
    for arg in sys.argv:
        if arg.startswith("--file="):
            only = arg.split("=", 1)[1]

    db_url = os.environ.get("KB_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not db_url:
        raise SystemExit("KB_DATABASE_URL / DATABASE_URL must be set")

    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        files = await list_candidates(conn)

    if only:
        files = [f for f in files if f[1] == only]

    print(f"Found {len(files)} PDFs needing layout backfill")
    if dry_run:
        for fid, name in files:
            print(f"  [dry-run] {name}  id={fid[:8]}")
        return

    parser = DoclingParser()
    for fid, name in files:
        async with await psycopg.AsyncConnection.connect(db_url) as conn:
            blob = await fetch_blob(conn, fid)
        if blob is None:
            print(f"  [skip] {name} — no blob")
            continue
        try:
            parsed = await parser.parse(blob, file_id=fid, workspace_id="")
        except Exception as e:
            print(f"  [error] {name} — {type(e).__name__}: {e}")
            continue
        async with await psycopg.AsyncConnection.connect(db_url) as conn:
            total_elems = 0
            for p in parsed.pages:
                await update_page_layout(
                    conn, fid, p.page_number, p.layout_json or {},
                )
                total_elems += len((p.layout_json or {}).get("elements", []))
            await conn.commit()
        print(f"  [done] {name:<40} → {total_elems} elements across {len(parsed.pages)} pages")


if __name__ == "__main__":
    asyncio.run(main())
