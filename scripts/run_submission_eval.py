#!/usr/bin/env python3
"""Run the submission eval (demo-corpus/eval/submission_eval.yaml) through the
live /chat pipeline and score each pair.

Rubric (transparent + deterministic):
  * normal pair  → PASS iff an expected SOURCE file is cited AND an expected
                   key FACT appears in the answer.
  * refusal pair → PASS iff the system refused (cite-or-refuse / safety).

Usage (stack must be up + seeded; see scripts/bootstrap.sh):
    python3 scripts/run_submission_eval.py
    # or inside docker:  docker compose exec -T api python scripts/run_submission_eval.py /app/eval.yaml
Env: KB_API_BASE_URL (default http://localhost:8000)
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

BASE = os.environ.get("KB_API_BASE_URL", "http://localhost:8000")
DEFAULT_SPEC = Path(__file__).resolve().parent.parent / "demo-corpus/eval/submission_eval.yaml"


def ask(query: str, workspace: str) -> dict:
    req = urllib.request.Request(
        f"{BASE}/chat",
        data=json.dumps({"query": query}).encode(),
        headers={"Content-Type": "application/json", "X-Test-Workspace": workspace},
    )
    try:
        with urllib.request.urlopen(req, timeout=150) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"_error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
    except Exception as e:  # noqa: BLE001
        return {"_error": f"{type(e).__name__}: {e}"}


def main(argv: list[str]) -> int:
    spec_path = Path(argv[1]) if len(argv) > 1 else DEFAULT_SPEC
    spec = yaml.safe_load(spec_path.read_text())
    ws = spec["workspace"]
    pairs = spec["pairs"]

    print(f"Submission eval — {len(pairs)} pairs → {BASE} (workspace {ws[:8]}…)\n")
    print(f"{'id':<24} {'verdict':<6} {'detail':<26} {'conf':<7} answer/​reason head")
    print("-" * 118)

    results = []
    for p in pairs:
        d = ask(p["question"], ws)
        if d.get("_error"):
            results.append({"id": p["id"], "pass": False, "detail": d["_error"]})
            print(f"{p['id']:<24} {'ERROR':<6} {d['_error'][:26]:<26}")
            time.sleep(2.0)
            continue
        g = d.get("generation") or {}
        refused = bool(g.get("refused"))
        conf = (d.get("confidence") or "—")
        if p.get("refuse"):
            passed = refused
            detail = f"refused={refused}"
            head = (g.get("refusal_reason") or g.get("answer") or "")[:46]
        else:
            ans = (g.get("answer") or "")
            cits = g.get("citations") or []
            labels = " ".join((c.get("label") or str(c.get("file_id") or "")) for c in cits)
            src_hit = any(s.lower() in labels.lower() for s in p.get("sources", []))
            fact_hit = any(str(f).lower() in ans.lower() for f in p.get("any_facts", []))
            passed = src_hit and fact_hit
            detail = f"src={'Y' if src_hit else 'n'} fact={'Y' if fact_hit else 'n'}"
            head = ans.replace("\n", " ")[:46]
        results.append({
            "id": p["id"], "pass": passed, "detail": detail,
            "refused": refused, "confidence": conf,
            "crag": d.get("crag_score"), "faith": d.get("faithfulness_verdict"),
        })
        print(f"{p['id']:<24} {'PASS' if passed else 'FAIL':<6} {detail:<26} {str(conf):<7} {head!r}")
        time.sleep(2.5)  # stay under Gemini parallel-call ceiling

    n = len(results)
    passed = sum(1 for r in results if r["pass"])
    pct = round(100 * passed / n, 1) if n else 0.0
    print("\n" + "=" * 40)
    print(f"  SCORE: {passed}/{n} pairs passed  ({pct}%)")
    print("=" * 40)

    out = Path("eval_out"); out.mkdir(exist_ok=True)
    report = {"base": BASE, "workspace": ws, "passed": passed, "total": n,
              "pct": pct, "results": results}
    (out / "submission_eval_results.json").write_text(json.dumps(report, indent=2))
    print(f"Report → eval_out/submission_eval_results.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
