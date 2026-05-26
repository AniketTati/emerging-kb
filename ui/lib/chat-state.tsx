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
          hits: [],
          crag_score: t.crag_score ?? 0,
          latency_ms: 0,
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
  return (
    <ChatContext.Provider value={{ state, dispatch }}>
      {children}
    </ChatContext.Provider>
  );
}

export { initialState };
