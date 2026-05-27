"""Upload a list of files to a workspace + poll until ready.

Usage:
    uv run python scripts/ingest_batch.py \
        --workspace <uuid> \
        --root demo-corpus/domains/construction/docs \
        file1.md file2.md ...

Or pass --batch-from <yaml-key> to slice the manifest later.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import httpx

# Allow `uv run python scripts/...` without -m by adding src/ to sys.path.
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from kb.eval.loader import ingest_file  # noqa: E402


BASE = os.environ.get("KB_API_BASE_URL", "http://localhost:8000")


async def upload_all(
    workspace_id: str, root: Path, names: list[str],
) -> tuple[int, int, list[str]]:
    ok = 0
    dup = 0
    failed: list[str] = []
    async with httpx.AsyncClient(
        base_url=BASE, timeout=httpx.Timeout(120.0),
    ) as client:
        for name in names:
            p = root / name
            r = await ingest_file(client, p, workspace_id=workspace_id)
            tag = {
                "ok": "NEW ",
                "duplicate": "DUP ",
            }.get(r.status, "FAIL")
            print(f"  [{tag}] {name:55s} http={r.http_status} id={r.file_id or '-'} {r.detail or ''}")
            if r.status == "ok":
                ok += 1
            elif r.status == "duplicate":
                dup += 1
            else:
                failed.append(name)
    return ok, dup, failed


async def poll_until_ready(
    workspace_id: str, expected_total: int,
    deadline_s: int = 900, interval_s: int = 6,
) -> dict:
    """Poll /dashboard/summary until every file in the workspace is ready/failed."""
    elapsed = 0
    last = {}
    async with httpx.AsyncClient(
        base_url=BASE, timeout=httpx.Timeout(30.0),
    ) as client:
        while elapsed < deadline_s:
            try:
                resp = await client.get(
                    "/dashboard/summary",
                    headers={"X-Test-Workspace": workspace_id},
                )
                last = resp.json()
            except Exception as exc:  # noqa: BLE001
                print(f"  poll error: {exc}")
                await asyncio.sleep(interval_s)
                elapsed += interval_s
                continue
            by = {e["label"]: e["count"] for e in last.get("files_by_lifecycle") or []}
            total = last.get("files_total") or 0
            ready = by.get("ready", 0)
            failed = by.get("failed", 0)
            inflight = total - ready - failed
            print(
                f"  t={elapsed:>3d}s  total={total} ready={ready} failed={failed} in_flight={inflight}",
            )
            if total >= expected_total and inflight <= 0:
                return last
            await asyncio.sleep(interval_s)
            elapsed += interval_s
    print("  TIMEOUT — returning last summary")
    return last


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--no-poll", action="store_true")
    ap.add_argument("files", nargs="+")
    args = ap.parse_args()

    print(f"[ingest_batch] workspace={args.workspace} root={args.root} files={len(args.files)}")
    ok, dup, failed = asyncio.run(
        upload_all(args.workspace, args.root, args.files),
    )
    print()
    print(f"[ingest_batch] upload done: new={ok} dup={dup} failed={len(failed)}")
    if failed:
        for f in failed:
            print(f"  FAILED: {f}")

    if args.no_poll:
        return 1 if failed else 0

    print()
    print("[ingest_batch] polling /dashboard/summary until pipeline is idle …")
    asyncio.run(poll_until_ready(args.workspace, expected_total=ok + dup))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
