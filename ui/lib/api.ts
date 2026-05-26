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

export type DocStatus =
  | "live"
  | "superseded"
  | "draft"
  | "archived"
  | "retracted";


export type FileResource = {
  id: string;
  name: string;
  mime_type: string;
  size_bytes: number;
  content_sha: string;
  lifecycle_state: LifecycleState;
  created_at: string;
  updated_at?: string;
  // Phase 5b / WA-6 / B2 — populated after the file passes through
  // `fields_extracting`. Null on files still being processed.
  inferred_doc_type?: string | null;
  source_authority?: number | null;
  source_authority_reason?: string | null;
  doc_status?: DocStatus | null;
};


export type LifecycleEventDetail = {
  from_state: LifecycleState | null;
  to_state: LifecycleState;
  event: string;
  payload: Record<string, unknown>;
  created_at: string;
};


export type FileDetails = {
  file: FileResource;
  lifecycle: LifecycleEventDetail[];
  n_pages: number;
  n_chunks: number;
  n_contextual_chunks: number;
  n_mentions: number;
  n_atomic_units: number;
  n_entities_linked: number;
  n_triples: number;
  chain_id: string | null;
  chain_role: string | null;
  chain_version_index: number | null;
  is_current_version: boolean | null;
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

/** GET /files — paginated. Backend caps `limit` at 200; default 50.
 *
 *  Returns `{items, total, limit, offset}` so the caller can decide whether
 *  more pages exist (`offset + items.length < total`). */
export async function listFiles(
  opts?: { limit?: number; offset?: number },
): Promise<{ items: FileResource[]; total: number; limit: number; offset: number }> {
  const limit = opts?.limit ?? 50;
  const offset = opts?.offset ?? 0;
  const resp = await fetch(
    `${KB_API_URL}/files?limit=${limit}&offset=${offset}`,
    { headers: workspaceHeaders(), cache: "no-store" },
  );
  return _handle(resp);
}


export async function getFileDetails(fileId: string): Promise<FileDetails> {
  const resp = await fetch(`${KB_API_URL}/files/${fileId}/details`, {
    headers: workspaceHeaders(),
    cache: "no-store",
  });
  return _handle(resp);
}


// ---------------------------------------------------------------------------
// Doc-detail surfaces — one fetcher per UI accordion. Each list endpoint
// is paginated; types mirror the Pydantic shapes from src/kb/api/files.py.
// ---------------------------------------------------------------------------

/** Worker-resolved source position (migration 0032). Present where the
 *  resolver successfully located the LLM-extracted snippet in the chunk
 *  text. UI uses these for deterministic citation highlighting. */
type SourcePos = {
  source_chunk_id?: string | null;
  source_char_start?: number | null;
  source_char_end?: number | null;
  source_page_numbers?: number[] | null;
};

export type ProposedField = SourcePos & {
  id: string;
  field_name: string;
  field_description: string | null;
  value_text: string | null;
  value_type: string | null;
  is_pii: boolean;
  model_id: string | null;
};

export type AtomicUnit = SourcePos & {
  id: string;
  unit_type: string;
  parameters: Record<string, unknown>;
  anchor_chunk_id: string | null;
  rarity_score: number | null;
  model_id: string | null;
};

export type Mention = SourcePos & {
  id: string;
  mention_text: string;
  mention_type: string;
  chunk_id: string | null;
  start_offset: number | null;
  end_offset: number | null;
  confidence: number | null;
  canonical_entity_id: string | null;
  canonical_name: string | null;
};

export type EntityMentioned = {
  entity_id: string;
  canonical_name: string;
  entity_type: string;
  mentions_in_doc: number;
  total_mentions: number;
};

export type TripleInDoc = {
  id: string;
  subject_text: string;
  predicate_text: string;
  object_text: string;
  confidence: number | null;
  chunk_id: string | null;
  source_page_numbers: number[] | null;
  subject_char_start: number | null;
  subject_char_end: number | null;
  object_char_start: number | null;
  object_char_end: number | null;
};

export type ChunkBody = {
  id: string;
  file_id: string;
  chunk_index: number;
  text: string;
  source_page_numbers: number[];
};

export const getChunk = (id: string) =>
  _getJson<ChunkBody>(`/chunks/${id}`);

export type ExtractedEntityInstance = {
  id: string;
  schema_entity_id: string;
  schema_entity_name: string | null;
  parent_entity_id: string | null;
  fields: Record<string, unknown>;
};

export type CitationByQuery = {
  query_id: string;
  query: string;
  answer: string | null;
  endpoint: string;
  created_at: string | null;
};

export type Paginated<T> = {
  items: T[];
  total: number;
  limit: number;
  offset: number;
};

/** R5 — per-element layout provenance captured by the Docling parser.
 *  Bbox coordinates are in the PDF's native coord system (bottom-left
 *  origin per PDF spec; `coord_origin` echoes that for the renderer to
 *  flip if it draws in top-left). */
export type LayoutElement = {
  label: string | null;        // section_header / text / table / picture / ...
  bbox: {
    l: number; t: number; r: number; b: number;
    coord_origin: string | null;
  };
  text?: string;               // up to 240 chars preview, when item is text-bearing
  charspan?: [number, number]; // optional [start, end] within page text
};

export type RawPage = {
  id?: string;
  page_number: number;
  text: string;
  content_sha?: string;
  created_at?: string;
  /** Free-form jsonb from the parser. For PDFs (post-R5) includes
   *  `size: {width,height}` + `elements: LayoutElement[]`. For OCR-
   *  parsed pages may include model_id / token counts instead. */
  layout_json?: {
    size?: { width: number | null; height: number | null };
    elements?: LayoutElement[];
    [k: string]: unknown;
  };
};

async function _getJson<T>(path: string): Promise<T> {
  const resp = await fetch(`${KB_API_URL}${path}`, {
    headers: workspaceHeaders(),
    cache: "no-store",
  });
  return _handle(resp);
}

export const getProposedFields = (id: string) =>
  _getJson<ProposedField[]>(`/files/${id}/proposed-fields`);

export const getExtractedEntities = (id: string) =>
  _getJson<ExtractedEntityInstance[]>(`/files/${id}/extracted-entities`);

export const getAtomicUnits = (
  id: string, opts?: { limit?: number; offset?: number },
) =>
  _getJson<Paginated<AtomicUnit>>(
    `/files/${id}/atomic-units?limit=${opts?.limit ?? 50}&offset=${opts?.offset ?? 0}`,
  );

export const getDocMentions = (
  id: string,
  opts?: { limit?: number; offset?: number; type?: string },
) => {
  const qs = new URLSearchParams({
    limit: String(opts?.limit ?? 100),
    offset: String(opts?.offset ?? 0),
  });
  if (opts?.type) qs.set("type", opts.type);
  return _getJson<Paginated<Mention>>(`/files/${id}/mentions?${qs}`);
};

export const getEntitiesMentioned = (
  id: string, opts?: { limit?: number; offset?: number },
) =>
  _getJson<Paginated<EntityMentioned>>(
    `/files/${id}/entities-mentioned?limit=${opts?.limit ?? 50}&offset=${opts?.offset ?? 0}`,
  );

export const getDocTriples = (
  id: string, opts?: { limit?: number; offset?: number },
) =>
  _getJson<Paginated<TripleInDoc>>(
    `/files/${id}/triples?limit=${opts?.limit ?? 50}&offset=${opts?.offset ?? 0}`,
  );

export const getDocCitations = (
  id: string, opts?: { limit?: number; offset?: number },
) =>
  _getJson<Paginated<CitationByQuery>>(
    `/files/${id}/citations?limit=${opts?.limit ?? 20}&offset=${opts?.offset ?? 0}`,
  );

export const getDocPages = (
  id: string, opts?: { limit?: number; offset?: number },
) =>
  _getJson<{ items: RawPage[]; total: number; limit: number; offset: number }>(
    `/files/${id}/pages?limit=${opts?.limit ?? 10}&offset=${opts?.offset ?? 0}`,
  );

export const getFile = (id: string) =>
  _getJson<FileResource & { lifecycle: LifecycleEventDetail[] }>(`/files/${id}`);


// ---------------------------------------------------------------------------
// Source-viewer surfaces — original file blob + structured xlsx parse.
// ---------------------------------------------------------------------------

export const blobUrl = (id: string) =>
  `${KB_API_URL}/files/${id}/blob`;

export async function fetchBlob(id: string): Promise<Blob> {
  const resp = await fetch(blobUrl(id), {
    headers: workspaceHeaders(),
    cache: "no-store",
  });
  if (!resp.ok) {
    throw new KbApiError(resp.status, await resp.text(), `blob ${resp.status}`);
  }
  return resp.blob();
}

export async function fetchBlobText(id: string): Promise<string> {
  return (await fetchBlob(id)).text();
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

// ---------------------------------------------------------------------------
// Chat — Phase 8f /chat + Phase 9 /chat/:id/stream
// ---------------------------------------------------------------------------

export type Citation = {
  hit_id: string;
  kind: string;
  file_id: string | null;
  snippet_preview: string;
  score: number;
  // R1 — Design 2 conflict-resolution markers populated by the
  // orchestrator. `superseded=true` means another doc in the same
  // chain currently holds the authoritative value for a fact this
  // citation's source disagrees on. The UI grays it out + shows
  // a "newer version available" hint.
  superseded?: boolean;
  superseded_by_doc_id?: string | null;
  conflict_resolution?:
    | "chain"
    | "status"
    | "authority"
    | "recency"
    | null;
  // Other polymorphic-envelope fields populated by build_citation /
  // citations.py enrichment. Optional everywhere — Wave A clients
  // may ignore.
  chain_id?: string | null;
  authority?: number | null;
  doc_status?: string | null;
  modality?: string | null;
  label?: string | null;
};

export type ConflictResolution = {
  entity_id: string;
  predicate: string;
  resolution: "chain" | "status" | "authority" | "recency" | "unresolved";
  picked_value: string | null;
  picked_doc_id: string | null;
  loser_doc_ids: string[];
  loser_values: string[];
  notes: string | null;
};

export type GenerationResult = {
  answer: string;
  citations: Citation[];
  refused: boolean;
  refusal_reason: string | null;
  model_id: string;
};

export type Hit = {
  id: string;
  kind: string;
  score: number;
  snippet: string;
  metadata: Record<string, unknown>;
};

export type ChatResponse = {
  query_id: string;
  query: string;
  rewrites: Record<string, string>;
  generation: GenerationResult;
  hits: Hit[];
  crag_score: number;
  latency_ms: number;
  // Pipeline-stage outcomes — all optional for back-compat with older
  // /chat clients; populated by Wave A orchestrator on every call.
  intent?: string;
  intent_confidence?: number;
  mode?: string;
  faithfulness_verdict?: string;
  faithfulness_score?: number | null;
  faithfulness_regenerations?: number;
  faithfulness_model_id?: string | null;
  citation_modalities?: string[];
  // R1 — surfaced conflict resolutions for the chat UI banner. Empty
  // when no chained-doc disagreements were detected for this query.
  conflict_resolutions?: ConflictResolution[];
  // Auto-created when caller doesn't pass one; the UI uses this to
  // thread subsequent calls into the same session + show history.
  session_id?: string | null;
  turn_index?: number | null;
};


// ---------------------------------------------------------------------------
// Sessions — chat history sidebar
// ---------------------------------------------------------------------------

export type SessionInfo = {
  id: string;
  workspace_id: string;
  created_at: string;
  last_active_at: string;
  title: string | null;
};

export type SessionTurn = {
  turn_index: number;
  user_query: string;
  resolved_query: string | null;
  answer: string | null;
  citations: Citation[];
  created_at: string;
  // Pipeline-stage metadata from query_log (LEFT JOIN'd server-side).
  // Populates the "How I answered" inspector on replay so it doesn't
  // show ? for Intent / Faithfulness / Mode.
  mode?: string | null;
  intent?: string | null;
  intent_confidence?: number | null;
  crag_score?: number | null;
  faithfulness_verdict?: string | null;
  faithfulness_score?: number | null;
  refused?: boolean | null;
  refusal_reason?: string | null;
};

export async function listSessions(limit = 50): Promise<SessionInfo[]> {
  const resp = await fetch(`${KB_API_URL}/sessions?limit=${limit}`, {
    headers: workspaceHeaders(),
  });
  const body = await _handle<{ items: SessionInfo[] }>(resp);
  return body.items ?? [];
}

export async function getSessionTurns(sessionId: string): Promise<SessionTurn[]> {
  const resp = await fetch(
    `${KB_API_URL}/sessions/${sessionId}/turns`,
    { headers: workspaceHeaders() },
  );
  const body = await _handle<{ items: SessionTurn[] }>(resp);
  return body.items ?? [];
}

export async function deleteSession(sessionId: string): Promise<number> {
  const resp = await fetch(
    `${KB_API_URL}/sessions/${sessionId}`,
    { method: "DELETE", headers: workspaceHeaders() },
  );
  const body = await _handle<{ deleted: number }>(resp);
  return body.deleted ?? 0;
}

export async function deleteSessionsBatch(
  sessionIds: string[],
): Promise<number> {
  if (sessionIds.length === 0) return 0;
  const resp = await fetch(`${KB_API_URL}/sessions/delete-batch`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...workspaceHeaders() },
    body: JSON.stringify({ session_ids: sessionIds }),
  });
  const body = await _handle<{ deleted: number }>(resp);
  return body.deleted ?? 0;
}


