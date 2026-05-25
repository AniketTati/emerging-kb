"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  type FileResource,
  blobUrl,
  fetchBlob,
  fetchBlobText,
} from "@/lib/api";
import { useCitation, type Citation } from "./DocDetailCitation";

/**
 * Render an uploaded file in its NATIVE format — PDF pages for PDFs,
 * rendered markdown for .md, header+body for .eml, table view for .xlsx,
 * raw text for .txt. This is the doc-detail page's left pane: the source
 * of truth the user audits the extraction against.
 *
 * Heavier renderers (PDF.js, markdown) are dynamic-imported so the page
 * stays light when the doc doesn't need them.
 */
export function SourceViewer({ file }: { file: FileResource }) {
  const kind = classifyKind(file);
  const { citation, cite } = useCitation();
  return (
    <div className="rounded-lg border border-zinc-200 bg-white overflow-hidden flex flex-col h-full">
      <div className="px-3 py-2 border-b border-zinc-200 flex items-center gap-2 text-[11px] mono text-zinc-500 flex-shrink-0">
        <span className="px-1.5 py-0.5 rounded bg-zinc-100 text-zinc-700 uppercase">
          source
        </span>
        <span>{file.mime_type}</span>
        {citation && (
          <span className="ml-2 flex items-center gap-1.5">
            <span className="text-amber-700">↳ citing:</span>
            <span className="text-zinc-900 truncate max-w-[260px]">
              {citationLabel(citation)}
            </span>
            <button
              type="button"
              onClick={() => cite(null)}
              className="text-zinc-500 hover:text-zinc-900"
              aria-label="Clear citation"
            >
              ×
            </button>
          </span>
        )}
        <span className="ml-auto">{(file.size_bytes / 1024).toFixed(1)} KB</span>
      </div>
      <div
        className="flex-1 overflow-auto"
        data-testid="source-viewer"
        data-kind={kind}
      >
        {kind === "pdf" && <PdfView file={file} citation={citation} />}
        {kind === "markdown" && <MarkdownView file={file} citation={citation} />}
        {kind === "email" && <EmailView file={file} citation={citation} />}
        {kind === "xlsx" && <XlsxView file={file} citation={citation} />}
        {kind === "text" && <PlainTextView file={file} citation={citation} />}
        {kind === "unknown" && (
          <div className="p-6 text-sm text-zinc-500">
            No native viewer for {file.mime_type}. Open the {" "}
            <a
              href={blobUrl(file.id)}
              target="_blank"
              rel="noreferrer"
              className="underline"
            >
              raw blob
            </a>
            .
          </div>
        )}
      </div>
    </div>
  );
}

function citationLabel(c: Citation): string {
  if (c.kind === "text") return `"${c.text.slice(0, 40)}${c.text.length > 40 ? "…" : ""}"`;
  if (c.kind === "xlsx-row")
    return `${c.sheet ?? "sheet"} row ${c.rowIndex}`;
  return `page ${c.pageNumber}`;
}

type Kind = "pdf" | "markdown" | "email" | "xlsx" | "text" | "unknown";

function classifyKind(file: FileResource): Kind {
  const m = (file.mime_type || "").toLowerCase();
  const n = file.name.toLowerCase();
  if (m === "application/pdf" || n.endsWith(".pdf")) return "pdf";
  if (m === "message/rfc822" || n.endsWith(".eml")) return "email";
  if (m.includes("spreadsheet") || n.endsWith(".xlsx")) return "xlsx";
  if (m === "text/markdown" || n.endsWith(".md")) return "markdown";
  if (m.startsWith("text/")) return "text";
  return "unknown";
}


// ---------------------------------------------------------------------------
// PDF — react-pdf (PDF.js). Dynamic-imported so the bundle stays light.
// ---------------------------------------------------------------------------

