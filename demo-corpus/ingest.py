"""Ingest the demo corpus into the running KB stack.

Usage:
    uv run python demo-corpus/ingest.py

Reads files in `demo-corpus/`, POSTs each to /files via the existing
WA-16 loader, then polls /dashboard/summary until all are 'ready'.

Configurable via env:
    KB_API_BASE_URL   default http://localhost:8000
    KB_DEMO_WORKSPACE default 00000000-0000-0000-0000-000000000001
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import httpx

from kb.eval.loader import ingest_directory


BASE = os.environ.get("KB_API_BASE_URL", "http://localhost:8000")
WORKSPACE = os.environ.get(
    "KB_DEMO_WORKSPACE", "00000000-0000-0000-0000-000000000001",
)


async def main() -> int:
    corpus_dir = Path(__file__).parent
    # Don't pick up the build script / ingest script themselves.
    payload_files = [
        p for p in corpus_dir.iterdir()
        if p.is_file() and p.suffix.lower() in {
            ".pdf", ".txt", ".md", ".eml", ".xlsx",
        }
    ]
    print(
        f"[ingest] uploading {len(payload_files)} files to {BASE} "
        f"workspace={WORKSPACE}",
    )
    for p in payload_files:
        print(f"  - {p.name} ({p.stat().st_size} bytes)")

    async with httpx.AsyncClient(
        base_url=BASE, timeout=httpx.Timeout(60.0),
    ) as client:
        report = await ingest_directory(
            client, corpus_dir,
            workspace_id=WORKSPACE,
            recursive=False, concurrency=2,
        )

    print()
    print(report.summary())
    for r in report.items:
        marker = "ok" if r.status in ("ok", "duplicate") else "FAIL"
        print(f"  [{marker:4s}] {r.name:30s} status={r.status} "
              f"http={r.http_status} id={r.file_id or '-'} "
              f"{r.detail or ''}")

    if report.errors > 0:
        return 1

    print()
    print("[ingest] polling /dashboard/summary for files_total + by_lifecycle …")
    # The pipeline takes ~60–120s per file; allow generous time.
    deadline_s = 600
    interval_s = 5
    elapsed = 0
    async with httpx.AsyncClient(
        base_url=BASE, timeout=httpx.Timeout(30.0),
    ) as client:
        while elapsed < deadline_s:
            try:
                resp = await client.get(
                    "/dashboard/summary",
                    headers={"X-Test-Workspace": WORKSPACE},
                )
                summary = resp.json()
            except Exception as exc:  # noqa: BLE001
                print(f"  poll error: {exc}")
                await asyncio.sleep(interval_s)
                elapsed += interval_s
                continue

            by_state = {
                e["label"]: e["count"]
                for e in summary.get("files_by_lifecycle") or []
            }
            ready = by_state.get("ready", 0)
            failed = by_state.get("failed", 0)
            in_flight = summary.get("files_total", 0) - ready - failed
            print(
                f"  t={elapsed:>3d}s  total={summary.get('files_total')} "
                f"ready={ready} failed={failed} in_flight={in_flight}",
            )
            if in_flight == 0:
                print("[ingest] done.")
                # Print one-line snapshot of doc-type distribution.
                doc_types = {
                    e["label"]: e["count"]
                    for e in summary.get("files_by_doc_type") or []
                }
                print(f"[ingest] doc_types: {doc_types}")
                print(f"[ingest] conflicts_open: {summary.get('conflicts_open')}")
                return 0 if failed == 0 else 2
            await asyncio.sleep(interval_s)
            elapsed += interval_s

    print(f"[ingest] timeout after {deadline_s}s; some files still in flight.")
    return 3


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
