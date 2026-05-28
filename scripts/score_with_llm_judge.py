"""Post-hoc LLM-judge scorer for domain eval results.

The strict scorer in run_domain_queries.py requires substring/citation
match. Many answers are CORRECT but get marked "fail-no-cit-match" or
"partial" because the system cited DIFFERENT (also correct) documents
than the manifest expected, or paraphrased the expected text.

This script takes an existing results JSON + the queries.yaml and asks
Gemini Flash to judge whether each actual_answer semantically matches
the expected_answer. Output: a second-axis "lenient" verdict alongside
the original strict verdict.

Cost: 1 Gemini Flash call per query (cheap; ~$0.05 for 50 queries).

Usage:
    uv run python scripts/score_with_llm_judge.py \\
        --results docs/construction_query_results_v6.json \\
        --queries demo-corpus/domains/construction/queries.yaml \\
        --out    docs/construction_query_results_v6_judged.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))


_JUDGE_SYSTEM_PROMPT = (
    "You are an impartial judge for a document Q&A evaluation. For each "
    "case you'll see the question, the expected answer (from the manifest), "
    "and the system's actual answer. Decide whether the system's answer "
    "is essentially correct.\n"
    "\n"
    "Output JSON exactly: {\"verdict\": \"correct\" | \"partial\" | \"wrong\" | "
    "\"refused\", \"reason\": \"<one sentence>\"}.\n"
    "\n"
    "Rules:\n"
    " - 'correct' = the answer contains the key facts from the expected "
    "answer. Different wording / order / extra context is OK. Different "
    "citation IDs are OK. The CORE fact must match (e.g. expected 'Feb 28 "
    "2026' and actual says '28 February 2026' → correct).\n"
    " - 'partial' = the answer is on the right topic and has SOME of the "
    "expected facts but is missing a non-trivial piece, or hedges where "
    "the expected answer is definitive.\n"
    " - 'wrong' = the answer asserts something contradicting the expected "
    "answer, or names the wrong entity/value/date, or is on the wrong "
    "topic entirely.\n"
    " - 'refused' = the system did not attempt an answer (empty, "
    "'I cannot answer', faithfulness_gate_refused). For adversarial "
    "queries where the expected_refusal is true, a refusal IS the correct "
    "outcome — judge as 'correct' in that case.\n"
    "\n"
    "Be strict but fair. If the system answered 'INR 22 lakh' and the "
    "expected was 'INR 1.28 crore' (different numbers), that's 'wrong'. "
    "If the system answered 'February 28, 2026' and the expected was "
    "'28 Feb 2026', that's 'correct'."
)


def _build_judge_user_prompt(
    question: str,
    expected_answer: str,
    expected_refusal: str | None,
    actual_answer: str,
    actual_refused: bool,
    actual_refusal_reason: str,
) -> str:
    lines = [
        f"Question: {question}",
        "",
    ]
    if expected_refusal:
        lines.append("Expected outcome: REFUSAL (this is an adversarial query)")
        lines.append(f"Expected refusal reasoning: {expected_refusal[:300]}")
    else:
        lines.append(f"Expected answer: {expected_answer or '(none provided)'}")
    lines.append("")
    if actual_refused:
        lines.append(f"System REFUSED. reason={actual_refusal_reason or '(unspecified)'}")
        lines.append(f"System answer text (may still be present): {actual_answer[:600]}")
    else:
        lines.append(f"System answer: {actual_answer[:1200]}")
    lines.append("")
    lines.append("Return JSON only.")
    return "\n".join(lines)


async def _judge_one(client, query: dict, result: dict) -> dict:
    """Judge one (query, result) pair. Returns dict with verdict + reason."""
    expected_answer = query.get("expected_answer") or ""
    expected_refusal = query.get("expected_refusal")
    actual_answer = result.get("answer_preview") or ""
    actual_refused = bool(result.get("api_refused", False))
    actual_refusal_reason = result.get("api_refusal_reason") or ""

    user_prompt = _build_judge_user_prompt(
        question=query.get("question", ""),
        expected_answer=expected_answer,
        expected_refusal=(
            expected_refusal if isinstance(expected_refusal, str)
            else ("true" if expected_refusal else None)
        ),
        actual_answer=actual_answer,
        actual_refused=actual_refused,
        actual_refusal_reason=actual_refusal_reason,
    )

    try:
        text = await client.generate_json(
            user=user_prompt,
            system=_JUDGE_SYSTEM_PROMPT,
            max_tokens=200,
        )
    except Exception as exc:
        return {"verdict": "error", "reason": f"judge_call_failed: {exc}"}

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Try to extract JSON from text
        import re
        m = re.search(r"\{[^}]+\}", text)
        if m:
            try:
                data = json.loads(m.group(0))
            except Exception:
                return {"verdict": "error", "reason": "judge returned unparseable JSON"}
        else:
            return {"verdict": "error", "reason": "judge returned no JSON"}

    v = (data.get("verdict") or "").lower().strip()
    if v not in ("correct", "partial", "wrong", "refused"):
        return {"verdict": "error", "reason": f"unknown verdict {v!r}"}
    return {
        "verdict": v,
        "reason": str(data.get("reason") or "")[:300],
    }


async def main(results_path: Path, queries_path: Path, out_path: Path,
               concurrency: int = 5) -> int:
    from kb.query.llm_client import make_query_llm_client

    with open(queries_path) as fh:
        queries = yaml.safe_load(fh)["queries"]
    qmap = {q["id"]: q for q in queries}

    with open(results_path) as fh:
        results = json.load(fh)

    # Handle both formats: list of {id, verdict, ...} from v2+, OR
    # {results: [...]} wrapping.
    if isinstance(results, dict) and "results" in results:
        results = results["results"]

    client = make_query_llm_client()
    if client is None:
        print("ERROR: no LLM client (set KB_GEMINI_API_KEY)", file=sys.stderr)
        return 1

    print(f"# judging {len(results)} results against {queries_path.name}")

    sem = asyncio.Semaphore(concurrency)
    async def judge_with_lock(query, result):
        async with sem:
            r = await _judge_one(client, query, result)
            return result["id"], r

    tasks = []
    for r in results:
        qid = r.get("id")
        if qid not in qmap:
            continue
        tasks.append(judge_with_lock(qmap[qid], r))
    judgments = await asyncio.gather(*tasks)
    judge_by_id = dict(judgments)

    # Attach + summarize
    for r in results:
        j = judge_by_id.get(r.get("id"))
        if j:
            r["llm_judge"] = j

    # Stats
    from collections import Counter
    judge_verdicts = Counter()
    strict_verdicts = Counter()
    for r in results:
        # original "strict" verdict — multiple keys depending on script version
        strict = (
            r.get("verdict")
            or r.get("v")
            or ("refused" if r.get("api_refused") else "answer")
        )
        strict_verdicts[strict] += 1
        if "llm_judge" in r:
            judge_verdicts[r["llm_judge"]["verdict"]] += 1

    print()
    print("# STRICT verdicts:")
    for v, n in strict_verdicts.most_common():
        print(f"  {n:>3d}  {v}")
    print()
    print("# LLM-JUDGE verdicts:")
    for v, n in judge_verdicts.most_common():
        print(f"  {n:>3d}  {v}")

    # Compute headline accuracy
    n_total = sum(judge_verdicts.values())
    n_correct = judge_verdicts.get("correct", 0)
    n_partial = judge_verdicts.get("partial", 0)
    n_refused = judge_verdicts.get("refused", 0)
    print()
    print(f"# headline accuracy (LLM-judge):")
    print(f"  correct only   : {n_correct}/{n_total} = {n_correct/n_total*100:.1f}%")
    print(f"  correct+partial: {(n_correct+n_partial)}/{n_total} = {(n_correct+n_partial)/n_total*100:.1f}%")

    # Disagreement: where strict said "pass" but judge said "wrong" (false-positive in our scorer)
    # or strict said "fail*" but judge said "correct" (false-negative in our scorer)
    fp_count = 0
    fn_count = 0
    for r in results:
        strict = (r.get("verdict") or r.get("v") or "")
        judge = (r.get("llm_judge") or {}).get("verdict", "")
        if strict == "pass" and judge == "wrong":
            fp_count += 1
        elif strict.startswith("fail") and judge == "correct":
            fn_count += 1
    print()
    print(f"# scorer disagreements (vs LLM-judge):")
    print(f"  scorer said PASS, judge said WRONG (over-credit):    {fp_count}")
    print(f"  scorer said FAIL, judge said CORRECT (under-credit): {fn_count}")

    out_path.write_text(json.dumps(results, indent=2))
    print()
    print(f"# results → {out_path}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, required=True)
    ap.add_argument("--queries", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--concurrency", type=int, default=5)
    args = ap.parse_args()
    out = args.out or args.results.with_name(args.results.stem + "_judged.json")
    raise SystemExit(asyncio.run(main(
        args.results, args.queries, out, args.concurrency,
    )))
