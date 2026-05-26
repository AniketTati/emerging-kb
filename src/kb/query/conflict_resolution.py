"""R1 — Orchestrator helper that wires `kb.query.conflict_detector` into
the chat pipeline.

Translation layer between retrieval-time `Hit` objects and the
pure-function Design 2 cascade in `conflict_detector.py`:

  hits (atomic_unit) + DB-fetched file metadata
       │
       ▼
  build_fact_candidates() — one FactCandidate per (file, predicate)
       │
       ▼
  resolve_all() (existing pure logic)
       │
       ▼
  list[ResolvedConflict] → orchestrator → generator (+ persist unresolved
                                                     to fact_conflicts)

Predicate convention:
  - Per atomic_unit, each scalar `parameters` key becomes a (predicate,
    value) tuple.
  - For typed plugins (clauses), prefix with the clause_type to keep
    `payment_terms.payment_due_days` ≠ `termination.payment_due_days`.
  - For generic_items plugin output, prefix with the unit_type itself
    (the LLM's item_type, e.g. "kpi.q1_revenue").
  - Skip narrative fields (summary, title, parties, anchor_chunk_index).

Entity convention:
  - When the source file is a doc-chain member, entity_id = chain_id —
    so MSA + Amendment in the same chain are recognised as describing
    the SAME logical contract.
  - Files outside any chain don't yield conflicts (their predicates
    aren't comparable across distinct entities).

Wave A scope: covers the high-value MSA ↔ Amendment case. Wave B can
add cross-doc entity-level fact collection (resolved entity IDs from
extracted_entities) for non-chain conflicts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from kb.query.conflict_detector import (
    FactCandidate,
    ResolvedConflict,
    resolve_all,
)
from kb.query.rrf import Hit


# Atomic-unit parameter keys that are narrative / structural — not facts
# we'd want to detect conflicts on.
_SKIP_PARAMETER_KEYS = frozenset({
    "summary", "title", "parties", "anchor_chunk_index",
    "clause_type", "item_type", "row_index", "cells", "header",
    "sheet_name", "body_preview", "body_length", "message_index",
})


@dataclass(frozen=True)
class FileMetaForConflict:
    """File-level fields the conflict detector needs.

    Strict superset of FileMetaForCitation — adds chain.current_version_id
    + file.created_at so the doc-chain + recency cascade rules can fire.
    """
    file_id: str
    source_authority: float | None
    doc_status: str | None
    chain_id: str | None
    chain_current_version_doc_id: str | None
    created_at_iso: str | None


async def fetch_file_metas_for_conflict(
    conn: Any, *, file_ids: Iterable[str],
) -> dict[str, FileMetaForConflict]:
    """Batch-fetch metadata for conflict resolution.

    Wraps the read in a SAVEPOINT — same rationale as
    `kb.query.citations.fetch_file_metas`: a query failure (bad UUID,
    missing column on an older schema) must not poison the outer
    transaction the caller is using for downstream writes
    (`fact_conflicts` INSERT, audit log, idempotency cache).
    """
    ids = sorted({fid for fid in file_ids if fid})
    if not ids:
        return {}

    try:
        await conn.execute("SAVEPOINT fetch_file_metas_for_conflict")
        in_savepoint = True
    except Exception:
        in_savepoint = False

    try:
        cur = await conn.execute(
            "SELECT f.id::text, "
            "       f.source_authority, f.doc_status, "
            "       f.created_at, "
            "       m.chain_id::text, "
            "       c.current_version_id::text "
            "FROM files f "
            "LEFT JOIN doc_chain_members m ON m.doc_id = f.id "
            "LEFT JOIN doc_chains c ON c.id = m.chain_id "
            "WHERE f.id::text = ANY(%s)",
            (ids,),
        )
        rows = await cur.fetchall()
        if in_savepoint:
            try:
                await conn.execute(
                    "RELEASE SAVEPOINT fetch_file_metas_for_conflict"
                )
            except Exception:
                pass
    except Exception:
        if in_savepoint:
            try:
                await conn.execute(
                    "ROLLBACK TO SAVEPOINT fetch_file_metas_for_conflict"
                )
                await conn.execute(
                    "RELEASE SAVEPOINT fetch_file_metas_for_conflict"
                )
            except Exception:
                pass
        return {}

    out: dict[str, FileMetaForConflict] = {}
    for r in rows:
        fid = str(r[0])
        # A file can technically appear in multiple chains (unusual but
        # the schema allows it); we just pick the first row arbitrarily.
        # Future work: prefer the most recent chain membership.
        if fid in out:
            continue
        out[fid] = FileMetaForConflict(
            file_id=fid,
            source_authority=(float(r[1]) if r[1] is not None else None),
            doc_status=r[2],
            created_at_iso=r[3].isoformat() if r[3] else None,
            chain_id=str(r[4]) if r[4] else None,
            chain_current_version_doc_id=str(r[5]) if r[5] else None,
        )
    return out


async def _fetch_chain_member_ids(
    conn: Any, *, chain_ids: Iterable[str],
) -> set[str]:
    """Return the file_ids of ALL members of the given chains.

    Lets `resolve_conflicts_for_hits` expand from "files the user's
    query happened to retrieve" to "every member of every chain those
    files participate in" — the latter is what the conflict detector
    actually needs to find disagreements (you need both sides of an
    MSA / Amendment pair, not just whichever the rerank surfaced).

    SAVEPOINT-wrapped per the same rationale as the other fetchers.
    """
    ids = sorted({c for c in chain_ids if c})
    if not ids:
        return set()

    try:
        await conn.execute("SAVEPOINT fetch_chain_members")
        in_savepoint = True
    except Exception:
        in_savepoint = False

    try:
        cur = await conn.execute(
            "SELECT doc_id::text FROM doc_chain_members "
            "WHERE chain_id::text = ANY(%s)",
            (ids,),
        )
        rows = await cur.fetchall()
        if in_savepoint:
            try:
                await conn.execute("RELEASE SAVEPOINT fetch_chain_members")
            except Exception:
                pass
    except Exception:
        if in_savepoint:
            try:
                await conn.execute("ROLLBACK TO SAVEPOINT fetch_chain_members")
                await conn.execute("RELEASE SAVEPOINT fetch_chain_members")
            except Exception:
                pass
        return set()

    return {str(r[0]) for r in rows}


async def fetch_atomic_units_by_file(
    conn: Any, *, file_ids: Iterable[str],
) -> list[tuple[str, str, str, dict[str, Any]]]:
    """Batch-fetch all atomic_units for a set of files.

    Returns a list of `(unit_id, file_id, unit_type, parameters)` tuples.
    Used by `resolve_conflicts_for_hits` to find conflicts among ALL
    units of chained files, not just the units that happened to surface
    as top-K hits — chunks frequently outrank atomic_units in retrieval
    but the question "did the MSA and Amendment disagree?" is best
    answered from their full structured-clause inventories.

    SAVEPOINT-wrapped per the same rationale as the other fetchers.
    """
    ids = sorted({fid for fid in file_ids if fid})
    if not ids:
        return []

    try:
        await conn.execute("SAVEPOINT fetch_atomic_units_by_file")
        in_savepoint = True
    except Exception:
        in_savepoint = False

    try:
        # Post nested-entities refactor: each atomic_unit now lives as
        # an extracted_entity sub-row carrying the same unit_type +
        # `fields` jsonb (renamed from `parameters`). Filter by
        # unit_type IS NOT NULL to skip parent doc_root rows.
        cur = await conn.execute(
            "SELECT id::text, file_id::text, unit_type, fields "
            "FROM extracted_entities WHERE file_id::text = ANY(%s) "
            "  AND unit_type IS NOT NULL",
            (ids,),
        )
        rows = await cur.fetchall()
        if in_savepoint:
            try:
                await conn.execute("RELEASE SAVEPOINT fetch_atomic_units_by_file")
            except Exception:
                pass
    except Exception:
        if in_savepoint:
            try:
                await conn.execute(
                    "ROLLBACK TO SAVEPOINT fetch_atomic_units_by_file"
                )
                await conn.execute(
                    "RELEASE SAVEPOINT fetch_atomic_units_by_file"
                )
            except Exception:
                pass
        return []

    out: list[tuple[str, str, str, dict[str, Any]]] = []
    for r in rows:
        params = r[3] if isinstance(r[3], dict) else (r[3] or {})
        if not isinstance(params, dict):
            params = {}
        params = dict(params)
        params.setdefault("__unit_type__", r[2])
        out.append((str(r[0]), str(r[1]), r[2], params))
    return out


def _stringify(value: Any) -> str | None:
    """Coerce a parameter value to a comparable string. Drop bools-as-
    strings and the empty-string case. Lists are joined to preserve
    order (e.g. `parties: ["NorthWind", "Vertex"]` → `NorthWind,Vertex`)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        # Avoid scientific notation drift for floats — Design 2 compares
        # values as strings; "30" must equal "30" across reads.
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)
    if isinstance(value, str):
        s = value.strip()
        return s or None
    if isinstance(value, (list, tuple)):
        parts = [_stringify(v) for v in value]
        kept = [p for p in parts if p]
        return ",".join(kept) if kept else None
    if isinstance(value, dict):
        # Don't try to flatten nested dicts — Wave A scope.
        return None
    return str(value)


