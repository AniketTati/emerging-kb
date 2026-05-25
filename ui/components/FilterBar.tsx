"use client";

import { Search, RefreshCw } from "lucide-react";
import { useUploadStore } from "@/lib/state";

export type FilterKey = "all" | "processing" | "ready" | "failed";

type Props = {
  active: FilterKey;
  onChange: (k: FilterKey) => void;
  query: string;
  onQueryChange: (q: string) => void;
};

/**
 * Filter chips + search input + "re-run failed" button — mirrors
 * prototype/upload.html. Re-run is a placeholder action in Wave A;
 * Phase 9b will wire it.
 */
export function FilterBar({ active, onChange, query, onQueryChange }: Props) {
  const { state } = useUploadStore();
  const rows = Object.values(state.rows);
  const counts = {
    all: rows.length,
    processing: rows.filter(
      (r) => r.lifecycle_state !== "ready" && r.lifecycle_state !== "failed",
    ).length,
    ready: rows.filter((r) => r.lifecycle_state === "ready").length,
    failed: rows.filter((r) => r.lifecycle_state === "failed").length,
  };

  const chip = (key: FilterKey, label: string) => {
    const isOn = active === key;
    return (
      <button
        type="button"
        onClick={() => onChange(key)}
        className={`text-xs px-2.5 py-1 rounded transition-colors ${
          isOn
            ? "text-zinc-900 bg-zinc-100"
            : "text-zinc-600 hover:bg-zinc-50"
        }`}
        aria-pressed={isOn}
      >
        {label}{" "}
        <span className={isOn ? "text-zinc-500 ml-0.5" : "text-zinc-400 ml-0.5"}>
          {counts[key]}
        </span>
      </button>
    );
  };

  return (
    <div className="flex items-center gap-3 mb-3">
      <div className="flex items-center gap-0.5 rounded-md border border-zinc-200 p-0.5">
        {chip("all", "All")}
        {chip("processing", "Processing")}
        {chip("ready", "Ready")}
        {chip("failed", "Failed")}
      </div>

      <div className="flex-1 relative">
        <Search
          className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-zinc-400"
          strokeWidth={1.75}
          aria-hidden
        />
        <input
          type="text"
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          placeholder="Filter by filename, type…"
          className="w-full text-xs pl-7 pr-3 py-1.5 rounded-md border border-zinc-200 focus:outline-none focus:border-zinc-400 focus:ring-1 focus:ring-zinc-200"
          aria-label="Filter files"
        />
      </div>

      <button
        type="button"
        disabled={counts.failed === 0}
        className="text-xs flex items-center gap-1.5 px-2.5 py-1.5 rounded-md border border-zinc-200 text-zinc-600 hover:bg-zinc-50 disabled:opacity-50 disabled:cursor-not-allowed"
        title={counts.failed === 0 ? "No failed uploads" : "Re-queue failed uploads"}
      >
        <RefreshCw className="w-3.5 h-3.5" strokeWidth={1.75} aria-hidden />
        Re-run failed
      </button>
    </div>
  );
}
