"use client";

/**
 * Explore — the "what's in my corpus" page.
 *
 * Two rails + a results column:
 *   - Left rail "View as": all-results / per-category with counts
 *   - Inner panel "Filter by": doc-type checkboxes (multi-select),
 *     date dropdown, has-anomaly / has-conflicts / has-chain toggles
 *   - Center: search input + paginated result list, grouped by
 *     category in "All results", flat when scoped to one kind.
 *
 * URL state — every filter + the active kind lives in the URL search
 * params, so:
 *   - browser back/forward navigates filter history
 *   - bookmarks / shares preserve the view
 *   - clicking into a file or chat → coming back via browser-back
 *     restores the exact view the user left
 *
 * Pagination — "Load more (N remaining)" button at the bottom of the
 * result list when total_estimate > items_loaded. Server-paginated
 * via offset+limit so a 100k workspace doesn't paint 100k rows.
 *
 * Per-entity Related accordion — caps at 25 items per bucket per the
 * /explore/entity/{id}/profile contract; for deeper drill-down, click
 * "view all →" which deep-links into a scoped Explore view.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams, usePathname } from "next/navigation";
import {
  Search, LayoutGrid, FileText, Folder, Puzzle, Users, GitMerge,
  Tag, AlertCircle, Loader2, ChevronRight, ExternalLink,
  Mail, DollarSign, User, Building, AlertOctagon, FileQuestion,
  type LucideIcon,
} from "lucide-react";
import {
  exploreSearch, getExploreCounts, getEntityProfile,
  type ExploreCounts, type ExploreHit, type ExploreKind,
  type EntityProfile, type EntityProfileBucket,
} from "@/lib/api";
import { Sidebar } from "@/components/Sidebar";


const BUCKET_ICONS: Record<string, LucideIcon> = {
  "file-text":     FileText,
  "dollar-sign":   DollarSign,
  "mail":          Mail,
  "user":          User,
  "users":         Users,
  "building":      Building,
  "alert-circle":  AlertCircle,
  "alert-octagon": AlertOctagon,
  "blueprint":     FileText,
  "sticky-note":   FileText,
  "file":          FileQuestion,
};


type RailKey = "all" | ExploreKind;

type RailItem = {
  key: RailKey;
  label: string;
  icon: LucideIcon;
  countKey?: keyof ExploreCounts;
};

const RAIL_ITEMS: RailItem[] = [
  { key: "all",          label: "All results",   icon: LayoutGrid },
  { key: "document",     label: "Documents",     icon: FileText,    countKey: "documents" },
  { key: "doc_type",     label: "Doc types",     icon: Folder,      countKey: "doc_types" },
  { key: "atomic_unit",  label: "Atomic units",  icon: Puzzle,      countKey: "atomic_units" },
  { key: "entity",       label: "Entities",      icon: Users,       countKey: "entities" },
  { key: "relationship", label: "Relationships", icon: GitMerge,    countKey: "relationships" },
  { key: "topic",        label: "Topics",        icon: Tag,         countKey: "topics" },
  { key: "anomaly",      label: "Anomalies",     icon: AlertCircle, countKey: "anomalies" },
];

const KIND_TO_RAIL: Record<ExploreKind, RailItem> = Object.fromEntries(
  RAIL_ITEMS.filter((r) => r.key !== "all").map((r) => [r.key, r])
) as Record<ExploreKind, RailItem>;


type DateRange = "any" | "7d" | "30d" | "365d";
type SortOrder = "relevance" | "name" | "recent";

const PAGE_SIZE = 60;
const FILTER_DOCTYPE_TOP_N = 6;


// ---------------------------------------------------------------------------
// URL state — every filter lives on `?` so back / forward + bookmarks work.
// `update(patch)` does a router.replace, which is a no-scroll navigation;
// useSearchParams below re-renders the page from the new URL.
// ---------------------------------------------------------------------------


type ExploreUrlState = {
  q: string;
  kind: RailKey;
  docTypes: string[];
  date: DateRange;
  anomaly: boolean;
  conflicts: boolean;
  chain: boolean;
  sort: SortOrder;
};

function parseExploreUrl(sp: URLSearchParams): ExploreUrlState {
  const kind = (sp.get("kind") as RailKey | null) ?? "all";
  return {
    q: sp.get("q") ?? "",
    kind: ([
      "all", "document", "doc_type", "atomic_unit",
      "entity", "relationship", "topic", "anomaly",
    ].includes(kind) ? kind : "all") as RailKey,
    docTypes: (sp.get("dt") ?? "").split(",").map((s) => s.trim()).filter(Boolean),
    date: (sp.get("date") as DateRange | null) ?? "any",
    anomaly: sp.get("anomaly") === "1",
    conflicts: sp.get("conflicts") === "1",
    chain: sp.get("chain") === "1",
    sort: (["relevance", "name", "recent"].includes(sp.get("sort") ?? "")
      ? (sp.get("sort") as SortOrder)
      : "relevance"),
  };
}

function buildExploreSearchString(s: ExploreUrlState): string {
  const params = new URLSearchParams();
  if (s.q) params.set("q", s.q);
  if (s.kind !== "all") params.set("kind", s.kind);
  if (s.docTypes.length) params.set("dt", s.docTypes.join(","));
  if (s.date !== "any") params.set("date", s.date);
  if (s.anomaly) params.set("anomaly", "1");
  if (s.conflicts) params.set("conflicts", "1");
  if (s.chain) params.set("chain", "1");
  if (s.sort !== "relevance") params.set("sort", s.sort);
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

function dateRangeToFromTo(d: DateRange): { from?: string; to?: string } {
  if (d === "any") return {};
  const days = d === "7d" ? 7 : d === "30d" ? 30 : 365;
  const now = new Date();
  const from = new Date(now.getTime() - days * 86_400_000);
  const iso = (x: Date) => x.toISOString().slice(0, 10);
  return { from: iso(from), to: iso(now) };
}


export default function ExplorePage() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const url = useMemo(
    () => parseExploreUrl(new URLSearchParams(searchParams.toString())),
    [searchParams],
  );

  function update(patch: Partial<ExploreUrlState>) {
    const next: ExploreUrlState = { ...url, ...patch };
    const qs = buildExploreSearchString(next);
    router.replace(`${pathname}${qs}`, { scroll: false });
  }

  // Search input has its own local state so typing isn't a re-render
  // storm against the URL bar. We push the debounced value to URL.
  const [qInput, setQInput] = useState(url.q);
  useEffect(() => { setQInput(url.q); }, [url.q]);
  useEffect(() => {
    const t = setTimeout(() => {
      if (qInput !== url.q) update({ q: qInput });
    }, 220);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qInput]);

  const [counts, setCounts] = useState<ExploreCounts | null>(null);
  const [allDocTypes, setAllDocTypes] = useState<{ name: string; count: number }[]>([]);
  const [docTypeListExpanded, setDocTypeListExpanded] = useState(false);

  // Result list state — pagination accumulates pages on Load more.
  const [items, setItems] = useState<ExploreHit[]>([]);
  const [totalEstimate, setTotalEstimate] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);

  // One-shot counts + doc-type browse.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [c, dtBrowse] = await Promise.all([
          getExploreCounts(),
          exploreSearch({ kind: "doc_type", limit: 100 }),
        ]);
        if (cancelled) return;
        setCounts(c);
        setAllDocTypes(dtBrowse.items.map((h) => ({
          name: h.title,
          count: Number((h.extra as { file_count?: number })?.file_count ?? 0),
        })));
      } catch (err) { console.error(err); }
    })();
    return () => { cancelled = true; };
  }, []);

  // Build the API filter args from current URL state. Doc types use
  // the comma-separated multi-value endpoint now.
  function apiArgs(forOffset: number) {
    const dr = dateRangeToFromTo(url.date);
    return {
      q: url.q || undefined,
      kind: url.kind === "all" ? null : url.kind,
      docTypes: url.docTypes.length ? url.docTypes : undefined,
      dateFrom: dr.from,
      dateTo: dr.to,
      hasAnomaly: url.anomaly,
      hasConflicts: url.conflicts,
      hasChain: url.chain,
      sort: url.sort,
      offset: forOffset,
      limit: PAGE_SIZE,
    };
  }

  // Refetch from offset=0 whenever any filter changes.
  const refetchFromStart = useCallback(async () => {
    setLoading(true);
    setOffset(0);
    try {
      const out = await exploreSearch(apiArgs(0));
      setItems(out.items);
      setTotalEstimate(out.total_estimate);
    } catch (err) {
      console.error(err);
      setItems([]); setTotalEstimate(0);
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    url.q, url.kind, JSON.stringify(url.docTypes), url.date,
    url.anomaly, url.conflicts, url.chain, url.sort,
  ]);

  useEffect(() => { refetchFromStart(); }, [refetchFromStart]);

  async function loadMore() {
    setLoadingMore(true);
    const newOffset = offset + PAGE_SIZE;
    try {
      const out = await exploreSearch(apiArgs(newOffset));
      setItems((prev) => [...prev, ...out.items]);
      setTotalEstimate(out.total_estimate);
      setOffset(newOffset);
    } catch (err) {
      console.error(err);
    } finally {
      setLoadingMore(false);
    }
  }

  // Group items for "all results" view; flat when scoped.
  const grouped = useMemo<Record<ExploreKind, ExploreHit[]>>(() => {
    const acc: Record<string, ExploreHit[]> = {};
    for (const h of items) (acc[h.kind] ??= []).push(h);
    return acc as Record<ExploreKind, ExploreHit[]>;
  }, [items]);

  // Doc-type checkbox toggle — multi-select.
  function toggleDocType(name: string) {
    const set = new Set(url.docTypes);
    if (set.has(name)) set.delete(name); else set.add(name);
    update({ docTypes: Array.from(set) });
  }

  function clearAllFilters() {
    update({ docTypes: [], date: "any", anomaly: false, conflicts: false, chain: false });
  }

  const anyFilterActive =
    url.docTypes.length > 0 || url.date !== "any"
    || url.anomaly || url.conflicts || url.chain;

  // Bucket "view all →" → scope into this Explore view.
  function scopeToBucket(bucket: EntityProfileBucket) {
    const patch: Partial<ExploreUrlState> = {};
    if (bucket.deep_link_kind && [
      "document", "atomic_unit", "anomaly", "entity",
      "doc_type", "relationship", "topic",
    ].includes(bucket.deep_link_kind)) {
      patch.kind = bucket.deep_link_kind as ExploreKind;
    }
    if (bucket.deep_link_doc_type) patch.docTypes = [bucket.deep_link_doc_type];
    if (bucket.deep_link_q) patch.q = bucket.deep_link_q;
    if (bucket.key === "anomalies") patch.anomaly = true;
    update(patch);
  }

  const remaining = Math.max(0, totalEstimate - items.length);

  return (
    <div className="flex h-full">
      <Sidebar current="explore" />

      <main className="flex-1 flex flex-col min-w-0 bg-white">
        <header className="h-12 flex-shrink-0 border-b border-zinc-200 flex items-center px-5 gap-4">
          <span className="text-sm text-zinc-900">Explore</span>
          {counts && (
            <span className="text-[11px] text-zinc-400 mono">
              {counts.documents} docs · {counts.doc_types} doc types ·{" "}
              {counts.atomic_units} atomic units · {counts.entities} entities ·{" "}
              {counts.relationships} relationships
            </span>
          )}
        </header>

        <div className="flex-1 flex min-h-0">
          {/* Left rail */}
          <aside
            className="w-[240px] flex-shrink-0 border-r border-zinc-200 overflow-y-auto bg-white"
            data-testid="explore-rail"
          >
            <div className="p-4">
              <div className="text-[10px] uppercase tracking-wider text-zinc-400 mb-2">
                View as
              </div>
              <div className="space-y-px">
                {RAIL_ITEMS.map((item) => {
                  const Icon = item.icon;
                  const active = item.key === url.kind;
                  const total =
                    item.key === "all"
                      ? totalEstimate
                      : (counts?.[item.countKey!] ?? 0);
                  return (
                    <button
                      key={item.key}
                      type="button"
                      onClick={() => update({ kind: item.key })}
                      className={`w-full flex items-center justify-between px-2 py-1.5 rounded text-sm cursor-pointer ${
                        active ? "bg-zinc-100 text-zinc-900" : "text-zinc-600 hover:bg-zinc-50"
                      }`}
                      data-testid={`explore-rail-${item.key}`}
                    >
                      <span className="flex items-center gap-2">
                        <Icon className="w-3.5 h-3.5" strokeWidth={1.75} />
                        {item.label}
                      </span>
                      <span className="text-[11px] text-zinc-500 mono">{total}</span>
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Filter-by panel */}
            <div className="border-t border-zinc-200 p-4">
              <div className="flex items-center justify-between mb-2">
                <div className="text-[10px] uppercase tracking-wider text-zinc-400">
                  Filter by
                </div>
                {anyFilterActive && (
                  <button
                    type="button"
                    onClick={clearAllFilters}
                    className="text-[10px] text-zinc-500 hover:text-zinc-900 mono cursor-pointer"
                    data-testid="explore-filter-clear"
                  >
                    clear
                  </button>
                )}
              </div>

              <div className="mb-3 text-xs">
                <div className="text-zinc-500 mb-1.5">Doc type</div>
                <div className="space-y-0.5">
                  {(docTypeListExpanded
                    ? allDocTypes
                    : allDocTypes.slice(0, FILTER_DOCTYPE_TOP_N)
                  ).map((dt) => (
                    <label
                      key={dt.name}
                      className="flex items-center gap-2 py-0.5 text-zinc-700 cursor-pointer"
                    >
                      <input
                        type="checkbox"
                        checked={url.docTypes.includes(dt.name)}
                        onChange={() => toggleDocType(dt.name)}
                        className="accent-zinc-900 w-3 h-3"
                        data-testid="explore-filter-doctype"
                      />
                      <span className="mono text-[11px] truncate flex-1">{dt.name}</span>
                      <span className="text-[10px] text-zinc-400 mono">{dt.count}</span>
                    </label>
                  ))}
                  {allDocTypes.length > FILTER_DOCTYPE_TOP_N && (
                    <button
                      type="button"
                      onClick={() => setDocTypeListExpanded(!docTypeListExpanded)}
                      className="text-zinc-500 hover:text-zinc-900 mt-1 mono text-[10px] cursor-pointer"
                    >
                      {docTypeListExpanded ? "− show fewer" : `+ ${allDocTypes.length - FILTER_DOCTYPE_TOP_N} more`}
                    </button>
                  )}
                </div>
              </div>

              <div className="mb-3 text-xs">
                <div className="text-zinc-500 mb-1.5">Date</div>
                <select
                  value={url.date}
                  onChange={(e) => update({ date: e.target.value as DateRange })}
                  className="w-full text-xs px-2 py-1 rounded border border-zinc-200 bg-white cursor-pointer"
                  data-testid="explore-filter-date"
                >
                  <option value="any">Any time</option>
                  <option value="7d">Last 7 days</option>
                  <option value="30d">Last 30 days</option>
                  <option value="365d">Last year</option>
                </select>
              </div>

              <div className="text-xs">
                <div className="text-zinc-500 mb-1.5">Has</div>
                <label className="flex items-center gap-2 py-0.5 text-zinc-700 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={url.anomaly}
                    onChange={(e) => update({ anomaly: e.target.checked })}
                    className="accent-zinc-900 w-3 h-3"
                    data-testid="explore-filter-anomaly"
                  />
                  anomaly
                </label>
                <label className="flex items-center gap-2 py-0.5 text-zinc-700 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={url.conflicts}
                    onChange={(e) => update({ conflicts: e.target.checked })}
                    className="accent-zinc-900 w-3 h-3"
                    data-testid="explore-filter-conflicts"
                  />
                  conflicts
                </label>
                <label className="flex items-center gap-2 py-0.5 text-zinc-700 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={url.chain}
                    onChange={(e) => update({ chain: e.target.checked })}
                    className="accent-zinc-900 w-3 h-3"
                    data-testid="explore-filter-chain"
                  />
                  chain (amendments / thread)
                </label>
              </div>
            </div>
          </aside>

          {/* Center: search + results */}
          <section className="flex-1 overflow-y-auto">
            <div className="max-w-3xl mx-auto px-8 py-6">
              <div className="relative mb-5">
                <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-zinc-400" />
                <input
                  type="text"
                  value={qInput}
                  onChange={(e) => setQInput(e.target.value)}
                  placeholder="Search files, entities, clauses, topics…"
                  className="w-full pl-9 pr-24 py-2.5 text-sm rounded-lg border border-zinc-200 focus:outline-none focus:border-zinc-400"
                  data-testid="explore-search-input"
                />
                {qInput && (
                  <button
                    type="button"
                    onClick={() => setQInput("")}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-[11px] text-zinc-500 hover:text-zinc-900 mono cursor-pointer"
                    data-testid="explore-search-clear"
                  >
                    clear
                  </button>
                )}
              </div>

              <div className="flex items-center justify-between mb-4 min-h-[20px]">
                <div className="text-xs text-zinc-500">
                  {loading ? (
                    <span className="flex items-center gap-2">
                      <Loader2 className="w-3 h-3 animate-spin" /> searching…
                    </span>
                  ) : (
                    <>
                      <span className="text-zinc-900 font-medium">
                        {items.length} of {totalEstimate}
                      </span>{" "}
                      {url.q ? <>for &ldquo;{url.q}&rdquo; </> : <>across the workspace </>}
                      {url.kind !== "all" && (
                        <>· kind <span className="mono">{url.kind}</span></>
                      )}
                    </>
                  )}
                </div>
                <label className="text-[11px] text-zinc-500 flex items-center gap-1.5">
                  Sort
                  <select
                    value={url.sort}
                    onChange={(e) => update({ sort: e.target.value as SortOrder })}
                    className="bg-white border border-zinc-200 rounded px-2 py-1 text-[11px] mono text-zinc-700 hover:border-zinc-400 focus:outline-none focus:ring-1 focus:ring-zinc-300 cursor-pointer"
                    data-testid="explore-sort"
                  >
                    <option value="relevance">relevance</option>
                    <option value="name">name (A→Z)</option>
                    <option value="recent">most recent</option>
                  </select>
                </label>
              </div>

              {!loading && items.length === 0 && (
                <div className="rounded-lg border border-zinc-200 p-8 text-center text-sm text-zinc-500">
                  No results match the current filters.
                </div>
              )}

              {items.length > 0 && (
                <>
                  <div className="space-y-6">
                    {Object.entries(grouped).map(([k, list]) => {
                      const rail = KIND_TO_RAIL[k as ExploreKind];
                      const Icon = rail?.icon ?? LayoutGrid;
                      const isScoped = url.kind !== "all";
                      const totalForKind = counts?.[rail?.countKey ?? "documents"] ?? list.length;
                      return (
                        <div key={k}>
                          <div className="text-[10px] uppercase tracking-wider text-zinc-400 mb-2 flex items-center gap-2">
                            <Icon className="w-3 h-3" strokeWidth={1.75} />
                            {rail?.label ?? k}
                            <span className="mono text-zinc-400">{list.length}</span>
                            {!isScoped && totalForKind > list.length && (
                              <button
                                type="button"
                                onClick={() => update({ kind: k as ExploreKind })}
                                className="ml-auto text-[11px] text-zinc-500 hover:text-zinc-900 mono cursor-pointer flex items-center gap-1"
                                data-testid="explore-view-all"
                                data-target={k}
                              >
                                view all <span aria-hidden>→</span>
                              </button>
                            )}
                          </div>
                          <ul className="space-y-1.5">
                            {list.map((it) => (
                              <ResultCard
                                key={`${it.kind}-${it.id}`}
                                hit={it}
                                onScopeToBucket={scopeToBucket}
                              />
                            ))}
                          </ul>
                        </div>
                      );
                    })}
                  </div>

                  {/* Pagination footer — server-paginated Load more. */}
                  {remaining > 0 && url.kind !== "all" && (
                    <div className="mt-6 flex items-center justify-center">
                      <button
                        type="button"
                        onClick={loadMore}
                        disabled={loadingMore}
                        className="flex items-center gap-2 px-4 py-2 text-xs rounded-md border border-zinc-200 bg-white hover:bg-zinc-50 cursor-pointer disabled:opacity-50"
                        data-testid="explore-load-more"
                      >
                        {loadingMore ? (
                          <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        ) : null}
                        <span>Load more</span>
                        <span className="text-zinc-400 mono">
                          ({remaining} remaining)
                        </span>
                      </button>
                    </div>
                  )}
                  {/* "All results" view: explicit nudge to scope into a single
                      kind for pagination — avoids confusing mixed-kind Load
                      more that fans out multiplicatively across buckets. */}
                  {remaining > 0 && url.kind === "all" && (
                    <div className="mt-6 text-center text-[11px] text-zinc-400">
                      Showing {items.length} of {totalEstimate}. Click a category in the left rail to load more from that bucket.
                    </div>
                  )}
                </>
              )}
            </div>
          </section>
        </div>
      </main>
    </div>
  );
}


// ===========================================================================
// Result card — handles all 7 kinds, with extra detail for entities
// ===========================================================================


function ResultCard({
  hit, onScopeToBucket,
}: {
  hit: ExploreHit;
  onScopeToBucket: (bucket: EntityProfileBucket) => void;
}) {
  const extra = hit.extra ?? {};

  if (hit.kind === "entity") {
    return <EntityCard hit={hit} onScopeToBucket={onScopeToBucket} />;
  }

  return (
    <li
      className="rounded-lg border border-zinc-200 px-4 py-3 hover:bg-zinc-50"
      data-testid="explore-result"
      data-kind={hit.kind}
    >
      <div className="flex items-center gap-2">
        <span className="text-[10px] mono px-1.5 py-0.5 rounded bg-zinc-100 text-zinc-700">
          {hit.kind}
        </span>
        <span className="text-sm font-medium text-zinc-900 truncate">
          {hit.title}
        </span>
        {hit.subtitle && (
          <span className="text-[11px] text-zinc-500 mono ml-auto truncate">
            {hit.subtitle}
          </span>
        )}
      </div>
      {hit.snippet && (
        <div className="mt-1.5 text-xs text-zinc-500 leading-relaxed line-clamp-2">
          {hit.snippet}
        </div>
      )}
      {hit.file_name && hit.file_id && (
        <div className="mt-2 flex items-center gap-3 text-[11px] text-zinc-500">
          <a
            href={`/files/${hit.file_id}`}
            className="hover:text-zinc-900 mono flex items-center gap-1 cursor-pointer"
          >
            <ExternalLink className="w-3 h-3" />
            {hit.file_name}
          </a>
          {typeof extra.rarity_score === "number" && (
            <span className="mono text-amber-700">
              rarity {(extra.rarity_score as number).toFixed(2)}
            </span>
          )}
        </div>
      )}
    </li>
  );
}


function EntityCard({
  hit, onScopeToBucket,
}: {
  hit: ExploreHit;
  onScopeToBucket: (bucket: EntityProfileBucket) => void;
}) {
  // Per-entity expand state persists in sessionStorage so navigating
  // away (file detail, another entity) and coming back via the browser
  // back button restores the open accordions on the same Explore view.
  const storageKey = `explore.entity.${hit.id}.open`;
  const [open, setOpen] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    return sessionStorage.getItem(storageKey) === "1";
  });
  const [profile, setProfile] = useState<EntityProfile | null>(null);
  const [profileLoading, setProfileLoading] = useState(false);

  const searchExtra = (hit.extra ?? {}) as {
    aliases?: string[];
    first_seen?: string;
    last_seen?: string;
    n_docs?: number;
    mention_count?: number;
  };
  const aliases = profile?.aliases ?? searchExtra.aliases ?? [];
  const firstSeen = profile?.first_seen ?? searchExtra.first_seen;
  const lastSeen = profile?.last_seen ?? searchExtra.last_seen;
  const nDocs = profile?.n_docs ?? searchExtra.n_docs ?? 0;
  const mentionCount = profile?.mention_count ?? searchExtra.mention_count ?? 0;

  // If the user revisits with open=true persisted, fetch profile.
  useEffect(() => {
    if (open && profile === null && !profileLoading) {
      setProfileLoading(true);
      getEntityProfile(hit.id)
        .then(setProfile)
        .catch((err) => console.error("getEntityProfile failed", err))
        .finally(() => setProfileLoading(false));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  async function toggle() {
    const next = !open;
    setOpen(next);
    try {
      if (typeof window !== "undefined") {
        if (next) sessionStorage.setItem(storageKey, "1");
        else sessionStorage.removeItem(storageKey);
      }
    } catch { /* sessionStorage may be disabled — fail safe */ }
  }

  return (
    <li
      className="rounded-lg border border-zinc-200 bg-white"
      data-testid="explore-result"
      data-kind="entity"
    >
      <button
        type="button"
        onClick={toggle}
        className="w-full text-left px-4 py-3 hover:bg-zinc-50 cursor-pointer"
        data-testid="explore-entity-toggle"
      >
        <div className="flex items-center gap-2">
          <ChevronRight
            className={`w-3.5 h-3.5 text-zinc-400 transition-transform ${open ? "rotate-90" : ""}`}
          />
          <span className="text-[10px] mono px-1.5 py-0.5 rounded bg-zinc-100 text-zinc-700">
            {profile?.entity_type ?? hit.subtitle ?? "ENTITY"}
          </span>
          <span className="text-sm font-medium text-zinc-900">
            {hit.title}
          </span>
          <span className="text-[11px] text-zinc-400 mono ml-auto">
            {firstSeen && <>first seen {firstSeen.slice(0, 7)}</>}
            {nDocs > 0 && firstSeen ? " · " : ""}
            {nDocs > 0 && `${nDocs} docs`}
          </span>
        </div>

        {profile?.summary && (
          <div className="mt-2 ml-6 text-xs text-zinc-500 leading-relaxed">
            {profile.summary}
          </div>
        )}
      </button>

      {open && (
        <div className="px-4 pb-4 ml-6 border-t border-zinc-100">
          <div className="rounded-lg border border-zinc-100 bg-zinc-50/40 p-3 mt-2">
            <div className="text-[10px] uppercase tracking-wider text-zinc-400 mb-2">
              Related
            </div>
            {profileLoading ? (
              <div className="flex items-center justify-center py-6 text-zinc-400">
                <Loader2 className="w-4 h-4 animate-spin" />
              </div>
            ) : profile === null ? (
              <div className="text-xs text-zinc-500 py-2">
                Failed to load entity profile.
              </div>
            ) : profile.related.length === 0 ? (
              <div className="text-xs text-zinc-500 py-2">
                No related items found.
              </div>
            ) : (
              <ul>
                {profile.related.map((b) => {
                  const Icon = BUCKET_ICONS[b.icon] ?? FileText;
                  return (
                    <li
                      key={b.key}
                      className="border-b border-zinc-200 last:border-0"
                      data-testid="entity-related-bucket"
                      data-bucket-key={b.key}
                    >
                      <div className="flex items-center gap-2 py-2 text-xs text-zinc-700">
                        <Icon className="w-3.5 h-3.5 text-zinc-500" strokeWidth={1.75} />
                        <span className="font-medium">{b.label}</span>
                        {b.subtitle && <span className="text-zinc-500">— {b.subtitle}</span>}
                        <button
                          type="button"
                          onClick={(e) => { e.stopPropagation(); onScopeToBucket(b); }}
                          className="ml-auto text-zinc-500 hover:text-zinc-900 mono cursor-pointer"
                          data-testid="entity-bucket-view-all"
                        >
                          view all <span aria-hidden>→</span>
                        </button>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-x-6 gap-y-2 mt-3 text-[11px]">
            <div>
              <div className="text-[10px] uppercase tracking-wider text-zinc-400 mb-0.5">
                Canonical name
              </div>
              <div className="text-zinc-900">{hit.title}</div>
            </div>
            {aliases.length > 0 && (
              <div>
                <div className="text-[10px] uppercase tracking-wider text-zinc-400 mb-0.5">
                  Aliases
                </div>
                <div className="text-zinc-700 mono">{aliases.join(" · ")}</div>
              </div>
            )}
            {(firstSeen || lastSeen) && (
              <div>
                <div className="text-[10px] uppercase tracking-wider text-zinc-400 mb-0.5">
                  First / last mention
                </div>
                <div className="text-zinc-700 mono">
                  {firstSeen ?? "—"} → {lastSeen ?? "—"}
                </div>
              </div>
            )}
          </div>

          <div className="mt-3 pt-3 border-t border-zinc-100 flex items-center gap-3 text-[11px] text-zinc-500">
            <a
              href={`/explore/entity/${hit.id}`}
              className="flex items-center gap-1 text-zinc-600 hover:text-zinc-900"
              data-testid="entity-open-profile"
            >
              <ExternalLink className="w-3 h-3" /> Open profile
            </a>
            <span className="ml-auto text-[10px] mono text-zinc-400">
              {mentionCount > 0 ? `${mentionCount} mentions · ` : ""}
              {hit.id.slice(0, 8)}…
            </span>
          </div>
        </div>
      )}
    </li>
  );
}