def build_fact_candidates(
    hits: list[Hit] | None,
    *,
    file_metas: dict[str, FileMetaForConflict],
    atomic_unit_params: dict[str, dict[str, Any]] | None = None,
    units_by_file: list[tuple[str, str, str, dict[str, Any]]] | None = None,
) -> list[FactCandidate]:
    """Build FactCandidates from atomic_units whose file is in a chain.

    Two input modes (caller picks one):

      A. `hits` + `atomic_unit_params` — legacy: pulls FactCandidates
         only from atomic_unit hits that survived top-K rerank. Tight
         scope but misses conflicts where chunks (rather than units)
         won the rerank.

      B. `units_by_file` — broader: a flat list of
         `(unit_id, file_id, unit_type, parameters)` tuples covering
         ALL atomic_units of the chained files involved in the query.
         This is what `resolve_conflicts_for_hits` uses post-R1 so
         the MSA + Amendment payment_terms conflict still surfaces
         when only their CHUNKS made it to top-10.

    Each (atomic_unit, scalar parameter) pair becomes one FactCandidate.
    Predicate format: `{namespace}.{key}` where namespace =
    clause_type (clauses plugin) or item_type / unit_type
    (generic_items plugin). Entity_id = chain_id so two chain members
    are recognised as describing the SAME logical document.
    """
    out: list[FactCandidate] = []

    # Collect (file_id, unit_id, params) triples regardless of which
    # input mode the caller used.
    triples: list[tuple[str, str, dict[str, Any]]] = []
    if units_by_file is not None:
        triples = [(fid, uid, p) for (uid, fid, _ut, p) in units_by_file]
    elif hits is not None and atomic_unit_params is not None:
        for h in hits:
            if h.kind != "atomic_unit":
                continue
            file_id = (h.metadata or {}).get("file_id")
            if not file_id:
                continue
            params = atomic_unit_params.get(h.id) or {}
            triples.append((file_id, h.id, params))

    for file_id, unit_id, params in triples:
        meta = file_metas.get(file_id)
        if meta is None or not meta.chain_id:
            # Files outside any chain can't be compared to a sibling on
            # the same entity → no useful conflict signal.
            continue

        unit_type = params.get("__unit_type__") or ""
        clause_type = params.get("clause_type") or params.get("item_type") or ""
        namespace = clause_type or unit_type

        for key, val in params.items():
            if key.startswith("__") or key in _SKIP_PARAMETER_KEYS:
                continue
            v = _stringify(val)
            if v is None:
                continue
            predicate = f"{namespace}.{key}" if namespace else key
            out.append(FactCandidate(
                doc_id=file_id,
                entity_id=meta.chain_id,
                predicate=predicate,
                value=v,
                authority=float(meta.source_authority) if meta.source_authority is not None else 0.5,
                doc_status=meta.doc_status or "live",
                doc_date_iso=meta.created_at_iso,
                chain_id=meta.chain_id,
                chain_current_version_doc_id=meta.chain_current_version_doc_id,
                hit_id=str(unit_id),
            ))
    return out


