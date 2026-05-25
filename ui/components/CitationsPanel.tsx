"use client";

import { FileText } from "lucide-react";
import { useChat } from "@/lib/chat-state";
import type { Citation, Hit } from "@/lib/api";

/**
 * Right-rail citation cards. Shows citations from the most-recent
 * assistant turn (in Wave A we only ever have one in-flight conversation;
 * Wave B persists multiple).
 */
export function CitationsPanel() {
  const { state } = useChat();
  const lastAssistant = [...state.turns].reverse().find(
    (t) => t.role === "assistant" && t.response,
  );

  if (!lastAssistant || !lastAssistant.response) {
    return (
      <aside className="w-[360px] flex-shrink-0 border-l border-zinc-200 bg-zinc-50/50 flex flex-col min-h-0">
        <header className="px-5 py-3 border-b border-zinc-200 flex items-center justify-between flex-shrink-0 bg-white">
          <div className="text-sm font-medium text-zinc-900">Sources</div>
        </header>
        <div className="flex-1 flex items-center justify-center text-xs text-zinc-400 mono p-8 text-center">
          Citations will appear here after your first question.
        </div>
      </aside>
    );
  }

  const { generation, hits } = lastAssistant.response;
  const cards = generation.citations.length > 0 ? generation.citations : [];
  return (
    <aside className="w-[360px] flex-shrink-0 border-l border-zinc-200 bg-zinc-50/50 flex flex-col min-h-0">
      <header className="px-5 py-3 border-b border-zinc-200 flex items-center justify-between flex-shrink-0 bg-white">
        <div className="text-sm font-medium text-zinc-900">
          Sources{" "}
          <span className="text-zinc-400 ml-1 font-normal">{cards.length}</span>
        </div>
        {hits.length > 0 && (
          <div className="text-[11px] mono text-zinc-500">
            {hits.length} retrieved
          </div>
        )}
      </header>
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {cards.length === 0 ? (
          <div className="text-xs text-zinc-500 mono">
            No citations were returned for this answer.
          </div>
        ) : (
          cards.map((c, i) => <CitationCardRow key={c.hit_id} c={c} index={i + 1} />)
        )}

        {hits.length > 0 && cards.length < hits.length && (
          <details className="mt-2 rounded border border-zinc-200 bg-white">
            <summary className="px-3 py-2 text-xs text-zinc-600 hover:text-zinc-900 cursor-pointer">
              {hits.length - cards.length} more retrieved (not cited)
            </summary>
            <div className="px-3 pb-3 pt-1 space-y-2">
              {hits.slice(cards.length).map((h) => (
                <HitRow key={h.id} h={h} />
              ))}
            </div>
          </details>
        )}
      </div>
    </aside>
  );
}

function CitationCardRow({ c, index }: { c: Citation; index: number }) {
  return (
    <div
      className="rounded-lg border border-zinc-200 bg-white p-3 space-y-2"
      data-testid="citation-card"
    >
      <div className="flex items-center justify-between text-xs">
        <span className="flex items-center gap-2 text-zinc-600">
          <span className="mono text-zinc-900">[{index}]</span>
          <FileText className="w-3.5 h-3.5 text-zinc-500" strokeWidth={1.75} />
          <span className="mono text-[11px]">{c.kind}</span>
        </span>
        <span className="mono text-[11px] text-zinc-500">
          {(c.score * 100).toFixed(0)}%
        </span>
      </div>
      {c.snippet_preview && (
        <div className="text-[12px] leading-relaxed text-zinc-700 line-clamp-4">
          {c.snippet_preview}
        </div>
      )}
      <div className="text-[10px] mono text-zinc-400 truncate" title={c.hit_id}>
        hit: {c.hit_id.slice(0, 12)}…
      </div>
    </div>
  );
}

function HitRow({ h }: { h: Hit }) {
  return (
    <div className="text-[11px] text-zinc-600">
      <span className="mono text-zinc-500">{h.kind}</span> ·{" "}
      <span className="mono">{(h.score * 100).toFixed(0)}%</span>{" "}
      <span className="text-zinc-500">— {h.snippet.slice(0, 70)}…</span>
    </div>
  );
}
