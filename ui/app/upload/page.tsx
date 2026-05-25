"use client";

import { useEffect, useRef, useState } from "react";
import { listFiles, subscribeToFileStatus } from "@/lib/api";
import { UploadProvider, useUploadStore } from "@/lib/state";
import { Sidebar } from "@/components/Sidebar";
import { TopBar } from "@/components/TopBar";
import { DropZone } from "@/components/DropZone";
import { FilesTable } from "@/components/FilesTable";
import { FilterBar, type FilterKey } from "@/components/FilterBar";

function UploadShell() {
  const { state, dispatch } = useUploadStore();
  const subscribed = useRef<Set<string>>(new Set());
  const [filter, setFilter] = useState<FilterKey>("all");
  const [query, setQuery] = useState("");

  useEffect(() => {
    let cancelled = false;
    listFiles()
      .then((r) => {
        if (cancelled) return;
        dispatch({ type: "seed", files: r.items });
      })
      .catch(() => {
        // Backend unreachable; user can still drop files.
      });
    return () => {
      cancelled = true;
    };
  }, [dispatch]);

  useEffect(() => {
    const cleanups: Array<() => void> = [];
    for (const id of state.order) {
      const row = state.rows[id];
      if (!row) continue;
      const terminal =
        row.lifecycle_state === "ready" ||
        row.lifecycle_state === "failed" ||
        row.lifecycle_state === "deleted";
      if (terminal) continue;
      if (subscribed.current.has(id)) continue;

      subscribed.current.add(id);
      const close = subscribeToFileStatus(id, {
        onLifecycle: (ev) => dispatch({ type: "lifecycle", event: ev }),
        onDone: () => subscribed.current.delete(id),
      });
      cleanups.push(close);
    }
    return () => {
      cleanups.forEach((c) => c());
    };
  }, [state.order, state.rows, dispatch]);

  return (
    <div className="flex h-full">
      <Sidebar current="upload" />
      <main className="flex-1 flex flex-col min-w-0 bg-zinc-50/40">
        <TopBar />
        <div className="flex-1 overflow-y-auto">
          <div className="max-w-6xl mx-auto px-8 py-8">
            <div className="mb-6">
              <h1 className="text-lg font-semibold text-zinc-900">
                Upload documents
              </h1>
              <p className="text-sm text-zinc-500 mt-1">
                Drop anything. The system auto-detects type, parses, extracts
                entities, and indexes — typically 30s–2m per doc.
              </p>
            </div>
            <DropZone />
            <FilterBar
              active={filter}
              onChange={setFilter}
              query={query}
              onQueryChange={setQuery}
            />
            <FilesTable filter={filter} query={query} />
          </div>
        </div>
      </main>
    </div>
  );
}

export default function UploadPage() {
  return (
    <UploadProvider>
      <UploadShell />
    </UploadProvider>
  );
}
