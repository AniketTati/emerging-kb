"use client";

/**
 * /schema-studio — Knowledge Map.
 *
 * Redesigned from the original 6-tab Schema Studio to be
 * layman-readable. The route stays `/schema-studio` for inbound link
 * compatibility but the page title is "Knowledge Map" and the IA is:
 *
 *   📚 Catalog        — every doc-type the system has learned, grouped
 *                      by domain (Legal / Finance / HR / …) with
 *                      inline-expand to fields + sub-entity column shapes.
 *   🔍 Needs Review   — anomalies (rarity > 0.8), unresolved
 *                      fact_conflicts, and empty-states for emerging
 *                      fields + synonym proposals.
 *   🕓 History        — workspace-wide file_lifecycle timeline,
 *                      grouped by day with filter chips.
 *
 * Data flow: one API call per tab against /knowledge-map/{stats,
 * catalog, needs-review, history}. No N+1 from the browser.
 */

import { Suspense, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams, usePathname } from "next/navigation";
import {
  Download, Library, AlertTriangle, Clock, ChevronRight, ChevronDown,
  Loader2, FileText, Sparkles, FileQuestion, MessageSquare, ExternalLink,
} from "lucide-react";

import { Sidebar } from "@/components/Sidebar";
import {
  getKnowledgeMapStats, getKnowledgeMapCatalog,
  getKnowledgeMapNeedsReview, getKnowledgeMapHistory,
  downloadSchemaExportYaml,
  type KMStats, type KMSchemaCard, type KMNeedsReview, type KMHistoryResp,
  type KMSubEntityType, type KMHistoryEvent,
} from "@/lib/api";
import {
  humanizeSchemaName, categorizeSchema, DOMAINS, VISIBLE_DOMAINS,
  relativeTime, type SchemaDomain,
} from "@/lib/schema-helpers";


type TabKey = "catalog" | "review" | "history";
const TAB_KEYS: TabKey[] = ["catalog", "review", "history"];

function parseTab(v: string | null): TabKey {
  return (TAB_KEYS as readonly string[]).includes(v ?? "")
    ? (v as TabKey)
    : "catalog";
}


export default function SchemaStudioPage() {
  return (
    <Suspense fallback={<PageSkeleton />}>
      <KnowledgeMapShell />
    </Suspense>
  );
}


function PageSkeleton() {
  return (
    <div className="flex h-full">
      <Sidebar current="schema" />
      <main className="flex-1 flex items-center justify-center">
        <Loader2 className="w-5 h-5 animate-spin text-zinc-400" />
      </main>
    </div>
  );
}


