"""M1 — per-stage measurement harness CLI (checklist M1 / DECISIONS D9).

Runs the VERIFIED eval (demo-corpus/domains/*/queries.yaml) through the
query pipeline IN-PROCESS and scores each pipeline stage separately:

  retrieval recall@k  — gold citation in the fused candidate set (pre-rerank)
  rerank retention    — given it was fused, did rerank keep it in top-10
  citation correctness— did the generated answer actually cite the gold doc
  faithfulness        — is the answer grounded

…reported per stratum AND per domain, with a localisation histogram
(where each question's gold doc was lost). This is what lets every later
fix (I1 chunking, R1 retrieval, Q5 faithfulness, …) be attributed to the
stage it targets.

Prereqs: the target domain's corpus is ingested + 'ready' in its
workspace, and the DB is reachable (source scripts/dev_env.sh).

Usage:
    source scripts/dev_env.sh

    # one domain (construction's workspace is known)
    uv run python scripts/run_stage_eval.py --domain construction

    # a quick slice
    uv run python scripts/run_stage_eval.py --domain construction --limit 10

    # several domains (pass a workspace for any not in KNOWN_WORKSPACES)
    uv run python scripts/run_stage_eval.py \
        --domain construction --domain legal \
        --workspace legal=<uuid> --delay 1.0
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from kb.eval.scorer import (  # noqa: E402
    render_stage_summary, score_stages, write_stage_csv,
)
from kb.eval.stage_runner import ALL_DOMAINS, run_stage_eval  # noqa: E402


def _parse_workspaces(pairs: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in pairs:
        if "=" not in p:
            raise SystemExit(f"--workspace must be domain=uuid, got {p!r}")
        domain, ws = p.split("=", 1)
        out[domain.strip()] = ws.strip()
    return out


async def _main(args: argparse.Namespace) -> int:
    domains = args.domain or list(ALL_DOMAINS)
    workspaces = _parse_workspaces(args.workspace or [])

    ids = (
        [i.strip() for i in args.ids.split(",") if i.strip()]
        if args.ids else None
    )
    observations = await run_stage_eval(
        domains, workspaces,
        limit=args.limit, ids=ids, stratified=args.stratified,
        delay_s=args.delay, concurrency=args.concurrency, progress=True,
    )

    report = score_stages(observations)
    print()
    print(render_stage_summary(report))

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = "_".join(domains) if len(domains) <= 2 else f"{len(domains)}domains"
    csv_path = out_dir / f"stage_eval_{stamp}.csv"
    json_path = out_dir / f"stage_eval_{stamp}.summary.json"
    write_stage_csv(observations, csv_path)
    json_path.write_text(json.dumps(report.to_dict(), indent=2))
    print()
    print(f"# per-question CSV → {csv_path}")
    print(f"# summary JSON     → {json_path}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="M1 per-stage eval harness")
    ap.add_argument(
        "--domain", action="append",
        help="domain to run (repeatable). Default: all 6.",
    )
    ap.add_argument(
        "--workspace", action="append", default=[],
        help="domain=workspace_uuid (repeatable) for domains not in "
             "KNOWN_WORKSPACES.",
    )
    ap.add_argument("--limit", type=int, default=None,
                    help="cap questions per domain (smoke runs)")
    ap.add_argument("--stratified", type=int, default=None,
                    help="fast dev loop: keep first N questions PER stratum "
                         "(e.g. 2 → ~12 Q). Full set is the phase-gate.")
    ap.add_argument("--ids", default=None,
                    help="comma-separated question ids to run (exact subset)")
    ap.add_argument("--concurrency", type=int, default=1,
                    help="questions to run in parallel (each gets its own "
                         "DB connection; provider RPM is the only ceiling)")
    ap.add_argument("--delay", type=float, default=0.0,
                    help="stagger (s) between worker starts to smooth the "
                         "call burst at launch")
    ap.add_argument("--out-dir", default=str(_ROOT / "eval_out"))
    args = ap.parse_args()
    raise SystemExit(asyncio.run(_main(args)))
