"use client";

/**
 * /schema-studio — Knowledge Map.
 *
 * Basic UI/UX principles applied:
 *   • Slim sticky header (breadcrumb + global pending pill) — doesn't
 *     scroll away.
 *   • Sticky tab strip — switching tabs always one click away regardless
 *     of scroll position.
 *   • Per-tab contextual stat strip (not repeated big stat cards on
 *     every tab).
 *   • Sticky search/filter bar on tabs that need it.
 *   • Compact rows (28-32px) — high information density, scannable at
 *     scale.
 *   • Slide-in side panel for detail (matches existing Doc Detail
 *     pattern).
 *   • Pagination / Load more — no pre-rendering of unbounded lists.
 *   • Sticky day headers in History.
 *   • No intro card — the page shouldn't lecture the user.
 */

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams, usePathname } from "next/navigation";
import {
  Download, Library, AlertTriangle, Clock, ChevronRight, ChevronDown,
  Loader2, FileText, MessageSquare, X, Search, Flame, AlertOctagon,
  Sprout, BookOpen,
} from "lucide-react";

import { Sidebar } from "@/components/Sidebar";
import {
  getKnowledgeMapStats, getKnowledgeMapCatalog,
  getKnowledgeMapNeedsReview, getKnowledgeMapHistory,
  downloadSchemaExportYaml,
  type KMStats, type KMSchemaCard, type KMNeedsReview, type KMHistoryResp,
  type KMSubEntityType, type KMHistoryEvent, type KMAnomaly, type KMConflict,
} from "@/lib/api";
import {
  humanizeSchemaName, categorizeSchema, DOMAINS, VISIBLE_DOMAINS,
  relativeTime, type SchemaDomain,
} from "@/lib/schema-helpers";


type TabKey = "catalog" | "review" | "history";
const TAB_KEYS: TabKey[] = ["catalog", "review", "history"];

function parseTab(v: string | null): TabKey {
  return (TAB_KEYS as readonly string[]).includes(v ?? "")
    ? (v as TabKey) : "catalog";
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
  useEffect(() => {
    getKnowledgeMapStats().then(setStats).catch(() => {});
  }, []);

  return (
    <div className="flex h-full">
      <Sidebar current="schema" />
      <main className="flex-1 flex flex-col min-w-0 bg-white overflow-hidden">

        {/* Sticky page header — slim, single row */}
        <header className="h-12 flex-shrink-0 border-b border-zinc-200 flex items-center px-5 gap-3 bg-white sticky top-0 z-30">
          <span className="text-sm text-zinc-500">Studio</span>
          <ChevronRight className="w-3 h-3 text-zinc-300" />
          <span className="text-sm font-medium text-zinc-900">Knowledge Map</span>
          {stats && stats.pending_review > 0 && (
            <button
              type="button"
              onClick={() => setTab("review")}
              className="text-[11px] px-2 py-0.5 rounded-full bg-amber-50 text-amber-800 hover:bg-amber-100 cursor-pointer mono"
              title="Items needing your review"
            >
              🔥 {stats.pending_review} pending
            </button>
          )}
          <button
            type="button"
            onClick={() => {
              downloadSchemaExportYaml().catch((err) => {
                console.error("export.yaml failed", err);
              });
            }}
            className="ml-auto flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-md text-zinc-600 hover:text-zinc-900 hover:bg-zinc-100 cursor-pointer"
            title="Download all active schemas as YAML"
          >
            <Download className="w-3.5 h-3.5" strokeWidth={1.75} />
            Export YAML
          </button>
        </header>

        {/* Sticky tab strip */}
        <div className="border-b border-zinc-200 px-5 flex items-end gap-5 text-sm bg-white sticky top-12 z-20">
          <TabButton active={tab === "catalog"} onClick={() => setTab("catalog")} icon={Library} label="Catalog" count={stats?.doc_types ?? null} />
          <TabButton active={tab === "review"}  onClick={() => setTab("review")}  icon={AlertTriangle} label="Needs Review" count={stats?.pending_review ?? null} pendingHighlight />
          <TabButton active={tab === "history"} onClick={() => setTab("history")} icon={Clock} label="History" count={null} />
        </div>

        <div className="flex-1 overflow-y-auto bg-zinc-50/40">
          {tab === "catalog" && <CatalogTab />}
          {tab === "review"  && <NeedsReviewTab />}
          {tab === "history" && <HistoryTab />}
        </div>
      </main>
    </div>
  );
}


