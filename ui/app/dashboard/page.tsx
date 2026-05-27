"use client";

import { useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  FileText,
  GitMerge,
  MessageSquareWarning,
  ScrollText,
  Search,
  ShieldCheck,
  Sparkles,
} from "lucide-react";

import { Sidebar } from "@/components/Sidebar";
import {
  type DashboardSummary,
  type NeedsAttentionItem,
  type NeedsAttentionKind,
  type CountByLabel,
  getDashboardSummary,
  getNeedsAttention,
} from "@/lib/api";


export default function DashboardPage() {
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [attention, setAttention] = useState<NeedsAttentionItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getDashboardSummary(), getNeedsAttention(50)])
      .then(([s, a]) => {
        if (cancelled) return;
        setSummary(s);
        setAttention(a);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="flex h-full">
      <Sidebar current="dashboard" />
      <main className="flex-1 flex flex-col min-w-0 bg-zinc-50/40">
        <DashboardTopBar summary={summary} />
        <div className="flex-1 overflow-y-auto">
          <div className="max-w-6xl mx-auto px-8 py-6">
            {loading && <LoadingBlock />}
            {error !== null && <ErrorBlock error={error} />}
            {summary !== null && !loading && error === null && (
              <DashboardBody summary={summary} attention={attention} />
            )}
          </div>
        </div>
      </main>
    </div>
  );
}


function DashboardTopBar({ summary }: { summary: DashboardSummary | null }) {
  return (
    <header className="h-12 flex-shrink-0 border-b border-zinc-200 bg-white flex items-center px-5 gap-4">
      <div className="flex items-center gap-2 text-sm">
        <span className="text-zinc-900">Dashboard</span>
        <span className="ml-1 text-[11px] text-zinc-400 mono">
          workspace summary · refreshed on load
        </span>
      </div>
      {summary && (
        <div className="ml-auto flex items-center gap-3 text-[11px] text-zinc-500 mono">
          <span>{summary.files_total} files</span>
          <span className="text-zinc-300">·</span>
          <span>{summary.queries_total} queries</span>
          <span className="text-zinc-300">·</span>
          <span>{summary.audit_log_total_rows} audit rows</span>
        </div>
      )}
    </header>
  );
}


function LoadingBlock() {
  return (
    <div className="text-center py-20 text-sm text-zinc-500" data-testid="dash-loading">
      Loading dashboard…
    </div>
  );
}


function ErrorBlock({ error }: { error: string }) {
  return (
    <div
      className="rounded-lg border border-red-200 bg-red-50/40 p-4 text-sm text-red-900"
      data-testid="dash-error"
    >
      <div className="font-medium mb-1">Couldn&apos;t load dashboard</div>
      <div className="mono text-[11px] text-red-700">{error}</div>
    </div>
  );
}


