"use client";

import { useEffect, useState } from "react";
import { FileText } from "lucide-react";
import { useChat } from "@/lib/chat-state";
import { getChunk, type Citation, type Hit } from "@/lib/api";

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

/** Type of the optional `ref` blob the backend attaches to enriched
 *  citations (Design 5 polymorphic envelope). Only the fields R2 cares
 *  about are typed here — the rest is opaque. */
type CitationRef = {
  source_chunk_id?: string | null;
  char_start?: number | null;
  char_end?: number | null;
  page?: number | null;
};

/** R2 — when a citation's ref carries (source_chunk_id, char_start, char_end)
 *  from the PR2 worker-side resolver, fetch the chunk and slice out the exact
 *  verbatim span. Falls back to `null` if any of the three are missing, the
 *  fetch fails, or the slice is degenerate. Caller renders the original
 *  `snippet_preview` (whole-chunk truncation) in that case. */
function useExactSnippet(c: Citation): string | null {
  const ref = (c as Citation & { ref?: CitationRef | null }).ref ?? null;
  const chunkId = ref?.source_chunk_id ?? null;
  const start = ref?.char_start ?? null;
  const end = ref?.char_end ?? null;
  const [snippet, setSnippet] = useState<string | null>(null);

  useEffect(() => {
    if (!chunkId || start == null || end == null || end <= start) {
      setSnippet(null);
      return;
    }
    let cancelled = false;
    getChunk(chunkId)
      .then((body) => {
        if (cancelled) return;
        const text = body.text ?? "";
        const slice = text.slice(start, end).trim();
        setSnippet(slice || null);
      })
      .catch(() => {
        // Network/SAVEPOINT failure — just stay on the whole-chunk preview.
        if (!cancelled) setSnippet(null);
      });
    return () => {
      cancelled = true;
    };
  }, [chunkId, start, end]);

  return snippet;
}

function CitationCardRow({ c, index }: { c: Citation; index: number }) {
  const exact = useExactSnippet(c);
  const ref = (c as Citation & { ref?: CitationRef | null }).ref ?? null;
  const page = ref?.page ?? null;
  const superseded = !!c.superseded;
  // R3 — show the file name as the card's primary title. The server's
  // citation enrichment builds a `label` like "vertex-msa.pdf · p. 2"
  // (or "vertex-pricing-tiers.xlsx · Sheet: Vendors · Row 7" for xlsx).
  // Falls back to the kind when label is absent (older response shape).
  const label = c.label || `${c.kind} · ${c.hit_id.slice(0, 8)}`;

  return (
    <div
      className={
        superseded
          ? "rounded-lg border border-amber-200 bg-amber-50/30 p-3 space-y-2"
          : "rounded-lg border border-zinc-200 bg-white p-3 space-y-2"
      }
      data-testid="citation-card"
      data-superseded={superseded || undefined}
    >
      {/* Header: file label as the primary identifier, badges on the right. */}
      <div className="flex items-start justify-between gap-2 text-xs">
        <div className="flex items-start gap-2 min-w-0 flex-1">
          <span
            className={
              superseded
                ? "mono text-amber-700 line-through decoration-amber-400 flex-shrink-0"
                : "mono text-zinc-900 flex-shrink-0"
            }
          >
            [{index}]
          </span>
          <FileText className="w-3.5 h-3.5 text-zinc-500 flex-shrink-0 mt-0.5" strokeWidth={1.75} />
          <a
            href={c.file_id ? `/files/${c.file_id}` : undefined}
            className="text-zinc-900 font-medium truncate hover:underline"
            title={label}
            data-testid="citation-label"
          >
            {label}
          </a>
        </div>
        <span className="mono text-[11px] text-zinc-500 flex-shrink-0">
          {(c.score * 100).toFixed(0)}%
        </span>
      </div>
      {/* Sub-row: chips for kind / page / exact / superseded / doc_status. */}
      <div className="flex flex-wrap items-center gap-1 text-[10px] mono">
        <span className="px-1 py-0.5 rounded bg-zinc-100 text-zinc-600">
          {c.kind}
        </span>
        {page != null && (
          <span className="px-1 py-0.5 rounded bg-zinc-100 text-zinc-600">
            p.{page}
          </span>
        )}
        {exact && (
          <span
            className="px-1 py-0.5 rounded bg-emerald-50 text-emerald-700 border border-emerald-200"
            title="Verbatim slice from PR2 worker-side resolver"
          >
            exact
          </span>
        )}
        {superseded && (
          <span
            className="px-1 py-0.5 rounded bg-amber-100 text-amber-900 border border-amber-200"
            title={c.conflict_resolution
              ? `Superseded via ${c.conflict_resolution}`
              : "Superseded"}
          >
            superseded
          </span>
        )}
        {c.doc_status && c.doc_status !== "live" && (
          <span className="px-1 py-0.5 rounded bg-zinc-100 text-zinc-600">
            {c.doc_status}
          </span>
        )}
      </div>
      {(exact || c.snippet_preview) && (
        <div className="text-[12px] leading-relaxed text-zinc-700 line-clamp-4">
          {exact ? (
            <span className="italic">&ldquo;{exact}&rdquo;</span>
          ) : (
            c.snippet_preview
          )}
        </div>
      )}
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
