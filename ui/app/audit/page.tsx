"use client";

/**
 * /audit — immutable query log + hash-chained integrity check.
 *
 * The "trust" surface — every /chat call lands a row here with full
 * provenance: mode, CRAG score, refusal reason, latency, model. The
 * top bar runs the hash-chain integrity walk so an operator can show
 * "the chain is intact" at a glance.
 *
 * Layout follows prototype/audit.html:
 *   1. Top-bar status (Queries last 24h · Refused · Avg latency ·
 *      Hash chain integrity badge).
 *   2. Filter row: endpoint / mode / status (refused | grounded) /
 *      free-text query search.
 *   3. Cursor-paginated table — newest first. Each row expands to
 *      show the truncated answer + a "Replay" button (just opens
 *      /chat?q=… so the user can ask it again in a fresh session).
 *
 * No backend changes — uses /audit + /audit-log/integrity that already
 * ship.
 */

import { useEffect, useMemo, useState } from "react";
import {
  ScrollText, Filter, Search, ShieldCheck, ShieldAlert,
  Loader2, ChevronRight, ExternalLink, Clock,
} from "lucide-react";
import {
  listAudit, getAuditIntegrity,
  type AuditEntry, type IntegrityResponse,
} from "@/lib/api";
import { Sidebar } from "@/components/Sidebar";


const PAGE_SIZE = 50;

type StatusFilter = "all" | "grounded" | "refused";
type EndpointFilter = "all" | "chat" | "search";


