"use client";

import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import Link from "next/link";
import {
  AtSign,
  Paperclip,
  Sparkles,
  ArrowUp,
  X,
  Search,
} from "lucide-react";
import { listFiles, type FileResource } from "@/lib/api";

type Props = {
  onSubmit: (query: string, opts?: { fileIds?: string[] }) => void;
  disabled?: boolean;
};

/**
 * Composer textarea. ⌘+Enter (or Ctrl+Enter) submits; plain Enter inserts a
 * newline.
 *
 * Functional toolbar buttons:
 *   - @ doc filter — popover with workspace files; multi-select scopes
 *     retrieval to those files via POST /chat `file_ids`
 *   - 📎 attach    — quick link to /upload (Wave A: separate upload flow)
 *   - ✨ deep_research — deferred (Wave B)
 */
export function Composer({ onSubmit, disabled }: Props) {
  const [value, setValue] = useState("");
  const [filterOpen, setFilterOpen] = useState(false);
  const [selectedFileIds, setSelectedFileIds] = useState<string[]>([]);
  const [selectedFileLabels, setSelectedFileLabels] = useState<
    Record<string, string>
  >({});
  const taRef = useRef<HTMLTextAreaElement>(null);

  function submit() {
    const q = value.trim();
    if (!q || disabled) return;
    onSubmit(
      q,
      selectedFileIds.length > 0 ? { fileIds: selectedFileIds } : undefined,
    );
    setValue("");
    // Refocus for fast follow-ups. Doc-filter selection persists across
    // turns by design — the user typically asks several questions about
    // the same scoped set.
    requestAnimationFrame(() => taRef.current?.focus());
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      submit();
    }
  }

  function removeFile(id: string) {
    setSelectedFileIds((prev) => prev.filter((x) => x !== id));
    setSelectedFileLabels((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
  }

  function clearFilter() {
    setSelectedFileIds([]);
    setSelectedFileLabels({});
  }

  return (
    <div className="flex-shrink-0 border-t border-zinc-200 bg-white">
      <div className="max-w-3xl mx-auto px-8 py-4">
        {selectedFileIds.length > 0 && (
          <SelectedFiles
            ids={selectedFileIds}
            labels={selectedFileLabels}
            onRemove={removeFile}
            onClear={clearFilter}
          />
        )}
        <div className="rounded-xl border border-zinc-200 bg-white focus-within:border-zinc-400 transition-colors">
          <textarea
            ref={taRef}
            rows={2}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Ask anything about your knowledge base…"
            className="w-full bg-transparent resize-none px-4 pt-3 pb-2 text-[14px] placeholder-zinc-400 outline-none"
            disabled={disabled}
            aria-label="Chat input"
            data-testid="chat-input"
          />
          <div className="px-2.5 py-2 flex items-center gap-1 border-t border-zinc-100 relative">
            <button
              type="button"
              onClick={() => setFilterOpen((o) => !o)}
              className={
                selectedFileIds.length > 0 || filterOpen
                  ? "flex items-center gap-1.5 px-2 py-1 rounded text-xs text-zinc-900 bg-zinc-100 hover:bg-zinc-200"
                  : "flex items-center gap-1.5 px-2 py-1 rounded text-xs text-zinc-500 hover:bg-zinc-100"
              }
              title="Scope retrieval to specific files"
              data-testid="chat-doc-filter"
            >
              <AtSign className="w-3.5 h-3.5" strokeWidth={1.75} />
              doc filter
              {selectedFileIds.length > 0 && (
                <span className="ml-0.5 px-1 py-0.5 rounded-full bg-zinc-900 text-white text-[10px] mono">
                  {selectedFileIds.length}
                </span>
              )}
            </button>
            <Link
              href="/upload"
              className="flex items-center gap-1.5 px-2 py-1 rounded text-xs text-zinc-500 hover:bg-zinc-100"
              title="Open the upload page to add documents"
              data-testid="chat-attach"
            >
              <Paperclip className="w-3.5 h-3.5" strokeWidth={1.75} /> attach
            </Link>
            <label
              className="flex items-center gap-1.5 px-2 py-1 rounded text-xs text-zinc-400 cursor-not-allowed"
              title="Coming soon"
            >
              <input type="checkbox" disabled className="accent-zinc-900 w-3 h-3" />
              <Sparkles className="w-3.5 h-3.5" strokeWidth={1.75} /> deep_research
            </label>
            <div className="ml-auto flex items-center gap-3">
              <span className="text-[11px] text-zinc-400 mono">⌘↵</span>
              <button
                type="button"
                onClick={submit}
                disabled={!value.trim() || disabled}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-zinc-900 text-white text-xs font-medium hover:bg-zinc-800 disabled:opacity-40 disabled:cursor-not-allowed"
                data-testid="chat-send"
              >
                Send <ArrowUp className="w-3.5 h-3.5" strokeWidth={1.75} />
              </button>
            </div>

            {filterOpen && (
              <DocFilterPopover
                selectedIds={selectedFileIds}
                onClose={() => setFilterOpen(false)}
                onToggle={(file) => {
                  setSelectedFileIds((prev) =>
                    prev.includes(file.id)
                      ? prev.filter((x) => x !== file.id)
                      : [...prev, file.id],
                  );
                  setSelectedFileLabels((prev) => ({
                    ...prev,
                    [file.id]: file.name,
                  }));
                }}
              />
            )}
          </div>
        </div>
        <div className="mt-2 text-[11px] text-zinc-400 px-1">
          Read-only. Retrieves and reasons; never sends, places, or mutates
          anything.
        </div>
      </div>
    </div>
  );
}


function SelectedFiles({
  ids,
  labels,
  onRemove,
  onClear,
}: {
  ids: string[];
  labels: Record<string, string>;
  onRemove: (id: string) => void;
  onClear: () => void;
}) {
  return (
    <div
      className="mb-2 flex items-center flex-wrap gap-1.5 text-[11px]"
      data-testid="chat-selected-files"
    >
      <span className="text-zinc-500 mono">Scoped to:</span>
      {ids.map((id) => (
        <span
          key={id}
          className="flex items-center gap-1 px-2 py-0.5 rounded bg-zinc-100 text-zinc-700 mono"
        >
          {labels[id] ?? id.slice(0, 8)}
          <button
            type="button"
            onClick={() => onRemove(id)}
            className="text-zinc-400 hover:text-zinc-900"
            aria-label={`Remove ${labels[id] ?? id} from scope`}
          >
            <X className="w-3 h-3" strokeWidth={1.75} />
          </button>
        </span>
      ))}
      <button
        type="button"
        onClick={onClear}
        className="ml-1 text-zinc-500 hover:text-zinc-900 mono underline-offset-2 hover:underline"
      >
        clear
      </button>
    </div>
  );
}


function DocFilterPopover({
  selectedIds,
  onToggle,
  onClose,
}: {
  selectedIds: string[];
  onToggle: (file: FileResource) => void;
  onClose: () => void;
}) {
  const [files, setFiles] = useState<FileResource[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    let cancelled = false;
    listFiles({ limit: 50, offset: 0 })
      .then((r) => !cancelled && setFiles(r.items))
      .catch((e) => !cancelled && setErr(String(e)));
    return () => {
      cancelled = true;
    };
  }, []);

  // Close on click-outside.
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (!ref.current) return;
      if (!ref.current.contains(e.target as Node)) onClose();
    }
    // Defer so the same click that opened doesn't immediately close.
    const t = window.setTimeout(
      () => document.addEventListener("mousedown", onDocClick),
      0,
    );
    return () => {
      window.clearTimeout(t);
      document.removeEventListener("mousedown", onDocClick);
    };
  }, [onClose]);

  const filtered = (files ?? []).filter((f) =>
    !query.trim()
      ? true
      : f.name.toLowerCase().includes(query.trim().toLowerCase()) ||
        (f.inferred_doc_type ?? "")
          .toLowerCase()
          .includes(query.trim().toLowerCase()),
  );

  return (
    <div
      ref={ref}
      className="absolute bottom-full left-2 mb-2 w-[360px] rounded-lg border border-zinc-200 bg-white shadow-lg z-10"
      data-testid="chat-doc-filter-popover"
    >
      <div className="px-3 pt-3 pb-2 border-b border-zinc-100 flex items-center gap-2">
        <Search className="w-3.5 h-3.5 text-zinc-400" strokeWidth={1.75} />
        <input
          autoFocus
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Filter files by name or type…"
          className="flex-1 bg-transparent outline-none text-xs placeholder-zinc-400"
        />
      </div>
      <div className="max-h-[280px] overflow-y-auto py-1">
        {err && (
          <div className="px-3 py-2 text-xs text-red-600 mono">{err}</div>
        )}
        {!err && files === null && (
          <div className="px-3 py-2 text-xs text-zinc-400">Loading files…</div>
        )}
        {!err && files !== null && filtered.length === 0 && (
          <div className="px-3 py-2 text-xs text-zinc-400">
            {files.length === 0 ? "No files in workspace" : "No matches"}
          </div>
        )}
        {filtered.map((f) => {
          const selected = selectedIds.includes(f.id);
          return (
            <button
              key={f.id}
              type="button"
              onClick={() => onToggle(f)}
              className={
                selected
                  ? "w-full text-left px-3 py-1.5 flex items-center gap-2 hover:bg-zinc-50 bg-zinc-100/50"
                  : "w-full text-left px-3 py-1.5 flex items-center gap-2 hover:bg-zinc-50"
              }
              data-testid="chat-doc-filter-item"
            >
              <input
                type="checkbox"
                checked={selected}
                readOnly
                className="accent-zinc-900 w-3 h-3"
                tabIndex={-1}
              />
              <span className="text-xs text-zinc-900 truncate flex-1" title={f.name}>
                {f.name}
              </span>
              {f.inferred_doc_type && (
                <span className="mono text-[10px] text-zinc-500 truncate" title={f.inferred_doc_type}>
                  {f.inferred_doc_type}
                </span>
              )}
            </button>
          );
        })}
      </div>
      <div className="px-3 py-2 border-t border-zinc-100 flex items-center justify-between text-[11px] mono text-zinc-400">
        <span>
          {selectedIds.length} of {files?.length ?? 0} selected
        </span>
        <button
          type="button"
          onClick={onClose}
          className="text-zinc-500 hover:text-zinc-900"
        >
          done
        </button>
      </div>
    </div>
  );
}
