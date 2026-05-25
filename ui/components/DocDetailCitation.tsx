"use client";

/**
 * Shared citation state for the doc-detail page.
 *
 * Right-pane components (mentions, atomic units, triples, fields)
 * publish a Citation when the user clicks them. The left-pane source
 * viewer subscribes and routes it to the format-specific renderer
 * (text-span highlight, xlsx row highlight, PDF page jump).
 */

import { createContext, useCallback, useContext, useState } from "react";

export type Citation =
  // Precise — worker resolved the exact chunk + char range at extraction
  // time (migration 0032). UI fetches /chunks/:id once and slices the
  // text to get the verbatim quote, then highlights it deterministically.
  | {
      kind: "exact";
      chunkId: string;
      start: number;
      end: number;
      pages?: number[];
    }
  // Best-effort fallback — used when the resolver couldn't find the
  // snippet (LLM paraphrased, only in contextual prefix, …) or for
  // pre-PR2 data the backfill couldn't repair.
  | { kind: "text"; text: string; page?: number[]; chunkId?: string | null }
  // Spreadsheet row hit — atomic_units carry sheet_name + row_index.
  | { kind: "xlsx-row"; sheet?: string; rowIndex: number }
  // Plain page jump — used when we only know which raw_page the data
  // came from (no extractable text query).
  | { kind: "page"; pageNumber: number };

type Ctx = {
  citation: Citation | null;
  cite: (c: Citation | null) => void;
};

const CitationCtx = createContext<Ctx | null>(null);

export function CitationProvider({ children }: { children: React.ReactNode }) {
  const [citation, setCitation] = useState<Citation | null>(null);
  const cite = useCallback((c: Citation | null) => setCitation(c), []);
  return (
    <CitationCtx.Provider value={{ citation, cite }}>
      {children}
    </CitationCtx.Provider>
  );
}

export function useCitation(): Ctx {
  const ctx = useContext(CitationCtx);
  if (!ctx) throw new Error("useCitation must be used inside CitationProvider");
  return ctx;
}
