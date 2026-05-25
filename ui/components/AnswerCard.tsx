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

      {/* Body */}
      {refused ? (
        <RefusalBody response={response} />
      ) : (
        <div
          className="text-[15px] leading-[1.75] text-zinc-800"
          data-testid="answer-text"
        >
          {segments.map((seg, i) =>
            seg.kind === "text" ? (
              <span key={i}>{seg.value}</span>
            ) : (
              <sup
                key={i}
                className="cref text-zinc-500 hover:text-zinc-900 font-medium px-0.5 text-[11px] cursor-pointer"
                title={`hit ${seg.hitId}`}
              >
                [{seg.index >= 0 ? seg.index + 1 : "?"}]
              </sup>
            ),
          )}
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
