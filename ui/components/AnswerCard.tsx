"use client";

import { Fragment, useMemo, type ReactNode } from "react";
import { ChevronRight } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSanitize from "rehype-sanitize";
import {
  type ChatResponse,
  type ChatStreamEvent,
  type Citation,
} from "@/lib/api";
import { PipelineTimeline } from "./MessageBubble";

type Props = {
  response: ChatResponse;
  /** Live pipeline events captured during the request (SSE). Shown
   *  inside the "How I answered" inspector so the user can audit which
   *  pipeline stages ran, in what order, and how long each took. */
  events?: ChatStreamEvent[];
  /** Submit a follow-up query in the same session. Wired by the Chat
   *  page to its `handleSubmit`. When omitted (e.g. preview surface),
   *  follow-up pills are hidden. */
  onFollowUp?: (query: string) => void;
};


/** Scroll the right-rail citation card matching `cardId` into view and
 *  briefly flash it so the user sees where their click landed. The DOM
 *  id is set by `CitationsPanel.CitationCardRow` (`citation-card-N`).
 *
 *  Falls back silently when the card isn't mounted (e.g. citation refers
 *  to a hit that wasn't included in the final cards list). */
function scrollAndFlashCitation(cardId: string): void {
  const el = document.getElementById(cardId);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  // CSS animation lives in globals.css under `[data-citation-flash]`.
  el.setAttribute("data-citation-flash", "true");
  window.setTimeout(() => {
    el.removeAttribute("data-citation-flash");
  }, 1500);
}

/**
 * Assistant turn: header pill (grounded / refused) + answer with inline
 * citation badges + "How I answered" collapsible inspector.
 */
