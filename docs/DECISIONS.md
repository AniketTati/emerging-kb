# Engineering decisions vs the assignment — researched, not guessed

For each technical decision the assignment (the PDF) forces, this doc gives:
**(1) the problem**, **(2) how a senior ML engineer should reason about it**,
**(3) what the current state of the art actually says (researched May 2026)**,
and **(4) the conclusion we commit to.**

The headline finding: **our architectural skeleton matches current SOTA on
almost every axis.** Where we fail the brief is not wrong choices — it's
*deferred/half-built pieces*, and for each one the field has a named, proven
technique. So this is "complete it the right way," not "redesign it."

Maps to the FIX_CHECKLIST tasks; this doc is the *why*, the checklist is the
*what*.

---

## D1 — Parsing scanned + digital PDFs (§2.2)

**Problem.** The brief requires both scanned and digital PDFs, "the right
parsing approach per format," structure preserved (tables, layout), long docs
without losing context.

**Senior-engineer reasoning.** Parsing is the root of the pipeline — every
downstream stage inherits its errors. You need (a) a layout-aware parser for
digital PDFs that keeps tables/headings, and (b) an OCR path for scans, with a
quality gate that escalates only when the cheap path fails. Re-OCRing a whole
document to fix one page is waste.

**What the field says (2026).** Open-source consensus is **Docling** (IBM —
DocLayNet layout + TableFormer tables, ~97.9% on complex tables) and **Marker**
(strong on academic/reference docs), both producing clean Markdown locally and
free. For scans, combine OCR with layout detection — **Mistral OCR / Azure
Document Intelligence / vision-LLM cleanup** (e.g. Marker `--use_llm`). Vision
parsers win on complex layouts but cost more and are slower, so gate them.
[Firecrawl: Best PDF Parsers 2026](https://www.firecrawl.dev/blog/best-pdf-parsers) ·
[Procycons benchmark](https://procycons.com/en/blogs/pdf-data-extraction-benchmark/) ·
[Reducto: LLM-ready parsers 2025](https://llms.reducto.ai/best-llm-ready-document-parsers-2025)

**Conclusion.** **Our choice (Docling primary + Gemini-vision OCR escalation
gated by a quality score) is exactly the SOTA pattern. Keep it.** The only fix
is the audit's per-page-vs-whole-doc waste: escalate OCR per bad page, not the
whole document. No redesign — a targeted efficiency fix.

---

## D2 — Chunking + contextualization (§2.2 long docs, §2.3 retrieval)

**Problem.** Long documents must be chunked without losing cross-boundary
context, and chunks must retrieve well. Today (audit I1) chunking runs *before*
classification, so the type-aware chunkers never fire for PDFs — they get a
generic splitter.

**Senior-engineer reasoning.** Chunk granularity is the second-biggest lever
after parsing. A contract should chunk by clause, a statement by row. Generic
fixed-size splitting destroys that structure. Separately, a bare chunk ("the
cap is ₹2.2cr") is ambiguous without its document context — you must inject
context before embedding, or embed with full-document context.

**What the field says (2026).** Two findings line up exactly with our gaps:
1. **Structure-aware splitting (e.g. MarkdownHeader) is "the single biggest and
   easiest improvement you can make."** ← this is precisely what I1 disables.
2. For context, the two leading techniques are **Anthropic Contextual
   Retrieval** (prepend doc context to each chunk before embedding — better
   coherence, higher cost) and **late chunking** (embed the whole doc, then
   mean-pool per chunk — cheaper, slightly lower relevance).
[Firecrawl: Best Chunking Strategies 2026](https://www.firecrawl.dev/blog/best-chunking-strategies-rag) ·
[Late chunking vs contextual retrieval (KX)](https://medium.com/kx-systems/late-chunking-vs-contextual-retrieval-the-math-behind-rags-context-problem-d5a26b9bbd38) ·
[Reconstructing Context (arXiv 2504.19754)](https://arxiv.org/pdf/2504.19754)

**Conclusion.**
- **Fix I1 first — it's the highest-leverage change in the whole system per the
  research** (structure-aware chunking is the biggest easy win, and we've
  disabled it for PDFs). Classify before chunk, route to the type-aware chunker.
- **We already use Contextual Retrieval — that's a SOTA choice. Keep it**, but
  its cost (resending the whole doc per chunk, no cache — audit I5/S1) is real;
  **evaluate switching to late chunking** as the cheaper alternative and let the
  eval decide. This is a measured trade, not a guess.

---

## D3 — Schema-conforming extraction + field convergence (§2.1, §2.2)

**Problem.** The brief requires "structured outputs that match the schema — not
raw text chunks," with consistent fields across documents. Today (audit I2)
field names are merged by exact-string match only; the real merger was deferred,
producing 577 distinct field names across 46 docs — cross-doc aggregation is
impossible.

**Senior-engineer reasoning.** There are two ways to get schema-conforming
fields: (a) *constrain* extraction to a fixed schema (rigid, fails on emergent
fields), or (b) *extract freely, then canonicalize* to the schema afterward.
For a system whose premise is "schema emerges from data," (b) is the only
coherent choice — but canonicalization must be semantic, not string-matching.

**What the field says (2026).** This is a solved problem with a named
framework: **EDC — Extract, Define, Canonicalize** (extract freely → define each
field → canonicalize variants to a target schema via **vector similarity +
LLM verification**). LLM-based canonicalization maps lexical variants ("temp." →
"temperature") to schema terms using a controlled vocabulary + embedding match +
an explicit LLM "is this transformation valid?" check. Schema-guided JSON output
(function calling) reduces format errors. And **PARSE (EMNLP 2025): 55% of
extraction accuracy gain came from *flattening* the schema** so the model never
infers relationships.
[Extract-Define-Canonicalize (arXiv 2404.03868)](https://arxiv.org/pdf/2404.03868) ·
[Schema-driven prompt design](https://tianpan.co/blog/2026-04-12-schema-driven-prompt-design-letting-your-data-model-drive-your-prompt-structure) ·
[LLM-powered data normalization](https://scrapingant.com/blog/llm-powered-data-normalization-cleaning-scraped-data)

**Conclusion.** **I2 is the single most important *correctness* fix, and the
SOTA prescribes exactly the method:** replace exact-string clustering with
**embedding-blocking + LLM-judge canonicalization (the EDC pattern)** — keep
free extraction, but converge field names semantically against the workspace
vocabulary, with an LLM validity check. Drop the 80%-prevalence promotion gate
(it blocks rare real fields). Keep schema-guided JSON output (we have it). This
is a build, but a well-trodden one.

---

## D4 — Identity / entity resolution (§2.2 "merge same logical thing")

**Problem.** When two docs describe the same entity, merge them. Today (audit
I4) we do top-1 nearest-neighbour + a threshold + an LLM judge for borderline —
which misses a true match that's the 2nd neighbour, and on an embedder hiccup
creates *all* entities as new (duplicate pollution).

**Senior-engineer reasoning.** Entity resolution is a classic blocking +
matching problem. Top-1 is wrong by construction — you want a *candidate set*
(blocking), then pairwise matching. And resolution must be resilient: a
transient failure must not silently fork the entity graph.

**What the field says (2026).** Strong consensus: **blocking improves both
runtime AND accuracy.** The modern recipe is **semantic blocking** (embeddings
+ ANN to build small candidate groups) → **LLM match/merge** on the candidates.
"Semantic entity resolution is best suited for knowledge graphs extracted from
unstructured text… embeddings block data into matching groups, then an LLM
matches and merges." Two-stage: dedup exact → embedding-block (top-k) → LLM.
[Towards Data Science: Semantic Entity Resolution](https://towardsdatascience.com/the-rise-of-semantic-entity-resolution/) ·
[Record Linkage + Blocking (MDPI 2025)](https://www.mdpi.com/1999-4893/18/11/723) ·
[Pre-trained Embeddings for Entity Resolution (VLDB)](https://www.vldb.org/pvldb/vol16/p2225-skoutas.pdf)

**Conclusion.** **Fix I4 to the SOTA recipe: embedding-blocking (top-k
candidates, not top-1) → LLM match.** Treat embedder failure as retry, never as
"create new." Our 4-stage design (exact → embedding → LLM-judge → new) is
already the right *shape* — the bug is top-1 and the fail-open. A surgical fix,
not a redesign.

---

## D5 — Hybrid retrieval, fusion, reranking, and the mode sprawl (§2.3)

**Problem.** "Don't rely on vector similarity alone — combine lexical and
semantic." Today we have a sound hybrid base (BM25 + dense, RRF, rerank) but
buried under a 13-"mode" facade (audit Q2) that is mostly 1 real retriever +
cosmetic post-filters.

**Senior-engineer reasoning.** The proven production retriever is simple:
sparse + dense in parallel, fuse by rank, rerank the top candidates with a
cross-encoder. Complexity beyond that (many "modes") needs to *earn its keep*
in the eval, or it's surface area that hides bugs.

**What the field says (2026).** This is the single most settled area. **Hybrid
(BM25/SPLADE + dense e5/BGE) + Reciprocal Rank Fusion (k=60) + cross-encoder
rerank is "the minimum viable baseline for any RAG deployment."** RRF needs no
score calibration. Cross-encoder rerank improves sharply at 50–100 candidates.
ColBERT/SPLADE late-interaction is the scale option.
[Hybrid Search + Reranking Playbook](https://optyxstack.com/rag-reliability/hybrid-search-reranking-playbook) ·
[Production RAG: Hybrid + Re-ranking (ColBERT/SPLADE/BGE)](https://machine-mind-ml.medium.com/production-rag-that-works-hybrid-search-re-ranking-colbert-splade-e5-bge-624e9703fa2b) ·
[Hybrid Search done right (BM25+HNSW+RRF)](https://ashutoshkumars1ngh.medium.com/hybrid-search-done-right-fixing-rag-retrieval-failures-using-bm25-hnsw-reciprocal-rank-fusion-a73596652d22)

**Conclusion.** **Our retrieval core (BM25 + dense + RRF k=60 + cross-encoder
rerank) IS the SOTA baseline — it's correct.** The fix (Q2) is to **delete the
mode facade down to a small honest set** (hybrid + SQL + chain + graph) and let
each remaining mode earn its place on the now-trustworthy eval. Consider
ColBERT/SPLADE only as a scale option later (S-series). We over-built on top of
a correct base; the fix is subtraction.

---

## D6 — Citations, provenance, confidence, faithfulness (§2.4)

**Problem.** Every claim must trace to file → page range → exact excerpt; show
"what the system did"; carry a confidence signal + reason. Today: citations
carry file + char-offset (page partly, P5), the faithfulness gate defaults to
weak Jaccard overlap (audit Q5), there's no answer-level confidence (P2), and a
fallback can attach fake citations (cheap bug).

**Senior-engineer reasoning.** "Cited or it didn't happen" means grounding must
be verified, not assumed. The robust pattern is claim-level: decompose the
answer into atomic claims, verify each against retrieved spans, and derive both
the citation and the confidence from that verification. Jaccard overlap is not a
grounding check.

**What the field says (2026).** The field has converged on **claim-decomposition
+ span-level verification**: decompose an answer into independently verifiable
claims, do local-to-global verification, map each claim to specific answer spans
with explicit context-side evidence (e.g. RT4CHART / RefChecker-style). Sobering
data point: **>95% of open-LLM answers contain at least one unattributed
sentence** — so this must be enforced, not hoped for. Confidence follows from
claim-level pass rates.
[Span-level hallucination detection (arXiv 2504.18639)](https://arxiv.org/pdf/2504.18639) ·
[Why citation-based RAG still hallucinates](https://yaihq.com/research/citation-based-rag-still-hallucinates) ·
[RAG evaluation guide 2025 (Maxim)](https://www.getmaxim.ai/articles/rag-evaluation-a-complete-guide-for-2025/)

**Conclusion.**
- **Q5: replace the Jaccard default with claim-decomposition + span
  verification** (we already ship an HHEM/NLI gate — make it the default and
  apply it per-claim). This is the SOTA grounding method.
- **P2: derive the answer confidence + reason from the per-claim verification**
  (e.g. "high — all 3 claims grounded; medium — 1 claim weakly supported").
- **Kill the fake-citation fallback** — an unattributed answer must say so, per
  "cited or it didn't happen."
- **P5: extend single-page to page-range.** All four are completion, not new
  architecture.

---

## D7 — User-defined schema + configurability (§2.1, §2.5)

**Problem.** The user must define/version/evolve a schema with NL field
descriptions and relationships; pipeline behavior (models, thresholds, limits,
prompts) must be configurable *without code changes*, layerable. Today: schema
versioning exists; the layered-config table exists — but the actual thresholds/
prompts are hardcoded constants (P1), and no demo schema artifact ships (P3).

**Senior-engineer reasoning.** "Domain-agnostic, swap config not code" is the
brief's central claim. If a domain swap or a threshold tune needs a code edit,
the claim is false. Config must be data, and the schema the user defines must be
a first-class, loadable artifact — not only an auto-discovered byproduct.

**What the field says (2026).** Schema-driven extraction explicitly treats the
data model as the contract that drives prompts and validation — "let your data
model drive your prompt structure." Schema-guided / function-calling outputs are
the norm. The discipline is: the schema is the configuration, and the pipeline
reads it at runtime.
[Schema-driven prompt design](https://tianpan.co/blog/2026-04-12-schema-driven-prompt-design-letting-your-data-model-drive-your-prompt-structure)

**Conclusion.**
- **P1: wire the hardcoded thresholds/limits/prompts through the existing
  layered-config table.** No new infra — connect what's there. This is what
  makes the domain-agnostic claim true.
- **P3: ship a demo schema (things, fields, NL descriptions, relationships) +
  a one-command loader**, satisfying the deliverable and proving config-driven
  onboarding.
- Schema versioning (§2.1) already meets the brief — leave it.

---

## D8 — Lineage, relationships, conflict across independent docs (§2.2, §2.3)

**Problem.** Capture lineage (parent chain) + relationships (cross-refs);
reconcile the same fact across documents. Today lineage (ltree) and relationships
(graph) exist, but conflict reconciliation only fires for *chained* docs (audit
Q1) — two independent docs that disagree are never surfaced.

**Senior-engineer reasoning.** "The same logical thing appears across multiple
documents and must be reconciled" (the brief's §1 motivation) is the core value.
Restricting reconciliation to revision chains delivers half of it. Conflict must
key on the *resolved entity + predicate*, not on a shared chain id.

**What the field says.** This rides on D3/D4: once fields converge (D3) and
entities resolve (D4), "the same fact across docs" is detectable by grouping on
(entity, predicate) and comparing values — the standard KG-construction +
fact-reconciliation pattern (EDC + entity resolution feed exactly this).

**Conclusion.** **Q1: un-gate conflict detection from chain_id — detect on
(resolved-entity, predicate) for any two docs, then classify
(chain-supersession / independent-transaction / contradiction).** This depends
on D3 + D4 landing first (convergent fields + resolved entities), which is why
the checklist orders ingestion before query.

---

## D9 — Evaluation (§5.5)

**Problem.** 15+ ground-truth Q&A with expected source files + key facts and a
scoring script.

**What the field says (2026).** Modern RAG eval scores **retrieval** (recall@k,
context relevance) and **generation** (faithfulness, answer correctness)
*separately*, with grounded/attributed ground truth.
[Complete guide to RAG evaluation 2025 (Maxim)](https://www.getmaxim.ai/articles/complete-guide-to-rag-evaluation-metrics-methods-and-best-practices-for-2025/)

**Conclusion.** **We exceed the requirement** (300 Q&A across 6 domains, now
93% corpus-grounded with evidence quotes + a scorer). The one SOTA gap is
**per-stage measurement** (checklist M1) — score retrieval and generation
separately, which the now-verified citations make possible. Do M1 early; it
makes every other decision measurable.

---

## The through-line

Across nine decisions, **only one was a genuinely questionable choice** (the
mode sprawl, D5 — and even that sits on a correct hybrid base). Everything else
is **the right architecture with a deferred or half-built piece**, and for each,
the field has a named, proven completion:

| Decision | Our skeleton vs SOTA | The fix the research prescribes |
|---|---|---|
| D1 Parsing | ✅ matches SOTA | per-page OCR escalation |
| D2 Chunking | ✅ right techniques, **disabled** | I1 classify-before-chunk (biggest easy win) |
| D3 Extraction | ✅ free-extract, **canonicalizer missing** | I2 = EDC (embedding-block + LLM-judge) |
| D4 Identity | ✅ right shape, **top-1 bug** | I4 = semantic blocking (top-k) |
| D5 Retrieval | ✅ IS the SOTA baseline | Q2 = delete the facade |
| D6 Citations | 🟡 weak gate | Q5/P2 = claim-decomposition + span verify |
| D7 Config | 🟡 infra exists, **not wired** | P1 = wire to layered config |
| D8 Conflict | 🟡 chain-only | Q1 = key on (entity, predicate) |
| D9 Eval | ✅ exceeds | M1 = per-stage measurement |

We did not pick wrong. We left things unfinished and let a facade hide it. The
path to delivering the PDF is **completion against these named methods**, in the
checklist's order, measured on the now-trustworthy eval.
