"""Load a schema YAML file into a workspace via POST /schemas/import.yaml.

The one-step "load this domain's schema" path (P3): point it at a committed
schema artifact (e.g. demo-corpus/domains/finance/schema.yaml) and it upserts
the schema + entities + fields + relationships into the target workspace.

Usage:
    uv run python scripts/load_schema.py \
        --workspace <uuid> \
        demo-corpus/domains/finance/schema.yaml

    # or read from stdin
    cat schema.yaml | uv run python scripts/load_schema.py --workspace <uuid> -

Env:
    KB_API_BASE_URL   API base (default http://localhost:8000)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx


BASE = os.environ.get("KB_API_BASE_URL", "http://localhost:8000")


def _read_body(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def load_schema(workspace_id: str, body: str) -> int:
    """POST the YAML body to /schemas/import.yaml. Returns process exit code."""
    with httpx.Client(base_url=BASE, timeout=httpx.Timeout(120.0)) as client:
        resp = client.post(
            "/schemas/import.yaml",
            content=body.encode("utf-8"),
            headers={
                "X-Test-Workspace": workspace_id,
                "Content-Type": "application/x-yaml",
            },
        )
    if resp.status_code != 200:
        print(f"[FAIL] http={resp.status_code} {resp.text}", file=sys.stderr)
        return 1

    payload = resp.json()
    imported = payload.get("imported", [])
    if not imported:
        print("[WARN] import succeeded but no schemas were returned", file=sys.stderr)
        return 0
    for item in imported:
        print(
            f"  [{item['action'].upper():7s}] {item['name']:24s} "
            f"v{item['current_version']}  "
            f"entities={item['entities']} fields={item['fields']} "
            f"relationships={item['relationships']}  "
            f"id={item['schema_id']}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, help="Target workspace UUID")
    parser.add_argument(
        "path",
        help="Path to a schema YAML file, or '-' to read from stdin",
    )
    args = parser.parse_args(argv)

    try:
        body = _read_body(args.path)
    except OSError as exc:
        print(f"[FAIL] cannot read {args.path}: {exc}", file=sys.stderr)
        return 1
    if not body.strip():
        print("[FAIL] empty schema document", file=sys.stderr)
        return 1

    print(f"Loading schema from {args.path} → workspace {args.workspace} ({BASE})")
    return load_schema(args.workspace, body)


if __name__ == "__main__":
    raise SystemExit(main())
