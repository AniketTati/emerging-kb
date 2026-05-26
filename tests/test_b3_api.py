"""B3 / WA-7 + WA-8 — HTTP + integration tests over testcontainers.

Covers:
  - Migration shape: query_log new columns (faithfulness_*, citation_modalities)
    + CHECK constraint on verdict + supporting indexes
  - kb.query.citations.fetch_file_metas: batch read of authority + doc_status
    + chain_id off the DB
  - Orchestrator integration: chat() now returns faithfulness_verdict, score,
    regenerations, citation_modalities; enrich_citations populates modality
    + authority on each Citation
  - HTTP endpoint: POST /chat returns the enriched envelope; query_log row
    persists faithfulness verdict + modalities
  - Regression: GET /triples (B1) + GET /conflicts (B2) + POST /search still
    return 200
"""

from __future__ import annotations

import hashlib
import os
import uuid
from contextlib import contextmanager
from unittest.mock import patch

import psycopg
import pytest

from kb.api.query import reset_orchestrator
from kb.config import get_settings
from kb.query.citations import (
    FileMetaForCitation,
    build_citation,
    fetch_file_metas,
)
from kb.query.rrf import Hit


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
    db_url: str,
    workspace: str,
    *,
    name: str = "doc.pdf",
    mime_type: str = "application/pdf",
    source_authority: float = 0.5,
    doc_status: str = "live",
    inferred_doc_type: str | None = None,
) -> str:
    file_id = str(uuid.uuid4())
    sha = hashlib.sha256(f"{workspace}-{file_id}".encode()).hexdigest()
    cols = [
        "id", "workspace_id", "name", "content_sha", "object_key",
        "mime_type", "size_bytes", "lifecycle_state",
        "source_authority", "doc_status",
    ]
    vals: list = [
        file_id, workspace, name, sha, f"raw/{file_id}",
        mime_type, 100, "ready", source_authority, doc_status,
    ]
    if inferred_doc_type is not None:
        cols.append("inferred_doc_type")
        vals.append(inferred_doc_type)
    placeholders = ", ".join(["%s"] * len(vals))
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        await conn.execute(
            f"INSERT INTO files ({', '.join(cols)}) VALUES ({placeholders})",
            tuple(vals),
        )
    return file_id


# ===========================================================================
# Migration shape
# ===========================================================================


async def test_query_log_has_b3_columns(db_url_superuser):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'query_log' AND column_name IN "
            "('faithfulness_score', 'faithfulness_verdict', "
            "'faithfulness_regenerations', 'citation_modalities')"
        )
        rows = {r[0]: (r[1], r[2]) for r in await cur.fetchall()}

    assert "faithfulness_score" in rows
    assert rows["faithfulness_score"][0] == "double precision"
    assert rows["faithfulness_score"][1] == "YES"

    assert "faithfulness_verdict" in rows
    assert rows["faithfulness_verdict"][0] == "text"
    assert rows["faithfulness_verdict"][1] == "YES"

    assert "faithfulness_regenerations" in rows
    assert rows["faithfulness_regenerations"][0] == "integer"
    assert rows["faithfulness_regenerations"][1] == "NO"

    assert "citation_modalities" in rows
    assert rows["citation_modalities"][0] == "ARRAY"
    assert rows["citation_modalities"][1] == "YES"


async def test_query_log_faithfulness_verdict_check(db_url_superuser, test_workspace):
    """Only the 4 documented verdicts are accepted (plus NULL)."""
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        # Valid: insert a baseline row.
        qid = str(uuid.uuid4())
        await conn.execute(
            "INSERT INTO query_log (id, workspace_id, query, endpoint, "
            "faithfulness_verdict) VALUES (%s, %s, %s, %s, %s)",
            (qid, test_workspace, "q", "chat", "pass"),
        )
        # Invalid verdict → CHECK violation.
        with pytest.raises(Exception):
            await conn.execute(
                "INSERT INTO query_log (id, workspace_id, query, endpoint, "
                "faithfulness_verdict) VALUES (%s, %s, %s, %s, %s)",
                (str(uuid.uuid4()), test_workspace, "q", "chat", "bogus"),
            )


async def test_query_log_faithfulness_regenerations_range(
    db_url_superuser, test_workspace,
):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute(
            "SELECT set_config('app.workspace_id', %s, true)", (test_workspace,),
        )
        with pytest.raises(Exception):
            await conn.execute(
                "INSERT INTO query_log (id, workspace_id, query, endpoint, "
                "faithfulness_regenerations) VALUES (%s, %s, %s, %s, %s)",
                (str(uuid.uuid4()), test_workspace, "q", "chat", -1),
            )


