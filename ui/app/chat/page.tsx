"use client";

import { Suspense, useEffect, useRef } from "react";
import { useSearchParams } from "next/navigation";
import { postChatStream } from "@/lib/api";
import { ChatProvider, useChat } from "@/lib/chat-state";
import { Sidebar } from "@/components/Sidebar";
import { TopBar } from "@/components/TopBar";
import { MessageBubble } from "@/components/MessageBubble";
import { Composer } from "@/components/Composer";
import { CitationsPanel } from "@/components/CitationsPanel";
import { ChatHistorySidebar } from "@/components/ChatHistorySidebar";

function ChatShell() {
  const { state, dispatch } = useChat();
  const threadRef = useRef<HTMLDivElement>(null);
  const searchParams = useSearchParams();

  // Auto-scroll the thread to the bottom on each new turn.
  useEffect(() => {
    if (!threadRef.current) return;
    threadRef.current.scrollTop = threadRef.current.scrollHeight;
  }, [state.turns]);

  // /audit "Replay" deep-link: ?q=… submits once on mount when the
  // thread is empty. Guard with a ref so React strict-mode double-
  // mount doesn't fire it twice.
  const replayedRef = useRef(false);
  useEffect(() => {
    if (replayedRef.current) return;
    const q = searchParams.get("q");
    if (q && state.turns.length === 0) {
      replayedRef.current = true;
      void handleSubmit(q);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleSubmit(
    query: string,
    opts?: { fileIds?: string[] },
  ) {
    const userId = crypto.randomUUID();
    const assistantId = crypto.randomUUID();
    dispatch({ type: "user_sent", userId, assistantId, content: query });
    try {
      const response = await postChatStream(
        query,
        // Thread the session_id through so subsequent turns land in the
        // SAME session — without this, every message in the thread
        // gets a fresh auto-created session and the recent-chats list
        // explodes with one row per question.
        { ...(opts ?? {}), sessionId: state.sessionId ?? undefined },
        {
          onEvent: (event) =>
            dispatch({ type: "assistant_event", assistantId, event }),
        },
      );
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
        <ChatTopBar
          sessionId={state.sessionId}
          turnCount={state.turns.length}
        />

        <div className="flex-1 flex min-h-0">
          <ChatHistorySidebar />

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
                    <MessageBubble
                      key={turn.id}
                      turn={turn}
                      onFollowUp={(q) => handleSubmit(q)}
                    />
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

function ChatTopBar({
  sessionId,
  turnCount,
}: {
  sessionId: string | null;
  turnCount: number;
}) {
  const { dispatch } = useChat();
  return (
    <header className="h-12 flex-shrink-0 border-b border-zinc-200 flex items-center px-5 gap-3">
      <div className="flex items-center gap-2 text-sm min-w-0">
        <span className="text-zinc-900 flex-shrink-0">Chat</span>
        <span className="text-zinc-300 flex-shrink-0">/</span>
        {sessionId ? (
          <>
            <span
              className="mono text-[11px] text-zinc-500 truncate"
              data-testid="chat-topbar-session"
              title={sessionId}
            >
              session {sessionId.slice(0, 8)}…
            </span>
            <span className="text-[11px] text-zinc-400 mono flex-shrink-0">
              · {turnCount} turn{turnCount === 1 ? "" : "s"}
            </span>
          </>
        ) : (
          <span className="text-[11px] text-zinc-400 mono">
            new conversation
          </span>
        )}
      </div>
      <span className="ml-auto text-[11px] text-zinc-400 mono hidden md:inline">
        ⌘+Enter to send · refusal-safe · cite-or-refuse
      </span>
      <button
        type="button"
        onClick={() => dispatch({ type: "new_chat" })}
        className="flex items-center gap-1.5 px-2 py-1 rounded text-xs text-zinc-500 hover:bg-zinc-100 cursor-pointer"
        data-testid="chat-new"
      >
        New chat
      </button>
      <a
        href="/upload"
        className="flex items-center gap-1.5 px-2 py-1 rounded text-xs text-zinc-500 hover:bg-zinc-100"
      >
        Upload docs →
      </a>
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
      {/* Suspense boundary required because ChatShell uses
       *  useSearchParams() to honour the /audit "Replay" deep-link.
       *  Without it, Next 15 fails the static prerender. */}
      <Suspense fallback={null}>
        <ChatShell />
      </Suspense>
    </ChatProvider>
  );
}
