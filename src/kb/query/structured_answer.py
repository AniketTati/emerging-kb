"""T2 (§6.3 / §6.4 / §5a / §5c) — answer-mode + trust gate + structured answers.

This is the "answer-DIRECT from structured" head that runs BEFORE the RAG
generator when the resolved predicate is trustworthy enough. It covers three
answer modes; everything else (NARRATIVE / HYBRID / RELATIONSHIP / AGGREGATE)
falls through to the existing retrieval+generation path.

  - **LIST** (§5c) — enumerate the resolved doc set (each cited), not a
    10-chunk RAG sample ("show all change orders over $50k").
  - **EXISTENCE** (§5a) — yes / no / hedge. Never a bare "No": asserting "none"
    requires near-complete coverage AND a high-confidence, non-entity clause
    (§6.3); otherwise it hedges and lets RAG try.
  - **LOOKUP** (§5a) — read one field value and **P2-confirm** it appears in /
    normalizes to its cited source chunk before shipping; if it can't be
    confirmed, return None → degrade to RAG (this is the coverage≠correctness
    guard, §6.3 / §8.3).

Every builder returns `None` to mean "I can't answer this directly — use RAG",
so the smart path always degrades to plain retrieval (I1/I2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from kb.query.structured_prefilter import (
    DEFAULT_CONSTANTS,
    ResolvedPredicate,
    ResolverConstants,
)


# ---------------------------------------------------------------------------
# answer_mode (§6.4) — provisional at S2, revised at S4.
# ---------------------------------------------------------------------------

LOOKUP = "LOOKUP"
AGGREGATE = "AGGREGATE"
EXISTENCE = "EXISTENCE"
LIST = "LIST"
NARRATIVE = "NARRATIVE"
HYBRID = "HYBRID"
RELATIONSHIP = "RELATIONSHIP"
CONVERSATIONAL = "CONVERSATIONAL"

ANSWER_MODES = frozenset({
    LOOKUP, AGGREGATE, EXISTENCE, LIST, NARRATIVE, HYBRID, RELATIONSHIP,
    CONVERSATIONAL,
})

# plan.mode (routing) + intent → provisional answer_mode. Conservative: when
# unsure, NARRATIVE (which degrades to plain RAG).
_MODE_TO_ANSWER = {
    "Q": AGGREGATE,
    "T": RELATIONSHIP,
    "G": NARRATIVE,
    "S": NARRATIVE,
    "E": NARRATIVE,     # entity profile is descriptive, not a single value
    "F": LIST,          # filter whole documents → enumerate them
    "D": LIST,          # doc-metadata filter → list docs
    "M": LIST,          # mention search → list where mentioned
    "C": LIST,          # atomic-unit filter → list rows
    "A": LIST,          # anomaly → list rare rows
    "K": NARRATIVE,
    "H": NARRATIVE,
    "I": LIST,
}
_INTENT_TO_ANSWER = {
    "factoid": LOOKUP,
    "negative": EXISTENCE,
    "aggregation": AGGREGATE,
    "set_operation": LIST,
    "multi-hop": RELATIONSHIP,
    "global/thematic": NARRATIVE,
    "field_filter": LIST,
    "entity_lookup": LOOKUP,
    "mention_search": LIST,
    "unit_filter": LIST,
    "anomaly": LIST,
    "doc_metadata": LIST,
    "temporal_history": NARRATIVE,
    "chain_aware": NARRATIVE,
    "scoped_summarize": NARRATIVE,
    "vague": NARRATIVE,
}

# Existence cue — "is there", "are there (any)", "do we/you have", "does X
# exist". Deliberately NOT "is the …" (that's a LOOKUP, e.g. "what is the cap").
_EXISTENCE_RE = re.compile(
    r"\b(is|are|was|were)\s+there\b|"
    r"\b(do|does|did)\s+(we|you|i|they|it)\s+have\b|"
    r"\b(exist|exists)\b|"
    r"\bany\s+\w+\s+(present|available|on\s+file)\b",
    re.IGNORECASE,
)


def provisional_answer_mode(plan_mode: str, intent: str | None, query: str = "") -> str:
    """Map the routing mode + intent → a provisional answer_mode (§6.4 S2)."""
    # An existence-phrased query overrides a LOOKUP/LIST default.
    if _EXISTENCE_RE.search(query or "") and (intent in ("negative", "factoid", None)):
        return EXISTENCE
    if intent and intent in _INTENT_TO_ANSWER:
        am = _INTENT_TO_ANSWER[intent]
    else:
        am = _MODE_TO_ANSWER.get((plan_mode or "H").upper(), NARRATIVE)
    return am


def revise_answer_mode(
    provisional: str, predicate: ResolvedPredicate,
    *, constants: ResolverConstants = DEFAULT_CONSTANTS,
) -> tuple[str, str | None]:
    """Stage 4 (§6.4) — revise the mode using resolver facts. Returns
    (answer_mode, reason_for_change|None).

      - LOOKUP resolving to >1 doc → LIST (never a bare single value across
        many docs — fixes HDFC/salary).
      - AGGREGATE that depends on an entity clause or a low-confidence /
        low-coverage field → LIST + caveat ("show the docs").
    """
    scope_n = predicate.scoped_doc_count  # -1 == ALL
    if provisional == LOOKUP and scope_n > 1:
        return LIST, "lookup_multi_doc"
    if provisional == AGGREGATE:
        entity_dep = any(c.kind == "entity" for c in predicate.clauses)
        low_conf = (
            not predicate.is_all
            and predicate.overall_confidence < constants.scope_conf_hard
        )
        if entity_dep or low_conf:
            return LIST, ("aggregate_entity_dependent" if entity_dep
                          else "aggregate_low_confidence")
    return provisional, None


# ---------------------------------------------------------------------------
# Trust gate (§6.3) — presence × correctness, weakest-clause, entity-always-low.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrustVerdict:
    can_answer_direct: bool   # may we attempt an answer-direct (vs RAG)?
    can_assert_none: bool     # may we assert a confident "none/no"?
    entity_dependent: bool
    weakest_coverage: float
    reason: str


def trust_gate(
    predicate: ResolvedPredicate,
    *, constants: ResolverConstants = DEFAULT_CONSTANTS,
) -> TrustVerdict:
    """Two-part, weakest-link trust gate (§6.3). Presence (coverage) is
    necessary but not sufficient; the correctness half (P2) is checked at
    render time. Entity clauses are ALWAYS treated as low (mention resolution
    ~47%) so an entity-link miss can never produce a confident 'no'."""
    constraining = [
        c for c in predicate.clauses
        if c.kind in ("field", "entity", "doctype", "unit")
        and (c.file_ids or c.confidence > 0 or c.ambiguous)
    ]
    entity_dependent = any(c.kind == "entity" for c in constraining)
    weakest_cov = min([c.coverage for c in constraining], default=0.0)
    # answer-direct only when the predicate is a HIGH-confidence scope (entity
    # clauses cap confidence below the hard threshold, so entity-dependent
    # predicates are never answer-direct → they go to RAG).
    can_direct = (not predicate.is_all) and predicate.is_hard(constants)
    # assert "none" only with near-complete coverage AND high confidence AND no
    # entity dependence (an entity miss looks like "none" but isn't).
    can_none = (
        weakest_cov >= constants.coverage_complete
        and predicate.overall_confidence >= constants.scope_conf_hard
        and not entity_dependent
    )
    reason = (
        "entity_dependent" if entity_dependent
        else ("hard_scope" if can_direct else "low_confidence")
    )
    return TrustVerdict(
        can_answer_direct=can_direct, can_assert_none=can_none,
        entity_dependent=entity_dependent, weakest_coverage=weakest_cov,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Structured answer
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StructuredAnswer:
    answer: str
    citations: list = field(default_factory=list)   # list[Citation]
    answer_mode: str = ""
    source: str = "structured"     # 'list' | 'existence' | 'lookup'
    p2_confirmed: bool = False
    notes: str | None = None


# -- SQL helpers (savepoint-protected) --------------------------------------


async def _safe(conn: Any, sp: str, sql: str, params: Any) -> list[tuple] | None:
    try:
        await conn.execute(f"SAVEPOINT {sp}")
    except Exception:  # noqa: BLE001
        return None
    try:
        cur = await conn.execute(sql, params)
        rows = await cur.fetchall()
        try:
            await conn.execute(f"RELEASE SAVEPOINT {sp}")
        except Exception:  # noqa: BLE001
            pass
        return rows
    except Exception:  # noqa: BLE001
        try:
            await conn.execute(f"ROLLBACK TO SAVEPOINT {sp}")
            await conn.execute(f"RELEASE SAVEPOINT {sp}")
        except Exception:  # noqa: BLE001
            pass
        return None


async def _file_names(conn: Any, workspace_id: str, file_ids: list[str]) -> dict[str, str]:
    if not file_ids:
        return {}
    rows = await _safe(
        conn, "sa_file_names",
        "SELECT id::text, name FROM files WHERE workspace_id = %s AND id = ANY(%s::uuid[])",
        (workspace_id, file_ids),
    )
    return {str(r[0]): (r[1] or "document") for r in (rows or [])}


async def _field_values(
    conn: Any, workspace_id: str, file_ids: list[str], key: str,
) -> dict[str, Any]:
    """{file_id: fields[key]} over doc_root rows for the given scope."""
    if not file_ids or not key:
        return {}
    rows = await _safe(
        conn, "sa_field_values",
        "SELECT file_id::text, fields -> %s FROM extracted_entities "
        "WHERE workspace_id = %s AND unit_type IS NULL AND file_id = ANY(%s::uuid[]) "
        "AND jsonb_exists(fields, %s)",
        (key, workspace_id, file_ids, key),
    )
    return {str(r[0]): r[1] for r in (rows or [])}


async def _lookup_value_with_source(
    conn: Any, workspace_id: str, file_id: str, key: str,
) -> tuple[Any, str | None, str | None]:
    """Return (value, source_chunk_id, source_chunk_text) for one doc's field.
    The per-field citation map (extracted_entities.citations) points at the
    contextual_chunk the value was extracted from (P2 source)."""
    rows = await _safe(
        conn, "sa_lookup_val",
        "SELECT ee.fields -> %s, (ee.citations ->> %s) "
        "FROM extracted_entities ee "
        "WHERE ee.workspace_id = %s AND ee.unit_type IS NULL AND ee.file_id = %s "
        "AND jsonb_exists(ee.fields, %s) LIMIT 1",
        (key, key, workspace_id, file_id, key),
    )
    if not rows:
        return (None, None, None)
    value, chunk_id = rows[0][0], rows[0][1]
    text = None
    if chunk_id:
        crows = await _safe(
            conn, "sa_lookup_chunk",
            "SELECT contextual_text FROM contextual_chunks WHERE id = %s",
            (chunk_id,),
        )
        if crows:
            text = crows[0][0]
    return (value, chunk_id, text)


def _value_in_text(value: Any, text: str | None) -> bool:
    """P2 confirmation: does `value` appear in / normalize to `text`?"""
    if text is None or value is None:
        return False
    t = text.lower()
    sval = str(value).strip().lower()
    if sval and sval in t:
        return True
    # Numeric: compare the bare integer/decimal digits (tolerate currency
    # symbols, thousands separators, and a trailing .0 from jsonb floats).
    try:
        num = float(value)
    except (TypeError, ValueError):
        return False
    digits = re.sub(r"[^0-9]", "", str(int(num)) if num.is_integer() else str(num))
    if not digits:
        return False
    text_digits = re.sub(r"[,\s]", "", t)
    return digits in re.sub(r"[^0-9]", "", text_digits) or str(num) in t


def _cite(file_id: str, snippet: str) -> Any:
    from kb.query.generate import Citation
    return Citation(
        hit_id=file_id, kind="chunk", file_id=file_id,
        snippet_preview=snippet[:240], score=1.0,
    )


# -- builders ---------------------------------------------------------------


def _predicate_label(predicate: ResolvedPredicate) -> str:
    parts: list[str] = []
    for c in predicate.clauses:
        if c.kind == "field" and c.canonical_key:
            parts.append(f"{c.canonical_key} {c.op or 'eq'} {c.value}")
        elif c.kind == "entity" and c.entity_names:
            parts.append(f"mentioning {c.entity_names[0]}")
        elif c.kind in ("doctype", "unit") and c.surface:
            parts.append(str(c.surface))
    return "; ".join(parts) or "your filter"


async def build_list_answer(
    conn: Any, *, workspace_id: str, predicate: ResolvedPredicate, query: str,
) -> StructuredAnswer | None:
    """S5c — enumerate the resolved doc set (each cited). Returns None when
    there's no usable scope (→ RAG)."""
    if predicate.is_all or not predicate.file_scope:
        return None
    file_ids = sorted(predicate.file_scope)
    names = await _file_names(conn, workspace_id, file_ids)
    if not names:
        return None
    # If a field clause drove the scope, show its value per doc.
    field_clause = next(
        (c for c in predicate.clauses if c.kind == "field" and c.canonical_key),
        None,
    )
    values: dict[str, Any] = {}
    if field_clause is not None:
        values = await _field_values(conn, workspace_id, file_ids, field_clause.canonical_key)

    lines = [f"Found {len(file_ids)} document(s) matching {_predicate_label(predicate)}:"]
    citations = []
    for fid in file_ids:
        name = names.get(fid, "document")
        if fid in values and values[fid] is not None:
            key = field_clause.canonical_key
            lines.append(f"- **{name}** — {key}: {values[fid]}")
            citations.append(_cite(fid, f"{name}: {key}={values[fid]}"))
        else:
            lines.append(f"- **{name}**")
            citations.append(_cite(fid, name))
    return StructuredAnswer(
        answer="\n".join(lines), citations=citations,
        answer_mode=LIST, source="list",
        notes=f"listed {len(file_ids)} docs from structured scope",
    )


