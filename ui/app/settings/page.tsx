"use client";

/**
 * /settings — workspace configuration surface.
 *
 * Three tabs (?tab=…):
 *   - models        ← per-stage LLM / embedder / reranker / faithfulness
 *                     model choices, resolved across all config layers
 *   - effective     ← every config key with its value + layer of origin.
 *                     Search-filtered; shows where each value comes
 *                     from (defaults / global / domain / workspace /
 *                     doc_type / doc / user).
 *   - overrides     ← active runtime overrides; create + soft-revoke
 *                     via inline forms.
 *
 * Layered config design lives in `docs/gaps_design.md` (Design 9).
 * Backend: src/kb/api/settings.py — no new endpoints needed.
 */

import { Suspense, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams, usePathname } from "next/navigation";
import {
  Brain, Layers, Sliders, Search, Loader2, Trash2,
  CheckCircle2, AlertCircle,
} from "lucide-react";
import {
  getEffectiveConfig, getModelChoices, listOverrides,
  createOverride, revokeOverride,
  type EffectiveEntry, type ModelChoicesResponse, type OverrideOut,
} from "@/lib/api";
import { Sidebar } from "@/components/Sidebar";


type Tab = "models" | "effective" | "overrides";


const LAYER_ORDER: Record<string, number> = {
  defaults: 0, global: 1, domain: 2, workspace: 3,
  doc_type: 4, doc: 5, user: 6,
};

const LAYER_TONE: Record<string, string> = {
  defaults:  "bg-zinc-100 text-zinc-600",
  global:    "bg-zinc-100 text-zinc-700",
  domain:    "bg-amber-50 text-amber-800 border border-amber-200",
  workspace: "bg-blue-50 text-blue-800 border border-blue-200",
  doc_type:  "bg-purple-50 text-purple-800 border border-purple-200",
  doc:       "bg-violet-50 text-violet-800 border border-violet-200",
  user:      "bg-emerald-50 text-emerald-800 border border-emerald-200",
};


export default function SettingsPage() {
  return (
    <Suspense fallback={null}>
      <SettingsShell />
    </Suspense>
  );
}


