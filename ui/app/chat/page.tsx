"use client";

import { ChatExperience } from "@/components/ChatExperience";

/**
 * /chat — landing page with no active session.
 *
 * Renders the same chat UI as /chat/[sessionId] but with sessionId=null:
 *   - empty thread (the EmptyState card with suggested queries)
 *   - sidebar still shows recent sessions
 *   - "New chat" button POSTs /sessions then navigates to /chat/<id>
 *   - first message from the composer auto-creates a session server-
 *     side and the URL replaces to /chat/<auto-id> so subsequent turns
 *     stay in the same session
 *
 * Active conversations live at /chat/[sessionId]/page.tsx.
 */
export default function ChatLandingPage() {
  return <ChatExperience sessionId={null} />;
}