async def build_existence_answer(
    conn: Any, *, workspace_id: str, predicate: ResolvedPredicate, query: str,
    constants: ResolverConstants = DEFAULT_CONSTANTS,
) -> StructuredAnswer | None:
    """S5a existence — yes / no / hedge. Never a bare 'No' (§6.3).

    A 0-match field clause collapses the scope to ALL via AND-salvage, so the
    "no" decision reads the CLAUSE's own coverage/confidence, not the predicate
    scope. Asserting "none" needs a near-complete-coverage, high-confidence,
    NON-entity clause; otherwise we hedge (return None → RAG)."""
    # Found — any constraining clause resolved to a non-empty doc set.
    if predicate.file_scope:
        file_ids = sorted(predicate.file_scope)
        names = await _file_names(conn, workspace_id, file_ids)
        shown = list(names.values())[:5]
        more = "" if len(file_ids) <= 5 else f" (+{len(file_ids) - 5} more)"
        return StructuredAnswer(
            answer=(
                f"Yes — found {len(file_ids)} document(s) matching "
                f"{_predicate_label(predicate)}: {', '.join(shown)}{more}."
            ),
            citations=[_cite(fid, names.get(fid, "document")) for fid in file_ids[:10]],
            answer_mode=EXISTENCE, source="existence",
        )
    # Not found — assert a clean "no" ONLY for a definite (non-entity) clause
    # that resolved to ZERO docs with near-complete coverage + high confidence.
    for c in predicate.clauses:
        if (
            c.kind in ("field", "doctype", "unit")
            and not c.file_ids and not c.ambiguous
            and c.coverage >= constants.coverage_complete
            and c.confidence >= constants.scope_conf_hard
        ):
            return StructuredAnswer(
                answer=(
                    f"No — no documents match {_predicate_label(predicate)}. "
                    "(Checked against the structured data, which is "
                    "near-complete for this field.)"
                ),
                citations=[], answer_mode=EXISTENCE, source="existence",
                notes="confident_none",
            )
    return None  # weak / entity-based / low-coverage → hedge via RAG (never bare "No")


