"""Phase 3a (§6.9) — typed-relationship Knowledge-Graph query path.

Pure tests pin the predicate canonicalization + intent detection + answer
rendering; the DB-backed tests prove the typed traversal filters by predicate /
entity type, relaxes when the specific ask matches nothing, and degrades to None
(→ RAG) when no seed resolves.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from kb.query.kg_relations import (
    KgAnswer,
    KgEdge,
    build_kg_answer,
    canonicalize_predicate,
    detect_relation_intent,
    existence_verdict,
    extract_asserted_object,
    format_kg_snippet,
    is_existence_query,
)


# ---------------------------------------------------------------------------
# Predicate canonicalization (KG-2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("surface,canon", [
    ("located in", "located_in"),
    ("is located in", "located_in"),
    ("is in", "located_in"),
    ("is from", "located_in"),
    ("is a counterparty to", "counterparty"),  # NOT has_role (the "is" trap)
    ("is", "has_role"),                          # bare role assignment
    ("has subsidiary", "has_subsidiary"),
    ("regulated as", "regulated_by"),
    ("has account with", "has_account_with"),
    ("frobnicates widgets", "frobnicates_widgets"),  # unknown → normalized
])
def test_canonicalize_predicate(surface, canon):
    assert canonicalize_predicate(surface) == canon


def test_canonicalize_predicate_empty():
    assert canonicalize_predicate(None) == ""
    assert canonicalize_predicate("") == ""


# ---------------------------------------------------------------------------
# Relation-intent detection (KG-1)
# ---------------------------------------------------------------------------


def test_intent_counterparty_is_not_person_filtered():
    """'who are the counterparties' must NOT force a PERSON filter — counterparties
    are usually ORGs (the 'who'→PERSON trap)."""
    i = detect_relation_intent("who are the counterparties to Acme Corp")
    assert i.predicate == "counterparty"
    assert i.target_types == ()


def test_intent_explicit_type_and_predicate():
    i = detect_relation_intent("which people signed for HDFC")
    assert i.predicate == "signed_by"
    assert i.target_types == ("PERSON",)


def test_intent_location():
    i = detect_relation_intent("where is NorthWind located")
    assert i.predicate == "located_in"


# ---------------------------------------------------------------------------
# Answer rendering + envelope (KG-4)
# ---------------------------------------------------------------------------


def test_kg_answer_properties_and_snippet():
    edges = (
        KgEdge("Acme", "has account with", "HDFC BANK", "h1", "ORG", "out",
               0.95, 2, ("f1",)),
        KgEdge("Acme", "located in", "Gurugram", "g1", "GPE", "out",
               0.9, 1, ("f2",)),
    )
    ans = KgAnswer(seed_id="a1", seed_name="Acme", edges=edges,
                   notes=("1 of 2 relationships rest on a single source — "
                          "treat as lower-confidence (graph not yet verified)",))
    assert ans.n_edges == 2
    assert ans.n_single_evidence == 1
    assert ans.file_ids == ["f1", "f2"]
    snippet = format_kg_snippet(ans)
    assert "Acme" in snippet and "HDFC BANK" in snippet
    assert "has account with" in snippet
    assert "lower-confidence" in snippet  # §6.9 flag surfaced


# ---------------------------------------------------------------------------
# Negative-existence verdict (KG-6)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("q,expected", [
    ("does Acme have an account with ICICI", True),
    ("is there a loan with Kotak", True),
    ("are there any subsidiaries", True),
    ("the closing balance of the account", False),
    ("total revenue for 2024", False),
])
def test_is_existence_query(q, expected):
    assert is_existence_query(q) is expected


def _acme_account_answer():
    return KgAnswer(
        seed_id="acme", seed_name="Acme Corp",
        edges=(KgEdge("Acme Corp", "has account with", "HDFC BANK", "hdfc",
                      "ORG", "out", 0.95, 2, ("f1",)),),
        intent_predicate="has_account_with",
    )


def test_existence_verdict_soft_no_names_actual_counterpart():
    v = existence_verdict(_acme_account_answer(), {"icici"}, "ICICI Bank")
    assert v is not None
    assert "likely NO" in v
    assert "HDFC BANK" in v and "ICICI Bank" in v  # actual + asserted both shown


def test_existence_verdict_yes_when_present():
    v = existence_verdict(_acme_account_answer(), {"hdfc"}, "HDFC BANK")
    assert v is not None and v.startswith("Answer: YES")


def test_existence_verdict_none_without_asserted():
    assert existence_verdict(_acme_account_answer(), set(), "x") is None


@pytest.mark.parametrize("q,obj", [
    ("does Acme have an account with ICICI Bank", "ICICI Bank"),
    ("is there a loan with Kotak Mahindra", "Kotak Mahindra"),
    ("is NorthWind located in Singapore", "Singapore"),
    ("how many loans does Acme have", None),  # no trailing prepositional object
])
def test_extract_asserted_object(q, obj):
    assert extract_asserted_object(q) == obj


# ---------------------------------------------------------------------------
# DB-backed traversal (testcontainers)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_typed_traversal_filters_and_relaxes(db_url_superuser):
    ws = str(uuid.uuid4())

    async def _eid(conn, name, etype):
        cur = await conn.execute(
            "INSERT INTO canonical_entities (workspace_id, canonical_name, entity_type) "
            "VALUES (%s, %s, %s) RETURNING id::text",
            (ws, name, etype),
        )
        return (await cur.fetchone())[0]

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        async with conn.transaction():
            acme = await _eid(conn, "Acme Corp Pvt Ltd", "ORG")
            hdfc = await _eid(conn, "HDFC BANK", "ORG")
            guru = await _eid(conn, "Gurugram", "GPE")
            jay = await _eid(conn, "Jayanti Iyer", "PERSON")
            for subj, pred, obj, conf, nev in [
                (acme, "has account with", hdfc, 0.95, 2),
                (acme, "is located in", guru, 0.9, 1),
                (acme, "signed by", jay, 0.9, 1),
            ]:
                await conn.execute(
                    "INSERT INTO relationships (workspace_id, subject_entity_id, "
                    "object_entity_id, predicate, confidence, n_evidence) "
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    (ws, subj, obj, pred, conf, nev),
                )

        # No intent → all 3 typed relations for Acme.
        allrel = await build_kg_answer(
            conn, workspace_id=ws, seed_ids=[acme],
            intent=detect_relation_intent("what do we know about Acme"),
        )
        assert allrel is not None and allrel.n_edges == 3
        assert allrel.n_single_evidence == 2  # the two nev=1 edges

        # Predicate filter: 'located' → only Gurugram.
        loc = await build_kg_answer(
            conn, workspace_id=ws, seed_ids=[acme],
            intent=detect_relation_intent("where is Acme located"),
        )
        assert loc is not None and loc.n_edges == 1
        assert loc.edges[0].object == "Gurugram"

        # Type filter: 'which people signed' → only the PERSON (Jayanti).
        ppl = await build_kg_answer(
            conn, workspace_id=ws, seed_ids=[acme],
            intent=detect_relation_intent("which people signed for Acme"),
        )
        assert ppl is not None and ppl.n_edges == 1
        assert ppl.edges[0].neighbor_type == "PERSON"

        # Relax: a predicate that matches nothing → show ALL + a relax note.
        relaxed = await build_kg_answer(
            conn, workspace_id=ws, seed_ids=[acme],
            intent=detect_relation_intent("what is Acme regulated by"),
        )
        assert relaxed is not None and relaxed.n_edges == 3
        assert any("no relation matching" in n for n in relaxed.notes)

        # relax=False (opportunistic augmentation): a no-match predicate yields
        # None — never inject off-topic relations into a non-graph answer.
        strict = await build_kg_answer(
            conn, workspace_id=ws, seed_ids=[acme],
            intent=detect_relation_intent("what is Acme regulated by"),
            relax=False,
        )
        assert strict is None

        # No seed → None (caller degrades to RAG).
        assert await build_kg_answer(
            conn, workspace_id=ws, seed_ids=[], intent=detect_relation_intent("x"),
        ) is None
