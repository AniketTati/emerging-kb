"""B9 / WA-16 + WA-17 — `python -m kb.eval` CLI.

Two subcommands:

  ingest    — load a directory tree into a workspace via POST /files
  run       — execute the golden question set against POST /chat,
              write per-question CSV + per-stratum summary

Example:

  python -m kb.eval ingest \
      --base-url http://localhost:8000 \
      --workspace 11111111-2222-3333-4444-555555555555 \
      --dir tests/fixtures

  python -m kb.eval run \
      --base-url http://localhost:8000 \
      --workspace 11111111-2222-3333-4444-555555555555 \
      --out eval_results.csv
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx

from kb.eval.loader import ingest_directory
from kb.eval.runner import load_golden_questions, run_eval
from kb.eval.scorer import render_summary, score_results, write_results_csv


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="kb.eval")
    sub = p.add_subparsers(dest="cmd", required=True)

    ing = sub.add_parser("ingest", help="POST /files for every file in a directory")
    ing.add_argument("--base-url", required=True, help="API base URL (e.g. http://localhost:8000)")
    ing.add_argument("--workspace", required=True, help="Target workspace UUID")
    ing.add_argument("--dir", required=True, help="Directory to walk")
    ing.add_argument("--limit", type=int, default=None)
    ing.add_argument("--concurrency", type=int, default=4)
    ing.add_argument("--no-recursive", action="store_true")

    run = sub.add_parser("run", help="Execute the golden question set via POST /chat")
    run.add_argument("--base-url", required=True)
    run.add_argument("--workspace", required=True)
    run.add_argument(
        "--questions", default=None,
        help="Optional path to a golden_questions.yaml override",
    )
    run.add_argument("--out", required=True, help="Output CSV path")
    run.add_argument("--summary-json", default=None,
                     help="Optional path for a machine-readable summary JSON")
    run.add_argument("--concurrency", type=int, default=2)
    return p


async def _ingest_cmd(args) -> int:
    async with httpx.AsyncClient(
        base_url=args.base_url, timeout=httpx.Timeout(60.0),
    ) as client:
        report = await ingest_directory(
            client, args.dir,
            workspace_id=args.workspace,
            recursive=not args.no_recursive,
            limit=args.limit,
            concurrency=args.concurrency,
        )
    print(report.summary())
    for r in report.items:
        if r.status not in ("ok", "duplicate"):
            print(f"  ! {r.path}: [{r.status}] {r.detail or '(no detail)'}",
                  file=sys.stderr)
    return 0 if report.errors == 0 else 1


async def _run_cmd(args) -> int:
    questions = load_golden_questions(args.questions)
    async with httpx.AsyncClient(
        base_url=args.base_url, timeout=httpx.Timeout(120.0),
    ) as client:
        results = await run_eval(
            client, questions,
            workspace_id=args.workspace,
            concurrency=args.concurrency,
        )
    out_path = write_results_csv(results, args.out)
    report = score_results(results)
    print(render_summary(report))
    print(f"\nCSV written to {out_path}")
    if args.summary_json:
        Path(args.summary_json).write_text(
            json.dumps(report.to_dict(), indent=2), encoding="utf-8",
        )
        print(f"Summary JSON written to {args.summary_json}")
    # Non-zero exit code if any HTTP errors occurred (CI gate).
    return 0 if report.total_errors == 0 else 2


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "ingest":
        return asyncio.run(_ingest_cmd(args))
    if args.cmd == "run":
        return asyncio.run(_run_cmd(args))
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
