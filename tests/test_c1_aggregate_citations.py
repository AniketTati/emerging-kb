"""C1 — aggregate (mode-Q) answers must be cited to their source documents.

Mechanism tests for `_ensure_aggregate_sources_cited`: when an answer cites
the synthetic aggregate Hit (which has no source file), the retrieved
source-doc hits the orchestrator kept behind it are attached as citations
("cited or it didn't happen"). No-op for non-aggregate answers.
"""

from __future__ import annotations

from kb.query.generate import Citation, _ensure_aggregate_sources_cited
from kb.query.rrf import Hit


def _agg_hit() -> Hit:
    return Hit(id="audit-uuid", kind="aggregate", score=1.0,
              snippet="Result: 5", metadata={"aggregate": True})


def _src_hit(hid: str, file_id: str) -> Hit:
    return Hit(id=hid, kind="chunk", score=0.9, snippet="…",
               metadata={"file_id": file_id})


def _agg_citation() -> Citation:
    return Citation(hit_id="audit-uuid", kind="aggregate", file_id=None,
                    snippet_preview="Result: 5", score=1.0)


def test_attaches_source_docs_when_only_aggregate_cited():
    hits = [_agg_hit(), _src_hit("c1", "F1"), _src_hit("c2", "F2")]
    out = _ensure_aggregate_sources_cited([_agg_citation()], hits)
    file_ids = {c.file_id for c in out}
    assert "F1" in file_ids and "F2" in file_ids       # source docs attached
    assert any(c.kind == "aggregate" for c in out)      # aggregate kept


def test_noop_for_non_aggregate_answer():
    hits = [_src_hit("c1", "F1")]
    cits = [Citation(hit_id="c1", kind="chunk", file_id="F1",
                     snippet_preview="…", score=0.9)]
    out = _ensure_aggregate_sources_cited(cits, hits)
    assert out == cits                                  # unchanged


def test_does_not_duplicate_already_cited_source():
    hits = [_agg_hit(), _src_hit("c1", "F1")]
    cits = [_agg_citation(),
            Citation(hit_id="c1", kind="chunk", file_id="F1",
                     snippet_preview="…", score=0.9)]
    out = _ensure_aggregate_sources_cited(cits, hits)
    assert [c.hit_id for c in out].count("c1") == 1     # no dup


def test_skips_source_hits_without_file_id():
    # a hit lacking file_id can't be a citable document → skipped
    hits = [_agg_hit(), Hit(id="c1", kind="chunk", score=0.5, snippet="x",
                            metadata={})]
    out = _ensure_aggregate_sources_cited([_agg_citation()], hits)
    assert all(c.file_id != "" for c in out)
    assert len(out) == 1                                # only the aggregate


def test_respects_limit():
    hits = [_agg_hit()] + [_src_hit(f"c{i}", f"F{i}") for i in range(10)]
    out = _ensure_aggregate_sources_cited([_agg_citation()], hits, limit=3)
    # 1 aggregate + 3 source docs
    assert len(out) == 4
