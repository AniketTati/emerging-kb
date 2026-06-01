# Ground-truth analysis — read the actual corpus, query by query

**Method:** I read the real source documents (contract, handover certificate,
drawing Rev C, safety report, daily report) in full, and grep-verified every
other failing-query fact against the real files. For each query I asked four
questions:
1. Is the answer physically present in the corpus?
2. In what form (frontmatter / table / prose / spread across docs)?
3. What operation does answering it actually require?
4. Is the eval's expected answer / citation itself correct?

This is the homework that was skipped for a week. It changes the conclusion.

---

## Finding 0 — The corpus is CLEAN, not messy

Every document is well-structured markdown: YAML frontmatter (`doc_id`,
`doc_type`, `effective_date`, `parties`, `status`, sometimes `chain_id` +
`parent_doc`), clear section headers, and facts in tables. The contract's
value is a table. The handover's punch-list status is a table. The daily
report's headcount is a table.

**Implication:** parsing and chunking are NOT the bottleneck here. The facts
are sitting in readable form. This corpus actually *masks* extraction
weakness — the docs are so clean a generator can read the answer straight
out of a retrieved passage. (At real enterprise scale with messy PDFs,
extraction quality bites harder. More on that at the end.)

---

## Finding 1 — Most answers ARE present. The failures are mostly READ-side.

I verified the facts behind the failing queries. They exist:

| Query | Fact | Present? | Where |
|---|---|---|---|
| q007 safety officer | "Pradeep Bhargava, Senior Safety Officer" | ✅ | safety-002 line 13 (and 10 other docs) |
| q015 final root cause | "system failure: PPE non-availability + supervisory gap; worker-error REJECTED" | ✅ | safety-001-investigation lines 60-92 |
| q022 worker-error vs system | same as q015 | ✅ | safety-001-investigation |
| q029 change orders >20L | CO-005 = ₹22,00,000; CO-018 = ₹22,18,400 | ✅ | change-order-001 line 52, change-order-002 line 32 |
| q009 foundation depth | "3.8 m ... hard weathered gneiss" | ✅ | drawing-004 lines 45-49 |
| q036 peak headcount | "Total 201" | ✅ | daily-004 line 24 |
| q025 fire-stop ratings | fire-stop / penetration content | ✅ | drawing-003, inspection-002, rfi-002 |
| q034 sub-contractors | Phoenix MEP, Sundar Structural, Sai Labour, Deshpande | ✅ | named across 6-18 docs each |

**So when v19 returned "wrong" or "refused" on these, it wasn't because the
data is missing — it's because the right document didn't reach the
generator, or the wrong strategy was used.** That's retrieval and planning,
not extraction.

---

## Finding 2 — The eval itself is wrong on ~7 queries

You said the corpus and answers can be wrong. They are. Confirmed cases:

| Query | Eval expects | Reality in corpus | Verdict on the eval |
|---|---|---|---|
| q006 site location | "Survey No. 184/2A, Whitefield Main Road, Bangalore 560066", cite **revC** | revC has NO address — only "Whitefield, Bangalore". The survey no. lives in **revA (superseded), environmental doc, safety-initial** | **Wrong citation.** Answer is reachable but not from the cited doc. |
| q011 PPE clause | "Clause 9 — Mahalaxmi shall maintain PPE for all workers + comply with Acts" | Clause 9 lists the Acts but **never says "maintain PPE"**. PPE specifics are in the labour contract / safety reports | **Over-claims.** Clause 9 ≠ a PPE clause. |
| q032 incident count | "1 LTI + **4** first-aid in April" | safety-002 says **6** first-aid in April | **Wrong number.** |
| q033 cumulative CO value | "~1.28 cr (CO-005 + CO-018 + others between)", mode = aggregation | Only 2 CO docs exist (sum = ₹44.18L). The real figure ₹1,30,19,400 is **stated in the handover certificate** | **Wrong mode + wrong number.** It's a lookup, not a sum, and the number is 1.30cr not 1.28cr. |
| q009 foundation depth | "Noamundi datacentre site" | Corpus is **Whitefield**; "Noamundi" appears nowhere. Depth 3.8 m is for Whitefield | **Wrong site name in the question.** |
| q041 two Mahalaxmis | cite `mahalaxmi-cross-domain` | That doc is **not in the construction workspace** (it's cross-domain) | **Unanswerable in this workspace by design.** |
| q036 avg headcount | "average ~140" | Peak 201 is stated; the ~140 average is **never stated** — must be computed across reports the corpus only partially provides | **Partially unanswerable.** |

That's ~7 of 50 where a "wrong"/"partial" verdict is the *eval's* fault, not
the system's. Our true ceiling on this eval is therefore well below 100% no
matter how good the system gets — roughly **43/50 is the realistic max**, and
several more are genuinely hard.

---

## Finding 3 — Chain-awareness is actively SUPPRESSING facts (the subtle bug)

q006's address exists only in **revA**, which is `SUPERSEDED`. revC (the
chain winner, which retrieval surfaces) *dropped* the address line. Our
chain logic correctly treats revC as authoritative and down-weights revA —
so the only document containing the address gets buried.

