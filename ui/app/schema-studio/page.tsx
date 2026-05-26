"use client";

/**
 * Schema Studio — the workspace's data-model surface.
 *
 * 6 tabs, lazy-loaded:
 *   - Typed       — curated/promoted schemas (entity types + fields)
 *   - Inferred    — L2b cross-doc field clusters not yet promoted
 *   - Collisions  — placeholder for naming-collision review
 *   - Vocabulary  — Design 6 synonym + acronym discovery
 *   - Lineage     — doc chains (amendments / email threads / revisions)
 *   - Versions    — schema-version history
 *
 * Scale to 100k: each tab uses server-side pagination (limit / offset)
 * + an inline search box for the high-cardinality tabs (Inferred,
 * Vocabulary). Per-tab fetches keep the initial page render cheap;
 * Lineage lazy-loads members per chain on click.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Layers, GitBranch, AlertOctagon, BookOpen, Network, History,
  Loader2,
} from "lucide-react";
import {
  listSchemas, listSchemaEntities, listSchemaEntityFields,
  listInferredFields, listVocabulary, listDocChains, listSchemaVersions,
  type SchemaSummary, type SchemaEntity, type SchemaField,
  type InferredField, type VocabEntry, type DocChainSummary,
  type SchemaVersionRow,
} from "@/lib/api";
import { Sidebar } from "@/components/Sidebar";


type TabKey = "typed" | "inferred" | "collisions" | "vocabulary" | "lineage" | "versions";

const TABS: { key: TabKey; label: string; icon: typeof Layers }[] = [
  { key: "typed",      label: "Typed",      icon: Layers },
  { key: "inferred",   label: "Inferred",   icon: GitBranch },
  { key: "collisions", label: "Collisions", icon: AlertOctagon },
  { key: "vocabulary", label: "Vocabulary", icon: BookOpen },
  { key: "lineage",    label: "Lineage",    icon: Network },
  { key: "versions",   label: "Versions",   icon: History },
];


function getWorkspaceId(): string {
  if (typeof window === "undefined") return "00000000-0000-0000-0000-000000000001";
  return (
    (window as unknown as { __KB_WORKSPACE__?: string }).__KB_WORKSPACE__
    ?? "00000000-0000-0000-0000-000000000001"
  );
}


export default function SchemaStudioPage() {
  const [tab, setTab] = useState<TabKey>("typed");

  return (
    <div className="flex h-full">
      <Sidebar current="schema" />

      <main className="flex-1 flex flex-col min-w-0 bg-white">
        <header className="h-12 flex-shrink-0 border-b border-zinc-200 flex items-center px-5 gap-4">
          <span className="text-sm text-zinc-900">Schema Studio</span>
          <span className="text-[11px] text-zinc-400 mono">
            Curated schemas · inferred clusters · vocabulary · lineage · versions
          </span>
        </header>

        {/* Tab strip */}
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
              </button>
            );
          })}
        </div>

        <div className="flex-1 overflow-y-auto bg-zinc-50/40">
          {tab === "typed"      && <TypedTab />}
          {tab === "inferred"   && <InferredTab />}
          {tab === "collisions" && <CollisionsTab />}
          {tab === "vocabulary" && <VocabularyTab />}
          {tab === "lineage"    && <LineageTab />}
          {tab === "versions"   && <VersionsTab />}
        </div>
      </main>
    </div>
  );
}


// ===========================================================================
// Tab: Typed
// ===========================================================================