async def test_query_log_modalities_gin_index_exists(db_url_superuser):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'query_log' "
            "AND indexname = 'query_log_modalities_gin_idx'"
        )
        row = await cur.fetchone()
        assert row is not None


# ===========================================================================
# fetch_file_metas — batch DB enrichment
# ===========================================================================


async def test_fetch_file_metas_pulls_authority_doc_status_chain(
    db_url_superuser, test_workspace,
):
    file_id = await _seed_file(
        db_url_superuser, test_workspace,
        source_authority=0.85, doc_status="superseded",
        inferred_doc_type="contract", name="agreement.pdf",
    )
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        metas = await fetch_file_metas(conn, file_ids=[file_id])

    assert file_id in metas
    m = metas[file_id]
    assert isinstance(m, FileMetaForCitation)
    assert m.source_authority == 0.85
    assert m.doc_status == "superseded"
    assert m.inferred_doc_type == "contract"
    assert m.name == "agreement.pdf"
    assert m.chain_id is None  # no chain seeded


async def test_fetch_file_metas_returns_empty_for_no_input(db_url_superuser):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        metas = await fetch_file_metas(conn, file_ids=[])
        assert metas == {}


async def test_fetch_file_metas_skips_unknown_files(db_url_superuser):
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        metas = await fetch_file_metas(
            conn, file_ids=[str(uuid.uuid4())],
        )
        assert metas == {}


async def test_build_citation_end_to_end_with_db_meta(
    db_url_superuser, test_workspace,
):
    """Round-trip: seed file, fetch_file_metas, build_citation produces a
    polymorphic envelope with authority + doc_status carried through."""
    file_id = await _seed_file(
        db_url_superuser, test_workspace,
        source_authority=0.92, doc_status="live",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        name="vendors.xlsx",
    )
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        metas = await fetch_file_metas(conn, file_ids=[file_id])
    meta = metas[file_id]

    hit = Hit(
        id="chunk-1", kind="chunk", score=0.42, snippet="VEN-001 row",
        metadata={"file_id": file_id, "sheet_name": "Q2", "row_index": 482},
    )
    c = build_citation(hit, meta)
    assert c.modality == "xlsx_row"
    assert c.authority == 0.92
    assert c.doc_status == "live"
    assert c.ref["sheet"] == "Q2"
    assert c.ref["row_index"] == 482
    assert c.confidence == 1.0  # xlsx_row exact lookup


# ===========================================================================
# Orchestrator integration — chat() with the gate
# ===========================================================================