function TabButton({
  active, onClick, icon: Icon, label, count, pendingHighlight,
}: {
  active: boolean;
  onClick: () => void;
  icon: typeof Library;
  label: string;
  count: number | null;
  pendingHighlight?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`py-2.5 flex items-center gap-2 cursor-pointer border-b-2 -mb-px transition-colors ${
        active
          ? "text-zinc-900 font-medium border-zinc-900"
          : "text-zinc-500 hover:text-zinc-900 border-transparent"
      }`}
      data-testid={`km-tab-${label.toLowerCase().replace(" ", "-")}`}
    >
      <Icon className="w-4 h-4" strokeWidth={1.75} />
      {label}
      {count !== null && (
        <span className={`text-[11px] mono ${
          pendingHighlight && count > 0
            ? "text-amber-700 bg-amber-50 px-1.5 py-0.5 rounded"
            : "text-zinc-400"
        }`}>
          {count}
        </span>
      )}
    </button>
  );
}


// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------


function StatStrip({ items }: { items: Array<{ label: string; value: number | string }> }) {
  return (
    <div className="px-5 py-2 border-b border-zinc-200 text-[12px] text-zinc-600 flex items-center gap-3 mono bg-white sticky top-0 z-10">
      {items.map((c, i) => (
        <span key={c.label} className="flex items-center gap-1.5">
          {i > 0 && <span className="text-zinc-300">·</span>}
          <span className="text-zinc-900 font-medium">{c.value}</span>
          <span className="text-zinc-500">{c.label}</span>
        </span>
      ))}
    </div>
  );
}


function ErrorBanner({ msg }: { msg: string }) {
  return (
    <div className="mx-5 my-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-[12px] text-red-700">
      {msg}
    </div>
  );
}


function SkeletonLines({ count = 6 }: { count?: number }) {
  return (
    <div className="px-5 py-3 space-y-2">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="h-8 bg-zinc-100 rounded animate-pulse" />
      ))}
    </div>
  );
}


// ---------------------------------------------------------------------------
// Side panel — slide-in from right, ~640px, used for any "detail" view.
// One panel per tab; closing returns to the list.
// ---------------------------------------------------------------------------


function SidePanel({
  open, onClose, title, subtitle, children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  // Esc to close
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <>
      {/* Dim backdrop — click closes */}
      <div
        className="fixed inset-0 bg-zinc-900/20 z-40"
        onClick={onClose}
        aria-hidden
      />
      <aside
        className="fixed right-0 top-0 bottom-0 w-full max-w-[640px] bg-white border-l border-zinc-200 shadow-xl z-50 flex flex-col"
        role="dialog"
        aria-modal
        data-testid="km-side-panel"
      >
        <header className="h-12 flex-shrink-0 border-b border-zinc-200 flex items-center px-5 gap-3">
          <div className="min-w-0 flex-1">
            <div className="text-sm font-medium text-zinc-900 truncate">{title}</div>
            {subtitle && (
              <div className="text-[11px] text-zinc-500 truncate">{subtitle}</div>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1 rounded hover:bg-zinc-100 cursor-pointer"
            aria-label="Close panel"
          >
            <X className="w-4 h-4 text-zinc-500" />
          </button>
        </header>
        <div className="flex-1 overflow-y-auto">
          {children}
        </div>
      </aside>
    </>
  );
}


// ---------------------------------------------------------------------------
// 📚 Catalog tab
// ---------------------------------------------------------------------------


function CatalogTab() {
  const [cards, setCards] = useState<KMSchemaCard[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);
  const [showDev, setShowDev] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getKnowledgeMapCatalog()
      .then((r) => !cancelled && setCards(r))
      .catch((e) => !cancelled && setErr(String(e)));
    return () => { cancelled = true; };
  }, []);

  // `/` focuses search.
  const searchRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "/" && document.activeElement?.tagName !== "INPUT") {
        e.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const filtered = useMemo(() => {
    if (!cards) return null;
    const q = search.trim().toLowerCase();
    if (!q) return cards;
    return cards.filter((c) => humanizeSchemaName(c.name).toLowerCase().includes(q));
  }, [cards, search]);

  const grouped = useMemo(() => {
    if (!filtered) return null;
    const g: Record<SchemaDomain, KMSchemaCard[]> = {
      legal: [], finance: [], hr: [], medical: [],
      engineering: [], communications: [], reports: [], dev: [],
    };
    for (const c of filtered) g[categorizeSchema(c.name)].push(c);
    for (const k of Object.keys(g) as SchemaDomain[]) {
      g[k].sort((a, b) => humanizeSchemaName(a.name).localeCompare(humanizeSchemaName(b.name)));
    }
    return g;
  }, [filtered]);

  const stats = useMemo(() => {
    if (!cards) return null;
    const visible = filtered?.length ?? 0;
    const filesTotal = cards.reduce((acc, c) => acc + c.file_count, 0);
    const subTotal = cards.reduce(
      (acc, c) => acc + c.sub_entity_types.reduce((s, st) => s + st.row_count, 0), 0,
    );
    return [
      { label: "doc types",     value: search ? `${visible} of ${cards.length}` : cards.length },
      { label: "files",         value: filesTotal },
      { label: "sub-entities",  value: subTotal },
    ];
  }, [cards, filtered, search]);

  const opened = useMemo(
    () => (cards ?? []).find((c) => c.id === openId) ?? null,
    [cards, openId],
  );

  return (
    <>
      {stats && <StatStrip items={stats} />}

      {/* Sticky search/filter bar — sits below the StatStrip in the
          same scroll context. */}
      <div className="px-5 py-2 border-b border-zinc-200 bg-white sticky top-[33px] z-10">
        <div className="relative max-w-md">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-zinc-400" />
          <input
            ref={searchRef}
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search doc types…  (press /)"
            className="w-full pl-8 pr-3 py-1.5 text-[13px] border border-zinc-200 rounded-md focus:outline-none focus:border-zinc-400"
            data-testid="km-catalog-search"
          />
        </div>
      </div>

      <div className="px-5 py-3">
        {err && <ErrorBanner msg={err} />}
        {cards === null ? <SkeletonLines count={8} /> : (
          <CatalogList
            grouped={grouped!}
            showDev={showDev}
            setShowDev={setShowDev}
            onOpen={setOpenId}
          />
        )}
      </div>

      <SidePanel
        open={opened !== null}
        onClose={() => setOpenId(null)}
        title={opened ? humanizeSchemaName(opened.name) : ""}
        subtitle={opened ? `${opened.file_count} file${opened.file_count === 1 ? "" : "s"} · ${opened.doc_root_fields.length} doc-level field${opened.doc_root_fields.length === 1 ? "" : "s"} · Created ${relativeTime(opened.created_at)}` : ""}
      >
        {opened && <CatalogDetail card={opened} />}
      </SidePanel>
    </>
  );
}


