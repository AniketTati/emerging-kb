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

import { Suspense, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams, usePathname } from "next/navigation";
import {
  FlaskConical, GitCompare, Beaker, Send, Loader2,
  CheckCircle2, AlertCircle, Play, Clock,
} from "lucide-react";
import {
  getEvalRun, listEvalRuns, postEvalRun,
  postChat,
  type ChatResponse, type EvalRun,
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
  // Past runs land in this list (newest first). The "Run eval suite"
  // button posts a fresh run + starts polling for completion; once it
  // finishes the row in the list updates and the summary renders below.
  const [runs, setRuns] = useState<EvalRun[] | null>(null);
  const [starting, setStarting] = useState(false);
  const [startErr, setStartErr] = useState<string | null>(null);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [ragas, setRagas] = useState(false);
  const [hhem, setHhem] = useState(false);

  // Initial list load.
  useEffect(() => {
    let cancelled = false;
    listEvalRuns(50)
      .then((items) => { if (!cancelled) setRuns(items); })
      .catch(() => { if (!cancelled) setRuns([]); });
    return () => { cancelled = true; };
  }, []);

  // Poll the active run every 3s until status leaves queued/running.
  // 3s matches the dashboard refresh cadence — short enough to feel
  // responsive without hammering the API for a 5-minute job.
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(() => {
    if (!activeRunId) return;
    function clear() {
      if (pollTimer.current) {
        clearInterval(pollTimer.current);
        pollTimer.current = null;
      }
    }
    async function tick() {
      if (!activeRunId) return;
      try {
        const fresh = await getEvalRun(activeRunId);
        setRuns((prev) => {
          if (!prev) return [fresh];
          const others = prev.filter((r) => r.id !== fresh.id);
          return [fresh, ...others];
        });
        if (fresh.status === "succeeded" || fresh.status === "failed") {
          clear();
          setActiveRunId(null);
        }
      } catch (err) {
        console.error("eval poll failed", err);
      }
    }
    pollTimer.current = setInterval(tick, 3000);
    // Immediate fire so the first refresh isn't 3s away.
    void tick();
    return clear;
  }, [activeRunId]);

  async function start() {
    setStarting(true);
    setStartErr(null);
    try {
      const r = await postEvalRun({ ragas, hhem });
      setRuns((prev) => {
        const others = (prev ?? []).filter((x) => x.id !== r.id);
        return [r, ...others];
      });
      if (r.status === "queued" || r.status === "running") {
        setActiveRunId(r.id);
      }
    } catch (err) {
      setStartErr(err instanceof Error ? err.message : String(err));
    } finally {
      setStarting(false);
    }
  }

  const latest = runs && runs.length > 0 ? runs[0] : null;
  const inFlight = latest && (latest.status === "queued" || latest.status === "running");

  return (
    <div className="space-y-4">
      {/* Run controls */}
      <div className="rounded-lg border border-zinc-200 bg-white p-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-base font-semibold text-zinc-900 flex items-center gap-2">
              <Beaker className="w-4 h-4 text-zinc-500" strokeWidth={1.75} />
              Eval suite
            </h2>
            <p className="text-xs text-zinc-500 mt-1 leading-relaxed max-w-2xl">
              Drives the 45-question regression set against the live{" "}
              <span className="mono">/chat</span> pipeline (~5 min wall time at
              concurrency=2). Per-question payloads persist so you can drill
              in afterwards.
            </p>
          </div>
          <button
            type="button"
            onClick={start}
            disabled={starting || !!inFlight}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-zinc-900 text-white text-xs hover:bg-zinc-700 cursor-pointer disabled:opacity-50 flex-shrink-0"
            data-testid="eval-run-start"
            title={inFlight ? "A run is already in flight" : undefined}
          >
            {starting || inFlight ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Play className="w-3.5 h-3.5" />
            )}
            {inFlight ? `${inFlight ? latest!.status : ""}…` : "Run eval suite"}
          </button>
        </div>

        <div className="mt-3 flex items-center gap-5 text-xs">
          <label className="flex items-center gap-1.5 text-zinc-700 cursor-pointer">
            <input
              type="checkbox" checked={ragas}
              onChange={(e) => setRagas(e.target.checked)}
              className="cursor-pointer"
              data-testid="eval-flag-ragas"
            />
            RAGAS (faithfulness · answer-relevancy · context-relevance)
            <span className="text-[10px] text-zinc-400 mono">requires Gemini key</span>
          </label>
          <label className="flex items-center gap-1.5 text-zinc-700 cursor-pointer">
            <input
              type="checkbox" checked={hhem}
              onChange={(e) => setHhem(e.target.checked)}
              className="cursor-pointer"
              data-testid="eval-flag-hhem"
            />
            HHEM-2.1 (~600MB model · ~60-90s cold start)
          </label>
        </div>

        {startErr && (
          <div className="mt-3 text-xs text-red-700 flex items-center gap-1.5">
            <AlertCircle className="w-3.5 h-3.5" /> {startErr}
          </div>
        )}
      </div>

      {/* Latest-run summary */}
      {latest && <RunSummaryCard run={latest} />}

      {/* History */}
      {runs && runs.length > 1 && (
        <div className="rounded-lg border border-zinc-200 bg-white">
          <div className="px-4 py-2 border-b border-zinc-100 text-[11px] uppercase tracking-wider text-zinc-500">
            Past runs ({runs.length - 1})
          </div>
          {runs.slice(1).map((r) => (
            <RunHistoryRow key={r.id} run={r} />
          ))}
        </div>
      )}

      {runs && runs.length === 0 && (
        <div className="rounded-lg border border-dashed border-zinc-200 bg-white px-6 py-10 text-center text-sm text-zinc-500">
          No eval runs yet. Click <span className="mono">Run eval suite</span> to start one.
        </div>
      )}
    </div>
  );
}


