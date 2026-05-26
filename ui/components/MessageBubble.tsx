"use client";

import type { Turn } from "@/lib/chat-state";
import type { ChatStreamEvent } from "@/lib/api";
import { AnswerCard } from "./AnswerCard";

export function MessageBubble({
  turn,
  onFollowUp,
}: {
  turn: Turn;
  /** Forwarded into AnswerCard so its follow-up pills can submit a
   *  new query in the same session. Optional — pills hide when absent. */
  onFollowUp?: (query: string) => void;
}) {
  if (turn.role === "user") {
    return (
      <div
        className="mb-10 flex justify-end"
        data-testid="user-message"
      >
        <div className="max-w-[80%] px-4 py-3 rounded-2xl rounded-tr-md bg-zinc-100 text-[14px] leading-relaxed text-zinc-900 whitespace-pre-wrap">
          {turn.content}
        </div>
      </div>
    );
  }

  if (turn.pending) {
    return (
      <div className="mb-10" data-testid="pending-assistant">
        <div className="flex items-center gap-2 mb-3 text-xs">
          <div className="w-5 h-5 rounded bg-zinc-900 flex items-center justify-center text-white text-[10px] font-semibold">
            K
          </div>
          <span className="text-zinc-500">Thinking…</span>
          {/* Bouncing-dots indicator stays as a "still working" hint
              even while the timeline below ticks. Staggered via
              globals.css `kb-thinking-dot`. */}
          <span className="inline-flex items-center gap-1 ml-1">
            <span className="kb-thinking-dot w-1.5 h-1.5 rounded-full bg-zinc-400" />
            <span className="kb-thinking-dot w-1.5 h-1.5 rounded-full bg-zinc-400" />
            <span className="kb-thinking-dot w-1.5 h-1.5 rounded-full bg-zinc-400" />
          </span>
        </div>
        <PipelineTimeline events={turn.events ?? []} live />
      </div>
    );
  }

  if (turn.error) {
    return (
      <div className="mb-10" data-testid="assistant-error">
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          Pipeline error: <span className="mono text-xs">{turn.error}</span>
        </div>
      </div>
    );
  }

  if (!turn.response) return null;
  return (
    <div className="mb-10">
      <AnswerCard
        response={turn.response}
        events={turn.events ?? []}
        onFollowUp={onFollowUp}
      />
    </div>
  );
}


/** Vertical timeline of pipeline events. Used both live (while pending)
 *  and inside the answer-card "How I answered" inspector (post-done).
 *  Each row shows a human-friendly label + a 1-line summary of the
 *  event's payload + the t_ms timestamp. */
export function PipelineTimeline({
  events,
  live,
}: {
  events: ChatStreamEvent[];
  live?: boolean;
}) {
  if (events.length === 0) {
    return (
      <div className="text-[11px] mono text-zinc-400 pl-7">
        Waiting for the pipeline to start…
      </div>
    );
  }
  return (
    <ol
      className="ml-2 border-l border-zinc-200 pl-4 space-y-1 text-[12px]"
      data-testid="pipeline-timeline"
    >
      {events.map((e, i) => (
        <li
          key={i}
          className="relative flex items-start gap-2"
          data-event={e.event}
        >
          <span
            className={
              live && i === events.length - 1
                ? "absolute -left-[21px] top-[5px] w-2.5 h-2.5 rounded-full bg-zinc-900 ring-2 ring-white animate-pulse"
                : "absolute -left-[21px] top-[5px] w-2.5 h-2.5 rounded-full bg-emerald-500 ring-2 ring-white"
            }
            aria-hidden
          />
          <span className="mono text-zinc-700 flex-shrink-0">
            {labelFor(e.event)}
          </span>
          <span className="text-zinc-500 truncate">
            {summaryFor(e)}
          </span>
          <span className="ml-auto mono text-[10px] text-zinc-400 flex-shrink-0">
            {formatMs(e.data.t_ms)}
          </span>
        </li>
      ))}
    </ol>
  );
}


/** Human-friendly event-type → display-label map. Unknown events fall
 *  through to the raw type string so we don't silently drop new ones
 *  the backend might add. */
function labelFor(eventType: string): string {
  switch (eventType) {
    case "started":              return "started";
    case "context_resolved":     return "context resolved";
    case "intent_classified":    return "intent";
    case "planned":              return "planned";
    case "query_rewritten":      return "rewrites";
    case "retrieving":           return "retrieving";
    case "retrieved":            return "retrieved";
    case "doc_filter_applied":   return "doc filter";
    case "mode_routed":          return "mode routed";
    case "crag_assessed":        return "CRAG";
    case "conflicts_resolved":   return "conflicts";
    case "generating":           return "generating";
    case "generated":            return "generated";
    case "faithfulness_checked": return "faithfulness";
    case "regenerating":         return "regenerating";
    case "citations_enriched":   return "citations";
    case "done":                 return "done";
    case "heartbeat":            return "heartbeat";
    case "error":                return "error";
    default:                     return eventType;
  }
}


/** One-line summary of an event's payload. Reads stage-specific keys
 *  the backend emits — keep in sync with kb/query/orchestrator.py's
 *  emit() call-sites. */
function summaryFor(e: ChatStreamEvent): string {
  const d = e.data as Record<string, unknown>;
  switch (e.event) {
    case "started":              return "";
    case "context_resolved":
      return `"${truncate(String(d.original ?? ""), 35)}" → "${truncate(String(d.resolved ?? ""), 35)}"`;
    case "intent_classified":
      return `${d.label} (${Math.round(Number(d.confidence ?? 0) * 100)}%)`;
    case "planned":              return `mode ${d.mode}`;
    case "query_rewritten":      return `${d.n_variants} variants`;
    case "retrieving":           return "running 6 channels…";
    case "retrieved": {
      const byKind = (d.by_kind ?? {}) as Record<string, number>;
      const breakdown = Object.entries(byKind)
        .map(([k, n]) => `${n} ${k}`).join(" · ");
      return `${d.n_hits} hits ${breakdown ? `(${breakdown})` : ""}`.trim();
    }
    case "doc_filter_applied":
      return `kept ${d.kept} of ${Number(d.kept) + Number(d.dropped)} hits (${d.scope_size} files)`;
    case "mode_routed":
      return `${d.kept} hits via mode ${d.mode}`;
    case "crag_assessed":
      return `${Number(d.score).toFixed(2)} (${d.bypassed ? "bypass" : "gating"} · threshold ${d.threshold})`;
    case "conflicts_resolved": {
      const byRule = (d.by_rule ?? {}) as Record<string, number>;
      return `${d.n_conflicts} ${Object.entries(byRule).map(([k, n]) => `${n} via ${k}`).join(" · ")}`;
    }
    case "generating":
      return d.force_refuse ? "refused (CRAG)" : `LLM call (${d.n_hits_seen} hits)`;
    case "generated":
      if (d.refused) return `refused: ${d.refusal_reason}`;
      return `${d.n_citations} citations`;
    case "faithfulness_checked":
      return `${d.verdict}${d.regenerations ? ` (retry ${d.regenerations})` : ""}`;
    case "regenerating":         return `attempt ${d.attempt}`;
    case "citations_enriched":   return `${d.n_citations} citations enriched`;
    case "done":                 return "answer ready";
    case "heartbeat":            return "—";
    case "error":                return String(d.detail ?? d.type ?? "?");
    default:                     return JSON.stringify(d).slice(0, 60);
  }
}


function formatMs(ms: number | undefined): string {
  if (ms === undefined || ms === null) return "";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}


function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}