function PdfView({
  file,
  citation,
}: {
  file: FileResource;
  citation: Citation | null;
}) {
  // react-pdf 10 + pdfjs-dist 5 ESM works under Next.js Turbopack
  // (webpack 5.98 hits a known ESM-interop bug; tracking issue
  // mozilla/pdf.js#20478). Dev script runs with --turbopack. The
  // text-layer + annotation-layer DOM is what citation highlighting
  // hooks into.
  const [Lib, setLib] = useState<typeof import("react-pdf") | null>(null);
  const [blob, setBlob] = useState<Blob | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [numPages, setNumPages] = useState(0);
  const [pageNum, setPageNum] = useState(1);
  const pageWrapRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([import("react-pdf"), fetchBlob(file.id)])
      .then(([lib, b]) => {
        if (cancelled) return;
        // Worker URL is resolved by the bundler so it stays in lockstep
        // with the pdfjs-dist transitive dep.
        lib.pdfjs.GlobalWorkerOptions.workerSrc = new URL(
          "pdfjs-dist/build/pdf.worker.min.mjs",
          import.meta.url,
        ).toString();
        setLib(lib);
        setBlob(b);
      })
      .catch((e) => !cancelled && setErr(String(e)));
    return () => {
      cancelled = true;
    };
  }, [file.id]);

  // Jump to the first cited page whenever a citation lands.
  useEffect(() => {
    if (!citation) return;
    if (citation.kind === "page") setPageNum(citation.pageNumber);
    if (citation.kind === "text" && citation.page && citation.page.length > 0) {
      setPageNum(citation.page[0]);
    }
  }, [citation]);

  // Best-effort text-layer highlight: once the page renders, walk the
  // PDF.js text spans and mark the first one whose textContent contains
  // the cited needle. PDF.js text layer is selectable DOM so this works
  // even though we don't pre-compute exact ranges.
  useEffect(() => {
    if (!citation || citation.kind !== "text") return;
    const wrap = pageWrapRef.current;
    if (!wrap) return;
    const needle = citation.text.trim();
    if (!needle) return;
    // Wait a tick for the text layer to mount.
    const id = window.setTimeout(() => {
      wrap.querySelectorAll(".kb-pdf-hit").forEach((el) =>
        el.classList.remove("kb-pdf-hit"),
      );
      const spans = wrap.querySelectorAll<HTMLElement>(
        ".react-pdf__Page__textContent span",
      );
      const lower = needle.toLowerCase();
      for (const sp of Array.from(spans)) {
        if ((sp.textContent ?? "").toLowerCase().includes(lower)) {
          sp.classList.add("kb-pdf-hit");
          sp.scrollIntoView({ behavior: "smooth", block: "center" });
          break;
        }
      }
    }, 400);
    return () => window.clearTimeout(id);
  }, [citation, pageNum]);

  if (err) return <div className="p-4 text-xs text-red-700 mono">{err}</div>;
  if (!Lib || !blob)
    return <div className="p-4 text-xs text-zinc-500">Loading PDF…</div>;

  const { Document, Page } = Lib;
  return (
    <div className="flex flex-col h-full" data-testid="pdf-view">
      <div className="px-3 py-2 border-b border-zinc-200 flex items-center gap-2 text-[11px] mono text-zinc-500 bg-zinc-50 flex-shrink-0">
        <button
          type="button"
          className="px-1.5 py-0.5 rounded hover:bg-zinc-100 disabled:opacity-30"
          disabled={pageNum <= 1}
          onClick={() => setPageNum((n) => n - 1)}
        >
          ◀
        </button>
        <span>
          page {pageNum} / {numPages || "…"}
        </span>
        <button
          type="button"
          className="px-1.5 py-0.5 rounded hover:bg-zinc-100 disabled:opacity-30"
          disabled={pageNum >= numPages}
          onClick={() => setPageNum((n) => n + 1)}
        >
          ▶
        </button>
      </div>
      <div
        ref={pageWrapRef}
        className="flex-1 overflow-auto p-3 bg-zinc-100 flex justify-center items-start"
      >
        <Document
          file={blob}
          onLoadSuccess={(p) => setNumPages(p.numPages)}
          loading={<div className="text-xs text-zinc-500 p-2">Rendering…</div>}
        >
          <Page
            pageNumber={pageNum}
            width={520}
            renderAnnotationLayer={false}
            // Text layer = selectable DOM that our citation highlighter
            // crawls when a mention/triple/field is clicked on the right.
            renderTextLayer={true}
          />
        </Document>
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Markdown — render with react-markdown + remark-gfm.
// ---------------------------------------------------------------------------

function MarkdownView({
  file,
  citation,
}: {
  file: FileResource;
  citation: Citation | null;
}) {
  const [text, setText] = useState<string | null>(null);
  const [Comp, setComp] = useState<{
    Markdown: typeof import("react-markdown").default;
    gfm: unknown;
    sanitize: unknown;
  } | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      fetchBlobText(file.id),
      import("react-markdown"),
      import("remark-gfm"),
      import("rehype-sanitize"),
    ])
      .then(([t, md, gfm, san]) => {
        if (cancelled) return;
        setText(t);
        setComp({ Markdown: md.default, gfm: gfm.default, sanitize: san.default });
      })
      .catch((e) => !cancelled && setErr(String(e)));
    return () => {
      cancelled = true;
    };
  }, [file.id]);

  if (err) return <div className="p-4 text-xs text-red-700 mono">{err}</div>;
  if (text === null || !Comp)
    return <div className="p-4 text-xs text-zinc-500">Loading…</div>;
  const { Markdown, gfm, sanitize } = Comp;
  const needle = citation?.kind === "text" ? citation.text : null;
  return (
    <div className="prose prose-sm prose-zinc max-w-none p-5">
      {/* When the user cites something, render the highlighted-text view
          inline above the markdown render so we can still wrap the hit
          in a <mark>. The fully rendered markdown stays below. */}
      {needle && (
        <div className="not-prose mb-4 rounded border border-amber-200 bg-amber-50/40 p-3 text-[12px] text-zinc-800">
          <div className="text-[10px] mono text-amber-700 uppercase mb-1">
            cited span
          </div>
          <HighlightedText text={text} needle={needle} />
        </div>
      )}
      <Markdown
        remarkPlugins={[gfm as never]}
        rehypePlugins={[sanitize as never]}
      >
        {text}
      </Markdown>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Email — parse with postal-mime, show From/To/Subject header + body.
// ---------------------------------------------------------------------------

type ParsedEmail = {
  from?: { name?: string; address?: string } | null;
  to?: Array<{ name?: string; address?: string }>;
  cc?: Array<{ name?: string; address?: string }>;
  subject?: string;
  date?: string;
  text?: string;
  html?: string;
};

function EmailView({
  file,
  citation,
}: {
  file: FileResource;
  citation: Citation | null;
}) {
  const [parsed, setParsed] = useState<ParsedEmail | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([fetchBlob(file.id), import("postal-mime")])
      .then(async ([b, mod]) => {
        if (cancelled) return;
        // postal-mime default export shape varies by version — try both.
        const Parser =
          (mod as { default?: { parse?: typeof import("postal-mime").default.parse } })
            .default ?? (mod as unknown as typeof import("postal-mime").default);
        const result = await (
          Parser as unknown as { parse: (input: ArrayBuffer) => Promise<ParsedEmail> }
        ).parse(await b.arrayBuffer());
        if (!cancelled) setParsed(result);
      })
      .catch((e) => !cancelled && setErr(String(e)));
    return () => {
      cancelled = true;
    };
  }, [file.id]);

  if (err) return <div className="p-4 text-xs text-red-700 mono">{err}</div>;
  if (!parsed) return <div className="p-4 text-xs text-zinc-500">Loading email…</div>;

  return (
    <div className="p-5">
      <div className="rounded border border-zinc-200 bg-zinc-50/50">
        <Field label="From" value={fmtAddr(parsed.from)} />
        <Field label="To" value={parsed.to?.map(fmtAddr).join(", ")} />
        {parsed.cc && parsed.cc.length > 0 && (
          <Field label="Cc" value={parsed.cc.map(fmtAddr).join(", ")} />
        )}
        <Field label="Subject" value={parsed.subject ?? ""} />
        <Field label="Date" value={parsed.date ?? ""} />
      </div>
      <div className="mt-4">
        {parsed.html ? (
          // Email HTML often carries tracking pixels + inline scripts —
          // render in a sandboxed iframe with srcDoc so any embedded
          // JS/network calls are scoped to a throw-away origin.
          // Citation highlighting in HTML emails would need DOM scripting
          // inside the iframe; deferred. The plain-text body (most .eml
          // in practice) supports highlighting via HighlightedText.
          <iframe
            title={`${file.name} body`}
            srcDoc={parsed.html}
            sandbox="allow-same-origin"
            className="w-full min-h-[400px] border border-zinc-200 rounded bg-white"
          />
        ) : (
          <pre className="text-[13px] text-zinc-800 whitespace-pre-wrap font-sans leading-relaxed">
            <HighlightedText
              text={parsed.text ?? ""}
              needle={citation?.kind === "text" ? citation.text : null}
            />
          </pre>
        )}
      </div>
    </div>
  );
}