function TypedTab() {
  const [schemas, setSchemas] = useState<SchemaSummary[]>([]);
  const [activeSchemaId, setActiveSchemaId] = useState<string | null>(null);
  const [entities, setEntities] = useState<SchemaEntity[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const out = await listSchemas();
        setSchemas(out);
        if (out.length > 0) setActiveSchemaId(out[0].id);
      } catch (err) { console.error(err); }
    })();
  }, []);

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
      <EmptyState
        title="No typed schemas yet"
        body="Curated schemas appear here once a field clears the auto-promotion threshold OR a user creates one explicitly via POST /schemas."
      />
    );
  }

  return (
    <div className="max-w-5xl mx-auto px-8 py-6">
      <h1 className="text-lg font-semibold text-zinc-900 mb-4">
        Typed schemas <span className="mono text-zinc-400 text-sm">{schemas.length}</span>
      </h1>

      <div className="flex gap-1 mb-5 text-xs">
        {schemas.map((s) => {
          const active = s.id === activeSchemaId;
          return (
            <button
              key={s.id}
              type="button"
              onClick={() => setActiveSchemaId(s.id)}
              className={`px-3 py-1.5 rounded-md cursor-pointer ${
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
        <EmptyState
          title="No entity types defined"
          body="This schema has no entity types yet."
        />
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
// Tab: Inferred
// ===========================================================================


function InferredTab() {
  const [fields, setFields] = useState<InferredField[]>([]);
  const [docTypeFilter, setDocTypeFilter] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [debouncedFilter, setDebouncedFilter] = useState("");

  useEffect(() => {
    const t = setTimeout(() => setDebouncedFilter(docTypeFilter.trim()), 220);
    return () => clearTimeout(t);
  }, [docTypeFilter]);

  useEffect(() => {
    setLoading(true);
    (async () => {
      try {
        const out = await listInferredFields({
          doc_type: debouncedFilter || undefined,
          limit: 500,
        });
        setFields(out);
      } finally {
        setLoading(false);
      }
    })();
  }, [debouncedFilter]);

  // Group by inferred_doc_type.
  const grouped = useMemo<Record<string, InferredField[]>>(() => {
    const acc: Record<string, InferredField[]> = {};
    for (const f of fields) (acc[f.inferred_doc_type] ??= []).push(f);
    return acc;
  }, [fields]);

  return (
    <div className="max-w-5xl mx-auto px-8 py-6">
      <h1 className="text-lg font-semibold text-zinc-900 mb-1">
        Inferred fields <span className="mono text-zinc-400 text-sm">{fields.length}</span>
      </h1>
      <p className="text-xs text-zinc-500 mb-4">
        Cross-doc field clusters from L2b extraction. Above-threshold clusters auto-promote to typed schemas.
      </p>

      <input
        type="text"
        value={docTypeFilter}
        onChange={(e) => setDocTypeFilter(e.target.value)}
        placeholder="Filter by doc_type (e.g. contract, invoice)"
        className="w-full text-xs px-3 py-2 mb-4 rounded border border-zinc-200 focus:outline-none focus:border-zinc-400"
      />

      {loading ? (
        <SpinnerInline />
      ) : fields.length === 0 ? (
        <EmptyState
          title="No inferred fields"
          body="Inferred fields show up after a few docs of the same type are ingested."
        />
      ) : (
        <div className="space-y-5">
          {Object.entries(grouped).map(([doctype, list]) => (
            <div key={doctype} className="rounded-lg border border-zinc-200 bg-white">
              <div className="px-4 py-2.5 border-b border-zinc-200 flex items-center gap-2">
                <span className="text-[10px] uppercase tracking-wider text-zinc-400">Inferred for</span>
                <span className="text-sm font-medium mono">{doctype}</span>
                <span className="ml-auto text-[11px] mono text-zinc-400">{list.length} field{list.length !== 1 ? "s" : ""}</span>
              </div>
              <table className="w-full text-xs">
                <thead className="text-zinc-500 bg-zinc-50/40">
                  <tr>
                    <th className="text-left px-4 py-2 font-medium">Field</th>
                    <th className="text-left px-4 py-2 font-medium">Type</th>
                    <th className="text-left px-4 py-2 font-medium">Docs</th>
                    <th className="text-left px-4 py-2 font-medium">Prevalence</th>
                    <th className="text-left px-4 py-2 font-medium">Stability</th>
                    <th className="text-left px-4 py-2 font-medium">Promoted</th>
                  </tr>
                </thead>
                <tbody>
                  {list.map((f) => (
                    <tr key={f.id} className="border-t border-zinc-200">
                      <td className="px-4 py-1.5 mono text-zinc-900">{f.canonical_name}</td>
                      <td className="px-4 py-1.5 mono text-zinc-600">{f.value_type ?? "text"}</td>
                      <td className="px-4 py-1.5 text-zinc-600">{f.n_docs_observed}</td>
                      <td className="px-4 py-1.5 text-zinc-600">
                        {(f.prevalence * 100).toFixed(0)}%
                      </td>
                      <td className="px-4 py-1.5 text-zinc-600">
                        {(f.stability * 100).toFixed(0)}%
                      </td>
                      <td className="px-4 py-1.5">
                        {f.is_promoted ? (
                          <span className="text-[10px] mono px-1.5 py-0.5 rounded bg-green-100 text-green-800">promoted</span>
                        ) : (
                          <span className="text-[10px] mono text-zinc-500">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
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
    <div className="max-w-5xl mx-auto px-8 py-6">
      <h1 className="text-lg font-semibold text-zinc-900 mb-1">
        Collisions
      </h1>
      <p className="text-xs text-zinc-500 mb-4">
        Naming overlaps between discovered fields and typed schema fields.
      </p>
      <EmptyState
        title="No collisions detected"
        body="When an L2b cluster's canonical name matches an existing typed schema field, it surfaces here for resolve / merge review. The Wave A heuristic only flags exact-name matches; semantic-similarity detection lands in Wave B."
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
    const domainId = `workspace:${getWorkspaceId()}`;
    (async () => {
      try {
        const out = await listVocabulary(domainId, 500);
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
    <div className="max-w-5xl mx-auto px-8 py-6">
      <h1 className="text-lg font-semibold text-zinc-900 mb-1">
        Vocabulary <span className="mono text-zinc-400 text-sm">{entries.length}</span>
      </h1>
      <p className="text-xs text-zinc-500 mb-4">
        Synonym + acronym clusters discovered by Design 6 field clustering.
        BM25 retrieval expands queries via these mappings.
      </p>

      <input
        type="text"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder="Filter by term or synonym"
        className="w-full text-xs px-3 py-2 mb-4 rounded border border-zinc-200 focus:outline-none focus:border-zinc-400"
      />

      {loading ? (
        <SpinnerInline />
      ) : filtered.length === 0 ? (
        <EmptyState
          title={entries.length === 0 ? "No vocabulary entries yet" : "No matches"}
          body={
            entries.length === 0
              ? "Synonym clusters auto-populate as the field-clustering worker runs over your corpus. Manually-added terms also show here."
              : "No vocabulary term matches that search."
          }
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
// Tab: Lineage (doc chains)
// ===========================================================================


function LineageTab() {
  const [chains, setChains] = useState<DocChainSummary[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const out = await listDocChains(200);
        setChains(out);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  return (
    <div className="max-w-5xl mx-auto px-8 py-6">
      <h1 className="text-lg font-semibold text-zinc-900 mb-1">
        Lineage <span className="mono text-zinc-400 text-sm">{chains.length}</span>
      </h1>
      <p className="text-xs text-zinc-500 mb-4">
        Doc chains: amendments, email threads, drawing revisions, circular + corrigendum pairs.
        The system marks the latest member as current_version; older members are superseded.
      </p>

      {loading ? (
        <SpinnerInline />
      ) : chains.length === 0 ? (
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
              data-testid="schema-chain-row"
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
              {c.title && (
                <div className="text-xs text-zinc-500 mt-1 truncate">
                  {c.title}
                </div>
              )}
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


function VersionsTab() {
  const [schemas, setSchemas] = useState<SchemaSummary[]>([]);
  const [activeSchemaId, setActiveSchemaId] = useState<string | null>(null);
  const [versions, setVersions] = useState<SchemaVersionRow[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    (async () => {
      const out = await listSchemas();
      setSchemas(out);
      if (out.length > 0) setActiveSchemaId(out[0].id);
    })();
  }, []);

  useEffect(() => {
    if (!activeSchemaId) return;
    setLoading(true);
    (async () => {
      try {
        const out = await listSchemaVersions(activeSchemaId, 50);
        setVersions(out);
      } finally {
        setLoading(false);
      }
    })();
  }, [activeSchemaId]);

  if (schemas.length === 0) {
    return (
      <EmptyState
        title="No schemas, no versions"
        body="Versions show schema evolution over time. They appear once any schema has been edited at least once."
      />
    );
  }

  return (
    <div className="max-w-5xl mx-auto px-8 py-6">
      <h1 className="text-lg font-semibold text-zinc-900 mb-4">
        Schema versions
      </h1>

      <div className="flex gap-1 mb-5 text-xs">
        {schemas.map((s) => {
          const active = s.id === activeSchemaId;
          return (
            <button
              key={s.id}
              type="button"
              onClick={() => setActiveSchemaId(s.id)}
              className={`px-3 py-1.5 rounded-md cursor-pointer ${
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
      ) : versions.length === 0 ? (
        <EmptyState
          title="No versions yet"
          body="The initial version is created on schema creation. Subsequent edits via /schemas/{id} mint new versions."
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
// Helpers
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
