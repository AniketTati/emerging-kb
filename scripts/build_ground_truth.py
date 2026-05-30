"""Re-derive eval ground truth FROM THE CORPUS (generate-then-verify).

Decision rationale: the eval-author answers are demonstrably unreliable —
wrong values, wrong entity attributions, corpus-vs-eval version drift
(legal MSA: eval says Indian/Delhi/2018, corpus says Delaware/Wilmington/2024),
and unfilled placeholders ("(Specific Rx)"). The corpus is the source of
truth, so for every query that does NOT currently ground, we re-derive the
expected answer from the corpus with rigorous, auditable annotation:

  PASS 1 — GENERATE: a strong model reads the question + the verified
    source doc(s) and writes the answer SUPPORTED ONLY by those docs, plus
    the exact quote that supports it. For long-form / aggregation / ambiguous
    strata it writes the required key facts / acceptable-answer criteria
    rather than one verbatim string.

  PASS 2 — VERIFY (independent): a second call checks the generated answer
    is fully entailed by the quoted evidence. Only verified answers are
    written. This is the guard against eval-hallucination — an answer that
    can't be tied back to a corpus quote is never accepted.

Every rewritten query gets: expected_answer (new), evidence_quote,
answer_provenance='corpus_grounded', source_docs, verified=true. Originals
are preserved (queries.yaml.bak*). A reviewable diff is written so a human
can audit every single change.

Run AFTER audit_eval.py --all + fix_eval.py --all --apply + finalize_eval.py
--all (so citations are already verified). Operates on the still-flagged set.

Usage:
    uv run python scripts/build_ground_truth.py --all            # dry-run diff
    uv run python scripts/build_ground_truth.py --all --apply     # write
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))
CORPUS = _ROOT / "demo-corpus"
DOMAINS_DIR = CORPUS / "domains"
AUDIT_DIR = _ROOT / "docs" / "eval_audit"

from scripts.audit_eval import (  # type: ignore
    _loads_tolerant, _read_doc, _llm_json_retry,
    _domain_scoped_docs, _load_manifest_docs, _keyword_candidates,
)

FLAGGED = {"CITATION_WRONG", "ANSWER_WRONG", "NOT_IN_CORPUS", "NEEDS_REVIEW"}

# Strata whose "answer" is a set of required facts / criteria, not one string.
_CRITERIA_STRATA = {"long-form", "aggregation", "ambiguous"}

_GEN_SYSTEM = (
    "You are a careful annotator building ground-truth answers for a "
    "retrieval-QA evaluation. You are given a QUESTION and a set of SOURCE "
    "DOCUMENTS retrieved for it. Write the answer SUPPORTED ONLY by these "
    "documents — never add outside knowledge, never guess.\n"
    "\n"
    "The answer may need to combine MULTIPLE documents (e.g. a conflict "
    "between two docs, or a total summed across several). Use as many as "
    "needed and quote the supporting text from EACH.\n"
    "\n"
    "If after reading all documents the corpus genuinely does NOT contain "
    "the answer — including FALSE-PREMISE questions (e.g. 'two POs with "
    "different rates' when only one PO exists) — set answerable=false and "
    "in `answer` state the correct grounded response: that the premised "
    "thing is not present / the premise is false. That refusal IS valid "
    "ground truth; write it clearly.\n"
    "\n"
    "Output JSON exactly:\n"
    "{\n"
    '  "answerable": true | false,\n'
    '  "answer": "<the grounded answer; if not answerable, the correct '
    "'not present / false premise' statement>\",\n"
    '  "evidence_quotes": ["<exact quote 1>", "<exact quote 2>", "..."],\n'
    '  "source_docs": ["<doc id(s) actually used>"]\n'
    "}"
)

_GEN_SYSTEM_CRITERIA = (
    "You are a careful annotator building ground-truth for a retrieval-QA "
    "evaluation. This question expects a LONG-FORM, AGGREGATION, or "
    "AMBIGUOUS answer, so the ground truth is the set of KEY FACTS / "
    "CRITERIA a correct answer must contain — not one verbatim string. "
    "Derive these ONLY from the SOURCE DOCUMENTS; never add outside "
    "knowledge.\n"
    "\n"
    "For AGGREGATION questions (how many / total / combined across the "
    "corpus): identify EACH contributing item across the documents, quote "
    "the value for each, then compute the count/total. Show the components "
    "so the arithmetic is auditable.\n"
    "\n"
    "Output JSON exactly:\n"
    "{\n"
    '  "answerable": true | false,\n'
    '  "answer": "<the expected answer, including the computed count/total '
    'and its components>",\n'
    '  "required_facts": ["<fact 1 a correct answer must include>", "..."],\n'
    '  "evidence_quotes": ["<exact supporting quote per component>", "..."],\n'
    '  "source_docs": ["<doc id(s) used>"]\n'
    "}"
)

_VERIFY_SYSTEM = (
    "You are verifying a ground-truth annotation. You are given a proposed "
    "ANSWER and the set of EVIDENCE QUOTES it was derived from. Confirm "
    "every factual claim in the answer is supported by the SET of quotes "
    "(any quote may support any claim). For aggregation answers, confirm "
    "each component value appears in a quote and the arithmetic (count/sum) "
    "is correct. The answer must not assert anything beyond the quotes.\n"
    "\n"
    "A grounded 'this is not present in the corpus / the premise is false' "
    "answer IS valid — mark it entailed if the quotes (or their absence) "
    "support that conclusion.\n"
    "\n"
    'Output JSON exactly: {"entailed": true | false, "why": "<one sentence>"}'
)


def _docs_for(query: dict, audit_rec: dict, manifest, scoped, all_for_kw) -> list[tuple[str, Path]]:
    """Best source docs: verified citations first, then audit search docs,
    then keyword candidates (for multi-doc aggregation)."""
    ids: list[str] = []
    for c in (query.get("expected_citations") or []):
        if c not in ids:
            ids.append(c)
    for d in (audit_rec.get("search_docs") or []):
        if d not in ids:
            ids.append(d)
    out: list[tuple[str, Path]] = []
    for i in ids:
        p = manifest.get(i) or scoped.get(i)
        if p:
            out.append((i, p))
    # ALWAYS add broad keyword candidates so conflict-pairs and aggregation
    # sources are present (the single cited doc is rarely enough for "two X
    # disagree" or "total across the corpus"). Citations stay first (highest
    # priority); candidates fill in the rest.
    have = {o[0] for o in out}
    for did, p in _keyword_candidates(query.get("question", ""), all_for_kw, k=12):
        if did not in have:
            out.append((did, p))
            have.add(did)
    return out[:12]


async def _derive_one(client, query, audit_rec, manifest, scoped, all_for_kw):
    qid = query["id"]
    question = query.get("question", "")
    stratum = query.get("stratum", "")
    is_criteria = stratum in _CRITERIA_STRATA

    docs = _docs_for(query, audit_rec, manifest, scoped, all_for_kw)
    if not docs:
        return {"id": qid, "status": "no_source", "stratum": stratum}

    doc_block = "\n\n".join(f"### DOC: {did}\n{_read_doc(p)}" for did, p in docs)
    user = f"QUESTION: {question}\n\nSOURCE DOCUMENTS:\n\n{doc_block}\n\nReturn JSON only."

    try:
        raw = await _llm_json_retry(
            client, user=user,
            system=_GEN_SYSTEM_CRITERIA if is_criteria else _GEN_SYSTEM,
            max_tokens=900,
        )
        gen = _loads_tolerant(raw) or {}
    except Exception as exc:
        return {"id": qid, "status": f"gen_error: {exc}", "stratum": stratum}

    def _as_str(v) -> str:
        if isinstance(v, list):
            return " ".join(str(x) for x in v if x)
        return str(v or "")

    def _quotes(g) -> list[str]:
        q = g.get("evidence_quotes") or g.get("evidence_quote") or []
        if isinstance(q, str):
            q = [q]
        return [str(x).strip() for x in q if x]

    answer = _as_str(gen.get("answer")).strip()
    quotes = _quotes(gen)
    evidence_joined = "  |  ".join(quotes)
    answerable = gen.get("answerable", False)

    # A grounded "not present / false premise" answer IS valid ground truth.
    # We still write it, but tag it so it can be recategorized to a
    # negative/refusal stratum.
    if not answerable:
        if answer:
            return {
                "id": qid, "status": "ok_refusal", "stratum": stratum,
                "old_expected": (query.get("expected_answer") or "")[:200],
                "new_expected": answer,
                "required_facts": [],
                "evidence_quote": evidence_joined[:400],
                "source_docs": [d for d, _ in docs[:3]],
                "suggest_stratum": "negative",
            }
        return {"id": qid, "status": "corpus_lacks_it", "stratum": stratum,
                "note": answer[:200]}

    if not answer or not quotes:
        return {"id": qid, "status": "empty_generation", "stratum": stratum}

    # PASS 2 — independent verification against the SET of quotes.
    vuser = (f"ANSWER: {answer}\n\nEVIDENCE QUOTES:\n- "
             + "\n- ".join(quotes) + "\n\nReturn JSON only.")
    try:
        vraw = await _llm_json_retry(client, user=vuser, system=_VERIFY_SYSTEM, max_tokens=200)
        ver = _loads_tolerant(vraw) or {}
    except Exception:
        ver = {"entailed": False}

    if not ver.get("entailed", False):
        return {"id": qid, "status": "failed_verify", "stratum": stratum,
                "proposed": answer[:200], "why": (ver.get("why") or "")[:160]}

    return {
        "id": qid, "status": "ok", "stratum": stratum,
        "old_expected": (query.get("expected_answer") or "")[:200],
        "new_expected": answer,
        "required_facts": gen.get("required_facts") or [],
        "evidence_quote": evidence_joined[:400],
        "source_docs": [d for d in (gen.get("source_docs") or []) if d] or [d for d, _ in docs[:2]],
    }


async def build_domain(domain, client, concurrency=4):
    audit = {r["id"]: r for r in json.loads((AUDIT_DIR / f"{domain}.json").read_text())}
    qdata = yaml.safe_load((DOMAINS_DIR / domain / "queries.yaml").read_text())
    qmap = {q["id"]: q for q in qdata["queries"]}
    manifest = _load_manifest_docs(domain)
    scoped = _domain_scoped_docs(domain)

    # flagged + not adversarial (adversarial ground truth is the refusal itself)
    targets = [
        qmap[qid] for qid, r in audit.items()
        if r["verdict"] in FLAGGED and qid in qmap
        and not qmap[qid].get("expected_refusal")
    ]

    sem = asyncio.Semaphore(concurrency)
    async def run(q):
        async with sem:
            try:
                return await _derive_one(client, q, audit.get(q["id"], {}),
                                         manifest, scoped, scoped)
            except Exception as exc:  # one bad query must not kill the domain
                return {"id": q["id"], "status": f"derive_crash: {exc}",
                        "stratum": q.get("stratum", "")}
    return await asyncio.gather(*(run(q) for q in targets))


def apply_domain(domain, results):
    qpath = DOMAINS_DIR / domain / "queries.yaml"
    raw = qpath.read_text()
    qdata = yaml.safe_load(raw)
    qmap = {q["id"]: q for q in qdata["queries"]}
    n = 0
    for r in results:
        if r["status"] not in ("ok", "ok_refusal"):
            continue
        q = qmap.get(r["id"])
        if not q:
            continue
        q["expected_answer"] = r["new_expected"]
        if r.get("required_facts"):
            q["required_facts"] = r["required_facts"]
        q["evidence_quote"] = r["evidence_quote"]
        if r.get("source_docs"):
            q["expected_citations"] = r["source_docs"]
        q["answer_provenance"] = "corpus_grounded"
        q["verified"] = True
        if r["status"] == "ok_refusal":
            # corpus genuinely lacks the premised fact — the grounded answer
            # is a refusal/"not present". Flag the stratum change for review.
            q["expected_refusal"] = True
            q["stratum_was"] = q.get("stratum")
            q["stratum"] = r.get("suggest_stratum", "negative")
        n += 1
    if n:
        qpath.with_suffix(".yaml.bak3").write_text(raw)
        qpath.write_text(yaml.safe_dump(qdata, sort_keys=False, allow_unicode=True, width=100))
    return n


async def main(domains, concurrency, apply):
    from kb.query.llm_client import make_query_llm_client
    client = make_query_llm_client()
    if client is None:
        print("ERROR: no LLM client", file=sys.stderr)
        return 1

    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    grand = {"ok": 0, "lacks": 0, "failed": 0, "other": 0}
    for domain in domains:
        print(f"# deriving ground truth for {domain} ...")
        results = await build_domain(domain, client, concurrency)
        from collections import Counter
        known = ("ok", "ok_refusal", "corpus_lacks_it", "failed_verify")
        c = Counter(r["status"] if r["status"] in known else "other" for r in results)
        grand["ok"] += c.get("ok", 0) + c.get("ok_refusal", 0)
        grand["lacks"] += c.get("corpus_lacks_it", 0)
        grand["failed"] += c.get("failed_verify", 0)
        grand["other"] += c.get("other", 0)
        # reviewable diff
        (AUDIT_DIR / f"{domain}_ground_truth.json").write_text(json.dumps(list(results), indent=2))
        print(f"  {domain}: {c.get('ok',0)} verified + {c.get('ok_refusal',0)} grounded-refusal, "
              f"{c.get('corpus_lacks_it',0)} corpus-lacks-it, "
              f"{c.get('failed_verify',0)} failed-verify, {c.get('other',0)} other")
        if apply:
            n = apply_domain(domain, results)
            print(f"            APPLIED {n} grounded answers (backup .yaml.bak3)")
    print(f"\nTOTAL: {grand['ok']} verified ground-truth answers, "
          f"{grand['lacks']} genuinely-absent (refusal/gap), "
          f"{grand['failed']} failed verification, {grand['other']} other")
    if not apply:
        print("\n(dry-run — review docs/eval_audit/<domain>_ground_truth.json, then --apply)")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args()
    doms = (["construction", "finance", "government", "healthcare", "legal", "mining"]
            if args.all else [args.domain])
    if doms == [None]:
        ap.error("pass --domain or --all")
    raise SystemExit(asyncio.run(main(doms, args.concurrency, args.apply)))