function Field({ label, value }: { label: string; value: string | undefined }) {
  return (
    <div className="grid grid-cols-[80px_1fr] gap-2 px-3 py-1.5 text-xs border-b border-zinc-100 last:border-b-0">
      <span className="text-zinc-500 mono">{label}</span>
      <span className="text-zinc-900 truncate">{value || <span className="text-zinc-400">—</span>}</span>
    </div>
  );
}

function fmtAddr(a?: { name?: string; address?: string } | null): string {
  if (!a) return "";
  if (a.name && a.address) return `${a.name} <${a.address}>`;
  return a.address ?? a.name ?? "";
}


// ---------------------------------------------------------------------------
// XLSX — server-parsed structured tables (one per sheet).
// ---------------------------------------------------------------------------

function XlsxView({
  file,
  citation,
}: {
  file: FileResource;
  citation: Citation | null;
}) {
  // SheetJS client-side parse: fetch the blob, hand to XLSX.read, render
  // each sheet via XLSX.utils.sheet_to_html. Avoids a server round-trip
  // and matches whatever the user's spreadsheet actually contains
  // (merged cells, formulas, formatting) better than a tabular re-parse.
  const [sheets, setSheets] = useState<
    { name: string; html: string }[] | null
  >(null);
  const [err, setErr] = useState<string | null>(null);
  const [tab, setTab] = useState(0);
  const [notFound, setNotFound] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([fetchBlob(file.id), import("xlsx")])
      .then(async ([b, xlsx]) => {
        if (cancelled) return;
        const wb = xlsx.read(await b.arrayBuffer(), { type: "array" });
        const out = wb.SheetNames.map((name) => ({
          name,
          html: xlsx.utils.sheet_to_html(wb.Sheets[name]),
        }));
        setSheets(out);
      })
      .catch((e) => !cancelled && setErr(String(e)));
    return () => {
      cancelled = true;
    };
  }, [file.id]);

  // Auto-switch to the sheet that contains the cited row OR text.
  useEffect(() => {
    if (!sheets || !citation) return;
    if (citation.kind === "xlsx-row" && citation.sheet) {
      const idx = sheets.findIndex((s) => s.name === citation.sheet);
      if (idx >= 0 && idx !== tab) setTab(idx);
      return;
    }
    if (citation.kind === "text") {
      const needle = citation.text.toLowerCase();
      const idx = sheets.findIndex((s) =>
        s.html.toLowerCase().includes(needle),
      );
      if (idx >= 0 && idx !== tab) setTab(idx);
    }
  }, [citation, sheets, tab]);

  // Apply highlight whenever the citation or active sheet changes.
  // Deferred past the next React render so the DOM written by
  // dangerouslySetInnerHTML reflects the current `tab` before we walk it.
  useEffect(() => {
    const id = window.setTimeout(() => {
      const wrap = wrapRef.current;
      if (!wrap) return;
      wrap.querySelectorAll("tr.kb-cited").forEach((el) =>
        el.classList.remove("kb-cited"),
      );
      wrap.querySelectorAll("td.kb-cited-cell").forEach((el) =>
        el.classList.remove("kb-cited-cell"),
      );
      setNotFound(false);
      if (!citation) return;
      const rows = wrap.querySelectorAll("tr");
      if (citation.kind === "xlsx-row") {
        const target = rows[citation.rowIndex];
        if (target) {
          target.classList.add("kb-cited");
          target.scrollIntoView({ behavior: "smooth", block: "center" });
        } else {
          setNotFound(true);
        }
        return;
      }
      if (citation.kind === "text") {
        // Cell-level text search inside the active sheet — first cell
        // whose textContent contains the needle gets the highlight.
        const needle = citation.text.toLowerCase().trim();
        if (!needle) return;
        for (const row of Array.from(rows)) {
          for (const cell of Array.from(row.querySelectorAll("td"))) {
            if ((cell.textContent ?? "").toLowerCase().includes(needle)) {
              cell.classList.add("kb-cited-cell");
              row.classList.add("kb-cited");
              cell.scrollIntoView({ behavior: "smooth", block: "center" });
              return;
            }
          }
        }
        // Searched every sheet that contains the needle (auto-switch
        // already moved us there). Reaching here means the active
        // sheet's cells don't contain the text — usually because the
        // mention came from a contextualizer-added prefix rather than
        // from the raw file body.
        setNotFound(true);
      }
    }, 50);
    return () => window.clearTimeout(id);
  }, [citation, sheets, tab]);

  if (err) return <div className="p-4 text-xs text-red-700 mono">{err}</div>;
  if (!sheets)
    return <div className="p-4 text-xs text-zinc-500">Loading workbook…</div>;
  if (sheets.length === 0)
    return <div className="p-4 text-xs text-zinc-500">Empty workbook.</div>;

  return (
    <div className="flex flex-col h-full">
      {sheets.length > 1 && (
        <div className="flex border-b border-zinc-200 bg-zinc-50 flex-shrink-0 overflow-x-auto">
          {sheets.map((s, i) => (
            <button
              key={s.name}
              type="button"
              onClick={() => setTab(i)}
              className={`text-xs px-3 py-1.5 border-r border-zinc-200 whitespace-nowrap ${
                i === tab
                  ? "bg-white text-zinc-900"
                  : "text-zinc-500 hover:bg-zinc-100"
              }`}
            >
              {s.name}
            </button>
          ))}
        </div>
      )}
      {notFound && (
        <div className="px-3 py-2 text-[11px] mono text-amber-800 bg-amber-50 border-b border-amber-200">
          ↳ not found in this file's cells — likely from a contextual
          prefix added during chunking, not from the raw spreadsheet
        </div>
      )}
      <div
        ref={wrapRef}
        className="xlsx-table-wrap flex-1 overflow-auto p-2"
        dangerouslySetInnerHTML={{ __html: sheets[tab].html }}
      />
    </div>
  );
}


