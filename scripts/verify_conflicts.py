#!/usr/bin/env python3
"""Verify Q1 — conflict resolution across docs — on the finance eval's
designed conflict stratum (q021-q026). Checks the answer resolves the
contradiction AND whether the structured conflict layer fired
(ChatResult.conflict_resolutions).

  source scripts/dev_env.sh && python3 scripts/verify_conflicts.py
"""
from __future__ import annotations

import asyncio

from kb.config import get_settings
from kb.db.pool import open_connection
from kb.query.orchestrator import Orchestrator

WS = "f0000000-0000-0000-0000-000000000001"

CASES: list[tuple[str, str, str | None]] = [
    ("q021 chain (8.85 vs 9.40)",
     "The original loan agreement says the interest rate is 8.85%; Addendum #1 says 9.40%. Which one controls now?", "9.40"),
    ("q022 indep (11.5 vs 16.5)",
     "Neha Kapoor's loan agreement says a fixed 11.5% rate but the bank statement shows a 16.5% reset. Which prevails?", None),
    ("q023 draft-vs-audited",
     "The FY2024-25 budget draft shows a different EBITDA than the audited FY2023-24 financials. Which figure is current?", None),
    ("q024 reg-vs-internal",
     "The PMLA rule says an INR 10 lakh CTR threshold; HDFC's internal compliance bulletin says INR 5 lakh. Which is operative?", None),
    ("q025 fraud-duplicate",
     "Aniket Desai's account shows two wire transactions of USD 142,000 each — one authorised, one a duplicate. Which is fraudulent?", None),
    ("q026 entity-identity",
     "Mahalaxmi Equipment and Mahalaxmi Infra appear in different documents. Are they the same legal entity?", None),
    # neutral phrasing — does it surface the conflict WITHOUT being told?
    ("neutral (rate, unprompted)",
     "What is the controlling interest rate on the Acme HDFC term loan?", "9.40"),
]


async def ask(orch, s, q):
    async with open_connection(s.app_database_url) as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (WS,))
            return await orch.chat(q, workspace_id=WS, conn=conn)


async def main():
    s = get_settings()
    orch = Orchestrator.make_default()
    for label, q, expect in CASES:
        r = await ask(orch, s, q)
        g = r.generation
        cr = r.conflict_resolutions or []
        ans = (getattr(g, "answer", "") or "").strip()
        print(f"=== {label} ===")
        print(f"  mode={(r.plan or {}).get('mode')} refused={bool(getattr(g,'refused',False))} "
              f"conflict_resolutions={len(cr)}")
        if cr:
            print(f"  conflict[0]: {str(cr[0])[:200]}")
        print(f"  answer: {ans[:300].replace(chr(10), ' ')}")
        if expect:
            print(f"  expect '{expect}' present: {expect in ans}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