async def test_chat_endpoint_returns_faithfulness_envelope(
    client, test_workspace,
):
    """POST /chat returns the new faithfulness_verdict + citation_modalities
    fields in the envelope."""
    reset_orchestrator()
    with _env(KB_QUERY_LLM="identity", KB_FAITHFULNESS_GATE="identity"):
        reset_orchestrator()
        resp = await client.post(
            "/chat",
            headers=headers(test_workspace),
            json={"query": "what is the cap?", "mode": "H"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert "faithfulness_verdict" in body
    assert body["faithfulness_verdict"] in ("pass", "skipped")
    assert "faithfulness_score" in body
    assert "faithfulness_regenerations" in body
    assert body["faithfulness_regenerations"] == 0
    assert "citation_modalities" in body
    assert isinstance(body["citation_modalities"], list)


async def test_chat_endpoint_persists_to_query_log(
    client, test_workspace, db_url_superuser,
):
    """The new query_log columns get populated by /chat."""
    reset_orchestrator()
    with _env(KB_QUERY_LLM="identity", KB_FAITHFULNESS_GATE="identity"):
        reset_orchestrator()
        resp = await client.post(
            "/chat",
            headers=headers(test_workspace),
            json={"query": "test query", "mode": "H"},
        )
        assert resp.status_code == 200
        query_id = resp.json()["query_id"]

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT faithfulness_verdict, faithfulness_score, "
            "       faithfulness_regenerations, citation_modalities "
            "FROM query_log WHERE id = %s",
            (query_id,),
        )
        row = await cur.fetchone()
    assert row is not None
    verdict, score, regens, modalities = row
    assert verdict in ("pass", "skipped")
    assert regens == 0
    # Modalities may be NULL when there are zero hits (no citations) — both
    # cases are acceptable shape-wise.
    assert modalities is None or isinstance(modalities, list)


async def test_chat_endpoint_heuristic_gate_path(
    client, test_workspace,
):
    """Switching to heuristic gate is no-key + deterministic. The verdict
    will be 'refused' (no hits → no snippets → no overlap) but the envelope
    must still be valid."""
    reset_orchestrator()
    with _env(KB_QUERY_LLM="identity", KB_FAITHFULNESS_GATE="heuristic"):
        reset_orchestrator()
        resp = await client.post(
            "/chat",
            headers=headers(test_workspace),
            json={"query": "anything", "mode": "H"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["faithfulness_verdict"] in ("pass", "low_confidence", "refused", "skipped")


# ===========================================================================
# Citation enrichment via orchestrator (with seeded hit metadata)
# ===========================================================================


async def test_orchestrator_enriches_citations_with_file_metadata(
    client, test_workspace, db_url_superuser,
):
    """When the orchestrator's _enrich_citations runs after a generation
    that returned bare citations, each Citation gets modality + authority
    + doc_status populated from the DB."""
    # We exercise this directly via the orchestrator (not via /chat) so we
    # can hand-craft hits with seeded file_ids.
    from kb.query.generate import Citation, GenerationResult
    from kb.query.orchestrator import Orchestrator

    file_id = await _seed_file(
        db_url_superuser, test_workspace,
        source_authority=0.77, doc_status="live", name="contract.pdf",
    )
    hits = [Hit(
        id="chunk-1", kind="chunk", score=0.42, snippet="...",
        metadata={"file_id": file_id, "source_page_numbers": [3]},
    )]
    gen = GenerationResult(
        answer="some answer",
        citations=[Citation(
            hit_id="chunk-1", kind="chunk", file_id=file_id,
            snippet_preview="...", score=0.42,
        )],
        refused=False,
        model_id="identity",
    )

    orch = Orchestrator.make_default()
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await orch._enrich_citations(gen, hits, conn)

    enriched = gen.citations[0]
    assert enriched.modality == "pdf_span"
    assert enriched.authority == 0.77
    assert enriched.doc_status == "live"
    assert enriched.ref is not None
    assert enriched.ref["page"] == 3


async def test_orchestrator_resolves_truncated_hit_ids(
    client, test_workspace, db_url_superuser,
):
    """T-mode (multi-hop) Gemini answers sometimes shorten hit_ids to
    8-12 char prefixes (e.g. `7c84e24b` instead of the full UUID).
    Without prefix resolution the citation never enriches and R1
    superseded-tagging silently misses. This test fakes that shape
    and asserts the prefix gets canonicalised to the full hit id and
    the citation gets fully enriched.
    """
    from kb.query.generate import Citation, GenerationResult
    from kb.query.orchestrator import Orchestrator

    file_id = await _seed_file(
        db_url_superuser, test_workspace,
        source_authority=0.5, doc_status="live", name="contract.pdf",
    )
    full_hit_id = "7c84e24b-abcd-1234-5678-1234567890ab"
    hits = [Hit(
        id=full_hit_id, kind="chunk", score=0.42, snippet="...",
        metadata={"file_id": file_id, "source_page_numbers": [1]},
    )]
    # Citation emitted with the truncated prefix the LLM produced.
    gen = GenerationResult(
        answer="some answer",
        citations=[Citation(
            hit_id="7c84e24b", kind="chunk", file_id=None,
            snippet_preview="", score=0.0,
        )],
        refused=False,
        model_id="gemini",
    )

    orch = Orchestrator.make_default()
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await orch._enrich_citations(gen, hits, conn)

    enriched = gen.citations[0]
    # Prefix got expanded back to the full UUID
    assert enriched.hit_id == full_hit_id
    # Enrichment populated the polymorphic fields
    assert enriched.modality == "pdf_span"
    assert enriched.file_id == file_id
    assert (enriched.label or "").startswith("contract.pdf")


# ===========================================================================
# Faithfulness retry loop
# ===========================================================================


async def test_orchestrator_retries_then_abstains_when_gate_refuses():
    """A generator that always produces an unverifiable answer + a heuristic
    gate that always refuses → orchestrator regenerates MAX_REGENERATIONS
    times then sets refused=True with reason='faithfulness_gate_refused'."""
    from kb.query.crag import IdentityCragGate
    from kb.query.faithfulness import (
        FaithfulnessResult,
        MAX_REGENERATIONS,
    )
    from kb.query.generate import Citation, GenerationResult
    from kb.query.orchestrator import Orchestrator

    gen_calls = {"n": 0}

    class StubGenerator:
        async def generate(self, query, hits, *, force_refuse=False, conflict_context=None):
            gen_calls["n"] += 1
            return GenerationResult(
                answer="unsupported claim",
                citations=[Citation(
                    hit_id="h1", kind="chunk", file_id=None,
                    snippet_preview="", score=0.0,
                )],
                refused=False, model_id="stub",
            )

    class AlwaysRefuseGate:
        async def assess(self, answer, snippets, *, model_id_hint=""):
            return FaithfulnessResult(
                verdict="refused", score=0.0, model_id="always-refuse",
            )

    class StubRewriter:
        async def rewrite(self, q):
            from kb.query.rewriter import Rewrites
            return Rewrites(original=q, step_back=q, hyde=q, query2doc=q)

    class StubEmbedder:
        async def embed_batch(self, texts):
            class _E:
                vector = [0.0] * 8
            return [_E() for _ in texts]

    class StubReranker:
        async def rerank(self, q, hits, *, top_k):
            return hits

    async def stub_channels(conn, *, workspace_id, query, query_vec,
                            limit=20, bm25_query=None):
        return {}

    orch = Orchestrator(
        rewriter=StubRewriter(),
        embedder=StubEmbedder(),
        reranker=StubReranker(),
        crag=IdentityCragGate(),
        generator=StubGenerator(),
        faithfulness=AlwaysRefuseGate(),
        run_channels=stub_channels,
    )
    result = await orch.chat("q", workspace_id=str(uuid.uuid4()), conn=None)

    # Generator ran (1 + MAX_REGENERATIONS) times.
    assert gen_calls["n"] == 1 + MAX_REGENERATIONS
    assert result.faithfulness_regenerations == MAX_REGENERATIONS
    assert result.faithfulness_verdict == "refused"
    assert result.generation.refused is True
    assert result.generation.refusal_reason == "faithfulness_gate_refused"


async def test_orchestrator_does_not_retry_when_generator_already_refused():
    """When the generator returns refused=True (e.g. CRAG force-refuse), we
    don't keep regenerating."""
    from kb.query.crag import IdentityCragGate
    from kb.query.faithfulness import FaithfulnessResult
    from kb.query.generate import GenerationResult
    from kb.query.orchestrator import Orchestrator

    gen_calls = {"n": 0}

    class StubGenerator:
        async def generate(self, query, hits, *, force_refuse=False, conflict_context=None):
            gen_calls["n"] += 1
            return GenerationResult(
                answer="", citations=[], refused=True,
                refusal_reason="no_hits", model_id="stub",
            )

    class AlwaysRefuseGate:
        async def assess(self, answer, snippets, *, model_id_hint=""):
            return FaithfulnessResult(verdict="refused", score=0.0)

    class StubRewriter:
        async def rewrite(self, q):
            from kb.query.rewriter import Rewrites
            return Rewrites(original=q, step_back=q, hyde=q, query2doc=q)

    class StubEmbedder:
        async def embed_batch(self, texts):
            class _E:
                vector = [0.0] * 8
            return [_E() for _ in texts]

    class StubReranker:
        async def rerank(self, q, hits, *, top_k):
            return hits

    async def stub_channels(conn, *, workspace_id, query, query_vec,
                            limit=20, bm25_query=None):
        return {}

    orch = Orchestrator(
        rewriter=StubRewriter(),
        embedder=StubEmbedder(),
        reranker=StubReranker(),
        crag=IdentityCragGate(),
        generator=StubGenerator(),
        faithfulness=AlwaysRefuseGate(),
        run_channels=stub_channels,
    )
    result = await orch.chat("q", workspace_id=str(uuid.uuid4()), conn=None)

    # Generator ran exactly once — no retry on upstream refusal.
    assert gen_calls["n"] == 1
    assert result.faithfulness_regenerations == 0
    # Gate said 'skipped' (generator refused → no answer to check).
    assert result.faithfulness_verdict == "skipped"


# ===========================================================================
# Regression — prior endpoints still work
# ===========================================================================


async def test_b1_triples_endpoint_regression(client, test_workspace):
    resp = await client.get("/triples", headers=headers(test_workspace))
    assert resp.status_code == 200


async def test_b2_conflicts_endpoint_regression(client, test_workspace):
    resp = await client.get("/conflicts", headers=headers(test_workspace))
    assert resp.status_code == 200


async def test_search_endpoint_regression(client, test_workspace):
    """POST /search still works (no gen → no faithfulness fields)."""
    reset_orchestrator()
    with _env(KB_QUERY_LLM="identity"):
        reset_orchestrator()
        resp = await client.post(
            "/search",
            headers=headers(test_workspace),
            json={"query": "anything", "mode": "H"},
        )
    assert resp.status_code == 200
    body = resp.json()
    # SearchResult has crag_score but no faithfulness fields.
    assert "crag_score" in body
    assert "faithfulness_verdict" not in body