// ---------------------------------------------------------------------------
// Explore — faceted search across the workspace
// ---------------------------------------------------------------------------

export type ExploreKind =
  | "document"
  | "doc_type"
  | "atomic_unit"
  | "entity"
  | "relationship"
  | "topic"
  | "anomaly";

export type ExploreCounts = {
  documents: number;
  doc_types: number;
  atomic_units: number;
  entities: number;
  relationships: number;
  topics: number;
  anomalies: number;
};

export type ExploreHit = {
  kind: ExploreKind;
  id: string;
  title: string;
  subtitle?: string | null;
  snippet?: string | null;
  file_id?: string | null;
  file_name?: string | null;
  extra?: Record<string, unknown>;
};

export type ExploreSearchResponse = {
  q: string;
  kind: ExploreKind | null;
  offset: number;
  limit: number;
  total_estimate: number;
  items: ExploreHit[];
};

export async function getExploreCounts(): Promise<ExploreCounts> {
  const resp = await fetch(`${KB_API_URL}/explore/counts`, {
    headers: workspaceHeaders(),
  });
  return _handle<ExploreCounts>(resp);
}

export async function exploreSearch(opts: {
  q?: string;
  kind?: ExploreKind | null;
  offset?: number;
  limit?: number;
}): Promise<ExploreSearchResponse> {
  const params = new URLSearchParams();
  if (opts.q) params.set("q", opts.q);
  if (opts.kind) params.set("kind", opts.kind);
  if (opts.offset != null) params.set("offset", String(opts.offset));
  if (opts.limit != null) params.set("limit", String(opts.limit));
  const resp = await fetch(
    `${KB_API_URL}/explore/search?${params.toString()}`,
    { headers: workspaceHeaders() },
  );
  return _handle<ExploreSearchResponse>(resp);
}


