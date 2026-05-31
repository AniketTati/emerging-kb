"use client";

/**
 * P1b — "define a domain schema from scratch" wizard + "load a domain schema"
 * importer. Both funnel through the single atomic POST /schemas/import.yaml
 * endpoint (P3): the wizard builds a SchemaImportDoc in local state and POSTs
 * it as JSON; the loader POSTs raw YAML pasted or read from a .yaml file.
 *
 * Rendered as two header buttons via <SchemaCreateActions/>. Self-contained
 * (its own centered modal) so it doesn't touch the large page.tsx component.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  X, Plus, Trash2, Loader2, ArrowLeft, ArrowRight, Check,
  UploadCloud, Sparkles, FileText, History,
} from "lucide-react";

import {
  importSchemaDoc, importSchemaYaml, listSchemas, listSchemaVersions,
  type SchemaImportDoc, type SchemaImportEntity, type SchemaImportField,
  type SchemaImportRelationship, type SchemaImportResponse,
  type SchemaSummary, type SchemaVersionRow,
} from "@/lib/api";

const FIELD_TYPES = ["string", "number", "boolean", "date", "datetime"] as const;
const REL_KINDS = [
  "contains", "part_of", "references", "associates", "attribute_link",
] as const;
const CARDINALITIES = ["one_to_one", "one_to_many", "many_to_many"] as const;


// ---------------------------------------------------------------------------
// Centered modal shell (the page uses a right SidePanel for details; a wizard
// reads better centered). Esc + backdrop close.
// ---------------------------------------------------------------------------

function Modal({
  open, onClose, title, subtitle, children, footer, wide,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
  wide?: boolean;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <>
      <div className="fixed inset-0 bg-zinc-900/30 z-40" onClick={onClose} aria-hidden />
      <div
        className={`fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 z-50 w-full ${
          wide ? "max-w-[720px]" : "max-w-[560px]"
        } max-h-[88vh] bg-white rounded-lg border border-zinc-200 shadow-2xl flex flex-col`}
        role="dialog"
        aria-modal
        data-testid="schema-wizard-modal"
      >
        <header className="flex-shrink-0 border-b border-zinc-200 flex items-center px-5 h-12 gap-3">
          <div className="min-w-0 flex-1">
            <div className="text-sm font-medium text-zinc-900 truncate">{title}</div>
            {subtitle && <div className="text-[11px] text-zinc-500 truncate">{subtitle}</div>}
          </div>
          <button type="button" onClick={onClose}
            className="p-1 rounded hover:bg-zinc-100 cursor-pointer" aria-label="Close">
            <X className="w-4 h-4 text-zinc-500" />
          </button>
        </header>
        <div className="flex-1 overflow-y-auto px-5 py-4">{children}</div>
        {footer && (
          <footer className="flex-shrink-0 border-t border-zinc-200 px-5 py-3 flex items-center gap-2">
            {footer}
          </footer>
        )}
      </div>
    </>
  );
}


function ErrBox({ msg }: { msg: string }) {
  return (
    <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-[12px] text-red-700 whitespace-pre-wrap">
      {msg}
    </div>
  );
}

function ResultBox({ result }: { result: SchemaImportResponse }) {
  return (
    <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-[12px] text-emerald-800 space-y-1">
      <div className="font-medium flex items-center gap-1.5">
        <Check className="w-3.5 h-3.5" /> Imported {result.imported.length} schema
        {result.imported.length === 1 ? "" : "s"}
      </div>
      {result.imported.map((it) => (
        <div key={it.schema_id} className="mono text-[11px]">
          [{it.action}] {it.name} v{it.current_version} — {it.entities} entities,{" "}
          {it.fields} fields, {it.relationships} relationships
        </div>
      ))}
    </div>
  );
}

const btnPrimary =
  "flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md bg-zinc-900 text-white hover:bg-zinc-700 disabled:opacity-50 cursor-pointer";
const btnGhost =
  "flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md text-zinc-600 hover:bg-zinc-100 disabled:opacity-50 cursor-pointer";
const inputCls =
  "w-full px-2.5 py-1.5 text-xs rounded-md border border-zinc-200 focus:border-zinc-400 focus:outline-none";


// ===========================================================================
// Header actions — two buttons + the two modals
// ===========================================================================

export function SchemaCreateActions({ onChanged }: { onChanged?: () => void }) {
  const [wizardOpen, setWizardOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [versionsOpen, setVersionsOpen] = useState(false);

  return (
    <>
      <button
        type="button"
        onClick={() => setVersionsOpen(true)}
        className="flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-md text-zinc-600 hover:text-zinc-900 hover:bg-zinc-100 cursor-pointer"
        title="View schema version history"
        data-testid="schema-versions-btn"
      >
        <History className="w-3.5 h-3.5" strokeWidth={1.75} /> Versions
      </button>
      <button
        type="button"
        onClick={() => setImportOpen(true)}
        className="flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-md text-zinc-600 hover:text-zinc-900 hover:bg-zinc-100 cursor-pointer"
        title="Load a domain schema from a YAML file"
        data-testid="schema-import-btn"
      >
        <UploadCloud className="w-3.5 h-3.5" strokeWidth={1.75} /> Import YAML
      </button>
      <button
        type="button"
        onClick={() => setWizardOpen(true)}
        className="flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-md bg-zinc-900 text-white hover:bg-zinc-700 cursor-pointer"
        title="Define a new domain schema from scratch"
        data-testid="schema-new-btn"
      >
        <Sparkles className="w-3.5 h-3.5" strokeWidth={1.75} /> New schema
      </button>

      <SchemaWizard
        open={wizardOpen}
        onClose={() => setWizardOpen(false)}
        onChanged={onChanged}
      />
      <SchemaImportModal
        open={importOpen}
        onClose={() => setImportOpen(false)}
        onChanged={onChanged}
      />
      <SchemaVersionsModal
        open={versionsOpen}
        onClose={() => setVersionsOpen(false)}
      />
    </>
  );
}


// ===========================================================================
// Schema version history (P6b) — list typed schemas, drill into versions
// ===========================================================================

function SchemaVersionsModal({
  open, onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const [schemas, setSchemas] = useState<SchemaSummary[] | null>(null);
  const [selected, setSelected] = useState<SchemaSummary | null>(null);
  const [versions, setVersions] = useState<SchemaVersionRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loadingVersions, setLoadingVersions] = useState(false);

  // Load the schema list whenever the modal opens.
  useEffect(() => {
    if (!open) return;
    setErr(null); setSelected(null); setVersions(null); setSchemas(null);
    listSchemas()
      .then(setSchemas)
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, [open]);

  const pick = useCallback(async (s: SchemaSummary) => {
    setSelected(s); setVersions(null); setErr(null); setLoadingVersions(true);
    try {
      setVersions(await listSchemaVersions(s.id));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoadingVersions(false);
    }
  }, []);

  return (
    <Modal
      open={open}
      onClose={onClose}
      wide
      title="Schema version history"
      subtitle="Every schema edit, import or rollback records an immutable version"
    >
      {err && <ErrBox msg={err} />}
      {!err && schemas === null && (
        <div className="flex items-center gap-2 text-[12px] text-zinc-400">
          <Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading schemas…
        </div>
      )}
      {schemas !== null && schemas.length === 0 && (
        <div className="text-[12px] text-zinc-400">No schemas yet. Create one with “New schema”.</div>
      )}
      {schemas !== null && schemas.length > 0 && (
        <div className="grid grid-cols-[200px_1fr] gap-4">
          {/* Schema list */}
          <div className="space-y-1 border-r border-zinc-100 pr-3">
            {schemas.map((s) => (
              <button
                key={s.id}
                type="button"
                onClick={() => pick(s)}
                className={`w-full text-left px-2 py-1.5 rounded-md text-xs cursor-pointer ${
                  selected?.id === s.id ? "bg-zinc-900 text-white" : "hover:bg-zinc-100 text-zinc-700"
                }`}
                data-testid="schema-versions-schema"
              >
                <div className="truncate font-medium">{s.name}</div>
                {s.current_version !== undefined && (
                  <div className={`mono text-[10px] ${selected?.id === s.id ? "text-zinc-300" : "text-zinc-400"}`}>
                    v{s.current_version}
                  </div>
                )}
              </button>
            ))}
          </div>

          {/* Version timeline for the selected schema */}
          <div className="min-w-0">
            {!selected && (
              <div className="text-[12px] text-zinc-400 pt-2">
                Select a schema to see its version history.
              </div>
            )}
            {selected && loadingVersions && (
              <div className="flex items-center gap-2 text-[12px] text-zinc-400">
                <Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading versions…
              </div>
            )}
            {selected && versions !== null && (
              <ol className="space-y-1.5" data-testid="schema-versions-list">
                {versions.length === 0 && (
                  <li className="text-[12px] text-zinc-400">No versions recorded.</li>
                )}
                {versions.map((v) => (
                  <li key={v.version} className="flex items-baseline gap-2 text-[12px]">
                    <span className="mono font-medium text-zinc-900 w-10 flex-shrink-0">v{v.version}</span>
                    <span className="px-1.5 py-0.5 rounded text-[10px] mono bg-zinc-100 text-zinc-600">
                      {v.kind ?? "edit"}
                    </span>
                    {v.parent_version != null && (
                      <span className="mono text-[10px] text-zinc-400">← v{v.parent_version}</span>
                    )}
                    <span className="ml-auto text-[11px] text-zinc-400 mono truncate">
                      {v.created_at ? new Date(v.created_at).toLocaleString() : ""}
                    </span>
                  </li>
                ))}
              </ol>
            )}
          </div>
        </div>
      )}
    </Modal>
  );
}


