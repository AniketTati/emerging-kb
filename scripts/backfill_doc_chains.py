"""Re-run the doc-chain detector on every file that isn't already a
chain member.

Use after upgrading the detector (e.g. broadening the contract-doc-type
predicate per E4) so files uploaded under the old logic get retro-chained
without re-uploading. Idempotent — files already in any chain are skipped.

Usage:
    source scripts/dev_env.sh
    uv run python scripts/backfill_doc_chains.py
"""

from __future__ import annotations

import asyncio
import os

import psycopg

from kb.workers.tasks import detect_doc_chain_file_impl


async def list_unchained_files(conn) -> list[tuple[str, str, str]]:
    """Return (file_id, name, lifecycle_state) for files not in any chain."""
    # doc_chain_members has a composite PK (chain_id, doc_id) and no `id`
    # column — detect non-membership via `m.doc_id IS NULL` after the
    # LEFT JOIN. RLS is bypassed since this script runs as superuser.
    cur = await conn.execute(
        """
        SELECT f.id::text, f.name, f.lifecycle_state
          FROM files f
          LEFT JOIN doc_chain_members m ON m.doc_id = f.id
         WHERE m.doc_id IS NULL
           AND f.lifecycle_state NOT IN ('queued', 'parsing', 'failed', 'deleted')
         ORDER BY f.created_at ASC
        """
    )
    return [(r[0], r[1], r[2]) for r in await cur.fetchall()]


async def main() -> None:
    db_url = os.environ.get("KB_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not db_url:
        raise SystemExit("KB_DATABASE_URL / DATABASE_URL must be set")

    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        files = await list_unchained_files(conn)
        print(f"Found {len(files)} unchained files to evaluate")

    # Run the chain detector OUTSIDE the listing connection — it opens its
    # own (with proper RLS workspace_id binding per-file). Sequential to
    # keep concurrent INSERTs into doc_chains deterministic; the workspace
    # is small enough that this finishes in seconds.
    chained = 0
    for file_id, name, state in files:
        try:
            await detect_doc_chain_file_impl(file_id)
        except Exception as e:  # noqa: BLE001 — surface and continue
            print(f"  [skip] {name} ({file_id[:8]}…) — {type(e).__name__}: {e}")
            continue

        # Re-check post-call so we can report what landed.
        async with await psycopg.AsyncConnection.connect(db_url) as conn2:
            cur = await conn2.execute(
                "SELECT c.type, m.role "
                "FROM doc_chain_members m JOIN doc_chains c ON c.id = m.chain_id "
                "WHERE m.doc_id = %s",
                (file_id,),
            )
            row = await cur.fetchone()
        if row:
            chained += 1
            print(f"  [chained] {name} → {row[0]} (role: {row[1]})")
        else:
            print(f"  [no chain] {name}")

    print(f"\nDone: chained {chained} / {len(files)} files")


if __name__ == "__main__":
    asyncio.run(main())
