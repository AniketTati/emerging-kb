"""Content-verified remap — the capstone of the trustworthy-eval pipeline.

For every still-flagged query, gather candidate source docs (the auditor's
LLM content-search + a fuzzy id-rename match), then VERIFY each candidate
with a grounding check: does this document actually support the expected
answer? Apply the remap ONLY when a candidate grounds. This closes the loop
— a citation is never repointed at a doc that doesn't contain the answer.

Outputs:
  - applies verified citation remaps in place (queries.yaml, with .bak)
  - writes docs/eval_audit/<domain>_FINAL_triage.md listing the genuinely
    broken queries (answer bugs / real gaps) that need a human decision.

Run AFTER audit_eval.py --all and fix_eval.py --all --apply.

Usage:
    uv run python scripts/finalize_eval.py --all
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import json
import sys
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))  # so `scripts.*` imports resolve
CORPUS = _ROOT / "demo-corpus"
DOMAINS_DIR = CORPUS / "domains"
AUDIT_DIR = _ROOT / "docs" / "eval_audit"

# reuse the auditor's helpers so verification is identical to the audit
from scripts.audit_eval import (  # type: ignore
    _AUDIT_SYSTEM_PROMPT, _loads_tolerant, _read_doc, _llm_json_retry,
    _domain_scoped_docs, _load_manifest_docs,
)
from scripts.fix_eval import _fuzzy_match, _real_doc_ids  # type: ignore

FLAGGED = {"CITATION_WRONG", "ANSWER_WRONG", "NOT_IN_CORPUS", "NEEDS_REVIEW"}


async def _grounds(client, question, expected, doc_id, doc_path) -> tuple[bool, str]:
    """Does doc_id support the expected answer? Returns (supported, evidence)."""
    text = _read_doc(doc_path)
    if not text:
        return False, ""
    user = (
        f"QUESTION: {question}\n\n"
        f"EXPECTED ANSWER (from dataset): {expected}\n\n"
        f"CITED DOCUMENTS:\n\n### DOC: {doc_id}\n{text}\n\n"
        "Return JSON only."
    )
    try:
        raw = await _llm_json_retry(
            client, user=user, system=_AUDIT_SYSTEM_PROMPT, max_tokens=600,
        )
        data = _loads_tolerant(raw) or {}
    except Exception:
        return False, ""
    supported = (data.get("supported") or "NONE") == "FULL"
    return supported, (data.get("evidence_quote") or "")[:300]


async def finalize_domain(domain: str, client, concurrency: int = 4) -> dict:
    audit = json.loads((AUDIT_DIR / f"{domain}.json").read_text())
    qpath = DOMAINS_DIR / domain / "queries.yaml"
    qdata = yaml.safe_load(qpath.read_text())
    qmap = {q["id"]: q for q in qdata["queries"]}
    scoped = _domain_scoped_docs(domain)
    manifest = _load_manifest_docs(domain)
    real_ids = _real_doc_ids(domain)

    sem = asyncio.Semaphore(concurrency)
    verified_remaps, genuine_bugs = [], []

    async def handle(r):
        qid = r["id"]
        if r["verdict"] not in FLAGGED:
            return
        q = qmap.get(qid)
        if not q:
            return
        question = q.get("question", "")
        expected = q.get("expected_answer", "")

        # Candidate docs: LLM content-search ∪ fuzzy id-rename of current cites.
        cands: list[str] = []
        for d in (r.get("search_docs") or []):
            if d in real_ids and d not in cands:
                cands.append(d)
        for c in (q.get("expected_citations") or []):
            if c in real_ids and c not in cands:
                cands.append(c)
            else:
                best, score = _fuzzy_match(c, real_ids)
                if best and score >= 0.5 and best not in cands:
                    cands.append(best)
        if not cands:
            genuine_bugs.append({"id": qid, "reason": "no candidate doc",
                                 "verdict": r["verdict"], "question": question,
                                 "expected": expected})
            return

        async with sem:
            for cand in cands[:4]:
                path = manifest.get(cand) or scoped.get(cand)
                if not path:
                    continue
                ok, evidence = await _grounds(client, question, expected, cand, path)
                if ok:
                    verified_remaps.append({
                        "id": qid, "new_citations": [cand],
                        "evidence": evidence,
                    })
                    return
        # nothing grounded → genuine answer bug or gap
        genuine_bugs.append({
            "id": qid, "verdict": r["verdict"], "question": question,
            "expected": expected,
            "discrepancy": r.get("discrepancy") or r.get("note") or "",
            "corpus_says": r.get("corpus_says") or r.get("search_answer") or "",
        })

    await asyncio.gather(*(handle(r) for r in audit))

    # Apply verified remaps in place.
    raw = qpath.read_text()
    for vr in verified_remaps:
        q = qmap.get(vr["id"])
        if q:
            q["expected_citations"] = vr["new_citations"]
            if vr["evidence"]:
                q["evidence_quote"] = vr["evidence"]
            q["citations_verified"] = True
    if verified_remaps:
        qpath.with_suffix(".yaml.bak2").write_text(raw)
        qpath.write_text(yaml.safe_dump(qdata, sort_keys=False,
                                        allow_unicode=True, width=100))

    # Triage report for the genuine bugs.
    lines = [f"# {domain} — genuine eval issues needing a human decision",
             f"\n{len(genuine_bugs)} queries could not be grounded to any "
             f"corpus doc (real answer bugs or corpus gaps).\n"]
    for b in genuine_bugs:
        lines.append(f"## {b['id']} [{b.get('verdict','')}]")
        lines.append(f"- **Q:** {b.get('question','')}")
        lines.append(f"- **expected:** {b.get('expected','')}")
        if b.get("discrepancy"):
            lines.append(f"- **issue:** {b['discrepancy']}")
        if b.get("corpus_says"):
            lines.append(f"- **corpus actually says:** {b['corpus_says']}")
        lines.append("")
    (AUDIT_DIR / f"{domain}_FINAL_triage.md").write_text("\n".join(lines))

    return {"domain": domain, "verified_remaps": len(verified_remaps),
            "genuine_bugs": len(genuine_bugs)}


async def main(domains, concurrency):
    from kb.query.llm_client import make_query_llm_client
    client = make_query_llm_client()
    if client is None:
        print("ERROR: no LLM client", file=sys.stderr)
        return 1
    grand = {"remaps": 0, "bugs": 0}
    for d in domains:
        print(f"# finalizing {d} ...")
        r = await finalize_domain(d, client, concurrency)
        grand["remaps"] += r["verified_remaps"]
        grand["bugs"] += r["genuine_bugs"]
        print(f"  {d}: {r['verified_remaps']} content-verified remaps applied, "
              f"{r['genuine_bugs']} genuine issues → {d}_FINAL_triage.md")
    print(f"\nTOTAL: {grand['remaps']} verified remaps, {grand['bugs']} genuine issues for review")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args()
    doms = (["construction", "finance", "government", "healthcare", "legal", "mining"]
            if args.all else [args.domain])
    if doms == [None]:
        ap.error("pass --domain or --all")
    raise SystemExit(asyncio.run(main(doms, args.concurrency)))