function DashboardBody({
  summary,
  attention,
}: {
  summary: DashboardSummary;
  attention: NeedsAttentionItem[];
}) {
  return (
    <div data-testid="dash-body">
      {/* Stat cards row */}
      <div className="grid grid-cols-4 gap-3 mb-6">
        <StatCard
          label="Files"
          value={summary.files_total}
          tone="default"
          detail={
            summary.files_by_doc_type.length === 0
              ? "no documents yet"
              : `${summary.files_by_doc_type.length} doc types`
          }
          testId="stat-files"
        />
        <StatCard
          label="Queries"
          value={summary.queries_total}
          tone="default"
          detail={`${summary.queries_last_24h} in last 24h`}
          testId="stat-queries"
        />
        <StatCard
          label="Open conflicts"
          value={summary.conflicts_open}
          tone={summary.conflicts_open > 0 ? "warn" : "default"}
          detail={`${summary.conflicts_resolved} resolved`}
          testId="stat-conflicts"
        />
        <StatCard
          label="Corrections"
          value={summary.corrections_open + summary.corrections_fixing}
          tone={
            summary.corrections_open + summary.corrections_fixing > 0
              ? "warn"
              : "default"
          }
          detail={`${summary.corrections_fixing} in flight`}
          testId="stat-corrections"
        />
      </div>

      {/* Group-by breakdown row */}
      <div className="grid grid-cols-2 gap-3 mb-6">
        <BreakdownCard
          title="Files by doc type"
          rows={summary.files_by_doc_type}
          emptyHint="no classified documents"
          link={{ href: "/schema-studio", label: "Schema Studio" }}
          testId="breakdown-doctype"
        />
        <BreakdownCard
          title="Files by status"
          rows={summary.files_by_doc_status}
          emptyHint="—"
          link={{ href: "/upload", label: "Upload" }}
          testId="breakdown-status"
        />
      </div>

      <div className="grid grid-cols-2 gap-3 mb-6">
        <BreakdownCard
          title="Queries by mode"
          rows={summary.queries_by_mode}
          emptyHint="no queries yet"
          link={{ href: "/chat", label: "Chat" }}
          testId="breakdown-mode"
        />
        <BreakdownCard
          title="Queries by faithfulness verdict"
          rows={summary.queries_by_faithfulness}
          emptyHint="no queries yet"
          link={{ href: "/audit", label: "Audit" }}
          testId="breakdown-verdict"
        />
      </div>

      {/* Needs attention — grouped + capped for scale.
          Without dedup: a workspace with 14 identical 'service_location.in_scope'
          within-doc conflicts would render 14 visually-identical rows on the
          DASHBOARD (a summary page). With dedup: "Conflict on
          'service_location.in_scope' (within-doc) × 14" → 1 row.
          Cap at 8 visible items; rest collapses to a "see all in Needs
          Review" link to the deeper triage UI. */}
      <AttentionPanel attention={attention} />


      {/* Footer details — small numeric panel */}
      <div className="grid grid-cols-3 gap-3 mb-8">
        <FooterStat
          label="Low-authority files"
          value={summary.files_low_authority}
          icon={ShieldCheck}
        />
        <FooterStat
          label="Sessions active (24h)"
          value={summary.sessions_active_24h}
          icon={MessageSquareWarning}
        />
        <FooterStat
          label="Regressions active"
          value={summary.regressions_active}
          icon={GitMerge}
        />
      </div>
    </div>
  );
}


function StatCard({
  label,
  value,
  detail,
  tone,
  testId,
}: {
  label: string;
  value: number;
  detail: string;
  tone: "default" | "warn";
  testId: string;
}) {
  return (
    <div
      className={`rounded-lg border p-4 ${
        tone === "warn"
          ? "border-amber-300/60 bg-amber-50/40"
          : "border-zinc-200 bg-white"
      }`}
      data-testid={testId}
    >
      <div className="text-[10px] uppercase tracking-wider text-zinc-400">
        {label}
      </div>
      <div className="flex items-baseline gap-2 mt-2">
        <div className="text-2xl font-semibold text-zinc-900">{value.toLocaleString()}</div>
      </div>
      <div className="mt-2 text-[11px] text-zinc-500 mono">{detail}</div>
    </div>
  );
}


/** Top-N cap for BreakdownCard rows. At 35 doc-types you can see all
 *  of them; at 500+ doc-types the unbounded list becomes a scroll
 *  trap on a SUMMARY page. Display top 8 by count, with a "see all
 *  → <link>" footer that hands off to the deeper view (Knowledge
 *  Map / Audit / etc.) where the user can search + filter properly. */
const BREAKDOWN_TOP_N = 8;

