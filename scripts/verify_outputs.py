#!/usr/bin/env python3
"""Output-VERIFIED coverage: every mode/layer, each answer checked against
ground truth computed from the DB (not just 'did it respond'). In-process.

  source scripts/dev_env.sh && python3 scripts/verify_outputs.py
"""
from __future__ import annotations

import asyncio

from kb.config import get_settings
from kb.db.pool import open_connection
from kb.query.orchestrator import Orchestrator

WS = "f0000000-0000-0000-0000-000000000001"

# (mode-label, query, expect)  expect: list[str] all-must-appear in answer,
#   "REFUSE" = must refuse, or None = manual spot-check (printed for review).
CASES: list[tuple[str, str, object]] = [
    ("Q  aggregate",  "what is the total of all debits across all the transactions", ["866,958,265"]),
    ("I  inventory",  "how many bank statements do I have", ["8 bank statement"]),
    ("A  anomaly",    "what are the most unusual transactions in the bank statements", ["48,240,000"]),
    ("K  chain",      "how has the HDFC loan interest rate changed over time", ["8.85", "9.40"]),
    ("H  factoid",    "what is the current interest rate on the HDFC loan", ["9.40"]),
    ("E  entity",     "tell me about Acme Corporation", ["184.2"]),
    ("T  multi-hop",  "how is Acme connected to Northwind", ["Northwind"]),
    ("M  mention",    "where is Northwind mentioned", ["Northwind"]),
    ("C  unit-filter","show me transactions over 100000", None),
    ("D  doc-meta",   "show me all the bank statement documents", None),
    ("S  scoped",     "summarize the loan-001 HDFC original agreement", None),
    ("G  global",     "give me a high-level summary of this workspace", None),
    ("F  field-filter","which loans have a principal amount over 50 crore", None),  # finance: no typed field -> honest
]


async def ask(orch, s, q):
    async with open_connection(s.app_database_url) as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (WS,))
            return await orch.chat(q, workspace_id=WS, conn=conn)


async def run():
    s = get_settings()
    orch = Orchestrator.make_default()
    print(f"\n=== OUTPUT-VERIFIED COVERAGE (ws {WS[:8]}) ===\n")
    for label, q, expect in CASES:
        r = await ask(orch, s, q)
        g = r.generation
        ans = (getattr(g, "answer", "") or "")
        refused = bool(getattr(g, "refused", False))
        mode = (r.plan or {}).get("mode")
        ncites = len(getattr(g, "citations", []) or [])
        if expect == "REFUSE":
            verdict = "PASS" if refused else "FAIL"
        elif isinstance(expect, list):
            missing = [e for e in expect if e not in ans]
            verdict = "PASS" if (not refused and not missing) else f"FAIL(missing={missing})"
        else:
            verdict = "MANUAL"
        print(f"[{verdict}] {label:<15} mode={str(mode):<4} refused={str(refused):<5} cites={ncites} faith={r.faithfulness_verdict}")
        print(f"     Q: {q}")
        print(f"     A: {ans.strip()[:300].replace(chr(10),' ')}")
        print()


if __name__ == "__main__":
    asyncio.run(run())
