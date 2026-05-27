"use client";

import { useCallback, useState } from "react";
import Link from "next/link";
import {
  ChevronRight,
  FileText,
  Image as ImageIcon,
  Mail,
  Table as TableIcon,
} from "lucide-react";
import {
  type FileDetails,
  blobUrl,
  getFileDetails,
  reExtractFile,
} from "@/lib/api";
import { useUploadStore, type FileRow } from "@/lib/state";
import { StageBadge } from "./StageBadge";
import type { FilterKey } from "./FilterBar";


type Props = {
  filter: FilterKey;
  query: string;
  /** True while the next page is in-flight. */
  loadingMore?: boolean;
  /** Last error from the pagination fetch (rendered inline if set). */
  loadError?: string | null;
  /** Called when the user clicks "Load more". The parent owns the offset
   *  bookkeeping; this component just renders the trigger. */
  onLoadMore?: () => void;
};


function matchesFilter(row: FileRow, filter: FilterKey, q: string): boolean {
  if (filter === "ready" && row.lifecycle_state !== "ready") return false;
  if (filter === "failed" && row.lifecycle_state !== "failed") return false;
  if (filter === "processing") {
    if (row.lifecycle_state === "ready" || row.lifecycle_state === "failed") {
      return false;
    }
  }
  if (filter === "attention") {
    const lowAuth =
      row.source_authority !== null &&
      row.source_authority !== undefined &&
      row.source_authority < 0.5;
    const notLive = row.doc_status && row.doc_status !== "live";
    const failed = row.lifecycle_state === "failed";
    if (!(lowAuth || notLive || failed)) return false;
  }
  if (q) {
    const hay = `${row.name} ${row.mime_type} ${row.inferred_doc_type ?? ""}`.toLowerCase();
    if (!hay.includes(q.toLowerCase())) return false;
  }
  return true;
}


function elapsedLabel(ms: number): string {
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}


function iconFor(mime: string) {
  if (mime === "message/rfc822") return Mail;
  if (mime.startsWith("image/")) return ImageIcon;
  if (mime.includes("spreadsheet")) return TableIcon;
  return FileText;
}


