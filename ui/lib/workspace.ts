// Wave A: single-tenant per env. No login screen.
// `00000000-0000-0000-0000-000000000001` matches the backend default-workspace
// sentinel from build_tracker §0 conventions.

export const DEFAULT_WORKSPACE_ID =
  process.env.NEXT_PUBLIC_KB_WORKSPACE_ID ||
  "00000000-0000-0000-0000-000000000001";

export const KB_API_URL =
  process.env.NEXT_PUBLIC_KB_API_URL || "http://localhost:8000";

export function workspaceHeaders(): Record<string, string> {
  return { "X-Test-Workspace": DEFAULT_WORKSPACE_ID };
}
