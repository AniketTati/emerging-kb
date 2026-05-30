# RCA — 12 failures + 21 partials in construction v14 eval

**Goal:** correct-only at industry-top (60-75%) + correct+partial at 90%+
**Current (v14):** 34% correct, 76% correct+partial
**Audit date:** 2026-05-28

---

## Methodology

For each of 12 failures and the most informative partials, I traced:
1. The exact question asked
2. The expected answer (from queries.yaml)
3. What the system actually returned
4. Which pipeline stage produced the failure (ingestion / extraction / retrieval / planning / generation / faithfulness)
5. Whether the root cause is data, code, or prompt

Grouped failures by **root cause**, not by stratum. Same RC often affects multiple strata.

---

## ROOT CAUSE INVENTORY (11 distinct causes)

### RC1 — Bug D: field-name fragmentation in proposed_fields (data)

**Pipeline stage:** EXTRACTION (`kv_tables.py`)

**The bug:** The KV+Tables extractor invents new `field_name`s per-doc instead of canonicalizing to a workspace-wide set. The same concept appears as `total_cost_premium` in CO-005 and `total_cost_inr` in CO-018. Q-mode's `SUM(value_numeric)` then sums one column at a time.

**Queries affected:** q033 (wrong), q019 (partial), q020 (partial), partial impact on q036.

**Fix status:** Proposal already generated at `docs/field_merge_proposal_construction.yaml` (34 merge groups across 9 doctypes). Needs review + apply.

**Effort:** 10 min review + 1 min apply.

**Predicted lift:** +1-2 correct (q033), +1 partial→correct conversion (q019, q020).

---

### RC2 — Canonical entity fragmentation (data)

**Pipeline stage:** IDENTITY RESOLUTION (4-stage pipeline at end of ingestion)

**The bug:** Mahalaxmi exists as 4 separate `canonical_entities` rows ("Mahalaxmi" 109 mentions, "Mahalaxmi Infrastructure Pvt Ltd" 62, "Mahalaxmi Infrastructure" 45, "Mahalaxmi Infra" 25). The 0.92 embedding-similarity threshold for auto-merge was too conservative; LLM-judge stage in identity resolution didn't catch these as same-entity.