// ---------------------------------------------------------------------------
// Schema Studio — Typed / Inferred / Vocabulary / Lineage / Versions
// ---------------------------------------------------------------------------

export type SchemaSummary = {
  id: string;
  name: string;
  description: string | null;
  domain_id?: string | null;
  lifecycle_state: string;
  current_version?: number;
};

export type SchemaListResp = { items: SchemaSummary[] };

export type SchemaEntity = {
  id: string;
  name: string;
  description: string | null;
  lifecycle_state: string;
};

export type SchemaField = {
  id: string;
  name: string;
  type: string | null;            // server returns 'type'
  nl_description?: string | null; // server returns 'nl_description'
  is_required?: boolean;
};

export type InferredField = {
  id: string;
  workspace_id: string;
  inferred_doc_type: string;
  canonical_name: string;
  description: string | null;
  value_type: string | null;
  n_docs_observed: number;
  prevalence: number;
  stability: number;
  value_type_confidence: number;
  is_promoted: boolean;
  promoted_schema_field_id: string | null;
  created_at: string | null;
};

export type VocabEntry = {
  id: string;
  domain_id: string;
  canonical_term: string;
  synonyms: string[];
  acronym_of: string | null;
  expansion: string | null;
  definition: string | null;
  source: string;          // 'discovered' | 'user_defined' | 'imported'
  confidence: number;
  n_docs_observed: number;
  active: boolean;
};

