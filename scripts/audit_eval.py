"""Grounded eval auditor — verify every expected answer against the corpus.

The problem this solves: the eval's `expected_answer` + `expected_citations`
were hand-authored and are sometimes WRONG (the cited doc doesn't contain the
answer; the stated number disagrees with the source; the answer isn't in the
corpus at all). Optimizing the system against a miscalibrated ruler is why we
never converged.

This tool reads the ACTUAL cited documents for each query and asks an LLM
judge to verify, with evidence, whether the expected answer is supported.
Output is an audit report + a machine-readable corrections file. A human
reviews the flags; corrections are applied to produce a VERIFIED eval we can
trust 100%.

Verdicts per query:
  GROUNDED        — expected answer is fully supported by the cited docs.
  CITATION_WRONG  — answer is correct but lives in a DIFFERENT doc than cited.
  ANSWER_WRONG    — cited docs contradict the expected answer (wrong number/fact).
  NOT_IN_CORPUS   — the fact isn't anywhere we can find.
  NEEDS_REVIEW    — auditor is unsure; human must look.
  ADVERSARIAL_OK  — refusal query; grounding check skipped (validated separately).

Usage:
    # audit one domain
    uv run python scripts/audit_eval.py --domain construction

    # audit all domains
    uv run python scripts/audit_eval.py --all
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

CORPUS = _ROOT / "demo-corpus"
DOMAINS_DIR = CORPUS / "domains"


_AUDIT_SYSTEM_PROMPT = (
    "You are a meticulous fact-checker auditing a Q&A evaluation dataset. "
    "You are given a QUESTION, the dataset's EXPECTED ANSWER, and the FULL "
    "TEXT of the documents the dataset says contain the answer. Your job is "
    "to verify — strictly, with evidence — whether the expected answer is "
    "actually supported by those documents.\n"
    "\n"
    "Rules:\n"
    " - Quote the exact supporting text when it exists. Do not paraphrase "
    "the evidence.\n"
    " - If the documents state a DIFFERENT value/fact than the expected "
    "answer (e.g. expected '4 incidents' but the doc says '6'), that is "
    "ANSWER_WRONG — report both numbers.\n"
    " - If the cited documents simply do NOT contain the answer (it's not "
    "there at all), say the cited docs lack it. (A later step searches the "
    "rest of the corpus.)\n"
    " - If the expected answer over-claims (asserts something the doc only "
    "partially supports — e.g. expected 'Clause 9 says maintain PPE' but "
    "Clause 9 only lists compliance Acts), that is ANSWER_WRONG with an "
    "explanation of the over-claim.\n"
    " - Be precise about numbers, dates, names, and clause references.\n"
    "\n"
    "Output JSON exactly:\n"
    "{\n"
    '  "supported": "FULL" | "PARTIAL" | "NONE",\n'
    '  "evidence_quote": "<exact quote from the docs, or empty>",\n'
    '  "corpus_says": "<what the docs ACTUALLY say the answer is, grounded>",\n'
    '  "discrepancy": "<one sentence on the mismatch, or null>"\n'
    "}"
)

_SEARCH_SYSTEM_PROMPT = (
    "You are locating where a fact lives in a document corpus. You are given "
    "a QUESTION and several CANDIDATE documents (retrieved by keyword). State "
    "which document(s), if any, actually contain the answer, and quote it.\n"
    "\n"
    "Output JSON exactly:\n"
    "{\n"
    '  "found": true | false,\n'
    '  "doc_ids": ["<doc id that contains the answer>"],\n'
    '  "grounded_answer": "<the answer as supported by those docs>",\n'
    '  "evidence_quote": "<exact quote>"\n'
    "}"
)


def _load_manifest_docs(domain: str) -> dict[str, Path]:
    """Map citation id → file path from the domain manifest."""
    manifest_path = DOMAINS_DIR / domain / "manifest.yaml"
    out: dict[str, Path] = {}
    if not manifest_path.exists():
        return out
    data = yaml.safe_load(manifest_path.read_text())
    for d in (data.get("docs") or []):
        did = d.get("id")
        f = d.get("file")
        if did and f:
            out[did] = _ROOT / f
    return out


def _domain_scoped_docs(domain: str) -> dict[str, Path]:
    """Docs visible to a domain's queries: the domain's own docs/threads PLUS
    the shared cross-domain workspace (where mahalaxmi-cross-domain,
    acme-corp-360-view etc. live). NOT other domains — q028 must not match a
    finance complaint."""
    out: dict[str, Path] = {}
    patterns = [
        str(DOMAINS_DIR / domain / "docs" / "*"),
        str(DOMAINS_DIR / domain / "threads" / "*"),
        str(CORPUS / "workspaces" / "*" / "docs" / "*"),
    ]
    for pat in patterns:
        for p in glob.glob(pat):
            path = Path(p)
            if path.is_file():
                out[path.stem] = path
    return out


def _read_doc(path: Path, cap: int = 8000) -> str:
    try:
        return path.read_text(errors="replace")[:cap]
    except Exception:
        return ""


async def _llm_json_retry(client, *, user: str, system: str, max_tokens: int,
                          attempts: int = 4):
    """generate_json with exponential backoff on transient errors. Re-raises
    on hard-deny (403 PERMISSION_DENIED) so the caller can abort fast rather
    than burn the whole batch against a dead key."""
    import asyncio as _a
    last = None
    for i in range(attempts):
        try:
            return await client.generate_json(
                user=user, system=system, max_tokens=max_tokens,
            )
        except Exception as exc:  # noqa: BLE001
            last = exc
            msg = str(exc)
            if "PERMISSION_DENIED" in msg or "403" in msg:
                raise  # hard deny — don't retry, abort the run
            if "429" in msg or "503" in msg or "RESOURCE_EXHAUSTED" in msg:
                await _a.sleep(2 ** i)
                continue
            raise
    raise last


def _loads_tolerant(raw: str) -> dict | None:
    """Parse LLM JSON robustly. The LLM often returns evidence quotes
    containing raw newlines / inner quotes that break strict json.loads
    ('Unterminated string'). strict=False allows control chars; the
    brace-balanced fallback strips any prose wrapper."""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        return json.loads(text, strict=False)
    except json.JSONDecodeError:
        pass
    # brace-balanced extraction
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if not in_str:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1], strict=False)
                    except json.JSONDecodeError:
                        return None
    return None


def _keyword_candidates(
    question: str, all_docs: dict[str, Path], k: int = 6,
) -> list[tuple[str, Path]]:
    """Cheap keyword overlap to pick candidate docs for the search pass."""
    import re
    stop = {
        "the", "is", "a", "an", "of", "in", "on", "for", "to", "what",
        "who", "which", "are", "any", "how", "many", "and", "or", "at",
        "this", "that", "with", "from", "by", "do", "does", "we", "have",
    }
    terms = [
        t for t in re.findall(r"[a-zA-Z0-9\-]{3,}", question.lower())
        if t not in stop
    ]
    scored: list[tuple[int, str, Path]] = []
    for did, path in all_docs.items():
        text = _read_doc(path, cap=12000).lower()
        score = sum(text.count(t) for t in terms)
        if score > 0:
            scored.append((score, did, path))
    scored.sort(reverse=True)
    return [(did, path) for _s, did, path in scored[:k]]


async def _audit_one(client, query: dict, manifest_docs: dict[str, Path],
                     all_docs: dict[str, Path]) -> dict:
    qid = query["id"]
    question = query.get("question", "")
    expected = query.get("expected_answer") or ""
    expected_refusal = query.get("expected_refusal")
    stratum = query.get("stratum") or ""
    cited = query.get("expected_citations") or []

    # Adversarial / refusal queries: grounding-check not applicable here.
    if expected_refusal:
        return {
            "id": qid, "verdict": "ADVERSARIAL_OK",
            "note": "refusal query — validated by adversarial suite, not grounding",
        }

    # Ambiguous queries: the correct answer IS a clarification request, so
    # there's no single fact to ground. Validated separately.
    if stratum == "ambiguous":
        return {
            "id": qid, "verdict": "AMBIGUOUS_OK",
            "note": "ambiguous query — expected answer is a clarification, not a fact",
        }

    # Negative queries: the correct answer is 'this does not exist in the
    # corpus'. So NOT finding it is the GROUNDED outcome, not a bug.
    if stratum == "negative" or not cited:
        # We still do a light corpus search; if the thing genuinely isn't
        # there, the negative answer is correct.
        pass

    # ---- Pass 1: check the cited docs ----
    cited_texts = []
    missing_citations = []
    for c in cited:
        p = manifest_docs.get(c) or all_docs.get(c)
        if p and p.exists():
            cited_texts.append(f"### DOC: {c}\n{_read_doc(p)}")
        else:
            missing_citations.append(c)

    pass1 = None
    if cited_texts:
        user = (
            f"QUESTION: {question}\n\n"
            f"EXPECTED ANSWER (from dataset): {expected}\n\n"
            f"CITED DOCUMENTS:\n\n" + "\n\n".join(cited_texts) + "\n\n"
            "Return JSON only."
        )
        try:
            raw = await _llm_json_retry(
                client, user=user, system=_AUDIT_SYSTEM_PROMPT, max_tokens=800,
            )
            pass1 = _loads_tolerant(raw) or {
                "supported": "NONE", "discrepancy": "audit_parse_failed",
            }
        except Exception as exc:
            pass1 = {"supported": "NONE", "discrepancy": f"audit_error: {exc}"}

    # If the audit LLM call itself failed (quota/403/network), do NOT
    # pretend the eval is wrong — surface a distinct AUDIT_ERROR so a
    # bad API run can never be mistaken for bad eval data.
    disc = (pass1 or {}).get("discrepancy") or ""
    if isinstance(disc, str) and (
        "audit_error" in disc or "audit_parse_failed" in disc
        or "PERMISSION_DENIED" in disc or "429" in disc or "403" in disc
    ):
        return {
            "id": qid, "verdict": "AUDIT_ERROR",
            "note": disc[:200], "cited": cited,
        }

    # Decide verdict from pass 1.
    supported = (pass1 or {}).get("supported", "NONE")
    if cited_texts and supported == "FULL":
        return {
            "id": qid, "verdict": "GROUNDED",
            "evidence": ((pass1 or {}).get("evidence_quote") or "")[:300],
            "corpus_says": ((pass1 or {}).get("corpus_says") or "")[:300],
        }

    # ---- Pass 2: search the rest of the corpus ----
    cands = _keyword_candidates(question, all_docs, k=6)
    cand_texts = [f"### DOC: {did}\n{_read_doc(path)}" for did, path in cands]
    search = None
    if cand_texts:
        user2 = (
            f"QUESTION: {question}\n\n"
            f"CANDIDATE DOCUMENTS:\n\n" + "\n\n".join(cand_texts) + "\n\n"
            "Return JSON only."
        )
        try:
            raw2 = await _llm_json_retry(
                client, user=user2, system=_SEARCH_SYSTEM_PROMPT, max_tokens=800,
            )
            search = _loads_tolerant(raw2) or {"found": False}
        except Exception as exc:
            search = {"found": False, "note": f"search_error: {exc}"}

    found = bool((search or {}).get("found"))
    # Guard against doc-id hallucination: keep only ids that actually exist
    # in the candidate set we showed the model.
    cand_ids = {did for did, _p in cands}
    found_docs = [d for d in ((search or {}).get("doc_ids") or []) if d in cand_ids]
    if not found_docs:
        found = False

    # Classify.
    if stratum == "negative":
        # Negative query: the expected answer asserts the thing ISN'T here.
        # If our search also fails to find it, the eval is correct → GROUNDED.
        verdict = "GROUNDED" if not found else "NEEDS_REVIEW"
    elif not cited_texts and missing_citations:
        # Cited docs not even resolvable (e.g. cross-workspace doc).
        verdict = "CITATION_WRONG" if found else "NOT_IN_CORPUS"
    elif not cited_texts and not cited:
        # No citations given by the eval at all (e.g. q034). Use search.
        verdict = "CITATION_WRONG" if found else "NOT_IN_CORPUS"
    elif supported == "NONE" and found and set(found_docs) - set(cited):
        verdict = "CITATION_WRONG"
    elif supported in ("NONE", "PARTIAL") and (pass1 or {}).get("discrepancy"):
        verdict = "ANSWER_WRONG"
    elif not found:
        verdict = "NOT_IN_CORPUS"
    else:
        verdict = "NEEDS_REVIEW"

    return {
        "id": qid,
        "verdict": verdict,
        "expected": expected[:200],
        "cited": cited,
        "missing_citations": missing_citations,
        "cited_support": supported,
        "discrepancy": (pass1 or {}).get("discrepancy"),
        "corpus_says": ((pass1 or {}).get("corpus_says") or "")[:300],
        "search_found": found,
        "search_docs": found_docs,
        "search_answer": ((search or {}).get("grounded_answer") or "")[:300],
        "search_evidence": ((search or {}).get("evidence_quote") or "")[:300],
    }


async def audit_domain(domain: str, client, concurrency: int = 5) -> list[dict]:
    queries_path = DOMAINS_DIR / domain / "queries.yaml"
    queries = yaml.safe_load(queries_path.read_text())["queries"]
    manifest_docs = _load_manifest_docs(domain)
    # Domain-scoped search set (own docs + shared cross-domain workspace).
    scoped_docs = _domain_scoped_docs(domain)

    sem = asyncio.Semaphore(concurrency)

    async def run(q):
        async with sem:
            r = await _audit_one(client, q, manifest_docs, scoped_docs)
            r["question"] = q.get("question", "")[:100]
            return r

    return await asyncio.gather(*(run(q) for q in queries))


def _print_report(domain: str, results: list[dict]) -> None:
    from collections import Counter
    c = Counter(r["verdict"] for r in results)
    print(f"\n## {domain} — {len(results)} queries")
    for v in ["GROUNDED", "ADVERSARIAL_OK", "AMBIGUOUS_OK", "CITATION_WRONG",
              "ANSWER_WRONG", "NOT_IN_CORPUS", "NEEDS_REVIEW", "AUDIT_ERROR"]:
        if c.get(v):
            print(f"  {c[v]:>3}  {v}")
    # Show the problems in detail.
    print(f"\n  --- FLAGGED (need correction) ---")
    for r in results:
        if r["verdict"] in ("CITATION_WRONG", "ANSWER_WRONG",
                            "NOT_IN_CORPUS", "NEEDS_REVIEW"):
            print(f"  [{r['verdict']}] {r['id']}: {r.get('question','')}")
            if r.get("discrepancy"):
                print(f"        issue: {r['discrepancy']}")
            if r.get("search_found"):
                print(f"        actually in: {r.get('search_docs')} → {(r.get('search_answer') or '')[:140]}")


async def main(domains: list[str], concurrency: int) -> int:
    from kb.query.llm_client import make_query_llm_client
    client = make_query_llm_client()
    if client is None:
        print("ERROR: no LLM client (set KB_GEMINI_API_KEY)", file=sys.stderr)
        return 1

    out_dir = _ROOT / "docs" / "eval_audit"
    out_dir.mkdir(parents=True, exist_ok=True)

    all_results = {}
    for domain in domains:
        print(f"\n# auditing {domain} (domain-scoped search) ...")
        results = await audit_domain(domain, client, concurrency)
        all_results[domain] = results
        _print_report(domain, results)
        (out_dir / f"{domain}.json").write_text(json.dumps(results, indent=2))

    # Aggregate
    from collections import Counter
    agg = Counter()
    for results in all_results.values():
        for r in results:
            agg[r["verdict"]] += 1
    print("\n\n# ===== AGGREGATE =====")
    total = sum(agg.values())
    for v, n in agg.most_common():
        print(f"  {n:>4}  {v}  ({n/total*100:.0f}%)")
    flagged = sum(agg[v] for v in
                  ("CITATION_WRONG", "ANSWER_WRONG", "NOT_IN_CORPUS", "NEEDS_REVIEW"))
    print(f"\n  {flagged}/{total} queries flagged for review "
          f"({flagged/total*100:.0f}% of the eval may be miscalibrated)")
    print(f"\n# per-domain JSON → {out_dir}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", help="single domain to audit")
    ap.add_argument("--all", action="store_true", help="audit all 6 domains")
    ap.add_argument("--concurrency", type=int, default=5)
    args = ap.parse_args()

    if args.all:
        doms = ["construction", "finance", "government",
                "healthcare", "legal", "mining"]
    elif args.domain:
        doms = [args.domain]
    else:
        ap.error("pass --domain <name> or --all")
    raise SystemExit(asyncio.run(main(doms, args.concurrency)))
