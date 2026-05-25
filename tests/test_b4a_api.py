"""B4a / WA-9 + WA-10 — HTTP + integration tests.

Covers:
  - Migration: query_log mode CHECK widened to 12 modes; intent +
    intent_confidence + plan columns + CHECK constraints + indexes
  - HTTP: POST /search and /chat accept all modes except Q (deferred);
    Q-mode returns 400 with "B4b" pointer
  - HTTP: /chat envelope includes intent / mode / plan; query_log row
    persists them; chat_result mode reflects planner override when
    request mode is 'H'
  - K-mode end-to-end: with seeded doc_chain + member files, /search?
    mode=K filters hits to the chain's current_version
  - T-mode: with seeded graph_edges + entities, T-mode boosts hits whose
    files mention PPR-connected entities
  - Q-mode orchestrator backstop: a hand-built Plan(mode='Q') returns the
    q_mode_refusal_envelope (refused=true, refusal_reason='q_mode_not_implemented')
  - Regression: B3 endpoints + prior search/chat tests still pass
"""

from __future__ import annotations

import hashlib
import os
import uuid
from contextlib import contextmanager

import psycopg
import pytest

from kb.api.query import reset_orchestrator
from kb.config import get_settings


pytestmark = pytest.mark.asyncio


@contextmanager
def _env(**kwargs):
    prior = {k: os.environ.get(k) for k in kwargs}
    for k, v in kwargs.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    get_settings.cache_clear()
    try:
        yield
    finally:
        for k, v in prior.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        get_settings.cache_clear()


@pytest.fixture
def test_workspace() -> str:
    return str(uuid.uuid4())


def headers(workspace: str) -> dict[str, str]:
    return {"X-Test-Workspace": workspace}


async def _seed_file(
    db_url: str, workspace: str, *,
    name: str = "doc.pdf",
    mime_type: str = "application/pdf",
) -> str:
    file_id = str(uuid.uuid4())
    sha = hashlib.sha256(f"{workspace}-{file_id}-{name}".encode()).hexdigest()
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        await conn.execute(
            "INSERT INTO files (id, workspace_id, name, content_sha, "
            "object_key, mime_type, size_bytes, lifecycle_state) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, 'ready')",
            (file_id, workspace, name, sha, f"raw/{file_id}", mime_type, 100),
        )
    return file_id


# ===========================================================================
# Migration shape
# ===========================================================================


async def test_query_log_has_b4a_columns(db_url_superuser):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'query_log' AND column_name IN "
            "('intent', 'intent_confidence', 'plan')"
        )
        rows = {r[0]: (r[1], r[2]) for r in await cur.fetchall()}

    assert "intent" in rows and rows["intent"][0] == "text"
    assert "intent_confidence" in rows and rows["intent_confidence"][0] == "double precision"
    assert "plan" in rows and rows["plan"][0] == "jsonb"


async def test_query_log_mode_check_admits_all_12(db_url_superuser, test_workspace):
    """Every spec mode is now insertable."""
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        for mode in ("E", "F", "S", "H", "T", "M", "G", "D", "C", "A", "Q", "K"):
            await conn.execute(
                "INSERT INTO query_log (id, workspace_id, query, mode, endpoint) "
                "VALUES (%s, %s, %s, %s, %s)",
                (str(uuid.uuid4()), test_workspace, "q", mode, "chat"),
            )

        # And an invalid mode still raises.
        with pytest.raises(Exception):
            await conn.execute(
                "INSERT INTO query_log (id, workspace_id, query, mode, endpoint) "
                "VALUES (%s, %s, %s, %s, %s)",
                (str(uuid.uuid4()), test_workspace, "q", "Z", "chat"),
            )


async def test_query_log_intent_confidence_range(db_url_superuser, test_workspace):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        with pytest.raises(Exception):
            await conn.execute(
                "INSERT INTO query_log (id, workspace_id, query, endpoint, "
                "intent_confidence) VALUES (%s, %s, %s, %s, %s)",
                (str(uuid.uuid4()), test_workspace, "q", "chat", 1.5),
            )


# ===========================================================================
# HTTP — mode acceptance
# ===========================================================================