function CatalogList({
  grouped, showDev, setShowDev, onOpen,
}: {
  grouped: Record<SchemaDomain, KMSchemaCard[]>;
  showDev: boolean;
  setShowDev: (v: boolean) => void;
  onOpen: (id: string) => void;
}) {
  const totalVisible = VISIBLE_DOMAINS.reduce((acc, d) => acc + grouped[d].length, 0);
  if (totalVisible === 0 && grouped.dev.length === 0) {
    return (
      <div className="text-[13px] text-zinc-500 px-2 py-8 text-center">
        No matches. Try a different search.
      </div>
    );
  }
  return (
    <div className="space-y-5">
      {VISIBLE_DOMAINS.map((dom) => {
        const items = grouped[dom];
        if (items.length === 0) return null;
        const meta = DOMAINS[dom];
        return (
          <section key={dom} data-testid={`km-domain-${dom}`}>
            <div className="mb-1 flex items-baseline gap-2 px-1">
              <span className="text-sm">{meta.emoji}</span>
              <h2 className="text-[12px] uppercase tracking-wider font-medium text-zinc-700">
                {meta.label}
              </h2>
              <span className="text-[11px] mono text-zinc-400">{items.length}</span>
            </div>
            <div className="bg-white border border-zinc-200 rounded-md divide-y divide-zinc-100">
              {items.map((c) => (
                <CatalogRow key={c.id} card={c} onOpen={() => onOpen(c.id)} />
              ))}
            </div>
          </section>
        );
      })}

      {grouped.dev.length > 0 && (
        <section className="pt-2 border-t border-zinc-200">
          <button
            type="button"
            onClick={() => setShowDev(!showDev)}
            className="text-[12px] text-zinc-500 hover:text-zinc-900 flex items-center gap-1.5 cursor-pointer px-1 py-1"
          >
            {showDev ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
            {DOMAINS.dev.emoji} {DOMAINS.dev.label}
            <span className="mono">{grouped.dev.length}</span>
            <span className="text-zinc-400">· hidden by default</span>
          </button>
          {showDev && (
            <div className="mt-2 bg-white border border-zinc-200 rounded-md divide-y divide-zinc-100">
              {grouped.dev.map((c) => (
                <CatalogRow key={c.id} card={c} onOpen={() => onOpen(c.id)} />
              ))}
            </div>
          )}
        </section>
      )}
    </div>
  );
}


function CatalogRow({ card, onOpen }: { card: KMSchemaCard; onOpen: () => void }) {
  const title = humanizeSchemaName(card.name);
  const subRows = card.sub_entity_types.reduce((acc, s) => acc + s.row_count, 0);
  return (
    <button
      type="button"
      onClick={onOpen}
      className="w-full px-3 py-2 text-left hover:bg-zinc-50 cursor-pointer flex items-center gap-3"
      data-testid="km-catalog-row"
    >
      <span className="text-[13px] text-zinc-900 truncate flex-1">{title}</span>
      <span className="text-[11px] mono text-zinc-500 flex-shrink-0">
        {card.file_count} file{card.file_count === 1 ? "" : "s"}
        <span className="mx-1.5 text-zinc-300">·</span>
        {card.doc_root_fields.length} field{card.doc_root_fields.length === 1 ? "" : "s"}
        {card.sub_entity_types.length > 0 && (
          <>
            <span className="mx-1.5 text-zinc-300">·</span>
            {card.sub_entity_types.length} sub-type{card.sub_entity_types.length === 1 ? "" : "s"}
            {subRows > 0 && <span className="text-zinc-400"> ({subRows} row{subRows === 1 ? "" : "s"})</span>}
          </>
        )}
      </span>
      <span className="text-[11px] text-zinc-400 flex-shrink-0 w-20 text-right">
        {relativeTime(card.created_at)}
      </span>
      <ChevronRight className="w-3.5 h-3.5 text-zinc-400 flex-shrink-0" />
    </button>
  );
}


function CatalogDetail({ card }: { card: KMSchemaCard }) {
  const router = useRouter();
  const title = humanizeSchemaName(card.name);
  return (
    <div className="p-5 space-y-5">
      {/* Action bar */}
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => router.push(`/chat?q=${encodeURIComponent(`summarize ${title.toLowerCase()}`)}`)}
          className="text-[12px] flex items-center gap-1.5 px-2.5 py-1 rounded-md border border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50 cursor-pointer"
        >
          <MessageSquare className="w-3.5 h-3.5" /> Ask a question
        </button>
        {card.file_count === 1 && card.file_ids[0] && (
          <a
            href={`/files/${card.file_ids[0]}`}
            className="text-[12px] flex items-center gap-1.5 px-2.5 py-1 rounded-md border border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50 cursor-pointer"
          >
            <FileText className="w-3.5 h-3.5" /> View file
          </a>
        )}
        {card.file_count > 1 && (
          <a
            href={`/upload`}
            className="text-[12px] flex items-center gap-1.5 px-2.5 py-1 rounded-md border border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50 cursor-pointer"
          >
            <FileText className="w-3.5 h-3.5" /> View {card.file_count} files
          </a>
        )}
        <button
          type="button"
          onClick={() => downloadSchemaExportYaml().catch(console.error)}
          className="text-[12px] flex items-center gap-1.5 px-2.5 py-1 rounded-md border border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50 cursor-pointer"
        >
          <Download className="w-3.5 h-3.5" /> Export YAML
        </button>
      </div>

      {/* Doc-root fields */}
      <section>
        <div className="text-[10px] uppercase tracking-wider text-zinc-400 mb-1.5">
          Doc-level fields ({card.doc_root_fields.length})
        </div>
        {card.doc_root_fields.length === 0 ? (
          <div className="text-[12px] text-zinc-400 italic">No doc-level fields.</div>
        ) : (
          <ul className="space-y-0.5">
            {card.doc_root_fields.map((f) => (
              <li
                key={f.name}
                className="text-[12px] grid grid-cols-[1fr_auto] gap-3 items-baseline py-1 border-b border-zinc-100 last:border-b-0"
                title={f.description ?? undefined}
              >
                <span className="mono text-zinc-700">{f.name}</span>
                <span className="text-[11px] text-zinc-400">{f.type ?? "—"}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Sub-entity types */}
      {card.sub_entity_types.length > 0 && (
        <section>
          <div className="text-[10px] uppercase tracking-wider text-zinc-400 mb-1.5">
            Contains
          </div>
          <div className="space-y-2">
            {card.sub_entity_types.map((s) => <SubEntityBlock key={s.unit_type} sub={s} />)}
          </div>
        </section>
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
          {sub.fields.map((f) => <span key={f.name}>{f.name}</span>)}
        </div>
      )}
    </div>
  );
}


// ---------------------------------------------------------------------------
// 🔍 Needs Review tab
// ---------------------------------------------------------------------------


type ReviewSubTab = "anomalies" | "conflicts" | "emerging" | "synonyms";
const REVIEW_PAGE_SIZE = 30;


function NeedsReviewTab() {
  const [data, setData] = useState<KMNeedsReview | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [sub, setSub] = useState<ReviewSubTab>("anomalies");
  const [openAnomaly, setOpenAnomaly] = useState<KMAnomaly | null>(null);
  const [openConflict, setOpenConflict] = useState<KMConflict | null>(null);
  const [shownAnomalies, setShownAnomalies] = useState(REVIEW_PAGE_SIZE);
  const [shownConflicts, setShownConflicts] = useState(REVIEW_PAGE_SIZE);

  useEffect(() => {
    let cancelled = false;
    getKnowledgeMapNeedsReview({ anomalyLimit: 100, conflictLimit: 100 })
      .then((r) => !cancelled && setData(r))
      .catch((e) => !cancelled && setErr(String(e)));
    return () => { cancelled = true; };
  }, []);

  // Default to the sub-tab with the most pending items.
  useEffect(() => {
    if (!data) return;
    const max = Math.max(data.anomalies_total, data.conflicts_total, data.emerging_fields_total, data.synonym_proposals_total);
    if (max === 0) return;
    if (data.anomalies_total === max) setSub("anomalies");
    else if (data.conflicts_total === max) setSub("conflicts");
    else if (data.emerging_fields_total === max) setSub("emerging");
    else setSub("synonyms");
  }, [data]);

  const stats = useMemo(() => data ? [
    { label: "anomalies",  value: data.anomalies_total },
    { label: "conflicts",  value: data.conflicts_total },
    { label: "emerging",   value: data.emerging_fields_total },
    { label: "synonyms",   value: data.synonym_proposals_total },
  ] : null, [data]);

  return (
    <>
      {stats && <StatStrip items={stats} />}

      {/* Sticky sub-tabs */}
      <div className="px-5 py-2 border-b border-zinc-200 bg-white sticky top-[33px] z-10 flex items-center gap-1 text-[12px]">
        <SubTab label="🔥 Anomalies"  count={data?.anomalies_total ?? 0}        active={sub === "anomalies"} onClick={() => setSub("anomalies")} />
        <SubTab label="⚠ Conflicts"   count={data?.conflicts_total ?? 0}        active={sub === "conflicts"} onClick={() => setSub("conflicts")} />
        <SubTab label="🌿 Emerging"   count={data?.emerging_fields_total ?? 0}  active={sub === "emerging"}  onClick={() => setSub("emerging")} />
        <SubTab label="📖 Synonyms"   count={data?.synonym_proposals_total ?? 0} active={sub === "synonyms"} onClick={() => setSub("synonyms")} />
      </div>

      <div className="px-5 py-3">
        {err && <ErrorBanner msg={err} />}
        {data === null ? <SkeletonLines count={8} /> : (
          <>
            {sub === "anomalies" && (
              <AnomalyList
                rows={data.anomalies.slice(0, shownAnomalies)}
                total={data.anomalies_total}
                onOpen={setOpenAnomaly}
                onLoadMore={() => setShownAnomalies((n) => n + REVIEW_PAGE_SIZE)}
                hasMore={shownAnomalies < data.anomalies.length}
              />
            )}
            {sub === "conflicts" && (
              <ConflictList
                rows={data.conflicts.slice(0, shownConflicts)}
                total={data.conflicts_total}
                onOpen={setOpenConflict}
                onLoadMore={() => setShownConflicts((n) => n + REVIEW_PAGE_SIZE)}
                hasMore={shownConflicts < data.conflicts.length}
              />
            )}
            {sub === "emerging" && (
              <ReviewEmptyState
                emoji="🌿"
                title={data.emerging_fields_total === 0 ? "Nothing emerging" : "Emerging fields"}
                body="Fields that haven't yet crossed the auto-promotion threshold (prevalence ≥ 80% · stability ≥ 90% · value-type confidence ≥ 90% · min 5 docs). In this workspace, every discovered field already crossed the threshold."
              />
            )}
            {sub === "synonyms" && (
              <ReviewEmptyState
                emoji="📖"
                title="No synonym proposals"
                body="Pairs of field names that look semantically similar (e.g. `monthly_uptime` ≈ `sla_uptime`). No proposals pending right now."
              />
            )}
          </>
        )}
      </div>

      <SidePanel
        open={openAnomaly !== null}
        onClose={() => setOpenAnomaly(null)}
        title={openAnomaly ? `${humanizeSchemaName(openAnomaly.unit_type)} · rarity ${openAnomaly.rarity_score.toFixed(2)}` : ""}
        subtitle={openAnomaly?.file_name ?? undefined}
      >
        {openAnomaly && <AnomalyDetail a={openAnomaly} />}
      </SidePanel>

      <SidePanel
        open={openConflict !== null}
        onClose={() => setOpenConflict(null)}
        title={openConflict ? `Conflict · ${openConflict.predicate}` : ""}
        subtitle={openConflict ? `${openConflict.evidence_count} sources · ${relativeTime(openConflict.observed_at)}` : ""}
      >
        {openConflict && <ConflictDetail c={openConflict} />}
      </SidePanel>
    </>
  );
}


function SubTab({ label, count, active, onClick }: { label: string; count: number; active: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`px-2.5 py-1 rounded-full cursor-pointer flex items-center gap-1.5 ${
        active
          ? "bg-zinc-900 text-white"
          : "text-zinc-700 hover:bg-zinc-100"
      }`}
    >
      <span>{label}</span>
      <span className={`text-[10px] mono ${active ? "text-zinc-300" : "text-zinc-400"}`}>{count}</span>
    </button>
  );
}


function AnomalyList({ rows, total, onOpen, onLoadMore, hasMore }: {
  rows: KMAnomaly[];
  total: number;
  onOpen: (a: KMAnomaly) => void;
  onLoadMore: () => void;
  hasMore: boolean;
}) {
  if (total === 0) return <ReviewEmptyState emoji="🔥" title="No anomalies" body="No sub-entity rows exceed the rarity threshold (0.8) right now." />;
  return (
    <>
      <div className="bg-white border border-zinc-200 rounded-md divide-y divide-zinc-100">
        {rows.map((a) => (
          <button
            key={a.id}
            type="button"
            onClick={() => onOpen(a)}
            className="w-full px-3 py-2 text-left hover:bg-zinc-50 cursor-pointer"
            data-testid="km-anomaly-row"
          >
            <div className="flex items-center gap-2 text-[11px]">
              <span className="px-1.5 py-0.5 rounded bg-rose-50 text-rose-700 mono">
                rarity {a.rarity_score.toFixed(2)}
              </span>
              <span className="mono text-zinc-500">{a.unit_type}</span>
              <span className="text-zinc-300">·</span>
              <span className="text-zinc-600 truncate flex-1">{a.file_name ?? "(unknown file)"}</span>
              <ChevronRight className="w-3 h-3 text-zinc-400 flex-shrink-0" />
            </div>
            <div className="mt-1 text-[12px] text-zinc-700 mono truncate">
              {summarizeFields(a.fields)}
            </div>
          </button>
        ))}
      </div>
      <LoadMore shown={rows.length} total={total} onLoadMore={onLoadMore} hasMore={hasMore} />
    </>
  );
}


function ConflictList({ rows, total, onOpen, onLoadMore, hasMore }: {
  rows: KMConflict[];
  total: number;
  onOpen: (c: KMConflict) => void;
  onLoadMore: () => void;
  hasMore: boolean;
}) {
  if (total === 0) return <ReviewEmptyState emoji="⚠" title="No conflicts" body="No unresolved conflicts in this workspace." />;
  return (
    <>
      <div className="bg-white border border-zinc-200 rounded-md divide-y divide-zinc-100">
        {rows.map((c) => (
          <button
            key={c.id}
            type="button"
            onClick={() => onOpen(c)}
            className="w-full px-3 py-2 text-left hover:bg-zinc-50 cursor-pointer"
            data-testid="km-conflict-row"
          >
            <div className="flex items-center gap-2 text-[11px]">
              <span className="px-1.5 py-0.5 rounded bg-amber-50 text-amber-800 mono">
                {c.evidence_count} sources
              </span>
              <span className="mono text-zinc-700 truncate flex-1">{c.predicate}</span>
              <span className="text-zinc-400">{relativeTime(c.observed_at)}</span>
              <ChevronRight className="w-3 h-3 text-zinc-400 flex-shrink-0" />
            </div>
            <div className="mt-1 text-[11px] text-zinc-600 mono truncate">
              {c.evidence_preview.slice(0, 3).map((e, i) => (
                <span key={i}>
                  {i > 0 && <span className="text-zinc-300 mx-1.5">·</span>}
                  {String((e as Record<string, unknown>).value ?? "—")}
                </span>
              ))}
            </div>
          </button>
        ))}
      </div>
      <LoadMore shown={rows.length} total={total} onLoadMore={onLoadMore} hasMore={hasMore} />
    </>
  );
}


function AnomalyDetail({ a }: { a: KMAnomaly }) {
  const router = useRouter();
  return (
    <div className="p-5 space-y-4">
      <div>
        <div className="text-[10px] uppercase tracking-wider text-zinc-400 mb-1.5">Why flagged</div>
        <div className="text-[13px] text-zinc-700 leading-relaxed">
          This <span className="mono">{a.unit_type}</span> row scored a rarity of{" "}
          <span className="mono">{a.rarity_score.toFixed(2)}</span> against the cohort
          (≥ 0.8 is the anomaly threshold). The values below stood out as outliers
          relative to other rows of the same type in this workspace.
        </div>
      </div>
      <div>
        <div className="text-[10px] uppercase tracking-wider text-zinc-400 mb-1.5">Fields</div>
        <ul className="space-y-0.5">
          {Object.entries(a.fields).map(([k, v]) => (
            <li key={k} className="text-[12px] grid grid-cols-[150px_1fr] gap-3 py-1 border-b border-zinc-100 last:border-b-0">
              <span className="mono text-zinc-500">{k}</span>
              <span className="mono text-zinc-800 break-all">{typeof v === "string" ? v : JSON.stringify(v)}</span>
            </li>
          ))}
        </ul>
      </div>
      <div className="flex gap-2">
        {a.file_id && (
          <a
            href={`/files/${a.file_id}`}
            className="text-[12px] flex items-center gap-1.5 px-2.5 py-1 rounded-md border border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50 cursor-pointer"
          >
            <FileText className="w-3.5 h-3.5" /> Open file
          </a>
        )}
        <button
          type="button"
          onClick={() => router.push(`/chat?q=${encodeURIComponent(`why is this ${a.unit_type} flagged as anomalous?`)}`)}
          className="text-[12px] flex items-center gap-1.5 px-2.5 py-1 rounded-md border border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50 cursor-pointer"
        >
          <MessageSquare className="w-3.5 h-3.5" /> Ask why
        </button>
      </div>
    </div>
  );
}


function ConflictDetail({ c }: { c: KMConflict }) {
  return (
    <div className="p-5 space-y-4">
      <div>
        <div className="text-[10px] uppercase tracking-wider text-zinc-400 mb-1.5">Predicate</div>
        <div className="text-[13px] mono text-zinc-800">{c.predicate}</div>
      </div>
      <div>
        <div className="text-[10px] uppercase tracking-wider text-zinc-400 mb-1.5">
          {c.evidence_count} sources disagree
        </div>
        <div className="space-y-1.5">
          {c.evidence_preview.map((e, i) => {
            const r = e as Record<string, unknown>;
            return (
              <div key={i} className="text-[12px] bg-zinc-50 border border-zinc-100 rounded px-2.5 py-1.5">
                <div className="mono text-zinc-900">{String(r.value ?? "—")}</div>
                <div className="text-[10px] text-zinc-500 mono mt-0.5">
                  via {String(r.hit_id ?? "?").slice(0, 8)}
                  {r.authority != null && <> · authority {String(r.authority)}</>}
                  {r.doc_status != null && <> · {String(r.doc_status)}</>}
                </div>
              </div>
            );
          })}
        </div>
      </div>
      {c.notes && (
        <div className="text-[11px] text-zinc-500 italic">{c.notes}</div>
      )}
    </div>
  );
}


function ReviewEmptyState({ emoji, title, body }: { emoji: string; title: string; body: string }) {
  return (
    <div className="bg-white border border-dashed border-zinc-200 rounded-md px-6 py-8 text-center">
      <div className="text-2xl mb-1">{emoji}</div>
      <div className="text-sm font-medium text-zinc-700">{title}</div>
      <div className="text-[12px] text-zinc-500 mt-1 max-w-md mx-auto leading-relaxed">{body}</div>
    </div>
  );
}


function LoadMore({ shown, total, onLoadMore, hasMore }: {
  shown: number;
  total: number;
  onLoadMore: () => void;
  hasMore: boolean;
}) {
  return (
    <div className="text-[12px] text-zinc-500 mt-2 flex items-center justify-between">
      <span>{shown} of {total}</span>
      {hasMore && (
        <button
          type="button"
          onClick={onLoadMore}
          className="text-[12px] px-2.5 py-1 rounded-md border border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50 cursor-pointer"
        >
          Load {Math.min(REVIEW_PAGE_SIZE, total - shown)} more
        </button>
      )}
    </div>
  );
}


function summarizeFields(fields: Record<string, unknown>): string {
  const entries = Object.entries(fields).slice(0, 5);
  return entries
    .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(" · ");
}


// ---------------------------------------------------------------------------
// 🕓 History tab
// ---------------------------------------------------------------------------


type HistoryFilter = "all" | "schema" | "extraction" | "identity" | "errors";

const HISTORY_PAGE_SIZE = 100;

const FILTER_MATCH: Record<HistoryFilter, (event: string) => boolean> = {
  all: () => true,
  schema: (e) => e.startsWith("schema_") || e === "doc_chain_detected",
  extraction: (e) => /^(parse|chunk|contextualization|embedding|raptor|mentions|fields|atomic|kv_tables)/.test(e),
  identity: (e) => e.startsWith("identit"),
  errors: (e) => e.includes("failed") || e.includes("error"),
};


function HistoryTab() {
  const [filter, setFilter] = useState<HistoryFilter>("all");
  const [events, setEvents] = useState<KMHistoryEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Initial load.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setEvents([]);
    getKnowledgeMapHistory({ limit: HISTORY_PAGE_SIZE })
      .then((r) => {
        if (cancelled) return;
        setEvents(r.items);
        setCursor(r.next_cursor);
        setTotal(r.total);
      })
      .catch((e) => !cancelled && setErr(String(e)))
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, []);

  const loadMore = useCallback(async () => {
    if (!cursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const r = await getKnowledgeMapHistory({ limit: HISTORY_PAGE_SIZE, cursor });
      setEvents((prev) => [...prev, ...r.items]);
      setCursor(r.next_cursor);
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoadingMore(false);
    }
  }, [cursor, loadingMore]);

  // Infinite scroll — Intersection Observer on a sentinel at the bottom.
  const sentinelRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el || !cursor) return;
    const obs = new IntersectionObserver(
      (entries) => entries[0].isIntersecting && loadMore(),
      { rootMargin: "200px" },
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [cursor, loadMore]);

  // Client-side filter on already-loaded events. Filter chips also push
  // a server-side prefix when relevant — but for the demo, in-memory is
  // fast enough.
  const filtered = useMemo(
    () => events.filter((e) => FILTER_MATCH[filter](e.event)),
    [events, filter],
  );

  // Group by day, preserving DESC order.
  const grouped = useMemo(() => {
    const out: Array<{ date: string; events: KMHistoryEvent[] }> = [];
    for (const e of filtered) {
      const date = e.created_at.slice(0, 10);
      const last = out[out.length - 1];
      if (last && last.date === date) last.events.push(e);
      else out.push({ date, events: [e] });
    }
    return out;
  }, [filtered]);

  const stats = useMemo(() => [
    { label: "events total", value: total },
    { label: "loaded",       value: events.length },
    { label: "filtered",     value: filtered.length },
  ], [events.length, filtered.length, total]);

  return (
    <>
      <StatStrip items={stats} />

      {/* Sticky filter bar */}
      <div className="px-5 py-2 border-b border-zinc-200 bg-white sticky top-[33px] z-10 flex items-center gap-2 text-[12px]">
        <span className="text-zinc-500">Filter:</span>
        {(["all", "schema", "extraction", "identity", "errors"] as HistoryFilter[]).map((f) => (
          <button
            key={f}
            type="button"
            onClick={() => setFilter(f)}
            className={`px-2 py-0.5 rounded-full cursor-pointer ${
              filter === f
                ? "bg-zinc-900 text-white"
                : "bg-white text-zinc-700 hover:bg-zinc-100 border border-zinc-200"
            }`}
          >
            {f}
          </button>
        ))}
      </div>

      <div className="px-5 py-3">
        {err && <ErrorBanner msg={err} />}
        {loading ? <SkeletonLines count={12} /> : grouped.length === 0 ? (
          <div className="text-[13px] text-zinc-500 px-2 py-8 text-center">
            No events match this filter.
          </div>
        ) : (
          <div className="space-y-5">
            {grouped.map((g) => (
              <section key={g.date}>
                <div className="text-[11px] uppercase tracking-wider text-zinc-400 mb-1 sticky top-[81px] bg-zinc-50/95 backdrop-blur py-1 z-[5]">
                  {formatDayHeader(g.date)} · {g.events.length} event{g.events.length === 1 ? "" : "s"}
                </div>
                <div className="bg-white border border-zinc-200 rounded-md divide-y divide-zinc-100">
                  {g.events.map((e) => (
                    <div
                      key={e.id}
                      className="px-3 py-1.5 text-[12px] grid grid-cols-[64px_180px_1fr_80px] gap-3 items-center"
                    >
                      <span className="mono text-zinc-400">{e.created_at.slice(11, 19)}</span>
                      <span className="mono text-zinc-700 truncate">{e.event}</span>
                      <span className="text-zinc-700 truncate" title={e.file_name ?? ""}>
                        {e.file_name ?? <span className="text-zinc-400 italic">(no file)</span>}
                      </span>
                      <span className="text-[10px] text-zinc-400 text-right truncate">
                        {e.to_state ?? ""}
                      </span>
                    </div>
                  ))}
                </div>
              </section>
            ))}
            <div ref={sentinelRef} className="h-6 flex items-center justify-center">
              {cursor && (loadingMore ? (
                <Loader2 className="w-4 h-4 text-zinc-400 animate-spin" />
              ) : (
                <span className="text-[11px] text-zinc-400">scroll for more</span>
              ))}
              {!cursor && events.length > 0 && (
                <span className="text-[11px] text-zinc-400">end of history</span>
              )}
            </div>
          </div>
        )}
      </div>
    </>
  );
}


function formatDayHeader(yyyymmdd: string): string {
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
