"""One-shot fix-up: delete all chains in a workspace then re-run
detect_doc_chain_file_impl for every ready file. Use after landing
the 'explicit chain via proposed_fields' patch to retroactively
correct chains that were formed by the fuzzy-title heuristic.

Usage:
    uv run python scripts/rerun_chain_detection.py \
        --workspace c0000000-0000-0000-0000-000000000001 \
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


async def main(workspace_id: str, dry_run: bool) -> int:
    settings = get_settings()
    from kb.workers.tasks import detect_doc_chain_file_impl  # noqa: E402

    async with open_connection(settings.database_url) as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)",
                (workspace_id,),
            )

            # Count existing chains / members in scope.
            cur = await conn.execute(
                "SELECT count(*) FROM doc_chains WHERE workspace_id = %s",
                (workspace_id,),
            )
            n_chains = (await cur.fetchone())[0]
            cur = await conn.execute(
                "SELECT count(*) FROM doc_chain_members WHERE workspace_id = %s",
                (workspace_id,),
            )
            n_members = (await cur.fetchone())[0]

            # List ready files we'll re-run.
            cur = await conn.execute(
                "SELECT id::text, name FROM files "
                "WHERE workspace_id = %s "
                "AND lifecycle_state NOT IN ('queued','parsing','failed','deleted') "
                "ORDER BY created_at ASC",
                (workspace_id,),
            )
            files = [(r[0], r[1]) for r in await cur.fetchall()]

        print(
            f"# workspace {workspace_id}: "
            f"{n_chains} chains, {n_members} members, "
            f"{len(files)} files to re-process",
        )

        if dry_run:
            print("DRY-RUN — nothing to do.")
            return 0

        # Wipe existing chains + members.
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)",
                (workspace_id,),
            )
            await conn.execute(
                "DELETE FROM doc_chain_members WHERE workspace_id = %s",
                (workspace_id,),
            )
            await conn.execute(
                "DELETE FROM doc_chains WHERE workspace_id = %s",
                (workspace_id,),
            )
            print(f"  wiped {n_chains} chains + {n_members} members")

    # Now re-run detection per file — sequentially, so explicit-chain
    # `upsert_chain` calls correctly find-or-create.
    print()
    print("re-running detect_doc_chain_file_impl for each file …")
    for fid, name in files:
        try:
            await detect_doc_chain_file_impl(fid)
            print(f"  done  {fid[:8]}  {name}")
        except Exception as exc:  # noqa: BLE001
            print(f"  FAIL  {fid[:8]}  {name}  {exc!r}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main(args.workspace, args.dry_run)))
