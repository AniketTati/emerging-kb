"use client";

import {
  createContext,
  useContext,
  useEffect,
  useReducer,
  useRef,
  type Dispatch,
  type ReactNode,
} from "react";
import {
  getSessionTurns,
  type ChatResponse,
  type ChatStreamEvent,
  type Citation,
  type SessionTurn,
} from "./api";

/** localStorage key that remembers the active chat session_id across
 *  navigations + page reloads. The DB still holds the canonical turn
 *  history; this key is just enough state for ChatProvider to know
 *  which session to fetch when it remounts. Cleared by `new_chat`. */
const SESSION_LS_KEY = "kb.chat.activeSessionId";

export type Turn = {
  id: string;
  role: "user" | "assistant";
  content: string;
  response?: ChatResponse;       // only assistant turns
  pending?: boolean;             // assistant placeholder while POST in flight
  error?: string;
  /** Live pipeline events received during the request — populated by
   *  the SSE stream handler. Empty until the first event arrives;
   *  retained after `pending` flips to false so the "How I answered"
   *  inspector can show the full trace. */
  events?: ChatStreamEvent[];
  /** Citations attached when the turn is replayed from a past
   *  session (not from a live SSE stream). Same shape as
   *  ChatResponse.generation.citations. */
  replayCitations?: Citation[];
};

export type State = {
  turns: Turn[];
  /** Backend session ID — null until the first response binds one
   *  (or until the user clicks a row in the history sidebar). All
   *  subsequent chat calls pass this so the turn lands in the same
   *  session and the carry-forward context resolver runs. */
  sessionId: string | null;
};

type Action =
  | { type: "user_sent"; userId: string; assistantId: string; content: string }
  | { type: "assistant_event"; assistantId: string; event: ChatStreamEvent }
  | { type: "assistant_answered"; assistantId: string; response: ChatResponse }
  | { type: "assistant_errored"; assistantId: string; error: string }
  | { type: "new_chat" }
  | { type: "load_session"; sessionId: string; turns: SessionTurn[] };

const initialState: State = { turns: [], sessionId: null };

export function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "user_sent": {
      return {
        ...state,
        turns: [
          ...state.turns,
          { id: action.userId, role: "user", content: action.content },
          {
            id: action.assistantId,
            role: "assistant",
            content: "",
            pending: true,
          },
        ],
      };
    }
    case "assistant_event": {
      return {
        ...state,
        turns: state.turns.map((t) =>
          t.id === action.assistantId
            ? { ...t, events: [...(t.events ?? []), action.event] }
            : t,
        ),
      };
    }
    case "assistant_answered": {
      const sid = action.response.session_id ?? state.sessionId;
      return {
        ...state,
        // Lock the session id once the backend binds it — subsequent
        // turns reuse this same session so the carry-forward resolver
        // can apply prior context ("was it changed?" → MSA).
        sessionId: sid ?? null,
        turns: state.turns.map((t) =>
          t.id === action.assistantId
            ? {
                ...t,
                pending: false,
                content: action.response.generation.answer,
                response: action.response,
              }
            : t,
        ),
      };
    }
    case "assistant_errored": {
      return {
        ...state,
        turns: state.turns.map((t) =>
          t.id === action.assistantId
            ? { ...t, pending: false, error: action.error }
            : t,
        ),
      };
    }
    case "new_chat": {
      // User clicked "+ new chat". Drop everything and let the next
      // /chat call auto-create a fresh session.
      return { turns: [], sessionId: null };
    }
    case "load_session": {
      // User clicked a row in the history sidebar. Replay the saved
      // turns from the backend into the same Turn shape the UI uses
      // for live messages, so the thread renders identically. We
      // don't have the streaming events list (those weren't saved)
      // but we synthesize a minimal `response` so MessageBubble /
      // AnswerCard render exactly as they do for live turns.
      const replayed: Turn[] = [];
      for (const t of action.turns) {
        replayed.push({
          id: `replay-u-${action.sessionId}-${t.turn_index}`,
          role: "user",
          content: t.user_query,
        });
        // Synthesize a ChatResponse from the persisted turn so
        // MessageBubble / AnswerCard render replayed history exactly
        // like a live turn. The pipeline-stage fields (mode / intent /
        // crag / faithfulness) come back via the LEFT JOIN to
        // query_log so the "How I answered" inspector doesn't show "?"
        // for replayed turns the way it used to.
        const synthResponse: ChatResponse = {
          query_id: `replay-${action.sessionId}-${t.turn_index}`,
          query: t.user_query,
          rewrites: {},
          generation: {
            answer: t.answer ?? "",
            citations: t.citations ?? [],
            refused: t.refused ?? !t.answer,
            refusal_reason: t.refusal_reason ?? (t.answer ? null : "no answer recorded"),
            model_id: "replay",
          },
          // We don't persist the hit list — synthesize a sentinel array
          // sized to `hits_count` so the inspector's "N hits" readout is
          // accurate even though the metadata is sparse.
          hits: t.hits_count && t.hits_count > 0
            ? Array.from({ length: t.hits_count }, (_, i) => ({
                id: `replay-hit-${i}`,
                kind: "chunk" as const,
                score: 0,
                snippet: "",
                metadata: {},
              }))
            : [],
          crag_score: t.crag_score ?? 0,
          latency_ms: t.latency_ms ?? 0,
          session_id: action.sessionId,
          turn_index: t.turn_index,
          mode: t.mode ?? undefined,
          intent: t.intent ?? undefined,
          intent_confidence: t.intent_confidence ?? undefined,
          faithfulness_verdict: t.faithfulness_verdict ?? undefined,
          faithfulness_score: t.faithfulness_score ?? undefined,
        };
        replayed.push({
          id: `replay-a-${action.sessionId}-${t.turn_index}`,
          role: "assistant",
          content: t.answer ?? "",
          response: synthResponse,
          replayCitations: t.citations ?? [],
        });
      }
      return { turns: replayed, sessionId: action.sessionId };
    }
    default:
      return state;
  }
}

