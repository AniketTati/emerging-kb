"""Per-doc layer review for a workspace. Pulls L0..L4 + chain info
for every file and prints a summary table. Used as the eval
checkpoint between ingestion + querying.

Output columns:
  name, doc_type, doc_status, n_chunks, n_mentions, n_fields,
  n_entities, n_subent, chain, chain_role, authority

Usage:
    uv run python scripts/domain_review.py \
        --workspace c0000000-0000-0000-0000-000000000001
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import httpx

BASE = "http://localhost:8000"


async def main(workspace_id: str) -> int:
    async with httpx.AsyncClient(base_url=BASE, timeout=30.0) as client:
        headers = {"X-Test-Workspace": workspace_id}
        # List files
        r = await client.get("/files?limit=200", headers=headers)
        items = r.json()
        items = items.get("items") if isinstance(items, dict) else items
        items.sort(key=lambda f: (f.get("inferred_doc_type") or "", f.get("name") or ""))

        # For each file, hit /details for layer counts
        async def get_details(fid: str) -> dict:
            r = await client.get(f"/files/{fid}/details", headers=headers)
            return r.json()
        async def get_pf(fid: str) -> list:
            r = await client.get(f"/files/{fid}/proposed-fields", headers=headers)
            return r.json()
        async def get_ee(fid: str) -> list:
            r = await client.get(f"/files/{fid}/extracted-entities", headers=headers)
            return r.json()
        async def get_chain(fid: str):
            r = await client.get(f"/files/{fid}/chain", headers=headers)
            if r.status_code == 404:
                return None
            return r.json()

        # Fetch in parallel
        details = await asyncio.gather(*[get_details(f["id"]) for f in items])
        pfs = await asyncio.gather(*[get_pf(f["id"]) for f in items])
        ees = await asyncio.gather(*[get_ee(f["id"]) for f in items])
        chains = await asyncio.gather(*[get_chain(f["id"]) for f in items])

        # Print per-doctype summary first
        from collections import Counter
        dtypes = Counter(f.get("inferred_doc_type") for f in items)
        print(f"# {len(items)} files across {len(dtypes)} doctypes:")
        for dt, n in sorted(dtypes.items(), key=lambda kv: -kv[1]):
            print(f"  {n:2d}  {dt}")
        print()

        # Per-doc rows
        print(f'{"name":58s}  {"doctype":24s}  {"status":11s}  '
              f'{"chunks":>6s} {"ment":>4s} {"flds":>4s} {"ent":>4s} {"sub":>3s}  '
              f'{"chain":40s}  {"role":11s}')
        print('-' * 200)
        # Track aggregates per doctype
        agg_by_dt: dict[str, dict[str, int]] = {}
        anomalies: list[str] = []
        for f, d, pf, ee, ch in zip(items, details, pfs, ees, chains):
            file_d = d.get("file", {}) if isinstance(d, dict) else {}
            dt = file_d.get("inferred_doc_type") or "?"
            n_chunks = d.get("n_chunks", 0) if isinstance(d, dict) else 0
            n_mentions = d.get("n_mentions", 0) if isinstance(d, dict) else 0
            n_fields = len(pf)
            n_entities = len(ee)
            # Sub-entities = entities with parent_entity_id set (non-root)
            n_sub = sum(1 for e in ee if e.get("parent_entity_id"))
            chain_key = (ch or {}).get("chain", {}).get("chain_key", "") if ch else ""
            chain_role = (ch or {}).get("file_role", "") if ch else ""

            name_short = f["name"][:55]
            print(f'{name_short:58s}  {dt[:24]:24s}  {file_d.get("doc_status",""):11s}  '
                  f'{n_chunks:>6d} {n_mentions:>4d} {n_fields:>4d} {n_entities:>4d} {n_sub:>3d}  '
                  f'{(chain_key or "(none)")[:40]:40s}  {chain_role:11s}')

            # Aggregate
            a = agg_by_dt.setdefault(dt, {"docs": 0, "fields": 0, "ents": 0, "subent": 0, "mentions": 0})
            a["docs"] += 1
            a["fields"] += n_fields
            a["ents"] += n_entities
            a["subent"] += n_sub
            a["mentions"] += n_mentions

            # Anomaly detection (rough)
            if n_chunks == 0:
                anomalies.append(f"NO CHUNKS: {f['name']}")
            if n_fields == 0:
                anomalies.append(f"NO FIELDS: {f['name']}")
            if n_entities == 0:
                anomalies.append(f"NO ENTITIES: {f['name']}")
            if file_d.get("source_authority") == 0.5 and not file_d.get("doc_type"):
                pass  # known issue (Bug C — deferred)
            if file_d.get("doc_status") == "superseded" and ch is None:
                anomalies.append(f"SUPERSEDED BUT NO CHAIN: {f['name']}")

        # Per-doctype aggregates
        print()
        print(f'# Per-doctype averages:')
        print(f'  {"doctype":30s}  {"docs":>4s} {"fld/doc":>7s} {"ent/doc":>7s} {"sub/doc":>7s} {"ment/doc":>8s}')
        for dt, a in sorted(agg_by_dt.items(), key=lambda kv: -kv[1]["docs"]):
            n = a["docs"]
            print(f'  {dt[:30]:30s}  {n:>4d} '
                  f'{a["fields"]/n:>7.1f} {a["ents"]/n:>7.1f} {a["subent"]/n:>7.1f} {a["mentions"]/n:>8.1f}')

        # Anomalies
        print()
        if anomalies:
            print(f'# {len(anomalies)} anomalies:')
            for a in anomalies:
                print(f'  ⚠ {a}')
        else:
            print('# no anomalies detected')
        return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main(args.workspace)))
