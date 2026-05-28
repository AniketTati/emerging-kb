"""Bug D semantic merger via LLM-judge.

The regex normalizer (scripts/normalize_field_names.py) catches cases
where field names differ by suffix (drawing_no ↔ drawing_number) but
misses semantic divergence like:
  - total_cost_premium ↔ total_cost_inr      (same concept, different stems)
  - approval_description ↔ approval_details
  - mahalaxmi_cost_engineer ↔ cost_engineer

This script asks Gemini Flash, per doctype, to look at the actual
field names + sample values + descriptions and group equivalents.
It produces a YAML proposal for human review BEFORE applying.

Usage:
    # Step 1 — generate proposal (dry-run by default)
    uv run python scripts/normalize_field_names_llm.py \\
        --workspace c0000000-0000-0000-0000-000000000001 \\
        --out /tmp/field_merge_proposal.yaml

    # Step 2 — review the YAML, edit canonicals if you disagree

    # Step 3 — apply
    uv run python scripts/normalize_field_names_llm.py \\
        --workspace c0000000-0000-0000-0000-000000000001 \\
        --apply /tmp/field_merge_proposal.yaml
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from kb.config import get_settings  # noqa: E402
from kb.db.pool import open_connection  # noqa: E402


_JUDGE_SYSTEM_PROMPT = (
    "You are a schema curator. You'll see field names extracted "
    "from documents of one type, along with sample values + "
    "descriptions. Some pairs of names are SEMANTICALLY EQUIVALENT — "
    "different names for the same concept (e.g. `total_cost_premium` "
    "and `total_cost_inr` both mean 'the monetary delta from this "
    "change order'). Your job: group equivalent names + pick a "
    "canonical for each group.\n"
    "\n"
    "Rules:\n"
    " - Only group names you're CONFIDENT mean the same thing. When "
    "in doubt, leave them separate. Wrong merges silently corrupt "
    "the database.\n"
    " - Field names with semantically distinct suffixes are NOT "
    "equivalent: `_date` ≠ `_amount`, `_percentage` ≠ `_amount`, "
    "`_status` ≠ `_type`.\n"
    " - When grouping, prefer the SHORTEST clear canonical name. "
    "Ties: pick the most common variant (which is implicit from "
    "value-sample frequency in the input).\n"
    " - Each input field can appear in at most ONE group. Ungrouped "
    "fields are fine — just omit them.\n"
    "\n"
    "Return JSON exactly: "
    '{"groups": [{"canonical": "<name>", "variants": ["<name>", "<name>"], '
    '"reason": "<one sentence>"}]}.'
    "Only include groups with 2+ variants. Empty `groups: []` if "
    "nothing in this doctype should be merged."
)


async def _gather_doctype_fields(
    conn, workspace_id: str,
) -> dict[str, list[dict]]:
    """Return {doctype: [{name, n_docs, sample_value, description}, ...]}."""
    cur = await conn.execute(
        """
        SELECT pf.inferred_doc_type,
               pf.field_name,
               count(DISTINCT pf.file_id) AS n_docs,
               (array_agg(pf.value_text ORDER BY length(pf.value_text) DESC))[1] AS sample_value,
               COALESCE(
                   (array_agg(pf.field_description ORDER BY length(pf.field_description) DESC NULLS LAST))[1],
                   ''
               ) AS description
          FROM proposed_fields pf
         WHERE pf.workspace_id = %s
           AND pf.inferred_doc_type IS NOT NULL
           AND pf.value_text IS NOT NULL
         GROUP BY pf.inferred_doc_type, pf.field_name
         ORDER BY pf.inferred_doc_type, n_docs DESC, pf.field_name
        """,
        (workspace_id,),
    )
    rows = await cur.fetchall()
    out: dict[str, list[dict]] = defaultdict(list)
    for dt, name, n_docs, sample, desc in rows:
        out[str(dt)].append({
            "name": str(name),
            "n_docs": int(n_docs),
            "sample_value": str(sample or "")[:80],
            "description": str(desc or "")[:120],
        })
    return out


def _format_judge_user_msg(doctype: str, fields: list[dict]) -> str:
    lines = [f"doc_type: {doctype}", "", "fields:"]
    for f in fields:
        d = f["description"]
        d_str = f" — {d}" if d else ""
        lines.append(
            f"  - name: {f['name']}  (n_docs={f['n_docs']}, "
            f"sample={f['sample_value']!r}){d_str}"
        )
    lines.append("")
    lines.append("Return JSON only.")
    return "\n".join(lines)


async def propose_merges(
    workspace_id: str, out_path: Path, min_docs_per_doctype: int = 2,
) -> int:
    from kb.query.llm_client import make_query_llm_client

    client = make_query_llm_client()
    if client is None:
        print("ERROR: no LLM client; set KB_GEMINI_API_KEY", file=sys.stderr)
        return 1

    settings = get_settings()
    async with open_connection(settings.database_url) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)",
            (workspace_id,),
        )
        by_dt = await _gather_doctype_fields(conn, workspace_id)

    # Only doctypes with 2+ docs (single-doc doctypes have no
    # cross-doc divergence to fix).
    candidates = {}
    for dt, fields in by_dt.items():
        # Count unique files across fields — proxy for "this doctype
        # has multiple docs."
        max_n = max((f["n_docs"] for f in fields), default=0)
        if max_n >= min_docs_per_doctype and len(fields) >= 2:
            candidates[dt] = fields
    print(
        f"# checking {len(candidates)} doctypes (of {len(by_dt)} total; "
        f"others have <{min_docs_per_doctype} docs or <2 fields)"
    )

    proposal: dict = {"workspace_id": workspace_id, "doctypes": {}}
    for dt, fields in sorted(candidates.items()):
        print(f"  → {dt} ({len(fields)} fields)…", end="", flush=True)
        user_msg = _format_judge_user_msg(dt, fields)
        try:
            raw = await client.generate_json(
                user=user_msg,
                system=_JUDGE_SYSTEM_PROMPT,
                max_tokens=2000,
            )
        except Exception as exc:
            print(f" LLM_ERROR: {exc}")
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            import re
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if not m:
                print(" PARSE_ERROR")
                continue
            try:
                data = json.loads(m.group(0))
            except Exception:
                print(" PARSE_ERROR")
                continue
        groups = data.get("groups") or []
        # Validate each group: variants must be in our input
        valid_field_names = {f["name"] for f in fields}
        clean_groups = []
        for g in groups:
            canon = g.get("canonical", "").strip()
            variants = [v.strip() for v in (g.get("variants") or []) if v.strip()]
            reason = g.get("reason", "").strip()
            # All variants (and canonical) must be in our input
            all_present = (
                canon in valid_field_names
                and all(v in valid_field_names for v in variants)
                and len(variants) >= 2
                and canon in variants
            )
            if not all_present:
                continue
            clean_groups.append({
                "canonical": canon,
                "variants": variants,
                "reason": reason,
            })
        proposal["doctypes"][dt] = {"groups": clean_groups}
        print(f" {len(clean_groups)} merge group(s)")

    out_path.write_text(yaml.dump(proposal, sort_keys=False, default_flow_style=False))
    n_total_groups = sum(len(d["groups"]) for d in proposal["doctypes"].values())
    print()
    print(f"# {n_total_groups} merge groups proposed across "
          f"{sum(1 for d in proposal['doctypes'].values() if d['groups'])} doctypes")
    print(f"# proposal → {out_path}")
    print("# review + edit before applying with --apply")
    return 0


async def apply_proposal(workspace_id: str, proposal_path: Path) -> int:
    settings = get_settings()
    with open(proposal_path) as fh:
        proposal = yaml.safe_load(fh)
    if proposal.get("workspace_id") != workspace_id:
        print(
            f"ERROR: proposal workspace {proposal.get('workspace_id')!r} "
            f"!= --workspace {workspace_id!r}",
            file=sys.stderr,
        )
        return 1
    n_renames = 0
    async with open_connection(settings.database_url) as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)",
                (workspace_id,),
            )
            for dt, dt_block in (proposal.get("doctypes") or {}).items():
                for group in (dt_block.get("groups") or []):
                    canon = group["canonical"]
                    variants = group["variants"]
                    for old in variants:
                        if old == canon:
                            continue
                        # Drop conflicts on (file_id, canonical_name)
                        # before rename to avoid UNIQUE violation.
                        await conn.execute(
                            """
                            DELETE FROM proposed_fields pf_old
                             USING proposed_fields pf_new
                             WHERE pf_old.workspace_id = %s
                               AND pf_old.inferred_doc_type = %s
                               AND pf_old.field_name = %s
                               AND pf_new.file_id = pf_old.file_id
                               AND pf_new.field_name = %s
                               AND pf_new.id <> pf_old.id
                            """,
                            (workspace_id, dt, old, canon),
                        )
                        cur = await conn.execute(
                            """
                            UPDATE proposed_fields
                               SET field_name = %s
                             WHERE workspace_id = %s
                               AND inferred_doc_type = %s
                               AND field_name = %s
                            """,
                            (canon, workspace_id, dt, old),
                        )
                        rc = int(getattr(cur, "rowcount", 0))
                        print(
                            f"  {dt}:  {old}  →  {canon}  "
                            f"({rc} rows)"
                        )
                        n_renames += rc
    print()
    print(f"# applied {n_renames} renames")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--out", type=Path, help="Write proposal YAML here (default mode)")
    ap.add_argument("--apply", type=Path, help="Apply a previously-generated proposal YAML")
    ap.add_argument("--min-docs", type=int, default=2)
    args = ap.parse_args()
    if args.apply:
        return asyncio.run(apply_proposal(args.workspace, args.apply))
    if not args.out:
        ap.error("either --out (propose) or --apply <yaml> required")
    return asyncio.run(propose_merges(args.workspace, args.out, args.min_docs))


if __name__ == "__main__":
    raise SystemExit(main())
