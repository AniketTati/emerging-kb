"""T2 Phase D — answer_mode, trust gate, and the S5a/S5c structured answers.

  - provisional_answer_mode / revise_answer_mode (pure).
  - trust_gate (pure): hard scope answers direct; entity-dependent never does;
    low coverage can't assert "none".
  - build_list_answer (S5c): enumerates the doc set + field values, each cited.
  - build_existence_answer (S5a): yes / confident-no / hedge (never a bare No).
  - build_lookup_answer (S5a): P2 source-confirm — confirmed value answers
    direct; an unconfirmable value returns None (→ RAG) — the coverage≠correctness
    guard (§8.3).
"""

from __future__ import annotations

import json
import uuid

import pytest

from kb.query.structured_prefilter import Clause, ResolvedPredicate

pytestmark = pytest.mark.asyncio


def test_predicate_label_is_human_readable():
    """Raw enum operators + unit_type spelling-variant surfaces must NOT leak
    into a user-facing answer (the "amount gt 50000000; transactionlisting,…"
    bug). Sync/pure — no DB."""
    from kb.query.structured_answer import _predicate_label
    p = ResolvedPredicate(clauses=(
        Clause(kind="field", canonical_key="amount", op="gt", value=50000000),
        Clause(kind="unit",
               surface="transactionlisting,transaction_listing,major_transaction"),
    ))
    label = _predicate_label(p)
    assert label == "amount > 50,000,000; transaction listing"
    assert "gt" not in label and "transactionlisting" not in label


# ---- seed helpers ----------------------------------------------------------


async def _ensure_se(conn, ws):
    cur = await conn.execute(
        "INSERT INTO schemas (workspace_id, name, lifecycle_state) "
        "VALUES (%s, %s, 'active') ON CONFLICT DO NOTHING RETURNING id",
        (ws, "auto:t2d:test"),
    )
    row = await cur.fetchone()
    if row is None:
        cur = await conn.execute(
            "SELECT id FROM schemas WHERE workspace_id = %s AND name = %s",
            (ws, "auto:t2d:test"),
        )
        row = await cur.fetchone()
    schema_id = row[0]
    cur = await conn.execute(
        "SELECT id FROM schema_entities WHERE workspace_id = %s AND schema_id = %s "
        "AND kind = 'doc_root' LIMIT 1", (ws, schema_id),
    )
    row = await cur.fetchone()
    if row:
        return str(row[0])
    cur = await conn.execute(
        "INSERT INTO schema_entities (schema_id, workspace_id, name, lifecycle_state, kind) "
        "VALUES (%s, %s, 'DocRoot', 'active', 'doc_root') RETURNING id", (schema_id, ws),
    )
    return str((await cur.fetchone())[0])


async def _seed_doc_with_chunk(conn, ws, *, fields: dict, chunk_text: str,
                               label: str, cite_field: str | None = None,
                               doc_type: str = "invoice") -> str:
    """file → chunk → contextual_chunk(text) + a doc_root extracted_entities row
    whose citations map points `cite_field` at that chunk (for P2)."""
    fid = str(uuid.uuid4())
    sha = (uuid.uuid4().hex * 2)[:64]
    await conn.execute(
        "INSERT INTO files (id, workspace_id, name, content_sha, object_key, "
        "mime_type, size_bytes, lifecycle_state, inferred_doc_type) "
        "VALUES (%s, %s, %s, %s, %s, 'application/pdf', 100, 'ready', %s)",
        (fid, ws, f"{label}.pdf", sha, f"raw_files/{sha}", doc_type),
    )
    chunk_id = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO chunks (id, file_id, workspace_id, chunk_index, text, "
        "source_page_numbers, token_count, content_sha) "
        "VALUES (%s, %s, %s, 0, %s, %s, 5, %s)",
        (chunk_id, fid, ws, chunk_text, [1], (uuid.uuid4().hex * 2)[:64]),
    )
    cc_id = str(uuid.uuid4())
    await conn.execute(
        "INSERT INTO contextual_chunks (id, chunk_id, file_id, workspace_id, "
        "contextual_prefix, contextual_text, model_id, prefix_token_count, "
        "cache_creation_input_tokens, cache_read_input_tokens) "
        "VALUES (%s, %s, %s, %s, '', %s, 'identity', 0, 0, 0)",
        (cc_id, chunk_id, fid, ws, chunk_text),
    )
    citations = {cite_field: cc_id} if cite_field else {}
    se_id = await _ensure_se(conn, ws)
    await conn.execute(
        "INSERT INTO extracted_entities (schema_entity_id, file_id, workspace_id, "
        "fields, citations, model_id) VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, 'mock')",
        (se_id, fid, ws, json.dumps(fields), json.dumps(citations)),
    )
    return fid


