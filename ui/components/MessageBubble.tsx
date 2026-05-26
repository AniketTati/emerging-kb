"use client";

import type { Turn } from "@/lib/chat-state";
import { AnswerCard } from "./AnswerCard";

export function MessageBubble({ turn }: { turn: Turn }) {
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
        </div>
        {/* Bouncing-dots indicator — staggered via globals.css to
            avoid the all-pulse-in-unison illusion of being frozen. */}
        <div className="flex items-center gap-1.5 text-zinc-400 text-sm pl-1">
          <span className="kb-thinking-dot w-2 h-2 rounded-full bg-zinc-500" />
          <span className="kb-thinking-dot w-2 h-2 rounded-full bg-zinc-500" />
          <span className="kb-thinking-dot w-2 h-2 rounded-full bg-zinc-500" />
        </div>
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
      <AnswerCard response={turn.response} />
    </div>
  );
}
