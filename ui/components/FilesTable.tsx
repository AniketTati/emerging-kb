"use client";

import { FileText } from "lucide-react";
import { useUploadStore, type FileRow } from "@/lib/state";
import { StageBadge } from "./StageBadge";
import type { FilterKey } from "./FilterBar";

type Props = { filter: FilterKey; query: string };

function matchesFilter(row: FileRow, filter: FilterKey, q: string): boolean {
  if (filter === "ready" && row.lifecycle_state !== "ready") return false;
  if (filter === "failed" && row.lifecycle_state !== "failed") return false;
  if (filter === "processing") {
    if (row.lifecycle_state === "ready" || row.lifecycle_state === "failed") {
      return false;
    }
  }
  if (q) {
    const hay = `${row.name} ${row.mime_type}`.toLowerCase();
    if (!hay.includes(q.toLowerCase())) return false;
  }
  return true;
}

function elapsedLabel(ms: number): string {
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

function shortType(mime: string): string {
  // text/csv → csv · application/pdf → pdf · message/rfc822 → email
  if (mime === "message/rfc822") return "email";
  const tail = mime.split("/").pop() || mime;
  if (tail.startsWith("vnd.openxmlformats-officedocument.spreadsheetml")) return "xlsx";
  if (tail === "plain") return "txt";
  return tail;
}

export function FilesTable({ filter, query }: Props) {
  const { state } = useUploadStore();
  const rows = state.order
    .map((id) => state.rows[id])
    .filter((row) => row && matchesFilter(row, filter, query));

  if (rows.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-zinc-200 bg-white px-6 py-10 text-center">
        <div className="text-sm text-zinc-700">
          {state.order.length === 0
            ? "No uploads yet."
            : "No files match the current filter."}
        </div>
        <div className="text-xs text-zinc-500 mt-1">
          Drop files above to get started.
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-zinc-200 overflow-hidden bg-white">
      {/* header */}
      <div className="grid grid-cols-[1fr_120px_180px_90px] gap-3 px-4 py-2.5 text-[11px] uppercase tracking-wider text-zinc-500 bg-zinc-50 border-b border-zinc-200">
        <div>File</div>
        <div>Type</div>
        <div>Stage</div>
        <div className="text-right">Elapsed</div>
      </div>

      {rows.map((row) => {
        const elapsed = elapsedLabel(row.updatedAt - row.startedAt);
        return (
          <div
            key={row.id}
            className="border-b border-zinc-100 last:border-0 hover:bg-zinc-50/50 transition-colors"
            data-testid="file-row"
            data-file-id={row.id}
            data-state={row.lifecycle_state}
          >
            <div className="grid grid-cols-[1fr_120px_180px_90px] gap-3 px-4 py-3 items-center">
              <div className="flex items-center gap-2 min-w-0">
                <FileText
                  className="w-3.5 h-3.5 text-zinc-500 flex-shrink-0"
                  strokeWidth={1.75}
                  aria-hidden
                />
                <span
                  className="text-sm text-zinc-900 truncate"
                  title={row.name}
                >
                  {row.name}
                </span>
              </div>
              <div
                className="text-xs text-zinc-600 truncate mono"
                title={row.mime_type}
              >
                {shortType(row.mime_type)}
              </div>
              <StageBadge state={row.lifecycle_state} />
              <div className="text-xs text-zinc-500 mono text-right">{elapsed}</div>
            </div>
            {row.error && (
              <div className="px-4 pb-3 text-xs text-red-600 mono">
                {row.error}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