async def test_search_accepts_K_mode(client, test_workspace):
    reset_orchestrator()
    with _env(KB_INTENT_CLASSIFIER="identity", KB_PLANNER="identity"):
        reset_orchestrator()
        resp = await client.post(
            "/search",
            headers=headers(test_workspace),
            json={"query": "what was the prior amendment", "mode": "K"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "K"
    assert body["intent"] in (
        "chain_aware", "temporal_history", "factoid", "vague",
    )


async def test_search_accepts_T_mode(client, test_workspace):
    reset_orchestrator()
    with _env(KB_INTENT_CLASSIFIER="identity", KB_PLANNER="identity"):
        reset_orchestrator()
        resp = await client.post(
            "/search",
            headers=headers(test_workspace),
            json={"query": "ACME Corp related to Vertex via Carrier", "mode": "T"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "T"


async def test_search_rejects_Q_until_b4b(client, test_workspace):
    """Q-mode is API-gated until B4b's SQL pipeline ships."""
    resp = await client.post(
        "/search",
        headers=headers(test_workspace),
        json={"query": "how many invoices", "mode": "Q"},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert "B4b" in body["detail"] or "B4b" in body["title"]


async def test_search_rejects_unknown_mode(client, test_workspace):
    resp = await client.post(
        "/search",
        headers=headers(test_workspace),
        json={"query": "q", "mode": "Z"},
    )
    assert resp.status_code == 400


async def test_search_default_H_mode_works(client, test_workspace):
    reset_orchestrator()
    with _env(KB_INTENT_CLASSIFIER="identity", KB_PLANNER="identity"):
        reset_orchestrator()
        resp = await client.post(
            "/search",
            headers=headers(test_workspace),
            json={"query": "test query"},   # mode defaults to H
        )
    assert resp.status_code == 200
    body = resp.json()
    # H is the explicit default; planner may override to a more specific
    # mode if it has high confidence — both are acceptable for this test.
    assert body["mode"] in (
        "E", "F", "S", "H", "T", "M", "G", "D", "C", "A", "K",
    )


# ===========================================================================
# HTTP — /chat envelope shape
# ===========================================================================


async def test_chat_envelope_includes_intent_and_plan(client, test_workspace):
    reset_orchestrator()
    with _env(
        KB_QUERY_LLM="identity",
        KB_FAITHFULNESS_GATE="identity",
        KB_INTENT_CLASSIFIER="identity",
        KB_PLANNER="identity",
    ):
        reset_orchestrator()
        resp = await client.post(
            "/chat",
            headers=headers(test_workspace),
            json={"query": "how many vendors did we pay", "mode": "H"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert "intent" in body
    assert body["intent"] == "aggregation"
    assert "intent_confidence" in body
    assert "mode" in body
    assert "plan" in body
    assert body["plan"]["intent"] == "aggregation"


async def test_chat_planner_can_override_H_default(client, test_workspace):
    """The planner upgrades 'H' to 'Q' on an aggregation query — Q then
    triggers the orchestrator's refusal envelope (until B4b)."""
    reset_orchestrator()
    with _env(
        KB_QUERY_LLM="identity",
        KB_FAITHFULNESS_GATE="identity",
        KB_INTENT_CLASSIFIER="identity",
        KB_PLANNER="identity",
    ):
        reset_orchestrator()
        resp = await client.post(
            "/chat",
            headers=headers(test_workspace),
            json={"query": "how many invoices total", "mode": "H"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "Q"
    # Q-mode backstop in orchestrator → refusal envelope.
    assert body["generation"]["refused"] is True
    assert body["generation"]["refusal_reason"] == "q_mode_not_implemented"


async def test_chat_persists_intent_and_plan_to_query_log(
    client, test_workspace, db_url_superuser,
):
    reset_orchestrator()
    with _env(
        KB_QUERY_LLM="identity",
        KB_FAITHFULNESS_GATE="identity",
        KB_INTENT_CLASSIFIER="identity",
        KB_PLANNER="identity",
    ):
        reset_orchestrator()
        resp = await client.post(
            "/chat",
            headers=headers(test_workspace),
            json={"query": "show me the chain of amendments", "mode": "H"},
        )
    body = resp.json()
    qid = body["query_id"]

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT mode, intent, intent_confidence, plan "
            "FROM query_log WHERE id = %s",
            (qid,),
        )
        row = await cur.fetchone()

    assert row is not None
    mode, intent, conf, plan = row
    assert mode == "K"  # chain_aware → K
    assert intent == "chain_aware"
    assert 0.0 <= conf <= 1.0
    assert plan is not None
    assert plan["mode"] == "K"


# ===========================================================================
# K-mode end-to-end
# ===========================================================================


async def test_k_mode_filters_hits_by_chain_current_version(
    client, test_workspace, db_url_superuser,
):
    """Seed a chain with 2 members where one is current_version. K-mode
    apply_mode + chain_view=current_version returns only the current."""
    from kb.query.mode_router import apply_mode
    from kb.query.planner import Plan
    from kb.query.rrf import Hit

    f_old = await _seed_file(db_url_superuser, test_workspace, name="contract_v1.pdf")
    f_cur = await _seed_file(db_url_superuser, test_workspace, name="contract_v2.pdf")
    chain_id = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        await conn.execute(
            "INSERT INTO doc_chains (id, workspace_id, type, title, "
            "current_version_id, detection_confidence) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (chain_id, test_workspace, "contract_chain", "Test", f_cur, 0.9),
        )
        await conn.execute(
            "INSERT INTO doc_chain_members (chain_id, doc_id, workspace_id, "
            "version_index, role) "
            "VALUES (%s, %s, %s, %s, %s), (%s, %s, %s, %s, %s)",
            (
                chain_id, f_old, test_workspace, 1, "original",
                chain_id, f_cur, test_workspace, 2, "amendment",
            ),
        )

    hits = [
        Hit(id="h1", kind="chunk", score=0.9, snippet="", metadata={"file_id": f_old}),
        Hit(id="h2", kind="chunk", score=0.8, snippet="", metadata={"file_id": f_cur}),
    ]
    plan = Plan(mode="K", chain_view="current_version")

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        out = await apply_mode(
            plan, hits,
            workspace_id=test_workspace, query="x", conn=conn,
        )

    # Only the current_version file's hit remains.
    assert len(out) == 1
    assert out[0].metadata["file_id"] == f_cur
    assert out[0].metadata["is_current_version"] is True
    assert out[0].metadata["chain_id"] == chain_id


async def test_k_mode_all_versions_keeps_both(
    client, test_workspace, db_url_superuser,
):
    from kb.query.mode_router import apply_mode
    from kb.query.planner import Plan
    from kb.query.rrf import Hit

    f_old = await _seed_file(db_url_superuser, test_workspace, name="v1.pdf")
    f_cur = await _seed_file(db_url_superuser, test_workspace, name="v2.pdf")
    chain_id = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        await conn.execute(
            "INSERT INTO doc_chains (id, workspace_id, type, "
            "current_version_id, detection_confidence) "
            "VALUES (%s, %s, %s, %s, %s)",
            (chain_id, test_workspace, "contract_chain", f_cur, 0.9),
        )
        await conn.execute(
            "INSERT INTO doc_chain_members (chain_id, doc_id, workspace_id, "
            "version_index, role) "
            "VALUES (%s, %s, %s, %s, %s), (%s, %s, %s, %s, %s)",
            (
                chain_id, f_old, test_workspace, 1, "original",
                chain_id, f_cur, test_workspace, 2, "amendment",
            ),
        )

    hits = [
        Hit(id="h1", kind="chunk", score=0.9, snippet="", metadata={"file_id": f_old}),
        Hit(id="h2", kind="chunk", score=0.8, snippet="", metadata={"file_id": f_cur}),
    ]
    plan = Plan(mode="K", chain_view="all_versions")
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        out = await apply_mode(
            plan, hits, workspace_id=test_workspace, query="x", conn=conn,
        )

    assert len(out) == 2


# ===========================================================================
# T-mode end-to-end (PPR boost)
# ===========================================================================


async def test_t_mode_boosts_files_with_ppr_connected_entities(
    client, test_workspace, db_url_superuser,
):
    """Seed 2 entities (Alpha, Beta) + a strong edge between them.
    Seed file f1 mentions Alpha. Query 'Alpha' → seeds={Alpha} → PPR
    surfaces Beta → file mentioning Beta gets boosted."""
    from kb.query.mode_router import apply_mode
    from kb.query.planner import Plan
    from kb.query.rrf import Hit

    e_alpha = str(uuid.uuid4())
    e_beta = str(uuid.uuid4())
    f_no = await _seed_file(db_url_superuser, test_workspace, name="unrelated.pdf")
    f_yes = await _seed_file(db_url_superuser, test_workspace, name="beta-doc.pdf")

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        # Entities
        for eid, name in [(e_alpha, "Alpha"), (e_beta, "Beta")]:
            await conn.execute(
                "INSERT INTO entities (id, workspace_id, canonical_name, "
                "entity_type, mention_count) VALUES (%s, %s, %s, 'ORG', 0)",
                (eid, test_workspace, name),
            )
        # Graph edge Alpha↔Beta
        await conn.execute(
            "INSERT INTO graph_edges (workspace_id, src_entity_id, "
            "dst_entity_id, edge_kind, weight) "
            "VALUES (%s, %s, %s, 'relationship', 5.0)",
            (test_workspace, e_alpha, e_beta),
        )
        # f_yes has a mention of Beta resolved to e_beta.
        mid = str(uuid.uuid4())
        # Need a chunk row first so extracted_mentions FK is satisfied.
        cur = await conn.execute(
            "INSERT INTO chunks (id, workspace_id, file_id, chunk_index, text, "
            "source_page_numbers, token_count, content_sha) "
            "VALUES (gen_random_uuid(), %s, %s, 0, %s, '{1}', 5, %s) "
            "RETURNING id::text",
            (
                test_workspace, f_yes, "Beta is mentioned here",
                hashlib.sha256(b"beta").hexdigest(),
            ),
        )
        chunk_id = (await cur.fetchone())[0]
        cur = await conn.execute(
            "INSERT INTO contextual_chunks (id, workspace_id, chunk_id, "
            "file_id, contextual_prefix, contextual_text, model_id, "
            "prefix_token_count) "
            "VALUES (gen_random_uuid(), %s, %s, %s, %s, %s, %s, %s) "
            "RETURNING id::text",
            (
                test_workspace, chunk_id, f_yes, "ctx", "ctx Beta",
                "identity", 1,
            ),
        )
        cchunk_id = (await cur.fetchone())[0]
        await conn.execute(
            "INSERT INTO extracted_mentions (id, workspace_id, file_id, "
            "contextual_chunk_id, mention_text, mention_type, "
            "start_offset, end_offset, confidence, model_id) "
            "VALUES (%s, %s, %s, %s, 'Beta', 'ORG', 0, 4, 0.9, 'identity')",
            (mid, test_workspace, f_yes, cchunk_id),
        )
        await conn.execute(
            "INSERT INTO mention_to_entity (mention_id, entity_id, "
            "workspace_id, confidence, resolved_method) "
            "VALUES (%s, %s, %s, 0.9, 'deterministic')",
            (mid, e_beta, test_workspace),
        )

    hits = [
        Hit(id="h1", kind="chunk", score=1.0, snippet="", metadata={"file_id": f_no}),
        Hit(id="h2", kind="chunk", score=1.0, snippet="", metadata={"file_id": f_yes}),
    ]
    plan = Plan(mode="T", seed_entities=(e_alpha,))
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        out = await apply_mode(
            plan, hits,
            workspace_id=test_workspace,
            query="Alpha",
            conn=conn,
        )

    # f_yes should be boosted to top; f_no scored unchanged.
    assert len(out) == 2
    top = out[0]
    assert top.metadata["file_id"] == f_yes
    assert top.metadata.get("ppr_boost") is True
    assert top.score > hits[1].score   # boosted above its original 1.0


# ===========================================================================
# Regression
# ===========================================================================


async def test_b1_endpoint_still_works(client, test_workspace):
    resp = await client.get("/triples", headers=headers(test_workspace))
    assert resp.status_code == 200


async def test_b2_endpoint_still_works(client, test_workspace):
    resp = await client.get("/conflicts", headers=headers(test_workspace))
    assert resp.status_code == 200


async def test_b3_search_still_works_with_intent_added(client, test_workspace):
    reset_orchestrator()
    with _env(KB_INTENT_CLASSIFIER="identity", KB_PLANNER="identity"):
        reset_orchestrator()
        resp = await client.post(
            "/search",
            headers=headers(test_workspace),
            json={"query": "anything", "mode": "H"},
        )
    assert resp.status_code == 200
    body = resp.json()
    # Old fields still present
    assert "hits" in body and "crag_score" in body and "rewrites" in body
    # New fields present + non-null on success
    assert body["intent"] is not None
    assert body["mode"] is not None
