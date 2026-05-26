"use client";

/**
 * Recent-chats rail for /chat. Loads /sessions on mount + refreshes every
 * time the user sends a new turn (so a freshly auto-created session
 * appears at the top). Clicking a row dispatches `load_session` which
 * replays the saved turns into the thread.
 *
 * Sticks alongside the message panel; intentionally NOT inside the
 * thin app-wide Sidebar — that rail is icon-only and would lose all
 * legibility with chat titles.
 */

import { useCallback, useEffect, useState } from "react";
import { Plus, MessageSquare, Loader2 } from "lucide-react";
import {
  listSessions,
  getSessionTurns,
  type SessionInfo,
} from "@/lib/api";
import { useChat } from "@/lib/chat-state";

function formatRelative(iso: string): string {
  const now = Date.now();
  const then = new Date(iso).getTime();
  const sec = Math.max(0, Math.floor((now - then) / 1000));
  if (sec < 60) return "just now";
  if (sec < 3600) return `${Math.floor(sec / 60)}m`;
  if (sec < 86_400) return `${Math.floor(sec / 3600)}h`;
  return `${Math.floor(sec / 86_400)}d`;
}

export function ChatHistorySidebar() {
  const { state, dispatch } = useChat();
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadingId, setLoadingId] = useState<string | null>(null);
  const lastTurnCount = state.turns.length;

  const refresh = useCallback(async () => {
    try {
      setLoading(true);
      const out = await listSessions(50);
      setSessions(out);
    } catch (err) {
      // Silent — sidebar is non-critical chrome.
      console.error("listSessions failed", err);
    } finally {
      setLoading(false);
    }
  }, []);

  // Initial load + refresh after every committed turn so a fresh
  // session pops to the top once the backend binds it.
  useEffect(() => {
    refresh();
  }, [refresh, lastTurnCount]);

  async function pick(s: SessionInfo) {
    if (s.id === state.sessionId) return;
    setLoadingId(s.id);
    try {
      const turns = await getSessionTurns(s.id);
      dispatch({ type: "load_session", sessionId: s.id, turns });
    } catch (err) {
      console.error("getSessionTurns failed", err);
    } finally {
      setLoadingId(null);
    }
  }

  return (
    <aside
      className="w-[260px] flex-shrink-0 border-r border-zinc-200 bg-zinc-50/40 flex flex-col"
      data-testid="chat-history-sidebar"
    >
      <div className="px-3 py-3 flex items-center justify-between border-b border-zinc-200">
        <div className="text-[11px] uppercase tracking-wider text-zinc-500 font-medium">
          Recent chats
        </div>
        <button
          type="button"
          onClick={() => dispatch({ type: "new_chat" })}
          className="flex items-center gap-1 px-2 py-1 text-xs text-zinc-600 hover:text-zinc-900 hover:bg-zinc-100 rounded-md cursor-pointer"
          data-testid="chat-new-button"
          title="Start a new chat"
        >
          <Plus className="w-3.5 h-3.5" strokeWidth={2} />
          <span>New</span>
        </button>
      </div>

      <div className="flex-1 overflow-y-auto py-2 px-2">
        {loading && sessions.length === 0 ? (
          <div className="flex items-center justify-center py-8 text-zinc-400">
            <Loader2 className="w-4 h-4 animate-spin" />
          </div>
        ) : sessions.length === 0 ? (
          <div className="px-2 py-4 text-xs text-zinc-400 leading-relaxed">
            No chats yet. Start by asking something below.
          </div>
        ) : (
          <ul className="space-y-0.5">
            {sessions.map((s) => {
              const active = s.id === state.sessionId;
              const isLoading = loadingId === s.id;
              return (
                <li key={s.id}>
                  <button
                    type="button"
                    onClick={() => pick(s)}
                    className={`w-full text-left px-2 py-2 rounded-md transition-colors cursor-pointer ${
                      active
                        ? "bg-zinc-200/70 text-zinc-900"
                        : "hover:bg-zinc-100 text-zinc-700"
                    }`}
                    data-testid="chat-history-row"
                    data-session-id={s.id}
                  >
                    <div className="flex items-start gap-2">
                      <MessageSquare
                        className="w-3.5 h-3.5 mt-0.5 flex-shrink-0 text-zinc-400"
                        strokeWidth={1.75}
                      />
                      <div className="flex-1 min-w-0">
                        <div className="text-[13px] truncate">
                          {s.title?.trim() || "Untitled chat"}
                        </div>
                        <div className="flex items-center gap-1.5 mt-0.5">
                          <span className="text-[10px] text-zinc-400">
                            {formatRelative(s.last_active_at)}
                          </span>
                          {isLoading && (
                            <Loader2 className="w-2.5 h-2.5 animate-spin text-zinc-400" />
                          )}
                        </div>
                      </div>
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </aside>
  );
}
