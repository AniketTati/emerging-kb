"""T2 Phase D — end-to-end chat() integration with a real DB connection.

Drives the full chat() pipeline (predicate resolution → confidence-weighted
scope → structured answer-direct → scope surfacing) against seeded loan docs,
using lightweight fakes for the LLM-backed components and a controllable
planner. Persistence no-ops without KB_DATABASE_URL (the answer still returns),
so this validates the in-request wiring without a running worker.
"""

from __future__ import annotations

import json
import uuid

import pytest

from kb.embeddings import EmbeddingResult
from kb.query.generate import GenerationResult
from kb.query.orchestrator import Orchestrator
from kb.query.planner import Plan
from kb.query.rewriter import Rewrites
from kb.query.rrf import Hit

pytestmark = pytest.mark.asyncio


class _Rewriter:
    async def rewrite(self, q: str) -> Rewrites:
        return Rewrites(original=q, step_back="", hyde="", query2doc="")


class _Embedder:
    async def embed_batch(self, texts):
        return [EmbeddingResult(vector=[1.0, 0.0, 0.0], model_id="fake", dim=3)
                for _ in texts]


class _Reranker:
    async def rerank(self, query, hits, *, top_k):
        return hits[:top_k]


class _Crag:
    async def assess(self, query, hits):
        return 0.8


class _Generator:
    def __init__(self):
        self.called = False

    async def generate(self, query, hits, *, force_refuse=False, conflict_context=None):
        self.called = True
        return GenerationResult(answer="RAG fallback answer", citations=[],
                                refused=False, model_id="fake-gen")


class _Planner:
    """Returns a fixed Plan (so we control field_filters without an LLM)."""
    def __init__(self, plan: Plan):
        self._plan = plan

    async def plan(self, query, intent, *, requested_mode=None, conn=None, workspace_id=None):
        return self._plan


class _Intent:
    def __init__(self, label="field_filter"):
        self.label = label

    async def classify(self, query):
        from kb.query.intent import IntentResult
        return IntentResult(label=self.label, confidence=0.9, model_id="fake")


def _scope_aware_channels():
    async def _run(conn, *, workspace_id, query, query_vec, limit=20,
                   bm25_query=None, file_scope=None):
        # Echo the scope as fake hits so retrieval "works" under a hard scope.
        fids = sorted(file_scope) if file_scope else ["x"]
        hits = [Hit(id=f"c{fid}", kind="chunk", score=0.5, snippet="s",
                    metadata={"file_id": fid}) for fid in fids]
        return {k: list(hits) for k in (
            "bm25_chunks", "bm25_raptor", "dense_chunks", "dense_raptor",
            "mentions_exact", "sub_entities_rarity")}
    return _run


async def _ensure_se(conn, ws):
    cur = await conn.execute(
        "INSERT INTO schemas (workspace_id, name, lifecycle_state) "
        "VALUES (%s, %s, 'active') ON CONFLICT DO NOTHING RETURNING id",
        (ws, "auto:t2int"),
    )
    row = await cur.fetchone()
    if row is None:
        cur = await conn.execute(
            "SELECT id FROM schemas WHERE workspace_id=%s AND name=%s", (ws, "auto:t2int"))
        row = await cur.fetchone()
    cur = await conn.execute(
        "SELECT id FROM schema_entities WHERE workspace_id=%s AND schema_id=%s "
        "AND kind='doc_root' LIMIT 1", (ws, row[0]))
    r2 = await cur.fetchone()
    if r2:
        return str(r2[0])
    cur = await conn.execute(
        "INSERT INTO schema_entities (schema_id, workspace_id, name, lifecycle_state, kind) "
        "VALUES (%s, %s, 'DocRoot', 'active', 'doc_root') RETURNING id", (row[0], ws))
    return str((await cur.fetchone())[0])