const ChatContext = createContext<{
  state: State;
  dispatch: Dispatch<Action>;
} | null>(null);

export function useChat() {
  const ctx = useContext(ChatContext);
  if (!ctx) {
    throw new Error("useChat must be used inside <ChatProvider>");
  }
  return ctx;
}

export function ChatProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, initialState);

  // Mirror sessionId → localStorage so navigating away from /chat and
  // back (or a page reload) doesn't drop the user into a fresh empty
  // session. The DB has the turns; we just need to remember which
  // session_id was active.
  //
  // Why the `mountedRef` guard: state.sessionId starts as null on every
  // mount, which would otherwise fire on the initial useEffect run and
  // wipe the localStorage key BEFORE the restore effect below gets to
  // read it. We only want to clear the key when sessionId transitions
  // from non-null → null (an explicit `new_chat`), not on the initial
  // null seen at mount time.
  const mountedRef = useRef(false);
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (state.sessionId) {
      window.localStorage.setItem(SESSION_LS_KEY, state.sessionId);
      mountedRef.current = true;
    } else if (mountedRef.current) {
      // We've previously had a sessionId in this mount's lifetime, so
      // a null now means the user clicked "new chat". Wipe the key so
      // the next mount doesn't auto-restore the old session.
      window.localStorage.removeItem(SESSION_LS_KEY);
    }
  }, [state.sessionId]);

  // Auto-restore on mount: if a session id is in localStorage AND we
  // don't already have turns (we might if the parent component already
  // dispatched load_session from a sidebar click), fetch turns + replay.
  // The ref guards against React strict-mode double-mount in dev.
  //
  // Skip restore when the URL carries `?q=…` — that's the /audit
  // "Replay" deep-link which wants a fresh session for the replayed
  // query, not the user's last conversation.
  const restoredRef = useRef(false);
  useEffect(() => {
    if (restoredRef.current) return;
    if (typeof window === "undefined") return;
    if (state.turns.length > 0 || state.sessionId) return;
    if (new URLSearchParams(window.location.search).has("q")) return;
    const stored = window.localStorage.getItem(SESSION_LS_KEY);
    if (!stored) return;
    restoredRef.current = true;
    (async () => {
      try {
        const turns = await getSessionTurns(stored);
        if (turns.length > 0) {
          dispatch({ type: "load_session", sessionId: stored, turns });
        }
        // Empty turns: could be a deleted session OR a session that was
        // auto-created but hasn't had its first message yet (the user
        // hit "New chat" and reloaded before sending). We intentionally
        // DON'T clear localStorage here — over-clearing on a transient
        // state was its own bug. The sidebar will surface the truth on
        // the next listSessions call; explicit "new_chat" clears the
        // anchor; a confirmed 404 on the session-info endpoint is the
        // only other signal the operator can rely on (not currently
        // wired — TODO if it becomes a real problem).
      } catch (err) {
        // Network / transient — KEEP the key. A flaky API call should
        // not make the user lose their session anchor. Log so the
        // operator can see something happened.
        // eslint-disable-next-line no-console
        console.warn("chat-state restore: fetch failed; keeping anchor", err);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <ChatContext.Provider value={{ state, dispatch }}>
      {children}
    </ChatContext.Provider>
  );
}

export { initialState };
