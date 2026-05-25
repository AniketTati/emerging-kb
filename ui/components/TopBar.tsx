"use client";

import { Command, Sun } from "lucide-react";
import { useUploadStore } from "@/lib/state";

export function TopBar() {
  const { state } = useUploadStore();
  const rows = Object.values(state.rows);
  const ready = rows.filter((r) => r.lifecycle_state === "ready").length;
  const failed = rows.filter((r) => r.lifecycle_state === "failed").length;
  const processing = rows.length - ready - failed;

  return (
    <header className="h-12 flex-shrink-0 border-b border-zinc-200 flex items-center px-5 gap-4">
      <div className="flex items-center gap-2 text-sm">
        <span className="text-zinc-900">Upload</span>
        <span className="ml-1 text-[11px] text-zinc-400 mono">
          {ready} ready · {processing} processing · {failed} failed
        </span>
      </div>

      <div className="ml-auto flex items-center gap-1">
        <button className="flex items-center gap-1.5 px-2 py-1 rounded text-xs text-zinc-500 hover:bg-zinc-100 mono">
          <Command className="w-3.5 h-3.5" strokeWidth={1.75} /> K
        </button>
        <button className="w-7 h-7 rounded hover:bg-zinc-100 flex items-center justify-center text-zinc-500">
          <Sun className="w-4 h-4" strokeWidth={1.75} />
        </button>
      </div>
    </header>
  );
}
