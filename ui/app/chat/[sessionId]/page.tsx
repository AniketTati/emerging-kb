"use client";

import { use } from "react";
import { ChatExperience } from "@/components/ChatExperience";

/**
 * /chat/[sessionId] — active conversation page.
 *
 * The URL is the SINGLE source of truth for which session is active.
 * sessionId flows: URL → page params → ChatExperience prop →
 * ChatProvider context → handleSubmit's POST /chat body. No reducer
 * state, no localStorage, no async restore — and therefore no race
 * to lose your turns.
 *
 * Side benefits: refresh, browser back/forward, and sharing the URL
 * all "just work" because the route + URL ARE the conversation.
 */
export default function ChatSessionPage({
  params,
}: {
  // Next 15 + React 19 — `params` is a Promise. `use()` unwraps it
  // synchronously for client components, same pattern we use in
  // /files/[id]/page.tsx.
  params: Promise<{ sessionId: string }>;
}) {
  const { sessionId } = use(params);
  return <ChatExperience sessionId={sessionId} />;
}