async def build_lookup_answer(
    conn: Any, *, workspace_id: str, predicate: ResolvedPredicate, query: str,
) -> StructuredAnswer | None:
    """S5a lookup — read ONE field value for ONE doc and P2-confirm it appears
    in its source chunk. Returns None (→ RAG) if it can't be confirmed."""
    if predicate.is_all or not predicate.file_scope:
        return None
    file_ids = sorted(predicate.file_scope)
    if len(file_ids) != 1:
        return None  # >1 doc is a LIST, not a bare lookup (handled at S4)
    field_clause = next(
        (c for c in predicate.clauses if c.kind == "field" and c.canonical_key),
        None,
    )
    if field_clause is None:
        return None
    fid = file_ids[0]
    value, _chunk_id, chunk_text = await _lookup_value_with_source(
        conn, workspace_id, fid, field_clause.canonical_key,
    )
    if value is None:
        return None
    # P2 — the value MUST be confirmable in its cited source chunk.
    if not _value_in_text(value, chunk_text):
        return None  # coverage≠correctness — degrade to RAG (§8.3)
    names = await _file_names(conn, workspace_id, [fid])
    name = names.get(fid, "the document")
    return StructuredAnswer(
        answer=f"{field_clause.canonical_key.replace('_', ' ').title()}: {value} (from {name}).",
        citations=[_cite(fid, str(value))],
        answer_mode=LOOKUP, source="lookup", p2_confirmed=True,
    )