function KnowledgeMapShell() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const tab = parseTab(searchParams.get("tab"));

  function setTab(next: TabKey) {
    const sp = new URLSearchParams(searchParams.toString());
    if (next === "catalog") sp.delete("tab"); else sp.set("tab", next);
    const qs = sp.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  }

  const [stats, setStats] = useState<KMStats | null>(null);
  const [statsErr, setStatsErr] = useState<string | null>(null);
  useEffect(() => {
    getKnowledgeMapStats()
      .then(setStats)
      .catch((e) => setStatsErr(String(e)));
  }, []);

  const TABS: Array<{ key: TabKey; label: string; icon: typeof Library; count: number | null }> = [
    { key: "catalog", label: "Catalog",      icon: Library,         count: stats?.doc_types ?? null },
    { key: "review",  label: "Needs Review", icon: AlertTriangle,   count: stats?.pending_review ?? null },
    { key: "history", label: "History",      icon: Clock,           count: null },
  ];

  return (
    <div className="flex h-full">
      <Sidebar current="schema" />
      <main className="flex-1 flex flex-col min-w-0 bg-white">
        {/* Top breadcrumb + Export */}
        <header className="h-12 flex-shrink-0 border-b border-zinc-200 flex items-center px-5 gap-3">
          <span className="text-sm text-zinc-500">Studio</span>
          <ChevronRight className="w-3 h-3 text-zinc-300" />
          <span className="text-sm font-medium text-zinc-900">Knowledge Map</span>
          <button
            type="button"
            onClick={() => {
              downloadSchemaExportYaml().catch((err) => {
                console.error("export.yaml failed", err);
                alert("Failed to export YAML — see console.");
              });
            }}
            className="ml-auto flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-md text-zinc-600 hover:text-zinc-900 hover:bg-zinc-100 cursor-pointer"
            title="Download all active schemas as YAML"
            data-testid="schema-export-yaml"
          >
            <Download className="w-3.5 h-3.5" strokeWidth={1.75} />
            Export YAML
          </button>
        </header>

        <div className="flex-1 overflow-y-auto bg-zinc-50/40">
          {/* Page title + subtitle + intro */}
          <div className="max-w-6xl mx-auto px-8 pt-8">
            <h1 className="text-2xl font-semibold text-zinc-900">Knowledge Map</h1>
            <p className="mt-1 text-sm text-zinc-600">
              What the system has learned from your documents.
            </p>

            <div className="mt-4 rounded-lg border border-zinc-200 bg-white px-4 py-3 text-[13px] text-zinc-600 leading-relaxed">
              Every uploaded document gets classified into a type, and the
              system pulls structured fields out automatically. This page
              shows what it found. <span className="text-zinc-900 font-medium">You don't need
              to design anything</span> — come here only to inspect what was
              learned or fix something the system isn't sure about.
            </div>

            {/* Stat cards */}
            <StatStrip stats={stats} err={statsErr} />

            {/* Tab strip */}
            <div className="mt-6 border-b border-zinc-200 flex items-end gap-6 text-sm">
              {TABS.map((t) => {
                const Icon = t.icon;
                const active = t.key === tab;
                return (
                  <button
                    key={t.key}
                    type="button"
                    onClick={() => setTab(t.key)}
                    className={`py-2.5 flex items-center gap-2 cursor-pointer border-b-2 -mb-px transition-colors ${
                      active
                        ? "text-zinc-900 font-medium border-zinc-900"
                        : "text-zinc-500 hover:text-zinc-900 border-transparent"
                    }`}
                    data-testid={`km-tab-${t.key}`}
                  >
                    <Icon className="w-4 h-4" strokeWidth={1.75} />
                    {t.label}
                    {t.count !== null && (
                      <span className={`text-[11px] mono ${
                        t.key === "review" && t.count > 0
                          ? "text-amber-700 bg-amber-50 px-1.5 py-0.5 rounded"
                          : "text-zinc-400"
                      }`}>
                        {t.count}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="max-w-6xl mx-auto px-8 py-6">
            {tab === "catalog" && <CatalogTab />}
            {tab === "review"  && <NeedsReviewTab />}
            {tab === "history" && <HistoryTab />}
          </div>
        </div>
      </main>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Shared
// ---------------------------------------------------------------------------


function StatStrip({ stats, err }: { stats: KMStats | null; err: string | null }) {
  if (err) {
    return (
      <div className="mt-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-[13px] text-red-700">
        Failed to load stats: <span className="mono">{err}</span>
      </div>
    );
  }
  const cards: Array<{ label: string; value: number | null; hint: string }> = [
    { label: "doc types",         value: stats?.doc_types ?? null,
      hint: "Distinct document types the system recognizes." },
    { label: "files ingested",    value: stats?.files_ingested ?? null,
      hint: "Files that reached `ready` lifecycle." },
    { label: "sub-entities",      value: stats?.sub_entities ?? null,
      hint: "Structured records extracted (transactions, line items, …)." },
    { label: "pending review",    value: stats?.pending_review ?? null,
      hint: "Anomalies, conflicts, or emerging fields awaiting decision." },
  ];
  return (
    <div className="mt-4 grid grid-cols-2 md:grid-cols-4 gap-3">
      {cards.map((c) => {
        const isPending = c.label === "pending review";
        const tone = isPending && (c.value ?? 0) > 0
          ? "bg-amber-50 border-amber-200"
          : "bg-white border-zinc-200";
        return (
          <div
            key={c.label}
            className={`rounded-lg border ${tone} px-4 py-3`}
            title={c.hint}
          >
            <div className="text-2xl font-semibold text-zinc-900 mono">
              {c.value !== null ? c.value.toLocaleString() : "—"}
            </div>
            <div className="text-[11px] text-zinc-500 mt-0.5">{c.label}</div>
          </div>
        );
      })}
    </div>
  );
}


function ErrorBanner({ msg }: { msg: string }) {
  return (
    <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
      Failed to load: <span className="mono">{msg}</span>
    </div>
  );
}

function EmptyState({ title, body }: { title: string; body: string }) {
  return (
    <div className="rounded-lg border border-dashed border-zinc-200 bg-white px-6 py-10 text-center">
      <div className="text-sm font-medium text-zinc-700">{title}</div>
      <div className="text-[13px] text-zinc-500 mt-1 max-w-md mx-auto">{body}</div>
    </div>
  );
}

function SpinnerInline() {
  return (
    <div className="flex items-center gap-2 text-sm text-zinc-500 py-4">
      <Loader2 className="w-4 h-4 animate-spin" />
      Loading…
    </div>
  );
}


// ---------------------------------------------------------------------------
// Tab: 📚 Catalog
// ---------------------------------------------------------------------------


function CatalogTab() {
  const [cards, setCards] = useState<KMSchemaCard[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [openIds, setOpenIds] = useState<Set<string>>(new Set());
  const [showDev, setShowDev] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getKnowledgeMapCatalog()
      .then((r) => !cancelled && setCards(r))
      .catch((e) => !cancelled && setErr(String(e)));
    return () => { cancelled = true; };
  }, []);

  // Group by domain.
  const grouped = useMemo(() => {
    if (!cards) return null;
    const g: Record<SchemaDomain, KMSchemaCard[]> = {
      legal: [], finance: [], hr: [], medical: [],
      engineering: [], communications: [], reports: [], dev: [],
    };
    for (const c of cards) {
      g[categorizeSchema(c.name)].push(c);
    }
    // Sort within each group by humanized name.
    for (const k of Object.keys(g) as SchemaDomain[]) {
      g[k].sort((a, b) => humanizeSchemaName(a.name).localeCompare(humanizeSchemaName(b.name)));
    }
    return g;
  }, [cards]);

  function toggle(id: string) {
    setOpenIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  if (err) return <ErrorBanner msg={err} />;
  if (cards === null || grouped === null) return <SpinnerInline />;
  if (cards.length === 0) {
    return (
      <EmptyState
        title="No schemas yet"
        body="Upload some documents and the system will start learning their structure here."
      />
    );
  }

  return (
    <div className="space-y-6">
      {VISIBLE_DOMAINS.map((dom) => {
        const items = grouped[dom];
        if (items.length === 0) return null;
        const meta = DOMAINS[dom];
        return (
          <section key={dom} data-testid={`km-domain-${dom}`}>
            <div className="mb-2 flex items-baseline gap-2">
              <span className="text-base">{meta.emoji}</span>
              <h2 className="text-sm font-semibold text-zinc-900">{meta.label}</h2>
              <span className="text-[11px] mono text-zinc-400">{items.length}</span>
              <span className="text-[11px] text-zinc-400 ml-1">· {meta.description}</span>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {items.map((c) => (
                <CatalogCard
                  key={c.id}
                  card={c}
                  open={openIds.has(c.id)}
                  onToggle={() => toggle(c.id)}
                />
              ))}
            </div>
          </section>
        );
      })}

      {/* Dev artifacts — collapsed footer */}
      {grouped.dev.length > 0 && (
        <section className="pt-4 border-t border-zinc-200">
          <button
            type="button"
            onClick={() => setShowDev((v) => !v)}
            className="text-[12px] text-zinc-500 hover:text-zinc-900 flex items-center gap-1.5 cursor-pointer"
            data-testid="km-toggle-dev"
          >
            {showDev ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
            {DOMAINS.dev.emoji} {DOMAINS.dev.label}
            <span className="mono">{grouped.dev.length}</span>
            <span className="text-zinc-400">· hidden by default</span>
          </button>
          {showDev && (
            <div className="mt-3 grid grid-cols-1 md:grid-cols-2 gap-3">
              {grouped.dev.map((c) => (
                <CatalogCard
                  key={c.id}
                  card={c}
                  open={openIds.has(c.id)}
                  onToggle={() => toggle(c.id)}
                />
              ))}
            </div>
          )}
        </section>
      )}
    </div>
  );
}


function CatalogCard({
  card, open, onToggle,
}: {
  card: KMSchemaCard;
  open: boolean;
  onToggle: () => void;
}) {
  const router = useRouter();
  const title = humanizeSchemaName(card.name);
  const hasSubs = card.sub_entity_types.length > 0;
  const totalSubRows = card.sub_entity_types.reduce((acc, s) => acc + s.row_count, 0);

  return (
    <div
      className={`rounded-lg border bg-white transition-colors ${
        open ? "border-zinc-300 shadow-sm" : "border-zinc-200 hover:border-zinc-300"
      }`}
      data-testid="km-catalog-card"
    >
      <button
        type="button"
        onClick={onToggle}
        className="w-full px-4 py-3 text-left cursor-pointer flex items-start gap-2"
        aria-expanded={open}
      >
        {open ? (
          <ChevronDown className="w-3.5 h-3.5 text-zinc-500 mt-1 flex-shrink-0" strokeWidth={2} />
        ) : (
          <ChevronRight className="w-3.5 h-3.5 text-zinc-500 mt-1 flex-shrink-0" strokeWidth={2} />
        )}
        <div className="flex-1 min-w-0">
          <div className="text-[14px] font-medium text-zinc-900 truncate">{title}</div>
          <div className="text-[11px] text-zinc-500 mt-0.5 flex items-center gap-2 flex-wrap">
            <span>{card.file_count} file{card.file_count === 1 ? "" : "s"}</span>
            <span className="text-zinc-300">·</span>
            <span>{card.doc_root_fields.length} field{card.doc_root_fields.length === 1 ? "" : "s"}</span>
            {hasSubs && (
              <>
                <span className="text-zinc-300">·</span>
                <span>
                  {card.sub_entity_types.length} sub-type{card.sub_entity_types.length === 1 ? "" : "s"}
                  {totalSubRows > 0 && (
                    <span className="text-zinc-400"> ({totalSubRows} row{totalSubRows === 1 ? "" : "s"})</span>
                  )}
                </span>
              </>
            )}
            <span className="text-zinc-300">·</span>
            <span>Created {relativeTime(card.created_at)}</span>
          </div>
        </div>
      </button>

      {open && (
        <div className="px-4 pb-4 border-t border-zinc-100 pt-3 space-y-3">
          {/* Doc-root fields */}
          <div>
            <div className="text-[10px] uppercase tracking-wider text-zinc-400 mb-1.5">
              Doc-level fields ({card.doc_root_fields.length})
            </div>
            {card.doc_root_fields.length === 0 ? (
              <div className="text-[12px] text-zinc-400 italic">
                No doc-level fields detected.
              </div>
            ) : (
              <ul className="space-y-1">
                {card.doc_root_fields.map((f) => (
                  <li
                    key={f.name}
                    className="text-[12px] grid grid-cols-[1fr_auto] gap-3 items-baseline"
                    title={f.description ?? undefined}
                  >
                    <span className="mono text-zinc-700">{f.name}</span>
                    <span className="text-[11px] text-zinc-400">{f.type ?? "—"}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {/* Sub-entity types */}
          {hasSubs && (
            <div>
              <div className="text-[10px] uppercase tracking-wider text-zinc-400 mb-1.5">
                Contains
              </div>
              <div className="space-y-2">
                {card.sub_entity_types.map((s) => (
                  <SubEntityBlock key={s.unit_type} sub={s} />
                ))}
              </div>
            </div>
          )}

          {/* Actions */}
          <div className="flex flex-wrap items-center gap-2 pt-2">
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                const q = `summarize ${title.toLowerCase()}`;
                router.push(`/chat?q=${encodeURIComponent(q)}`);
              }}
              className="text-[11px] flex items-center gap-1 px-2.5 py-1 rounded-md border border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50 cursor-pointer"
              data-testid="km-card-ask"
            >
              <MessageSquare className="w-3 h-3" /> Ask a question
            </button>
            {card.file_count === 1 && card.file_ids[0] && (
              <a
                href={`/files/${card.file_ids[0]}`}
                className="text-[11px] flex items-center gap-1 px-2.5 py-1 rounded-md border border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50 cursor-pointer"
              >
                <FileText className="w-3 h-3" /> View file
              </a>
            )}
            {card.file_count > 1 && (
              <a
                href={`/upload`}
                className="text-[11px] flex items-center gap-1 px-2.5 py-1 rounded-md border border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50 cursor-pointer"
                title={`Browse all ${card.file_count} files of this type in Upload`}
              >
                <FileText className="w-3 h-3" /> View {card.file_count} files
              </a>
            )}
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                downloadSchemaExportYaml().catch(console.error);
              }}
              className="text-[11px] flex items-center gap-1 px-2.5 py-1 rounded-md border border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50 cursor-pointer"
            >
              <Download className="w-3 h-3" /> Export YAML
            </button>
          </div>
        </div>
      )}
    </div>
  );
}


function SubEntityBlock({ sub }: { sub: KMSubEntityType }) {
  return (
    <div className="rounded-md bg-zinc-50 border border-zinc-100 px-3 py-2">
      <div className="text-[12px] font-medium text-zinc-800 flex items-center gap-2">
        <span className="text-zinc-400">↳</span>
        <span>{humanizeSchemaName(sub.unit_type)}</span>
        <span className="text-[10px] text-zinc-400 mono">
          {sub.row_count} row{sub.row_count === 1 ? "" : "s"}
        </span>
      </div>
      {sub.fields.length > 0 && (
        <div className="mt-1.5 text-[11px] text-zinc-600 flex flex-wrap gap-x-3 gap-y-0.5 mono ml-4">
          {sub.fields.map((f) => (
            <span key={f.name}>{f.name}</span>
          ))}
        </div>
      )}
    </div>
  );
}


// ---------------------------------------------------------------------------
// Tab: 🔍 Needs Review
// ---------------------------------------------------------------------------


function NeedsReviewTab() {
  const [data, setData] = useState<KMNeedsReview | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getKnowledgeMapNeedsReview({ anomalyLimit: 30, conflictLimit: 30 })
      .then((r) => !cancelled && setData(r))
      .catch((e) => !cancelled && setErr(String(e)));
    return () => { cancelled = true; };
  }, []);

  if (err) return <ErrorBanner msg={err} />;
  if (data === null) return <SpinnerInline />;

  const total = data.anomalies_total + data.conflicts_total + data.emerging_fields_total + data.synonym_proposals_total;
  if (total === 0) {
    return <NeedsReviewAllClear />;
  }

  return (
    <div className="space-y-6">
      <AnomaliesSection data={data} />
      <ConflictsSection data={data} />
      <EmergingFieldsSection total={data.emerging_fields_total} />
      <SynonymProposalsSection total={data.synonym_proposals_total} />
    </div>
  );
}


function NeedsReviewAllClear() {
  return (
    <div className="rounded-lg border border-zinc-200 bg-white px-6 py-10">
      <div className="text-center">
        <Sparkles className="w-6 h-6 text-zinc-400 mx-auto mb-2" strokeWidth={1.75} />
        <div className="text-sm font-medium text-zinc-700">
          Nothing needs your attention right now.
        </div>
      </div>
      <div className="mt-5 max-w-2xl mx-auto text-[12px] text-zinc-500 leading-relaxed">
        <div className="mb-1">When the system encounters one of these, it'll show up here:</div>
        <ul className="list-disc pl-5 space-y-0.5">
          <li><strong>Anomalies</strong> — a value that's an outlier relative to the cohort (e.g. a $196k transaction in a corpus where most transactions are under $5k).</li>
          <li><strong>Conflicts</strong> — two sources disagree on the same fact (e.g. one doc says net-30, another says net-45 for the same vendor).</li>
          <li><strong>Emerging fields</strong> — a new field appearing across multiple docs that hasn't been promoted yet.</li>
          <li><strong>Synonym proposals</strong> — two field names that look like the same concept (e.g. `monthly_uptime` and `sla_uptime`).</li>
        </ul>
      </div>
    </div>
  );
}


function AnomaliesSection({ data }: { data: KMNeedsReview }) {
  if (data.anomalies_total === 0) return null;
  return (
    <section data-testid="km-anomalies-section">
      <SectionHeader emoji="🔥" title="Anomalies"
        count={data.anomalies_total} shown={data.anomalies.length}
        blurb="Sub-entities the system flagged as outliers (rarity ≥ 0.8) — worth a human glance to confirm they're real."
      />
      <div className="space-y-2">
        {data.anomalies.map((a) => (
          <div key={a.id} className="rounded-md border border-zinc-200 bg-white px-3 py-2.5"
               data-testid="km-anomaly-row">
            <div className="flex items-center gap-2 text-[11px]">
              <span className="px-1.5 py-0.5 rounded bg-rose-50 text-rose-700 mono">
                rarity {a.rarity_score.toFixed(2)}
              </span>
              <span className="mono text-zinc-500">{a.unit_type}</span>
              <span className="text-zinc-300">·</span>
              <span className="text-zinc-600 truncate">{a.file_name ?? "(unknown file)"}</span>
            </div>
            <div className="mt-1.5 text-[12px] text-zinc-700 mono break-all">
              {summarizeFields(a.fields)}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}


function ConflictsSection({ data }: { data: KMNeedsReview }) {
  if (data.conflicts_total === 0) return null;
  return (
    <section data-testid="km-conflicts-section">
      <SectionHeader emoji="⚠️" title="Conflicts"
        count={data.conflicts_total} shown={data.conflicts.length}
        blurb="Two or more sources disagree on the same predicate. The orchestrator's chain/authority/recency rules will pick one at query time — but a human decision is more reliable."
      />
      <div className="space-y-2">
        {data.conflicts.map((c) => (
          <div key={c.id} className="rounded-md border border-zinc-200 bg-white px-3 py-2.5"
               data-testid="km-conflict-row">
            <div className="flex items-center gap-2 text-[11px]">
              <span className="px-1.5 py-0.5 rounded bg-amber-50 text-amber-800 mono">
                {c.evidence_count} sources
              </span>
              <span className="mono text-zinc-700">{c.predicate}</span>
              <span className="text-zinc-300">·</span>
              <span className="text-zinc-500">{relativeTime(c.observed_at)}</span>
            </div>
            <div className="mt-1.5 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-1.5">
              {c.evidence_preview.map((e, i) => (
                <div key={i} className="text-[11px] mono bg-zinc-50 border border-zinc-100 rounded px-2 py-1 truncate">
                  <span className="text-zinc-900">{String((e as Record<string, unknown>).value ?? "—")}</span>
                  <span className="text-zinc-400 ml-1">
                    via {String((e as Record<string, unknown>).hit_id ?? "?").slice(0, 8)}
                  </span>
                </div>
              ))}
            </div>
            {c.notes && (
              <div className="mt-1.5 text-[11px] text-zinc-500 italic">{c.notes}</div>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}


function EmergingFieldsSection({ total }: { total: number }) {
  return (
    <section>
      <SectionHeader emoji="🌿" title="Emerging fields"
        count={total} shown={0}
        blurb="New fields that haven't yet crossed the auto-promotion threshold (prevalence ≥ 80% · stability ≥ 90% · value-type confidence ≥ 90% · min 5 docs)."
      />
      {total === 0 && (
        <div className="rounded-md border border-zinc-100 bg-white px-3 py-3 text-[12px] text-zinc-500">
          ✓ All fields the system has discovered are stable and already in production.
        </div>
      )}
    </section>
  );
}


function SynonymProposalsSection({ total }: { total: number }) {
  return (
    <section>
      <SectionHeader emoji="📖" title="Synonym proposals"
        count={total} shown={0}
        blurb="Two field names that look semantically similar (e.g. `monthly_uptime` ≈ `sla_uptime`) — merge to one canonical name for better query expansion."
      />
      {total === 0 && (
        <div className="rounded-md border border-zinc-100 bg-white px-3 py-3 text-[12px] text-zinc-500">
          No synonym proposals pending right now.
        </div>
      )}
    </section>
  );
}


function SectionHeader({
  emoji, title, count, shown, blurb,
}: {
  emoji: string;
  title: string;
  count: number;
  shown: number;
  blurb: string;
}) {
  return (
    <div className="mb-2">
      <div className="flex items-baseline gap-2">
        <span className="text-base">{emoji}</span>
        <h2 className="text-sm font-semibold text-zinc-900">{title}</h2>
        <span className="text-[11px] mono text-zinc-400">
          {count > 0 && shown < count
            ? `${shown} of ${count}`
            : count}
        </span>
      </div>
      <p className="text-[11px] text-zinc-500 mt-0.5 ml-7 max-w-3xl">{blurb}</p>
    </div>
  );
}


function summarizeFields(fields: Record<string, unknown>): string {
  // Show the most informative subset — prefer fields that look
  // numeric or date-ish since those tend to drive anomaly scores.
  const entries = Object.entries(fields).slice(0, 5);
  return entries
    .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(" · ");
}


// ---------------------------------------------------------------------------
// Tab: 🕓 History
// ---------------------------------------------------------------------------


type HistoryFilter = "all" | "schema" | "extraction" | "identity" | "errors";

const FILTER_PREFIX: Record<HistoryFilter, string | undefined> = {
  all: undefined,
  schema: "schema_",
  extraction: undefined,
  identity: "identit",
  errors: undefined,
};

const FILTER_MATCH: Record<HistoryFilter, (event: string) => boolean> = {
  all: () => true,
  schema: (e) => e.startsWith("schema_") || e === "doc_chain_detected",
  extraction: (e) => /^(parse|chunk|contextualization|embedding|raptor|mentions|fields|atomic|kv_tables)/.test(e),
  identity: (e) => e.startsWith("identit"),
  errors: (e) => e.includes("failed") || e.includes("error"),
};

function HistoryTab() {
  const [filter, setFilter] = useState<HistoryFilter>("all");
  const [resp, setResp] = useState<KMHistoryResp | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setResp(null);
    getKnowledgeMapHistory({ limit: 200 })
      .then((r) => !cancelled && setResp(r))
      .catch((e) => !cancelled && setErr(String(e)));
    return () => { cancelled = true; };
  }, []);

  if (err) return <ErrorBanner msg={err} />;
  if (resp === null) return <SpinnerInline />;
  if (resp.items.length === 0) {
    return (
      <EmptyState
        title="No history yet"
        body="Once you upload documents, every step the pipeline takes is recorded here."
      />
    );
  }

  // Client-side filter (we asked for everything since the volume is
  // small enough for the demo). At scale we'd push the filter
  // server-side via event_filter.
  const matchFn = FILTER_MATCH[filter];
  const filtered = resp.items.filter((e) => matchFn(e.event));

  // Group by date for readability.
  const groups: Array<{ date: string; events: KMHistoryEvent[] }> = [];
  for (const e of filtered) {
    const date = e.created_at.slice(0, 10);
    const last = groups[groups.length - 1];
    if (last && last.date === date) last.events.push(e);
    else groups.push({ date, events: [e] });
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 text-[12px]">
        <span className="text-zinc-500">Filter:</span>
        {(["all","schema","extraction","identity","errors"] as HistoryFilter[]).map((f) => (
          <button
            key={f}
            type="button"
            onClick={() => setFilter(f)}
            className={`px-2.5 py-1 rounded-full border cursor-pointer ${
              filter === f
                ? "bg-zinc-900 text-white border-zinc-900"
                : "bg-white text-zinc-700 border-zinc-200 hover:border-zinc-300"
            }`}
            data-testid={`km-history-filter-${f}`}
          >
            {f}
          </button>
        ))}
        <span className="ml-auto text-zinc-400 mono text-[11px]">
          {filtered.length} of {resp.items.length} events shown · {resp.total} total in workspace
        </span>
      </div>

      {groups.length === 0 ? (
        <div className="text-[12px] text-zinc-500 italic">
          No events match this filter.
        </div>
      ) : (
        <div className="space-y-5">
          {groups.map((g) => (
            <div key={g.date}>
              <div className="text-[11px] uppercase tracking-wider text-zinc-400 mb-2">
                {formatDayHeader(g.date)} · {g.events.length} event{g.events.length === 1 ? "" : "s"}
              </div>
              <ul className="space-y-1">
                {g.events.map((e) => (
                  <li
                    key={e.id}
                    className="text-[12px] grid grid-cols-[80px_180px_1fr] gap-3 items-start py-1.5 border-b border-zinc-100 last:border-b-0"
                  >
                    <span className="mono text-zinc-400">{e.created_at.slice(11, 19)}</span>
                    <span className="mono text-zinc-700">{e.event}</span>
                    <span className="text-zinc-700 truncate" title={e.file_name ?? ""}>
                      {e.file_name ?? <span className="text-zinc-400 italic">(no file)</span>}
                      {e.to_state && (
                        <span className="ml-2 text-[10px] text-zinc-400">→ {e.to_state}</span>
                      )}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}


function formatDayHeader(yyyymmdd: string): string {
  // Compare to today/yesterday for a friendly label.
  const today = new Date();
  const todayStr = today.toISOString().slice(0, 10);
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  const yesterdayStr = yesterday.toISOString().slice(0, 10);
  if (yyyymmdd === todayStr) return "Today";
  if (yyyymmdd === yesterdayStr) return "Yesterday";
  return new Date(yyyymmdd).toLocaleDateString(undefined, {
    weekday: "long", year: "numeric", month: "short", day: "numeric",
  });
}
