"""Bug D Phase 7 — backfill normalizer for sub-entity column names.

Same idea as scripts/normalize_field_names.py (which handles
doc-level scalars) but operating on the jsonb keys INSIDE
extracted_entities.fields for sub-entity rows.

Construction examples this fixes:
  ApprovalsRequired:
    approval_description (10 rows)  ← canonical (most-common)
    approval_details (9 rows)       → renamed to approval_description
  ApprovalsRequired:
    approver_name (9 rows)          ← canonical
    name (4 rows)                   → renamed to approver_name

Heuristic uses the same safe-suffix list as normalize_field_names:
strips known synonymous suffixes (_no/_id/_inr/_amount/_amt) but
KEEPS semantically distinct suffixes (_date/_status/_percentage/etc).

--aggressive flag also merges known semantic synonyms:
  description ↔ details, name ↔ title, notes ↔ comments,
  amount ↔ value, id ↔ ref ↔ reference.

Usage:
    # Conservative: suffix-only matching (default)
    uv run python scripts/normalize_subentity_keys.py \\
        --workspace c0000000-0000-0000-0000-000000000001 \\
        [--dry-run]

    # Aggressive: also merges known semantic synonyms
    uv run python scripts/normalize_subentity_keys.py \\
        --workspace ... --aggressive [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from kb.config import get_settings  # noqa: E402
from kb.db.pool import open_connection  # noqa: E402


_SYNONYM_SUFFIXES = (
    "_number", "_num", "_no",
    "_id",
    "_amount", "_amt",
    "_inr", "_usd", "_eur", "_rs", "_gbp",
)

_SEMANTIC_SYNONYM_CLUSTERS: tuple[frozenset[str], ...] = (
    frozenset({"description", "details", "desc"}),
    frozenset({"name", "title"}),
    frozenset({"notes", "comments", "remarks"}),
    frozenset({"amount", "value"}),
    frozenset({"id", "ref", "reference"}),
)


def _canonical_form(name: str) -> str:
    n = name.lower()
    for _ in range(5):
        stripped = False
        for suffix in _SYNONYM_SUFFIXES:
            if n.endswith(suffix) and len(n) > len(suffix):
                n = n[: -len(suffix)]
                stripped = True
                break
        if not stripped:
            break
    return n


def _semantic_cluster(name: str) -> str | None:
    lc = name.lower()
    for cluster in _SEMANTIC_SYNONYM_CLUSTERS:
        if lc in cluster:
            return ":".join(sorted(cluster))
    return None


def _pick_canonical(variants: list[tuple[str, int]]) -> str:
    return min(variants, key=lambda v: (-v[1], len(v[0]), v[0]))[0]


async def main(workspace_id: str, dry_run: bool, aggressive: bool) -> int:
    settings = get_settings()
    async with open_connection(settings.database_url) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)",
            (workspace_id,),
        )
        cur = await conn.execute(
            """
            SELECT se.name, jsonb_object_keys(ee.fields), count(*) AS n_rows
              FROM extracted_entities ee
              JOIN schema_entities se ON se.id = ee.schema_entity_id
                                      AND se.kind = 'sub_entity'
             WHERE ee.workspace_id = %s
               AND ee.fields IS NOT NULL
             GROUP BY se.name, jsonb_object_keys(ee.fields)
             ORDER BY se.name, n_rows DESC, jsonb_object_keys(ee.fields)
            """,
            (workspace_id,),
        )
        rows = await cur.fetchall()

    by_cluster: dict[tuple[str, str], list[tuple[str, int]]] = defaultdict(list)
    for subentity, col, n in rows:
        col_s = str(col)
        if aggressive and (sk := _semantic_cluster(col_s)):
            cluster_key = f"semantic:{sk}"
        else:
            cluster_key = _canonical_form(col_s)
        by_cluster[(str(subentity), cluster_key)].append((col_s, int(n)))

    merges: list[tuple[str, str, str]] = []
    for (subentity, _ck), variants in sorted(by_cluster.items()):
        unique_names = sorted({v[0] for v in variants})
        if len(unique_names) < 2:
            continue
        canonical = _pick_canonical(variants)
        for old in unique_names:
            if old == canonical:
                continue
            merges.append((subentity, old, canonical))

    print(
        f"# {len(merges)} merge(s) proposed across {len(by_cluster)} clusters "
        f"({'aggressive' if aggressive else 'suffix-only'} mode)"
    )
    for subentity, old, new in merges:
        print(f"  {subentity}.{old}  →  {subentity}.{new}")

    if dry_run:
        print()
        print("DRY-RUN — no jsonb keys renamed.")
        return 0
    if not merges:
        return 0

    async with open_connection(settings.database_url) as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)",
                (workspace_id,),
            )
            total_rows_touched = 0
            for subentity, old_name, new_name in merges:
                cur = await conn.execute(
                    """
                    UPDATE extracted_entities ee
                       SET fields = (ee.fields - %s::text)
                                    || jsonb_build_object(%s::text, ee.fields->%s::text)
                      FROM schema_entities se
                     WHERE ee.schema_entity_id = se.id
                       AND se.kind = 'sub_entity'
                       AND se.name = %s::text
                       AND ee.workspace_id = %s::uuid
                       AND ee.fields ? %s::text
                    """,
                    (
                        old_name, new_name, old_name,
                        subentity, workspace_id, old_name,
                    ),
                )
                rc = int(getattr(cur, "rowcount", 0))
                total_rows_touched += rc
                print(f"  {subentity}.{old_name}→{new_name}: rewrote {rc} rows")
            print()
            print(f"# applied {len(merges)} merges, {total_rows_touched} rows touched")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--aggressive", action="store_true")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main(
        args.workspace, args.dry_run, args.aggressive,
    )))
