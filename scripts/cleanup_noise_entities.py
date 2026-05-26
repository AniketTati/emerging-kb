"""Backfill: delete canonical entities of NOISE types (CARDINAL /
QUANTITY / DATE / MONEY / ORDINAL / PERCENT / TIME).

These were inserted by the resolver before R4. The post-R4 worker skips
them, but existing workspaces still carry the bloat — this script
cleans them out. Idempotent.

Usage:
    source scripts/dev_env.sh
    uv run python scripts/cleanup_noise_entities.py [--dry-run]

The `mention_to_entity` links are CASCADE-deleted with the entity rows
(per migration 0019). The underlying `extracted_mentions` rows are kept
— they're still useful for chunk citations.
"""

from __future__ import annotations

import asyncio
import os
import sys

import psycopg

from kb.identity.resolve import NOISE_MENTION_TYPES


async def main() -> None:
    dry_run = "--dry-run" in sys.argv
    db_url = os.environ.get("KB_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not db_url:
        raise SystemExit("KB_DATABASE_URL / DATABASE_URL must be set")

    types_list = sorted(NOISE_MENTION_TYPES)
    print(f"Noise types to cleanup: {types_list}")

    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        # Count what we're about to remove (per type breakdown).
        cur = await conn.execute(
            "SELECT entity_type, count(*) FROM entities "
            "WHERE entity_type = ANY(%s) GROUP BY entity_type ORDER BY 2 DESC",
            (types_list,),
        )
        breakdown = await cur.fetchall()
        total = sum(n for _, n in breakdown)
        print(f"\nFound {total} noise entities:")
        for t, n in breakdown:
            print(f"  {t:<12} {n:>5}")

        # Also count downstream mention_to_entity rows that will cascade.
        cur = await conn.execute(
            "SELECT count(*) FROM mention_to_entity me "
            "JOIN entities e ON e.id = me.entity_id "
            "WHERE e.entity_type = ANY(%s)",
            (types_list,),
        )
        m2e_count = (await cur.fetchone())[0]
        print(f"  → {m2e_count} mention_to_entity links will cascade")

        if dry_run:
            print("\n[dry-run] no changes written. Re-run without --dry-run.")
            return
        if total == 0:
            print("\nNothing to clean. Done.")
            return

        # DELETE in one statement — the CASCADE constraint on
        # mention_to_entity.entity_id handles the link cleanup.
        await conn.execute(
            "DELETE FROM entities WHERE entity_type = ANY(%s)",
            (types_list,),
        )
        await conn.commit()
        print(f"\nDeleted {total} entities (+ {m2e_count} mention_to_entity links).")


if __name__ == "__main__":
    asyncio.run(main())
