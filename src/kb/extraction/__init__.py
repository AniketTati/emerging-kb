"""Phase 5 — open extraction package.

Three submodules land here across 5a/5b/5c:

- `mentions` (5a) — NER over contextual chunks → `extracted_mentions` table.
- `fields` (5b) — doc-type classifier + emergent field proposer.
- `promotion` (5b) — cross-doc field clustering + auto-promotion to typed schema.
- `plugins/{clauses,transactions,rows}` (5c) — atomic-unit extraction per doc-type.
- `anomaly` (5c) — per-type rarity / anomaly scoring.

All three submodules follow the factory pattern established by
`kb.contextualization.make_contextualizer()` (3b/3b-bis) and
`kb.summarization.make_summarizer()` (3d):
  - 3-impl adapter (Gemini default + Anthropic alt + Identity fallback).
  - 4-value env selector (`KB_<STAGE>_EXTRACTOR ∈ {gemini, anthropic, identity, auto}`).
  - `auto` probes Gemini key → Anthropic key → Identity.
"""