export type DocChainSummary = {
  id: string;
  type: string;                      // server: 'type' (e.g. 'contract_chain')
  title?: string | null;
  current_version_id: string | null;
  member_count: number;
  detection_confidence?: number | null;
  created_at: string | null;
};

export type SchemaVersionRow = {
  version: number;
  kind?: string;
  parent_version?: number | null;
  created_at: string | null;
  description?: string | null;
  created_by?: string | null;
};


export async function listSchemas(): Promise<SchemaSummary[]> {
  const resp = await fetch(`${KB_API_URL}/schemas`, {
    headers: workspaceHeaders(),
  });
  const body = await _handle<SchemaListResp>(resp);
  return body.items ?? [];
}

export async function listSchemaEntities(schemaId: string): Promise<SchemaEntity[]> {
  const resp = await fetch(
    `${KB_API_URL}/schemas/${schemaId}/entities`,
    { headers: workspaceHeaders() },
  );
  const body = await _handle<{ items: SchemaEntity[] }>(resp);
  return body.items ?? [];
}

export async function listSchemaEntityFields(
  schemaId: string, entityId: string,
): Promise<SchemaField[]> {
  const resp = await fetch(
    `${KB_API_URL}/schemas/${schemaId}/entities/${entityId}/fields`,
    { headers: workspaceHeaders() },
  );
  const body = await _handle<{ items: SchemaField[] }>(resp);
  return body.items ?? [];
}

