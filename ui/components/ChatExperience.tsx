"use client";

import { Suspense, useEffect, useRef } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { createSession, getSessionTurns, postChatStream } from "@/lib/api";
import { ChatProvider, useChat } from "@/lib/chat-state";
import { Sidebar } from "@/components/Sidebar";
import { MessageBubble } from "@/components/MessageBubble";
import { Composer } from "@/components/Composer";
import { CitationsPanel } from "@/components/CitationsPanel";
import { ChatHistorySidebar } from "@/components/ChatHistorySidebar";

/**
 * Shared chat UI rendered by both:
 *   /chat              — landing (sessionId=null, empty thread)
 *   /chat/[sessionId]  — active session (sessionId from URL params)
 *
 * sessionId is a PROP driven by the URL. It's the single source of
 * truth for which conversation is active. The reducer state inside
 * ChatProvider only owns the turns array.
 *
 * When sessionId changes:
 *   - non-null  → fetch turns + dispatch set_turns_from_session
 *   - null      → dispatch clear_turns (landing page state)
 *
 * Sends always pass sessionId (from prop). When sessionId is null
 * (first message from landing) and the backend auto-creates one, we
 * router.replace to /chat/<new-id> so the URL becomes the anchor for
 * every subsequent send — no race, no lost turns.
 */
export function ChatExperience({ sessionId }: { sessionId: string | null }) {
  return (
    <ChatProvider sessionId={sessionId}>
      {/* Suspense wraps ChatShell because it calls useSearchParams()
          for the ?q= replay deep-link, which Next 15 requires to live
          inside a Suspense boundary for static prerendering. */}
      <Suspense fallback={null}>
        <ChatShell />
      </Suspense>
    </ChatProvider>
  );
}

function ChatShell() {
  const { state, dispatch, sessionId } = useChat();
  const router = useRouter();
  const searchParams = useSearchParams();
  const threadRef = useRef<HTMLDivElement>(null);

  // --- Load this session's turns whenever the URL session id changes.
  //     React strict-mode double-mount is tolerated — repeated fetches
  //     are idempotent and the dispatch just re-hydrates the same data.
  useEffect(() => {
    if (sessionId == null) {
      dispatch({ type: "clear_turns" });
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const turns = await getSessionTurns(sessionId);
        if (cancelled) return;
        dispatch({ type: "set_turns_from_session", sessionId, turns });
      } catch (err) {
        // eslint-disable-next-line no-console
        console.warn("ChatExperience: getSessionTurns failed", err);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sessionId, dispatch]);

  // Auto-scroll the thread to the bottom on each new turn.
  useEffect(() => {
    if (!threadRef.current) return;
    threadRef.current.scrollTop = threadRef.current.scrollHeight;
  }, [state.turns]);

  // /audit "Replay" deep-link: ?q=… submits once on mount when the
  // thread is empty AND we're on the landing page (no session in URL).
  // Strict-mode guard via ref.
  const replayedRef = useRef(false);
  useEffect(() => {
    if (replayedRef.current) return;
    const q = searchParams.get("q");
    if (q && sessionId == null && state.turns.length === 0) {
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
        // sessionId from URL prop — the SINGLE source of truth. No
        // more state.sessionId / localStorage / restore-effect races.
        {
          ...(opts ?? {}),
          sessionId: sessionId ?? undefined,
        },
        {
          onEvent: (event) =>
            dispatch({ type: "assistant_event", assistantId, event }),
        },
      );
      dispatch({ type: "assistant_answered", assistantId, response });

      // First-message-on-landing case: backend auto-created a session
      // and returned its id. Bind the URL to it so every subsequent
      // send carries the same session_id from the prop, and so a
      // reload / share / back-button keeps the conversation.
      // router.replace avoids polluting browser history with the
      // pre-binding /chat entry.
      if (sessionId == null && response.session_id) {
        router.replace(`/chat/${response.session_id}`);
      }
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
        <ChatTopBar sessionId={sessionId} turnCount={state.turns.length} />

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
  const router = useRouter();
  async function newChat() {
    // POST /sessions eagerly so the row appears in the sidebar BEFORE
    // the first message — matches the user's "I clicked New, where is
    // it?" expectation. The route navigation triggers the sidebar's
    // refresh effect (which keys on sessionId), and the new empty
    // session shows up immediately.
    try {
      const { id } = await createSession();
      router.push(`/chat/${id}`);
    } catch {
      // Fallback: just go to the landing route. Backend will auto-
      // create on first message; UX degrades to the old behavior.
      router.push("/chat");
    }
  }

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
          <span className="text-[11px] text-zinc-400 mono">new conversation</span>
        )}
      </div>
      <span className="ml-auto text-[11px] text-zinc-400 mono hidden md:inline">
        ⌘+Enter to send · refusal-safe · cite-or-refuse
      </span>
      <button
        type="button"
        onClick={newChat}
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
