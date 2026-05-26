"use client";

import { ChevronRight } from "lucide-react";
import { segmentAnswer, type ChatResponse } from "@/lib/api";

type Props = { response: ChatResponse };

/**
 * Assistant turn: header pill (grounded / refused) + answer with inline
 * citation badges + "How I answered" collapsible inspector.
 */
export function AnswerCard({ response }: Props) {
  const refused = response.generation.refused;
  const segments = refused
    ? []
    : segmentAnswer(response.generation.answer, response.generation.citations);

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
        <div
          className="text-[15px] leading-[1.75] text-zinc-800"
          data-testid="answer-text"
        >
          {segments.map((seg, i) => {
            if (seg.kind === "text") {
              return <span key={i}>{seg.value}</span>;
            }
            // Annotate the inline [N] badge with the underlying
            // citation's status so the user can see at-a-glance which
            // numbers point at a superseded source.
            const cit = response.generation.citations[seg.index];
            const superseded = !!cit?.superseded;
            return (
              <sup
                key={i}
                className={
                  superseded
                    ? "cref text-amber-700 hover:text-amber-900 font-medium px-0.5 text-[11px] cursor-pointer line-through decoration-amber-400"
                    : "cref text-zinc-500 hover:text-zinc-900 font-medium px-0.5 text-[11px] cursor-pointer"
                }
                title={
                  superseded
                    ? `hit ${seg.hitId} — superseded; newer version cited above`
                    : `hit ${seg.hitId}`
                }
                data-superseded={superseded || undefined}
              >
                [{seg.index >= 0 ? seg.index + 1 : "?"}]
              </sup>
            );
          })}
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
        <div className="px-4 pb-4 pt-3 text-xs border-t border-zinc-200">
          <div className="grid grid-cols-[110px_1fr] gap-x-3 gap-y-2 mono">
            <div className="text-zinc-400">Mode</div>
            <div className="text-zinc-700">H (hybrid)</div>
            <div className="text-zinc-400">Rewrites</div>
            <div className="text-zinc-700">
              original · step_back · hyde · query2doc (4 variants)
            </div>
            <div className="text-zinc-400">Channels</div>
            <div className="text-zinc-700">
              bm25_chunks · bm25_raptor · dense_chunks · dense_raptor ·
              mentions_exact · atomic_units_rarity (6)
            </div>
            <div className="text-zinc-400">CRAG</div>
            <div className="text-zinc-700">
              {response.crag_score >= 0.5
                ? `confident (${response.crag_score.toFixed(2)} ≥ 0.5)`
                : `low confidence (${response.crag_score.toFixed(2)} < 0.5) → refused`}
            </div>
            <div className="text-zinc-400">Model</div>
            <div className="text-zinc-700">{response.generation.model_id}</div>
            <div className="text-zinc-400">Citations</div>
            <div className="text-zinc-700">
              {response.generation.citations.length} returned
            </div>
          </div>
        </div>
      </details>
    </div>
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
  return (
    <div
      className="rounded-lg border border-amber-200 bg-amber-50/40 p-4 text-[14px] leading-relaxed text-zinc-800"
      data-testid="refusal-body"
    >
      <div className="font-medium text-zinc-900 mb-1">
        I can&apos;t answer that with the evidence I have.
      </div>
      <div className="text-zinc-600">
        Reason: <span className="mono">{reason ?? "unknown"}</span>.{" "}
        {reason === "no_hits" && "Retrieval returned zero results across all channels. "}
        {reason === "insufficient_evidence" &&
          "The CRAG gate scored the top results below the 0.5 threshold. "}
        {reason === "parse_error" &&
          "The LLM produced output that couldn't be safely parsed. "}
        {reason === "llm_error" &&
          "The LLM call failed; we'd rather refuse than guess. "}
        Try uploading more relevant documents or rephrasing your question.
      </div>
    </div>
  );
}
