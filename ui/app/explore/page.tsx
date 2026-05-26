"use client";

/**
 * Explore — the "what's in my corpus" page.
 *
 * Left rail: category badges with per-category counts. Click to scope
 * the search to that kind.
 * Center: search input + result list, grouped by category (or single-
 * category when scoped). Empty query browses all kinds.
 *
 * Scale: results are paginated server-side (offset+limit). At demo scale
 * (~10k rows) one round-trip suffices; at 100k we'd add "load more" +
 * trigram indexes on the ILIKE columns (see api/explore.py header).
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Search, LayoutGrid, FileText, Folder, Puzzle, Users, GitMerge,
  Tag, AlertCircle, Loader2,
  type LucideIcon,
} from "lucide-react";
import {
  exploreSearch, getExploreCounts,
  type ExploreCounts, type ExploreHit, type ExploreKind,
  type ExploreSearchResponse,
} from "@/lib/api";
import { Sidebar } from "@/components/Sidebar";


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


export default function ExplorePage() {
  const [counts, setCounts] = useState<ExploreCounts | null>(null);
  const [kind, setKind] = useState<"all" | ExploreKind>("all");
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [results, setResults] = useState<ExploreSearchResponse | null>(null);
  const [loading, setLoading] = useState(false);

  // Initial counts load.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const c = await getExploreCounts();
        if (!cancelled) setCounts(c);
      } catch (err) {
        console.error("getExploreCounts failed", err);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // Debounce typing so we don't fire a request every keystroke.
  useEffect(() => {
    const t = setTimeout(() => setDebounced(q.trim()), 220);
    return () => clearTimeout(t);
  }, [q]);

  const runSearch = useCallback(async () => {
    setLoading(true);
    try {
      const out = await exploreSearch({
        q: debounced || undefined,
        kind: kind === "all" ? null : kind,
        limit: 60,
      });
      setResults(out);
    } catch (err) {
      console.error("exploreSearch failed", err);
      setResults(null);
    } finally {
      setLoading(false);
    }
  }, [debounced, kind]);

  useEffect(() => {
    runSearch();
  }, [runSearch]);

  // Group results by kind so the "all results" view shows nice
  // category buckets.
  const grouped = useMemo<Record<ExploreKind, ExploreHit[]>>(() => {
    const acc: Record<string, ExploreHit[]> = {};
    for (const h of results?.items ?? []) {
      (acc[h.kind] ??= []).push(h);
    }
    return acc as Record<ExploreKind, ExploreHit[]>;
  }, [results]);

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
          {/* Left rail: View as */}
          <aside
            className="w-[220px] flex-shrink-0 border-r border-zinc-200 overflow-y-auto bg-white"
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
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-[11px] text-zinc-500 hover:text-zinc-900 mono"
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
                        {results.items.length} of {results.total_estimate}
                      </span>{" "}
                      {results.q ? (
                        <>for &ldquo;{results.q}&rdquo; </>
                      ) : (
                        <>across the workspace </>
                      )}
                      {results.kind && (
                        <>· kind <span className="mono">{results.kind}</span></>
                      )}
                    </>
                  ) : (
                    "no results"
                  )}
                </div>
              </div>

              {results && results.items.length === 0 && !loading && (
                <div className="rounded-lg border border-zinc-200 p-8 text-center text-sm text-zinc-500">
                  No results.
                </div>
              )}

              {results && results.items.length > 0 && (
                <div className="space-y-6">
                  {Object.entries(grouped).map(([k, items]) => {
                    const rail = KIND_TO_RAIL[k as ExploreKind];
                    const Icon = rail?.icon ?? LayoutGrid;
                    return (
                      <div key={k}>
                        <div className="text-[10px] uppercase tracking-wider text-zinc-400 mb-2 flex items-center gap-2">
                          <Icon className="w-3 h-3" strokeWidth={1.75} />
                          {rail?.label ?? k}
                          <span className="mono text-zinc-400">{items.length}</span>
                        </div>
                        <ul className="space-y-1.5">
                          {items.map((it) => (
                            <li
                              key={`${it.kind}-${it.id}`}
                              className="rounded-lg border border-zinc-200 px-4 py-3 hover:bg-zinc-50"
                              data-testid="explore-result"
                              data-kind={it.kind}
                            >
                              <div className="flex items-center gap-2">
                                <span className="text-[10px] mono px-1.5 py-0.5 rounded bg-zinc-100 text-zinc-700">
                                  {it.kind}
                                </span>
                                <span className="text-sm font-medium text-zinc-900 truncate">
                                  {it.title}
                                </span>
                                {it.subtitle && (
                                  <span className="text-[11px] text-zinc-500 mono ml-auto truncate">
                                    {it.subtitle}
                                  </span>
                                )}
                              </div>
                              {it.snippet && (
                                <div className="mt-1.5 text-xs text-zinc-500 leading-relaxed line-clamp-2">
                                  {it.snippet}
                                </div>
                              )}
                            </li>
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
