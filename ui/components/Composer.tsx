"use client";

import { useRef, useState, type KeyboardEvent } from "react";
import { AtSign, Paperclip, Sparkles, ArrowUp } from "lucide-react";

type Props = {
  onSubmit: (query: string) => void;
  disabled?: boolean;
};

/**
 * Composer textarea. ⌘+Enter (or Ctrl+Enter) submits; plain Enter inserts a
 * newline. Buttons are visual-only in Wave A (deep-research / attach / @doc
 * filter are Wave B).
 */
export function Composer({ onSubmit, disabled }: Props) {
  const [value, setValue] = useState("");
  const taRef = useRef<HTMLTextAreaElement>(null);

  function submit() {
    const q = value.trim();
    if (!q || disabled) return;
    onSubmit(q);
    setValue("");
    // Refocus for fast follow-ups.
    requestAnimationFrame(() => taRef.current?.focus());
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      submit();
    }
  }

  return (
    <div className="flex-shrink-0 border-t border-zinc-200 bg-white">
      <div className="max-w-3xl mx-auto px-8 py-4">
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
          <div className="px-2.5 py-2 flex items-center gap-1 border-t border-zinc-100">
            <button
              type="button"
              disabled
              className="flex items-center gap-1.5 px-2 py-1 rounded text-xs text-zinc-400 cursor-not-allowed"
              title="Wave B"
            >
              <AtSign className="w-3.5 h-3.5" strokeWidth={1.75} /> doc filter
            </button>
            <button
              type="button"
              disabled
              className="flex items-center gap-1.5 px-2 py-1 rounded text-xs text-zinc-400 cursor-not-allowed"
              title="Wave B"
            >
              <Paperclip className="w-3.5 h-3.5" strokeWidth={1.75} /> attach
            </button>
            <label
              className="flex items-center gap-1.5 px-2 py-1 rounded text-xs text-zinc-400 cursor-not-allowed"
              title="Wave B"
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
