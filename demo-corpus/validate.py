#!/usr/bin/env python3
"""Validate a demo-corpus domain's docs against its manifest.

Asserts:
  1. Every `docs[].file` path in the manifest exists on disk.
  2. Every `chain` referenced in any doc's frontmatter resolves to a
     chain id in `../entities.yaml`.
  3. Every `parties[]` id in any doc's frontmatter resolves to an
     org/people entity id in `../entities.yaml`.
  4. Every `parent_doc` reference resolves to another doc in the
     manifest.
  5. Stressors declared in the manifest are present in the doc text
     (e.g. a doc claiming `rare_clause_4hr_delivery` must contain
     "4 hour" or "four-hour" somewhere).
  6. PII docs actually contain a synthetic PAN/Aadhaar string.

Usage:
    python -m demo-corpus.validate legal
    python -m demo-corpus.validate                  # validates ALL domains
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:
    raise SystemExit("PyYAML required: pip install pyyaml") from exc


REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS_ROOT = REPO_ROOT / "demo-corpus"
ENTITIES_PATH = CORPUS_ROOT / "entities.yaml"


# ---------------------------------------------------------------------------
# Stressor → text-pattern map. The validator checks the doc actually
# contains the marker. Patterns are case-insensitive regex; either side
# of the |alternation| acceptable.
# ---------------------------------------------------------------------------
STRESSOR_PATTERNS: dict[str, str] = {
    "rare_clause_4hr_delivery":           r"(four[- ]hour|4[- ]hour|within (4|four) hours?)",
    "rare_mfn_clause":                    r"(most[- ]favored[- ]nation|MFN)",
    "rare_non_compete_36mo":              r"(thirty[- ]six.*months|36.*months)",
    "rare_unlimited_liability":           r"(unlimited.*liability|without limit)",
    "rare_clause_perpetual_obligation":   r"(perpetual)",
    "conflict_source_payment_terms":      r"(NET[- ](30|45|60|90))",
    "conflict_source_indemnity_cap":      r"(indemnif|cap)",
    "conflict_source_nda_term":           r"(years?|term)",
    "chain_member":                       r".",                              # always present
    "chain_winner":                       r".",
    "authority_resolved_winner":          r".",
    "authority_draft_vs_signed":          r"(draft|tbd|unexecuted|placeholder)",
    "pii_pan":                            r"\b[A-Z]{5}\d{4}[A-Z]\b",         # PAN format
    "pii_aadhaar":                        r"\b\d{4}\s?\d{4}\s?\d{4}\b",      # Aadhaar 12-digit
    "parser_scanned_ocr":                 r"(scanned|OCR'?d|OCR'?ed|originally scanned)",
    "cross_domain_finance":               r"(HDFC|cross[- ]domain|finance)",
    "jurisdiction_singapore_filter":      r"Singapore",
    "email_chain_l0_5":                   r"(In-Reply-To|Message-ID)",
    "conflict_unresolved_nda_term":       r".",
    "conflict_resolution_target":         r".",
}


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except FileNotFoundError:
        die(f"missing file: {path}")
    except yaml.YAMLError as exc:
        die(f"{path}: invalid YAML — {exc}")


def die(msg: str) -> None:
    print(f"❌ {msg}", file=sys.stderr)
    sys.exit(1)


def read_doc_body(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        die(f"manifest references missing doc: {path}")


def validate_domain(domain: str, entities: dict[str, Any]) -> int:
    """Validate one domain. Returns count of validated docs."""
    manifest_path = CORPUS_ROOT / "domains" / domain / "manifest.yaml"
    manifest = load_yaml(manifest_path)
    if manifest.get("domain") != domain:
        die(f"manifest domain mismatch: expected {domain!r}, got "
            f"{manifest.get('domain')!r}")

    org_ids = {o["id"] for o in entities.get("organizations", [])}
    person_ids = {p["id"] for p in entities.get("people", [])}
    chain_ids = {c["id"] for c in entities.get("chains", [])}
    valid_party_ids = org_ids | person_ids

    docs = manifest.get("docs") or []
    if not docs:
        die(f"manifest for {domain} has no docs")

    doc_ids = {d["id"] for d in docs}
    errors: list[str] = []

    for d in docs:
        doc_id = d["id"]
        rel_path = d["file"]
        full_path = REPO_ROOT / rel_path

        if not full_path.exists():
            errors.append(f"{doc_id}: file missing — {rel_path}")
            continue

        body = read_doc_body(full_path)

        # 1. Chain validity
        if (chain := d.get("chain")) and chain not in chain_ids:
            errors.append(f"{doc_id}: unknown chain id {chain!r}")

        # 2. Parties validity
        for p in d.get("parties") or []:
            if p not in valid_party_ids:
                errors.append(
                    f"{doc_id}: party {p!r} not in entity registry"
                )

        # 3. Subject (employment-style) validity
        if (subj := d.get("subject")) and subj not in person_ids:
            errors.append(f"{doc_id}: subject {subj!r} not in entity registry")

        # 4. Parent doc resolves
        if (parent := d.get("parent_doc")) and parent not in doc_ids:
            errors.append(
                f"{doc_id}: parent_doc {parent!r} not in this manifest"
            )

        # 5. Stressor markers actually present
        for s in d.get("stressors") or []:
            if pat := STRESSOR_PATTERNS.get(s):
                if not re.search(pat, body, flags=re.IGNORECASE):
                    errors.append(
                        f"{doc_id}: stressor {s!r} declared but pattern "
                        f"r'{pat}' not found in body"
                    )

    if errors:
        for err in errors:
            print(f"  ❌ {err}", file=sys.stderr)
        die(f"{domain}: {len(errors)} validation error(s)")

    print(f"✅ {domain}: {len(docs)} docs validated cleanly")
    return len(docs)


def main(argv: list[str]) -> int:
    entities = load_yaml(ENTITIES_PATH)

    if len(argv) >= 2:
        target_domains = [argv[1]]
    else:
        target_domains = [
            p.name for p in (CORPUS_ROOT / "domains").iterdir()
            if p.is_dir() and (p / "manifest.yaml").exists()
        ]

    total = 0
    for d in target_domains:
        total += validate_domain(d, entities)
    print(f"\n✅ Total: {total} docs validated across "
          f"{len(target_domains)} domain(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