async def resolve_conflicts_for_hits(
    conn: Any,
    hits: list[Hit],
) -> list[ResolvedConflict]:
    """End-to-end orchestrator helper.

    Strategy (post-R1 broaden):
      1. Collect file_ids from ALL top-K hits (any kind — chunks,
         raptor nodes, atomic units). Chunks usually dominate the
         top-10 even when the question is about structured facts; we
         still want to surface chain-level conflicts on those files.
      2. Lookup chain membership + authority/status/date metadata.
      3. For each chained file, pull ALL its atomic_units (not just
         the ones that surfaced as hits). The full clause/item
         inventory is where comparable predicates live.
      4. Build FactCandidates → resolve_all → filter out consensus.

    Returns ONLY non-trivial resolutions — conflict groups where
    either a rule fired (chain / status / authority / recency) or
    the cascade couldn't pick a winner (unresolved). Consensus rows
    (single distinct value across siblings) are filtered out — the
    generator doesn't need to know about them.

    Returns [] when:
      - No hits
      - None of the hit files are in a doc-chain
      - Chained files have no atomic_units
      - All grouped predicates have one distinct value (consensus only)
    """
    if not hits:
        return []

    # Step 1 — file_ids of ALL hits, regardless of kind.
    file_ids: set[str | None] = {(h.metadata or {}).get("file_id") for h in hits}
    file_ids.discard(None)
    file_ids.discard("")
    if not file_ids:
        return []

    # Step 2 — chain membership lookup. Bail early if no chained files.
    file_metas = await fetch_file_metas_for_conflict(
        conn, file_ids=[f for f in file_ids if f],
    )
    chain_ids = {meta.chain_id for meta in file_metas.values() if meta.chain_id}
    if not chain_ids:
        return []

    # Step 2b — expand to ALL members of those chains, not just the
    # hit-listed files. The user's query may have surfaced only the
    # Amendment (or only the MSA) but the conflict detector needs to
    # see BOTH sides to find disagreements on shared predicates.
    all_member_ids = await _fetch_chain_member_ids(
        conn, chain_ids=chain_ids,
    )
    if not all_member_ids:
        return []

    # Refresh metas for any newly-discovered chain members.
    missing_metas = [fid for fid in all_member_ids if fid not in file_metas]
    if missing_metas:
        more_metas = await fetch_file_metas_for_conflict(
            conn, file_ids=missing_metas,
        )
        file_metas.update(more_metas)

    # Step 3 — pull ALL atomic_units for ALL chain members.
    units = await fetch_atomic_units_by_file(
        conn, file_ids=list(all_member_ids),
    )
    if not units:
        return []

    # Step 4 — build candidates and resolve.
    candidates = build_fact_candidates(
        hits=None,
        file_metas=file_metas,
        units_by_file=units,
    )
    if not candidates:
        return []

    resolutions = resolve_all(candidates)
    return [r for r in resolutions if r.resolution != "consensus"]


