"""One-shot backfill — populate proposed_fields.value_numeric +
value_currency from value_text using kb.extraction.value_normalize.

Run once after migration 0045 to retrofit existing rows. New
inserts get these columns automatically via insert_proposed_field.

Usage:
    uv run python scripts/backfill_value_numeric.py \\
        --workspace c0000000-0000-0000-0000-000000000001 \\
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
from kb.extraction.value_normalize import normalize_value  # noqa: E402


async def main(workspace_id: str, dry_run: bool) -> int:
    settings = get_settings()
    async with open_connection(settings.database_url) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)",
            (workspace_id,),
        )
        cur = await conn.execute(
            "SELECT id::text, value_text "
            "  FROM proposed_fields "
            " WHERE workspace_id = %s "
            "   AND value_text IS NOT NULL "
            "   AND value_numeric IS NULL",
            (workspace_id,),
        )
        rows = await cur.fetchall()

    print(f"# scanning {len(rows)} rows with value_text but no value_numeric…")
    updates = []
    sample_show = 0
    for rid, vtext in rows:
        nv = normalize_value(str(vtext))
        if nv is None:
            continue
        updates.append((str(rid), nv.numeric, nv.currency))
        if sample_show < 10:
            print(f"  parsed {vtext!r:40s} -> {nv.numeric:>14g} {nv.currency or ''}")
            sample_show += 1

    print()
    print(f"# {len(updates)} rows would be updated (of {len(rows)} scanned)")

    if dry_run:
        return 0
    if not updates:
        return 0

    async with open_connection(settings.database_url) as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)",
                (workspace_id,),
            )
            for rid, numeric, currency in updates:
                await conn.execute(
                    "UPDATE proposed_fields "
                    "   SET value_numeric = %s, value_currency = %s "
                    " WHERE id = %s::uuid",
                    (numeric, currency, rid),
                )
    print(f"# applied {len(updates)} updates")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main(args.workspace, args.dry_run)))
