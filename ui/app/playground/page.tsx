"use client";

/**
 * /playground — query sandbox.
 *
 * Two modes via ?tab=:
 *   - sandbox  → single one-off query against the live pipeline; full
 *                response panel with citations + verdict + inspector.
 *                Bypasses chat-history (not saved to a session).
 *   - compare  → A/B same query against two URL-bar-overridable model
 *                choices, rendered side-by-side. Useful before flipping
 *                a model in the runtime overrides.
 *
 * Eval-suite runner is a roadmap item — needs a `/eval` endpoint that
 * doesn't exist yet. A clear "coming soon" panel keeps it visible.
 *
 * Sandbox responses use the existing /chat surface but with
 * `chat_history_enabled=false` semantics — actually, the backend
 * doesn't honor that flag (it always logs to query_log). We just
 * don't thread a session_id, so the row lands as a one-off in the
 * audit log without polluting any chat session.
 */

import { Suspense, useState } from "react";
import { useRouter, useSearchParams, usePathname } from "next/navigation";
import {
  FlaskConical, GitCompare, Beaker, Send, Loader2,
  CheckCircle2, AlertCircle,
} from "lucide-react";
import {
  postChat,
  type ChatResponse,
} from "@/lib/api";
import { Sidebar } from "@/components/Sidebar";


type Tab = "sandbox" | "compare" | "eval";


export default function PlaygroundPage() {
  return (
    <Suspense fallback={null}>
      <PlaygroundShell />
    </Suspense>
  );
}


function PlaygroundShell() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const tab = ((["sandbox", "compare", "eval"] as Tab[]).includes(
    (searchParams.get("tab") ?? "sandbox") as Tab,
  )
    ? (searchParams.get("tab") ?? "sandbox")
    : "sandbox") as Tab;

  function setTab(next: Tab) {
    const sp = new URLSearchParams(searchParams.toString());
    if (next === "sandbox") sp.delete("tab");
    else sp.set("tab", next);
    const qs = sp.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  }

  return (
    <div className="flex h-full">
      <Sidebar current="playground" />
      <main className="flex-1 flex flex-col min-w-0 bg-zinc-50/40">
        <header className="h-12 flex-shrink-0 border-b border-zinc-200 flex items-center px-5 gap-3 bg-white">
          <FlaskConical className="w-4 h-4 text-zinc-500" strokeWidth={1.75} />
          <span className="text-sm text-zinc-900">Playground</span>
          <span className="text-[11px] text-zinc-400 mono">
            sandbox · not saved to chat history
          </span>
        </header>

        <div className="border-b border-zinc-200 px-8 flex gap-1 bg-white">
          {([
            { key: "sandbox", label: "Sandbox",        icon: FlaskConical },
            { key: "compare", label: "Compare configs", icon: GitCompare },
            { key: "eval",    label: "Eval suite",     icon: Beaker },
          ] as { key: Tab; label: string; icon: typeof FlaskConical }[]).map((t) => {
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
                data-testid={`pg-tab-${t.key}`}
                data-active={active || undefined}
              >
                <Icon className="w-3.5 h-3.5" strokeWidth={1.75} />
                {t.label}
              </button>
            );
          })}
        </div>

        <div className="flex-1 overflow-y-auto">
          <div className="max-w-6xl mx-auto px-8 py-6">
            {tab === "sandbox" && <SandboxTab />}
            {tab === "compare" && <CompareTab />}
            {tab === "eval" && <EvalTab />}
          </div>
        </div>
      </main>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Sandbox tab — one-off query with full response inspector
// ---------------------------------------------------------------------------

// 13 actual planner modes the backend accepts (src/kb/api/query.py).
const MODE_OPTIONS: { value: string; label: string }[] = [
  { value: "H", label: "H — hybrid (default)" },
  { value: "E", label: "E — entity lookup" },
  { value: "F", label: "F — find/filter" },
  { value: "S", label: "S — summarize" },
  { value: "T", label: "T — HippoRAG traversal" },
  { value: "M", label: "M — metadata/structured" },
  { value: "G", label: "G — global/corpus RAPTOR" },
  { value: "D", label: "D — deep multi-hop" },
  { value: "C", label: "C — compare/contrast" },
  { value: "A", label: "A — aggregate" },
  { value: "Q", label: "Q — SQL aggregation" },
  { value: "K", label: "K — doc-chain aware" },
  { value: "I", label: "I — inventory listing" },
];


