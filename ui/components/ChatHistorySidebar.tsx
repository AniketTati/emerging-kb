"use client";

/**
 * Recent-chats rail for /chat. Loads /sessions on mount + refreshes
 * after every turn the user sends (so a freshly auto-created session
 * appears at the top). Clicking a row dispatches `load_session` which
 * replays the saved turns into the thread.
 *
 * Two delete affordances:
 *   - Single trash icon on each row (appears on hover).
 *   - Multi-select mode (click "Select"): rows show checkboxes,
 *     a footer bar appears with "Delete N" + "Cancel".
 *
 * Sticks alongside the message panel; intentionally NOT inside the
 * thin app-wide Sidebar — that rail is icon-only and would lose all
 * legibility with chat titles.
 */

import { useCallback, useEffect, useState } from "react";
import { Plus, MessageSquare, Loader2, Trash2, Check, X } from "lucide-react";
import {
  listSessions,
  getSessionTurns,
  deleteSession,
  deleteSessionsBatch,
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
  // Single-delete pending (the row whose trash icon was just clicked,
  // needs confirmation). Cleared on confirm or cancel.
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  // Multi-select mode + which ids are checked.
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [batchDeleting, setBatchDeleting] = useState(false);
  const lastTurnCount = state.turns.length;

  const refresh = useCallback(async () => {
    try {
      setLoading(true);
      const out = await listSessions(50);
      setSessions(out);
    } catch (err) {
      console.error("listSessions failed", err);
    } finally {
      setLoading(false);
    }
  }, []);

  // Refresh triggers — be aggressive, the list is cheap to fetch:
  //   - Mount (initial load)
  //   - Turn count changes (a new turn just landed → maybe a new session
  //     also got auto-created)
  //   - sessionId changes (user switched sessions, started new chat, or
  //     localStorage-restored on remount → active highlight needs to move)
  //   - Window focus + tab visibility (user came back from another tab
  //     or window — DB may have changes from another instance)
  // Together these cover: "create new chat", "ask follow-up question",
  // "leave + come back", and "two windows open at once".
  useEffect(() => {
    refresh();
  }, [refresh, lastTurnCount, state.sessionId]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const onFocus = () => { void refresh(); };
    const onVisible = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [refresh]);

  async function pick(s: SessionInfo) {
    if (selecting) {
      // In multi-select, clicking the row toggles the checkbox.
      toggleSelected(s.id);
      return;
    }
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

  function toggleSelected(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function enterSelectMode() {
    setSelecting(true);
    setSelected(new Set());
  }

  function exitSelectMode() {
    setSelecting(false);
    setSelected(new Set());
  }

  async function handleDeleteOne(id: string) {
    try {
      await deleteSession(id);
      // If we just deleted the active session, drop the thread.
      if (id === state.sessionId) dispatch({ type: "new_chat" });
      await refresh();
    } catch (err) {
      console.error("deleteSession failed", err);
    } finally {
      setPendingDelete(null);
    }
  }

  async function handleDeleteSelected() {
    if (selected.size === 0) return;
    setBatchDeleting(true);
    try {
      const ids = Array.from(selected);
      await deleteSessionsBatch(ids);
      // If the currently-active session was among them, reset.
      if (state.sessionId && ids.includes(state.sessionId)) {
        dispatch({ type: "new_chat" });
      }
      await refresh();
      exitSelectMode();
    } catch (err) {
      console.error("deleteSessionsBatch failed", err);
    } finally {
      setBatchDeleting(false);
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
        <div className="flex items-center gap-1">
          {!selecting ? (
            <>
              <button
                type="button"
                onClick={enterSelectMode}
                disabled={sessions.length === 0}
                className="px-2 py-1 text-xs text-zinc-600 hover:text-zinc-900 hover:bg-zinc-100 rounded-md cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
                data-testid="chat-select-mode"
                title="Select chats to delete"
              >
                Select
              </button>
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
            </>
          ) : (
            <button
              type="button"
              onClick={exitSelectMode}
              className="px-2 py-1 text-xs text-zinc-600 hover:text-zinc-900 hover:bg-zinc-100 rounded-md cursor-pointer"
              data-testid="chat-select-cancel"
            >
              Cancel
            </button>
          )}
        </div>
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
              const isChecked = selected.has(s.id);
              const isPending = pendingDelete === s.id;
              return (
                <li key={s.id} className="group/row relative">
                  <button
                    type="button"
                    onClick={() => pick(s)}
                    className={`w-full text-left px-2 py-2 rounded-md transition-colors cursor-pointer ${
                      active
                        ? "bg-zinc-200/70 text-zinc-900"
                        : isChecked
                          ? "bg-blue-50 text-zinc-900"
                          : "hover:bg-zinc-100 text-zinc-700"
                    }`}
                    data-testid="chat-history-row"
                    data-session-id={s.id}
                  >
                    <div className="flex items-start gap-2">
                      {selecting ? (
                        <div
                          className={`w-3.5 h-3.5 mt-0.5 flex-shrink-0 rounded border ${
                            isChecked
                              ? "bg-zinc-900 border-zinc-900 flex items-center justify-center"
                              : "border-zinc-400"
                          }`}
                          aria-hidden
                        >
                          {isChecked && (
                            <Check className="w-2.5 h-2.5 text-white" strokeWidth={3} />
                          )}
                        </div>
                      ) : (
                        <MessageSquare
                          className="w-3.5 h-3.5 mt-0.5 flex-shrink-0 text-zinc-400"
                          strokeWidth={1.75}
                        />
                      )}
                      <div className="flex-1 min-w-0 pr-6">
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

                  {!selecting && (
                    <div className="absolute right-1.5 top-1.5">
                      {isPending ? (
                        <div className="flex items-center gap-0.5 bg-white rounded shadow-sm border border-zinc-200 px-1">
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              handleDeleteOne(s.id);
                            }}
                            className="p-1 text-red-600 hover:bg-red-50 rounded"
                            data-testid="chat-delete-confirm"
                            title="Confirm delete"
                          >
                            <Check className="w-3 h-3" strokeWidth={2.5} />
                          </button>
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              setPendingDelete(null);
                            }}
                            className="p-1 text-zinc-500 hover:bg-zinc-100 rounded"
                            data-testid="chat-delete-cancel"
                            title="Cancel"
                          >
                            <X className="w-3 h-3" strokeWidth={2.5} />
                          </button>
                        </div>
                      ) : (
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            setPendingDelete(s.id);
                          }}
                          className="opacity-0 group-hover/row:opacity-100 transition-opacity p-1 text-zinc-400 hover:text-red-600 hover:bg-white rounded cursor-pointer"
                          data-testid="chat-delete-button"
                          title="Delete chat"
                        >
                          <Trash2 className="w-3 h-3" strokeWidth={1.75} />
                        </button>
                      )}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {/* Multi-select footer */}
      {selecting && (
        <div className="border-t border-zinc-200 px-3 py-2.5 flex items-center gap-2 bg-white">
          <div className="text-xs text-zinc-600">
            {selected.size} selected
          </div>
          <button
            type="button"
            onClick={handleDeleteSelected}
            disabled={selected.size === 0 || batchDeleting}
            className="ml-auto flex items-center gap-1 px-2 py-1 text-xs rounded-md cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed bg-red-600 text-white hover:bg-red-700"
            data-testid="chat-delete-selected"
          >
            {batchDeleting ? (
              <Loader2 className="w-3 h-3 animate-spin" />
            ) : (
              <Trash2 className="w-3 h-3" strokeWidth={2} />
            )}
            <span>Delete {selected.size > 0 ? selected.size : ""}</span>
          </button>
        </div>
      )}
    </aside>
  );
}
