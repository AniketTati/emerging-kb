"use client";

import { useEffect, useRef } from "react";
import { postChatStream } from "@/lib/api";
import { ChatProvider, useChat } from "@/lib/chat-state";
import { Sidebar } from "@/components/Sidebar";
import { TopBar } from "@/components/TopBar";
import { MessageBubble } from "@/components/MessageBubble";
import { Composer } from "@/components/Composer";
import { CitationsPanel } from "@/components/CitationsPanel";

function ChatShell() {
  const { state, dispatch } = useChat();
  const threadRef = useRef<HTMLDivElement>(null);

  // Auto-scroll the thread to the bottom on each new turn.
  useEffect(() => {
    if (!threadRef.current) return;
    threadRef.current.scrollTop = threadRef.current.scrollHeight;
  }, [state.turns]);

  async function handleSubmit(
    query: string,
    opts?: { fileIds?: string[] },
  ) {
    const userId = crypto.randomUUID();
    const assistantId = crypto.randomUUID();
    dispatch({ type: "user_sent", userId, assistantId, content: query });
    try {
      const response = await postChatStream(query, opts ?? {}, {
        onEvent: (event) =>
          dispatch({ type: "assistant_event", assistantId, event }),
      });
      dispatch({ type: "assistant_answered", assistantId, response });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      dispatch({ type: "assistant_errored", assistantId, error: msg });
    }
  }

  const pending = state.turns.some((t) => t.role === "assistant" && t.pending);

  return (
    <div className="flex h-full">
      <Sidebar current="chat" />

      <main className="flex-1 flex flex-col min-w-0 bg-white">
        <ChatTopBar />

        <div className="flex-1 flex min-h-0">
          <section className="flex-1 flex flex-col min-w-0">
            <div
              ref={threadRef}
              className="flex-1 overflow-y-auto"
              data-testid="chat-thread"
            >
              <div className="max-w-3xl mx-auto px-8 py-10">
                {state.turns.length === 0 ? (
                  <EmptyState onPick={handleSubmit} />
                ) : (
                  state.turns.map((turn) => (
                    <MessageBubble key={turn.id} turn={turn} />
                  ))
                )}
              </div>
            </div>
            <Composer onSubmit={handleSubmit} disabled={pending} />
          </section>

          <CitationsPanel />
        </div>
      </main>
    </div>
  );
}

function ChatTopBar() {
  return (
    <header className="h-12 flex-shrink-0 border-b border-zinc-200 flex items-center px-5 gap-4">
      <div className="flex items-center gap-2 text-sm">
        <span className="text-zinc-900">Chat</span>
        <span className="ml-1 text-[11px] text-zinc-400 mono">
          ⌘+Enter to send · refusal-safe · cite-or-refuse
        </span>
      </div>
      <div className="ml-auto flex items-center gap-1">
        <a
          href="/upload"
          className="flex items-center gap-1.5 px-2 py-1 rounded text-xs text-zinc-500 hover:bg-zinc-100"
        >
          Upload docs →
        </a>
      </div>
    </header>
  );
}

// Reuse TopBar's ready/processing/failed counts only on /upload — chat uses
// its own thin top bar. Importing TopBar so the un-used import warning
// doesn't fire if we later choose to share.
void TopBar;

function EmptyState({ onPick }: { onPick: (q: string) => void }) {
  return (
    <div className="text-center pt-20" data-testid="chat-empty-state">
      <div className="text-2xl text-zinc-900 font-semibold mb-3">
        Ask anything about your knowledge base
      </div>
      <div className="text-sm text-zinc-500 max-w-md mx-auto mb-8">
        Try a question like &ldquo;Summarize the contracts I&apos;ve uploaded&rdquo;
        or &ldquo;Which docs mention Aurangabad?&rdquo;. Every answer cites the
        source. If the corpus doesn&apos;t support it, the system refuses
        rather than guesses.
      </div>
      <div className="flex flex-col gap-2 max-w-md mx-auto text-left">
        {[
          "What documents do I have indexed?",
          "Summarize the key topics across my uploads.",
          "Find the most recent contract in my knowledge base.",
        ].map((q) => (
          <SuggestedQuery key={q} text={q} onPick={onPick} />
        ))}
      </div>
    </div>
  );
}

function SuggestedQuery({
  text,
  onPick,
}: {
  text: string;
  onPick: (q: string) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onPick(text)}
      className="px-4 py-3 rounded-lg border border-zinc-200 bg-zinc-50/40 text-sm text-zinc-700 hover:border-zinc-400 hover:bg-zinc-50 transition-colors cursor-pointer text-left"
      data-testid="chat-suggestion"
    >
      {text}
    </button>
  );
}

export default function ChatPage() {
  return (
    <ChatProvider>
      <ChatShell />
    </ChatProvider>
  );
}