def _field_pred(scope, *, key="total", coverage=1.0, conf=0.95, file_ids=None, total=4):
    return ResolvedPredicate(
        clauses=(Clause(kind="field", canonical_key=key, op="gt", value=1000,
                        coverage=coverage, confidence=conf,
                        file_ids=tuple(file_ids if file_ids is not None
                                       else (sorted(scope) if scope else ()))),),
        file_scope=(None if scope is None else frozenset(scope)),
        overall_confidence=(conf if scope else 0.0),
        total_docs=total,
    )


async def _set_ws(conn, ws):
    await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (ws,))


# ===========================================================================
# answer_mode + trust gate (pure)
# ===========================================================================


async def test_revise_answer_mode():
    from kb.query.structured_answer import revise_answer_mode
    # LOOKUP across >1 doc → LIST
    p_multi = _field_pred({"a", "b"})
    assert revise_answer_mode("LOOKUP", p_multi)[0] == "LIST"
    # LOOKUP single doc stays LOOKUP
    p_one = _field_pred({"a"})
    assert revise_answer_mode("LOOKUP", p_one)[0] == "LOOKUP"
    # AGGREGATE depending on an entity clause → LIST
    p_ent = ResolvedPredicate(
        clauses=(Clause(kind="entity", confidence=0.7, file_ids=("a",)),),
        file_scope=frozenset({"a"}), overall_confidence=0.7, total_docs=4)
    assert revise_answer_mode("AGGREGATE", p_ent)[0] == "LIST"


async def test_trust_gate_hard_entity_lowcov():
    from kb.query.structured_answer import trust_gate
    # hard field scope → answer-direct + can assert none (cov complete)
    tg = trust_gate(_field_pred({"a"}, coverage=1.0, conf=0.95))
    assert tg.can_answer_direct and tg.can_assert_none
    # entity-dependent → never answer-direct, never confident-none
    p_ent = ResolvedPredicate(
        clauses=(Clause(kind="entity", coverage=0.6, confidence=0.7, file_ids=("a",)),),
        file_scope=frozenset({"a"}), overall_confidence=0.6, total_docs=4)
    tge = trust_gate(p_ent)
    assert not tge.can_answer_direct and tge.entity_dependent and not tge.can_assert_none
    # low coverage field → can't assert none
    tgl = trust_gate(_field_pred({"a"}, coverage=0.5, conf=0.6))
    assert not tgl.can_assert_none


# ===========================================================================
# S5c LIST
# ===========================================================================


async def test_build_list_answer_enumerates_with_values(db_superuser):
    from kb.query.structured_answer import build_list_answer
    ws = str(uuid.uuid4())
    await _set_ws(db_superuser, ws)
    a = await _seed_doc_with_chunk(db_superuser, ws, fields={"total": 5000},
                                   chunk_text="t", label="A")
    b = await _seed_doc_with_chunk(db_superuser, ws, fields={"total": 9000},
                                   chunk_text="t", label="B")
    pred = _field_pred({a, b}, key="total")
    ans = await build_list_answer(db_superuser, workspace_id=ws, predicate=pred, query="show all")
    assert ans is not None
    assert "Found 2 document(s)" in ans.answer
    assert "A.pdf" in ans.answer and "B.pdf" in ans.answer
    assert "5000" in ans.answer and "9000" in ans.answer
    assert len(ans.citations) == 2


async def test_build_list_answer_none_when_unscoped(db_superuser):
    from kb.query.structured_answer import build_list_answer
    ws = str(uuid.uuid4())
    await _set_ws(db_superuser, ws)
    pred = ResolvedPredicate(file_scope=None)  # ALL
    ans = await build_list_answer(db_superuser, workspace_id=ws, predicate=pred, query="x")
    assert ans is None


# ===========================================================================
# S5a EXISTENCE
# ===========================================================================


async def test_existence_yes(db_superuser):
    from kb.query.structured_answer import build_existence_answer
    ws = str(uuid.uuid4())
    await _set_ws(db_superuser, ws)
    a = await _seed_doc_with_chunk(db_superuser, ws, fields={"total": 5000},
                                   chunk_text="t", label="A")
    pred = _field_pred({a}, key="total")
    ans = await build_existence_answer(db_superuser, workspace_id=ws, predicate=pred, query="is there")
    assert ans is not None and ans.answer.startswith("Yes")


async def test_existence_confident_no_on_high_coverage(db_superuser):
    from kb.query.structured_answer import build_existence_answer
    ws = str(uuid.uuid4())
    await _set_ws(db_superuser, ws)
    # field present on all docs (coverage 1.0), but ZERO match the predicate →
    # AND-salvage collapses scope to ALL; the clause still carries the signal.
    pred = ResolvedPredicate(
        clauses=(Clause(kind="field", canonical_key="amount", op="gt", value=1e9,
                        coverage=1.0, confidence=0.95, file_ids=()),),
        file_scope=None, overall_confidence=0.0, total_docs=10)
    ans = await build_existence_answer(db_superuser, workspace_id=ws, predicate=pred, query="any over 1B")
    assert ans is not None and ans.answer.startswith("No")
    assert ans.notes == "confident_none"