function SandboxTab() {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState("H");
  const [busy, setBusy] = useState(false);
  const [response, setResponse] = useState<ChatResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!query.trim() || busy) return;
    setBusy(true);
    setErr(null);
    setResponse(null);
    try {
      // No sessionId → server auto-creates a one-off session, so this
      // doesn't pollute the chat-history sidebar.
      const r = await postChat(query, { mode });
      setResponse(r);
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : String(e2));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <form onSubmit={submit} className="rounded-lg border border-zinc-200 bg-white p-4">
        <label className="text-[11px] uppercase tracking-wider text-zinc-400 mb-1.5 block">
          Query
        </label>
        <textarea
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Ask anything — runs the full 6-channel pipeline + CRAG + faithfulness gate."
          rows={3}
          className="w-full text-sm rounded border border-zinc-200 px-3 py-2 mono focus:outline-none focus:border-zinc-400 resize-none"
          data-testid="pg-sandbox-query"
        />
        <div className="mt-3 flex items-center gap-4">
          <label className="flex items-center gap-2 text-xs text-zinc-700">
            Force mode:
            <select
              value={mode}
              onChange={(e) => setMode(e.target.value)}
              className="text-xs px-2 py-1 rounded border border-zinc-200 mono cursor-pointer"
              data-testid="pg-sandbox-mode"
            >
              {MODE_OPTIONS.map((m) => (
                <option key={m.value} value={m.value}>{m.label}</option>
              ))}
            </select>
          </label>
          <button
            type="submit"
            disabled={!query.trim() || busy}
            className="ml-auto flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-zinc-900 text-white text-xs hover:bg-zinc-700 cursor-pointer disabled:opacity-50"
            data-testid="pg-sandbox-submit"
          >
            {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Send className="w-3.5 h-3.5" />}
            Run
          </button>
        </div>
      </form>

      {err && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          <AlertCircle className="w-4 h-4 inline mr-1.5" /> {err}
        </div>
      )}

      {response && <ResponsePanel r={response} />}
    </div>
  );
}


// ---------------------------------------------------------------------------
// Compare tab — A/B same query, two configs
// ---------------------------------------------------------------------------

