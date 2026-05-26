"use client";

import {
  createContext,
  useContext,
  useReducer,
  type Dispatch,
  type ReactNode,
} from "react";
import type { ChatResponse, ChatStreamEvent } from "./api";

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
};

export type State = { turns: Turn[] };

type Action =
  | { type: "user_sent"; userId: string; assistantId: string; content: string }
  | { type: "assistant_event"; assistantId: string; event: ChatStreamEvent }
  | { type: "assistant_answered"; assistantId: string; response: ChatResponse }
  | { type: "assistant_errored"; assistantId: string; error: string };

const initialState: State = { turns: [] };

export function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "user_sent": {
      return {
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
        turns: state.turns.map((t) =>
          t.id === action.assistantId
            ? { ...t, events: [...(t.events ?? []), action.event] }
            : t,
        ),
      };
    }
    case "assistant_answered": {
      return {
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
        turns: state.turns.map((t) =>
          t.id === action.assistantId
            ? { ...t, pending: false, error: action.error }
            : t,
        ),
      };
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