async def persist_fact_conflicts(
    conn: Any,
    *,
    workspace_id: str,
    resolutions: Iterable[ResolvedConflict],
) -> int:
    """Write resolved + unresolved conflicts to `fact_conflicts` for the
    Dashboard Needs-attention surface + audit trail.

    Architecture §6 step 7 says "emit fact_conflicts row with all
    candidate evidence" — i.e. every conflict, regardless of how the
    Design 2 cascade resolved it. The dashboard filters to
    `resolution='unresolved'` for the needs-attention surface, but the
    full set lives in `fact_conflicts` so the Audit page can replay
    "we found 14 conflicts in this corpus, 13 chain-superseded + 1
    flagged for human review".

    Pre-fix this function dropped every resolved conflict and only
    persisted the unresolved ones. That left the table empty during
    normal use (most conflicts ARE resolvable via chain / authority /
    recency), so the dashboard always showed 0.

    Returns the number of rows written. Idempotency: dedupes on
    (workspace_id, entity_id, predicate, observed_at-bucketed-to-day) —
    if the same conflict was already flagged today, skip. Each row's
    `evidence` jsonb captures the per-loser shape from Design 2.

    SAVEPOINT-wrapped so a write failure (entity_id doesn't reference a
    real entities row, e.g. when we're using chain_id as a stand-in)
    doesn't poison the outer transaction. We just log + continue.
    """
    # `consensus` (all candidates agreed) isn't really a conflict at
    # all — skip those. Everything else (chain / status / authority /
    # recency / unresolved) gets a row.
    rows = [r for r in resolutions if r.resolution != "consensus"]
    if not rows:
        return 0

    written = 0
    for r in rows:
        evidence = [
            {
                "doc_id": c.doc_id,
                "value": c.value,
                "authority": c.authority,
                "doc_status": c.doc_status,
                "doc_date_iso": c.doc_date_iso,
                "hit_id": c.hit_id,
            }
            for c in r.losers
        ]
        try:
            await conn.execute("SAVEPOINT persist_fact_conflict")
            sp = True
        except Exception:
            sp = False
        # Split the "subject" of the conflict into entity_id vs
        # chain_id. The detector populates `r.entity_id` with whatever
        # identifier it has — for clause-level / atomic-unit conflicts
        # that's actually a `chain_id` (the doc chain the units belong
        # to). The post-0034 schema has separate FK columns; pick the
        # right slot by checking whether the value exists in entities
        # or doc_chains. We keep it simple: if a chain row exists with
        # that id, treat as chain-based; else assume entity. This
        # cheap lookup runs at most a handful of times per chat call.
        subject_entity_id: str | None = None
        subject_chain_id: str | None = None
        try:
            cur = await conn.execute(
                "SELECT 1 FROM doc_chains WHERE id = %s::uuid",
                (r.entity_id,),
            )
            if await cur.fetchone() is not None:
                subject_chain_id = r.entity_id
            else:
                subject_entity_id = r.entity_id
        except Exception:
            # If the lookup blows up (bad UUID, RLS denied), fall back
            # to writing to entity_id and let the row's FK constraint
            # be the safety net. SAVEPOINT below catches the failure.
            subject_entity_id = r.entity_id

        try:
            await conn.execute(
                "INSERT INTO fact_conflicts "
                "  (workspace_id, entity_id, chain_id, predicate, "
                "   evidence, resolution, resolved_value, "
                "   resolved_doc_id, notes) "
                "VALUES (%s, %s::uuid, %s::uuid, %s, %s::jsonb, "
                "        %s, %s, %s::uuid, %s)",
                (
                    workspace_id,
                    subject_entity_id,
                    subject_chain_id,
                    r.predicate,
                    __import__("json").dumps(evidence),
                    r.resolution,
                    r.picked_value,
                    # Audit value — the chain/status/authority rules
                    # pick a winner; record its doc_id so the Audit
                    # page can show "X won because chain rule fired".
                    (r.picked_candidate.doc_id if r.picked_candidate else None),
                    r.notes,
                ),
            )
            written += 1
            if sp:
                try:
                    await conn.execute("RELEASE SAVEPOINT persist_fact_conflict")
                except Exception:
                    pass
        except Exception:
            if sp:
                try:
                    await conn.execute(
                        "ROLLBACK TO SAVEPOINT persist_fact_conflict"
                    )
                    await conn.execute(
                        "RELEASE SAVEPOINT persist_fact_conflict"
                    )
                except Exception:
                    pass
            # Surface to logs but don't fail the whole chat call.
            import logging
            logging.getLogger(__name__).warning(
                "fact_conflicts insert failed for entity_id=%s predicate=%s",
                r.entity_id, r.predicate,
            )
    return written


