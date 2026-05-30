# The system in plain English

Written after reading the actual code (`workers/tasks.py`,
`query/orchestrator.py`) — not from memory. Two halves: how a document
gets in (ingestion), and how a question gets answered (query). Then: how
far this is from the best-in-world systems we want to match, and what to
do next.

---

## PART 1 — What happens when a document comes in

A file is uploaded → stored in object storage (MinIO) → a row is created
→ a chain of 10 background steps runs, each handing off to the next. Here
is each step and *why* it exists.

**1. Parse — turn the file into text.**
We read the raw bytes and pick a parser based on file type. Digital PDFs,
scans, images, emails, spreadsheets each need different handling. The
primary parser is Docling; if the text it produces looks low-quality (e.g.
a bad scan), we automatically re-try with Gemini OCR. Output: the document's
text, page by page.
*Why:* everything downstream needs clean text; garbage text here poisons
everything.

**2. Split (chunk) — cut the text into passages.**
We break the document into overlapping, nested passages (big sections →
medium → small). Right now the chunk sizes are chosen by *file type*, not
document type, because at this point we don't yet know what kind of
document it is.
*Why:* retrieval works on passages, not whole documents. Nesting lets us
match at the right granularity.

**3. Contextualize — give each passage a sentence of context.**
For each passage we add a short prefix describing where it sits in the
document ("This is from the indemnity section of the EPC contract between
Acme and Mahalaxmi…"). This is the Anthropic "contextual retrieval"
technique.
*Why:* a bare passage like "the cap is ₹2.2 crore" is ambiguous; with
context it becomes findable and answerable.

**4. Embed — turn each passage into a vector.**
We convert every contextualized passage into a numeric vector (Gemini
embeddings) so we can search by meaning, not just keywords.
*Why:* lets "foundation issues" match "concrete delivery failed, QC poor"
even with no shared words.

**5. Build the summary tree (RAPTOR) — summarize clusters of passages.**
We cluster related passages and write short summaries of each cluster,
then summaries of summaries — a small tree on top of the document.
*Why:* broad questions ("summarize this project") are answered better from
summaries than from scattered low-level passages.

**6. Extract mentions — find the names.**
We scan the text for named things — people, organizations, places, laws,
dates — and record where each appears.
*Why:* powers "which documents mention X" and feeds identity resolution.

**7. Extract fields + tables (the big one) — turn prose into structured data.**
ONE LLM call does three things at once for the document:
  (a) **classifies** what kind of document it is (change order, RFI,
      daily report, contract…);
  (b) **pulls out scalar facts** as field/value pairs (contract_value =
      ₹44.1 cr, effective_date = 2025-03-08) → stored in `proposed_fields`;
  (c) **pulls out repeating rows** (each transaction, each clause, each
      line item) as structured sub-entities.
Crucially, before extracting, it looks up the field names already used by
*other documents of the same type* and tells the LLM "prefer these names."
*Why:* this is what makes the system answer specific-field questions and do
aggregation. The name-reuse step is there to stop every document inventing
its own field names.

**8. Build the entity hierarchy + detect doc chains.**
We connect the structured pieces into a parent/child tree (a bank statement
*contains* transactions), extract relationships (subject–predicate–object),
and detect document chains (this change order amends that contract; Rev B
supersedes Rev A).
*Why:* powers multi-hop questions and "what's the current version" questions.

**9. Resolve identity — merge spelling variants.**
"Aakash Cons.", "Aakash Constructions", "Aakash Constructions Pvt Ltd"
become one canonical entity. We use exact-match, then vector similarity,
then an LLM judge for borderline cases.
*Why:* without this, "how many distinct vendors" and "everything about
Aakash" both break.

**10. Build the graph — connect entities across documents.**
Final step wires entities into a graph (who co-occurs with whom, who relates
to what) across the whole corpus.
*Why:* powers graph-traversal questions ("which contracts share an
arbitration venue").

The net result: from one uploaded file we produce passages, vectors, a
summary tree, named mentions, structured fields, structured rows, an entity
hierarchy, doc chains, and graph edges. That's the "knowledge" the query
side draws on.

---

## PART 2 — What happens when a question is asked

**1. Resolve context — understand follow-ups.**
If the question says "what about *its* payment terms", we use the chat
history to rewrite it into a standalone question.
*Why:* multi-turn chat needs the pronouns resolved before anything else.

**2. Plan — figure out what kind of question this is.**
We classify the intent (a specific fact? a summary? an aggregation? a
chain question? adversarial?) and pick a retrieval "mode" suited to it.
*Why:* "sum all contract values" and "who is the architect" need very
different handling.

**3. Retrieve — search six ways at once.**
We run six searches in parallel: keyword-over-passages, keyword-over-
summaries, meaning-over-passages, meaning-over-summaries, exact-mention
match, and rare-term match. We merge the six ranked lists into one
(reciprocal-rank fusion) and keep the top ~30.
*Why:* no single search method catches everything; combining them catches
needles, paraphrases, and exact names alike.

**4. Rerank — re-sort the top candidates.**
A second, more careful model re-scores the top ~30 against the question and
keeps the best ~10.
*Why:* the first-pass search is keyword/vector-biased; a cross-encoder
reads question and passage together and is far more accurate about
relevance.

**5. Relevance gate (CRAG) — refuse if the evidence is weak.**
A small model judges whether the kept passages actually answer the
question. If confidence is too low, we refuse instead of guessing.
*Why:* refusing is correct behavior; a confident wrong answer is the worst
outcome.

**6. Conflict resolution — handle disagreement.**
If documents disagree on the same fact, we apply rules (later revision wins,
higher-authority source wins, most recent wins) and tell the answer-writer
which value is authoritative.
*Why:* naive RAG picks one source arbitrarily; we resolve it deliberately.

**7. Generate — write the cited answer.**
The LLM writes the answer using only the kept passages, citing each claim.
For aggregation questions, a separate path turns the question into a safe
database query and returns the computed number.
*Why:* grounded, cited answers are the whole point.

**8. Faithfulness gate — check the answer is grounded.**
We verify each sentence is supported by the sources; if not, we regenerate
once or twice, then downgrade to "low confidence" rather than ship a
hallucination.
*Why:* last line of defense against made-up facts.

**9. Persist — record everything.**
The full turn (question, retrieved passages, answer, citations, scores) is
written to immutable logs so any answer is reproducible.
*Why:* auditability is a hard requirement.

---

## PART 3 — Where this sits vs the best in the world

The original brief (`problem_statement.md`) sets the bar explicitly:
**a domain-agnostic enterprise KB that discovers structure from data, with
no upfront schema** — and names the competitors: Glean, Hebbia, NotebookLM,
Onyx.

What's genuinely strong here, and rare even among those:
- Structured extraction + aggregation over emergent fields (most "chat with
  docs" tools can't sum anything).
- Conflict resolution as a first-class step.
- Doc-chain / supersession awareness.
- Cited, refuse-or-answer discipline with an audit trail.

The honest gap to "best in the world" is **not the query pipeline** — that
machinery is already sophisticated. The gap is that **the knowledge the
ingestion side produces doesn't converge.** The system is designed to let
schema *emerge* from data (a deliberate, correct design choice). But emergent
fields and entities don't automatically *agree with each other* across
documents, and that's where answers leak.

The clearest evidence, measured on our 46-document construction corpus:
- **577 distinct field names for 996 extracted facts.** Nearly every
  document invents its own vocabulary.
- The name-reuse safeguard in step 7 only reuses names *within a document
  type that already has other documents.* **18 of our 28 document types have
  only one document** — so for those, there's nothing to reuse and the
  vocabulary fragments by design.
- Universal concepts (date, amount, party, location) appear in every
  document type under different names and are never reconciled across types.

This is the core realization, and it's subtle: **the thing that makes the
system special (schema emerges from data) is also the thing that's hurting
answers (the emerged schema doesn't line up).** Glean-class quality comes
from closing exactly this loop — letting structure emerge *and then
converge* to a shared, queryable vocabulary.

A second, smaller gap: the answer-writer (Gemini Flash) gives 80%-complete
answers on chain and long-form questions — it states the gist but drops
sub-items. That's a read-side fix (force complete enumeration, or use a
stronger model for those question types).

---

## PART 4 — What to do next (in priority order)

**The headline:** invest in making emergent structure *converge*. That is
the single move that most closes the gap to Glean-class quality, and it's
upstream of everything the query side does.

1. **Cross-type canonical vocabulary (the real fix for the 577 problem).**
   Today name-reuse is per-document-type and fails on one-off types. Add a
   *global* concept vocabulary for universal fields (date, amount, party,
   location, person, status) that every document type maps onto, plus
   convention-based naming so even a first-of-its-kind document uses
   standard names. This is the highest-leverage change and it's durable —
   it fixes future uploads, not just today's corpus.

2. **Promote the obvious "needle" facts to real fields.** Always extract
   site address, key people (safety officer, PM, architect), headline
   values, key dates as structured fields. Turns a class of questions from
   "hunt through passages" into one-row lookups.

3. **Tag entity roles.** Classify each organization/person (sub-contractor,
   vendor, client, consultant, government body). Unlocks "how many distinct
   sub-contractors" cleanly.

4. **Strengthen doc-chain linking.** Link change-orders to their contract,
   variations into a value chain, incident initial→investigation→corrective.
   Makes "current value / latest version" deterministic instead of relying
   on the answer-writer to piece it together.

5. **Force complete answers on chain/long-form questions** (read side) —
   structured generation that lists every item, before considering a
   stronger model.

6. **Broaden conflict detection beyond doc-chains** (read side) — right now
   it only fires for documents already in a chain, so two independent
   purchase orders or RFIs that disagree never get reconciled.

Steps 1–4 are ingestion (write-path) and durable. Steps 5–6 are query
(read-path). Do the write-path work first: it's the bigger lever, it
survives re-ingestion, and it shrinks the read-path problem before we spend
anything on a bigger model.

**One thing to decide together:** the tension in Part 3 is a genuine design
choice, not a bug. "Let schema emerge" vs "enforce a converged vocabulary"
are in tension. The best systems do *both* — emerge, then converge. Agreeing
that convergence is now a first-class goal is the real decision in front of us.
