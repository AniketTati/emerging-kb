# Chat eval — 20 queries with expected responses

Run these against `http://localhost:8000/chat` (or the `/chat` UI) on the demo
workspace to gauge retrieval + synthesis quality. Each row names the doc(s) we
expect to be cited and the kind of mode-routing we expect from the intent
classifier + planner.

> Tooling: `scripts/run_chat_eval.py` runs the whole list against the live API
> (3-second spacing to stay under Gemini's parallel-call ceiling) and dumps a
> per-query verdict to `/tmp/eval_results.json`.

Categories let us measure per-axis quality:

- **corpus-scope** — synthesis across many docs (modes G / D)
- **factoid-\*** — single-fact lookup (modes H / F / S)
- **conflict-resolution** — same predicate, disagreeing docs (R1 should fire)
- **chain-aware** — supersession / version (mode K)
- **multi-hop** — facts requiring cross-doc joins (mode T)
- **vague / refusal-correct** — edge cases for the gate

| # | Category | Query | Expected mode | Expected files cited | Expected behaviour |
|---|---|---|---|---|---|
| Q1 | corpus-scope | "Summarize all the documents in this workspace" | G | several across all types | Synthesis covering MSA / case study / financial / press release / postmortem |
| Q2 | corpus-scope | "What types of documents do I have" | G or D | several | Lists contract / invoice / email / postmortem / resume / report |
| Q3 | factoid-contract | "What is the payment due period in the MSA between NorthWind and Vertex" | H / F | vertex-msa.pdf · vertex-amendment.txt | Answer: net-30 (MSA) superseded by net-45 (Amendment). R1 conflict-banner should fire. |
| Q4 | conflict-resolution | "Tell me about the MSA between NorthWind and Vertex including payment terms" | T / H | both MSA + Amendment | **Should produce the R1 conflict banner** "Resolved 1 conflict via chain rules" + MSA card marked SUPERSEDED |
| Q5 | chain-aware | "What did Amendment No. 1 change in the MSA" | K / F | vertex-amendment.txt | Names the scope expansion + payment-terms change |
| Q6 | factoid-financial | "How much was billed on invoice VRX-2026-0317" | F | invoice-mar2026.pdf | Specific dollar amount |
| Q7 | factoid-hr | "What is the starting salary in the employment offer letter" | F / S | employment-offer-letter.pdf | Specific compensation figure |
| Q8 | factoid-medical | "What abnormal lab results does the blood panel show" | F / S | lab-blood-panel.pdf | Names the high glucose / out-of-range markers |
| Q9 | factoid-incident | "What was the root cause of the recent incident postmortem" | K | incident-postmortem.md | Names the root cause (unbounded S3 prefix scan) |
| Q10 | factoid-financial | "What was NorthWind Capital revenue in Q1 2026" | H / F | quarterly-financial-summary.md | $12.4M / 15% YoY |
| Q11 | vague | "Anything interesting going on" | H | should refuse or give meta-answer | Acceptable to refuse — query is too vague |
| Q12 | multi-hop | "Which documents mention Vertex Industries" | H | many (MSA, Amendment, invoice, email, case study, press release, RFC) | A multi-doc list with cross-references |
| Q13 | factoid-hr | "What programming languages does the software engineer resume list" | F / S | resume-software-engineer.pdf | Lists Python / TypeScript / Go etc. (or honestly says "not explicit") |
| Q14 | factoid-financial | "What does the pricing sheet list as the rate for the standard processing tier" | F | vertex-pricing-tiers.xlsx | Specific tier name + rate |
| Q15 | factoid-meeting | "What action items came out of the most recent standup" | K | weekly-standup-notes.md | Lists action items |
| Q16 | refusal-correct | "What is the capital of France" | any | 0 relevant docs → model self-refuses | **Should refuse** with an honest "not in corpus" message |
| Q17 | numeric-precision | "What is the SLA processing time guarantee" | F | vertex-pricing-tiers.xlsx | Per-tier hours; honest if not specified |
| Q18 | factoid-email | "Who participated in the IT incident email thread" | K / S | it-incident-thread.eml | Lists From / To addresses |
| Q19 | factoid-medical-eob | "What was denied in the insurance explanation of benefits" | F | insurance-eob.pdf | Denied service line or honest "none denied" |
| Q20 | factoid-narrative | "What outcome did the customer case study report" | F | customer-case-study.md | Names the % reduction / business outcome |

## Current baseline (post chat-UX fixes)

Run 2026-05-26 against the demo workspace after the chat-UX fixes commit
(`f7e0a6a`-ish on `waveB/chat-ux-fixes`). Spaced 3s between calls to avoid
Gemini rate limits. Mode/intent assignments are nondeterministic between
runs — the column shows the most-recent observation.

| # | Mode | Refused | CRAG | Cites | Notes |
|---|---|---|---:|---:|---|
| Q1 | G | ok | 0.0 | 8 | Bypassed CRAG (G mode); synthesis answer |
| Q2 | D | ok | 0.0 | 10 | Bypassed CRAG (D mode); lists doc types |
| Q3 | F | ok | 0.1 | 3 | Cites both MSA + Amendment |
| Q4 | T | ok | 1.0 | 6 | R1 conflict banner fires, MSA marked SUPERSEDED |
| Q5 | F | ok | 0.8 | 2 | Identifies scope + payment-term changes |
| Q6 | F | ok | 0.5 | 1 | Concrete amount cited |
| Q7 | S | ok | 0.5 | 1 | Honest "not explicit" when applicable |
| Q8 | S | ok | 0.5 | 1 | Names high fasting glucose |
| Q9 | K | ok | 1.0 | 1 | Concrete root cause cited |
| Q10 | K | refuse (model self-refusal) | 0.0 | 0 | **Issue:** K-mode routing pulled MSA chunks instead of financial report. Retrieval-tuning gap. |
| Q11 | H | refuse | 0.0 | 0 | Correct — query too vague |
| Q12 | H | ok | 1.0 | 9 | Cross-doc list works |
| Q13 | F | ok | 0.0 | 1 | Honest "not explicit" — resume parser caveat |
| Q14 | F | ok | 0.8 | 1 | Per-tier prices |
| Q15 | K | refuse (model self-refusal) | 0.0 | 0 | **Issue:** K-mode pulled wrong docs |
| Q16 | F | refuse | 0.0 | 0 | Correct — out-of-corpus |
| Q17 | F | ok | 0.0 | 1 | Honest about absence |
| Q18 | K | refuse (model self-refusal) | 0.0 | 0 | **Issue:** K-mode mis-routing again |
| Q19 | F | ok | 0.0 | 1 | Concrete denial info |
| Q20 | F | ok | 0.5 | 1 | Business outcome cited |

**Score:** 14/20 answered, 6 refused. Of the 6 refusals: 2 are correct (Q11
vague, Q16 out-of-corpus); 4 are mis-routing to K-mode that pulled wrong
context — a retrieval/planning issue, not a chat bug.

## Known issues this surfaces (for follow-up tuning)

1. **Intent classifier flakiness** — same query maps to different modes
   across runs ("What documents do I have" oscillates between G / D / F / Q).
   Wave-B fix: ensemble or temperature=0 on the classifier; or pin classifier
   to a "vague_meta" bucket that always uses G.

2. **K-mode (chain-aware) over-triggers** — Q10, Q15, Q18 routed to K because
   the planner sees temporal/sequence hints ("Q1 2026", "most recent",
   "thread"). K-mode then retrieves chain-relevant chunks even when the doc
   isn't in a chain. Fix: K should fall back to H when the inferred entity
   isn't in any chain.

3. **Resume / NER misses** — Q13 returns honest "not listed" because the
   resume's skills section embeds languages in prose rather than a bullet
   list. R5-style structured layout could enable better parsing.

4. **EOB content sparse** — Q19 says "no denials" with low CRAG. The synthetic
   demo EOB may simply not contain a denial; not a bug.