function RunSummaryCard({ run }: { run: EvalRun }) {
  const statusBadge = (() => {
    switch (run.status) {
      case "succeeded":
        return <span className="text-[10px] mono px-1.5 py-0.5 rounded bg-emerald-50 text-emerald-800 border border-emerald-200">succeeded</span>;
      case "failed":
        return <span className="text-[10px] mono px-1.5 py-0.5 rounded bg-red-50 text-red-800 border border-red-200">failed</span>;
      case "running":
        return <span className="text-[10px] mono px-1.5 py-0.5 rounded bg-blue-50 text-blue-800 border border-blue-200 inline-flex items-center gap-1"><Loader2 className="w-2.5 h-2.5 animate-spin" />running</span>;
      default:
        return <span className="text-[10px] mono px-1.5 py-0.5 rounded bg-zinc-100 text-zinc-700 border border-zinc-200 inline-flex items-center gap-1"><Clock className="w-2.5 h-2.5" />queued</span>;
    }
  })();

  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-4" data-testid="eval-run-summary">
      <div className="flex items-center gap-2 mb-3 text-xs">
        <span className="text-zinc-500">Latest run</span>
        {statusBadge}
        <span className="mono text-[11px] text-zinc-500 ml-auto">
          {new Date(run.started_at).toLocaleString()}
        </span>
      </div>

      {run.status === "failed" && run.error && (
        <pre className="text-[11px] mono text-red-700 bg-red-50 border border-red-200 rounded p-2 overflow-x-auto whitespace-pre-wrap max-h-[200px]">
          {run.error.slice(0, 1000)}
        </pre>
      )}

      {(run.status === "queued" || run.status === "running") && (
        <div className="text-xs text-zinc-500">
          Polling every 3s. A typical 45-question run takes 4–6 minutes.
        </div>
      )}

      {run.status === "succeeded" && run.summary && (
        <div className="space-y-4">
          {/* Core 5 metrics */}
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            <Metric label="Total" value={run.summary.total.toString()} sub={run.summary.total_errors > 0 ? `${run.summary.total_errors} errors` : "all green"} />
            <Metric label="Lexical" value={pct(run.summary.overall_lexical_avg)} sub="keyword overlap" />
            <Metric label="Refusal" value={pct(run.summary.overall_refusal_accuracy)} sub="must-refuse acc" />
            <Metric label="Citation" value={pct(run.summary.overall_citation_accuracy)} sub="min-citations met" />
            <Metric label="Faith" value={pct(run.summary.overall_faithfulness_avg)} sub="HHEM verdict" />
          </div>

          {/* Optional LLM-judged metrics */}
          {(run.summary.ragas_faithfulness_avg !== null ||
            run.summary.hhem_pass_rate !== null ||
            run.summary.notes.length > 0) && (
            <div className="rounded border border-zinc-200 bg-zinc-50/40 p-3">
              <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-2">
                LLM-judged
              </div>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
                <MetricSmall label="RAGAS faithfulness" value={fmtOpt(run.summary.ragas_faithfulness_avg)} />
                <MetricSmall label="RAGAS answer-relevancy" value={fmtOpt(run.summary.ragas_answer_relevancy_avg)} />
                <MetricSmall label="RAGAS context-relevance" value={fmtOpt(run.summary.ragas_context_relevance_avg)} />
                <MetricSmall label="HHEM pass rate" value={fmtOpt(run.summary.hhem_pass_rate)} />
              </div>
              {run.summary.notes.length > 0 && (
                <ul className="mt-2 text-[11px] text-zinc-500 space-y-0.5">
                  {run.summary.notes.map((n, i) => (
                    <li key={i}>· {n}</li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {/* Per-stratum breakdown */}
          {run.summary.by_stratum.length > 0 && (
            <details className="rounded border border-zinc-200">
              <summary className="px-3 py-2 text-xs text-zinc-600 hover:text-zinc-900 cursor-pointer">
                Per-stratum breakdown ({run.summary.by_stratum.length} strata)
              </summary>
              <div className="px-3 pb-3 pt-1">
                <table className="w-full text-[11px] mono">
                  <thead>
                    <tr className="text-zinc-500 border-b border-zinc-100">
                      <th className="text-left py-1.5">Stratum</th>
                      <th className="text-right">N</th>
                      <th className="text-right">Lex</th>
                      <th className="text-right">Ref</th>
                      <th className="text-right">Cite</th>
                      <th className="text-right">Faith</th>
                      <th className="text-right">Lat (ms)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {run.summary.by_stratum.map((s) => (
                      <tr key={s.stratum} className="border-b border-zinc-100 last:border-0">
                        <td className="py-1.5 text-zinc-700">{s.stratum}</td>
                        <td className="text-right text-zinc-600">{s.count}</td>
                        <td className="text-right">{pct(s.lexical_overlap_avg)}</td>
                        <td className="text-right">{pct(s.refusal_accuracy)}</td>
                        <td className="text-right">{pct(s.citation_accuracy)}</td>
                        <td className="text-right">{pct(s.faithfulness_pass_rate)}</td>
                        <td className="text-right text-zinc-500">{Math.round(s.avg_latency_ms)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          )}
        </div>
      )}
    </div>
  );
}


function RunHistoryRow({ run }: { run: EvalRun }) {
  return (
    <div className="grid grid-cols-[1fr_auto_auto_auto] gap-4 items-center px-4 py-2 text-xs border-b border-zinc-100 last:border-0">
      <div className="flex items-center gap-2">
        <span className="mono text-zinc-400">{run.id.slice(0, 8)}…</span>
        <span className={
          run.status === "succeeded" ? "text-emerald-700" :
          run.status === "failed" ? "text-red-700" : "text-zinc-500"
        }>
          {run.status}
        </span>
      </div>
      <div className="text-zinc-500 mono">
        {run.summary ? `${run.summary.total} Q · lex ${pct(run.summary.overall_lexical_avg)} · faith ${pct(run.summary.overall_faithfulness_avg)}` : "—"}
      </div>
      <div className="text-zinc-400 mono">
        {run.enable_ragas ? "+ragas " : ""}{run.enable_hhem ? "+hhem" : ""}
      </div>
      <div className="text-zinc-500 mono text-right">
        {new Date(run.started_at).toLocaleString()}
      </div>
    </div>
  );
}


function Metric({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="rounded border border-zinc-200 bg-white p-2.5">
      <div className="text-[10px] uppercase tracking-wider text-zinc-400">{label}</div>
      <div className="text-lg font-semibold text-zinc-900 mt-0.5">{value}</div>
      <div className="text-[10px] text-zinc-500 mono">{sub}</div>
    </div>
  );
}


function MetricSmall({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[10px] text-zinc-500">{label}</div>
      <div className="text-sm text-zinc-900 mono">{value}</div>
    </div>
  );
}


function pct(v: number): string {
  return `${(v * 100).toFixed(0)}%`;
}


function fmtOpt(v: number | null): string {
  return v === null ? "—" : (v * 100).toFixed(0) + "%";
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