# ---------------------------------------------------------------------------
# False-premise / locator-existence gate (§6.13).
# ---------------------------------------------------------------------------

_LOCATOR_RE = re.compile(
    r"\b(clause|section|article|exhibit|schedule|appendix|annexure|annex|"
    r"paragraph|para|item|attachment|addendum)\s+(?:no\.?\s*)?"
    r"([0-9]{1,4}[A-Za-z]?(?:\.[0-9]+)*)\b",
    re.IGNORECASE,
)


def parse_locator(query: str) -> tuple[str, str] | None:
    """Extract an asserted structural locator (e.g. ('clause', '99')) from the
    query, or None. Used by the §6.13 existence gate to catch the QUESTION
    form ('what does clause 99 say?') that the assertion-form adversarial
    pre-filter doesn't."""
    m = _LOCATOR_RE.search(query or "")
    if not m:
        return None
    return (m.group(1).lower(), m.group(2))


async def locator_exists(
    conn: Any, *, workspace_id: str, scope: Any, locator: tuple[str, str],
) -> bool:
    """Does the locator phrase appear in the scoped docs (or the whole corpus
    when unscoped)? Checked via ILIKE over chunk text in a few common phrasings
    AND any matching unit_type rows. Conservative: returns True on ANY match or
    on a query error (fail-OPEN — never block a legitimate query on a false
    'absent'); only a confident absence lets the caller redirect."""
    ltype, lnum = locator
    patterns = [
        f"%{ltype} {lnum}%", f"%{ltype} no. {lnum}%", f"%{ltype} no {lnum}%",
        f"%{ltype}-{lnum}%", f"%{ltype}.{lnum}%",
    ]
    scope_list = sorted(scope) if scope else None
    scope_sql = " AND cc.file_id = ANY(%(scope)s::uuid[]) " if scope_list else ""
    params = {"ws": workspace_id, "pats": patterns}
    if scope_list:
        params["scope"] = scope_list
    rows = await _safe(
        conn, "sa_locator_chunk",
        "SELECT 1 FROM contextual_chunks cc "
        "WHERE cc.workspace_id = %(ws)s "
        "  AND lower(cc.contextual_text) ILIKE ANY(%(pats)s) "
        + scope_sql + " LIMIT 1",
        params,
    )
    if rows is None:
        return True   # query error → fail open (do not block)
    return bool(rows)


async def try_structured_answer(
    conn: Any,
    *,
    workspace_id: str,
    query: str,
    predicate: ResolvedPredicate,
    answer_mode: str,
    constants: ResolverConstants = DEFAULT_CONSTANTS,
) -> StructuredAnswer | None:
    """Attempt an answer-direct from structured data for LIST / EXISTENCE /
    LOOKUP. Returns None for every other mode, or whenever the trust gate /
    P2 check isn't satisfied — the caller then runs the normal RAG path."""
    if conn is None or predicate is None:
        return None
    trust = trust_gate(predicate, constants=constants)

    if answer_mode == EXISTENCE:
        return await build_existence_answer(
            conn, workspace_id=workspace_id, predicate=predicate,
            query=query, constants=constants,
        )
    # LIST / LOOKUP answer-direct only on a high-confidence (hard) scope; a
    # soft/low-confidence scope falls through to scoped RAG (P1).
    if not trust.can_answer_direct:
        return None
    if answer_mode == LIST:
        return await build_list_answer(
            conn, workspace_id=workspace_id, predicate=predicate, query=query,
        )
    if answer_mode == LOOKUP:
        return await build_lookup_answer(
            conn, workspace_id=workspace_id, predicate=predicate, query=query,
        )
    return None
