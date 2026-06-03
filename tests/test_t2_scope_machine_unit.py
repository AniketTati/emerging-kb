"""T2 Phase C — scope state machine, predicate carry-forward, S0.5 gate.

  - classify_non_retrieval (pure): greeting/thanks/ack/meta vs real queries.
  - chat_memory: carry_forward_predicate persist + read round-trip (+ reset).
  - scope_state_machine (DB, re-resolves for RELAX):
    NONE / CLEAR (schema doc-noun + aggregate) / RELAX (loosen bound — §8.5) /
    INTERSECT / intersect_empty / REPLACE / INHERIT.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio


# ---- seed helpers (local; mirror test_t2_structured_prefilter_unit) --------


async def _ensure_doc_root_se(conn, ws: str) -> str:
    cur = await conn.execute(
        "INSERT INTO schemas (workspace_id, name, lifecycle_state) "
        "VALUES (%s, %s, 'active') ON CONFLICT DO NOTHING RETURNING id",
        (ws, "auto:t2c:test"),
    )
    row = await cur.fetchone()
    if row is None:
        cur = await conn.execute(
            "SELECT id FROM schemas WHERE workspace_id = %s AND name = %s",
            (ws, "auto:t2c:test"),
        )
        row = await cur.fetchone()
    schema_id = row[0]
    cur = await conn.execute(
        "SELECT id FROM schema_entities WHERE workspace_id = %s AND schema_id = %s "
        "AND kind = 'doc_root' LIMIT 1", (ws, schema_id),
    )
    row = await cur.fetchone()
    if row is not None:
        return str(row[0])
    cur = await conn.execute(
        "INSERT INTO schema_entities (schema_id, workspace_id, name, lifecycle_state, kind) "
        "VALUES (%s, %s, 'DocRoot', 'active', 'doc_root') RETURNING id", (schema_id, ws),
    )
    return str((await cur.fetchone())[0])


async def _seed_doc(conn, ws: str, *, doc_type: str, fields: dict, label: str,
                    se_id: str | None = None) -> str:
    import json
    fid = str(uuid.uuid4())
    sha = (uuid.uuid4().hex * 2)[:64]
    await conn.execute(
        "INSERT INTO files (id, workspace_id, name, content_sha, object_key, "
        "mime_type, size_bytes, lifecycle_state, inferred_doc_type) "
        "VALUES (%s, %s, %s, %s, %s, 'application/pdf', 100, 'ready', %s)",
        (fid, ws, f"{label}.pdf", sha, f"raw_files/{sha}", doc_type),
    )
    if se_id is None:
        se_id = await _ensure_doc_root_se(conn, ws)
    await conn.execute(
        "INSERT INTO extracted_entities (schema_entity_id, file_id, workspace_id, "
        "fields, citations, model_id) VALUES (%s, %s, %s, %s::jsonb, '{}'::jsonb, 'mock')",
        (se_id, fid, ws, json.dumps(fields)),
    )
    return fid


async def _set_ws(conn, ws: str) -> None:
    await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (ws,))


def _plan(**kw):
    from types import SimpleNamespace
    base = dict(field_filters=(), seed_entities=(), doc_types=(),
                unit_types=(), date_filters=(), mode="F")
    base.update(kw)
    return SimpleNamespace(**base)


# ===========================================================================
# S0.5 classifier (pure)
# ===========================================================================


async def test_classify_non_retrieval():
    from kb.query.orchestrator import classify_non_retrieval as c
    assert c("thanks!") == "thanks"
    assert c("Hi there") == "greeting"
    assert c("ok") == "ack"
    assert c("what can you do") == "meta"
    # Real questions (even riding a pleasantry) are NOT swallowed.
    assert c("thanks, now what's the total revenue?") is None
    assert c("what is the interest rate on the loan?") is None
    assert c("okay so walk me through the indemnification clause in detail") is None
    assert c("") is None


# ===========================================================================
# chat_memory — predicate carry-forward
# ===========================================================================


async def test_carry_forward_predicate_roundtrip(db_superuser):
    from kb.domain.chat_memory import (
        create_session, read_session, update_session_carry_forward,
    )
    ws = str(uuid.uuid4())
    await _set_ws(db_superuser, ws)
    sid = await create_session(db_superuser, workspace_id=ws, title="t")
    # default empty
    sess = await read_session(db_superuser, session_id=sid)
    assert sess.carry_forward_predicate == {}
    # write a predicate (file_scope holds real file uuids — the denormalized
    # carry_forward_file_scope column is uuid[]).
    fa, fb = str(uuid.uuid4()), str(uuid.uuid4())
    pred = {"file_scope": [fa, fb], "overall_confidence": 0.9,
            "clauses": [{"kind": "field", "canonical_key": "total"}]}
    await update_session_carry_forward(
        db_superuser, session_id=sid,
        carry_forward_predicate=pred, carry_forward_file_scope=[fa, fb],
    )
    sess = await read_session(db_superuser, session_id=sid)
    assert sess.carry_forward_predicate["overall_confidence"] == 0.9
    assert sess.carry_forward_predicate["file_scope"] == [fa, fb]
    # reset with {} (distinct from None = leave unchanged)
    await update_session_carry_forward(
        db_superuser, session_id=sid, carry_forward_predicate={},
    )
    sess = await read_session(db_superuser, session_id=sid)
    assert sess.carry_forward_predicate == {}


# ===========================================================================
# scope_state_machine
# ===========================================================================


async def _seed_loans(conn, ws):
    """3 loans: rates 9.5 / 8.5 / 7.0 (all have interest_rate)."""
    await _set_ws(conn, ws)
    l1 = await _seed_doc(conn, ws, doc_type="loan", fields={"interest_rate": 9.5}, label="l1")
    l2 = await _seed_doc(conn, ws, doc_type="loan", fields={"interest_rate": 8.5}, label="l2")
    l3 = await _seed_doc(conn, ws, doc_type="loan", fields={"interest_rate": 7.0}, label="l3")
    return l1, l2, l3


async def test_scope_machine_none_when_no_inherited(db_superuser):
    from kb.domain.structured_schema import clear_cache, live_schema
    from kb.query.structured_prefilter import resolve, scope_state_machine
    clear_cache()
    ws = str(uuid.uuid4())
    l1, l2, l3 = await _seed_loans(db_superuser, ws)
    schema = await live_schema(db_superuser, workspace_id=ws)
    fresh = await resolve(db_superuser, workspace_id=ws,
                          plan=_plan(field_filters=({"field": "interest_rate", "op": "gt", "value": 9},)),
                          schema=schema)
    pred, decision = await scope_state_machine(
        db_superuser, workspace_id=ws, query="loans over 9%",
        inherited=None, fresh=fresh, schema=schema,
    )
    assert decision == "none" and pred.file_scope == frozenset({l1})


async def test_scope_machine_relax_loosens_bound(db_superuser):
    """§8.5 — 'over 9%' then 'make it 8%' returns the 8–9% docs too."""
    from kb.domain.structured_schema import clear_cache, live_schema
    from kb.query.structured_prefilter import resolve, scope_state_machine
    clear_cache()
    ws = str(uuid.uuid4())
    l1, l2, l3 = await _seed_loans(db_superuser, ws)
    schema = await live_schema(db_superuser, workspace_id=ws)
    inherited = await resolve(db_superuser, workspace_id=ws,
                              plan=_plan(field_filters=({"field": "interest_rate", "op": "gt", "value": 9},)),
                              schema=schema)
    assert inherited.file_scope == frozenset({l1})        # only 9.5 > 9
    # Follow-up "make it 8% not 9%" — fresh plan has no field (the user didn't
    # repeat 'interest rate'); the machine re-resolves the carried clause @ 8.
    fresh = await resolve(db_superuser, workspace_id=ws, plan=_plan(), schema=schema)
    pred, decision = await scope_state_machine(
        db_superuser, workspace_id=ws, query="make it 8% not 9%",
        inherited=inherited, fresh=fresh, schema=schema,
    )
    assert decision == "relax"
    assert pred.file_scope == frozenset({l1, l2})          # 9.5 and 8.5 both > 8


async def test_scope_machine_relax_uses_original_phrasing(db_superuser):
    """The context resolver can launder 'make it 9%' (anaphoric) into a
    resolved query without the relax cue — relax must still fire off the
    ORIGINAL phrasing, not become an INTERSECT."""
    from kb.domain.structured_schema import clear_cache, live_schema
    from kb.query.structured_prefilter import resolve, scope_state_machine
    clear_cache()
    ws = str(uuid.uuid4())
    l1, l2, l3 = await _seed_loans(db_superuser, ws)
    schema = await live_schema(db_superuser, workspace_id=ws)
    inherited = await resolve(db_superuser, workspace_id=ws,
                              plan=_plan(field_filters=({"field": "interest_rate", "op": "gt", "value": 9},)),
                              schema=schema)
    # effective_query was rewritten by the resolver and LOST the relax cue;
    # only the original retains "make it 8%". fresh = ALL (no new clause).
    fresh = await resolve(db_superuser, workspace_id=ws, plan=_plan(), schema=schema)
    pred, decision = await scope_state_machine(
        db_superuser, workspace_id=ws,
        query="loans interest rate",                    # laundered (no cue)
        original_query="make it 8% instead of 9%",      # raw user phrasing
        inherited=inherited, fresh=fresh, schema=schema, refinement=True,
    )
    assert decision == "relax"
    assert pred.file_scope == frozenset({l1, l2})        # broadened to 8–9% too


async def test_scope_machine_clear_on_domain_noun(db_superuser):
    """§8.6 — 'across all loans …' clears the carried scope."""
    from kb.domain.structured_schema import clear_cache, live_schema
    from kb.query.structured_prefilter import resolve, scope_state_machine
    clear_cache()
    ws = str(uuid.uuid4())
    l1, l2, l3 = await _seed_loans(db_superuser, ws)
    schema = await live_schema(db_superuser, workspace_id=ws)
    inherited = await resolve(db_superuser, workspace_id=ws,
                              plan=_plan(field_filters=({"field": "interest_rate", "op": "gt", "value": 9},)),
                              schema=schema)
    fresh = await resolve(db_superuser, workspace_id=ws, plan=_plan(), schema=schema)
    pred, decision = await scope_state_machine(
        db_superuser, workspace_id=ws, query="across all loans what is the average rate",
        inherited=inherited, fresh=fresh, schema=schema,
    )
    assert decision == "clear" and pred.is_all


async def test_scope_machine_aggregate_defaults_to_all(db_superuser):
    from kb.domain.structured_schema import clear_cache, live_schema
    from kb.query.structured_prefilter import resolve, scope_state_machine
    clear_cache()
    ws = str(uuid.uuid4())
    l1, l2, l3 = await _seed_loans(db_superuser, ws)
    schema = await live_schema(db_superuser, workspace_id=ws)
    inherited = await resolve(db_superuser, workspace_id=ws,
                              plan=_plan(field_filters=({"field": "interest_rate", "op": "gt", "value": 9},)),
                              schema=schema)
    fresh = await resolve(db_superuser, workspace_id=ws, plan=_plan(), schema=schema)
    pred, decision = await scope_state_machine(
        db_superuser, workspace_id=ws, query="what is the average interest rate",
        inherited=inherited, fresh=fresh, schema=schema, is_aggregate=True,
    )
    assert decision == "clear" and pred.is_all


async def test_scope_machine_intersect_on_refinement(db_superuser):
    from kb.domain.structured_schema import clear_cache, live_schema
    from kb.query.structured_prefilter import resolve, scope_state_machine
    clear_cache()
    ws = str(uuid.uuid4())
    l1, l2, l3 = await _seed_loans(db_superuser, ws)
    # Add non-loan docs so the 'loan' doc_type scope is a genuine narrowing
    # (3/5), not 100% of the corpus (which reads as over-broad → ALL).
    await _seed_doc(db_superuser, ws, doc_type="invoice", fields={"total": 1}, label="inv1")
    await _seed_doc(db_superuser, ws, doc_type="invoice", fields={"total": 2}, label="inv2")
    from kb.domain.structured_schema import clear_cache as _cc
    _cc()
    schema = await live_schema(db_superuser, workspace_id=ws)
    # prior focus = all loans (doctype); refine to those with rate > 9.
    inherited = await resolve(db_superuser, workspace_id=ws,
                              plan=_plan(doc_types=("loan",)), schema=schema)
    fresh = await resolve(db_superuser, workspace_id=ws,
                          plan=_plan(field_filters=({"field": "interest_rate", "op": "gt", "value": 9},)),
                          schema=schema)
    pred, decision = await scope_state_machine(
        db_superuser, workspace_id=ws, query="of those, which have a rate over 9",
        inherited=inherited, fresh=fresh, schema=schema, refinement=True,
    )
    assert decision == "intersect"
    assert pred.file_scope == frozenset({l1})


async def test_scope_machine_intersect_empty_widens_to_new_clause(db_superuser):
    from kb.domain.structured_schema import clear_cache, live_schema
    from kb.query.structured_prefilter import resolve, scope_state_machine
    clear_cache()
    ws = str(uuid.uuid4())
    l1, l2, l3 = await _seed_loans(db_superuser, ws)
    schema = await live_schema(db_superuser, workspace_id=ws)
    inherited = await resolve(db_superuser, workspace_id=ws,
                              plan=_plan(field_filters=({"field": "interest_rate", "op": "lt", "value": 7.5},)),
                              schema=schema)
    assert inherited.file_scope == frozenset({l3})         # only 7.0 < 7.5
    fresh = await resolve(db_superuser, workspace_id=ws,
                          plan=_plan(field_filters=({"field": "interest_rate", "op": "gt", "value": 9},)),
                          schema=schema)  # {l1} — disjoint from {l3}
    pred, decision = await scope_state_machine(
        db_superuser, workspace_id=ws, query="of those, the ones over 9",
        inherited=inherited, fresh=fresh, schema=schema, refinement=True,
    )
    assert decision == "intersect_empty"
    assert pred.file_scope == frozenset({l1})              # new clause, across all
    assert "intersect_empty_widened_to_all" in pred.notes


async def test_scope_machine_replace_on_new_predicate(db_superuser):
    from kb.domain.structured_schema import clear_cache, live_schema
    from kb.query.structured_prefilter import resolve, scope_state_machine
    clear_cache()
    ws = str(uuid.uuid4())
    l1, l2, l3 = await _seed_loans(db_superuser, ws)
    schema = await live_schema(db_superuser, workspace_id=ws)
    inherited = await resolve(db_superuser, workspace_id=ws,
                              plan=_plan(field_filters=({"field": "interest_rate", "op": "gt", "value": 9},)),
                              schema=schema)
    fresh = await resolve(db_superuser, workspace_id=ws,
                          plan=_plan(field_filters=({"field": "interest_rate", "op": "gt", "value": 8},)),
                          schema=schema)
    pred, decision = await scope_state_machine(
        db_superuser, workspace_id=ws, query="which loans have a rate over 8",
        inherited=inherited, fresh=fresh, schema=schema, refinement=False,
    )
    assert decision == "replace"
    assert pred.file_scope == frozenset({l1, l2})


async def test_scope_machine_inherit_on_anaphoric_followup(db_superuser):
    from kb.domain.structured_schema import clear_cache, live_schema
    from kb.query.structured_prefilter import resolve, scope_state_machine
    clear_cache()
    ws = str(uuid.uuid4())
    l1, l2, l3 = await _seed_loans(db_superuser, ws)
    schema = await live_schema(db_superuser, workspace_id=ws)
    inherited = await resolve(db_superuser, workspace_id=ws,
                              plan=_plan(field_filters=({"field": "interest_rate", "op": "gt", "value": 9},)),
                              schema=schema)
    fresh = await resolve(db_superuser, workspace_id=ws, plan=_plan(), schema=schema)  # ALL
    pred, decision = await scope_state_machine(
        db_superuser, workspace_id=ws, query="summarize them",
        inherited=inherited, fresh=fresh, schema=schema, refinement=False,
    )
    assert decision == "inherit"
    assert pred.file_scope == frozenset({l1})
