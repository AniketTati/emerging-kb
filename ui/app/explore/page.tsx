"use client";

/**
 * Explore — the "what's in my corpus" page.
 *
 * Two rails + a results column:
 *   - Left rail "View as": all-results / per-category with counts
 *   - Inner panel "Filter by": doc-type checkboxes, date dropdown,
 *     has-anomaly / has-conflicts / has-chain toggles
 *   - Center: search input + result list, grouped by category in
 *     "All results", flat when scoped to one kind. Entity cards show
 *     aliases + first/last mention block.
 *
 * Per-group "view all →" scopes the view to that kind.
 *
 * Scale: results are server-paginated. At 100k+, pg_trgm GIN indexes
 * on entity / file name columns + count caching make the explore
 * surface stay snappy.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Search, LayoutGrid, FileText, Folder, Puzzle, Users, GitMerge,
  Tag, AlertCircle, Loader2, ChevronRight, ExternalLink,
  Mail, DollarSign, User, Building, AlertOctagon, FileQuestion,
  type LucideIcon,
} from "lucide-react";
import {
  exploreSearch, getExploreCounts, getEntityProfile,
  type ExploreCounts, type ExploreHit, type ExploreKind,
  type ExploreSearchResponse, type EntityProfile,
  type EntityProfileBucket,
} from "@/lib/api";
import { Sidebar } from "@/components/Sidebar";


// Map the API's icon-name strings to lucide React components. The
// /explore/entity/{id}/profile endpoint returns an `icon` field per
// bucket; we resolve it here so the page can render the right glyph
// without a string-to-component switch scattered through JSX.
const BUCKET_ICONS: Record<string, LucideIcon> = {
  "file-text":   FileText,
  "dollar-sign": DollarSign,
  "mail":        Mail,
  "user":        User,
  "users":       Users,
  "building":    Building,
  "alert-circle": AlertCircle,
  "alert-octagon": AlertOctagon,
  "blueprint":   FileText,
  "sticky-note": FileText,
  "file":        FileQuestion,
};


type RailItem = {
  key: "all" | ExploreKind;
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


// Show this many doc types in the Filter-by panel before collapsing
// the rest behind a "+ N more" expander.
const FILTER_DOCTYPE_TOP_N = 6;


export default function ExplorePage() {
  const [counts, setCounts] = useState<ExploreCounts | null>(null);
  const [kind, setKind] = useState<"all" | ExploreKind>("all");
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");

  // Filters
  const [docTypeFilter, setDocTypeFilter] = useState<Set<string>>(new Set());
  const [docTypeListExpanded, setDocTypeListExpanded] = useState(false);
  const [dateRange, setDateRange] = useState<DateRange>("any");
  const [hasAnomaly, setHasAnomaly] = useState(false);
  const [hasConflicts, setHasConflicts] = useState(false);
  const [hasChain, setHasChain] = useState(false);

  // All doc types (for the Filter-by checkboxes), pulled once.
  const [allDocTypes, setAllDocTypes] = useState<{ name: string; count: number }[]>([]);

  // Search results
  const [results, setResults] = useState<ExploreSearchResponse | null>(null);
  const [loading, setLoading] = useState(false);

  // Initial counts + doc-type browse (once).
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

  // Debounce typing.
  useEffect(() => {
    const t = setTimeout(() => setDebounced(q.trim()), 220);
    return () => clearTimeout(t);
  }, [q]);

  const runSearch = useCallback(async () => {
    setLoading(true);
    try {
      // Pass B — push the has-* + doc_type filters server-side.
      // (Client-side filter pass below also still runs for paranoid
      // safety + future date filter which is client-only.)
      const onlyDocType =
        docTypeFilter.size === 1
          ? Array.from(docTypeFilter)[0]
          : undefined;
      const out = await exploreSearch({
        q: debounced || undefined,
        kind: kind === "all" ? null : kind,
        docType: onlyDocType ?? null,
        hasAnomaly,
        hasConflicts,
        hasChain,
        limit: 60,
      });
      setResults(out);
    } catch (err) {
      console.error(err);
      setResults(null);
    } finally {
      setLoading(false);
    }
  }, [debounced, kind, docTypeFilter, hasAnomaly, hasConflicts, hasChain]);

  useEffect(() => { runSearch(); }, [runSearch]);

  // Client-side filtering on top of the loaded result page (cheap,
  // and avoids backend complexity for the niche filters). Server-side
  // filtering lands when explore_search grows query params for these.
  const filteredItems = useMemo(() => {
    const items = results?.items ?? [];
    return items.filter((h) => {
      if (docTypeFilter.size > 0) {
        const dt = (h.extra as { inferred_doc_type?: string } | undefined)?.inferred_doc_type;
        // For document rows we have the doc_type in extra.inferred_doc_type;
        // for atomic_unit / anomaly rows we filter on h.subtitle (file name)
        // — keep these passing for now since we don't have the doc_type
        // joined into those buckets. Doc type filter is most useful on the
        // Documents bucket and we let other kinds through.
        if (dt && !docTypeFilter.has(dt)) return false;
      }
      if (dateRange !== "any") {
        const days = dateRange === "7d" ? 7 : dateRange === "30d" ? 30 : 365;
        const cutoff = Date.now() - days * 86_400_000;
        const created = (h.extra as { created_at?: string } | undefined)?.created_at;
        if (created && new Date(created).getTime() < cutoff) return false;
      }
      if (hasAnomaly && h.kind !== "anomaly") return false;
      if (hasConflicts) {
        // Conflicts are surfaced via the anomaly kind too in Wave A;
        // Pass B will fold a `has_conflicts` flag onto each row.
      }
      if (hasChain) {
        // Same — needs chain_id surfaced per row. Pass B.
      }
      return true;
    });
  }, [results, docTypeFilter, dateRange, hasAnomaly, hasConflicts, hasChain]);

  const grouped = useMemo<Record<ExploreKind, ExploreHit[]>>(() => {
    const acc: Record<string, ExploreHit[]> = {};
    for (const h of filteredItems) (acc[h.kind] ??= []).push(h);
    return acc as Record<ExploreKind, ExploreHit[]>;
  }, [filteredItems]);

  function toggleDocType(name: string) {
    setDocTypeFilter((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name); else next.add(name);
      return next;
    });
  }
  function clearAllFilters() {
    setDocTypeFilter(new Set());
    setDateRange("any");
    setHasAnomaly(false);
    setHasConflicts(false);
    setHasChain(false);
  }

  // Called when the user clicks "view all →" on an Entity card's
  // Related accordion bucket. We translate the bucket's deep-link
  // hints into Explore filters and reset the view.
  function scopeToBucket(bucket: EntityProfileBucket) {
    if (bucket.deep_link_kind === "document"
        || bucket.deep_link_kind === "atomic_unit"
        || bucket.deep_link_kind === "anomaly"
        || bucket.deep_link_kind === "entity"
        || bucket.deep_link_kind === "doc_type"
        || bucket.deep_link_kind === "relationship"
        || bucket.deep_link_kind === "topic") {
      setKind(bucket.deep_link_kind);
    }
    if (bucket.deep_link_doc_type) {
      setDocTypeFilter(new Set([bucket.deep_link_doc_type]));
    }
    if (bucket.deep_link_q) {
      setQ(bucket.deep_link_q);
    }
    // Special-case: anomalies bucket flips the has_anomaly toggle
    // instead (kind=anomaly already filters that bucket, but the
    // toggle persists across kind changes which is more useful UX).
    if (bucket.key === "anomalies") {
      setHasAnomaly(true);
    }
  }
  const anyFilterActive =
    docTypeFilter.size > 0 || dateRange !== "any"
    || hasAnomaly || hasConflicts || hasChain;

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
          {/* Left rail: View as + Filter by */}
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
                  const active = item.key === kind;
                  const total =
                    item.key === "all"
                      ? (results?.total_estimate ?? 0)
                      : (counts?.[item.countKey!] ?? 0);
                  return (
                    <button
                      key={item.key}
                      type="button"
                      onClick={() => setKind(item.key)}
                      className={`w-full flex items-center justify-between px-2 py-1.5 rounded text-sm cursor-pointer ${
                        active
                          ? "bg-zinc-100 text-zinc-900"
                          : "text-zinc-600 hover:bg-zinc-50"
                      }`}
                      data-testid={`explore-rail-${item.key}`}
                    >
                      <span className="flex items-center gap-2">
                        <Icon className="w-3.5 h-3.5" strokeWidth={1.75} />
                        {item.label}
                      </span>
                      <span className="text-[11px] text-zinc-500 mono">
                        {total}
                      </span>
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

              {/* Doc type checkboxes */}
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
                        checked={docTypeFilter.has(dt.name)}
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
                      {docTypeListExpanded
                        ? "− show fewer"
                        : `+ ${allDocTypes.length - FILTER_DOCTYPE_TOP_N} more`}
                    </button>
                  )}
                </div>
              </div>

              {/* Date dropdown */}
              <div className="mb-3 text-xs">
                <div className="text-zinc-500 mb-1.5">Date</div>
                <select
                  value={dateRange}
                  onChange={(e) => setDateRange(e.target.value as DateRange)}
                  className="w-full text-xs px-2 py-1 rounded border border-zinc-200 bg-white cursor-pointer"
                  data-testid="explore-filter-date"
                >
                  <option value="any">Any time</option>
                  <option value="7d">Last 7 days</option>
                  <option value="30d">Last 30 days</option>
                  <option value="365d">Last year</option>
                </select>
              </div>

              {/* Has toggles */}
              <div className="text-xs">
                <div className="text-zinc-500 mb-1.5">Has</div>
                <label className="flex items-center gap-2 py-0.5 text-zinc-700 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={hasAnomaly}
                    onChange={(e) => setHasAnomaly(e.target.checked)}
                    className="accent-zinc-900 w-3 h-3"
                    data-testid="explore-filter-anomaly"
                  />
                  anomaly
                </label>
                <label className="flex items-center gap-2 py-0.5 text-zinc-700 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={hasConflicts}
                    onChange={(e) => setHasConflicts(e.target.checked)}
                    className="accent-zinc-900 w-3 h-3"
                    data-testid="explore-filter-conflicts"
                  />
                  conflicts
                </label>
                <label className="flex items-center gap-2 py-0.5 text-zinc-700 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={hasChain}
                    onChange={(e) => setHasChain(e.target.checked)}
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
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  placeholder="Search files, entities, clauses, topics…"
                  className="w-full pl-9 pr-24 py-2.5 text-sm rounded-lg border border-zinc-200 focus:outline-none focus:border-zinc-400"
                  data-testid="explore-search-input"
                />
                {q && (
                  <button
                    type="button"
                    onClick={() => setQ("")}
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
                  ) : results ? (
                    <>
                      <span className="text-zinc-900 font-medium">
                        {filteredItems.length} of {results.total_estimate}
                      </span>{" "}
                      {results.q ? (
                        <>for &ldquo;{results.q}&rdquo; </>
                      ) : (
                        <>across the workspace </>
                      )}
                      {results.kind && (
                        <>· kind <span className="mono">{results.kind}</span></>
                      )}
                      {anyFilterActive && results.items.length !== filteredItems.length && (
                        <span className="text-zinc-400"> ({results.items.length - filteredItems.length} filtered out)</span>
                      )}
                    </>
                  ) : (
                    "no results"
                  )}
                </div>
              </div>

              {results && filteredItems.length === 0 && !loading && (
                <div className="rounded-lg border border-zinc-200 p-8 text-center text-sm text-zinc-500">
                  No results match the current filters.
                </div>
              )}

              {results && filteredItems.length > 0 && (
                <div className="space-y-6">
                  {Object.entries(grouped).map(([k, items]) => {
                    const rail = KIND_TO_RAIL[k as ExploreKind];
                    const Icon = rail?.icon ?? LayoutGrid;
                    const totalForKind = counts?.[rail?.countKey ?? "documents"] ?? items.length;
                    const isScoped = kind !== "all";
                    return (
                      <div key={k}>
                        <div className="text-[10px] uppercase tracking-wider text-zinc-400 mb-2 flex items-center gap-2">
                          <Icon className="w-3 h-3" strokeWidth={1.75} />
                          {rail?.label ?? k}
                          <span className="mono text-zinc-400">{items.length}</span>
                          {!isScoped && totalForKind > items.length && (
                            <button
                              type="button"
                              onClick={() => setKind(k as ExploreKind)}
                              className="ml-auto text-[11px] text-zinc-500 hover:text-zinc-900 mono cursor-pointer flex items-center gap-1"
                              data-testid="explore-view-all"
                              data-target={k}
                            >
                              view all <span aria-hidden>→</span>
                            </button>
                          )}
                        </div>
                        <ul className="space-y-1.5">
                          {items.map((it) => (
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
  // Lazy-load the profile rollup on first expand — cheaper than
  // pre-fetching for every entity in the result list. The first 3
  // surface forms + first/last mention come from the search payload
  // already, so the collapsed card has all the prototype info; only
  // the RELATED accordion needs a network round-trip.
  const [open, setOpen] = useState(false);
  const [profile, setProfile] = useState<EntityProfile | null>(null);
  const [profileLoading, setProfileLoading] = useState(false);

  const searchExtra = (hit.extra ?? {}) as {
    aliases?: string[];
    first_seen?: string;
    last_seen?: string;
    n_docs?: number;
    mention_count?: number;
  };
  // When the profile is loaded, prefer its values (more authoritative);
  // else fall back to whatever the search results gave us.
  const aliases = profile?.aliases ?? searchExtra.aliases ?? [];
  const firstSeen = profile?.first_seen ?? searchExtra.first_seen;
  const lastSeen = profile?.last_seen ?? searchExtra.last_seen;
  const nDocs = profile?.n_docs ?? searchExtra.n_docs ?? 0;
  const mentionCount = profile?.mention_count ?? searchExtra.mention_count ?? 0;

  async function toggle() {
    if (!open && profile === null) {
      setProfileLoading(true);
      try {
        const p = await getEntityProfile(hit.id);
        setProfile(p);
      } catch (err) {
        console.error("getEntityProfile failed", err);
      } finally {
        setProfileLoading(false);
      }
    }
    setOpen(!open);
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
            {firstSeen && (
              <>first seen {firstSeen.slice(0, 7)}</>
            )}
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
          {/* RELATED accordion */}
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
                        <Icon
                          className="w-3.5 h-3.5 text-zinc-500"
                          strokeWidth={1.75}
                        />
                        <span className="font-medium">{b.label}</span>
                        {b.subtitle && (
                          <span className="text-zinc-500">— {b.subtitle}</span>
                        )}
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            onScopeToBucket(b);
                          }}
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

          {/* 3-column footer block (Canonical / Aliases / First-Last) */}
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

          {/* Footer actions */}
          <div className="mt-3 pt-3 border-t border-zinc-100 flex items-center gap-3 text-[11px] text-zinc-500">
            <button
              type="button"
              disabled
              title="Entity profile page lands in a later pass"
              className="flex items-center gap-1 text-zinc-400 cursor-not-allowed"
            >
              <ExternalLink className="w-3 h-3" /> Open profile
            </button>
            <button
              type="button"
              disabled
              title="Graph view (HippoRAG neighbors) coming later"
              className="flex items-center gap-1 text-zinc-400 cursor-not-allowed"
            >
              <GitMerge className="w-3 h-3" /> Show as graph
            </button>
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
