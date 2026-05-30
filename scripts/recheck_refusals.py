"""Validate every auto-converted refusal against the FULL domain corpus.

The ground-truth builder fed only the top-12 keyword-retrieved docs. If the
answer lived in a doc that didn't rank (e.g. a cross-domain doc), the builder
wrongly concluded "not present" and converted the query to a refusal. That is
a trust bug: a real question turned into a false refusal.

This recheck feeds EVERY domain-visible document (own docs + threads +
cross-domain workspace — small markdown, fits the context window) and asks
honestly: is this answerable from the full corpus? If yes, it REVERTS the
refusal and writes the real grounded answer + evidence. If no, the refusal
is confirmed trustworthy.

Targets only queries this session converted to refusal (answer_provenance=
corpus_grounded AND expected_refusal=true AND stratum_was set).

Usage:
    uv run python scripts/recheck_refusals.py --all --apply
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
sys.path.insert(0, str(_ROOT))
CORPUS = _ROOT / "demo-corpus"
DOMAINS_DIR = CORPUS / "domains"

from scripts.audit_eval import _loads_tolerant, _read_doc, _llm_json_retry  # type: ignore

_SYSTEM = (
    "You are fact-checking whether a question is answerable from a document "
    "corpus. You are given the QUESTION and the FULL set of documents "
    "available to it. Read them all. Decide honestly:\n"
    " - If the answer IS in the corpus, give it, grounded, with the exact "
    "quote and the doc id.\n"
    " - If the corpus genuinely does NOT contain it (or the question's "
    "premise is false — e.g. 'two X disagree' when only one X exists), say "
    "so; that refusal is the correct answer.\n"
    "\n"
    "Output JSON exactly:\n"
    "{\n"
    '  "answerable": true | false,\n'
    '  "answer": "<grounded answer, or the correct not-present/false-premise statement>",\n'
    '  "evidence_quotes": ["<exact quote>", "..."],\n'
    '  "source_docs": ["<doc id(s)>"]\n'
    "}"
)


def _domain_all_docs(domain: str) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for pat in [
        str(DOMAINS_DIR / domain / "docs" / "*"),
        str(DOMAINS_DIR / domain / "threads" / "*"),
        str(CORPUS / "workspaces" / "*" / "docs" / "*"),
    ]:
        for p in glob.glob(pat):
            pp = Path(p)
            if pp.is_file():
                out[pp.stem] = pp
    return out


async def _recheck_one(client, q, all_docs):
    # Feed every doc (cap each to keep total reasonable).
    blocks = [f"### DOC: {did}\n{_read_doc(p, cap=4000)}" for did, p in all_docs.items()]
    user = (f"QUESTION: {q['question']}\n\nFULL CORPUS ({len(blocks)} docs):\n\n"
            + "\n\n".join(blocks) + "\n\nReturn JSON only.")
    try:
        raw = await _llm_json_retry(client, user=user, system=_SYSTEM, max_tokens=900)
        d = _loads_tolerant(raw) or {}
    except Exception as exc:
        return {"id": q["id"], "status": f"error: {exc}"}

    def _q(v):
        if isinstance(v, list):
            return [str(x).strip() for x in v if x]
        return [str(v).strip()] if v else []

    if d.get("answerable"):
        ans = d.get("answer")
        ans = " ".join(ans) if isinstance(ans, list) else str(ans or "")
        quotes = _q(d.get("evidence_quotes") or d.get("evidence_quote"))
        if ans.strip() and quotes:
            return {"id": q["id"], "status": "now_answerable",
                    "answer": ans.strip(),
                    "evidence": "  |  ".join(quotes)[:400],
                    "source_docs": [s for s in (d.get("source_docs") or []) if s]}
    return {"id": q["id"], "status": "refusal_confirmed",
            "answer": (str(d.get("answer") or "")).strip()[:300]}


async def recheck_domain(domain, client, apply, concurrency=3):
    qpath = DOMAINS_DIR / domain / "queries.yaml"
    qdata = yaml.safe_load(qpath.read_text())
    qmap = {q["id"]: q for q in qdata["queries"]}
    # converted-this-session refusals
    targets = [q for q in qdata["queries"]
               if q.get("expected_refusal") and q.get("answer_provenance") == "corpus_grounded"
               and q.get("stratum_was")]
    if not targets:
        return {"domain": domain, "rechecked": 0, "reverted": 0, "confirmed": 0}

    all_docs = _domain_all_docs(domain)
    sem = asyncio.Semaphore(concurrency)
    async def run(q):
        async with sem:
            return await _recheck_one(client, q, all_docs)
    results = await asyncio.gather(*(run(q) for q in targets))

    reverted = confirmed = 0
    for r in results:
        q = qmap.get(r["id"])
        if not q:
            continue
        if r["status"] == "now_answerable":
            # REVERT the false refusal → write the real grounded answer.
            q.pop("expected_refusal", None)
            q["stratum"] = q.pop("stratum_was", q.get("stratum"))
            q["expected_answer"] = r["answer"]
            q["evidence_quote"] = r["evidence"]
            if r.get("source_docs"):
                q["expected_citations"] = r["source_docs"]
            q["answer_provenance"] = "corpus_grounded_fullscan"
            q["verified"] = True
            reverted += 1
        elif r["status"] == "refusal_confirmed":
            q["refusal_confirmed_fullscan"] = True
            confirmed += 1

    if apply and (reverted or confirmed):
        qpath.with_suffix(".yaml.bak4").write_text(qpath.read_text())
        qpath.write_text(yaml.safe_dump(qdata, sort_keys=False, allow_unicode=True, width=100))
    return {"domain": domain, "rechecked": len(targets),
            "reverted": reverted, "confirmed": confirmed, "results": results}


async def main(domains, apply, concurrency):
    from kb.query.llm_client import make_query_llm_client
    client = make_query_llm_client()
    if client is None:
        print("ERROR: no LLM client", file=sys.stderr)
        return 1
    g = {"reverted": 0, "confirmed": 0}
    for d in domains:
        r = await recheck_domain(d, client, apply, concurrency)
        g["reverted"] += r["reverted"]; g["confirmed"] += r["confirmed"]
        print(f"{d:13}: rechecked {r['rechecked']:2} refusals → "
              f"{r['reverted']} were FALSE (reverted to real answer), "
              f"{r['confirmed']} confirmed genuine")
        for rr in r.get("results", []):
            if rr["status"] == "now_answerable":
                print(f"    REVERTED {rr['id']}: {rr['answer'][:90]}")
    print(f"\nTOTAL: {g['reverted']} false-refusals corrected, {g['confirmed']} genuine refusals confirmed")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--concurrency", type=int, default=3)
    args = ap.parse_args()
    doms = (["construction", "finance", "government", "healthcare", "legal", "mining"]
            if args.all else [args.domain])
    if doms == [None]:
        ap.error("pass --domain or --all")
    raise SystemExit(asyncio.run(main(doms, args.apply, args.concurrency)))
