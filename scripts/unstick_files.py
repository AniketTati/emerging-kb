"""One-shot recovery: advance files stuck mid-pipeline.

The forward-only lifecycle guard (in src/kb/domain/files.py) prevents
NEW stuck-doc bugs going forward, but we still have 19 files that
got clobbered before the fix landed. This script picks each one up
and runs the appropriate post-state worker impl directly so they
end at 'ready'.

State → resume-task map:
  entities_extracting  → extract_schema_entities_file_impl
  identity_resolving   → resolve_identities_file_impl
  units_extracting     → extract_atomic_units_file_impl
  fields_extracting    → extract_fields_file_impl
  mentions_extracting  → extract_mentions_file_impl
  raptor_building      → raptor_build_file_impl
  embedded             → raptor_build_file_impl  (legacy alias)
  contextualized       → embed_file_impl
  chunked              → contextualize_file_impl
  parsed               → chunk_file_impl

Usage:
    source scripts/dev_env.sh
    uv run python scripts/unstick_files.py            # dry-run, list stuck
    uv run python scripts/unstick_files.py --run      # actually advance them

The script is idempotent — running it twice on a now-`ready` file is a noop.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Awaitable, Callable

import psycopg


# Each stuck state maps to the worker_impl function that should advance it.
# When the impl returns, the file should be one state forward; we loop
# until it lands at 'ready' / 'failed' / 'deleted' or stops moving.
_RESUME_MAP: dict[str, str] = {
    "entities_extracting": "extract_schema_entities_file_impl",
    "identity_resolving":  "resolve_identities_file_impl",
    "units_extracting":    "extract_atomic_units_file_impl",
    "fields_extracting":   "extract_fields_file_impl",
    "mentions_extracting": "extract_mentions_file_impl",
    "raptor_building":     "raptor_build_file_impl",
    "embedded":            "raptor_build_file_impl",
    "contextualized":      "embed_file_impl",
    "chunked":             "contextualize_file_impl",
    "parsed":              "chunk_file_impl",
}

_TERMINAL: set[str] = {"ready", "failed", "deleted"}


async def _list_stuck(conn) -> list[tuple[str, str, str]]:
    cur = await conn.execute(
        "SELECT id::text, name, lifecycle_state FROM files "
        "WHERE lifecycle_state NOT IN ('ready','failed','deleted','queued','parsing') "
        "ORDER BY updated_at"
    )
    return await cur.fetchall()


async def _get_state(conn, file_id: str) -> str | None:
    cur = await conn.execute(
        "SELECT lifecycle_state FROM files WHERE id = %s", (file_id,)
    )
    row = await cur.fetchone()
    return row[0] if row else None


async def _resume_one(file_id: str, name: str, state: str) -> str:
    """Advance one file by running its resume-task impl. Loop forward
    until the file reaches a terminal state or stops moving."""
    from kb.workers import tasks as _tasks

    last_state = state
    iterations = 0
    while last_state not in _TERMINAL and iterations < 12:
        resume_fn_name = _RESUME_MAP.get(last_state)
        if resume_fn_name is None:
            print(f"  ! no resume function for state={last_state}; bailing")
            return last_state
        fn: Callable[[str], Awaitable[None]] = getattr(_tasks, resume_fn_name)
        print(f"  → running {resume_fn_name}({file_id[:8]}…) from state={last_state}")
        try:
            await fn(file_id)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {resume_fn_name} raised: {type(exc).__name__}: {exc}")
            return last_state

        # Re-read state from DB
        db_url = os.environ["KB_DATABASE_URL"]
        async with await psycopg.AsyncConnection.connect(db_url) as conn:
            new_state = await _get_state(conn, file_id)
        if new_state is None:
            print(f"  ! {file_id[:8]}… disappeared")
            return "deleted"
        if new_state == last_state:
            print(f"  - state didn't advance ({new_state}); bailing")
            return new_state
        last_state = new_state
        iterations += 1
    return last_state


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run", action="store_true",
        help="Actually run resume tasks (omitted = dry-run / list only).",
    )
    args = parser.parse_args(argv)

    db_url = os.environ.get("KB_DATABASE_URL")
    if not db_url:
        print("KB_DATABASE_URL not set — source scripts/dev_env.sh", file=sys.stderr)
        return 2

    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        stuck = await _list_stuck(conn)

    if not stuck:
        print("No stuck files.")
        return 0

    print(f"Found {len(stuck)} stuck file(s):")
    for fid, name, state in stuck:
        print(f"  {fid[:8]}… {state:25s} {name}")

    if not args.run:
        print("\n(dry run — pass --run to advance them)")
        return 0

    print(f"\nAdvancing {len(stuck)} file(s) → ready ...")
    final_counts: dict[str, int] = {}
    for fid, name, state in stuck:
        print(f"\n{fid[:8]}… ({name}) starting at {state}:")
        final = await _resume_one(fid, name, state)
        final_counts[final] = final_counts.get(final, 0) + 1
        print(f"  ✓ final state: {final}")

    print()
    print("Summary by final state:")
    for k, n in sorted(final_counts.items()):
        print(f"  {k:25s} {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