function CompareTab() {
  const [query, setQuery] = useState("");
  const [modeA, setModeA] = useState("H");
  const [modeB, setModeB] = useState("T");
  const [busy, setBusy] = useState(false);
  const [a, setA] = useState<ChatResponse | null>(null);
  const [b, setB] = useState<ChatResponse | null>(null);
  const [aErr, setAErr] = useState<string | null>(null);
  const [bErr, setBErr] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!query.trim() || busy) return;
    setBusy(true);
    setAErr(null); setBErr(null); setA(null); setB(null);

    const aP = postChat(query, { mode: modeA })
      .then(setA).catch((e2) => setAErr(e2 instanceof Error ? e2.message : String(e2)));
    const bP = postChat(query, { mode: modeB })
      .then(setB).catch((e2) => setBErr(e2 instanceof Error ? e2.message : String(e2)));

    await Promise.all([aP, bP]);
    setBusy(false);
  }

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-zinc-200 bg-amber-50/40 px-4 py-3 text-xs text-amber-900">
        <span className="font-medium">A/B testing.</span> Submit the same
        query under two different planner modes side-by-side — useful
        before deciding which mode to bias toward in production. Per-call
        model-id overrides will land when /chat gains an
        <span className="mono"> ?override=</span> query param.
      </div>

      <form onSubmit={submit} className="rounded-lg border border-zinc-200 bg-white p-4">
        <label className="text-[11px] uppercase tracking-wider text-zinc-400 mb-1.5 block">
          Query
        </label>
        <textarea
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Run the same query against both configs side-by-side."
          rows={2}
          className="w-full text-sm rounded border border-zinc-200 px-3 py-2 mono focus:outline-none focus:border-zinc-400 resize-none"
          data-testid="pg-compare-query"
        />
        <div className="mt-3 grid grid-cols-[1fr_auto_1fr_auto] gap-3 items-end">
          <label className="text-xs text-zinc-700 flex flex-col gap-1">
            <span className="text-[11px] uppercase tracking-wider text-zinc-400">Config A — mode</span>
            <select
              value={modeA}
              onChange={(e) => setModeA(e.target.value)}
              className="text-xs px-2 py-1.5 rounded border border-zinc-200 mono cursor-pointer"
              data-testid="pg-compare-mode-a"
            >
              {MODE_OPTIONS.map((m) => (
                <option key={m.value} value={m.value}>{m.label}</option>
              ))}
            </select>
          </label>
          <span className="text-zinc-300 pb-2">vs</span>
          <label className="text-xs text-zinc-700 flex flex-col gap-1">
            <span className="text-[11px] uppercase tracking-wider text-zinc-400">Config B — mode</span>
            <select
              value={modeB}
              onChange={(e) => setModeB(e.target.value)}
              className="text-xs px-2 py-1.5 rounded border border-zinc-200 mono cursor-pointer"
              data-testid="pg-compare-mode-b"
            >
              {MODE_OPTIONS.map((m) => (
                <option key={m.value} value={m.value}>{m.label}</option>
              ))}
            </select>
          </label>
          <button
            type="submit"
            disabled={!query.trim() || busy || modeA === modeB}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-zinc-900 text-white text-xs hover:bg-zinc-700 cursor-pointer disabled:opacity-50"
            data-testid="pg-compare-submit"
            title={modeA === modeB ? "Pick two different modes to compare" : undefined}
          >
            {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <GitCompare className="w-3.5 h-3.5" />}
            Run A + B in parallel
          </button>
        </div>
      </form>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <CompareSlot label={`A · mode ${modeA}`} response={a} err={aErr} busy={busy} testId="pg-compare-a" />
        <CompareSlot label={`B · mode ${modeB}`} response={b} err={bErr} busy={busy} testId="pg-compare-b" />
      </div>
    </div>
  );
}


function CompareSlot({
  label,
  response,
  err,
  busy,
  testId,
}: {
  label: string;
  response: ChatResponse | null;
  err: string | null;
  busy: boolean;
  testId: string;
}) {
  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-4 min-h-[200px]" data-testid={testId}>
      <div className="flex items-center gap-2 mb-3 text-xs">
        <span className="text-[10px] mono px-1.5 py-0.5 rounded bg-zinc-100 text-zinc-700">
          {label}
        </span>
        {response && (
          <span className="ml-auto mono text-[11px] text-zinc-500">
            {response.latency_ms}ms · CRAG {response.crag_score.toFixed(2)}
          </span>
        )}
      </div>
      {busy && !response && !err && (
        <div className="flex items-center justify-center py-10 text-zinc-400">
          <Loader2 className="w-5 h-5 animate-spin" />
        </div>
      )}
      {err && (
        <div className="text-xs text-red-700 mono">{err}</div>
      )}
      {response && (
        <ResponsePanel r={response} compact />
      )}
    </div>
  );
}


// ---------------------------------------------------------------------------
// Eval tab — roadmap placeholder
// ---------------------------------------------------------------------------