This is a real architectural tension: **"latest version wins" is right for
conflicting facts, but wrong for facts that simply weren't repeated in the
latest version.** A field present in revA and never contradicted (the site
address) should still be answerable. Today it isn't.

---

## Finding 4 — The real failure taxonomy (from ground truth, not verdicts)

Recategorizing all 32 non-correct v19 queries by what's *actually* wrong:

**A. Retrieval miss — right doc exists, didn't reach the generator (~7)**
q006 (address in revA/env/safety), q007 (safety-002), q011 (EPC vs labour
contract), q025 (rfi-002), q027 (mep drawing), q030 (rfi-001), q041
(needs cross-doc).
→ Fix: retrieval reliability. The fact is one well-ranked passage away.

**B. Wrong strategy — answer is a lookup in a summary doc, system aggregates (~4)**
q033 (handover states cumulative CO value), q019/q020 (handover states final
value + variation total), q018 (handover states punch-list 38/4).
→ Fix: prefer the summary/certificate document over computing.

**C. Chain reasoning — needs the full initial→investigation→corrective walk (~6)**
q013, q014, q015, q016, q017, q022, q038.
→ Fix: when a chain is detected, feed the generator ALL members in order;
and don't suppress superseded members that hold non-conflicting facts
(Finding 3).

**D. Genuine multi-doc aggregation / computation (~3)**
q032 (count incidents across safety docs), q034 (distinct sub-contractors),
q036 (average headcount over time).
→ Fix: entity-role tagging (q034) + time-series aggregation (q036). These
are the only ones where structured-extraction convergence is the real lever.

**E. Eval is wrong (~7, overlaps above)** — Finding 2.
→ Fix: correct the eval, or accept these as a lower ceiling.

**F. Generation completeness — long-form drops sub-points (~5)**
q037, q038, q039, q040, plus partials. The data is retrieved; Flash gives
80%. → Fix: structured/complete generation.

---

## Finding 5 — This corrects my earlier recommendation

Last turn I said the #1 lever was **extraction convergence** (the 577-field-
names problem). The ground truth says that's **only the lever for ~3 queries**
(category D). For THIS corpus, the dominant failures are **retrieval (A),
strategy/mode (B), and chain handling (C)** — all READ-side — plus a **broken
eval (E)**.

The 577-names problem is real and it WILL dominate at scale with messy data,
because clean hand-authored docs are hiding it today. But it is not what's
costing us the construction eval right now. I was about to spend a week on
the wrong thing again. The corpus told a different story than the verdicts.

---

## What will actually make this work — grounded plan

Ordered by measured impact on THIS corpus, then durability at scale.

### Tier 1 — Read-side fixes that recover real, present answers (most of the gain)

1. **Retrieval reliability (category A — ~7 queries).**
   The facts are one passage away but not ranked top-10. The cross-encoder
   rerank helped some and hurt others because it reranks a too-small pool and
   has no notion of "authoritative source for this field." Concrete moves:
   - Rerank the full fused pool, stabilized (or Cohere for reliability).
   - When a query names a field/attribute, boost documents whose
     frontmatter/section actually contains that attribute.
   - Don't let chain-winner logic remove the only doc that has the fact
     (Finding 3) — keep superseded docs eligible for *non-conflicting* fields.

2. **Strategy selection: prefer summary docs over aggregation (category B — ~4).**
   When a "total / cumulative / final / current status" question is asked and
   a certificate/summary/handover document states it directly, read it —
   don't fire Q-mode to sum source docs that may be incomplete. This single
   change fixes q033/q018/q019/q020 and is how the corpus is actually built
   (it ships pre-computed summaries).

3. **Chain delivery to the generator (category C — ~6).**
   When a chain is detected, retrieve and present every member in order, and
   answer the specific point (current / final / what-changed) FIRST, then the
   trail. The chains exist in the data (frontmatter `chain_id` + `parent_doc`);
   we're just not feeding them through completely.

### Tier 2 — The genuine structured-data work (category D + scale)

4. **Entity-role tagging** (q034) and **time-series aggregation** (q036).
5. **Cross-document field convergence** (the 577 problem) — *deferred in
   priority for this eval, but mandatory for Glean-class at scale.* Do it as
   the foundation for the next corpus, not as a fix for this one.

### Tier 3 — Fix the measurement

6. **Correct the eval** for the ~7 broken expected answers (Finding 2), or at
   minimum annotate them so they don't drive design. We cannot converge
   against a ruler that is itself miscalibrated on 14% of items.

---

## The honest bottom line

- The pipeline is not fundamentally broken, and the corpus is not missing the
  data. **Most failures are the read path not delivering present facts, plus
  an eval that's wrong on ~7 items.**
- The thing I was about to rewrite (extraction convergence) matters for SCALE
  and for ~3 queries, not for most of this eval. Reading the corpus prevented
  a second wasted week.
- Realistic, honest target on the *current* eval after Tier 1 + Tier 3:
  **~30-34 correct of the ~43 that are actually answerable** — i.e. we'd be at
  the true ceiling of a correctly-measured eval, not 60% of a miscalibrated one.
- For Glean-class at scale, Tier 2 (convergence + roles + aggregation) is the
  durable investment, and it should be built against a corpus that actually
  stresses it (messy, redundant, large) — not this clean 46-doc set.
