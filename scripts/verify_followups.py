#!/usr/bin/env python3
"""Verify multi-turn FOLLOW-UPS (anaphora / context carry) end-to-end,
in-process. Pre-creates a COMMITTED session so `_persist_turn` (which opens
its own fresh connection) can find it — otherwise turn 1 never persists and
turn 2 has no history to resolve against.

  source scripts/dev_env.sh && python3 scripts/verify_followups.py
"""
from __future__ import annotations

import asyncio
import sys

from kb.config import get_settings
from kb.db.pool import open_connection
from kb.domain.chat_memory import create_session
from kb.query.orchestrator import Orchestrator

WS = sys.argv[1] if len(sys.argv) > 1 else "f0000000-0000-0000-0000-000000000001"

# (label, [(turn_query, expected_substring_in_answer_or_None)])
CONVS: list[tuple[str, list[tuple[str, str | None]]]] = [
    ("anaphora 'its' → HDFC loan rate", [
        ("tell me about the HDFC term loan from HDFC Bank to Acme Corp", None),
        ("what is its current interest rate?", "9.40"),
    ]),
    ("anaphora 'its' → audit report", [
        ("summarize the latest audit report", None),
        ("what were its main findings?", None),
    ]),
    ("carry scope → bank statements", [
        ("how many bank statements do I have?", None),
        ("what are the most unusual transactions in them?", None),
    ]),
]


async def _new_session(s) -> str:
    async with open_connection(s.app_database_url) as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)", (WS,))
            return await create_session(conn, workspace_id=WS, title="followup-verify")


async def _turn(orch, s, q: str, sid: str):
    async with open_connection(s.app_database_url) as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)", (WS,))
            return await orch.chat(q, workspace_id=WS, conn=conn, session_id=sid)


async def main() -> None:
    s = get_settings()
    orch = Orchestrator.make_default()
    for label, turns in CONVS:
        sid = await _new_session(s)
        print(f"\n=== {label}  (session {sid[:8]}) ===")
        for i, (q, expect) in enumerate(turns):
            r = await _turn(orch, s, q, sid)
            g = r.generation
            ans = (getattr(g, "answer", "") or "").strip()
            tag = f"T{i + 1}"
            print(f"  {tag} ask     : {q!r}")
            print(f"  {tag} resolved: {r.resolved_query!r}")
            if i > 0:
                rewrote = bool(r.resolved_query) and \
                    r.resolved_query.strip().lower() != q.strip().lower()
                print(f"  {tag} REWROTE follow-up: {rewrote}")
                print(f"  {tag} refused : {bool(getattr(g, 'refused', False))}")
                print(f"  {tag} answer  : {ans[:170].replace(chr(10), ' ')}")
                if expect:
                    print(f"  {tag} expect '{expect}' present: {expect in ans}")


if __name__ == "__main__":
    asyncio.run(main())