function EvalTab() {
  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-zinc-200 bg-white p-6">
        <h2 className="text-base font-semibold text-zinc-900 flex items-center gap-2">
          <Beaker className="w-5 h-5 text-zinc-500" strokeWidth={1.75} />
          Eval suite
        </h2>
        <p className="text-sm text-zinc-600 mt-2 leading-relaxed">
          Pick a curated question set, run it against the current config,
          and inspect per-query CRAG / faithfulness / latency. Lands when
          the backend ships <span className="mono">POST /eval/run</span> +
          <span className="mono"> GET /eval/{`{run_id}`}</span> with stored
          regression runs.
        </p>
        <p className="text-sm text-zinc-600 mt-2 leading-relaxed">
          Today the curated questions live in the repo at
          {" "}
          <a
            href="https://github.com/AniketTati/emerging-kb/tree/main/tests/eval"
            className="mono text-zinc-700 hover:text-zinc-900 underline"
            target="_blank"
            rel="noopener noreferrer"
          >
            tests/eval
          </a>
          {" "}
          and the regression set runs in CI. The interactive runner here
          is the natural follow-on — see{" "}
          <a
            href="https://github.com/AniketTati/emerging-kb/blob/main/docs/build_tracker.md"
            className="text-zinc-700 hover:text-zinc-900 underline"
            target="_blank"
            rel="noopener noreferrer"
          >
            docs/build_tracker.md
          </a>{" "}
          for the roadmap.
        </p>
      </div>

      <div className="rounded-lg border border-zinc-200 bg-white p-6">
        <h3 className="text-sm font-semibold text-zinc-900 mb-3">
          What's already running in CI
        </h3>
        <ul className="text-sm text-zinc-700 space-y-1.5 list-disc pl-5">
          <li>Per-channel retrieval recall against ground-truth chunks</li>
          <li>CRAG calibration over the 12-query mode matrix</li>
          <li>Faithfulness gate true-positive rate (HHEM agreement)</li>
          <li>End-to-end latency p50 / p95 / p99 for /chat</li>
        </ul>
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Shared — minimal response panel (avoids dragging the full AnswerCard
// here which is wired into the chat-state context).
// ---------------------------------------------------------------------------

function ResponsePanel({ r, compact }: { r: ChatResponse; compact?: boolean }) {
  const refused = r.generation.refused;
  return (
    <div className="space-y-3" data-testid="pg-response">
      <div className="flex items-center gap-2 text-xs">
        {refused ? (
          <span className="flex items-center gap-1 text-amber-700">
            <AlertCircle className="w-3.5 h-3.5" />
            refused · {r.generation.refusal_reason ?? "?"}
          </span>
        ) : (
          <span className="flex items-center gap-1 text-emerald-700">
            <CheckCircle2 className="w-3.5 h-3.5" />
            grounded · {(r.crag_score * 100).toFixed(0)}%
          </span>
        )}
        {!compact && (
          <span className="ml-auto mono text-[11px] text-zinc-500">
            {r.latency_ms}ms · {r.hits.length} hits · mode {r.mode ?? "?"}
          </span>
        )}
      </div>
      <div className="text-sm text-zinc-800 whitespace-pre-wrap leading-relaxed">
        {r.generation.answer || (refused ? "(refused)" : "(empty)")}
      </div>
      {r.generation.citations.length > 0 && (
        <div className="text-[11px] text-zinc-500 mono">
          {r.generation.citations.length} citation{r.generation.citations.length === 1 ? "" : "s"}
        </div>
      )}
      {!compact && (
        <details className="rounded border border-zinc-200">
          <summary className="px-3 py-1.5 text-[11px] text-zinc-600 cursor-pointer hover:text-zinc-900">
            Inspector — raw response
          </summary>
          <pre className="px-3 py-2 text-[10px] mono text-zinc-700 bg-zinc-50 overflow-x-auto leading-tight max-h-[300px]">
            {JSON.stringify(
              {
                mode: r.mode,
                intent: r.intent,
                intent_confidence: r.intent_confidence,
                crag_score: r.crag_score,
                faithfulness_verdict: r.faithfulness_verdict,
                faithfulness_score: r.faithfulness_score,
                faithfulness_regenerations: r.faithfulness_regenerations,
                latency_ms: r.latency_ms,
                n_hits: r.hits.length,
                n_citations: r.generation.citations.length,
                model_id: r.generation.model_id,
              },
              null, 2,
            )}
          </pre>
        </details>
      )}
    </div>
  );
}