export default function AuditPage() {
  const [items, setItems] = useState<AuditEntry[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [integrity, setIntegrity] = useState<IntegrityResponse | null>(null);
  const [integrityErr, setIntegrityErr] = useState<string | null>(null);

  // Filters are applied client-side over the loaded rows. Pagination is
  // server-side, so to find matches "further back" the user clicks
  // Load more. We surface that hint in the footer.
  const [status, setStatus] = useState<StatusFilter>("all");
  const [endpoint, setEndpoint] = useState<EndpointFilter>("all");
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  // Initial load: page + integrity walk in parallel.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([
      listAudit({ limit: PAGE_SIZE }),
      getAuditIntegrity().catch((err) => {
        setIntegrityErr(err instanceof Error ? err.message : String(err));
        return null;
      }),
    ])
      .then(([page, integ]) => {
        if (cancelled) return;
        setItems(page.items);
        setNextCursor(page.next_cursor);
        if (integ) setIntegrity(integ);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  async function loadMore() {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const page = await listAudit({ cursor: nextCursor, limit: PAGE_SIZE });
      setItems((prev) => [...prev, ...page.items]);
      setNextCursor(page.next_cursor);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoadingMore(false);
    }
  }

  // Client-side filter — keeps the UI snappy. For deep filtering across
  // 10k+ rows we'd push these to the backend; today the API ships row-
  // level filters but doesn't accept them yet.
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return items.filter((it) => {
      if (status === "refused" && !it.refused) return false;
      if (status === "grounded" && it.refused) return false;
      if (endpoint !== "all" && !it.endpoint.includes(endpoint)) return false;
      if (q) {
        const hay = `${it.query} ${it.answer ?? ""} ${it.model_id ?? ""}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [items, status, endpoint, query]);

  // Rollups for the top-bar stat cards.
  const stats = useMemo(() => rollup(items), [items]);

  return (
    <div className="flex h-full">
      <Sidebar current="audit" />
      <main className="flex-1 flex flex-col min-w-0 bg-white">
        <TopBar
          integrity={integrity}
          integrityErr={integrityErr}
          totalLoaded={items.length}
        />

        <div className="flex-1 overflow-y-auto">
          <div className="max-w-6xl mx-auto px-8 py-6">
            {error && (
              <div className="mb-4 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
                Failed to load audit: <span className="mono">{error}</span>
              </div>
            )}

            {/* Stat strip — derived from loaded rows. Honest about
                "this is what we've fetched", not "all-time". */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
              <Stat label="Queries loaded" value={items.length} sub="newest first" />
              <Stat label="Refused" value={stats.refused} sub={stats.refused > 0 ? `${Math.round(stats.refusedPct * 100)}% refusal rate` : "all answered"} tone={stats.refused > 0 ? "warn" : undefined} />
              <Stat label="Avg latency" value={stats.avgLatencyMs != null ? `${stats.avgLatencyMs}ms` : "—"} sub={stats.p95LatencyMs != null ? `p95 ${stats.p95LatencyMs}ms` : ""} />
              <Stat label="Avg CRAG" value={stats.avgCragScore != null ? stats.avgCragScore.toFixed(2) : "—"} sub="0-1; higher = more grounded" />
            </div>

            {/* Filter row */}
            <div className="flex flex-wrap items-center gap-2 mb-3">
              <div className="flex items-center gap-1.5 text-[11px] text-zinc-500">
                <Filter className="w-3.5 h-3.5" />
                Filter:
              </div>
              <select
                value={endpoint}
                onChange={(e) => setEndpoint(e.target.value as EndpointFilter)}
                className="text-xs px-2 py-1 rounded-md border border-zinc-200 bg-white mono cursor-pointer"
                data-testid="audit-filter-endpoint"
              >
                <option value="all">All endpoints</option>
                <option value="chat">/chat</option>
                <option value="search">/search</option>
              </select>
              <select
                value={status}
                onChange={(e) => setStatus(e.target.value as StatusFilter)}
                className="text-xs px-2 py-1 rounded-md border border-zinc-200 bg-white mono cursor-pointer"
                data-testid="audit-filter-status"
              >
                <option value="all">All status</option>
                <option value="grounded">Grounded</option>
                <option value="refused">Refused</option>
              </select>
              <div className="relative flex-1 min-w-[200px]">
                <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-zinc-400" />
                <input
                  type="text"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search query text, answer, model…"
                  className="w-full text-xs pl-7 pr-3 py-1.5 rounded-md border border-zinc-200 focus:outline-none focus:border-zinc-400"
                  data-testid="audit-filter-q"
                />
              </div>
              <div className="text-[11px] text-zinc-400 mono ml-auto">
                {filtered.length} of {items.length} match
              </div>
            </div>

            {/* Table */}
            {loading ? (
              <div className="flex items-center justify-center py-20 text-zinc-400">
                <Loader2 className="w-5 h-5 animate-spin" />
              </div>
            ) : filtered.length === 0 ? (
              <div className="rounded-lg border border-dashed border-zinc-200 px-6 py-10 text-center text-sm text-zinc-500">
                {items.length === 0
                  ? "No queries logged yet. Ask something in /chat."
                  : "No matches — try clearing filters or Load more from further back."}
              </div>
            ) : (
              <div className="rounded-lg border border-zinc-200 overflow-hidden bg-white">
                <div className="grid grid-cols-[140px_1fr_80px_80px_80px_24px] gap-3 px-4 py-2.5 text-[11px] uppercase tracking-wider text-zinc-500 bg-zinc-50 border-b border-zinc-200">
                  <div>When</div>
                  <div>Query</div>
                  <div className="text-right">Mode</div>
                  <div className="text-right">CRAG</div>
                  <div className="text-right">Latency</div>
                  <div></div>
                </div>
                {filtered.map((row) => (
                  <AuditRow
                    key={row.id}
                    row={row}
                    expanded={expanded.has(row.id)}
                    onToggle={() => {
                      setExpanded((prev) => {
                        const next = new Set(prev);
                        if (next.has(row.id)) next.delete(row.id);
                        else next.add(row.id);
                        return next;
                      });
                    }}
                  />
                ))}
              </div>
            )}

            {/* Pagination */}
            <div className="mt-4 flex items-center justify-between text-xs text-zinc-500">
              <div className="mono">
                Showing {items.length}{nextCursor ? "" : " (no more rows)"}
              </div>
              {nextCursor && (
                <button
                  type="button"
                  onClick={loadMore}
                  disabled={loadingMore}
                  className="rounded-md border border-zinc-200 bg-white px-3 py-1.5 hover:bg-zinc-50 cursor-pointer disabled:opacity-50"
                  data-testid="audit-load-more"
                >
                  {loadingMore ? (
                    <span className="flex items-center gap-1.5">
                      <Loader2 className="w-3 h-3 animate-spin" /> Loading…
                    </span>
                  ) : (
                    "Load more"
                  )}
                </button>
              )}
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}


function TopBar({
  integrity,
  integrityErr,
  totalLoaded,
}: {
  integrity: IntegrityResponse | null;
  integrityErr: string | null;
  totalLoaded: number;
}) {
  return (
    <header className="h-12 flex-shrink-0 border-b border-zinc-200 flex items-center px-5 gap-3">
      <div className="flex items-center gap-2 text-sm">
        <ScrollText className="w-4 h-4 text-zinc-500" strokeWidth={1.75} />
        <span className="text-zinc-900">Audit</span>
        <span className="text-[11px] text-zinc-400 mono">
          immutable · every answer is reproducible
        </span>
      </div>

      <div className="ml-auto flex items-center gap-3">
        <span className="text-[11px] text-zinc-400 mono">
          {totalLoaded} queries loaded
        </span>
        <IntegrityBadge integrity={integrity} err={integrityErr} />
      </div>
    </header>
  );
}


function IntegrityBadge({
  integrity,
  err,
}: {
  integrity: IntegrityResponse | null;
  err: string | null;
}) {
  if (err) {
    return (
      <span
        className="flex items-center gap-1.5 px-2 py-1 rounded-md text-[11px] mono bg-zinc-100 text-zinc-600"
        title={err}
        data-testid="audit-integrity"
      >
        <Clock className="w-3 h-3" />
        integrity: check failed
      </span>
    );
  }
  if (!integrity) {
    return (
      <span className="flex items-center gap-1.5 px-2 py-1 rounded-md text-[11px] mono bg-zinc-100 text-zinc-500">
        <Loader2 className="w-3 h-3 animate-spin" />
        checking integrity…
      </span>
    );
  }
  if (integrity.ok) {
    return (
      <span
        className="flex items-center gap-1.5 px-2 py-1 rounded-md text-[11px] mono bg-emerald-50 text-emerald-800 border border-emerald-200"
        title={`SHA-256 chain walked ${integrity.total_rows} rows — no divergence.`}
        data-testid="audit-integrity"
      >
        <ShieldCheck className="w-3 h-3" />
        chain intact · {integrity.total_rows} rows
      </span>
    );
  }
  return (
    <span
      className="flex items-center gap-1.5 px-2 py-1 rounded-md text-[11px] mono bg-red-50 text-red-800 border border-red-200"
      title={
        integrity.notes ??
        `Chain divergence at row ${integrity.broken_at_position} (id ${integrity.broken_at_row_id ?? "?"}).`
      }
      data-testid="audit-integrity"
    >
      <ShieldAlert className="w-3 h-3" />
      chain BROKEN at row {integrity.broken_at_position ?? "?"}
    </span>
  );
}


function Stat({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string | number;
  sub: string;
  tone?: "warn";
}) {
  return (
    <div className="rounded-lg border border-zinc-200 p-3 bg-white">
      <div className="text-[10px] uppercase tracking-wider text-zinc-400">
        {label}
      </div>
      <div
        className={`text-xl font-semibold mt-1 ${
          tone === "warn" ? "text-amber-700" : "text-zinc-900"
        }`}
      >
        {value}
      </div>
      <div className="text-[11px] text-zinc-500 mono mt-0.5">{sub}</div>
    </div>
  );
}


function AuditRow({
  row,
  expanded,
  onToggle,
}: {
  row: AuditEntry;
  expanded: boolean;
  onToggle: () => void;
}) {
  const refused = row.refused;
  return (
    <div
      className="border-b border-zinc-100 last:border-0"
      data-testid="audit-row"
      data-refused={refused}
    >
      <button
        type="button"
        onClick={onToggle}
        className="w-full text-left grid grid-cols-[140px_1fr_80px_80px_80px_24px] gap-3 px-4 py-2.5 items-center hover:bg-zinc-50/60"
        aria-expanded={expanded}
      >
        <div className="text-[11px] text-zinc-500 mono truncate" title={row.created_at}>
          {formatTimestamp(row.created_at)}
        </div>
        <div className="text-sm text-zinc-900 truncate flex items-center gap-2">
          {refused && (
            <span className="text-[10px] mono px-1.5 py-0.5 rounded bg-amber-100 text-amber-900 border border-amber-200 flex-shrink-0">
              refused
            </span>
          )}
          <span className="truncate" title={row.query}>{row.query}</span>
        </div>
        <div className="text-[11px] text-zinc-600 mono text-right">{row.mode}</div>
        <div className="text-[11px] text-zinc-600 mono text-right">
          {row.crag_score != null ? row.crag_score.toFixed(2) : "—"}
        </div>
        <div className="text-[11px] text-zinc-500 mono text-right">
          {row.latency_ms != null ? `${row.latency_ms}ms` : "—"}
        </div>
        <ChevronRight
          className={`w-3.5 h-3.5 text-zinc-400 transition-transform ${expanded ? "rotate-90" : ""}`}
        />
      </button>

      {expanded && (
        <div className="px-4 pb-3 pt-1 bg-zinc-50/40 border-t border-zinc-100 space-y-2">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-2 text-[11px]">
            <KV k="Endpoint" v={row.endpoint} />
            <KV k="Mode" v={row.mode} />
            <KV
              k="Status"
              v={refused ? `refused · ${row.refusal_reason ?? "—"}` : "grounded"}
            />
            <KV k="Model" v={row.model_id ?? "—"} />
            <KV k="ID" v={row.id} />
            <KV k="When" v={row.created_at} />
          </div>
          {row.answer && (
            <div>
              <div className="text-[10px] uppercase tracking-wider text-zinc-400 mb-1">
                Answer (truncated to 500 chars)
              </div>
              <div className="text-xs text-zinc-700 bg-white border border-zinc-200 rounded p-2 leading-relaxed whitespace-pre-wrap">
                {row.answer}
              </div>
            </div>
          )}
          <div className="flex items-center gap-3 pt-1 text-[11px]">
            <a
              href={`/chat?q=${encodeURIComponent(row.query)}`}
              className="inline-flex items-center gap-1 text-zinc-700 hover:text-zinc-900"
              data-testid="audit-replay"
            >
              <ExternalLink className="w-3 h-3" /> Replay in /chat
            </a>
          </div>
        </div>
      )}
    </div>
  );
}


function KV({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex gap-2 min-w-0">
      <span className="text-zinc-400 w-20 flex-shrink-0">{k}</span>
      <span className="text-zinc-700 mono truncate" title={v}>{v}</span>
    </div>
  );
}


function rollup(items: AuditEntry[]) {
  const refused = items.filter((i) => i.refused).length;
  const refusedPct = items.length > 0 ? refused / items.length : 0;
  const latencies = items
    .map((i) => i.latency_ms)
    .filter((n): n is number => typeof n === "number")
    .sort((a, b) => a - b);
  const avgLatencyMs = latencies.length > 0
    ? Math.round(latencies.reduce((a, b) => a + b, 0) / latencies.length)
    : null;
  const p95LatencyMs = latencies.length >= 20
    ? latencies[Math.floor(latencies.length * 0.95)]
    : null;
  const crags = items
    .map((i) => i.crag_score)
    .filter((n): n is number => typeof n === "number");
  const avgCragScore = crags.length > 0
    ? crags.reduce((a, b) => a + b, 0) / crags.length
    : null;
  return { refused, refusedPct, avgLatencyMs, p95LatencyMs, avgCragScore };
}


function formatTimestamp(iso: string): string {
  try {
    const d = new Date(iso);
    const now = new Date();
    const sameDay = d.toDateString() === now.toDateString();
    if (sameDay) {
      return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    }
    return d.toLocaleString([], {
      month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  } catch {
    return iso;
  }
}
