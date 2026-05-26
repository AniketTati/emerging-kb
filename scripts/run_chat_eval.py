"""Run the 20-query chat eval (see `docs/chat_eval_queries.md`) and dump a
per-query verdict to `/tmp/eval_results.json`.

Spaces calls 3s apart to stay under Gemini's parallel-call ceiling on the
free tier. Single-shot — not a benchmark loop. Re-run after changing any of:
- intent classifier / planner mode routing
- CRAG threshold or bypass-mode list
- generator prompt or output cap
- a retrieval channel that ranks results
- the demo corpus

Usage:
    source scripts/dev_env.sh
    uv run python scripts/run_chat_eval.py
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

WORKSPACE = "00000000-0000-0000-0000-000000000001"
CHAT_URL = "http://localhost:8000/chat"


# Keep in sync with the table in docs/chat_eval_queries.md.
QUERIES = [
    {"id": "Q1-CORPUS-SUMMARY", "category": "corpus-scope", "query": "Summarize all the documents in this workspace"},
    {"id": "Q2-LIST-DOCS", "category": "corpus-scope", "query": "What types of documents do I have"},
    {"id": "Q3-CONTRACT-TERMS", "category": "factoid-contract", "query": "What is the payment due period in the MSA between NorthWind and Vertex"},
    {"id": "Q4-CONFLICT", "category": "conflict-resolution", "query": "Tell me about the MSA between NorthWind and Vertex including payment terms"},
    {"id": "Q5-AMENDMENT-CHAIN", "category": "chain-aware", "query": "What did Amendment No. 1 change in the MSA"},
    {"id": "Q6-INVOICE-AMOUNT", "category": "factoid-financial", "query": "How much was billed on invoice VRX-2026-0317"},
    {"id": "Q7-EMPLOYMENT", "category": "factoid-hr", "query": "What is the starting salary in the employment offer letter"},
    {"id": "Q8-LAB-RESULTS", "category": "factoid-medical", "query": "What abnormal lab results does the blood panel show"},
    {"id": "Q9-POSTMORTEM-CAUSE", "category": "factoid-incident", "query": "What was the root cause of the recent incident postmortem"},
    {"id": "Q10-FINANCIAL-KPI", "category": "factoid-financial", "query": "What was NorthWind Capital revenue in Q1 2026"},
    {"id": "Q11-VAGUE", "category": "vague", "query": "Anything interesting going on"},
    {"id": "Q12-ENTITY-CROSS-DOC", "category": "multi-hop", "query": "Which documents mention Vertex Industries"},
    {"id": "Q13-RESUME-SKILLS", "category": "factoid-hr", "query": "What programming languages does the software engineer resume list"},
    {"id": "Q14-PRICING", "category": "factoid-financial", "query": "What does the pricing sheet list as the rate for the standard processing tier"},
    {"id": "Q15-MEETING-ACTIONS", "category": "factoid-meeting", "query": "What action items came out of the most recent standup"},
    {"id": "Q16-OUT-OF-CORPUS", "category": "refusal-correct", "query": "What is the capital of France"},
    {"id": "Q17-CONTRADICTORY-NUMBERS", "category": "numeric-precision", "query": "What is the SLA processing time guarantee"},
    {"id": "Q18-EML-THREAD-ATTENDEES", "category": "factoid-email", "query": "Who participated in the IT incident email thread"},
    {"id": "Q19-EOB-DENIED", "category": "factoid-medical-eob", "query": "What was denied in the insurance explanation of benefits"},
    {"id": "Q20-CASE-STUDY-OUTCOME", "category": "factoid-narrative", "query": "What outcome did the customer case study report"},
]


def ask(query: str) -> dict:
    req = urllib.request.Request(
        CHAT_URL,
        data=json.dumps({"query": query}).encode(),
        headers={
            "Content-Type": "application/json",
            "X-Test-Workspace": WORKSPACE,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"_error": f"HTTP {e.code}", "_body": e.read().decode()[:200]}
    except Exception as e:
        return {"_error": f"{type(e).__name__}: {e}"[:200]}


def main() -> None:
    results = []
    print(f"{'id':<28} {'mode':<6} {'refused':<24} {'crag':<5} {'cits':<5} head")
    print("-" * 160)
    for q in QUERIES:
        d = ask(q["query"])
        time.sleep(3)  # Gemini parallel-call ceiling
        if d.get("_error"):
            results.append({**q, "error": d["_error"]})
            print(f"{q['id']:<28} ERROR {d['_error']}")
            continue
        g = d["generation"]
        ans = (g.get("answer") or "").strip()
        cits = g.get("citations") or []
        confl = d.get("conflict_resolutions") or []
        row = {
            "id": q["id"],
            "category": q["category"],
            "query": q["query"],
            "refused": g["refused"],
            "reason": g.get("refusal_reason"),
            "mode": d.get("mode"),
            "intent": d.get("intent"),
            "crag": round(d.get("crag_score") or 0.0, 2),
            "faithfulness": d.get("faithfulness_verdict"),
            "n_cits": len(cits),
            "n_conflicts": len(confl),
            "cited_files": sorted({
                c.get("label", "").split(" · ")[0]
                for c in cits if c.get("label")
            }),
            "answer_head": ans[:200],
        }
        results.append(row)
        refused_str = (
            f"REFUSE:{row['reason'] or '?'}" if row["refused"] else "ok"
        )
        head = ans[:80].replace("\n", " ")
        print(
            f"{row['id']:<28} {row['mode']:<6} {refused_str:<24} "
            f"{row['crag']:<5} {row['n_cits']:<5} {head!r}"
        )

    # Per-category roll-up.
    from collections import defaultdict
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        by_cat[r.get("category", "?")].append(r)
    print()
    print("=== per-category ===")
    for cat, items in sorted(by_cat.items()):
        ok = sum(1 for i in items if not i.get("refused") and not i.get("error"))
        print(f"  {cat:<28} {ok}/{len(items)}")
    n_ok = sum(1 for r in results if not r.get("refused") and not r.get("error"))
    print(f"\nTotal answered: {n_ok}/{len(results)}")

    out = "/tmp/eval_results.json"
    json.dump(results, open(out, "w"), indent=2)
    print(f"Full dump → {out}")


if __name__ == "__main__":
    main()