// ---------------------------------------------------------------------------
// Plain text — .txt, raw fallback.
// ---------------------------------------------------------------------------

function PlainTextView({
  file,
  citation,
}: {
  file: FileResource;
  citation: Citation | null;
}) {
  const [text, setText] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchBlobText(file.id)
      .then((t) => !cancelled && setText(t))
      .catch((e) => !cancelled && setErr(String(e)));
    return () => {
      cancelled = true;
    };
  }, [file.id]);

  if (err) return <div className="p-4 text-xs text-red-700 mono">{err}</div>;
  if (text === null) return <div className="p-4 text-xs text-zinc-500">Loading…</div>;

  return (
    <pre className="text-[13px] text-zinc-800 whitespace-pre-wrap font-sans leading-relaxed p-5">
      <HighlightedText text={text} needle={citation?.kind === "text" ? citation.text : null} />
    </pre>
  );
}


/**
 * Render `text` as plain text with the first occurrence of `needle`
 * wrapped in a <mark> + scroll-into-view on mount. Case-insensitive,
 * tolerant of internal whitespace. Returns the full text when no match.
 */
function HighlightedText({
  text,
  needle,
}: {
  text: string;
  needle: string | null;
}) {
  const markRef = useRef<HTMLElement | null>(null);
  const segments = useMemo(() => splitOnNeedle(text, needle), [text, needle]);
  useEffect(() => {
    if (markRef.current) {
      markRef.current.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [segments]);

  if (!segments || segments.length === 1) return <>{text}</>;
  return (
    <>
      {segments.map((seg, i) =>
        seg.match ? (
          <mark
            key={i}
            ref={i === firstMatchIndex(segments) ? markRef : null}
            className="bg-amber-200 text-zinc-900 rounded px-0.5"
            data-testid="source-highlight"
          >
            {seg.value}
          </mark>
        ) : (
          <span key={i}>{seg.value}</span>
        ),
      )}
    </>
  );
}

type Segment = { value: string; match: boolean };

function splitOnNeedle(text: string, needle: string | null): Segment[] | null {
  if (!needle) return null;
  // Case-insensitive whole-substring match — collapses runs of whitespace
  // in the needle so a chunk-extracted phrase still matches text that
  // got re-wrapped in the source.
  const normalizedNeedle = needle.trim().replace(/\s+/g, "\\s+");
  if (!normalizedNeedle) return null;
  try {
    const rx = new RegExp(escapeRegex(needle).replace(/\\s/g, "\\s+"), "i");
    const m = rx.exec(text);
    if (!m) return [{ value: text, match: false }];
    const [hit] = m;
    return [
      { value: text.slice(0, m.index), match: false },
      { value: hit, match: true },
      { value: text.slice(m.index + hit.length), match: false },
    ];
  } catch {
    return [{ value: text, match: false }];
  }
}

function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\s+/g, "\\s+");
}

function firstMatchIndex(segments: Segment[]): number {
  return segments.findIndex((s) => s.match);
}