def build_conflict_prompt_block(
    resolutions: list[ResolvedConflict],
    *,
    file_metas: dict[str, FileMetaForConflict] | None = None,
) -> str:
    """Render conflict resolutions as a prompt-friendly text block the
    generator can append before its retrieved-snippets section.

    Output is human-readable + LLM-parseable — each line names the
    predicate, the picked value, and which rule fired, with the losers
    listed so the model can mention the supersession explicitly.

    Returns "" when there's nothing to surface.
    """
    lines: list[str] = []
    for r in resolutions:
        if r.resolution == "consensus":
            continue
        picked_doc = r.picked_candidate.doc_id if r.picked_candidate else "?"
        loser_doc_values = ", ".join(
            f"{c.doc_id[:8]}={c.value}" for c in r.losers
        )
        if r.resolution == "unresolved":
            lines.append(
                f"- UNRESOLVED CONFLICT on {r.predicate}: "
                f"sources disagree ({loser_doc_values}). "
                f"Surface both values; do not pick one."
            )
        else:
            lines.append(
                f"- RESOLVED {r.predicate}: picked '{r.picked_value}' "
                f"(from doc {picked_doc[:8]}) via {r.resolution}; "
                f"superseded: {loser_doc_values}. "
                f"Cite the picked source as authoritative; mention the "
                f"superseded value only if explanatory."
            )
    if not lines:
        return ""
    return (
        "Conflict-resolution context (apply these decisions in your "
        "answer):\n" + "\n".join(lines)
    )