**Queries affected:** q034 (refused — Q-mode couldn't COUNT(DISTINCT canonical_name) cleanly).

**Fix status:** Proposal generated at `docs/entity_dedup_proposal_construction.yaml` (37 merge groups). Needs review + apply.

**Effort:** 10 min review + 1 min apply.

**Predicted lift:** +1 correct (q034 partial fix).

---

### RC3 — Generator prompt missing substitution-trap rules (prompt)

**Pipeline stage:** GENERATION (`generate.py:_SYSTEM_PROMPT`)

**The bug:** Phase 2.6 cut the 215-line prompt to 60 lines. The cut removed the explicit substitution-traps table:
```
asked 'location/address/site' → don't return project name
asked 'cost/value/amount' → don't return contract number
asked 'date' → don't return doc number
asked 'who is X' → don't return what X is doing
```
Without that explicit rule, the LLM confuses similar-looking fields.

**Queries affected:** q006 (wrong — "project site location" returned **project name** "Acme Whitefield Datacentre Phase-2" instead of the address "Survey No. 184/2A, Whitefield Main Road").

**Fix:** Restore the substitution-traps section to the system prompt.

**Effort:** 30 minutes (rewrite section).

**Predicted lift:** +1 correct (q006).

---

### RC4 — Generator prompt missing detailed conflict-resolution rules (prompt)

**Pipeline stage:** GENERATION

**The bug:** Phase 2.6 cut the detailed conflict-resolution prose with the 5-priority signal cascade. The new slim prompt says "USE the resolved winner from each `<conflict>` tag" but the generator still picks the LOSER side and stops.

**Queries affected:**
- q021 (wrong): "Grid C vs Grid D" → generator described Rev A's Grid C (loser) and stopped. Didn't surface Grid D as winner.
- q022 (wrong): "worker error vs system failure" → generator described only the initial report's worker error (loser). Didn't mention investigation's system failure.
- q014 (partial): walks safety chain but mentions only the initial worker error.
- q017 (partial), q018 (partial) — same pattern of stopping at first answer.

**Hypothesis:** the structured `<conflict_resolution>` block is being placed in the user message, BUT the slim prompt's reference to it ("USE the resolved winner") is too terse — the LLM follows the dominant prose pattern of describing the first snippet it sees.

**Fix:** Restore the conflict-resolution prose with the priority cascade (chain → status → authority → recency) AND add an explicit "Never report the LOSING side without naming the winning side" rule.

**Effort:** 1 hour.

**Predicted lift:** +3 correct (q021, q022, q014 partial→correct).

---

### RC5 — Generator prompt missing enumeration discipline (prompt)

**Pipeline stage:** GENERATION

**The bug:** Phase 2.6 simplified the enumeration rule. Many partials are "answer is right, but only mentioned 1 of 3 items":
- q004 (partial): contract value was **INR 44.10 cr → INR 45.38 cr (after variations)**. Generator returned only INR 44.10. Missed the final after-variations value.
- q010 (partial): worker is Mr. Dinesh Kumar (32, mason at Sai Labour Agency, Aadhaar redactable as XXXX XXXX 1842). Generator returned just the name.
- q013 (partial): chain Rev A → Rev B → Rev C. Generator skipped some.
- q016 (partial): 2-rev chain. Generator described Rev A only.
- q017 (partial): "No — superseded by Rev B then Rev C". Generator just said "no longer authoritative" with one citation.
- q018 (partial): "38 of 42 closed + 4 carried into DLP". Generator returned just the 4.
- q019 (partial): variations chain "44.10 → 44.32 (CO-005) → 45.38 (cumulative)". Generator returned only first two.
- q020 (partial): "18 change orders cumulative". Generator mentioned CO-005 only.

**Fix:** Restore explicit enumeration prose. Add a rule: "When the question concerns a chain, history, or list of items, enumerate ALL items even if only one was mentioned in the user query — multi-item answers are the norm for chain-aware queries."

**Effort:** 1 hour combined with RC4 prompt restoration.

**Predicted lift:** +5-8 partial → correct conversions.

---

### RC6 — Generator parse_error under heavy prompt load (generation)

**Pipeline stage:** GENERATION

**The bug:** q015 ("FINAL root cause of May 2025 worker fall") triggered `n_conflicts=30` in the conflict detector. Even after compacting Phase 2.3's conflict block to one line per conflict, the generator still parse_errored. The raw output is malformed JSON.

**Hypothesis:** With 30 detected conflicts + 10 hits × 2.5K chars each + system prompt + conflict block + user message, the input context is ~30K tokens. Even though `max_output_tokens=16000`, the generator may be hitting attention bottlenecks producing valid JSON.

**Fix options:**
1. Investigate the raw output (need to enable detailed generator logging on parse_error)
2. Cap conflicts shown to generator at top-5 by relevance (drop the rest)
3. Use Gemini Pro for generation when conflict_count > 10 (higher capacity)
4. Enable `thinking_budget=2048` on generator (gives it more reasoning room before producing JSON)

**Effort:** 4-6 hours (investigate then decide).

**Predicted lift:** +1 correct (q015).

---

### RC7 — Conflict detector over-firing (rule-based detection)

**Pipeline stage:** CONFLICT RESOLUTION (`conflict_detector.py`)

**The bug:** q023 ("Two POs different unit rates — which authoritative?") — the EXPECTED answer is "Each PO is independently authoritative for its own consignment. Different POs for different time periods can have different rates due to market fluctuation."

But the conflict detector saw 2 PO rows with different unit_rate values for "steel" → flagged as conflict → unresolved → conflict block told generator to "surface BOTH". Generator picked one as THE authoritative.

**Real issue:** POs aren't ontologically in conflict — they're independent transactions. The detector treats every cross-doc disagreement as a conflict.

**Fix:**
1. Add ontology rules to conflict_detector: certain predicates (like PO `unit_rate`) are NOT cross-doc conflicts when the docs are different transactions.
2. OR: when conflict resolution is `unresolved` and the docs are NOT in a doc_chain, downgrade to "independent values" not "conflict".

**Effort:** 4 hours (taxonomy + code).

**Predicted lift:** +1 correct (q023).

---

### RC8 — Q-mode planner can't do date arithmetic (capability)

**Pipeline stage:** Q-MODE PLANNER (`q_payload_gen.py`)

**The bug:**
- q035 ("Total project duration in days from contract signing to mechanical completion") → Q-mode refused with "requires calculating the difference between two dates, not supported"
- q028 ("Any incidents with >1 month investigation period?") → similar — needs DATE(corrective) - DATE(initial) > 30 days

**The expected SQL would be:**
```sql
SELECT (max(value_numeric) - min(value_numeric)) AS days
FROM proposed_fields WHERE field_name IN ('mechanical_completion_date','contract_effective_date');
```
But the q_payload_gen prompt currently teaches MIN/MAX as a single aggregation, not difference-of-two.

**Fix:** Add date-arithmetic patterns to q_payload_gen prompt with examples. Specifically:
- "Days between two events" → emit subqueries OR DATE_PART expression
- May need to extend the Q-mode grammar to support expressions

**Effort:** 1 day (grammar + prompt + validator).

**Predicted lift:** +1 correct (q035, q028 partial improvement).

---

### RC9 — Q-mode planner conservatism on MAX/MIN over time-series (capability)

**Pipeline stage:** Q-MODE PLANNER

**The bug:** q036 ("Average headcount at PEAK") → Q-mode refused saying "available data does not support querying for peak values directly. It only provides 'total_headcount' for specific daily reports."

But that's exactly what MAX does. The Q-mode prompt didn't teach the planner to use MAX on daily_site_report rows.

**Fix:** Add example to q_payload_gen prompt: "For 'peak X over time' → SELECT MAX(field) FROM proposed_fields WHERE inferred_doc_type='<type>' GROUP BY ...".

**Effort:** 2 hours.

**Predicted lift:** +1 partial→correct (q036).

---

### RC10 — Retrieval misses on specific queries (retrieval)

**Pipeline stage:** RETRIEVAL (`channels.py`, `rerank.py`)

**The bug:** Three queries refuse because retrieval doesn't surface the right doc:
- q009 ("foundation depth at Noamundi") → expected `drawing-004-structural-foundation-datacentre`. The doc EXISTS (we cite it in v10 q044), but for this query BM25/dense miss it.
- q011 (partial): expected EPC contract Clause 9; retrieval surfaced labour-contract-001-sai-marathi-scan instead. Marathi document has higher BM25 score on "PPE" + "labour" but it's the wrong clause source.
- q025 (refused): "Two RFI responses give different fire-stop ratings". Expected `rfi-002-mep-electrical-routing`. Retrieval missed.

**Hypothesis for q011:** Marathi labour contract has stronger "PPE" + "labour" + "site" matches in BM25 than the EPC contract's clause. Need re-rank tiebreaker on `inferred_doc_type` priority.

**Fix:**
1. Per-query investigation (need to see top-20 retrieved for each).
2. Add cross-encoder rerank (BGE-reranker-v2) instead of current rerank.
3. Boost authoritative doctypes (contract > daily report) in rerank stage.

**Effort:** 1-2 days (rerank model integration + tune).

**Predicted lift:** +2-3 correct (q009, q011, q025).

---

### RC11 — Generator enumeration weakness on chain queries (generator capability)

**Pipeline stage:** GENERATION

**The bug:** Even with the chain documented in retrieval, the generator stops at the first item it cites. q013, q014, q016, q017 all suffer from this. Some are RC5 (prompt) but some are deeper — the model genuinely cuts off because the prompt schema requires a single JSON `answer` field and the model treats that as "give one answer".

**Fix options:**
1. Strengthen enumeration prompt (RC5)
2. Switch generator from Flash to Pro for chain-aware queries (Pro is better at multi-item synthesis)
3. Add a post-generation check: if `intent in {chain_aware, temporal_history}` and answer length < 500 chars, retry with explicit "enumerate every distinct item" hint

**Effort:** Choose 1: 1 hour (RC5 alone), 1 day (Pro fallback), 2 days (post-generation enumeration retry).

**Predicted lift:** +2-3 partial→correct conversions (compounding with RC5).

---

## ROOT CAUSE → QUERY MAP (the master matrix)

| Failure | Stratum | Primary RC | Secondary RC |
|---|---|---|---|
| q006 | needle | RC3 (substitution) | — |
| q009 | needle | RC10 (retrieval miss) | — |
| q015 | chain-aware | RC6 (parse_error) | — |
| q021 | conflict-resolution | RC4 (conflict prompt) | — |
| q022 | conflict-resolution | RC4 (conflict prompt) | — |
| q023 | conflict-resolution | RC7 (detector over-fire) | RC4 |
| q025 | conflict-resolution | RC10 (retrieval miss) | — |
| q028 | rare-clause | RC8 (date arith) | — |
| q033 | aggregation | RC1 (Bug D) | — |
| q034 | aggregation | RC2 (entity dedup) | — |
| q035 | aggregation | RC8 (date arith) | — |
| q036 | aggregation | RC9 (MAX over time) | — |

**Partials affected by these RCs:**
- RC4 prompt: q014, q017, q018
- RC5 enumeration: q004, q010, q013, q014, q016, q017, q018, q019, q020
- RC1 Bug D: q019, q020 (cumulative variations)
- RC10 retrieval: q011

---

## SOLUTION PLAN — ordered by leverage and risk

### Phase A — Data fixes (1 day, near-zero risk)
Just apply existing proposals after review.

| Step | Action | Files | Predicted lift |
|---|---|---|---|
| A.1 | Review `docs/field_merge_proposal_construction.yaml` — keep good merges, drop bad ones | YAML | — |
| A.2 | Apply with `scripts/normalize_field_names_llm.py --apply` | DB writes | +2 correct (q033 + q019/q020 partials) |
| A.3 | Review `docs/entity_dedup_proposal_construction.yaml` — drop Siemens/Healthineers, Whitefield-vs-Phase-2 etc. | YAML | — |
| A.4 | Apply with `scripts/dedup_canonical_entities.py --apply` | DB writes | +1 correct (q034) |

**Expected after Phase A:** 34% → 40% correct, 76% → 82% correct+partial.

### Phase B — Generator prompt restoration (1 day, low risk)
Surgical undo of Phase 2.6 — restore the rules that demonstrably help. Keep the structure improvements; add back the prose that matters.

| Step | Restore | Targets |
|---|---|---|
| B.1 | Substitution-traps table | q006 |
| B.2 | Detailed conflict-resolution prose with priority cascade + "never report loser without winner" | q021, q022, q014 |
| B.3 | Strong enumeration discipline with chain-query examples | q013, q014, q016, q017, q018, q019, q020 (partials → correct) |
| B.4 | "Compute when asked" with date examples | q028 (partial uplift) |
| B.5 | Keep the structured `<conflict_resolution>` block reference | — |
| B.6 | Drop only the redundant rules that overlap | keep prompt under ~110 lines |

**Expected after Phase B:** +3 correct (q006, q021, q022), +5-8 partial→correct conversions (q004, q013, q014, q016, q017, q018, q019, q020). **45-50% correct, 85-90% correct+partial.**

### Phase C — Q-mode capability extensions (2 days, medium risk)
Capabilities the planner currently refuses on.

| Step | Add to Q-mode | Targets |
|---|---|---|
| C.1 | Date arithmetic — examples in q_payload_gen prompt + grammar extension for `EXTRACT(epoch FROM (date1 - date2))/86400` | q035, q028 |
| C.2 | MAX/AVG over time-series — explicit example in prompt | q036 |
| C.3 | Per-doctype canonicalized field hints — already partial via RC1 fix |  |
| C.4 | Fallback: if Q-mode refuses, route to H-mode with the original query + a "compute the answer from snippets" instruction | catches Q-mode misses |

**Expected after Phase C:** +3 correct (q035, q028, q036). **50-55% correct, 90%+ correct+partial.**

### Phase D — Retrieval tightening (2 days, medium risk)
Per-query investigation + rerank improvements.

| Step | Action | Targets |
|---|---|---|
| D.1 | For q009, q011, q025: dump top-20 retrieved + identify why expected doc isn't there | — |
| D.2 | Add doctype-priority boost in rerank (contract > daily report when ambiguous) | q011 |
| D.3 | Optional: integrate cross-encoder rerank (BGE-reranker-v2-m3) — replaces current rerank | q009, q025 |
| D.4 | If q011 needs Marathi handling, check Marathi extraction quality | q011 |

**Expected after Phase D:** +2-3 correct (q009, q011, q025). **55-60% correct.**

### Phase E — Conflict detector tightening (1 day, medium risk)
Stop over-firing on independent transactions.

| Step | Action | Targets |
|---|---|---|
| E.1 | Add doctype-aware conflict rule: `purchase_order` and similar transactional doctypes don't trigger conflicts across instances unless they share a `pk` (consignment ID, transaction ID) | q023 |
| E.2 | When conflict is `unresolved` AND docs aren't chain-related, downgrade severity in the conflict block | softer signal to generator |

**Expected after Phase E:** +1 correct (q023). **60% correct.**

### Phase F — Parse-error investigation (4 hours, low risk)
Just diagnose first; don't change code blindly.

| Step | Action |
|---|---|
| F.1 | Add raw-output capture on parse_error in generate.py logger |
| F.2 | Run q015 in isolation; inspect the raw output |
| F.3 | If output is truncated → bump tokens further OR cap conflict-block items to 5 |
| F.4 | If output is malformed JSON for another reason → fix the brace-balanced extractor |

**Expected after Phase F:** +1 correct (q015). **62% correct, 92% correct+partial.**

### Phase G — Quality push for industry-top (1-2 weeks)
Beyond Phase A-F we're at ~62% correct / ~92% correct+partial. To push correct-only into the 70-75% range:

- Switch generator from Gemini Flash → Pro for high-stakes queries (chain-aware, conflict-resolution, multi-hop). Pro is +10-15pp on correctness for synthesis tasks.
- Enable HHEM faithfulness gate (currently disabled — defaults to Heuristic).
- Enable per-sentence regeneration with HHEM (current code already supports it).
- Tighten the enumeration prompt with negative-example tuning.

**Expected after Phase G:** 70-75% correct, 92-94% correct+partial. **Industry-top territory.**

---

## TIMELINE

| Phase | Time | Cumulative correct | Cumulative correct+partial |
|---|---|---|---|
| Current (v14) | — | 34% | 76% |
| A — Data fixes | 1 day | 40% | 82% |
| B — Prompt restoration | 1 day | 50% | 88% |
| C — Q-mode | 2 days | 55% | 90% |
| D — Retrieval | 2 days | 60% | 92% |
| E — Conflict tightening | 1 day | 62% | 92% |
| F — Parse error | 0.5 day | 64% | 93% |
| **Subtotal** | **~7 days** | **64%** | **93%** |
| G — Pro model + HHEM | 1-2 weeks | 72% | 94% |

**~3 weeks to industry-top.** First 7 days alone gets correct+partial above 90%.

---

## OPEN QUESTIONS (need your call)

1. **Phase A proposal review** — the YAMLs contain some questionable merges (Siemens vs Healthineers, Whitefield vs Phase-2). Want me to walk through them with you OR do you trust the auto-LLM judgment and apply as-is?

2. **Phase G Pro switch** — Gemini Pro is ~5x cost per query vs Flash. Acceptable for production?

3. **HHEM enablement** — currently Identity gate. Switching to HHEM adds ~600MB model + 100ms per turn. Acceptable?

4. **Phase D rerank model** — BGE-reranker-v2-m3 is ~500MB, runs in 50ms per query. Replaces current LLM-judge rerank. Acceptable to add?

These 4 decisions determine the realistic ceiling.