function SettingsShell() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const tab = (["models", "effective", "overrides"] as Tab[]).includes(
    (searchParams.get("tab") ?? "models") as Tab,
  )
    ? (searchParams.get("tab") ?? "models") as Tab
    : "models";

  function setTab(next: Tab) {
    const sp = new URLSearchParams(searchParams.toString());
    if (next === "models") sp.delete("tab");
    else sp.set("tab", next);
    const qs = sp.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  }

  return (
    <div className="flex h-full">
      <Sidebar current="settings" />
      <main className="flex-1 flex flex-col min-w-0 bg-zinc-50/40">
        <header className="h-12 flex-shrink-0 border-b border-zinc-200 flex items-center px-5 gap-3 bg-white">
          <span className="text-zinc-900 text-sm">Settings</span>
          <span className="text-[11px] text-zinc-400 mono">
            workspace · models · runtime overrides
          </span>
        </header>

        {/* Tab strip */}
        <div className="border-b border-zinc-200 px-8 flex gap-1 bg-white">
          {([
            { key: "models",    label: "Models",          icon: Brain },
            { key: "effective", label: "Effective config", icon: Layers },
            { key: "overrides", label: "Overrides",       icon: Sliders },
          ] as { key: Tab; label: string; icon: typeof Brain }[]).map((t) => {
            const Icon = t.icon;
            const active = tab === t.key;
            return (
              <button
                key={t.key}
                type="button"
                onClick={() => setTab(t.key)}
                className={`flex items-center gap-2 px-3 py-2.5 text-xs border-b-2 transition-colors cursor-pointer ${
                  active
                    ? "border-zinc-900 text-zinc-900"
                    : "border-transparent text-zinc-500 hover:text-zinc-900"
                }`}
                data-testid={`settings-tab-${t.key}`}
                data-active={active || undefined}
              >
                <Icon className="w-3.5 h-3.5" strokeWidth={1.75} />
                {t.label}
              </button>
            );
          })}
        </div>

        <div className="flex-1 overflow-y-auto">
          <div className="max-w-5xl mx-auto px-8 py-6">
            {tab === "models" && <ModelsTab />}
            {tab === "effective" && <EffectiveTab />}
            {tab === "overrides" && <OverridesTab />}
          </div>
        </div>
      </main>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Models tab
// ---------------------------------------------------------------------------

const MODEL_STAGES: Array<{ key: keyof ModelChoicesResponse; label: string; hint: string }> = [
  { key: "extraction_llm",     label: "Extraction LLM",     hint: "Mentions / fields / atomic units extraction" },
  { key: "hard_query_llm",     label: "Hard-query LLM",     hint: "Complex multi-hop generation (mode H)" },
  { key: "generation",         label: "Generation",         hint: "Default answer generation" },
  { key: "generation_hard",    label: "Generation (hard)",  hint: "Generation when CRAG triggers hard mode" },
  { key: "embedder",           label: "Embedder",           hint: "Dense vector backbone for chunks + RAPTOR" },
  { key: "reranker",           label: "Reranker",           hint: "Cross-encoder over the fused candidate set" },
  { key: "faithfulness",       label: "Faithfulness judge", hint: "Sentence-level grounding gate" },
  { key: "intent_classifier",  label: "Intent classifier",  hint: "Routes queries to mode (E/F/S/H/T/M/G/D/C/A/Q/K/I)" },
  { key: "conflict_detector",  label: "Conflict detector",  hint: "Chained-doc disagreement resolution" },
];


function ModelsTab() {
  const [models, setModels] = useState<ModelChoicesResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getModelChoices()
      .then((r) => { if (!cancelled) setModels(r); })
      .catch((e) => { if (!cancelled) setErr(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20 text-zinc-400">
        <Loader2 className="w-5 h-5 animate-spin" />
      </div>
    );
  }
  if (err) return <ErrorBanner msg={err} />;
  if (!models) return null;

  return (
    <div>
      <div className="mb-4">
        <h2 className="text-base font-semibold text-zinc-900">Per-stage models</h2>
        <p className="text-sm text-zinc-500 mt-0.5">
          Resolved values from the layered config (defaults → global → domain →
          workspace → doc_type → doc → user). Edit via the Overrides tab.
        </p>
      </div>
      <div className="rounded-lg border border-zinc-200 bg-white divide-y divide-zinc-100">
        {MODEL_STAGES.map((s) => {
          const v = models[s.key];
          return (
            <div
              key={s.key}
              className="grid grid-cols-[200px_1fr] gap-4 px-4 py-3 items-center"
              data-testid="settings-model-row"
            >
              <div>
                <div className="text-sm text-zinc-900">{s.label}</div>
                <div className="text-[11px] text-zinc-500">{s.hint}</div>
              </div>
              <div className="mono text-xs text-zinc-700">
                {v ?? (
                  <span className="text-zinc-400">— unconfigured (falls back at call site)</span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Effective config tab
// ---------------------------------------------------------------------------

function EffectiveTab() {
  const [entries, setEntries] = useState<EffectiveEntry[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState("");
  const [layerFilter, setLayerFilter] = useState<string>("all");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getEffectiveConfig()
      .then((r) => { if (!cancelled) setEntries(r.entries); })
      .catch((e) => { if (!cancelled) setErr(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const filtered = useMemo(() => {
    if (!entries) return [];
    const qn = q.trim().toLowerCase();
    return entries
      .filter((e) => layerFilter === "all" || e.layer === layerFilter)
      .filter((e) => !qn || `${e.key} ${JSON.stringify(e.value)}`.toLowerCase().includes(qn))
      .sort((a, b) => a.key.localeCompare(b.key));
  }, [entries, q, layerFilter]);

  const layerCounts = useMemo(() => {
    const out: Record<string, number> = {};
    for (const e of entries ?? []) out[e.layer] = (out[e.layer] ?? 0) + 1;
    return out;
  }, [entries]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20 text-zinc-400">
        <Loader2 className="w-5 h-5 animate-spin" />
      </div>
    );
  }
  if (err) return <ErrorBanner msg={err} />;

  return (
    <div>
      <div className="mb-4">
        <h2 className="text-base font-semibold text-zinc-900">Effective configuration</h2>
        <p className="text-sm text-zinc-500 mt-0.5">
          Every config key the system can resolve, with the layer it
          ultimately came from. Lower layers shadow higher ones.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2 mb-3">
        <div className="relative flex-1 min-w-[240px]">
          <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-zinc-400" />
          <input
            type="text"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search keys / values…"
            className="w-full text-xs pl-7 pr-3 py-1.5 rounded-md border border-zinc-200 focus:outline-none focus:border-zinc-400"
            data-testid="settings-effective-search"
          />
        </div>
        <select
          value={layerFilter}
          onChange={(e) => setLayerFilter(e.target.value)}
          className="text-xs px-2 py-1 rounded-md border border-zinc-200 bg-white mono cursor-pointer"
          data-testid="settings-effective-layer"
        >
          <option value="all">All layers</option>
          {Object.keys(LAYER_ORDER)
            .sort((a, b) => LAYER_ORDER[a] - LAYER_ORDER[b])
            .map((l) => (
              <option key={l} value={l}>
                {l} ({layerCounts[l] ?? 0})
              </option>
            ))}
        </select>
        <span className="text-[11px] text-zinc-400 mono ml-auto">
          {filtered.length} of {entries?.length ?? 0} keys
        </span>
      </div>

      <div className="rounded-lg border border-zinc-200 bg-white overflow-hidden">
        <div className="grid grid-cols-[1fr_120px_1fr] gap-3 px-4 py-2 text-[11px] uppercase tracking-wider text-zinc-500 bg-zinc-50 border-b border-zinc-200">
          <div>Key</div>
          <div>Layer</div>
          <div>Value</div>
        </div>
        {filtered.length === 0 ? (
          <div className="px-4 py-10 text-center text-sm text-zinc-500">
            No keys match the current filter.
          </div>
        ) : (
          filtered.map((e) => (
            <div
              key={`${e.key}-${e.layer}`}
              className="grid grid-cols-[1fr_120px_1fr] gap-3 px-4 py-2 items-center border-b border-zinc-100 last:border-0"
              data-testid="settings-effective-row"
            >
              <div className="text-xs text-zinc-900 mono truncate" title={e.key}>
                {e.key}
              </div>
              <div>
                <span
                  className={`text-[10px] mono px-1.5 py-0.5 rounded ${LAYER_TONE[e.layer] ?? "bg-zinc-100 text-zinc-600"}`}
                  title={e.scope_id ? `scope_id: ${e.scope_id}` : undefined}
                >
                  {e.layer}
                </span>
              </div>
              <div className="text-xs text-zinc-700 mono truncate" title={JSON.stringify(e.value)}>
                {formatValue(e.value)}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Overrides tab
// ---------------------------------------------------------------------------

function OverridesTab() {
  const [overrides, setOverrides] = useState<OverrideOut[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);

  async function reload() {
    setLoading(true);
    try {
      const o = await listOverrides();
      setOverrides(o);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { void reload(); }, []);

  if (loading && !overrides) {
    return (
      <div className="flex items-center justify-center py-20 text-zinc-400">
        <Loader2 className="w-5 h-5 animate-spin" />
      </div>
    );
  }

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold text-zinc-900">Runtime overrides</h2>
          <p className="text-sm text-zinc-500 mt-0.5">
            Active overrides shadow the YAML defaults at runtime. Revoking
            keeps the row for audit but flips `active=false`.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setCreating(true)}
          disabled={creating}
          className="px-3 py-1.5 rounded-md bg-zinc-900 text-white text-xs hover:bg-zinc-700 cursor-pointer disabled:opacity-50"
          data-testid="settings-overrides-new"
        >
          + New override
        </button>
      </div>

      {err && <ErrorBanner msg={err} />}

      {creating && (
        <NewOverrideForm
          onSaved={async () => { setCreating(false); await reload(); }}
          onCancel={() => setCreating(false)}
        />
      )}

      <div className="rounded-lg border border-zinc-200 bg-white overflow-hidden">
        <div className="grid grid-cols-[140px_1fr_1fr_120px_60px] gap-3 px-4 py-2 text-[11px] uppercase tracking-wider text-zinc-500 bg-zinc-50 border-b border-zinc-200">
          <div>Scope</div>
          <div>Key</div>
          <div>Value</div>
          <div>Set at</div>
          <div></div>
        </div>
        {(!overrides || overrides.length === 0) ? (
          <div className="px-4 py-10 text-center text-sm text-zinc-500">
            No active overrides — the system is running on YAML defaults.
          </div>
        ) : (
          overrides.map((o) => (
            <OverrideRow key={o.id} o={o} onRevoked={reload} />
          ))
        )}
      </div>
    </div>
  );
}


function OverrideRow({
  o,
  onRevoked,
}: {
  o: OverrideOut;
  onRevoked: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function revoke() {
    setBusy(true);
    setErr(null);
    try {
      await revokeOverride({
        scope_kind: o.scope_kind,
        scope_id: o.scope_id ?? "",
        config_key: o.config_key,
      });
      await onRevoked();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  return (
    <div
      className="grid grid-cols-[140px_1fr_1fr_120px_60px] gap-3 px-4 py-2 items-center border-b border-zinc-100 last:border-0"
      data-testid="settings-override-row"
    >
      <div className="text-[11px] mono text-zinc-700 truncate" title={o.scope_id ?? ""}>
        <span className="text-zinc-400">{o.scope_kind}</span>
        {o.scope_id && <span> · {o.scope_id.slice(0, 8)}…</span>}
      </div>
      <div className="text-xs text-zinc-900 mono truncate" title={o.config_key}>
        {o.config_key}
      </div>
      <div className="text-xs text-zinc-700 mono truncate" title={JSON.stringify(o.config_value)}>
        {formatValue(o.config_value)}
      </div>
      <div className="text-[11px] text-zinc-500 mono truncate" title={o.set_at}>
        {new Date(o.set_at).toLocaleDateString()}
      </div>
      <div className="text-right">
        {err ? (
          <span className="text-red-600 text-[10px] mono" title={err}>err</span>
        ) : (
          <button
            type="button"
            onClick={revoke}
            disabled={busy}
            className="text-red-600 hover:bg-red-50 rounded p-1 cursor-pointer disabled:opacity-50"
            data-testid="settings-override-revoke"
            aria-label={`Revoke override ${o.config_key}`}
          >
            {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
          </button>
        )}
      </div>
    </div>
  );
}


function NewOverrideForm({
  onSaved,
  onCancel,
}: {
  onSaved: () => Promise<void>;
  onCancel: () => void;
}) {
  const [scopeKind, setScopeKind] = useState("workspace");
  const [scopeId, setScopeId] = useState("");
  const [key, setKey] = useState("");
  const [value, setValue] = useState("");
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState(false);

  async function save() {
    setSaving(true);
    setErr(null);
    setOk(false);
    let parsedValue: unknown;
    try {
      // Allow raw JSON for typed values; fall back to string.
      parsedValue = JSON.parse(value);
    } catch {
      parsedValue = value;
    }
    try {
      await createOverride({
        scope_kind: scopeKind,
        scope_id: scopeId,
        config_key: key,
        config_value: parsedValue,
        reason: reason || undefined,
      });
      setOk(true);
      await onSaved();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      className="mb-4 rounded-lg border border-zinc-300 bg-white p-4"
      data-testid="settings-override-form"
    >
      <div className="grid grid-cols-2 gap-3 mb-3">
        <Field label="Scope kind" hint="workspace | domain | doc_type | doc | user">
          <select
            value={scopeKind}
            onChange={(e) => setScopeKind(e.target.value)}
            className="w-full text-xs px-2 py-1.5 rounded border border-zinc-200 bg-white mono cursor-pointer"
          >
            {["workspace", "domain", "doc_type", "doc", "user"].map((k) => (
              <option key={k} value={k}>{k}</option>
            ))}
          </select>
        </Field>
        <Field label="Scope id" hint="UUID / doc_type name / etc. — blank ok for workspace">
          <input
            value={scopeId}
            onChange={(e) => setScopeId(e.target.value)}
            placeholder="optional"
            className="w-full text-xs px-2 py-1.5 rounded border border-zinc-200 mono"
          />
        </Field>
        <Field label="Config key" hint="e.g. models.embedder · planner.crag_threshold">
          <input
            value={key}
            onChange={(e) => setKey(e.target.value)}
            className="w-full text-xs px-2 py-1.5 rounded border border-zinc-200 mono"
            data-testid="override-key"
          />
        </Field>
        <Field label="Value (JSON or string)" hint='e.g. 0.5 · "gemini-2.5-flash" · {"foo": 1}'>
          <input
            value={value}
            onChange={(e) => setValue(e.target.value)}
            className="w-full text-xs px-2 py-1.5 rounded border border-zinc-200 mono"
            data-testid="override-value"
          />
        </Field>
        <Field label="Reason" hint="Audit trail — why this override was set">
          <input
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="optional"
            className="w-full text-xs px-2 py-1.5 rounded border border-zinc-200"
          />
        </Field>
      </div>
      {err && (
        <div className="mb-3 text-xs text-red-700 flex items-center gap-1.5">
          <AlertCircle className="w-3.5 h-3.5" /> {err}
        </div>
      )}
      {ok && (
        <div className="mb-3 text-xs text-emerald-700 flex items-center gap-1.5">
          <CheckCircle2 className="w-3.5 h-3.5" /> Saved
        </div>
      )}
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={save}
          disabled={saving || !key}
          className="px-3 py-1.5 rounded-md bg-zinc-900 text-white text-xs hover:bg-zinc-700 cursor-pointer disabled:opacity-50"
          data-testid="settings-override-save"
        >
          {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin inline mr-1" /> : null}
          Save override
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="px-3 py-1.5 rounded-md text-xs text-zinc-600 hover:bg-zinc-100 cursor-pointer"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}


function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wider text-zinc-400 mb-1">
        {label}
      </div>
      {children}
      <div className="text-[10px] text-zinc-400 mt-0.5">{hint}</div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

function ErrorBanner({ msg }: { msg: string }) {
  return (
    <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
      Failed to load: <span className="mono">{msg}</span>
    </div>
  );
}


function formatValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}
