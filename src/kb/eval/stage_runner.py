"""M1 — per-stage eval driver (in-process, no HTTP surface).

Drives each VERIFIED question (demo-corpus/domains/<domain>/queries.yaml,
carrying `expected_citations` + `evidence_quote` + `verified`) through
`orchestrator.chat()` directly, with a capturing event sink that records
the pipeline's internal fused candidate set (pre-rerank) and reranked
top-K. The orchestrator emits those two lists only when an `emit` sink is
threaded in (default no-op), so production is unaffected — see
`Orchestrator._retrieve_and_rerank`.

Each question's trace becomes a `StageObservation`; `scorer.score_stages`
then reports retrieval recall@k, rerank retention, citation correctness,
and faithfulness — per stratum AND per domain — localising where each
question's gold doc was lost (checklist M1 / DECISIONS D9).

Workspace isolation mirrors `kb.api.deps.kb_app_connection`: one fresh
connection+transaction per question, with `app.workspace_id` SET LOCAL so
RLS scopes retrieval. A per-question connection isolates a failing query
from poisoning the rest of the run.

Usage is via `scripts/run_stage_eval.py` (CLI) or `run_domain_stage_eval`
directly.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import yaml

from kb.config import get_settings
from kb.db.pool import open_connection
from kb.eval.scorer import StageObservation


_LOG = logging.getLogger(__name__)

# repo root: src/kb/eval/stage_runner.py → parents[3].
_ROOT = Path(__file__).resolve().parents[3]
_DOMAINS_DIR = _ROOT / "demo-corpus" / "domains"

ALL_DOMAINS: tuple[str, ...] = (
    "construction", "finance", "government", "healthcare", "legal", "mining",
)

# Known domain → workspace_id mappings. Only construction is committed
# (docs/demo-corpus-eval-construction.md); the rest are filled in as each
# domain is ingested. The CLI's --workspace always overrides this.
# All domains co-located in ONE workspace (single-tenant, no login) so the app's
# default view holds the whole mixed corpus. construction is already ingested
# here; finance is ingested into the SAME workspace (no separate finance ws, no
# construction re-ingest/copy). The app is pointed here via ui/.env.local.
KNOWN_WORKSPACES: dict[str, str] = {
    "construction": "c0000000-0000-0000-0000-000000000001",
    "finance":      "c0000000-0000-0000-0000-000000000001",
}


# ---------------------------------------------------------------------------
# Ground-truth loader (verified eval)
# ---------------------------------------------------------------------------


def load_verified_queries(domain: str) -> list[dict[str, Any]]:
    """Load a domain's queries.yaml as raw dicts. Each carries `id`,
    `stratum`, `question`, `expected_citations`, and the verification
    flags (`verified` / `citations_verified` / `expected_refusal`)."""
    path = _DOMAINS_DIR / domain / "queries.yaml"
    if not path.exists():
        raise FileNotFoundError(f"no queries.yaml for domain {domain!r}: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    queries = data.get("queries") or []
    if not isinstance(queries, list):
        raise ValueError(f"{path}: 'queries' must be a list")
    return [q for q in queries if isinstance(q, dict)]


def _is_verified(q: dict[str, Any]) -> bool:
    """A question is verified ground truth when the rebuild marked either
    `verified: true` (answer + citations) or `citations_verified: true`."""
    return bool(q.get("verified")) or bool(q.get("citations_verified"))


def select_subset(
    queries: list[dict[str, Any]],
    *,
    ids: list[str] | None = None,
    stratified: int | None = None,
) -> list[dict[str, Any]]:
    """Pick a fast inner-loop subset from a domain's full question set.

    - `ids`: keep exactly these question ids (in the file's order).
    - `stratified`: keep the first `stratified` questions PER stratum — a
      cheap representative sample (~2/stratum ≈ 12 Q for construction)
      that runs in minutes, for the dev loop. The full set stays the
      phase-gate. `ids` wins if both are given.

    A small subset is for fast iteration only — recall numbers on it are
    not the source of truth (fewer distractors); the full corpus is.
    """
    if ids:
        wanted = list(ids)
        by_id = {str(q.get("id")): q for q in queries}
        return [by_id[i] for i in wanted if i in by_id]
    if stratified and stratified > 0:
        per: dict[str, int] = {}
        out: list[dict[str, Any]] = []
        for q in queries:
            s = str(q.get("stratum") or "")
            if per.get(s, 0) < stratified:
                per[s] = per.get(s, 0) + 1
                out.append(q)
        return out
    return queries


# ---------------------------------------------------------------------------
# slug → file_id resolution
# ---------------------------------------------------------------------------


async def resolve_slugs_to_file_ids(
    conn: Any, workspace_id: str, slugs: list[str],
) -> dict[str, str | None]:
    """Map each `expected_citation` slug to a `file_id` in the workspace.

    The eval slug (e.g. `contract-001-acme-mahalaxmi-epc`) equals the
    uploaded filename stem (`contract-001-acme-mahalaxmi-epc.md`). Returns
    `{slug: file_id_or_None}`; None means the gold doc isn't ingested in
    this workspace (so retrieval misses on it aren't the pipeline's fault).
    """
    if not slugs:
        return {}
    cur = await conn.execute(
        "SELECT name, id::text FROM files "
        "WHERE workspace_id = %s AND lifecycle_state = 'ready'",
        (workspace_id,),
    )
    rows = await cur.fetchall()
    by_stem: dict[str, str] = {}
    for name, fid in rows:
        stem = name.rsplit(".", 1)[0] if "." in name else name
        by_stem[stem] = fid
    return {s: by_stem.get(s) for s in slugs}


# ---------------------------------------------------------------------------
# Event sink — captures the orchestrator's fused + reranked file_ids
# ---------------------------------------------------------------------------


class _StageCapture:
    """Async event sink matching the orchestrator's `emit` signature.
    Records the fused candidate set (pre-rerank) and reranked top-K as
    ordered file_id lists."""

    def __init__(self) -> None:
        self.fused: list[str | None] = []
        self.reranked: list[str | None] = []

    async def __call__(self, event_type: str, payload: dict[str, Any]) -> None:
        if event_type == "fused_candidates":
            self.fused = list(payload.get("file_ids") or [])
        elif event_type == "reranked_candidates":
            self.reranked = list(payload.get("file_ids") or [])


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


async def _run_one(
    orchestrator: Any, conn: Any, workspace_id: str,
    q: dict[str, Any], domain: str,
) -> StageObservation:
    """Drive one question through chat() and assemble its StageObservation."""
    slugs = [s for s in (q.get("expected_citations") or []) if isinstance(s, str)]
    resolved = await resolve_slugs_to_file_ids(conn, workspace_id, slugs)
    expected_fids = tuple(v for v in resolved.values() if v)
    # Resolved iff every cited slug found a file (empty slug list → trivially
    # resolved; such rows just won't be scorable for retrieval).
    citations_resolved = all(resolved.get(s) for s in slugs) if slugs else True

    capture = _StageCapture()
    refused = False
    faith_verdict: str | None = None
    cited_fids: tuple[str, ...] = ()
    err: str | None = None

    try:
        result = await orchestrator.chat(
            q["question"],
            workspace_id=workspace_id,
            conn=conn,
            event_sink=capture,
        )
        refused = bool(result.generation.refused)
        faith_verdict = result.faithfulness_verdict
        cited_fids = tuple(
            c.file_id for c in result.generation.citations
            if getattr(c, "file_id", None)
        )
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("stage-eval chat failed on %s: %s", q.get("id"), exc)
        err = f"{type(exc).__name__}: {exc}"[:200]

    return StageObservation(
        question_id=str(q.get("id") or ""),
        domain=domain,
        stratum=str(q.get("stratum") or ""),
        verified=_is_verified(q),
        expected_refusal=bool(q.get("expected_refusal")),
        expected_file_ids=expected_fids,
        fused_file_ids=tuple(capture.fused),
        reranked_file_ids=tuple(capture.reranked),
        cited_file_ids=cited_fids,
        refused=refused,
        faithfulness_verdict=faith_verdict,
        citations_resolved=citations_resolved,
        error=err,
    )


async def _run_one_isolated(
    orchestrator: Any, workspace_id: str, q: dict[str, Any], domain: str,
    settings: Any,
) -> StageObservation:
    """Open a fresh connection + txn (mirrors kb.api.deps) and drive one
    question. Each question is fully isolated so concurrent questions can't
    share/poison a connection."""
    async with open_connection(settings.app_database_url) as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.workspace_id', %s, true)",
                (workspace_id,),
            )
            return await _run_one(orchestrator, conn, workspace_id, q, domain)


async def run_domain_stage_eval(
    domain: str,
    workspace_id: str,
    *,
    orchestrator: Any = None,
    limit: int | None = None,
    ids: list[str] | None = None,
    stratified: int | None = None,
    delay_s: float = 0.0,
    concurrency: int = 1,
    progress: bool = True,
) -> list[StageObservation]:
    """Run the per-stage eval for one domain against its ingested
    workspace. Returns one StageObservation per question, in input order.

    Each chat() fans out ~6-8 SEQUENTIAL Gemini calls (intent, planner,
    rewriter, generation, faithfulness), so a single question is
    latency-bound (~2-3 min) while CPU + provider quota sit nearly idle.
    `concurrency` runs that many questions in parallel — each with its own
    DB connection — turning the wall-clock from sum-of-latencies into
    max-of-latencies. Keep it modest (provider RPM is the only ceiling).
    `delay_s` optionally staggers task starts to smooth the call burst.
    """
    if orchestrator is None:
        from kb.query.orchestrator import Orchestrator
        orchestrator = Orchestrator.make_default()

    queries = load_verified_queries(domain)
    queries = select_subset(queries, ids=ids, stratified=stratified)
    if limit is not None:
        queries = queries[:limit]

    settings = get_settings()
    total = len(queries)
    sem = asyncio.Semaphore(max(1, concurrency))
    from kb.eval.scorer import localise
    done = 0

    async def _worker(idx: int, q: dict[str, Any]) -> StageObservation:
        nonlocal done
        # Stagger starts so N workers don't fire their first call in the
        # same instant (smooths the provider call burst at launch).
        if delay_s:
            await asyncio.sleep(delay_s * idx)
        async with sem:
            obs = await _run_one_isolated(
                orchestrator, workspace_id, q, domain, settings,
            )
        done += 1
        if progress:
            print(
                f"[{done:>3d}/{total}] {obs.question_id:18s} "
                f"[{obs.stratum:14s}] {localise(obs):14s} "
                f"{'ERR ' + obs.error if obs.error else ''}",
                flush=True,
            )
        return obs

    # gather preserves input order in the returned list regardless of
    # completion order.
    return list(await asyncio.gather(
        *(_worker(i, q) for i, q in enumerate(queries))
    ))


async def run_stage_eval(
    domains: list[str],
    workspaces: dict[str, str],
    *,
    limit: int | None = None,
    ids: list[str] | None = None,
    stratified: int | None = None,
    delay_s: float = 0.0,
    concurrency: int = 1,
    progress: bool = True,
) -> list[StageObservation]:
    """Run the per-stage eval across several domains, reusing one
    orchestrator (and its model singletons) for the whole run."""
    from kb.query.orchestrator import Orchestrator
    orchestrator = Orchestrator.make_default()

    all_obs: list[StageObservation] = []
    for domain in domains:
        ws = workspaces.get(domain) or KNOWN_WORKSPACES.get(domain)
        if not ws:
            raise ValueError(
                f"no workspace_id for domain {domain!r}; pass --workspace"
            )
        if progress:
            print(f"\n# === {domain} (workspace={ws}) ===", flush=True)
        obs = await run_domain_stage_eval(
            domain, ws, orchestrator=orchestrator,
            limit=limit, ids=ids, stratified=stratified,
            delay_s=delay_s, concurrency=concurrency,
            progress=progress,
        )
        all_obs.extend(obs)
    return all_obs
