"use client";

/**
 * In-memory upload state. Wave A: refresh wipes (Phase 10c/Audit will
 * persist). Per-file rows keyed by the FileResource.id.
 */

import { createContext, useContext, useReducer, Dispatch } from "react";
import type { FileResource, LifecycleEvent, LifecycleState } from "./api";

export type FileRow = {
  id: string;
  name: string;
  mime_type: string;
  size_bytes: number;
  lifecycle_state: LifecycleState;
  events: LifecycleEvent[];
  startedAt: number;        // ms epoch
  updatedAt: number;
  error?: string;
  // Surfaced by the widened /files response (Phase 5b + B2).
  inferred_doc_type?: string | null;
  source_authority?: number | null;
  doc_status?: string | null;
};

export type State = { rows: Record<string, FileRow>; order: string[] };

type Action =
  | { type: "seed"; files: FileResource[] }
  | { type: "upserted"; file: FileResource }
  | { type: "lifecycle"; event: LifecycleEvent }
  | { type: "errored"; fileId: string; error: string };

const initialState: State = { rows: {}, order: [] };

function rowFromFile(file: FileResource, now: number): FileRow {
  return {
    id: file.id,
    name: file.name,
    mime_type: file.mime_type,
    size_bytes: file.size_bytes,
    lifecycle_state: file.lifecycle_state,
    events: [],
    startedAt: now,
    updatedAt: now,
    inferred_doc_type: file.inferred_doc_type ?? null,
    source_authority: file.source_authority ?? null,
    doc_status: file.doc_status ?? null,
  };
}

export function reducer(state: State, action: Action): State {
  const now = Date.now();
  switch (action.type) {
    case "seed": {
      const rows: Record<string, FileRow> = {};
      const order: string[] = [];
      for (const f of action.files) {
        rows[f.id] = rowFromFile(f, now);
        order.push(f.id);
      }
      return { rows, order };
    }
    case "upserted": {
      const existing = state.rows[action.file.id];
      const row: FileRow = existing
        ? {
            ...existing,
            lifecycle_state: action.file.lifecycle_state,
            updatedAt: now,
            // Re-merge the per-doc fields — a later /files refetch
            // may surface inferred_doc_type/source_authority after
            // the file moves past `fields_extracting`.
            inferred_doc_type: action.file.inferred_doc_type ?? existing.inferred_doc_type,
            source_authority: action.file.source_authority ?? existing.source_authority,
            doc_status: action.file.doc_status ?? existing.doc_status,
          }
        : rowFromFile(action.file, now);
      const order = existing ? state.order : [action.file.id, ...state.order];
      return { rows: { ...state.rows, [action.file.id]: row }, order };
    }
    case "lifecycle": {
      const ev = action.event;
      const existing = state.rows[ev.file_id];
      if (!existing) return state;
      const events = [...existing.events, ev];
      const row: FileRow = {
        ...existing,
        events,
        lifecycle_state: ev.to_state,
        updatedAt: now,
      };
      return { ...state, rows: { ...state.rows, [ev.file_id]: row } };
    }
    case "errored": {
      const existing = state.rows[action.fileId];
      if (!existing) return state;
      const row: FileRow = { ...existing, error: action.error, updatedAt: now };
      return { ...state, rows: { ...state.rows, [action.fileId]: row } };
    }
    default:
      return state;
  }
}

const UploadContext = createContext<{
  state: State;
  dispatch: Dispatch<Action>;
} | null>(null);

export function useUploadStore() {
  const ctx = useContext(UploadContext);
  if (!ctx) {
    throw new Error("useUploadStore must be used inside <UploadProvider>");
  }
  return ctx;
}

export function UploadProvider({ children }: { children: React.ReactNode }) {
  const [state, dispatch] = useReducer(reducer, initialState);
  return (
    <UploadContext.Provider value={{ state, dispatch }}>
      {children}
    </UploadContext.Provider>
  );
}

export { initialState };
