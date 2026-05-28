"use client";

import {
  createContext,
  useContext,
  useReducer,
  type Dispatch,
  type ReactNode,
} from "react";
import type {
  ChatResponse,
  ChatStreamEvent,
  Citation,
  SessionTurn,
} from "./api";

/**
 * Chat state — turns ONLY.
 *
 * `sessionId` is NOT in state anymore. It lives in the URL
 * (`/chat/[sessionId]`) and is the single source of truth. The page
 * component reads it from the URL via `useParams()` and passes it
 * into `ChatProvider` as a prop, which exposes it on the context for
 * any descendant that needs it. The reducer never touches it.
 *
 * Why: previously `sessionId` was in reducer state, mirrored to
 * localStorage, and async-restored on mount. That had a race — if the
 * user typed faster than the restore completed, their first message
 * went to a fresh auto-created session, then load_session overwrote
 * the active session id, then subsequent messages went to a
 * DIFFERENT session. Net effect: chats appeared to "lose" turns
 * because turns landed in multiple orphan sessions. URL-as-truth
 * removes the race entirely.
 */
export type Turn = {
  id: string;
  role: "user" | "assistant";
  content: string;
  response?: ChatResponse;       // only assistant turns
  pending?: boolean;             // assistant placeholder while POST in flight
  error?: string;
  events?: ChatStreamEvent[];
  /** Citations attached when the turn is replayed from a past
   *  session (not from a live SSE stream). */
  replayCitations?: Citation[];
};

export type State = {
  turns: Turn[];
};

type Action =
  | { type: "user_sent"; userId: string; assistantId: string; content: string }
  | { type: "assistant_event"; assistantId: string; event: ChatStreamEvent }
  | { type: "assistant_answered"; assistantId: string; response: ChatResponse }
  | { type: "assistant_errored"; assistantId: string; error: string }
  | { type: "clear_turns" }
  | { type: "set_turns_from_session"; sessionId: string; turns: SessionTurn[] };

const initialState: State = { turns: [] };

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
      return {
        ...state,
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
    case "clear_turns": {
      return { turns: [] };
    }
    case "set_turns_from_session": {
      // Server-side replay: hydrate the thread from persisted turns
      // (chat_turns rows) so the UI renders identically to a live
      // stream. We synthesize a minimal ChatResponse per turn so
      // MessageBubble / AnswerCard render with the same shape.
      const replayed: Turn[] = [];
      for (const t of action.turns) {
        replayed.push({
          id: `replay-u-${action.sessionId}-${t.turn_index}`,
          role: "user",
          content: t.user_query,
        });
        const synthResponse: ChatResponse = {
          query_id: `replay-${action.sessionId}-${t.turn_index}`,
          query: t.user_query,
          rewrites: {},
          generation: {
            answer: t.answer ?? "",
            citations: t.citations ?? [],
            refused: t.refused ?? !t.answer,
            refusal_reason:
              t.refusal_reason ?? (t.answer ? null : "no answer recorded"),
            model_id: "replay",
          },
          // We don't persist the hit list — synthesize a sentinel array
          // sized to `hits_count` so the inspector's "N hits" readout is
          // accurate even though the metadata is sparse.
          hits:
            t.hits_count && t.hits_count > 0
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
      return { turns: replayed };
    }
    default:
      return state;
  }
}

type ChatContextValue = {
  state: State;
  dispatch: Dispatch<Action>;
  /** Session id from the URL. `null` on the landing /chat route (no
   *  active conversation), a real UUID on /chat/[sessionId]. */
  sessionId: string | null;
};

const ChatContext = createContext<ChatContextValue | null>(null);

export function useChat() {
  const ctx = useContext(ChatContext);
  if (!ctx) {
    throw new Error("useChat must be used inside <ChatProvider>");
  }
  return ctx;
}

export function ChatProvider({
  sessionId,
  children,
}: {
  sessionId: string | null;
  children: ReactNode;
}) {
  const [state, dispatch] = useReducer(reducer, initialState);
  return (
    <ChatContext.Provider value={{ state, dispatch, sessionId }}>
      {children}
    </ChatContext.Provider>
  );
}

export { initialState };
