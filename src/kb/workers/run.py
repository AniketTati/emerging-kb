"""Worker entrypoint — `python -m kb.workers.run`.

Kept thin: delegates to Procrastinate's CLI worker. The docker-compose worker
container uses `procrastinate --app=kb.workers.app.app worker` directly so we
also need this module-level script for ad-hoc invocations.
"""

from __future__ import annotations

import asyncio

from kb.workers.app import app


async def _main() -> None:
    async with app.open_async():
        await app.run_worker_async()


if __name__ == "__main__":
    asyncio.run(_main())
