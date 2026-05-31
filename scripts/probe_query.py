#!/usr/bin/env python3
"""Dev probe: fire a /chat query at the live API and print a compact,
structured summary for query-pipeline review.

  python3 scripts/probe_query.py "sum of all transactions" [workspace_id]

Prints intent, chosen mode + plan params, refusal, gates (crag/faithfulness),
the answer, and citations (label + modality) — everything needed to judge
whether a flow resolved + routed + cited correctly.
"""
from __future__ import annotations

import json
import sys
import urllib.request

API = "http://localhost:8000/chat"
DEFAULT_WS = "f0000000-0000-0000-0000-000000000001"  # finance, 46 docs


def probe(query: str, ws: str) -> dict:
    req = urllib.request.Request(
        API,
        method="POST",
        data=json.dumps({"query": query}).encode(),
        headers={"Content-Type": "application/json", "X-Test-Workspace": ws},
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.load(resp)


def show(query: str, d: dict) -> None:
    pl = d.get("plan") or {}
    g = d.get("generation") or {}
    hits = d.get("hits") or []
    print(f"Q: {query}")
    print(f"  intent : {d.get('intent')} ({d.get('intent_confidence')})")
    print(f"  mode   : {pl.get('mode')}   notes={pl.get('notes')}")
    if pl.get("field_filters"):
        print(f"  filters: {pl.get('field_filters')}")
    if pl.get("unit_types"):
        print(f"  units  : {pl.get('unit_types')}")
    if pl.get("q_payload"):
        print(f"  q_payload: {json.dumps(pl.get('q_payload'))[:400]}")
    print(f"  refused: {g.get('refused')}  reason={g.get('refusal_reason')}")
    print(
        f"  crag   : {d.get('crag_score')}  faith: {d.get('faithfulness_verdict')} "
        f"{round(d.get('faithfulness_score') or 0, 2)} ({d.get('faithfulness_model_id')})"
    )
    print(f"  confid : {d.get('confidence')} — {d.get('confidence_reason')}")
    modes_applied = sorted({(h.get("metadata") or {}).get("mode_applied") for h in hits})
    print(f"  hits   : {len(hits)}  mode_applied={modes_applied}")
    print(f"  ANSWER : {(g.get('answer') or '').strip()[:700]}")
    cits = g.get("citations") or []
    print(f"  CITES ({len(cits)}):")
    for c in cits[:8]:
        ref = c.get("ref") or {}
        print(
            f"     {c.get('label') or c.get('file_id')} | {c.get('modality')} "
            f"| p{ref.get('page_start')}-{ref.get('page_end')}"
        )
    print()


if __name__ == "__main__":
    q = sys.argv[1]
    workspace = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_WS
    show(q, probe(q, workspace))
