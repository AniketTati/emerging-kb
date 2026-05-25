/**
 * Backend client for the KB UI.
 *
 * Endpoints consumed in Phase 10a:
 *   - POST /files (multipart) — Phase 2a
 *   - GET /files — Phase 2a (list)
 *   - GET /upload/:file_id/status (SSE) — Phase 9
 *
 * 10b will extend with /chat + /chat/:id/stream + /search + /audit.
 */

import { KB_API_URL, workspaceHeaders } from "./workspace";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type LifecycleState =
  | "queued"
  | "parsing"
  | "parsed"
  | "chunked"
  | "contextualized"
  | "embedded"
  | "raptor_building"
  | "mentions_extracting"
  | "fields_extracting"
  | "units_extracting"
  | "entities_extracting"
  | "identity_resolving"
  | "ready"
  | "failed"
  | "deleted";

export type FileResource = {
  id: string;
  name: string;
  mime_type: string;
  size_bytes: number;
  content_sha: string;
  lifecycle_state: LifecycleState;
  created_at: string;
  // Other server-side fields exist; we model only what the UI consumes.
};

export type LifecycleEvent = {
  id: string;
  file_id: string;
  from_state: LifecycleState | null;
  to_state: LifecycleState;
  event: string;
  payload: Record<string, unknown>;
  created_at: string;
};

// ---------------------------------------------------------------------------
// REST helpers
// ---------------------------------------------------------------------------

class KbApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, body: unknown, message: string) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function _handle<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    let body: unknown;
    try {
      body = await resp.json();
    } catch {
      body = await resp.text();
    }
    throw new KbApiError(
      resp.status,
      body,
      `KB API ${resp.status}: ${typeof body === "string" ? body : JSON.stringify(body)}`,
    );
  }
  return (await resp.json()) as T;
}

// ---------------------------------------------------------------------------
// File ops
// ---------------------------------------------------------------------------

export async function listFiles(): Promise<{ items: FileResource[]; total: number }> {
  const resp = await fetch(`${KB_API_URL}/files`, {
    headers: workspaceHeaders(),
    cache: "no-store",
  });
  return _handle(resp);
}

/**
 * Upload one file via multipart POST /files.
 *
 * `idempotencyKey` defaults to a random UUID — passing the same key for the
 * same content returns the existing FileResource (200 + X-Dedup-Reason header)
 * instead of a duplicate (Phase 2a semantics).
 */
export async function uploadFile(
  file: File,
  idempotencyKey: string = crypto.randomUUID(),
): Promise<FileResource> {
  const form = new FormData();
  form.append("file", file, file.name);
  const resp = await fetch(`${KB_API_URL}/files`, {
    method: "POST",
    body: form,
    headers: {
      ...workspaceHeaders(),
      "Idempotency-Key": idempotencyKey,
    },
  });
  return _handle(resp);
}

/**
 * Subscribe to lifecycle events for a file via Phase 9's SSE endpoint.
 * Returns a cleanup function that closes the EventSource.
 *
 * Note: native EventSource does not support custom request headers, so the
 * default workspace is the only one usable from the browser; Wave A is
 * single-tenant per env so this is the intended path. Cross-tenant SSE will
 * need a token-in-URL or fetch-based polyfill in Wave B.
 */
export function subscribeToFileStatus(
  fileId: string,
  handlers: {
    onLifecycle?: (ev: LifecycleEvent) => void;
    onDone?: (data: { reason?: string }) => void;
    onHeartbeat?: () => void;
    onError?: (err: unknown) => void;
  },
): () => void {
  const url = `${KB_API_URL}/upload/${fileId}/status`;
  const es = new EventSource(url);

  es.addEventListener("lifecycle", (e) => {
    try {
      handlers.onLifecycle?.(JSON.parse((e as MessageEvent).data));
    } catch (err) {
      handlers.onError?.(err);
    }
  });

  es.addEventListener("done", (e) => {
    try {
      handlers.onDone?.(JSON.parse((e as MessageEvent).data));
    } catch {
      handlers.onDone?.({});
    }
    es.close();
  });

  es.addEventListener("heartbeat", () => {
    handlers.onHeartbeat?.();
  });

  es.onerror = (err) => {
    handlers.onError?.(err);
  };

  return () => es.close();
}

// ---------------------------------------------------------------------------
// Stage helpers — UI domain logic shared with components
// ---------------------------------------------------------------------------

/**
 * Canonical 5-stage pipeline projected from any lifecycle_state.
 * Used for the 5-pip status indicator.
 *
 * Stage indexing:
 *   0 — parsing/parsed         (raw → text)
 *   1 — chunked/contextualized/embedded (chunked + dense rep)
 *   2 — raptor_building        (RAPTOR per-doc tree)
 *   3 — *_extracting           (mentions/fields/units/entities/identity)
 *   4 — ready                  (terminal)
 */
const STAGE_INDEX: Record<LifecycleState, number> = {
  queued: -1,
  parsing: 0,
  parsed: 0,
  chunked: 1,
  contextualized: 1,
  embedded: 1,
  raptor_building: 2,
  mentions_extracting: 3,
  fields_extracting: 3,
  units_extracting: 3,
  entities_extracting: 3,
  identity_resolving: 3,
  ready: 4,
  failed: -1,
  deleted: -1,
};

export function stageIndexFor(state: LifecycleState): number {
  return STAGE_INDEX[state] ?? -1;
}

export function isTerminal(state: LifecycleState): boolean {
  return state === "ready" || state === "failed" || state === "deleted";
}

// Pretty-print for the stage label.
export function stageLabelFor(state: LifecycleState): string {
  return state.replace(/_/g, " ");
}

export { KbApiError };