export function AnswerCard({ response, events, onFollowUp }: Props) {
  const refused = response.generation.refused;
  const pipelineEvents = events ?? [];
  const followUps = useMemo(
    () => deriveFollowUps(response),
    [response],
  );

  return (
    <div className="mb-2" data-testid="answer-card" data-refused={refused}>
      {/* Header pill */}
      <div className="flex items-center gap-2 mb-4 text-xs">
        <div className="w-5 h-5 rounded bg-zinc-900 flex items-center justify-center text-white text-[10px] font-semibold">
          K
        </div>
        <span className="text-zinc-500">Answer</span>
        {refused ? (
          <span className="flex items-center gap-1 text-amber-700">
            <span className="w-1.5 h-1.5 rounded-full bg-amber-500" />
            <span className="mono">refused · {response.generation.refusal_reason}</span>
          </span>
        ) : (
          <span className="flex items-center gap-1 text-zinc-500">
            <span className="w-1.5 h-1.5 rounded-full bg-zinc-900" />
            <span className="mono">grounded · {(response.crag_score * 100).toFixed(0)}%</span>
          </span>
        )}
      </div>

      {/* R1 — Design 2 conflict-resolution banner. Renders only when
          the orchestrator detected disagreement between chained docs
          (typical case: MSA vs Amendment on payment_terms). Honest
          about what we resolved vs. what we couldn't. */}
      <ConflictResolutionBanner response={response} />

      {/* Body */}
      {refused ? (
        <RefusalBody response={response} />
      ) : (
        <MarkdownAnswer
          answer={response.generation.answer}
          citations={response.generation.citations}
        />
      )}

      {/* Follow-up suggestion pills — context-aware drilldowns derived
          from the response (entity from top hit, intent-keyed prompts).
          Hidden on refusal (the refusal body has its own "Try this"
          guidance) and when no callback is wired. */}
      {!refused && onFollowUp && followUps.length > 0 && (
        <div className="mt-5" data-testid="followup-pills">
          <div className="text-[10px] uppercase tracking-wider text-zinc-400 mb-2">
            Try also
          </div>
          <div className="flex flex-wrap gap-2">
            {followUps.map((q) => (
              <button
                key={q}
                type="button"
                onClick={() => onFollowUp(q)}
                className="text-[12px] text-zinc-700 bg-zinc-50 hover:bg-zinc-100 border border-zinc-200 rounded-full px-3 py-1.5 cursor-pointer transition-colors"
                data-testid="followup-pill"
              >
                {q}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Inspector */}
      <details className="mt-6 rounded-lg border border-zinc-200">
        <summary className="px-4 py-2.5 flex items-center gap-2 text-xs text-zinc-600 hover:text-zinc-900 cursor-pointer">
          <ChevronRight className="w-3.5 h-3.5 text-zinc-400 chev" strokeWidth={1.75} />
          How I answered
          <span className="ml-auto mono text-zinc-400">
            {response.latency_ms}ms · CRAG {response.crag_score.toFixed(2)} ·{" "}
            {response.hits.length} hits
          </span>
        </summary>
        <div className="px-4 pb-4 pt-3 text-xs border-t border-zinc-200 space-y-4">
          {pipelineEvents.length > 0 && (
            <div>
              <div className="text-zinc-500 mb-2 mono">Pipeline trace</div>
              <PipelineTimeline events={pipelineEvents} />
            </div>
          )}
          <div>
            <div className="text-zinc-500 mb-2 mono">Summary</div>
            <div className="grid grid-cols-[110px_1fr] gap-x-3 gap-y-2 mono">
              <div className="text-zinc-400">Mode</div>
              <div className="text-zinc-700">{response.mode ?? "H"}</div>
              <div className="text-zinc-400">Intent</div>
              <div className="text-zinc-700">
                {response.intent ?? "?"}
                {response.intent_confidence != null && (
                  <> · {Math.round(response.intent_confidence * 100)}%</>
                )}
              </div>
              <div className="text-zinc-400">Channels</div>
              <div className="text-zinc-700">
                bm25_chunks · bm25_raptor · dense_chunks · dense_raptor ·
                mentions_exact · sub_entities_rarity (6)
              </div>
              <AutoMergeRow response={response} />
              <div className="text-zinc-400">CRAG</div>
              <div className="text-zinc-700">
                {response.crag_score.toFixed(2)}
                {response.mode && response.mode !== "H"
                  ? ` (bypassed — mode ${response.mode})`
                  : response.crag_score >= 0.5
                    ? " (pass)"
                    : " (refused)"}
              </div>
              <div className="text-zinc-400">Faithfulness</div>
              <div className="text-zinc-700">
                {response.faithfulness_verdict ?? "?"}
                {response.faithfulness_regenerations
                  ? ` · ${response.faithfulness_regenerations} retries`
                  : ""}
              </div>
              <div className="text-zinc-400">Model</div>
              <div className="text-zinc-700">{response.generation.model_id}</div>
              <div className="text-zinc-400">Citations</div>
              <div className="text-zinc-700">
                {response.generation.citations.length} returned
              </div>
            </div>
          </div>
        </div>
      </details>
    </div>
  );
}

/** Inspector row showing the AutoMergingRetriever swap-to-parent stat.
 *  Populated by the orchestrator on every chat response since PR #46
 *  (the LlamaIndex hierarchical-chunking refactor). When the leaf hits
 *  cluster densely under a single parent (≥ merge_threshold, default
 *  0.5), the retriever swaps them for the parent so the generator sees
 *  the full subsection.
 *
 *  Hidden when `auto_merge` is absent (older /chat clients without the
 *  refactor) or when nothing got merged (`leaves_replaced === 0`) — no
 *  point cluttering the inspector with a zero. */
function AutoMergeRow({ response }: { response: ChatResponse }) {
  const am = response.plan?.auto_merge;
  if (!am) return null;
  if (am.leaves_replaced === 0 && am.initial_leaf_hits === 0) return null;
  const merges = Object.entries(am.merges_by_level)
    .map(([level, count]) => `L${level}: ${count}`)
    .join(" · ");
  return (
    <>
      <div className="text-zinc-400">Auto-merge</div>
      <div className="text-zinc-700">
        {am.leaves_replaced > 0 ? (
          <>
            {am.leaves_replaced} leaves → {merges || "0 parents"}
            <span className="text-zinc-400"> · {am.initial_leaf_hits}→{am.final_hit_count} hits</span>
          </>
        ) : (
          <span className="text-zinc-400">
            no merges ({am.initial_leaf_hits} leaf hits, none clustered)
          </span>
        )}
      </div>
    </>
  );
}

/** R1 — banner above the answer body listing every resolved conflict.
 *  Hidden when the orchestrator detected none. One row per (entity,
 *  predicate); the rule that fired is shown as a small chip on the
 *  right ("chain", "status", "authority", "recency", "unresolved").
 *
 *  Goal: make the supersession reasoning legible. A user reading the
 *  answer should be able to see "we picked net-45 from the Amendment
 *  because it supersedes the MSA's net-30 via the chain rule" without
 *  having to dig through the inspector. */
function ConflictResolutionBanner({ response }: { response: ChatResponse }) {
  const conflicts = response.conflict_resolutions ?? [];
  if (conflicts.length === 0) return null;

  return (
    <div
      className="mb-4 rounded-lg border border-amber-200 bg-amber-50/40 px-4 py-3"
      data-testid="conflict-resolutions"
    >
      <div className="text-xs font-medium text-amber-900 mb-2 flex items-center gap-2">
        <span className="w-1.5 h-1.5 rounded-full bg-amber-500" />
        Resolved {conflicts.length === 1 ? "1 conflict" : `${conflicts.length} conflicts`} across doc-chain versions
      </div>
      <div className="space-y-1.5">
        {conflicts.map((c, i) => (
          <div
            key={`${c.entity_id}-${c.predicate}-${i}`}
            className="grid grid-cols-[1fr_auto] gap-3 items-center text-[12px]"
            data-testid="conflict-row"
          >
            <div className="text-zinc-800">
              <span className="mono text-zinc-600">{c.predicate}</span>
              {c.resolution === "unresolved" ? (
                <>
                  {" "}
                  <span className="text-zinc-500">— ambiguous, showing both:</span>{" "}
                  <span className="mono">{c.loser_values.join(" / ")}</span>
                </>
              ) : (
                <>
                  {" picked "}
                  <span className="mono font-medium text-zinc-900">
                    {c.picked_value ?? "—"}
                  </span>
                  {c.loser_values.length > 0 && (
                    <>
                      {" over "}
                      <span className="mono text-zinc-500 line-through decoration-amber-400">
                        {c.loser_values.join(" / ")}
                      </span>
                    </>
                  )}
                </>
              )}
            </div>
            <span className="mono text-[10px] px-1.5 py-0.5 rounded bg-amber-100 text-amber-900 border border-amber-200">
              via {c.resolution}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function RefusalBody({ response }: { response: ChatResponse }) {
  const reason = response.generation.refusal_reason;
  const hits = response.hits || [];
  const hitsByKind = hits.reduce<Record<string, number>>((acc, h) => {
    acc[h.kind] = (acc[h.kind] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div
      className="rounded-lg border border-amber-200 bg-amber-50/40 p-4 text-[14px] leading-relaxed text-zinc-800"
      data-testid="refusal-body"
    >
      <div className="font-medium text-zinc-900 mb-1">
        I can&apos;t answer that with the evidence I have.
      </div>
      <div className="text-zinc-600 mb-3">
        Reason: <span className="mono">{reason ?? "unknown"}</span>.{" "}
        {reason === "no_hits" &&
          "Retrieval returned zero results across all 6 channels. "}
        {reason === "insufficient_evidence" && (
          <>
            The CRAG relevance gate scored the top results at{" "}
            <span className="mono">{(response.crag_score * 100).toFixed(0)}%</span>
            , below the 50% threshold. The retrieved snippets exist but they
            don&apos;t answer your specific question.{" "}
          </>
        )}
        {reason === "parse_error" &&
          "The LLM produced output that couldn't be safely parsed. "}
        {reason === "llm_error" &&
          "The LLM call failed; we'd rather refuse than guess. "}
        {reason === "faithfulness_gate_refused" &&
          "The faithfulness gate flagged the draft answers as not grounded in the snippets; we abstained rather than emit a hallucination. "}
      </div>

      {/* R3 — surface what the system DID find so the user can iterate.
          Even on refusal, retrieval ran and returned hits we can show. */}
      {hits.length > 0 && (
        <div className="rounded border border-amber-100 bg-white/60 px-3 py-2 mb-3 text-xs">
          <div className="text-zinc-600 mb-1.5">
            Retrieval did surface{" "}
            <span className="mono font-medium text-zinc-900">{hits.length}</span>{" "}
            hit{hits.length === 1 ? "" : "s"}{" "}
            <span className="text-zinc-500">
              ({Object.entries(hitsByKind).map(([k, n]) => `${n} ${k}`).join(" · ")})
            </span>{" "}
            but they weren&apos;t a confident match.
          </div>
          <details className="text-zinc-500">
            <summary className="cursor-pointer hover:text-zinc-700 mono">
              show top hit previews
            </summary>
            <div className="mt-2 space-y-1">
              {hits.slice(0, 3).map((h, i) => (
                <div key={i} className="text-[11px]">
                  <span className="mono text-zinc-400">
                    [{i + 1}] {h.kind} · {(h.score * 100).toFixed(0)}%
                  </span>
                  <span className="ml-2 text-zinc-600">
                    {h.snippet.slice(0, 80)}…
                  </span>
                </div>
              ))}
            </div>
          </details>
        </div>
      )}

      <div className="text-zinc-600">
        <span className="font-medium">Try this:</span>{" "}
        {reason === "no_hits" ? (
          <>upload documents related to your question, or try different keywords.</>
        ) : reason === "insufficient_evidence" ? (
          <>
            rephrase with more specific terms (entity names, dates, or doc-type
            keywords like &ldquo;contract&rdquo; or &ldquo;invoice&rdquo;), or
            check the upload page for files that should match.
          </>
        ) : (
          <>rephrase your question or upload more relevant documents.</>
        )}
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// MarkdownAnswer — renders the LLM answer as markdown (headings / lists /
// bold / italics / tables / code) WHILE preserving the inline `[uuid]`
// citation tokens as clickable chips that scroll the right-rail card
// into view.
//
// Strategy: react-markdown handles the block structure. We override the
// `p`, `li`, `td`, `th`, `strong`, `em` renderers — anywhere prose lives —
// to walk their children and replace every string segment's `[uuid]`
// occurrence with a `<CitationChip>` button. The walker is recursive but
// shallow (markdown nesting is bounded).
// ---------------------------------------------------------------------------

function MarkdownAnswer({
  answer,
  citations,
}: {
  answer: string;
  citations: Citation[];
}) {
  // Build a stable hit_id → array-index map once per render. The chip
  // looks up the index for its display label + DOM-id target.
  const indexByShortId = useMemo(() => {
    const m = new Map<string, number>();
    citations.forEach((c, i) => m.set(c.hit_id.slice(0, 8), i));
    return m;
  }, [citations]);

  const withChips = useMemo(
    () => makeChildrenTransformer(citations, indexByShortId),
    [citations, indexByShortId],
  );

  return (
    <div
      className="prose prose-zinc max-w-none text-[15px] leading-[1.75] text-zinc-800
                 prose-headings:font-semibold prose-headings:text-zinc-900
                 prose-h1:text-lg prose-h2:text-base prose-h3:text-sm
                 prose-p:my-3 prose-ul:my-3 prose-ol:my-3 prose-li:my-1
                 prose-strong:text-zinc-900 prose-strong:font-semibold
                 prose-code:text-[13px] prose-code:bg-zinc-100 prose-code:px-1
                 prose-code:py-0.5 prose-code:rounded prose-code:before:content-none
                 prose-code:after:content-none
                 prose-table:text-[13px] prose-th:bg-zinc-50
                 prose-th:px-2 prose-th:py-1.5 prose-td:px-2 prose-td:py-1.5
                 prose-th:border prose-th:border-zinc-200
                 prose-td:border prose-td:border-zinc-200"
      data-testid="answer-text"
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeSanitize]}
        components={{
          p: ({ children }) => <p>{withChips(children)}</p>,
          li: ({ children }) => <li>{withChips(children)}</li>,
          td: ({ children }) => <td>{withChips(children)}</td>,
          th: ({ children }) => <th>{withChips(children)}</th>,
          strong: ({ children }) => <strong>{withChips(children)}</strong>,
          em: ({ children }) => <em>{withChips(children)}</em>,
          h1: ({ children }) => <h1>{withChips(children)}</h1>,
          h2: ({ children }) => <h2>{withChips(children)}</h2>,
          h3: ({ children }) => <h3>{withChips(children)}</h3>,
        }}
      >
        {answer}
      </ReactMarkdown>
    </div>
  );
}


/** Build a `(children: ReactNode) => ReactNode` walker that replaces
 *  inline `[uuid]` or `[uuid1, uuid2, …]` patterns inside string
 *  children with `<CitationChip>` buttons. Non-string children
 *  (already-rendered ReactElements) pass through unchanged.
 *
 *  The bracket can carry a SINGLE UUID OR multiple comma-separated
 *  UUIDs — the gen-LLM often groups its sources that way when one
 *  claim is supported by multiple snippets. We render one chip per
 *  UUID, separated by a thin space, so the user sees [1] [2] [3]
 *  instead of a raw `[uuid, uuid, uuid]` dump in the prose.
 *
 *  Short forms (e.g. `[a8b21618]`) and full UUIDs both supported —
 *  the gen prompt asks for full UUIDs but older / partial-quote
 *  variants land too. */
function makeChildrenTransformer(
  citations: Citation[],
  indexByShortId: Map<string, number>,
): (children: ReactNode) => ReactNode {
  // One UUID-ish token — match the canonical RFC-4122 form (8-4-4-4-12
  // with hyphens) OR an 8-char short id. The full form is listed
  // FIRST in the alternation so the regex engine greedily consumes
  // the whole UUID instead of matching just the leading 8 hex chars
  // (the trailing 12-hex segment doesn't have a leading dash, so a
  // looser pattern would split one UUID into two false "tokens").
  const UUID_FULL = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
  const UUID_SHORT = "[0-9a-f]{8}";
  const UUID_TOKEN = `(?:${UUID_FULL}|${UUID_SHORT})`;
  // Bracket containing ONE or more UUIDs separated by `,` + whitespace.
  const CITE_GROUP_RE = new RegExp(
    `\\[(${UUID_TOKEN}(?:\\s*,\\s*${UUID_TOKEN})*)\\]`,
    "gi",
  );
  const UUID_RE = new RegExp(UUID_TOKEN, "gi");

  function replaceInString(s: string, keyPrefix: string): ReactNode[] {
    const out: ReactNode[] = [];
    let last = 0;
    let n = 0;
    for (const m of s.matchAll(CITE_GROUP_RE)) {
      if (m.index === undefined) continue;
      if (m.index > last) {
        out.push(
          <Fragment key={`${keyPrefix}-t${n++}`}>{s.slice(last, m.index)}</Fragment>,
        );
      }
      // Split the inside-of-brackets into one or more UUID tokens.
      const ids = m[1].match(UUID_RE) ?? [];
      ids.forEach((raw, j) => {
        if (j > 0) {
          // Tight visual gap between chips for the multi-citation form.
          out.push(<span key={`${keyPrefix}-sp${n++}`}>&thinsp;</span>);
        }
        const shortId = raw.slice(0, 8);
        const index = indexByShortId.get(shortId) ?? -1;
        out.push(
          <CitationChip
            key={`${keyPrefix}-c${n++}`}
            index={index}
            citation={index >= 0 ? citations[index] : undefined}
          />,
        );
      });
      last = m.index + m[0].length;
    }
    if (last < s.length) {
      out.push(<Fragment key={`${keyPrefix}-t${n++}`}>{s.slice(last)}</Fragment>);
    }
    return out;
  }

  return function walk(children: ReactNode): ReactNode {
    if (typeof children === "string") return replaceInString(children, "s");
    if (Array.isArray(children)) {
      return children.map((c, i) =>
        typeof c === "string" ? (
          <Fragment key={i}>{replaceInString(c, `a${i}`)}</Fragment>
        ) : (
          <Fragment key={i}>{c}</Fragment>
        ),
      );
    }
    return children;
  };
}


/** Derive up to 3 contextual follow-up prompts from the response.
 *  Steered by (intent, mode, top-hit filename, conflict presence) so
 *  the chips relate to what the user just asked — not generic
 *  "what changed" / "what contradicts" prompts that are jarring on
 *  one-shot factoid lookups (e.g. "what is the salary in the offer
 *  letter" doesn't benefit from "what other documents contradict
 *  this answer?" — there's nothing chain-able about a salary).
 *
 *  Kept fully client-side (no backend "next questions" call) so the
 *  pills appear instantly when the answer renders. */
function deriveFollowUps(response: ChatResponse): string[] {
  const out: string[] = [];
  const hits = response.hits ?? [];
  const intent = response.intent ?? "";
  const mode = response.mode ?? "";
  const hasConflicts = (response.conflict_resolutions ?? []).length > 0;

  // Pull the top hit's filename stem (e.g. "employment-offer-letter")
  // for personalized prompts. If the answer cites multiple files we
  // can still drill into the top one.
  const topFile = hits.find((h) => {
    const md = h.metadata as Record<string, unknown>;
    return typeof md.file_name === "string" && md.file_name.length > 0;
  });
  const fileStem = topFile
    ? ((topFile.metadata as { file_name: string }).file_name).replace(/\.[^.]+$/, "")
    : null;

  // -----------------------------------------------------------------
  // 1. Q-mode aggregations → drilldown into the same data, not the
  //    "what contradicts" generic chip.
  // -----------------------------------------------------------------
  if (mode === "Q") {
    const aggMd = hits[0]?.metadata as Record<string, unknown> | undefined;
    const cols = Array.isArray(aggMd?.column_names) ? aggMd!.column_names as string[] : [];
    if (cols.length > 0) {
      out.push(`Break this down by file`);
      out.push(`Show me the raw rows that produced this`);
    } else {
      out.push("Break this down by category");
      out.push("Show me outliers and anomalies");
    }
  }

  // -----------------------------------------------------------------
  // 2. Conflict-aware: only suggest "what contradicts" when the chain
  //    actually CONTAINS a chained doc (so the question is meaningful).
  //    For one-shot lookups (offer letter, NDA, resume) skip it.
  // -----------------------------------------------------------------
  if (hasConflicts) {
    out.push("Show every superseded value the answer skipped");
  } else if (mode !== "Q") {
    // Only suggest doc-chain probes if the response actually involves
    // a chained doc (e.g. MSA + amendment). Heuristic: hit filenames
    // that suggest versioning (amendment, addendum, v2, revised, …).
    const hasVersioned = hits.some((h) => {
      const name = ((h.metadata as { file_name?: string })?.file_name || "").toLowerCase();
      return /amend|addendum|revised|\bv\d|\bversion\b/.test(name);
    });
    if (hasVersioned) {
      out.push("Which version is currently in effect?");
    }
  }

  // -----------------------------------------------------------------
  // 3. Intent-keyed drilldown — tailored per intent.
  // -----------------------------------------------------------------
  if (intent === "factoid" && fileStem) {
    out.push(`What else is in ${fileStem}?`);
  } else if (intent === "summarize" && fileStem) {
    out.push(`Which document is most authoritative on this?`);
  } else if (intent === "find" || intent === "search") {
    out.push("Group these results by document type");
  } else if (intent === "compare") {
    out.push("Highlight the differences in a table");
  } else if (intent === "explain" && fileStem) {
    out.push(`Trace this back to the primary source in ${fileStem}`);
  } else if (intent === "list" || intent === "inventory") {
    out.push("Filter this list to the most recent additions");
  } else if (intent === "aggregation") {
    // Already partially handled by mode='Q' branch above; redundant
    // chip would dedupe.
  } else if (fileStem) {
    out.push(`What else is in ${fileStem}?`);
  }

  // -----------------------------------------------------------------
  // 4. Last-resort drilldown into the top file.
  // -----------------------------------------------------------------
  if (out.length < 2 && fileStem) {
    out.push(`Summarize ${fileStem} in more detail`);
  }

  // Dedupe while preserving order; cap to 3.
  return Array.from(new Set(out)).slice(0, 3);
}


function CitationChip({
  index,
  citation,
}: {
  index: number;
  citation: Citation | undefined;
}) {
  const superseded = !!citation?.superseded;
  const cardId = index >= 0 ? `citation-card-${index}` : null;
  return (
    <button
      type="button"
      onClick={() => cardId && scrollAndFlashCitation(cardId)}
      className={
        superseded
          ? "cref text-amber-700 hover:text-amber-900 font-medium px-0.5 text-[11px] cursor-pointer line-through decoration-amber-400 align-super"
          : "cref text-zinc-500 hover:text-zinc-900 font-medium px-0.5 text-[11px] cursor-pointer align-super"
      }
      title={
        superseded
          ? `Citation ${index + 1} — superseded; click to view source`
          : `Citation ${index + 1} — click to view source`
      }
      data-superseded={superseded || undefined}
      data-citation-index={index}
      aria-label={`Open citation ${index + 1}`}
    >
      [{index >= 0 ? index + 1 : "?"}]
    </button>
  );
}