async def _seed(conn, ws, *, doc_type, fields, label):
    fid = str(uuid.uuid4())
    sha = (uuid.uuid4().hex * 2)[:64]
    await conn.execute(
        "INSERT INTO files (id, workspace_id, name, content_sha, object_key, "
        "mime_type, size_bytes, lifecycle_state, inferred_doc_type) "
        "VALUES (%s,%s,%s,%s,%s,'application/pdf',100,'ready',%s)",
        (fid, ws, f"{label}.pdf", sha, f"raw_files/{sha}", doc_type))
    se = await _ensure_se(conn, ws)
    await conn.execute(
        "INSERT INTO extracted_entities (schema_entity_id, file_id, workspace_id, "
        "fields, citations, model_id) VALUES (%s,%s,%s,%s::jsonb,'{}'::jsonb,'mock')",
        (se, fid, ws, json.dumps(fields)))
    return fid


def _orch(plan: Plan):
    gen = _Generator()
    o = Orchestrator(
        rewriter=_Rewriter(), embedder=_Embedder(), reranker=_Reranker(),
        crag=_Crag(), generator=gen, intent_classifier=_Intent(),
        planner=_Planner(plan), run_channels=_scope_aware_channels(),
    )
    return o, gen


async def test_chat_structured_list_over_scope(db_superuser):
    """which loans over 9% → F/LIST → hard scope → structured doc list, surfaced."""
    ws = str(uuid.uuid4())
    await db_superuser.execute("SELECT set_config('app.workspace_id', %s, true)", (ws,))
    hi = await _seed(db_superuser, ws, doc_type="loan", fields={"interest_rate": 9.5}, label="loanHi")
    # ≥ MIN_DOCS_FOR_COVERAGE (3) loans so coverage is trusted → hard scope.
    await _seed(db_superuser, ws, doc_type="loan", fields={"interest_rate": 8.0}, label="loanLo1")
    await _seed(db_superuser, ws, doc_type="loan", fields={"interest_rate": 7.0}, label="loanLo2")
    # a few invoices so the corpus isn't all-loans (avoids over-broad noise)
    for i in range(2):
        await _seed(db_superuser, ws, doc_type="invoice", fields={"total": i}, label=f"inv{i}")

    plan = Plan(mode="F", intent="field_filter",
                field_filters=({"field": "interest_rate", "op": "gt", "value": 9},))
    orch, gen = _orch(plan)

    result = await orch.chat(
        "which loans have an interest rate over 9", workspace_id=ws,
        conn=db_superuser, session_id=None,
    )
    # Scope surfaced + answered from the structured layer (no RAG generator).
    assert result.scope is not None
    assert result.scope["answer_mode"] == "LIST"
    assert result.scope["answered_from_structured"] is True
    assert result.scope["scoped_doc_count"] == 1          # only loanHi (9.5 > 9)
    # hard scope; may auto-widen since the (fake) scoped retrieval is thin.
    assert result.scope["scope_mode"] in ("hard", "hard_widened")
    assert not result.generation.refused
    assert "loanHi.pdf" in result.generation.answer
    assert gen.called is False                            # structured path, not RAG


async def test_chat_unscoped_query_uses_rag(db_superuser):
    """A plain query with no resolvable predicate → unscoped RAG (no scope)."""
    ws = str(uuid.uuid4())
    await db_superuser.execute("SELECT set_config('app.workspace_id', %s, true)", (ws,))
    await _seed(db_superuser, ws, doc_type="loan", fields={"interest_rate": 9.5}, label="l1")
    plan = Plan(mode="H", intent="vague")     # no field_filters → ALL
    orch, gen = _orch(plan)
    result = await orch.chat("tell me about the portfolio", workspace_id=ws,
                             conn=db_superuser, session_id=None)
    assert gen.called is True                 # fell through to RAG
    assert result.generation.answer == "RAG fallback answer"
    # scope payload present but unscoped (ALL).
    assert result.scope is None or result.scope["scoped_doc_count"] == -1