export function FilesTable({
  filter,
  query,
  loadingMore = false,
  loadError = null,
  onLoadMore,
}: Props) {
  const { state } = useUploadStore();
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [details, setDetails] = useState<Record<string, FileDetails | "loading" | "error">>({});

  const onToggle = useCallback(
    async (id: string) => {
      setExpanded((prev) => {
        const next = new Set(prev);
        if (next.has(id)) {
          next.delete(id);
        } else {
          next.add(id);
        }
        return next;
      });
      if (!details[id]) {
        setDetails((d) => ({ ...d, [id]: "loading" }));
        try {
          const data = await getFileDetails(id);
          setDetails((d) => ({ ...d, [id]: data }));
        } catch {
          setDetails((d) => ({ ...d, [id]: "error" }));
        }
      }
    },
    [details],
  );

  const rows = state.order
    .map((id) => state.rows[id])
    .filter((row): row is FileRow => !!row && matchesFilter(row, filter, query));

  const loadedCount = state.order.length;
  const totalCount = state.total ?? loadedCount;
  const hasMore = state.total !== null && loadedCount < state.total;
  const isFiltered = filter !== "all" || query.length > 0;

  if (rows.length === 0) {
    return (
      <div className="space-y-3">
        <div className="rounded-lg border border-dashed border-zinc-200 bg-white px-6 py-10 text-center">
          <div className="text-sm text-zinc-700">
            {loadedCount === 0
              ? "No uploads yet."
              : isFiltered && hasMore
                ? `No files in the first ${loadedCount} match the current filter.`
                : "No files match the current filter."}
          </div>
          <div className="text-xs text-zinc-500 mt-1">
            {loadedCount === 0
              ? "Drop files above to get started."
              : isFiltered && hasMore
                ? "Try \"Load more\" below — matches may exist further back."
                : "Adjust filters or drop more files above."}
          </div>
        </div>
        {hasMore && (
          <LoadMoreFooter
            loadedCount={loadedCount}
            totalCount={totalCount}
            visibleCount={0}
            isFiltered={isFiltered}
            loadingMore={loadingMore}
            loadError={loadError}
            onLoadMore={onLoadMore}
          />
        )}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-zinc-200 overflow-hidden bg-white">
        <div className="grid grid-cols-[1fr_160px_120px_180px_120px_80px] gap-3 px-4 py-2.5 text-[11px] uppercase tracking-wider text-zinc-500 bg-zinc-50 border-b border-zinc-200">
        <div>File</div>
        <div>Type</div>
        <div>Status</div>
        <div>Stage</div>
        <div className="text-right">Elapsed</div>
        <div className="text-right">Detected</div>
      </div>

      {rows.map((row) => {
        const Icon = iconFor(row.mime_type);
        const elapsed = elapsedLabel(row.updatedAt - row.startedAt);
        const isOpen = expanded.has(row.id);
        const detail = details[row.id];
        return (
          <div
            key={row.id}
            className="border-b border-zinc-100 last:border-0 transition-colors"
            data-testid="file-row"
            data-file-id={row.id}
            data-state={row.lifecycle_state}
          >
            <button
              type="button"
              onClick={() => onToggle(row.id)}
              className="w-full text-left grid grid-cols-[1fr_160px_120px_180px_120px_80px] gap-3 px-4 py-3 items-center hover:bg-zinc-50/60"
              aria-expanded={isOpen}
              data-testid="file-row-toggle"
            >
              <div className="flex items-center gap-2 min-w-0">
                <ChevronRight
                  className={`w-3.5 h-3.5 text-zinc-400 flex-shrink-0 transition-transform ${isOpen ? "rotate-90" : ""}`}
                  strokeWidth={1.75}
                />
                <Icon
                  className="w-3.5 h-3.5 text-zinc-500 flex-shrink-0"
                  strokeWidth={1.75}
                  aria-hidden
                />
                <Link
                  href={`/files/${row.id}`}
                  className="text-sm text-zinc-900 truncate hover:underline"
                  title={row.name}
                  data-testid="file-row-link"
                >
                  {row.name}
                </Link>
              </div>
              <div className="text-xs text-zinc-700 truncate" title={row.inferred_doc_type ?? row.mime_type}>
                {row.inferred_doc_type ? (
                  <span className="mono">{row.inferred_doc_type}</span>
                ) : (
                  <span className="text-zinc-400 mono">classifying…</span>
                )}
              </div>
              <div>
                <StatusBadges row={row} />
              </div>
              <StageBadge state={row.lifecycle_state} />
              <div className="text-xs text-zinc-500 mono text-right">{elapsed}</div>
              <div className="text-xs text-zinc-600 mono text-right truncate">
                <DetectedSummary detail={detail} fallback="—" />
              </div>
            </button>

            {row.error && (
              <div className="px-4 pb-3 text-xs text-red-600 mono">{row.error}</div>
            )}

            {isOpen && (
              <ExpandedDetail row={row} detail={detail} />
            )}
          </div>
        );
      })}
      </div>

      <LoadMoreFooter
        loadedCount={loadedCount}
        totalCount={totalCount}
        visibleCount={rows.length}
        isFiltered={isFiltered}
        loadingMore={loadingMore}
        loadError={loadError}
        onLoadMore={hasMore ? onLoadMore : undefined}
      />
    </div>
  );
}


/** Footer rendered below the files table. Shows where the user is in the
 *  paginated dataset and offers a "Load more" button when more rows remain.
 *
 *  Three distinct cases:
 *    - Fully loaded, no filter   → "All N files loaded"
 *    - Fully loaded, filtered    → "X of N match the current filter"
 *    - More to fetch             → "Showing N of M · [Load more]"  (+ filter hint)
 *
 *  Surfacing "filtered" vs "loaded" totals matters because filtering happens
 *  client-side: a match further back in the timeline isn't visible until the
 *  user pages forward. The hint nudges them to keep loading. */
function LoadMoreFooter({
  loadedCount,
  totalCount,
  visibleCount,
  isFiltered,
  loadingMore,
  loadError,
  onLoadMore,
}: {
  loadedCount: number;
  totalCount: number;
  visibleCount: number;
  isFiltered: boolean;
  loadingMore: boolean;
  loadError: string | null;
  onLoadMore?: () => void;
}) {
  const hasMore = !!onLoadMore && loadedCount < totalCount;

  let summary: string;
  if (!hasMore) {
    summary = isFiltered
      ? `${visibleCount} of ${totalCount} match the current filter`
      : `All ${totalCount} files loaded`;
  } else if (isFiltered) {
    summary = `${visibleCount} match · ${loadedCount} of ${totalCount} loaded`;
  } else {
    summary = `Showing ${loadedCount} of ${totalCount}`;
  }

  return (
    <div
      className="flex items-center justify-between gap-3 px-1 text-xs text-zinc-500"
      data-testid="files-pagination"
    >
      <div className="mono" data-testid="files-pagination-summary">
        {summary}
        {isFiltered && hasMore && (
          <span className="ml-2 text-zinc-400">
            (filter applies to loaded rows only — load more to find matches further back)
          </span>
        )}
      </div>
      <div className="flex items-center gap-3">
        {loadError && (
          <span className="text-red-600 mono" data-testid="files-pagination-error">
            {loadError}
          </span>
        )}
        {hasMore && (
          <button
            type="button"
            onClick={onLoadMore}
            disabled={loadingMore}
            data-testid="files-load-more"
            className="rounded-md border border-zinc-200 bg-white px-3 py-1.5 text-xs text-zinc-700 hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {loadingMore ? "Loading…" : "Load more"}
          </button>
        )}
      </div>
    </div>
  );
}


function StatusBadges({ row }: { row: FileRow }) {
  const badges: Array<{ label: string; tone: "neutral" | "warn" | "ok" }> = [];

  if (row.doc_status && row.doc_status !== "live") {
    badges.push({
      label: row.doc_status,
      tone: row.doc_status === "superseded" ? "warn" : "neutral",
    });
  } else if (row.doc_status === "live") {
    badges.push({ label: "live", tone: "ok" });
  }

  if (
    row.source_authority !== null &&
    row.source_authority !== undefined
  ) {
    const auth = row.source_authority;
    const tone: "neutral" | "warn" | "ok" =
      auth < 0.5 ? "warn" : auth >= 0.8 ? "ok" : "neutral";
    badges.push({ label: `auth ${auth.toFixed(2)}`, tone });
  }

  if (badges.length === 0) {
    return <span className="text-[11px] text-zinc-400 mono">—</span>;
  }

  return (
    <div className="flex items-center gap-1 flex-wrap">
      {badges.map((b) => (
        <span
          key={b.label}
          className={`text-[10px] mono px-1.5 py-0.5 rounded ${
            b.tone === "warn"
              ? "bg-amber-50 text-amber-800 border border-amber-200"
              : b.tone === "ok"
                ? "bg-zinc-50 text-zinc-700 border border-zinc-200"
                : "bg-zinc-100 text-zinc-600"
          }`}
        >
          {b.label}
        </span>
      ))}
    </div>
  );
}


function DetectedSummary({
  detail,
  fallback,
}: {
  detail: FileDetails | "loading" | "error" | undefined;
  fallback: string;
}) {
  if (!detail) return <span className="text-zinc-400">{fallback}</span>;
  if (detail === "loading") return <span className="text-zinc-400">…</span>;
  if (detail === "error") return <span className="text-zinc-400">—</span>;
  const parts: string[] = [];
  if (detail.n_entities_linked > 0) parts.push(`${detail.n_entities_linked} ent`);
  if (detail.n_sub_entities > 0) parts.push(`${detail.n_sub_entities} au`);
  if (parts.length === 0 && detail.n_chunks > 0) parts.push(`${detail.n_chunks} ch`);
  return <span>{parts.join(" · ") || "—"}</span>;
}


function ExpandedDetail({
  row,
  detail,
}: {
  row: FileRow;
  detail: FileDetails | "loading" | "error" | undefined;
}) {
  return (
    <div
      className="px-12 py-4 bg-zinc-50/70 border-t border-zinc-100"
      data-testid="file-row-detail"
    >
      {detail === "loading" && (
        <div className="text-xs text-zinc-500">Loading details…</div>
      )}
      {detail === "error" && (
        <div className="text-xs text-red-600 mono">
          Failed to load details from /files/{row.id}/details
        </div>
      )}
      {detail && detail !== "loading" && detail !== "error" && (
        <DetailBody detail={detail} />
      )}
    </div>
  );
}


function DetailBody({ detail }: { detail: FileDetails }) {
  const f = detail.file;
  const stages = computeStageTimeline(detail.lifecycle);
  const sizeKb = (f.size_bytes / 1024).toFixed(1);

  return (
    <div className="space-y-4">
      {/* 5-stage timeline */}
      <div className="grid grid-cols-5 gap-2">
        {stages.map((s) => (
          <StageColumn key={s.label} stage={s} />
        ))}
      </div>

      {/* Rollup row: doc-type · counts · chain */}
      <div className="border-t border-zinc-200 pt-3 grid grid-cols-3 gap-4 text-xs">
        <div>
          <div className="text-[10px] uppercase tracking-wider text-zinc-400 mb-1.5">
            Doc-type
          </div>
          <div className="text-zinc-700">
            {f.inferred_doc_type ?? <span className="text-zinc-400">unclassified</span>}
            {f.source_authority !== null && f.source_authority !== undefined && (
              <span className="ml-2 mono text-[10px] text-zinc-500">
                authority {f.source_authority.toFixed(2)}
              </span>
            )}
          </div>
          {f.source_authority_reason && (
            <div className="mt-1 text-zinc-500 text-[11px]">
              {f.source_authority_reason}
            </div>
          )}
          {f.doc_status && f.doc_status !== "live" && (
            <div className="mt-1 text-amber-700 mono text-[11px]">
              status: {f.doc_status}
            </div>
          )}
        </div>

        <div>
          <div className="text-[10px] uppercase tracking-wider text-zinc-400 mb-1.5">
            Extracted
          </div>
          <div className="text-zinc-700 grid grid-cols-2 gap-x-3 gap-y-1 mono">
            <span className="text-zinc-500">pages</span>
            <span>{detail.n_pages}</span>
            <span className="text-zinc-500">chunks</span>
            <span>{detail.n_chunks}</span>
            <span className="text-zinc-500">mentions</span>
            <span>{detail.n_mentions}</span>
            <span className="text-zinc-500">sub-entities</span>
            <span>{detail.n_sub_entities}</span>
            <span className="text-zinc-500">entities</span>
            <span>{detail.n_entities_linked}</span>
            <span className="text-zinc-500">triples</span>
            <span>{detail.n_triples}</span>
          </div>
        </div>

        <div>
          <div className="text-[10px] uppercase tracking-wider text-zinc-400 mb-1.5">
            Source
          </div>
          <div className="text-zinc-700 mono">{f.mime_type}</div>
          <div className="mt-1 text-zinc-500 mono text-[11px]">{sizeKb} KB</div>
          <div className="mt-1 text-zinc-400 mono text-[10px] truncate" title={f.content_sha}>
            sha {f.content_sha.slice(0, 16)}…
          </div>
          {detail.chain_id && (
            <div className="mt-2 text-zinc-700 text-[11px]">
              <span className="text-zinc-500">chain:</span>{" "}
              <span className="mono">{detail.chain_role ?? "member"}</span>
              {detail.chain_version_index !== null && (
                <span className="mono text-zinc-500"> v{detail.chain_version_index}</span>
              )}
              {detail.is_current_version && (
                <span className="ml-1 text-[10px] mono px-1 py-0.5 rounded bg-zinc-100 text-zinc-700">
                  current
                </span>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Action row — Doc Detail / Preview PDF / Re-extract / (failed
          only) Re-parse with VLM fallback. Re-extract is workspace +
          per-file idempotent on the backend; the lifecycle events
          stream back into this very row via SSE so the user sees the
          new stage timestamps appear in place. */}
      <RowActions f={f} lifecycleCount={detail.lifecycle.length} />
    </div>
  );
}


function RowActions({
  f,
  lifecycleCount,
}: {
  f: FileDetails["file"];
  lifecycleCount: number;
}) {
  const [busy, setBusy] = useState<"reextract" | "reparse" | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const failed = f.lifecycle_state === "failed";

  async function trigger(stage: "extraction" | "parsing") {
    setBusy(stage === "extraction" ? "reextract" : "reparse");
    setMsg(null);
    setErr(null);
    try {
      const r = await reExtractFile(f.id, stage);
      setMsg(
        `${stage === "parsing" ? "Re-parse" : "Re-extraction"} queued · ${r.deferred.length} task${r.deferred.length === 1 ? "" : "s"}`,
      );
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to enqueue");
    } finally {
      setBusy(null);
    }
  }

  // Preview-PDF is shown for any file MIME the browser can render
  // inline. We rely on the backend Content-Type + `inline`
  // Content-Disposition (set in src/kb/api/files.py:get_file_blob).
  const previewable =
    f.mime_type === "application/pdf" ||
    f.mime_type.startsWith("image/") ||
    f.mime_type.startsWith("text/");

  return (
    <div className="border-t border-zinc-200 pt-3 flex items-center gap-3 text-xs text-zinc-500 flex-wrap">
      <a
        href={`/files/${f.id}`}
        className="hover:text-zinc-900 inline-flex items-center gap-1.5"
      >
        Open Doc Detail →
      </a>
      {previewable && (
        <a
          href={blobUrl(f.id)}
          target="_blank"
          rel="noopener noreferrer"
          className="hover:text-zinc-900 inline-flex items-center gap-1.5"
          data-testid="file-row-preview"
        >
          Preview {f.mime_type === "application/pdf" ? "PDF" : "file"} →
        </a>
      )}
      <button
        type="button"
        onClick={() => trigger("extraction")}
        disabled={busy !== null}
        className="hover:text-zinc-900 inline-flex items-center gap-1.5 cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
        data-testid="file-row-reextract"
      >
        {busy === "reextract" ? "Queueing…" : "Re-extract"}
      </button>
      {failed && (
        <button
          type="button"
          onClick={() => trigger("parsing")}
          disabled={busy !== null}
          className="hover:text-amber-900 text-amber-700 inline-flex items-center gap-1.5 cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
          title="Re-run parse + chunk + embed + extract from scratch — useful when the original parse failed (try VLM / OCR fallback)."
          data-testid="file-row-reparse"
        >
          {busy === "reparse" ? "Queueing…" : "Re-parse from scratch"}
        </button>
      )}
      <a
        href="/explore"
        className="hover:text-zinc-900 inline-flex items-center gap-1.5"
      >
        Explore →
      </a>
      <span className="ml-auto mono text-zinc-400">
        {msg && (
          <span className="text-zinc-700 mr-2" data-testid="file-row-action-msg">
            {msg}
          </span>
        )}
        {err && (
          <span className="text-red-600 mr-2" data-testid="file-row-action-err">
            {err}
          </span>
        )}
        {lifecycleCount} lifecycle events
      </span>
    </div>
  );
}


type StageInfo = {
  label: string;
  detail: string;
  doneAt: string | null;
};


// Project the 18+ lifecycle events into the 5 stage buckets the
// prototype shows: Parse · Contextualize · Extract · Resolve · Index.
// We pick the most informative event per bucket to drive the
// sub-label, and use the latest matching event's `created_at` as the
// stage completion time.
function computeStageTimeline(events: FileDetails["lifecycle"]): StageInfo[] {
  const byEvent = new Map<string, (typeof events)[number]>();
  for (const ev of events) byEvent.set(ev.event, ev);

  const parse = byEvent.get("parse_done");
  const chunk = byEvent.get("chunking_done");
  const ctx = byEvent.get("contextualization_done");
  const embed = byEvent.get("embedding_done");
  const raptor = byEvent.get("raptor_build_done");
  const mentions = byEvent.get("mentions_extracted");
  // PR #42 collapsed the legacy "fields_extracted" + "atomic_units_extracted"
  // pair into a single "kv_tables_extracted" event. We keep the legacy
  // lookups for back-compat with old lifecycle history rows; new events
  // come through `kvTables`.
  const kvTables = byEvent.get("kv_tables_extracted");
  const fields = byEvent.get("fields_extracted");
  const units = byEvent.get("atomic_units_extracted");
  const identities = byEvent.get("identities_resolved");
  const graph = byEvent.get("graph_built");

  const at = (e?: (typeof events)[number]) =>
    e ? new Date(e.created_at).toLocaleTimeString() : null;

  return [
    {
      label: "Parse",
      detail: parse
        ? `${(parse.payload as { parser?: string }).parser ?? "unknown"} · ${(parse.payload as { pages?: number }).pages ?? 0} pages`
        : "—",
      doneAt: at(parse),
    },
    {
      label: "Contextualize",
      detail: ctx
        ? `${(chunk?.payload as { chunk_count?: number })?.chunk_count ?? "?"} chunks · ${(ctx.payload as { model_id?: string }).model_id ?? "?"}`
        : chunk
          ? `${(chunk.payload as { chunk_count?: number }).chunk_count ?? "?"} chunks`
          : "—",
      doneAt: at(ctx ?? chunk),
    },
    {
      label: "Extract",
      detail: kvTables
        ? (() => {
            const p = kvTables.payload as {
              scalar_count?: number;
              row_count?: number;
              unit_types?: string[];
            };
            const mc = (mentions?.payload as { mention_count?: number })?.mention_count ?? 0;
            const types = p.unit_types?.length ?? 0;
            return `${mc} mentions · ${p.scalar_count ?? 0} fields · ${p.row_count ?? 0} sub-entities${types > 0 ? ` (${types} type${types === 1 ? "" : "s"})` : ""}`;
          })()
        : fields
          ? `${(mentions?.payload as { mention_count?: number })?.mention_count ?? 0} mentions · ${(fields.payload as { field_count?: number }).field_count ?? 0} fields`
          : mentions
            ? `${(mentions.payload as { mention_count?: number }).mention_count ?? 0} mentions`
            : "—",
      doneAt: at(kvTables ?? fields ?? mentions ?? units),
    },
    {
      label: "Resolve",
      detail: identities
        ? `${(identities.payload as { mention_count?: number }).mention_count ?? 0} → entities`
        : "—",
      doneAt: at(identities),
    },
    {
      label: "Index",
      detail: graph
        ? `${(graph.payload as { edges_upserted?: number }).edges_upserted ?? 0} edges`
        : embed
          ? `embed ${(embed.payload as { embedding_count?: number }).embedding_count ?? 0}`
          : raptor
            ? `raptor ${(raptor.payload as { leaf_count?: number }).leaf_count ?? 0}`
            : "—",
      doneAt: at(graph ?? raptor ?? embed),
    },
  ];
}


function StageColumn({ stage }: { stage: StageInfo }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-zinc-400">
        {stage.label}
      </div>
      <div className="text-xs text-zinc-700 mt-0.5">{stage.detail}</div>
      <div className="text-[11px] text-zinc-500 mono">
        {stage.doneAt ? `done ${stage.doneAt}` : "pending"}
      </div>
    </div>
  );
}