export async function listInferredFields(opts: {
  doc_type?: string;
  only_promotable?: boolean;
  limit?: number;
} = {}): Promise<InferredField[]> {
  const params = new URLSearchParams();
  if (opts.doc_type) params.set("doc_type", opts.doc_type);
  if (opts.only_promotable) params.set("only_promotable", "true");
  if (opts.limit) params.set("limit", String(opts.limit));
  const qs = params.toString();
  const url = `${KB_API_URL}/schemas/inferred-fields${qs ? `?${qs}` : ""}`;
  const resp = await fetch(url, { headers: workspaceHeaders() });
  const body = await _handle<{ items: InferredField[] }>(resp);
  return body.items ?? [];
}

export async function listVocabulary(
  domainId: string, limit = 200,
): Promise<VocabEntry[]> {
  const resp = await fetch(
    `${KB_API_URL}/vocabulary?domain_id=${encodeURIComponent(domainId)}&limit=${limit}`,
    { headers: workspaceHeaders() },
  );
  const body = await _handle<{ items: VocabEntry[] }>(resp);
  return body.items ?? [];
}

export async function listDocChains(limit = 100): Promise<DocChainSummary[]> {
  const resp = await fetch(
    `${KB_API_URL}/chains?limit=${limit}`,
    { headers: workspaceHeaders() },
  );
  const body = await _handle<{ items: DocChainSummary[] }>(resp);
  return body.items ?? [];
}

export async function listSchemaVersions(
  schemaId: string, limit = 50,
): Promise<SchemaVersionRow[]> {
  const resp = await fetch(
    `${KB_API_URL}/schemas/${schemaId}/versions?limit=${limit}`,
    { headers: workspaceHeaders() },
  );
  const body = await _handle<{ items: SchemaVersionRow[] }>(resp);
  return body.items ?? [];
}

export async function promoteInferredField(fieldId: string): Promise<{
  inferred_field_id: string;
  schema_field_id: string;
  schema_entity_id: string;
}> {
  const resp = await fetch(
    `${KB_API_URL}/schemas/inferred-fields/${fieldId}/promote`,
    { method: "POST", headers: workspaceHeaders() },
  );
  return _handle(resp);
}

export async function renameInferredField(
  fieldId: string, canonicalName: string,
): Promise<InferredField> {
  const resp = await fetch(
    `${KB_API_URL}/schemas/inferred-fields/${fieldId}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json", ...workspaceHeaders() },
      body: JSON.stringify({ canonical_name: canonicalName }),
    },
  );
  return _handle(resp);
}

export async function discardInferredField(fieldId: string): Promise<number> {
  const resp = await fetch(
    `${KB_API_URL}/schemas/inferred-fields/${fieldId}`,
    { method: "DELETE", headers: workspaceHeaders() },
  );
  const body = await _handle<{ deleted: number }>(resp);
  return body.deleted ?? 0;
}

export async function postChat(
  query: string,
  opts: {
    idempotencyKey?: string;
    /** Chat-UX `@ doc filter` — scope retrieval to these file_ids. */
    fileIds?: string[];
    /** Override the planner-suggested mode. Defaults to 'H' (hybrid). */
    mode?: string;
  } = {},
): Promise<ChatResponse> {
  const idempotencyKey = opts.idempotencyKey ?? crypto.randomUUID();
  const body: Record<string, unknown> = { query, mode: opts.mode ?? "H" };
  if (opts.fileIds && opts.fileIds.length > 0) body.file_ids = opts.fileIds;
  const resp = await fetch(`${KB_API_URL}/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": idempotencyKey,
      ...workspaceHeaders(),
    },
    body: JSON.stringify(body),
  });
  return _handle(resp);
}


