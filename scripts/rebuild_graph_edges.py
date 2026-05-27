"""Re-trigger build_graph_file for every ready file in a workspace.

Use after fixing Bug G (the lineage-FK violation that previously killed
every graph_edges insert) to retroactively backfill graph_edges for
docs that ingested before the fix. Once graph_edges is populated,
T-mode (PPR multi-hop) and E-mode (single-entity boost) work end-to-end
for entity-centric queries like "Tell me about Mahalaxmi" or "Who are
the subcontractors of the contractor for the Acme datacentre?".

Usage:
    uv run python scripts/rebuild_graph_edges.py \
        --workspace c0000000-0000-0000-0000-000000000001
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
from kb.workers.app import app as procrastinate_app  # noqa: E402


async def main(workspace_id: str) -> int:
    settings = get_settings()
    async with open_connection(settings.database_url) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)",
            (workspace_id,),
        )
        cur = await conn.execute(
            "SELECT id::text FROM files "
            "WHERE workspace_id = %s AND lifecycle_state = 'ready' "
            "ORDER BY created_at",
            (workspace_id,),
        )
        file_ids = [r[0] for r in await cur.fetchall()]
    print(f"# deferring build_graph_file for {len(file_ids)} ready files")
    async with procrastinate_app.open_async():
        for fid in file_ids:
            await procrastinate_app.configure_task(
                name="build_graph_file"
            ).defer_async(file_id=fid)
    print("# deferred — worker will process. Check graph_edges count "
          "after worker drains.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main(args.workspace)))