function BreakdownCard({
  title,
  rows,
  emptyHint,
  link,
  testId,
}: {
  title: string;
  rows: CountByLabel[];
  emptyHint: string;
  link?: { href: string; label: string };
  testId: string;
}) {
  // Sort by count DESC so the top of the list is the most relevant.
  const sorted = useMemo(
    () => [...rows].sort((a, b) => b.count - a.count),
    [rows],
  );
  const total = sorted.reduce((s, r) => s + r.count, 0);
  const visible = sorted.slice(0, BREAKDOWN_TOP_N);
  const hiddenCount = sorted.length - visible.length;
  const hiddenSum = sorted.slice(BREAKDOWN_TOP_N).reduce((s, r) => s + r.count, 0);

  return (
    <div
      className="rounded-lg border border-zinc-200 bg-white"
      data-testid={testId}
    >
      <div className="px-4 py-3 border-b border-zinc-200 flex items-center justify-between">
        <div className="text-sm font-medium text-zinc-900">{title}</div>
        {link && (
          <a
            href={link.href}
            className="text-[11px] text-zinc-500 hover:text-zinc-900 flex items-center gap-1"
          >
            {link.label}
            <ArrowRight className="w-3 h-3" strokeWidth={1.75} />
          </a>
        )}
      </div>
      {sorted.length === 0 ? (
        <div className="px-4 py-8 text-center text-xs text-zinc-400">
          {emptyHint}
        </div>
      ) : (
        <>
          <div className="divide-y divide-zinc-100">
            {visible.map((row) => {
              const pct = total > 0 ? (row.count / total) * 100 : 0;
              return (
                <div
                  key={row.label}
                  className="px-4 py-2 flex items-center gap-3"
                  data-testid={`${testId}-row`}
                >
                  <div className="text-xs text-zinc-700 mono w-44 flex-shrink-0 truncate" title={row.label}>
                    {row.label}
                  </div>
                  <div className="flex-1 h-1.5 bg-zinc-100 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-zinc-900"
                      style={{ width: `${pct.toFixed(1)}%` }}
                    />
                  </div>
                  <div className="text-xs text-zinc-900 mono w-10 text-right">
                    {row.count}
                  </div>
                </div>
              );
            })}
          </div>
          {hiddenCount > 0 && (
            <div className="px-4 py-2 border-t border-zinc-100 text-[11px] text-zinc-500 flex items-center justify-between">
              <span>
                + {hiddenCount} more{" "}
                <span className="text-zinc-400 mono">({hiddenSum} total)</span>
              </span>
              {link && (
                <a
                  href={link.href}
                  className="text-zinc-600 hover:text-zinc-900 flex items-center gap-1"
                >
                  see all
                  <ArrowRight className="w-3 h-3" strokeWidth={1.75} />
                </a>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}


function AttentionRow({ item, count = 1 }: { item: NeedsAttentionItem; count?: number }) {
  const Icon = iconFor(item.kind);
  const link = linkFor(item);
  return (
    <a
      href={link}
      className="block px-4 py-2.5 hover:bg-zinc-50"
      data-testid="attention-row"
      data-kind={item.kind}
    >
      <div className="flex items-start gap-3">
        <Icon className="w-3.5 h-3.5 text-zinc-700 mt-0.5 flex-shrink-0" strokeWidth={1.75} />
        <div className="flex-1 min-w-0 text-xs">
          <div className="text-zinc-700 truncate flex items-center gap-2">
            <span className="truncate">{item.title}</span>
            {count > 1 && (
              <span
                className="text-[10px] mono px-1.5 py-0.5 rounded bg-amber-50 text-amber-800 flex-shrink-0"
                title={`${count} items grouped`}
              >
                ×{count}
              </span>
            )}
          </div>
          <div className="mt-0.5 text-[11px] text-zinc-400 mono flex items-center gap-2">
            <span>{item.kind}</span>
            <span className="text-zinc-300">·</span>
            <span>{item.severity}</span>
            <span className="text-zinc-300">·</span>
            <span>{formatDate(item.created_at)}</span>
          </div>
        </div>
        <ArrowRight className="w-3.5 h-3.5 text-zinc-400 flex-shrink-0 mt-0.5" strokeWidth={1.75} />
      </div>
    </a>
  );
}


/** Group identical Needs Attention items so the dashboard doesn't
 *  render 14 visually-identical conflict rows. We dedup on (kind +
 *  title) since that's what the user sees; the count becomes a
 *  `×N` suffix. */
const ATTENTION_TOP_N = 8;

function AttentionPanel({ attention }: { attention: NeedsAttentionItem[] }) {
  const grouped = useMemo(() => {
    const buckets = new Map<string, { rep: NeedsAttentionItem; count: number }>();
    for (const item of attention) {
      const key = `${item.kind}::${item.title}`;
      const existing = buckets.get(key);
      if (existing) existing.count += 1;
      else buckets.set(key, { rep: item, count: 1 });
    }
    return Array.from(buckets.values())
      .sort((a, b) => b.count - a.count);
  }, [attention]);

  const visible = grouped.slice(0, ATTENTION_TOP_N);
  const hiddenGroups = grouped.length - visible.length;

  return (
    <div className="rounded-lg border border-zinc-200 bg-white mb-6">
      <div className="px-4 py-3 border-b border-zinc-200 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <AlertCircle className="w-3.5 h-3.5 text-zinc-700" strokeWidth={1.75} />
          <div className="text-sm font-medium text-zinc-900">Needs attention</div>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-[11px] mono text-zinc-500">
            {attention.length === 0
              ? "all clear"
              : `${attention.length} item${attention.length === 1 ? "" : "s"} · ${grouped.length} unique`}
          </span>
          {attention.length > 0 && (
            <a
              href="/schema-studio?tab=review"
              className="text-[11px] text-zinc-500 hover:text-zinc-900 flex items-center gap-1"
            >
              Triage <ArrowRight className="w-3 h-3" strokeWidth={1.75} />
            </a>
          )}
        </div>
      </div>
      {attention.length === 0 ? (
        <div
          className="px-4 py-8 text-center text-xs text-zinc-400"
          data-testid="attention-empty"
        >
          Nothing flagged. As conflicts, corrections, low-confidence answers,
          or low-authority files appear, they&apos;ll surface here.
        </div>
      ) : (
        <>
          <div className="divide-y divide-zinc-100">
            {visible.map((g) => (
              <AttentionRow key={`${g.rep.kind}-${g.rep.title}`} item={g.rep} count={g.count} />
            ))}
          </div>
          {hiddenGroups > 0 && (
            <div className="px-4 py-2 border-t border-zinc-100 text-[11px] text-zinc-500 flex items-center justify-between">
              <span>+ {hiddenGroups} more group{hiddenGroups === 1 ? "" : "s"} of items</span>
              <a
                href="/schema-studio?tab=review"
                className="text-zinc-600 hover:text-zinc-900 flex items-center gap-1"
              >
                see all in Needs Review
                <ArrowRight className="w-3 h-3" strokeWidth={1.75} />
              </a>
            </div>
          )}
        </>
      )}
    </div>
  );
}


function FooterStat({
  label,
  value,
  icon: Icon,
}: {
  label: string;
  value: number;
  icon: typeof CheckCircle2;
}) {
  return (
    <div className="rounded-lg border border-zinc-200 bg-white px-4 py-3 flex items-center gap-3">
      <Icon className="w-4 h-4 text-zinc-500" strokeWidth={1.75} />
      <div className="flex-1">
        <div className="text-[10px] uppercase tracking-wider text-zinc-400">
          {label}
        </div>
        <div className="text-sm font-medium text-zinc-900 mono">{value}</div>
      </div>
    </div>
  );
}


function iconFor(kind: NeedsAttentionKind) {
  switch (kind) {
    case "conflict":
      return GitMerge;
    case "correction":
      return MessageSquareWarning;
    case "low_confidence_chat":
      return Sparkles;
    case "low_authority_file":
      return FileText;
    default:
      return AlertCircle;
  }
}


function linkFor(item: NeedsAttentionItem): string {
  switch (item.kind) {
    case "conflict":
      return "/conflicts";
    case "correction":
      return "/corrections";
    case "low_confidence_chat":
      return "/audit";
    case "low_authority_file":
      return "/upload";
    default:
      return "/dashboard";
  }
}


function formatDate(iso: string): string {
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}


// Silence unused import warnings for icons we may use after wiring more pages.
void Search;
void ScrollText;