async def test_existence_hedges_on_entity_miss(db_superuser):
    """§8.10 — an entity-link miss (not a field) must NEVER assert 'no'."""
    from kb.query.structured_answer import build_existence_answer
    ws = str(uuid.uuid4())
    await _set_ws(db_superuser, ws)
    pred = ResolvedPredicate(
        clauses=(Clause(kind="entity", surface="Globex", coverage=0.0,
                        confidence=0.0, file_ids=()),),
        file_scope=None, overall_confidence=0.0, total_docs=10)
    ans = await build_existence_answer(db_superuser, workspace_id=ws, predicate=pred, query="is Globex here")
    assert ans is None        # hedge → RAG, never a bare "No"


# ===========================================================================
# S5a LOOKUP + P2
# ===========================================================================


async def test_lookup_p2_confirmed_answers_direct(db_superuser):
    from kb.query.structured_answer import build_lookup_answer
    ws = str(uuid.uuid4())
    await _set_ws(db_superuser, ws)
    a = await _seed_doc_with_chunk(
        db_superuser, ws, fields={"interest_rate": 9.5},
        chunk_text="The loan carries an interest rate of 9.5% per annum.",
        label="loanA", cite_field="interest_rate",
    )
    pred = _field_pred({a}, key="interest_rate")
    ans = await build_lookup_answer(db_superuser, workspace_id=ws, predicate=pred, query="rate?")
    assert ans is not None and ans.p2_confirmed
    assert "9.5" in ans.answer


async def test_lookup_p2_unconfirmed_degrades_to_rag(db_superuser):
    """§8.3 — coverage≠correctness: a value not confirmable in its source chunk
    is NOT answered direct (returns None → RAG)."""
    from kb.query.structured_answer import build_lookup_answer
    ws = str(uuid.uuid4())
    await _set_ws(db_superuser, ws)
    a = await _seed_doc_with_chunk(
        db_superuser, ws, fields={"interest_rate": 9.5},
        chunk_text="This chunk is about something else entirely — widgets.",
        label="loanB", cite_field="interest_rate",
    )
    pred = _field_pred({a}, key="interest_rate")
    ans = await build_lookup_answer(db_superuser, workspace_id=ws, predicate=pred, query="rate?")
    assert ans is None        # P2 failed → degrade to RAG


async def test_locator_gate_present_vs_absent(db_superuser):
    """§6.13 — a locator present in the docs proceeds; an absent one is flagged."""
    from kb.query.structured_answer import locator_exists, parse_locator
    ws = str(uuid.uuid4())
    await _set_ws(db_superuser, ws)
    await _seed_doc_with_chunk(
        db_superuser, ws, fields={"x": 1},
        chunk_text="Clause 12 sets out the indemnification cap of $25M.",
        label="contractA", doc_type="contract",
    )
    assert parse_locator("what does clause 12 say") == ("clause", "12")
    # present → exists (proceed)
    assert await locator_exists(
        db_superuser, workspace_id=ws, scope=None, locator=("clause", "12")) is True
    # absent → does not exist (→ redirect)
    assert await locator_exists(
        db_superuser, workspace_id=ws, scope=None, locator=("clause", "99")) is False


async def test_try_structured_answer_dispatch(db_superuser):
    from kb.query.structured_answer import try_structured_answer
    ws = str(uuid.uuid4())
    await _set_ws(db_superuser, ws)
    a = await _seed_doc_with_chunk(db_superuser, ws, fields={"total": 5000},
                                   chunk_text="t", label="A")
    b = await _seed_doc_with_chunk(db_superuser, ws, fields={"total": 9000},
                                   chunk_text="t", label="B")
    pred = _field_pred({a, b}, key="total")
    # LIST on a hard scope → structured list
    ans = await try_structured_answer(db_superuser, workspace_id=ws, query="show all",
                                      predicate=pred, answer_mode="LIST")
    assert ans is not None and ans.source == "list"
    # NARRATIVE → always None (→ RAG)
    assert await try_structured_answer(db_superuser, workspace_id=ws, query="x",
                                       predicate=pred, answer_mode="NARRATIVE") is None
    # LIST on a SOFT (low-confidence) scope → None (P1: soft = boost, not direct)
    soft = _field_pred({a, b}, key="total", conf=0.5)
    assert await try_structured_answer(db_superuser, workspace_id=ws, query="show all",
                                       predicate=soft, answer_mode="LIST") is None
