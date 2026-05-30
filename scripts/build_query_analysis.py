"""Per-query forensic analysis report.

Joins queries.yaml + v16 judged results + the most-recent matching
query_log row to produce a single markdown document grouping every
query by stratum, with: expected answer, actual answer, pipeline path
(intent → mode → CRAG), top-K retrieved files with rank, and whether
the expected citations made it into the top-K.

Output: docs/CONSTRUCTION_QUERY_FORENSIC.md
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections import defaultdict
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from kb.db.pool import open_connection  # noqa: E402
import os  # noqa: E402

WORKSPACE = "c0000000-0000-0000-0000-000000000001"
QUERIES = _ROOT / "demo-corpus/domains/construction/queries.yaml"
V16 = _ROOT / "docs/construction_query_results_v16_judged.json"
OUT = _ROOT / "docs/CONSTRUCTION_QUERY_FORENSIC.md"

DB_URL = os.environ.get(
    "KB_DATABASE_URL",
    "postgresql://kb:kb@localhost:5432/kb",
)


async def main() -> int:
    queries = yaml.safe_load(QUERIES.read_text())["queries"]
    v16_results = {r["id"]: r for r in json.loads(V16.read_text())}

    async with open_connection(DB_URL) as conn:
        if True:
            # Get the most-recent query_log row for each query text. v16
            # was the last full eval run; matching on query text + only
            # last 24h means we pick up v16 hits (v17 was killed before
            # any queries ran).
            logs_by_text: dict[str, dict] = {}
            cur = await conn.execute(
                """
                SELECT DISTINCT ON (query)
                  query, mode, intent, refused, refusal_reason,
                  crag_score, hit_ids, answer, citations,
                  faithfulness_score, faithfulness_verdict,
                  latency_ms, created_at
                FROM query_log
                WHERE workspace_id = %s::uuid
                  AND created_at > now() - interval '24 hours'
                ORDER BY query, created_at DESC
                """,
                (WORKSPACE,),
            )
            for row in await cur.fetchall():
                logs_by_text[row[0]] = {
                    "mode": row[1], "intent": row[2],
                    "refused": row[3], "refusal_reason": row[4],
                    "crag_score": row[5], "hit_ids": row[6],
                    "answer": row[7], "citations": row[8],
                    "faithfulness_score": row[9],
                    "faithfulness_verdict": row[10],
                    "latency_ms": row[11], "created_at": row[12],
                }

            # Resolve all hit_ids → (kind, file_name) for both chunk and
            # raptor_node origin tables.
            all_hit_ids: set[str] = set()
            for log in logs_by_text.values():
                for h in log.get("hit_ids") or []:
                    all_hit_ids.add(str(h.get("id") or ""))
            all_hit_ids.discard("")

            file_by_chunk: dict[str, str] = {}
            file_by_raptor: dict[str, str] = {}
            if all_hit_ids:
                ids_list = sorted(all_hit_ids)
                cur = await conn.execute(
                    """
                    SELECT c.id::text, f.name
                    FROM chunks c JOIN files f ON f.id = c.file_id
                    WHERE c.workspace_id = %s::uuid
                      AND c.id::text = ANY(%s)
                    """,
                    (WORKSPACE, ids_list),
                )
                for cid, fname in await cur.fetchall():
                    file_by_chunk[cid] = fname

                cur = await conn.execute(
                    """
                    SELECT rn.id::text, f.name
                    FROM raptor_nodes rn
                    JOIN files f ON f.id = rn.file_id
                    WHERE rn.workspace_id = %s::uuid
                      AND rn.id::text = ANY(%s)
                    """,
                    (WORKSPACE, ids_list),
                )
                for nid, fname in await cur.fetchall():
                    if fname and nid not in file_by_raptor:
                        file_by_raptor[nid] = fname

    # Group queries by stratum
    by_stratum: dict[str, list] = defaultdict(list)
    for q in queries:
        by_stratum[q.get("stratum") or "?"].append(q)

    lines: list[str] = [
        "# Construction Query Forensic — per-query path + retrieval audit",
        "",
        "Generated from v16 eval (Phase A+B+C applied).",
        "Total: 50 queries, 18 correct / 22 partial / 8 wrong / 2 refused (LLM-judge).",
        "",
        "Each block shows: question, expected, actual, pipeline path "
        "(intent → mode → CRAG), top retrieved files, and whether the "
        "expected citations made the top-10.",
        "",
        "---",
        "",
    ]

    stratum_order = [
        "needle", "chain-aware", "conflict-resolution",
        "rare-clause", "aggregation", "long-form",
        "ambiguous", "negative", "adversarial",
    ]
    ordered = [s for s in stratum_order if s in by_stratum] + \
              [s for s in by_stratum if s not in stratum_order]

    for stratum in ordered:
        qs = by_stratum[stratum]
        lines.append(f"## Stratum: **{stratum}** ({len(qs)} queries)")
        lines.append("")

        for q in qs:
            qid = q["id"]
            r = v16_results.get(qid) or {}
            j = r.get("llm_judge") or {}
            verdict = j.get("verdict", "?")
            log = logs_by_text.get(q["question"]) or {}

            mode = log.get("mode") or "?"
            intent = log.get("intent") or "?"
            crag = log.get("crag_score")
            crag_str = f"{crag:.2f}" if crag is not None else "?"
            refused = log.get("refused")
            refusal = log.get("refusal_reason") or ""
            faith = log.get("faithfulness_score")
            faith_str = f"{faith:.2f}" if faith is not None else "?"
            latency = log.get("latency_ms")
            latency_str = f"{latency}ms" if latency else "?"

            # Verdict marker
            mark = {"correct": "✅", "partial": "🟡",
                    "wrong": "❌", "refused": "⏸️"}.get(verdict, "❓")

            lines.append(f"### {qid} {mark} `{verdict}`")
            lines.append("")
            lines.append(f"**Q:** {q['question']}")
            lines.append("")
            exp = q.get("expected_answer", "")
            lines.append(f"**Expected:** {exp}")
            lines.append("")
            actual = r.get("answer_preview") or "(no answer captured)"
            lines.append(f"**Actual:** {actual}")
            lines.append("")

            # Pipeline path
            path_bits = [
                f"intent=`{intent}`",
                f"mode=`{mode}`",
                f"CRAG=`{crag_str}`",
                f"faithful=`{faith_str}`",
                f"latency={latency_str}",
            ]
            if refused:
                path_bits.append(f"refused=`{refusal[:80]}`")
            lines.append("**Path:** " + " · ".join(path_bits))
            lines.append("")

            # Retrieved top-K
            hit_ids = log.get("hit_ids") or []
            expected_cits = q.get("expected_citations") or []
            expected_set = {c.lower() for c in expected_cits}

            lines.append("**Top-10 retrieved:**")
            lines.append("")
            seen = set()
            for i, h in enumerate(hit_ids[:10]):
                hid = str(h.get("id") or "")
                kind = h.get("kind") or "?"
                if kind == "chunk":
                    fname = file_by_chunk.get(hid, "(?)")
                elif kind in ("raptor_node",):
                    fname = file_by_raptor.get(hid, "(?)")
                else:
                    fname = h.get("metadata", {}).get("file_name") or "(?)"
                marker = ""
                if expected_set:
                    for ec in expected_set:
                        if ec in fname.lower():
                            marker = " ← expected"
                            break
                lines.append(
                    f"  {i+1:>2}. `{kind:>11}`  {fname}{marker}"
                )
                seen.add(fname.lower())

            # Did any expected citation make it into top-10?
            if expected_cits:
                hit_files = {(file_by_chunk.get(str(h.get("id") or ""))
                              or file_by_raptor.get(str(h.get("id") or ""))
                              or "").lower()
                             for h in hit_ids[:10]}
                missed = []
                hit_status = []
                for ec in expected_cits:
                    found = any(ec.lower() in hf for hf in hit_files if hf)
                    if found:
                        hit_status.append(f"✓ {ec}")
                    else:
                        missed.append(ec)
                        hit_status.append(f"✗ {ec}")
                lines.append("")
                lines.append("**Expected citations vs retrieved:**")
                for h in hit_status:
                    lines.append(f"  - {h}")
                if missed:
                    lines.append(
                        f"\n  → **{len(missed)}/{len(expected_cits)} "
                        f"expected docs NOT in top-10**"
                    )

            # Judge reason
            if j.get("reason"):
                lines.append("")
                lines.append(f"**Judge:** {j['reason']}")

            lines.append("")
            lines.append("---")
            lines.append("")

    OUT.write_text("\n".join(lines))
    print(f"Wrote {OUT.relative_to(_ROOT)} ({len(lines)} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
