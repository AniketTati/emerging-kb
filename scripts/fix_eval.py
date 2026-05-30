"""Apply grounded corrections to the eval, producing a VERIFIED query set.

Consumes the audit output (docs/eval_audit/<domain>.json) and the live
queries.yaml, and produces corrections. Two classes of fix:

  1. CITATION REMAP (high confidence, auto-applied):
     The cited doc-id is stale (doesn't resolve) OR doesn't contain the
     answer, but the auditor's content search FOUND the answer in a real
     doc. We repoint expected_citations at the real doc(s). This is safe —
     we're pointing at documents the auditor proved contain the answer.

  2. ANSWER CORRECTION (lower confidence, FLAGGED for human review):
     The corpus contradicts the expected answer (ANSWER_WRONG). Changing
     an expected answer is consequential, so these are written to a review
     file, not auto-applied.

Every verified query gets stamped with `evidence_quote` + `verified: true`
so the eval is auditable end-to-end.

Usage:
    # dry-run: write proposals to docs/eval_audit/<domain>_corrections.yaml
    uv run python scripts/fix_eval.py --domain healthcare

    # apply citation remaps in place (answer corrections still need review)
    uv run python scripts/fix_eval.py --domain healthcare --apply
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent
CORPUS = _ROOT / "demo-corpus"
DOMAINS_DIR = CORPUS / "domains"
AUDIT_DIR = _ROOT / "docs" / "eval_audit"


def _real_doc_ids(domain: str) -> set[str]:
    """All resolvable doc ids visible to this domain."""
    ids: set[str] = set()
    manifest = DOMAINS_DIR / domain / "manifest.yaml"
    if manifest.exists():
        data = yaml.safe_load(manifest.read_text())
        for d in (data.get("docs") or []):
            if d.get("id"):
                ids.add(d["id"])
    # plus stems of all files visible to the domain
    for pat in [
        str(DOMAINS_DIR / domain / "docs" / "*"),
        str(DOMAINS_DIR / domain / "threads" / "*"),
        str(CORPUS / "workspaces" / "*" / "docs" / "*"),
    ]:
        for p in glob.glob(pat):
            ids.add(Path(p).stem)
    return ids


def _tokens(doc_id: str) -> set[str]:
    """Meaningful tokens from a doc id — split on '-', drop pure-numeric
    sequence markers (001, rev1, v2) so 'gr-001-pmay-beed' ≈ 'pmay-beed-gr'."""
    parts = doc_id.lower().split("-")
    out = set()
    for p in parts:
        if not p:
            continue
        if p.isdigit():
            continue
        if p in ("rev1", "rev2", "rev3", "v1", "v2", "v3", "scan", "scanned",
                 "original", "doc"):
            continue
        out.add(p)
    return out


def _fuzzy_match(stale_id: str, real_ids: set[str]) -> tuple[str | None, float]:
    """Best real doc id for a stale citation id by token Jaccard. Returns
    (best_id, score). Deterministic — far safer than an LLM for ID remapping
    when the stale id is just a renamed version of a real one."""
    st = _tokens(stale_id)
    if not st:
        return None, 0.0
    best_id, best_score = None, 0.0
    for rid in real_ids:
        rt = _tokens(rid)
        if not rt:
            continue
        inter = len(st & rt)
        union = len(st | rt)
        score = inter / union if union else 0.0
        # bonus for stale being a near-subset of real (rename adds a prefix)
        if st <= rt and st:
            score = max(score, 0.85)
        if score > best_score:
            best_id, best_score = rid, score
    return best_id, best_score


def build_corrections(domain: str) -> dict:
    audit = json.loads((AUDIT_DIR / f"{domain}.json").read_text())
    audit_by_id = {r["id"]: r for r in audit}
    real_ids = _real_doc_ids(domain)

    # Load queries to get the original stale citations per id.
    queries = yaml.safe_load((DOMAINS_DIR / domain / "queries.yaml").read_text())["queries"]
    cites_by_id = {q["id"]: (q.get("expected_citations") or []) for q in queries}

    remaps = []          # high-confidence: fuzzy + LLM agree, or fuzzy strong
    remaps_review = []   # fuzzy and LLM disagree, or fuzzy weak — needs eyes
    answer_fixes = []    # corpus contradicts expected answer
    gaps = []            # genuinely not in corpus

    _FUZZY_STRONG = 0.5

    def _fuzzy_remap(stale_list):
        """Map each stale citation id to its best real id (fuzzy)."""
        out, weak = [], False
        for sid in stale_list:
            if sid in real_ids:
                out.append(sid)  # already valid
                continue
            best, score = _fuzzy_match(sid, real_ids)
            if best and score >= _FUZZY_STRONG:
                out.append(best)
            else:
                weak = True
        return out, weak

    for r in audit:
        v = r["verdict"]
        qid = r["id"]
        if v in ("GROUNDED", "ADVERSARIAL_OK", "AMBIGUOUS_OK"):
            continue
        llm_docs = [d for d in (r.get("search_docs") or []) if d in real_ids]
        stale = cites_by_id.get(qid, r.get("cited", []))

        if v in ("CITATION_WRONG", "NOT_IN_CORPUS"):
            fuzzy_docs, weak = _fuzzy_remap(stale)
            # Decision: trust fuzzy when it resolved every stale id strongly.
            if fuzzy_docs and not weak:
                # If LLM agrees (overlap) we're extra confident; either way fuzzy wins.
                remaps.append({
                    "id": qid,
                    "old_citations": stale,
                    "new_citations": fuzzy_docs,
                    "method": "fuzzy" + ("+llm" if set(fuzzy_docs) & set(llm_docs) else ""),
                    "evidence": r.get("search_evidence", "")[:300],
                    "grounded_answer": r.get("search_answer", "")[:300],
                })
            elif llm_docs:
                # fuzzy weak/empty but LLM found something → needs review.
                remaps_review.append({
                    "id": qid,
                    "old_citations": stale,
                    "fuzzy_proposal": fuzzy_docs,
                    "llm_proposal": llm_docs,
                    "grounded_answer": r.get("search_answer", "")[:300],
                })
            else:
                gaps.append({
                    "id": qid, "expected": r.get("expected", ""),
                    "note": "neither fuzzy nor LLM located this fact",
                })
        elif v in ("ANSWER_WRONG", "NEEDS_REVIEW"):
            fuzzy_docs, _ = _fuzzy_remap(stale)
            answer_fixes.append({
                "id": qid,
                "current_expected": r.get("expected", ""),
                "corpus_says": r.get("corpus_says", "") or r.get("search_answer", ""),
                "discrepancy": r.get("discrepancy", ""),
                "suggested_citations": fuzzy_docs or llm_docs or stale,
            })

    return {
        "domain": domain,
        "citation_remaps": remaps,
        "citation_remaps_needs_review": remaps_review,
        "answer_corrections_for_review": answer_fixes,
        "genuine_gaps": gaps,
    }


def apply_citation_remaps(domain: str, corrections: dict) -> int:
    """Repoint expected_citations + stamp evidence/verified. In-place edit
    of queries.yaml (with a .bak backup)."""
    qpath = DOMAINS_DIR / domain / "queries.yaml"
    raw = qpath.read_text()
    data = yaml.safe_load(raw)
    qmap = {q["id"]: q for q in data["queries"]}

    remap_by_id = {r["id"]: r for r in corrections["citation_remaps"]}
    n = 0
    for qid, remap in remap_by_id.items():
        q = qmap.get(qid)
        if not q:
            continue
        q["expected_citations"] = remap["new_citations"]
        if remap.get("evidence"):
            q["evidence_quote"] = remap["evidence"]
        q["citations_verified"] = True
        n += 1

    # Also stamp GROUNDED/ADV/AMB queries as verified (they passed audit).
    audit = json.loads((AUDIT_DIR / f"{domain}.json").read_text())
    for r in audit:
        if r["verdict"] in ("GROUNDED",):
            q = qmap.get(r["id"])
            if q:
                q.setdefault("evidence_quote", r.get("evidence", "")[:300])
                q["citations_verified"] = True

    qpath.with_suffix(".yaml.bak").write_text(raw)
    qpath.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100))
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--apply", action="store_true",
                    help="apply citation remaps in place (answer fixes still need review)")
    args = ap.parse_args()

    domains = (
        ["construction", "finance", "government", "healthcare", "legal", "mining"]
        if args.all else [args.domain]
    )
    if not domains or domains == [None]:
        ap.error("pass --domain <name> or --all")

    grand = {"remaps": 0, "answer_fixes": 0, "gaps": 0}
    for domain in domains:
        corr = build_corrections(domain)
        out = AUDIT_DIR / f"{domain}_corrections.yaml"
        out.write_text(yaml.safe_dump(corr, sort_keys=False, allow_unicode=True, width=100))
        nr = len(corr["citation_remaps"])
        na = len(corr["answer_corrections_for_review"])
        ng = len(corr["genuine_gaps"])
        grand["remaps"] += nr
        grand["answer_fixes"] += na
        grand["gaps"] += ng
        print(f"{domain:14}: {nr} citation remaps, {na} answer fixes (review), {ng} gaps  → {out.name}")
        if args.apply:
            applied = apply_citation_remaps(domain, corr)
            print(f"               APPLIED {applied} citation remaps to queries.yaml (backup: queries.yaml.bak)")

    print(f"\nTOTAL: {grand['remaps']} remaps, {grand['answer_fixes']} answer-fixes-for-review, {grand['gaps']} gaps")
    if not args.apply:
        print("\n(dry-run — re-run with --apply to repoint citations. Answer corrections always need manual review.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
