"""One-shot backfill for files already ingested before the
'L3 status enum → file.doc_status' projection landed.

Scans proposed_fields for `status` / `doc_status` rows whose value
matches a canonical doc-status enum, and applies set_doc_status to
the parent file row.

Usage:
    uv run python scripts/backfill_doc_status.py \
        [--workspace c0000000-0000-0000-0000-000000000001] \
        [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from kb.config import get_settings  # noqa: E402
from kb.db.pool import open_connection  # noqa: E402
from kb.domain.conflicts import DOC_STATUSES, set_doc_status  # noqa: E402


async def main(workspace_id: str | None, dry_run: bool) -> int:
    settings = get_settings()
    async with open_connection(settings.database_url) as conn:
        async with conn.transaction():
            if workspace_id:
                await conn.execute(
                    "SELECT set_config('app.workspace_id', %s, true)",
                    (workspace_id,),
                )
            # Pull (file_id, file_name, status_value, current_doc_status)
            # for every file in scope where a canonical status proposed_field exists.
            params: tuple
            scope_sql = "AND f.workspace_id = %s" if workspace_id else ""
            if workspace_id:
                params = (workspace_id,)
            else:
                params = ()
            cur = await conn.execute(
                f"""
                SELECT f.id::text, f.name, lower(trim(pf.value_text)) AS new_status, f.doc_status
                FROM proposed_fields pf
                JOIN files f ON f.id = pf.file_id
                WHERE lower(pf.field_name) IN ('status', 'doc_status')
                  AND pf.value_text IS NOT NULL
                  AND lower(trim(pf.value_text)) IN ({','.join("%s" for _ in DOC_STATUSES)})
                  {scope_sql}
                ORDER BY f.created_at ASC, pf.id ASC
                """,
                (*DOC_STATUSES, *params),
            )
            rows = await cur.fetchall()

        seen: dict[str, tuple[str, str, str]] = {}
        for fid, name, new_status, current in rows:
            # First canonical status per file wins (matches the worker logic).
            seen.setdefault(fid, (name, new_status, current))

        to_update = [
            (fid, name, new, cur)
            for fid, (name, new, cur) in seen.items()
            if new != cur
        ]
        nothing_to_do = [
            (fid, name, new, cur)
            for fid, (name, new, cur) in seen.items()
            if new == cur
        ]

        print(f"# files with a canonical status proposed_field: {len(seen)}")
        print(f"#   already in sync: {len(nothing_to_do)}")
        print(f"#   need update:     {len(to_update)}")

        for fid, name, new, cur in to_update:
            print(f"  {fid[:8]}  {name:55s}  {cur} -> {new}")

        if dry_run or not to_update:
            print()
            print("DRY-RUN — no changes applied." if dry_run else "No updates needed.")
            return 0

        async with conn.transaction():
            if workspace_id:
                await conn.execute(
                    "SELECT set_config('app.workspace_id', %s, true)",
                    (workspace_id,),
                )
            applied = 0
            for fid, _name, new, _cur in to_update:
                ok = await set_doc_status(conn, file_id=fid, new_status=new)
                if ok:
                    applied += 1
            print()
            print(f"applied {applied} of {len(to_update)} updates")
        return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main(args.workspace, args.dry_run)))
