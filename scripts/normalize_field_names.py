"""Bug D backfill — merge near-duplicate scalar field names per doctype.

Same doctype, different docs, equivalent concept stored under different
field names — e.g. CO-005 has `total_cost_premium`, CO-018 has
`total_cost_inr`. Cross-doc aggregations break because the LLM can only
filter on ONE field name.

This script:
1. Groups proposed_fields rows by (inferred_doc_type, canonical_form)
   where canonical_form strips known-redundant suffixes.
2. For each cluster of 2+ variants, picks the most-common variant as
   canonical (ties: shorter name wins, then alphabetic).
3. UPDATEs proposed_fields.field_name to canonical for all variants.

Safe suffixes to strip (treated as synonymous):
   _no, _num, _number, _id        — identifier marker
   _amt, _amount, _value          — value marker
   _inr, _usd, _eur, _rs, _gbp    — currency marker
   _percentage, _percent, _pct    — percent marker

Suffixes NEVER stripped (semantically distinct):
   _date, _time, _year, _month, _day, _at
   _status, _type, _kind, _category
   _start, _end, _from, _to
   _before, _after, _change

Run with --dry-run first to inspect proposed merges.

Usage:
    uv run python scripts/normalize_field_names.py \\
        --workspace c0000000-0000-0000-0000-000000000001 \\
        [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from kb.config import get_settings  # noqa: E402
from kb.db.pool import open_connection  # noqa: E402


# Suffixes that are synonymous (safe to strip when comparing names).
# Calibrated against the construction workspace: includes identifier
# markers + amount markers + currency markers. EXCLUDES `_value`
# (sometimes distinct from `_amount`), `_percentage` / `_percent` /
# `_pct` (these are RATIOS, not the same as absolute amounts —
# merging `gst_percentage` with `gst_amount` would silently corrupt
# the data).
_SYNONYM_SUFFIXES = (
    "_number", "_num", "_no",      # identifier marker variants
    "_id",                          # identifier marker
    "_amount", "_amt",              # value marker
    "_inr", "_usd", "_eur", "_rs", "_gbp",  # currency marker
)

# Suffixes that mark a semantically distinct concept (DO NOT strip).
# Listed for documentation; the canonicalizer just doesn't strip them.
_DISTINCT_SUFFIXES = (
    "_date", "_time", "_year", "_month", "_day", "_at",
    "_status", "_type", "_kind", "_category",
    "_start", "_end", "_from", "_to",
    "_before", "_after", "_change", "_diff",
    "_count", "_total",
    "_value",                              # sometimes distinct from _amount
    "_percentage", "_percent", "_pct",     # ratios, NOT the same as amounts
)


def _canonical_form(name: str) -> str:
    """Repeatedly strip a synonym suffix until stable.

    Examples:
      drawing_number   → drawing
      drawing_no       → drawing
      total_cost_inr   → total_cost
      total_cost_amount → total_cost
      po_number        → po
      po_no            → po
      revision_id      → revision
      sheet_number     → sheet
      meeting_date     → meeting_date    (NOT stripped — _date is distinct)
      meeting_number   → meeting
    """
    n = name.lower()
    for _ in range(5):  # bounded iteration
        stripped = False
        for suffix in _SYNONYM_SUFFIXES:
            if n.endswith(suffix) and len(n) > len(suffix):
                n = n[: -len(suffix)]
                stripped = True
                break
        if not stripped:
            break
    return n


def _pick_canonical(variants: list[tuple[str, int]]) -> str:
    """Pick the canonical field name from a cluster of variants.

    Tiebreaker order:
      1. Highest n_docs (most established)
      2. Shortest name (less suffix noise)
      3. Alphabetic (deterministic)
    """
    return min(variants, key=lambda v: (-v[1], len(v[0]), v[0]))[0]


async def main(workspace_id: str, dry_run: bool) -> int:
    settings = get_settings()
    async with open_connection(settings.database_url) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)",
            (workspace_id,),
        )
        cur = await conn.execute(
            """
            SELECT inferred_doc_type, field_name, count(DISTINCT file_id) AS n_docs
              FROM proposed_fields
             WHERE workspace_id = %s AND inferred_doc_type IS NOT NULL
             GROUP BY inferred_doc_type, field_name
             ORDER BY inferred_doc_type, n_docs DESC, field_name
            """,
            (workspace_id,),
        )
        rows = await cur.fetchall()

    # Group: (doctype, canonical) → [(field_name, n_docs), ...]
    by_cluster: dict[tuple[str, str], list[tuple[str, int]]] = defaultdict(list)
    for dt, fn, n in rows:
        key = (str(dt), _canonical_form(str(fn)))
        by_cluster[key].append((str(fn), int(n)))

    # Filter to clusters with 2+ distinct variants.
    merges: list[tuple[str, str, str]] = []  # (doctype, old_name, new_name)
    for (dt, canon), variants in sorted(by_cluster.items()):
        names = sorted({v[0] for v in variants})
        if len(names) < 2:
            continue
        canonical = _pick_canonical(variants)
        for old_name in names:
            if old_name == canonical:
                continue
            merges.append((dt, old_name, canonical))

    print(f"# {len(merges)} merge(s) proposed across {len(by_cluster)} clusters")
    if not merges:
        return 0
    for dt, old, new in merges:
        print(f"  {dt}:  {old}  →  {new}")

    if dry_run:
        print()
        print("DRY-RUN — no changes applied.")
        return 0

    # Apply merges. Run all in one txn so a failure is atomic.
    async with open_connection(settings.database_url) as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)",
                (workspace_id,),
            )
            updated_total = 0
            for dt, old_name, new_name in merges:
                # Update proposed_fields. Careful: if a row with the new
                # name already exists for the same file, we'd violate
                # the UNIQUE (file_id, field_name) constraint if one
                # exists. Let me handle that by deleting the
                # rare-duplicate first, then renaming.
                cur = await conn.execute(
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
                    (workspace_id, dt, old_name, new_name),
                )
                cur = await conn.execute(
                    """
                    UPDATE proposed_fields
                       SET field_name = %s
                     WHERE workspace_id = %s
                       AND inferred_doc_type = %s
                       AND field_name = %s
                    """,
                    (new_name, workspace_id, dt, old_name),
                )
                rc = getattr(cur, "rowcount", 0)
                updated_total += rc
                print(f"  {dt}:  {old_name}→{new_name}  rows={rc}")
            print()
            print(f"# applied {len(merges)} merges, {updated_total} rows updated")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main(args.workspace, args.dry_run)))
