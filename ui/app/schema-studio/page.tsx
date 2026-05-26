"use client";

/**
 * Schema Studio — the workspace's data-model surface.
 *
 * 6 tabs: Typed · Inferred · Collisions · Vocabulary · Lineage · Versions.
 *
 * The Inferred tab matches the prototype layout (prototype/schema-studio.html):
 *   - DOC TYPES rail on the left with per-doc-type counts (n_inferred · n_ready)
 *     and "new" badges for types we haven't seen before today.
 *   - Auto-promote thresholds card below the rail (prevalence ≥ 0.80,
 *     stability ≥ 0.90, vt-conf ≥ 0.90, min docs).
 *   - Header strip: counts in tab labels (Typed 47 · Inferred 12 · …),
 *     subtitle ("X fields emerging across Y docs · Z ready to promote ·
 *     N promoted in the last 5 min"), Export YAML.
 *   - Row content: status dot (typed-promoted / approaching / emerging),
 *     a progress bar with a threshold-marker on the right end,
 *     expandable detail with THRESHOLDS · SAMPLE VALUES · INFERRED TYPE ·
 *     FIRST PROPOSED, action buttons (Promote now · Rename · Discard).
 *
 * Scale: client-paginates fields at 1000 per fetch. Per-tab fetches keep
 * initial render cheap. Vocabulary + Lineage lazy-load on tab activation.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Layers, GitBranch, AlertOctagon, BookOpen, Network, History,
  Loader2, Download, Filter as FilterIcon, Pencil, Trash2,
  Sparkles, ChevronRight,
} from "lucide-react";
import {
  listSchemas, listSchemaEntities, listSchemaEntityFields,
  listInferredFields, listVocabulary, listDocChains, listSchemaVersions,
  promoteInferredField, renameInferredField, discardInferredField,
  getInferredFieldSampleValues, downloadSchemaExportYaml,
  type SchemaSummary, type SchemaEntity, type SchemaField,
  type InferredField, type VocabEntry, type DocChainSummary,
  type SchemaVersionRow,
  type InferredFieldSampleValue,
} from "@/lib/api";
import { Sidebar } from "@/components/Sidebar";


// ---------------------------------------------------------------------------
// Auto-promote thresholds (kept in sync with kb.extraction.promotion
// defaults). Surfacing them in the UI gives curators a visible answer to
// "what does the system need to see before it promotes this on its own?".
// ---------------------------------------------------------------------------

const THRESHOLDS = {
  prevalence: 0.80,
  stability:  0.90,
  vt_conf:    0.90,
  min_docs_prod: 20,
  min_docs_demo: 5,
};


type TabKey = "typed" | "inferred" | "collisions" | "vocabulary" | "lineage" | "versions";


function getWorkspaceId(): string {
  if (typeof window === "undefined") return "00000000-0000-0000-0000-000000000001";
  return (
    (window as unknown as { __KB_WORKSPACE__?: string }).__KB_WORKSPACE__
    ?? "00000000-0000-0000-0000-000000000001"
  );
}


// Status the row sits in given its prevalence + promotion state. Drives
// the colored dot + the inline badge. "approaching" = within 0.05 of the
// promotion threshold; tweak as needed.
type RowStatus = "typed-promoted" | "approaching" | "emerging";

function statusOf(f: InferredField): RowStatus {
  if (f.is_promoted) return "typed-promoted";
  const distance = THRESHOLDS.prevalence - f.prevalence;
  if (distance <= 0.05) return "approaching";
  return "emerging";
}


export default function SchemaStudioPage() {
  const [tab, setTab] = useState<TabKey>("inferred");
  // Aggregate counts for the tab strip header. Loaded once.
  const [allInferred, setAllInferred] = useState<InferredField[]>([]);
  const [typedSchemas, setTypedSchemas] = useState<SchemaSummary[]>([]);
  const [vocabCount, setVocabCount] = useState(0);
  const [chainCount, setChainCount] = useState(0);
  const [topbarLoading, setTopbarLoading] = useState(true);

  const refreshAggregates = useCallback(async () => {
    setTopbarLoading(true);
    try {
      const [inferred, schemas, vocab, chains] = await Promise.all([
        listInferredFields({ limit: 1000 }),
        listSchemas(),
        listVocabulary(`workspace:${getWorkspaceId()}`, 500),
        listDocChains(200),
      ]);
      setAllInferred(inferred);
      setTypedSchemas(schemas);
      setVocabCount(vocab.length);
      setChainCount(chains.length);
    } catch (err) {
      console.error(err);
    } finally {
      setTopbarLoading(false);
    }
  }, []);

  useEffect(() => { refreshAggregates(); }, [refreshAggregates]);

  // Tab counts shown in the strip (prototype showed
  // "Typed 47 · Inferred 12 · Collisions 3 · Vocabulary 847").
  const counts = useMemo(() => ({
    typed: typedSchemas.length,
    inferred: allInferred.filter((f) => !f.is_promoted).length,
    collisions: 0,  // Wave B
    vocabulary: vocabCount,
    lineage: chainCount,
    versions: typedSchemas.length, // one version row per schema picker entry
  }), [typedSchemas.length, allInferred, vocabCount, chainCount]);

  // Subtitle stats (prototype: "12 fields emerging across 24 contracts · 3 ready to promote · 1 promoted in the last 5 minutes")
  const subtitleStats = useMemo(() => {
    const emerging = allInferred.filter((f) => !f.is_promoted).length;
    const distinctDocTypes = new Set(allInferred.map((f) => f.inferred_doc_type)).size;
    const ready = allInferred.filter(
      (f) => !f.is_promoted
        && f.prevalence >= THRESHOLDS.prevalence
        && f.stability >= THRESHOLDS.stability,
    ).length;
    const fiveMinAgo = Date.now() - 5 * 60_000;
    const promotedRecent = allInferred.filter(
      (f) => f.is_promoted && f.created_at
        && new Date(f.created_at).getTime() >= fiveMinAgo,
    ).length;
    return { emerging, distinctDocTypes, ready, promotedRecent };
  }, [allInferred]);

  const TABS: { key: TabKey; label: string; icon: typeof Layers; count: number }[] = [
    { key: "typed",      label: "Typed",      icon: Layers,        count: counts.typed },
    { key: "inferred",   label: "Inferred",   icon: GitBranch,     count: counts.inferred },
    { key: "collisions", label: "Collisions", icon: AlertOctagon,  count: counts.collisions },
    { key: "vocabulary", label: "Vocabulary", icon: BookOpen,      count: counts.vocabulary },
    { key: "lineage",    label: "Lineage",    icon: Network,       count: counts.lineage },
    { key: "versions",   label: "Versions",   icon: History,       count: counts.versions },
  ];

  return (
    <div className="flex h-full">
      <Sidebar current="schema" />

      <main className="flex-1 flex flex-col min-w-0 bg-white">
        {/* Header strip — prototype: Studio › Schema · v1.4.2 · auto-saved · counts · Export YAML */}
        <header className="h-12 flex-shrink-0 border-b border-zinc-200 flex items-center px-5 gap-4">
          <span className="text-sm text-zinc-500">Studio</span>
          <ChevronRight className="w-3 h-3 text-zinc-300" />
          <span className="text-sm font-medium text-zinc-900">Schema</span>
          {!topbarLoading && (
            <span className="text-[11px] text-zinc-400 mono">
              {counts.typed} typed · {counts.inferred} inferred ·{" "}
              {counts.collisions} collisions
            </span>
          )}
          <button
            type="button"
            className="ml-auto flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-md text-zinc-600 hover:text-zinc-900 hover:bg-zinc-100 cursor-pointer"
            data-testid="schema-export-yaml"
            onClick={() => {
              downloadSchemaExportYaml().catch((err) => {
                console.error("export.yaml failed", err);
                alert("Failed to export YAML — see console.");
              });
            }}
            title="Download all active typed schemas as a YAML file"
          >
            <Download className="w-3.5 h-3.5" strokeWidth={1.75} />
            Export YAML
          </button>
        </header>

        {/* Tab strip with counts */}
        <div className="border-b border-zinc-200 px-6 flex items-end gap-6 text-sm">
          {TABS.map((t) => {
            const Icon = t.icon;
            const active = t.key === tab;
            return (
              <button
                key={t.key}
                type="button"
                onClick={() => setTab(t.key)}
                className={`py-2.5 flex items-center gap-2 cursor-pointer border-b-2 -mb-px ${
                  active
                    ? "text-zinc-900 font-medium border-zinc-900"
                    : "text-zinc-500 hover:text-zinc-900 border-transparent"
                }`}
                data-testid={`schema-tab-${t.key}`}
              >
                <Icon className="w-3.5 h-3.5" strokeWidth={1.75} />
                {t.label}
                <span className={`text-[11px] mono ${active ? "text-zinc-400" : "text-zinc-400"}`}>
                  {t.count}
                </span>
              </button>
            );
          })}
        </div>

        <div className="flex-1 overflow-hidden bg-zinc-50/40">
          {tab === "typed"      && <TypedTab schemas={typedSchemas} />}
          {tab === "inferred"   && (
            <InferredTabRich
              fields={allInferred}
              subtitle={subtitleStats}
              onMutated={refreshAggregates}
            />
          )}
          {tab === "collisions" && <CollisionsTab />}
          {tab === "vocabulary" && <VocabularyTab />}
          {tab === "lineage"    && <LineageTab />}
          {tab === "versions"   && <VersionsTab schemas={typedSchemas} />}
        </div>
      </main>
    </div>
  );
}


