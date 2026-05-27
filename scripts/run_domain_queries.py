"""Run every query in a domain's queries.yaml against the workspace
that has its docs ingested. Score on:
  - returned_answer (not error / not refused unless expected)
  - citation match (any expected_citation doc_id appears in citations)
  - refusal correctness (adversarial → refused)

Writes /tmp/construction_query_results.json + per-query summary to stdout.

Usage:
    uv run python scripts/run_domain_queries.py \
        --workspace c0000000-0000-0000-0000-000000000001 \
        --queries demo-corpus/domains/construction/queries.yaml
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import httpx
import yaml

BASE = "http://localhost:8000"


async def run_one(client: httpx.AsyncClient, workspace_id: str, q: dict) -> dict:
    """POST /chat for one query. Returns scored result."""
    payload = {"query": q["question"]}
    t0 = time.monotonic()
    try:
        r = await client.post(
            "/chat", json=payload,
            headers={"X-Test-Workspace": workspace_id},
            timeout=120.0,
        )
    except Exception as exc:
        return {
            "id": q["id"], "ok": False, "verdict": "error",
            "elapsed_s": time.monotonic() - t0,
            "error": str(exc)[:200],
        }
    elapsed = time.monotonic() - t0

    if r.status_code != 200:
        return {
            "id": q["id"], "ok": False, "verdict": "http_error",
            "http": r.status_code, "elapsed_s": elapsed,
            "body": r.text[:400],
        }

    body = r.json()
    # Actual /chat shape: {query, query_id, rewrites, generation: {answer, citations: [{label, file_id, ...}], refused, refusal_reason}}
    gen = body.get("generation") or {}
    answer = gen.get("answer") or body.get("answer") or ""
    # Explicit refused flag from the API beats keyword sniffing — the
    # orchestrator's new adversarial short-circuit always sets this.
    api_refused = bool(gen.get("refused"))
    api_refusal_reason = gen.get("refusal_reason") or ""
    raw_cits = gen.get("citations") or body.get("citations") or body.get("hits") or []
    cit_names = []
    for c in raw_cits:
        if isinstance(c, dict):
            cit_names.append(c.get("label") or c.get("file_name") or c.get("name") or c.get("doc_id") or "")
        elif isinstance(c, str):
            cit_names.append(c)

    # Score
    # api_refused (explicit flag from /chat) is the truth; fall back to
    # keyword sniffing only when the API didn't tell us either way
    # (e.g. faithfulness gate set refused=True with a real answer body
    # — count that as a refusal too, since the gate refused to ship).
    is_refusal = api_refused or (
        "refuse" in answer.lower() or "cannot" in answer.lower()
        or "i'm not able" in answer.lower()
        or "i am not able" in answer.lower()
        or "i can't" in answer.lower()
    )
    expected_refusal = bool(q.get("expected_refusal"))
    expected_cits = q.get("expected_citations") or []

    if expected_refusal:
        verdict = "pass" if is_refusal else "fail-should-have-refused"
    elif not answer.strip():
        verdict = "fail-empty"
    else:
        # Citation check — for each expected citation, see if the citation appears.
        cit_hits = 0
        for ec in expected_cits:
            for cn in cit_names:
                if ec.lower() in (cn or "").lower():
                    cit_hits += 1
                    break
        if expected_cits and cit_hits == 0:
            verdict = "fail-no-cit-match"
        elif expected_cits and cit_hits < len(expected_cits):
            verdict = "partial-some-cits"
        else:
            verdict = "pass"

    return {
        "id": q["id"], "stratum": q.get("stratum"),
        "ok": True, "verdict": verdict,
        "elapsed_s": round(elapsed, 1),
        "answer_preview": (answer or "")[:200],
        "n_citations": len(cit_names),
        "citations": cit_names[:6],
        "is_refusal": is_refusal,
        "api_refused": api_refused,
        "api_refusal_reason": api_refusal_reason,
        "expected_refusal": expected_refusal,
        "expected_citations": expected_cits,
    }


async def main(workspace_id: str, queries_path: Path, out_path: Path,
               delay_s: float = 1.0) -> int:
    with open(queries_path) as fh:
        data = yaml.safe_load(fh)
    queries = data.get("queries") or []
    print(f"# {len(queries)} queries in {queries_path.name}")
    print()

    results: list[dict] = []
    async with httpx.AsyncClient(base_url=BASE, timeout=120.0) as client:
        for i, q in enumerate(queries, start=1):
            r = await run_one(client, workspace_id, q)
            results.append(r)
            stratum = (r.get("stratum") or "?")[:14]
            verdict = r.get("verdict", "?")
            elapsed = r.get("elapsed_s", 0)
            answer = (r.get("answer_preview") or r.get("error") or "")[:80]
            print(f'[{i:>2d}/{len(queries)}] {r["id"]:18s} [{stratum:14s}] {verdict:25s} {elapsed:>5.1f}s  {answer}')
            # Polite delay to avoid Gemini rate limits.
            if delay_s and i < len(queries):
                await asyncio.sleep(delay_s)

    # Aggregate
    from collections import Counter
    counts = Counter(r["verdict"] for r in results)
    by_stratum = {}
    for r in results:
        s = r.get("stratum") or "?"
        by_stratum.setdefault(s, Counter())[r["verdict"]] += 1

    print()
    print(f'# OVERALL ({len(results)} queries):')
    for v, n in counts.most_common():
        print(f'  {n:>3d}  {v}')
    print()
    print(f'# BY STRATUM:')
    for s in sorted(by_stratum.keys()):
        breakdown = ", ".join(f"{v}={n}" for v, n in by_stratum[s].most_common())
        print(f'  {s:18s}  {breakdown}')

    # Write JSON
    out_path.write_text(json.dumps(results, indent=2))
    print()
    print(f'# detailed results → {out_path}')
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--queries", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("/tmp/domain_query_results.json"))
    ap.add_argument("--delay", type=float, default=1.0)
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main(args.workspace, args.queries, args.out, args.delay)))