/** Live pipeline event from POST /chat/stream — each backend stage
 *  emits one of these as it happens. The `data` payload is opaque to
 *  the client; the UI reads stage-specific keys (label, score, n_hits,
 *  …) per event type. `t_ms` is always present and measured from the
 *  start of the chat request on the server. */
export type ChatStreamEvent = {
  event: string;
  data: Record<string, unknown> & { t_ms?: number };
};


/** Stream chat events from POST /chat/stream. Returns a Promise that
 *  resolves to the final ChatResponse envelope (extracted from the
 *  terminal `done` event). Handlers fire for every intermediate event
 *  so the UI can render a live progress timeline.
 *
 *  Why fetch + manual SSE parsing instead of native EventSource:
 *  EventSource is GET-only and can't carry the X-Test-Workspace header
 *  or a JSON body. So we manually split the response stream on the
 *  SSE `\n\n` block delimiter and parse `event: …\ndata: …` lines. */
export async function postChatStream(
  query: string,
  opts: {
    idempotencyKey?: string;
    fileIds?: string[];
    mode?: string;
    sessionId?: string;
  } = {},
  handlers: {
    onEvent?: (evt: ChatStreamEvent) => void;
    onError?: (err: Error) => void;
    signal?: AbortSignal;
  } = {},
): Promise<ChatResponse> {
  const idempotencyKey = opts.idempotencyKey ?? crypto.randomUUID();
  const body: Record<string, unknown> = { query, mode: opts.mode ?? "H" };
  if (opts.fileIds && opts.fileIds.length > 0) body.file_ids = opts.fileIds;
  if (opts.sessionId) body.session_id = opts.sessionId;

  const resp = await fetch(`${KB_API_URL}/chat/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": idempotencyKey,
      Accept: "text/event-stream",
      ...workspaceHeaders(),
    },
    body: JSON.stringify(body),
    signal: handlers.signal,
  });
  if (!resp.ok || !resp.body) {
    const errBody = await resp.text().catch(() => "");
    throw new KbApiError(
      resp.status,
      errBody,
      `chat/stream ${resp.status}: ${errBody.slice(0, 200)}`,
    );
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let final: ChatResponse | null = null;

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // SSE blocks are delimited by a blank line ("\n\n"). Process
      // whole blocks; keep the partial trailing fragment in `buffer`.
      let sep: number;
      while ((sep = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        const evt = parseSseBlock(block);
        if (!evt) continue;
        if (evt.event === "done") {
          final = evt.data as unknown as ChatResponse;
        }
        if (evt.event === "error") {
          const detail =
            (evt.data as Record<string, unknown>).detail ?? "stream error";
          throw new Error(String(detail));
        }
        handlers.onEvent?.(evt);
      }
    }
  } catch (err) {
    const e = err instanceof Error ? err : new Error(String(err));
    handlers.onError?.(e);
    throw e;
  }

  if (!final) {
    throw new Error("chat/stream ended without a `done` event");
  }
  return final;
}


/** Parse one SSE block ("event: ...\ndata: ..." lines) into a typed event.
 *  Returns null for blocks without a recognisable `event:` line. */
function parseSseBlock(block: string): ChatStreamEvent | null {
  let event: string | null = null;
  let data = "";
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) {
      event = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      // SSE spec: multiple data lines are joined with newlines. Most
      // backends emit one — ours included — but handle both shapes.
      data = data ? data + "\n" + line.slice("data:".length).trim()
                   : line.slice("data:".length).trim();
    }
  }
  if (!event) return null;
  let parsed: Record<string, unknown> = {};
  try {
    parsed = data ? JSON.parse(data) : {};
  } catch {
    parsed = {};
  }
  return { event, data: parsed };
}

/**
 * Stream the cached answer for a past chat query via Phase 9 SSE.
 * Useful for replaying an answer at "typing" speed in the UI.
 */
export function subscribeToChatStream(
  queryId: string,
  handlers: {
    onChunk?: (chunk: { text: string; offset: number }) => void;
    onDone?: (data: {
      refused?: boolean;
      refusal_reason?: string | null;
      citations?: Citation[];
      model_id?: string;
    }) => void;
    onError?: (err: unknown) => void;
  },
): () => void {
  const url = `${KB_API_URL}/chat/${queryId}/stream`;
  const es = new EventSource(url);
  es.addEventListener("chunk", (e) => {
    try {
      handlers.onChunk?.(JSON.parse((e as MessageEvent).data));
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
  es.onerror = (err) => handlers.onError?.(err);
  return () => es.close();
}

// Helper for the UI: render an answer with inline [hit_id] citations as
// clickable badges. Returns an array of {kind: 'text' | 'cite', value, ...}
// segments — easier to render in JSX than dangerous innerHTML.
export type AnswerSegment =
  | { kind: "text"; value: string }
  | { kind: "cite"; hitId: string; index: number };

export function segmentAnswer(
  answer: string,
  citations: Citation[],
): AnswerSegment[] {
  const ids = new Map(citations.map((c, i) => [c.hit_id.slice(0, 8), i]));
  const segments: AnswerSegment[] = [];
  // Match either full UUID prefix [xxxxxxxx-xxxx-...] or short [xxxxxxxx].
  const re = /\[([0-9a-f]{8}(?:-[0-9a-f]{4}){0,4}(?:-[0-9a-f]{12})?)\]/gi;
  let last = 0;
  for (const m of answer.matchAll(re)) {
    if (m.index === undefined) continue;
    const text = answer.slice(last, m.index);
    if (text) segments.push({ kind: "text", value: text });
    const raw = m[1];
    const shortId = raw.slice(0, 8);
    const idx = ids.get(shortId) ?? -1;
    segments.push({ kind: "cite", hitId: raw, index: idx });
    last = m.index + m[0].length;
  }
  if (last < answer.length) {
    segments.push({ kind: "text", value: answer.slice(last) });
  }
  return segments;
}

export { KbApiError };


// ---------------------------------------------------------------------------
// Dashboard (B7 / WA-14) — /dashboard/summary + /dashboard/needs-attention
// ---------------------------------------------------------------------------


export type CountByLabel = { label: string; count: number };


export type DashboardSummary = {
  workspace_id: string;
  files_total: number;
  files_by_lifecycle: CountByLabel[];
  files_by_doc_type: CountByLabel[];
  files_by_doc_status: CountByLabel[];
  files_low_authority: number;
  queries_total: number;
  queries_last_24h: number;
  queries_by_mode: CountByLabel[];
  queries_by_faithfulness: CountByLabel[];
  queries_refused: number;
  queries_low_confidence: number;
  conflicts_open: number;
  conflicts_resolved: number;
  corrections_open: number;
  corrections_fixing: number;
  regressions_active: number;
  sessions_active_24h: number;
  audit_log_total_rows: number;
};


export type NeedsAttentionKind =
  | "conflict"
  | "correction"
  | "low_confidence_chat"
  | "low_authority_file";


export type NeedsAttentionItem = {
  kind: NeedsAttentionKind;
  id: string;
  title: string;
  severity: "blocker" | "important" | "minor" | "enhancement";
  created_at: string;
  payload: Record<string, unknown>;
};


export async function getDashboardSummary(): Promise<DashboardSummary> {
  const url = `${KB_API_URL}/dashboard/summary`;
  const resp = await fetch(url, { headers: workspaceHeaders() });
  if (!resp.ok) {
    throw new KbApiError(
      resp.status,
      await resp.text().catch(() => ""),
      `GET /dashboard/summary failed: ${resp.status}`,
    );
  }
  return (await resp.json()) as DashboardSummary;
}


export async function getNeedsAttention(
  limit = 50,
): Promise<NeedsAttentionItem[]> {
  const url = `${KB_API_URL}/dashboard/needs-attention?limit=${limit}`;
  const resp = await fetch(url, { headers: workspaceHeaders() });
  if (!resp.ok) {
    throw new KbApiError(
      resp.status,
      await resp.text().catch(() => ""),
      `GET /dashboard/needs-attention failed: ${resp.status}`,
    );
  }
  const body = (await resp.json()) as { items: NeedsAttentionItem[] };
  return body.items ?? [];
}