// ===========================================================================
// Import-a-file modal (paste YAML or upload .yaml)
// ===========================================================================

function SchemaImportModal({
  open, onClose, onChanged,
}: {
  open: boolean;
  onClose: () => void;
  onChanged?: () => void;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<SchemaImportResponse | null>(null);

  const reset = useCallback(() => {
    setText(""); setErr(null); setResult(null); setBusy(false);
  }, []);

  const close = useCallback(() => { reset(); onClose(); }, [reset, onClose]);

  async function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setText(await file.text());
    setErr(null); setResult(null);
  }

  async function submit() {
    setBusy(true); setErr(null); setResult(null);
    try {
      const res = await importSchemaYaml(text);
      setResult(res);
      onChanged?.();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={close}
      title="Import a domain schema"
      subtitle="Paste or upload a schema YAML — upserts schemas, entities, fields & relationships in one step"
      footer={
        <>
          <label className={btnGhost + " relative overflow-hidden"}>
            <FileText className="w-3.5 h-3.5" /> Choose .yaml file
            <input
              type="file"
              accept=".yaml,.yml,application/x-yaml,text/yaml"
              onChange={onFile}
              className="absolute inset-0 opacity-0 cursor-pointer"
              data-testid="schema-import-file"
            />
          </label>
          <div className="ml-auto flex items-center gap-2">
            {result
              ? <button type="button" onClick={close} className={btnPrimary}>Done</button>
              : (
                <button
                  type="button"
                  onClick={submit}
                  disabled={busy || !text.trim()}
                  className={btnPrimary}
                  data-testid="schema-import-submit"
                >
                  {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <UploadCloud className="w-3.5 h-3.5" />}
                  Load schema
                </button>
              )}
          </div>
        </>
      }
    >
      <div className="space-y-3">
        <textarea
          value={text}
          onChange={(e) => { setText(e.target.value); setResult(null); }}
          placeholder={"schemas:\n  - name: Finance\n    entities:\n      - name: LoanAgreement\n        fields:\n          - name: principal\n            type: number"}
          spellCheck={false}
          className="w-full h-56 px-3 py-2 text-[12px] mono rounded-md border border-zinc-200 focus:border-zinc-400 focus:outline-none resize-y"
          data-testid="schema-import-textarea"
        />
        {err && <ErrBox msg={err} />}
        {result && <ResultBox result={result} />}
      </div>
    </Modal>
  );
}


// ===========================================================================
// From-scratch wizard
// ===========================================================================

type WizField = { name: string; type: SchemaImportField["type"]; description: string; required: boolean };
type WizEntity = { name: string; description: string; fields: WizField[] };
type WizRel = {
  name: string; from: string; to: string;
  kind: SchemaImportRelationship["kind"];
  cardinality: SchemaImportRelationship["cardinality"];
};

type Step = 0 | 1 | 2 | 3; // basics → entities+fields → relationships → review
const STEP_LABELS = ["Schema", "Entities & fields", "Relationships", "Review"];

function SchemaWizard({
  open, onClose, onChanged,
}: {
  open: boolean;
  onClose: () => void;
  onChanged?: () => void;
}) {
  const [step, setStep] = useState<Step>(0);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [entities, setEntities] = useState<WizEntity[]>([]);
  const [rels, setRels] = useState<WizRel[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<SchemaImportResponse | null>(null);

  const reset = useCallback(() => {
    setStep(0); setName(""); setDescription("");
    setEntities([]); setRels([]); setBusy(false); setErr(null); setResult(null);
  }, []);
  const close = useCallback(() => { reset(); onClose(); }, [reset, onClose]);

  const entityNames = useMemo(
    () => entities.map((e) => e.name.trim()).filter(Boolean),
    [entities],
  );

  // --- entity/field/rel mutators ------------------------------------------
  const addEntity = () =>
    setEntities((es) => [...es, { name: "", description: "", fields: [] }]);
  const updEntity = (i: number, patch: Partial<WizEntity>) =>
    setEntities((es) => es.map((e, j) => (j === i ? { ...e, ...patch } : e)));
  const delEntity = (i: number) =>
    setEntities((es) => es.filter((_, j) => j !== i));
  const addField = (ei: number) =>
    setEntities((es) => es.map((e, j) => j === ei
      ? { ...e, fields: [...e.fields, { name: "", type: "string", description: "", required: false }] }
      : e));
  const updField = (ei: number, fi: number, patch: Partial<WizField>) =>
    setEntities((es) => es.map((e, j) => j === ei
      ? { ...e, fields: e.fields.map((f, k) => (k === fi ? { ...f, ...patch } : f)) }
      : e));
  const delField = (ei: number, fi: number) =>
    setEntities((es) => es.map((e, j) => j === ei
      ? { ...e, fields: e.fields.filter((_, k) => k !== fi) }
      : e));

  const addRel = () =>
    setRels((rs) => [...rs, {
      name: "", from: entityNames[0] ?? "", to: entityNames[0] ?? "",
      kind: "references", cardinality: "one_to_many",
    }]);
  const updRel = (i: number, patch: Partial<WizRel>) =>
    setRels((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const delRel = (i: number) => setRels((rs) => rs.filter((_, j) => j !== i));

  // --- validation per step ------------------------------------------------
  const stepValid = useMemo(() => {
    if (step === 0) return name.trim().length > 0;
    if (step === 1) {
      if (entities.length === 0) return false;
      if (entities.some((e) => !e.name.trim())) return false;
      // duplicate entity names
      if (new Set(entityNames).size !== entityNames.length) return false;
      if (entities.some((e) => e.fields.some((f) => !f.name.trim()))) return false;
      return true;
    }
    if (step === 2) {
      return rels.every((r) =>
        r.name.trim() && entityNames.includes(r.from) && entityNames.includes(r.to));
    }
    return true;
  }, [step, name, entities, entityNames, rels]);

  function buildDoc(): SchemaImportDoc {
    const ents: SchemaImportEntity[] = entities.map((e) => ({
      name: e.name.trim(),
      description: e.description.trim() || undefined,
      fields: e.fields.map((f) => ({
        name: f.name.trim(),
        type: f.type,
        description: f.description.trim() || undefined,
        required: f.required || undefined,
      })),
    }));
    const relationships: SchemaImportRelationship[] = rels.map((r) => ({
      name: r.name.trim(), from: r.from, to: r.to,
      kind: r.kind, cardinality: r.cardinality,
    }));
    return {
      schemas: [{
        name: name.trim(),
        description: description.trim() || undefined,
        entities: ents,
        relationships,
      }],
    };
  }

  async function submit() {
    setBusy(true); setErr(null);
    try {
      const res = await importSchemaDoc(buildDoc());
      setResult(res);
      onChanged?.();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const totalFields = entities.reduce((n, e) => n + e.fields.length, 0);

  return (
    <Modal
      open={open}
      onClose={close}
      wide
      title="Define a domain schema"
      subtitle={STEP_LABELS.map((l, i) => `${i === step ? "● " : ""}${l}`).join("   ")}
      footer={
        result ? (
          <button type="button" onClick={close} className={btnPrimary + " ml-auto"}>Done</button>
        ) : (
          <>
            <button
              type="button"
              onClick={() => setStep((s) => (s > 0 ? ((s - 1) as Step) : s))}
              disabled={step === 0 || busy}
              className={btnGhost}
            >
              <ArrowLeft className="w-3.5 h-3.5" /> Back
            </button>
            <div className="ml-auto flex items-center gap-2">
              {step < 3 ? (
                <button
                  type="button"
                  onClick={() => setStep((s) => ((s + 1) as Step))}
                  disabled={!stepValid}
                  className={btnPrimary}
                  data-testid="wizard-next"
                >
                  Next <ArrowRight className="w-3.5 h-3.5" />
                </button>
              ) : (
                <button
                  type="button"
                  onClick={submit}
                  disabled={busy || !name.trim() || entities.length === 0}
                  className={btnPrimary}
                  data-testid="wizard-create"
                >
                  {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
                  Create schema
                </button>
              )}
            </div>
          </>
        )
      }
    >
      {result ? (
        <ResultBox result={result} />
      ) : (
        <div className="space-y-4">
          {/* Step 0 — basics */}
          {step === 0 && (
            <div className="space-y-3">
              <label className="block">
                <div className="text-[11px] text-zinc-500 mb-1">Schema name *</div>
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Finance"
                  className={inputCls}
                  data-testid="wizard-schema-name"
                  autoFocus
                />
              </label>
              <label className="block">
                <div className="text-[11px] text-zinc-500 mb-1">Description</div>
                <textarea
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="What this domain covers…"
                  className={inputCls + " h-20 resize-y"}
                />
              </label>
            </div>
          )}

          {/* Step 1 — entities & fields */}
          {step === 1 && (
            <div className="space-y-3">
              {entities.length === 0 && (
                <div className="text-[12px] text-zinc-400 py-2">
                  No entity types yet. Add the document/record types this domain has
                  (e.g. LoanAgreement, BankStatement).
                </div>
              )}
              {entities.map((ent, ei) => (
                <div key={ei} className="rounded-md border border-zinc-200 p-3 space-y-2">
                  <div className="flex items-center gap-2">
                    <input
                      value={ent.name}
                      onChange={(e) => updEntity(ei, { name: e.target.value })}
                      placeholder="EntityType name"
                      className={inputCls + " font-medium"}
                      data-testid={`wizard-entity-name-${ei}`}
                    />
                    <button type="button" onClick={() => delEntity(ei)}
                      className="p-1 rounded hover:bg-zinc-100 text-zinc-400 hover:text-red-600 cursor-pointer"
                      aria-label="Remove entity">
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                  <input
                    value={ent.description}
                    onChange={(e) => updEntity(ei, { description: e.target.value })}
                    placeholder="Description (optional)"
                    className={inputCls}
                  />
                  {/* fields */}
                  <div className="space-y-1.5 pl-2 border-l-2 border-zinc-100">
                    {ent.fields.map((f, fi) => (
                      <div key={fi} className="flex items-center gap-1.5">
                        <input
                          value={f.name}
                          onChange={(e) => updField(ei, fi, { name: e.target.value })}
                          placeholder="field_name"
                          className={inputCls}
                          data-testid={`wizard-field-name-${ei}-${fi}`}
                        />
                        <select
                          value={f.type}
                          onChange={(e) => updField(ei, fi, { type: e.target.value as WizField["type"] })}
                          className="px-1.5 py-1.5 text-xs rounded-md border border-zinc-200 bg-white cursor-pointer"
                        >
                          {FIELD_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                        </select>
                        <label className="flex items-center gap-1 text-[11px] text-zinc-500 whitespace-nowrap">
                          <input
                            type="checkbox"
                            checked={f.required}
                            onChange={(e) => updField(ei, fi, { required: e.target.checked })}
                          /> req
                        </label>
                        <button type="button" onClick={() => delField(ei, fi)}
                          className="p-1 rounded hover:bg-zinc-100 text-zinc-400 hover:text-red-600 cursor-pointer"
                          aria-label="Remove field">
                          <Trash2 className="w-3 h-3" />
                        </button>
                      </div>
                    ))}
                    <button type="button" onClick={() => addField(ei)}
                      className="flex items-center gap-1 text-[11px] text-zinc-500 hover:text-zinc-900 cursor-pointer">
                      <Plus className="w-3 h-3" /> field
                    </button>
                  </div>
                </div>
              ))}
              <button type="button" onClick={addEntity} className={btnGhost} data-testid="wizard-add-entity">
                <Plus className="w-3.5 h-3.5" /> Add entity
              </button>
            </div>
          )}

          {/* Step 2 — relationships */}
          {step === 2 && (
            <div className="space-y-3">
              {entityNames.length < 1 ? (
                <div className="text-[12px] text-zinc-400">Add entities first.</div>
              ) : (
                <>
                  {rels.length === 0 && (
                    <div className="text-[12px] text-zinc-400 py-2">
                      Relationships are optional. Add typed edges between entity types
                      (e.g. LoanAgreement references Organization).
                    </div>
                  )}
                  {rels.map((r, i) => (
                    <div key={i} className="rounded-md border border-zinc-200 p-3 space-y-2">
                      <div className="flex items-center gap-2">
                        <input
                          value={r.name}
                          onChange={(e) => updRel(i, { name: e.target.value })}
                          placeholder="relationship_name"
                          className={inputCls + " font-medium"}
                          data-testid={`wizard-rel-name-${i}`}
                        />
                        <button type="button" onClick={() => delRel(i)}
                          className="p-1 rounded hover:bg-zinc-100 text-zinc-400 hover:text-red-600 cursor-pointer"
                          aria-label="Remove relationship">
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                      <div className="grid grid-cols-2 gap-1.5">
                        <select value={r.from} onChange={(e) => updRel(i, { from: e.target.value })}
                          className="px-2 py-1.5 text-xs rounded-md border border-zinc-200 bg-white cursor-pointer">
                          {entityNames.map((n) => <option key={n} value={n}>{n}</option>)}
                        </select>
                        <select value={r.to} onChange={(e) => updRel(i, { to: e.target.value })}
                          className="px-2 py-1.5 text-xs rounded-md border border-zinc-200 bg-white cursor-pointer">
                          {entityNames.map((n) => <option key={n} value={n}>{n}</option>)}
                        </select>
                        <select value={r.kind} onChange={(e) => updRel(i, { kind: e.target.value as WizRel["kind"] })}
                          className="px-2 py-1.5 text-xs rounded-md border border-zinc-200 bg-white cursor-pointer">
                          {REL_KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
                        </select>
                        <select value={r.cardinality} onChange={(e) => updRel(i, { cardinality: e.target.value as WizRel["cardinality"] })}
                          className="px-2 py-1.5 text-xs rounded-md border border-zinc-200 bg-white cursor-pointer">
                          {CARDINALITIES.map((c) => <option key={c} value={c}>{c}</option>)}
                        </select>
                      </div>
                    </div>
                  ))}
                  <button type="button" onClick={addRel} className={btnGhost} data-testid="wizard-add-rel">
                    <Plus className="w-3.5 h-3.5" /> Add relationship
                  </button>
                </>
              )}
            </div>
          )}

          {/* Step 3 — review */}
          {step === 3 && (
            <div className="space-y-3 text-[12px]">
              <div className="rounded-md border border-zinc-200 p-3">
                <div className="font-medium text-zinc-900">{name.trim() || "(unnamed)"}</div>
                {description.trim() && <div className="text-zinc-500 mt-0.5">{description.trim()}</div>}
                <div className="mono text-[11px] text-zinc-500 mt-2">
                  {entities.length} entities · {totalFields} fields · {rels.length} relationships
                </div>
              </div>
              {entities.map((e, i) => (
                <div key={i} className="pl-2 border-l-2 border-zinc-100">
                  <div className="font-medium text-zinc-800">{e.name.trim()}</div>
                  <div className="mono text-[11px] text-zinc-500">
                    {e.fields.length === 0 ? "no fields" : e.fields.map((f) =>
                      `${f.name.trim()}:${f.type}${f.required ? "*" : ""}`).join("  ")}
                  </div>
                </div>
              ))}
              {err && <ErrBox msg={err} />}
            </div>
          )}

          {step !== 3 && err && <ErrBox msg={err} />}
        </div>
      )}
    </Modal>
  );
}