// ===========================================================================
// Tab: Inferred — the rich prototype layout
// ===========================================================================


function InferredTabRich({
  fields, subtitle, onMutated,
}: {
  fields: InferredField[];
  subtitle: { emerging: number; distinctDocTypes: number; ready: number; promotedRecent: number };
  onMutated: () => Promise<void>;
}) {
  const [selectedDocType, setSelectedDocType] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<"all" | RowStatus>("all");
  const [sortKey, setSortKey] = useState<"prevalence" | "stability" | "docs" | "name">("prevalence");

  // Per-doc-type counts for the rail. "new" badge if the type has any
  // row created within the last 24h.
  const byDocType = useMemo(() => {
    const m: Record<string, { all: number; ready: number; newish: boolean }> = {};
    const day = Date.now() - 24 * 60 * 60_000;
    for (const f of fields) {
      const k = f.inferred_doc_type;
      const slot = m[k] ?? { all: 0, ready: 0, newish: false };
      slot.all += 1;
      if (!f.is_promoted
          && f.prevalence >= THRESHOLDS.prevalence
          && f.stability >= THRESHOLDS.stability) {
        slot.ready += 1;
      }
      if (f.created_at && new Date(f.created_at).getTime() >= day) {
        slot.newish = true;
      }
      m[k] = slot;
    }
    return m;
  }, [fields]);

  const docTypesSorted = useMemo(
    () => Object.entries(byDocType)
      .sort((a, b) => b[1].all - a[1].all)
      .map(([k, v]) => ({ doc_type: k, ...v })),
    [byDocType],
  );

  // Auto-select the doc_type with most rows when first loaded.
  useEffect(() => {
    if (selectedDocType === null && docTypesSorted.length > 0) {
      setSelectedDocType(docTypesSorted[0].doc_type);
    }
  }, [docTypesSorted, selectedDocType]);

  // Filtered + sorted rows for the right pane.
  const filteredRows = useMemo(() => {
    let rows = fields.filter((f) => f.inferred_doc_type === selectedDocType);
    if (statusFilter !== "all") {
      rows = rows.filter((f) => statusOf(f) === statusFilter);
    }
    rows = [...rows];
    rows.sort((a, b) => {
      if (sortKey === "prevalence") return b.prevalence - a.prevalence;
      if (sortKey === "stability") return b.stability - a.stability;
      if (sortKey === "docs") return b.n_docs_observed - a.n_docs_observed;
      return a.canonical_name.localeCompare(b.canonical_name);
    });
    return rows;
  }, [fields, selectedDocType, statusFilter, sortKey]);

  return (
    <div className="flex h-full overflow-hidden">
      {/* Inner left rail — DOC TYPES + thresholds */}
      <aside
        className="w-[260px] flex-shrink-0 border-r border-zinc-200 bg-white overflow-y-auto"
        data-testid="schema-doctypes-rail"
      >
        <div className="p-4 border-b border-zinc-200">
          <div className="text-[10px] uppercase tracking-wider text-zinc-400 mb-2">
            Doc types
          </div>
          <ul className="space-y-px">
            {docTypesSorted.map((d) => {
              const active = d.doc_type === selectedDocType;
              return (
                <li key={d.doc_type}>
                  <button
                    type="button"
                    onClick={() => setSelectedDocType(d.doc_type)}
                    className={`w-full flex items-center gap-2 px-2 py-1.5 rounded text-xs cursor-pointer ${
                      active
                        ? "bg-zinc-100 text-zinc-900"
                        : "text-zinc-700 hover:bg-zinc-50"
                    }`}
                    data-testid="schema-doctype-row"
                    data-doctype={d.doc_type}
                  >
                    <span className="font-medium truncate flex-1 text-left mono">
                      {d.doc_type}
                    </span>
                    {d.newish && (
                      <span className="text-[9px] mono px-1 py-0.5 rounded bg-blue-100 text-blue-800">
                        new
                      </span>
                    )}
                    <span className="text-[10px] mono text-zinc-500 tabular-nums">
                      {d.all} · {d.ready}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
          <div className="text-[10px] text-zinc-400 mt-2 leading-relaxed">
            <span className="mono">N total</span> ·{" "}
            <span className="mono">N ready-to-promote</span>
          </div>
        </div>

        {/* Auto-promote thresholds card */}
        <div className="p-4">
          <div className="text-[10px] uppercase tracking-wider text-zinc-400 mb-2 flex items-center gap-1.5">
            <Sparkles className="w-3 h-3" strokeWidth={1.75} />
            Auto-promote thresholds
          </div>
          <ul className="space-y-1 text-xs text-zinc-600 mono">
            <li>prevalence ≥ <span className="text-zinc-900">{THRESHOLDS.prevalence.toFixed(2)}</span></li>
            <li>stability ≥ <span className="text-zinc-900">{THRESHOLDS.stability.toFixed(2)}</span></li>
            <li>vt-conf ≥ <span className="text-zinc-900">{THRESHOLDS.vt_conf.toFixed(2)}</span></li>
            <li>min docs = <span className="text-zinc-900">{THRESHOLDS.min_docs_prod}</span> (prod) / <span className="text-zinc-900">{THRESHOLDS.min_docs_demo}</span> (demo)</li>
          </ul>
        </div>
      </aside>

      {/* Right pane — header + rows */}
      <section className="flex-1 overflow-y-auto">
        <div className="max-w-4xl mx-auto px-8 py-6">
          <div className="flex items-start justify-between mb-1">
            <h1 className="text-lg font-semibold text-zinc-900">
              Inferred fields {selectedDocType && (
                <>
                  · <span className="mono">{selectedDocType}</span>
                </>
              )}
            </h1>
            <div className="flex items-center gap-2">
              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value as "all" | RowStatus)}
                className="text-xs px-2 py-1 rounded border border-zinc-200 bg-white cursor-pointer"
                data-testid="schema-filter-status"
              >
                <option value="all">All statuses</option>
                <option value="typed-promoted">Typed (promoted)</option>
                <option value="approaching">Approaching</option>
                <option value="emerging">Emerging</option>
              </select>
              <select
                value={sortKey}
                onChange={(e) => setSortKey(e.target.value as typeof sortKey)}
                className="text-xs px-2 py-1 rounded border border-zinc-200 bg-white cursor-pointer"
                data-testid="schema-sort"
              >
                <option value="prevalence">Sort: prevalence</option>
                <option value="stability">Sort: stability</option>
                <option value="docs">Sort: docs</option>
                <option value="name">Sort: name</option>
              </select>
            </div>
          </div>
          <p className="text-xs text-zinc-500 mb-4">
            {subtitle.emerging} fields emerging across {subtitle.distinctDocTypes} doc type{subtitle.distinctDocTypes !== 1 ? "s" : ""}
            {" · "}
            <span className="text-zinc-700">{subtitle.ready}</span> ready to promote
            {subtitle.promotedRecent > 0 && (
              <> · {subtitle.promotedRecent} promoted in the last 5 minutes</>
            )}
          </p>

          {/* Status legend */}
          <div className="flex items-center gap-4 text-[11px] mb-3 mono">
            <span className="flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-zinc-900" /> typed (promoted)
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-blue-500" /> approaching
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-zinc-300" /> emerging
            </span>
            <span className="ml-auto text-zinc-400 flex items-center gap-1">
              <FilterIcon className="w-3 h-3" strokeWidth={1.75} />
              threshold markers on each bar
            </span>
          </div>

          {filteredRows.length === 0 ? (
            <div className="rounded-lg border border-zinc-200 bg-white p-8 text-center text-sm text-zinc-500">
              No inferred fields match the current filter.
            </div>
          ) : (
            <div className="space-y-2">
              {filteredRows.map((f) => (
                <InferredRow key={f.id} field={f} onMutated={onMutated} />
              ))}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Inferred row — collapsed header (dot · name · description · bar · badge)
// + expandable detail (thresholds · sample values · type · first proposed)
// + action buttons
// ---------------------------------------------------------------------------


function InferredRow({
  field, onMutated,
}: {
  field: InferredField;
  onMutated: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<"promote" | "discard" | "rename" | null>(null);
  const [renameOpen, setRenameOpen] = useState(false);
  const [renameVal, setRenameVal] = useState(field.canonical_name);
  // Sample values lazy-loaded on first expand (Pass B endpoint).
  const [samples, setSamples] = useState<InferredFieldSampleValue[] | null>(null);
  const [samplesLoading, setSamplesLoading] = useState(false);

  const status = statusOf(field);
  const dotClass =
    status === "typed-promoted" ? "bg-zinc-900"
    : status === "approaching"   ? "bg-blue-500"
    : "bg-zinc-300";
  const statusBadge =
    status === "typed-promoted" ? "just promoted"
    : status === "approaching"   ? "approaching"
    : "emerging";
  const badgeClass =
    status === "typed-promoted" ? "bg-zinc-900 text-white"
    : status === "approaching"   ? "bg-blue-100 text-blue-800"
    : "bg-zinc-100 text-zinc-600";

  async function doPromote() {
    setBusy("promote");
    try { await promoteInferredField(field.id); await onMutated(); }
    catch (err) { console.error(err); }
    finally { setBusy(null); }
  }
  async function doDiscard() {
    if (!confirm(`Discard inferred field "${field.canonical_name}"?`)) return;
    setBusy("discard");
    try { await discardInferredField(field.id); await onMutated(); }
    catch (err) { console.error(err); }
    finally { setBusy(null); }
  }
  async function doRename() {
    if (!renameVal.trim() || renameVal.trim() === field.canonical_name) {
      setRenameOpen(false);
      return;
    }
    setBusy("rename");
    try { await renameInferredField(field.id, renameVal.trim()); await onMutated(); setRenameOpen(false); }
    catch (err) { console.error(err); }
    finally { setBusy(null); }
  }

  async function toggleOpen() {
    const next = !open;
    setOpen(next);
    if (next && samples === null) {
      setSamplesLoading(true);
      try {
        const out = await getInferredFieldSampleValues(field.id, 5);
        setSamples(out);
      } catch (err) {
        console.error("getInferredFieldSampleValues failed", err);
        setSamples([]);
      } finally {
        setSamplesLoading(false);
      }
    }
  }

  return (
    <div className="rounded-lg border border-zinc-200 bg-white" data-testid="inferred-row" data-status={status}>
      <button
        type="button"
        onClick={toggleOpen}
        className="w-full flex items-center gap-3 px-4 py-3 hover:bg-zinc-50 cursor-pointer"
      >
        <ChevronRight
          className={`w-3.5 h-3.5 text-zinc-400 transition-transform ${open ? "rotate-90" : ""}`}
        />
        <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${dotClass}`} aria-hidden />
        <span className="mono text-sm text-zinc-900 truncate">{field.canonical_name}</span>
        {field.description && (
          <span className="text-xs text-zinc-500 truncate hidden md:inline">
            {field.description}
          </span>
        )}

        {/* Threshold bar */}
        <div className="ml-auto flex items-center gap-2 flex-shrink-0">
          <ThresholdBar value={field.prevalence} threshold={THRESHOLDS.prevalence} />
          <span className={`text-[10px] mono px-1.5 py-0.5 rounded ${badgeClass}`}>
            {statusBadge}
          </span>
        </div>
      </button>

      {open && (
        <div className="border-t border-zinc-200 px-4 py-4 bg-zinc-50/40">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-x-6 gap-y-3 text-xs">
            <div>
              <div className="text-[10px] uppercase tracking-wider text-zinc-400 mb-1">
                Thresholds
              </div>
              <table className="mono">
                <tbody>
                  <tr>
                    <td className="text-zinc-500 pr-3">prevalence</td>
                    <td className="text-zinc-900 tabular-nums">{field.prevalence.toFixed(2)}</td>
                    <td className="text-zinc-400 pl-2 tabular-nums">/ {THRESHOLDS.prevalence.toFixed(2)}</td>
                  </tr>
                  <tr>
                    <td className="text-zinc-500 pr-3">stability</td>
                    <td className="text-zinc-900 tabular-nums">{field.stability.toFixed(2)}</td>
                    <td className="text-zinc-400 pl-2 tabular-nums">/ {THRESHOLDS.stability.toFixed(2)}</td>
                  </tr>
                  <tr>
                    <td className="text-zinc-500 pr-3">vt confidence</td>
                    <td className="text-zinc-900 tabular-nums">{field.value_type_confidence.toFixed(2)}</td>
                    <td className="text-zinc-400 pl-2 tabular-nums">/ {THRESHOLDS.vt_conf.toFixed(2)}</td>
                  </tr>
                  <tr>
                    <td className="text-zinc-500 pr-3">doc count</td>
                    <td className="text-zinc-900 tabular-nums">{field.n_docs_observed}</td>
                    <td className="text-zinc-400 pl-2 tabular-nums">/ {THRESHOLDS.min_docs_demo}</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div>
              <div className="text-[10px] uppercase tracking-wider text-zinc-400 mb-1">
                Sample values
              </div>
              {samplesLoading ? (
                <div className="flex items-center gap-2 text-zinc-400 text-[11px]">
                  <Loader2 className="w-3 h-3 animate-spin" /> loading…
                </div>
              ) : !samples || samples.length === 0 ? (
                <div className="text-zinc-500 italic text-[11px]">
                  No sample values found.
                </div>
              ) : (
                <ul className="space-y-1 text-[11px]">
                  {samples.map((s, i) => (
                    <li key={`${s.file_id}-${i}`} className="text-zinc-700">
                      <span className="mono">
                        &ldquo;{s.value_text.length > 60
                          ? s.value_text.slice(0, 60) + "…"
                          : s.value_text}&rdquo;
                      </span>
                      {s.file_name && (
                        <span className="text-zinc-400"> — {s.file_name}</span>
                      )}
                    </li>
                  ))}
                  {samples.length === 5 && (
                    <li className="text-[10px] mono text-zinc-400">
                      + more in <span className="text-zinc-500">proposed_fields</span>
                    </li>
                  )}
                </ul>
              )}
            </div>

            <div>
              <div className="text-[10px] uppercase tracking-wider text-zinc-400 mb-1">
                Inferred type
              </div>
              <div className="mono text-zinc-900">{field.value_type ?? "text"}</div>
              <div className="text-[10px] uppercase tracking-wider text-zinc-400 mt-3 mb-1">
                First proposed
              </div>
              <div className="mono text-zinc-700">
                {field.created_at ? new Date(field.created_at).toLocaleDateString() : "—"}
              </div>
            </div>
          </div>

          {/* Action footer */}
          <div className="mt-4 pt-3 border-t border-zinc-200 flex items-center gap-2 text-xs">
            {!field.is_promoted ? (
              <button
                type="button"
                onClick={doPromote}
                disabled={busy !== null}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-white bg-zinc-900 hover:bg-zinc-800 cursor-pointer disabled:opacity-50"
                data-testid="inferred-promote"
              >
                {busy === "promote" ? <Loader2 className="w-3 h-3 animate-spin" /> : <Sparkles className="w-3 h-3" />}
                Promote now (override)
              </button>
            ) : (
              <span className="text-[11px] mono text-zinc-500">
                Already promoted to typed schema.
              </span>
            )}

            {renameOpen ? (
              <form
                onSubmit={(e) => { e.preventDefault(); doRename(); }}
                className="flex items-center gap-1"
              >
                <input
                  autoFocus
                  value={renameVal}
                  onChange={(e) => setRenameVal(e.target.value)}
                  className="text-xs mono px-2 py-1 rounded border border-zinc-300 focus:border-zinc-500 focus:outline-none"
                />
                <button
                  type="submit"
                  disabled={busy !== null}
                  className="px-2 py-1 rounded bg-zinc-900 text-white cursor-pointer"
                >save</button>
                <button
                  type="button"
                  onClick={() => { setRenameOpen(false); setRenameVal(field.canonical_name); }}
                  className="px-2 py-1 rounded text-zinc-500 cursor-pointer"
                >cancel</button>
              </form>
            ) : (
              <button
                type="button"
                onClick={() => setRenameOpen(true)}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-zinc-700 hover:bg-zinc-100 cursor-pointer"
                data-testid="inferred-rename"
              >
                <Pencil className="w-3 h-3" />
                Rename
              </button>
            )}

            <button
              type="button"
              title="Wave B — merge with another inferred field"
              disabled
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-zinc-400 cursor-not-allowed"
            >
              <GitBranch className="w-3 h-3" />
              Merge with…
            </button>

            <button
              type="button"
              onClick={doDiscard}
              disabled={busy !== null}
              className="ml-auto flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-red-600 hover:bg-red-50 cursor-pointer disabled:opacity-50"
              data-testid="inferred-discard"
            >
              {busy === "discard" ? <Loader2 className="w-3 h-3 animate-spin" /> : <Trash2 className="w-3 h-3" />}
              Discard
            </button>
          </div>
        </div>
      )}
    </div>
  );
}


// ---------------------------------------------------------------------------
// Threshold bar — fills up to `value`, with a small marker line at
// `threshold`. When value crosses the marker, the bar shifts to the
// "above threshold" color so it visually pops.
// ---------------------------------------------------------------------------


function ThresholdBar({ value, threshold }: { value: number; threshold: number }) {
  const valuePct  = Math.max(0, Math.min(1, value)) * 100;
  const markerPct = Math.max(0, Math.min(1, threshold)) * 100;
  const above = value >= threshold;
  return (
    <div className="relative w-32 h-1.5 rounded bg-zinc-100 flex-shrink-0">
      <div
        className={`absolute left-0 top-0 bottom-0 rounded ${above ? "bg-zinc-900" : "bg-zinc-400"}`}
        style={{ width: `${valuePct}%` }}
      />
      <div
        className="absolute top-[-2px] bottom-[-2px] w-px bg-zinc-700"
        style={{ left: `${markerPct}%` }}
        aria-hidden
        title={`threshold ${threshold.toFixed(2)}`}
      />
      <span className="absolute -top-4 right-0 text-[10px] mono text-zinc-500 tabular-nums">
        {value.toFixed(2)}
      </span>
    </div>
  );
}


// ===========================================================================
// Tab: Typed
// ===========================================================================


function TypedTab({ schemas }: { schemas: SchemaSummary[] }) {
  const [activeSchemaId, setActiveSchemaId] = useState<string | null>(null);
  const [entities, setEntities] = useState<SchemaEntity[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (schemas.length > 0 && !activeSchemaId) setActiveSchemaId(schemas[0].id);
  }, [schemas, activeSchemaId]);

  useEffect(() => {
    if (!activeSchemaId) return;
    setLoading(true);
    (async () => {
      try {
        const out = await listSchemaEntities(activeSchemaId);
        setEntities(out);
      } finally {
        setLoading(false);
      }
    })();
  }, [activeSchemaId]);

  if (schemas.length === 0) {
    return (
      <div className="max-w-4xl mx-auto px-8 py-6">
        <EmptyState
          title="No typed schemas yet"
          body="Curated schemas appear here once a field clears the auto-promotion threshold or a user creates one explicitly via POST /schemas."
        />
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto px-8 py-6 overflow-y-auto h-full">
      <h1 className="text-lg font-semibold text-zinc-900 mb-4">
        Typed schemas <span className="mono text-zinc-400 text-sm">{schemas.length}</span>
      </h1>

      <div className="flex flex-wrap gap-1 mb-5 text-xs">
        {schemas.map((s) => {
          const active = s.id === activeSchemaId;
          return (
            <button
              key={s.id}
              type="button"
              onClick={() => setActiveSchemaId(s.id)}
              className={`px-3 py-1.5 rounded-md cursor-pointer mono ${
                active
                  ? "bg-zinc-900 text-white"
                  : "bg-white border border-zinc-200 text-zinc-700 hover:bg-zinc-100"
              }`}
            >
              {s.name}
            </button>
          );
        })}
      </div>

      {loading ? (
        <SpinnerInline />
      ) : entities.length === 0 ? (
        <EmptyState title="No entity types defined" body="This schema has no entity types yet." />
      ) : (
        <div className="space-y-3">
          {entities.map((e) => (
            <TypedEntityCard key={e.id} schemaId={activeSchemaId!} entity={e} />
          ))}
        </div>
      )}
    </div>
  );
}


function TypedEntityCard({
  schemaId, entity,
}: { schemaId: string; entity: SchemaEntity }) {
  const [open, setOpen] = useState(false);
  const [fields, setFields] = useState<SchemaField[] | null>(null);

  async function toggle() {
    if (!open && fields === null) {
      try {
        const out = await listSchemaEntityFields(schemaId, entity.id);
        setFields(out);
      } catch (err) {
        console.error(err);
        setFields([]);
      }
    }
    setOpen(!open);
  }

  return (
    <div className="rounded-lg border border-zinc-200 bg-white">
      <button
        type="button"
        onClick={toggle}
        className="w-full flex items-center gap-3 px-4 py-3 hover:bg-zinc-50 cursor-pointer"
      >
        <ChevronRight className={`w-3.5 h-3.5 text-zinc-400 transition-transform ${open ? "rotate-90" : ""}`} />
        <span className="text-[10px] mono px-1.5 py-0.5 rounded bg-zinc-100 text-zinc-700">
          {entity.lifecycle_state}
        </span>
        <span className="text-sm font-medium text-zinc-900">{entity.name}</span>
        {entity.description && (
          <span className="text-xs text-zinc-500 truncate ml-2">
            {entity.description}
          </span>
        )}
        <span className="ml-auto text-[11px] text-zinc-400 mono">
          {fields ? `${fields.length} fields` : "click to load"}
        </span>
      </button>
      {open && fields !== null && (
        <div className="border-t border-zinc-200 px-4 py-3 bg-zinc-50/40">
          {fields.length === 0 ? (
            <div className="text-xs text-zinc-500">No fields.</div>
          ) : (
            <table className="w-full text-xs">
              <thead className="text-zinc-500">
                <tr>
                  <th className="text-left py-1 font-medium">Name</th>
                  <th className="text-left py-1 font-medium">Type</th>
                  <th className="text-left py-1 font-medium">Required</th>
                  <th className="text-left py-1 font-medium">Description</th>
                </tr>
              </thead>
              <tbody>
                {fields.map((f) => (
                  <tr key={f.id} className="border-t border-zinc-200">
                    <td className="py-1.5 mono text-zinc-900">{f.name}</td>
                    <td className="py-1.5 mono text-zinc-600">{f.type ?? "text"}</td>
                    <td className="py-1.5 text-zinc-600">{f.is_required ? "yes" : "—"}</td>
                    <td className="py-1.5 text-zinc-600">{f.nl_description ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}


// ===========================================================================
// Tab: Collisions
// ===========================================================================


function CollisionsTab() {
  return (
    <div className="max-w-4xl mx-auto px-8 py-6 overflow-y-auto h-full">
      <h1 className="text-lg font-semibold text-zinc-900 mb-1">Collisions</h1>
      <p className="text-xs text-zinc-500 mb-4">
        Naming overlaps between discovered fields and typed schema fields.
      </p>
      <EmptyState
        title="No collisions detected"
        body="Semantic-similarity collision detection (when an L2b cluster's name fuzzy-matches a typed schema field) lands in Pass B. Wave A's exact-name check fires zero collisions on the demo corpus."
      />
    </div>
  );
}


// ===========================================================================
// Tab: Vocabulary
// ===========================================================================


function VocabularyTab() {
  const [entries, setEntries] = useState<VocabEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");

  useEffect(() => {
    setLoading(true);
    (async () => {
      try {
        const out = await listVocabulary(`workspace:${getWorkspaceId()}`, 500);
        setEntries(out);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const filtered = useMemo(() => {
    const s = search.trim().toLowerCase();
    if (!s) return entries;
    return entries.filter((e) =>
      e.canonical_term.toLowerCase().includes(s)
      || e.synonyms.some((syn) => syn.toLowerCase().includes(s))
    );
  }, [entries, search]);

  return (
    <div className="max-w-4xl mx-auto px-8 py-6 overflow-y-auto h-full">
      <h1 className="text-lg font-semibold text-zinc-900 mb-1">
        Vocabulary <span className="mono text-zinc-400 text-sm">{entries.length}</span>
      </h1>
      <p className="text-xs text-zinc-500 mb-4">
        Synonym + acronym clusters discovered by Design 6 field clustering. BM25 retrieval expands queries via these mappings.
      </p>
      <input
        type="text"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder="Filter by term or synonym"
        className="w-full text-xs px-3 py-2 mb-4 rounded border border-zinc-200 focus:outline-none focus:border-zinc-400"
      />
      {loading ? <SpinnerInline />
      : filtered.length === 0 ? (
        <EmptyState
          title={entries.length === 0 ? "No vocabulary entries yet" : "No matches"}
          body={entries.length === 0
            ? "Synonym clusters auto-populate as the field-clustering worker runs over your corpus. Manually-added terms also show here."
            : "No vocabulary term matches that search."}
        />
      ) : (
        <div className="rounded-lg border border-zinc-200 bg-white">
          <table className="w-full text-xs">
            <thead className="text-zinc-500 bg-zinc-50/40">
              <tr>
                <th className="text-left px-4 py-2 font-medium">Term</th>
                <th className="text-left px-4 py-2 font-medium">Synonyms</th>
                <th className="text-left px-4 py-2 font-medium">Source</th>
                <th className="text-left px-4 py-2 font-medium">Confidence</th>
                <th className="text-left px-4 py-2 font-medium">Docs</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((e) => (
                <tr key={e.id} className="border-t border-zinc-200">
                  <td className="px-4 py-1.5 mono text-zinc-900">{e.canonical_term}</td>
                  <td className="px-4 py-1.5 text-zinc-700">
                    {e.synonyms.length === 0 ? (
                      <span className="text-zinc-400">—</span>
                    ) : (
                      <span className="mono text-[11px]">{e.synonyms.join(" · ")}</span>
                    )}
                  </td>
                  <td className="px-4 py-1.5">
                    <span className="text-[10px] mono px-1.5 py-0.5 rounded bg-zinc-100 text-zinc-700">
                      {e.source}
                    </span>
                  </td>
                  <td className="px-4 py-1.5 mono text-zinc-600">
                    {(e.confidence * 100).toFixed(0)}%
                  </td>
                  <td className="px-4 py-1.5 text-zinc-600">{e.n_docs_observed}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}


// ===========================================================================
// Tab: Lineage
// ===========================================================================


function LineageTab() {
  const [chains, setChains] = useState<DocChainSummary[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try { setChains(await listDocChains(200)); }
      finally { setLoading(false); }
    })();
  }, []);

  return (
    <div className="max-w-4xl mx-auto px-8 py-6 overflow-y-auto h-full">
      <h1 className="text-lg font-semibold text-zinc-900 mb-1">
        Lineage <span className="mono text-zinc-400 text-sm">{chains.length}</span>
      </h1>
      <p className="text-xs text-zinc-500 mb-4">
        Doc chains: amendments, email threads, drawing revisions, circulars.
        Latest member is current_version; older members are superseded.
      </p>
      {loading ? <SpinnerInline />
      : chains.length === 0 ? (
        <EmptyState
          title="No doc chains detected"
          body="Chains form when the worker detects amendment / supersession language across docs, or matching In-Reply-To headers on emails."
        />
      ) : (
        <div className="space-y-2">
          {chains.map((c) => (
            <div
              key={c.id}
              className="rounded-lg border border-zinc-200 bg-white px-4 py-3"
            >
              <div className="flex items-center gap-3">
                <span className="text-[10px] mono px-1.5 py-0.5 rounded bg-zinc-100 text-zinc-700">
                  {c.type}
                </span>
                <span className="text-sm text-zinc-900">
                  {c.member_count} member{c.member_count !== 1 ? "s" : ""}
                </span>
                {c.detection_confidence != null && (
                  <span className="text-[11px] mono text-zinc-500">
                    confidence {(c.detection_confidence * 100).toFixed(0)}%
                  </span>
                )}
                <span className="ml-auto text-[11px] mono text-zinc-400">
                  chain {c.id.slice(0, 8)}…
                </span>
              </div>
              {c.title && <div className="text-xs text-zinc-500 mt-1 truncate">{c.title}</div>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}


// ===========================================================================
// Tab: Versions
// ===========================================================================


function VersionsTab({ schemas }: { schemas: SchemaSummary[] }) {
  const [activeSchemaId, setActiveSchemaId] = useState<string | null>(null);
  const [versions, setVersions] = useState<SchemaVersionRow[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (schemas.length > 0 && !activeSchemaId) setActiveSchemaId(schemas[0].id);
  }, [schemas, activeSchemaId]);

  useEffect(() => {
    if (!activeSchemaId) return;
    setLoading(true);
    (async () => {
      try { setVersions(await listSchemaVersions(activeSchemaId, 50)); }
      finally { setLoading(false); }
    })();
  }, [activeSchemaId]);

  if (schemas.length === 0) {
    return (
      <div className="max-w-4xl mx-auto px-8 py-6">
        <EmptyState
          title="No schemas, no versions"
          body="Versions show schema evolution over time. They appear once any schema has been edited."
        />
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto px-8 py-6 overflow-y-auto h-full">
      <h1 className="text-lg font-semibold text-zinc-900 mb-4">Schema versions</h1>
      <div className="flex flex-wrap gap-1 mb-5 text-xs">
        {schemas.map((s) => {
          const active = s.id === activeSchemaId;
          return (
            <button
              key={s.id}
              type="button"
              onClick={() => setActiveSchemaId(s.id)}
              className={`px-3 py-1.5 rounded-md cursor-pointer mono ${
                active
                  ? "bg-zinc-900 text-white"
                  : "bg-white border border-zinc-200 text-zinc-700 hover:bg-zinc-100"
              }`}
            >
              {s.name}
            </button>
          );
        })}
      </div>
      {loading ? <SpinnerInline />
      : versions.length === 0 ? (
        <EmptyState
          title="No versions yet"
          body="The initial version is created on schema creation. Subsequent edits mint new versions."
        />
      ) : (
        <div className="rounded-lg border border-zinc-200 bg-white">
          <table className="w-full text-xs">
            <thead className="text-zinc-500 bg-zinc-50/40">
              <tr>
                <th className="text-left px-4 py-2 font-medium">Version</th>
                <th className="text-left px-4 py-2 font-medium">Created</th>
                <th className="text-left px-4 py-2 font-medium">By</th>
                <th className="text-left px-4 py-2 font-medium">Description</th>
              </tr>
            </thead>
            <tbody>
              {versions.map((v) => (
                <tr key={`${activeSchemaId}-${v.version}`} className="border-t border-zinc-200">
                  <td className="px-4 py-1.5 mono text-zinc-900">v{v.version}</td>
                  <td className="px-4 py-1.5 mono text-zinc-600">
                    {v.created_at ? new Date(v.created_at).toLocaleString() : "—"}
                  </td>
                  <td className="px-4 py-1.5 text-zinc-600">{v.created_by ?? "—"}</td>
                  <td className="px-4 py-1.5 text-zinc-600">
                    {v.description ?? (v.kind ? `kind: ${v.kind}` : "")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}


// ===========================================================================
// Shared helpers
// ===========================================================================


function EmptyState({ title, body }: { title: string; body: string }) {
  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-8 text-center">
      <div className="text-sm font-medium text-zinc-900 mb-1">{title}</div>
      <div className="text-xs text-zinc-500 max-w-md mx-auto leading-relaxed">{body}</div>
    </div>
  );
}


function SpinnerInline() {
  return (
    <div className="flex items-center justify-center py-12 text-zinc-400">
      <Loader2 className="w-5 h-5 animate-spin" />
    </div>
  );
}
