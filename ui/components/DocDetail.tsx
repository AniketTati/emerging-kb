"use client";

import {
  ReactNode,
  useCallback,
  useEffect,
  useState,
} from "react";
import {
  ChevronRight,
  FileText,
  Mail,
  Image as ImageIcon,
  Table as TableIcon,
  AlertTriangle,
  Sparkles,
} from "lucide-react";
import { SourceViewer } from "./SourceViewer";
import {
  CitationProvider,
  useCitation,
  type Citation,
} from "./DocDetailCitation";
import {
  type AtomicUnit,
  type CitationByQuery,
  type EntityMentioned,
  type ExtractedEntityInstance,
  type FileDetails,
  type FileResource,
  type LifecycleEventDetail,
  type Mention,
  type Paginated,
  type ProposedField,
  type RawPage,
  type TripleInDoc,
  getSubEntities,
  getDocCitations,
  getDocMentions,
  getDocPages,
  getDocTriples,
  getEntitiesMentioned,
  getExtractedEntities,
  getFile,
  getFileDetails,
  getProposedFields,
} from "@/lib/api";

/**
 * Doc-detail audit view — one column with a header card + featured-clause
 * hero + 9 lazy-loaded accordions. Each accordion binds one section to
 * one endpoint, paginated. Designed for docs from 1 to 500+ pages.
 */
export function DocDetail({ fileId }: { fileId: string }) {
  const [file, setFile] = useState<FileResource | null>(null);
  const [details, setDetails] = useState<FileDetails | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getFile(fileId), getFileDetails(fileId)])
      .then(([f, d]) => {
        if (cancelled) return;
        setFile(f);
        setDetails(d);
      })
      .catch((e) => !cancelled && setError(String(e)));
    return () => {
      cancelled = true;
    };
  }, [fileId]);

  if (error) {
    return (
      <div className="p-8 text-sm text-red-700">
        Failed to load file: {error}
      </div>
    );
  }
  if (!file || !details) {
    return <div className="p-8 text-sm text-zinc-500">Loading…</div>;
  }

  return (
    <CitationProvider>
      <div className="flex flex-col h-full">
        {/* Single-row sticky header — keeps source + extracted panes tall. */}
        <div className="flex-shrink-0 border-b border-zinc-200 bg-white">
          <HeaderCard file={file} details={details} />
        </div>

        {/* Two-pane body: source on left, extraction on right.
            Each pane uses 100% of remaining vertical with its own scroll. */}
        <div className="flex-1 grid grid-cols-2 gap-3 min-h-0 px-3 py-3 bg-zinc-50">
          <div className="min-h-0" data-testid="source-pane">
            <SourceViewer file={file} />
          </div>
          <div className="min-h-0 overflow-y-auto" data-testid="extracted-pane">
            <div className="space-y-3">
              <FeaturedClause fileId={fileId} />
              <SectionGroup>
                <ParsedTextSection fileId={fileId} totalPages={details.n_pages} />
                <ProposedFieldsSection fileId={fileId} />
                <AtomicUnitsSection fileId={fileId} total={details.n_sub_entities} />
                <ExtractedEntitiesSection fileId={fileId} />
                <MentionsSection fileId={fileId} total={details.n_mentions} />
                <EntitiesMentionedSection
                  fileId={fileId}
                  total={details.n_entities_linked}
                />
                <TriplesSection fileId={fileId} total={details.n_triples} />
                <ChainSection details={details} />
                <ProcessingLogSection details={details} />
                <CitationsSection fileId={fileId} />
              </SectionGroup>
            </div>
          </div>
        </div>
      </div>
    </CitationProvider>
  );
}


// ---------------------------------------------------------------------------
// Header card — filename · doc-type · authority · status · chain · stage
// ---------------------------------------------------------------------------

function iconFor(mime: string) {
  if (mime === "message/rfc822") return Mail;
  if (mime.startsWith("image/")) return ImageIcon;
  if (mime.includes("spreadsheet")) return TableIcon;
  return FileText;
}

function HeaderCard({
  file,
  details,
}: {
  file: FileResource;
  details: FileDetails;
}) {
  // Single-row sticky strip: filename · classification badges · inline
  // count pills. The block card ate ~240px vertical the panes need.
  // Counts stay inline as warn-aware pills so audit gaps still stand
  // out at a glance.
  const Icon = iconFor(file.mime_type);
  const authority = file.source_authority;
  const lowAuth =
    authority !== null && authority !== undefined && authority < 0.5;
  const notLive = file.doc_status && file.doc_status !== "live";

  return (
    <div className="flex items-center gap-2 px-4 py-2 flex-wrap">
      <Icon
        className="w-4 h-4 text-zinc-500 flex-shrink-0"
        strokeWidth={1.75}
        aria-hidden
      />
      <div
        className="text-sm font-medium text-zinc-900 truncate max-w-[280px]"
        data-testid="doc-detail-filename"
        title={file.name}
      >
        {file.name}
      </div>

      {file.inferred_doc_type && (
        <Chip label={file.inferred_doc_type} tone="neutral" />
      )}
      {authority !== null && authority !== undefined && (
        <Chip
          label={`auth ${authority.toFixed(2)}`}
          tone={lowAuth ? "warn" : authority >= 0.8 ? "good" : "neutral"}
          title={file.source_authority_reason ?? undefined}
        />
      )}
      {file.doc_status && (
        <Chip
          label={file.doc_status}
          tone={notLive ? "warn" : "neutral"}
        />
      )}
      {details.chain_id && (
        <Chip
          label={`chain ${details.chain_role}${
            details.chain_version_index !== null
              ? ` v${details.chain_version_index}`
              : ""
          }${details.is_current_version ? " · current" : ""}`}
          tone="neutral"
        />
      )}

      {/* Inline count pills — pushed to the right edge. */}
      <div className="ml-auto flex items-center gap-1.5 text-[11px] mono">
        <CountPill label="pages" n={details.n_pages} />
        <CountPill label="chunks" n={details.n_chunks} />
        <CountPill label="mentions" n={details.n_mentions} warn={details.n_mentions === 0} />
        <CountPill label="units" n={details.n_sub_entities} />
        <CountPill label="entities" n={details.n_entities_linked} warn={details.n_entities_linked === 0} />
        <CountPill label="triples" n={details.n_triples} />
      </div>
    </div>
  );
}

function Chip({
  label,
  tone,
  title,
}: {
  label: string;
  tone: "neutral" | "good" | "warn";
  title?: string;
}) {
  const cls =
    tone === "warn"
      ? "bg-amber-50 text-amber-800"
      : tone === "good"
        ? "bg-emerald-50 text-emerald-800"
        : "bg-zinc-100 text-zinc-700";
  return (
    <span
      className={`text-[11px] mono px-1.5 py-0.5 rounded ${cls}`}
      title={title}
    >
      {label}
    </span>
  );
}

function CountPill({
  label,
  n,
  warn,
}: {
  label: string;
  n: number;
  warn?: boolean;
}) {
  return (
    <span
      className={`px-1.5 py-0.5 rounded ${warn ? "bg-amber-50 text-amber-800" : "bg-zinc-50 text-zinc-700"}`}
      title={warn ? "warning: this layer extracted nothing" : undefined}
    >
      <span className={warn ? "text-amber-900" : "text-zinc-900"}>{n}</span>
      <span className="ml-1 text-zinc-500">{label}</span>
      {warn && (
        <AlertTriangle
          className="inline-block w-3 h-3 ml-1 text-amber-700"
          strokeWidth={2}
        />
      )}
    </span>
  );
}


// ---------------------------------------------------------------------------
// Featured clause — top atomic_unit by rarity. The prototype's hero zone.
// ---------------------------------------------------------------------------

function FeaturedClause({ fileId }: { fileId: string }) {
  const [unit, setUnit] = useState<AtomicUnit | "none" | null>(null);
  useEffect(() => {
    let cancelled = false;
    getSubEntities(fileId, { limit: 1 }).then((r) => {
      if (cancelled) return;
      setUnit(r.items[0] ?? "none");
    });
    return () => {
      cancelled = true;
    };
  }, [fileId]);

  if (unit === null) {
    return (
      <div className="rounded-lg border border-zinc-200 bg-white p-5 text-sm text-zinc-500">
        Loading featured clause…
      </div>
    );
  }
  if (unit === "none") return null;

  return (
    <div className="rounded-lg border border-zinc-300 bg-white p-5">
      <div className="flex items-center gap-2 text-[11px] mono text-zinc-500 mb-2">
        <Sparkles className="w-3.5 h-3.5 text-zinc-700" strokeWidth={1.75} />
        <span className="px-1.5 py-0.5 rounded bg-zinc-100 text-zinc-700 uppercase">
          {unit.unit_type}
        </span>
        <span className="ml-auto text-zinc-900">
          rarity {unit.rarity_score?.toFixed(2) ?? "—"}
        </span>
      </div>
      <pre className="text-sm text-zinc-900 leading-relaxed border-l-2 border-zinc-300 pl-3 whitespace-pre-wrap font-sans">
        {prettyParameters(unit.parameters)}
      </pre>
      <div className="mt-3 text-[11px] text-zinc-500 mono">
        Featured because this unit has the highest rarity in the doc.
      </div>
    </div>
  );
}

function prettyParameters(p: Record<string, unknown>): string {
  // Atomic units come in flavors: clause text, table rows, price rows, etc.
  // Render the most useful prose form rather than dumping JSON.
  if (typeof p.text === "string") return p.text;
  if (Array.isArray(p.cells)) {
    const cells = (p.cells as unknown[]).map((c) => String(c)).join(" · ");
    const sheet = typeof p.sheet_name === "string" ? `[${p.sheet_name}] ` : "";
    return `${sheet}${cells}`;
  }
  return JSON.stringify(p, null, 2);
}


// ---------------------------------------------------------------------------
// Accordion shell
// ---------------------------------------------------------------------------

function SectionGroup({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-lg border border-zinc-200 bg-white divide-y divide-zinc-100">
      {children}
    </div>
  );
}

function Accordion({
  icon: Icon,
  title,
  count,
  warn,
  testId,
  children,
}: {
  icon: typeof FileText;
  title: string;
  count?: number | string;
  warn?: boolean;
  testId?: string;
  children: (open: boolean) => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div data-testid={testId}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full px-5 py-3 flex items-center gap-2.5 hover:bg-zinc-50 text-left"
        aria-expanded={open}
      >
        <ChevronRight
          className={`w-3.5 h-3.5 text-zinc-400 transition-transform ${
            open ? "rotate-90" : ""
          }`}
          strokeWidth={1.75}
        />
        <Icon className="w-3.5 h-3.5 text-zinc-500" strokeWidth={1.75} />
        <span className="text-sm text-zinc-900">{title}</span>
        {count !== undefined && (
          <span
            className={`ml-auto text-[11px] mono ${
              warn ? "text-amber-800" : "text-zinc-500"
            }`}
          >
            {count}
            {warn && (
              <AlertTriangle
                className="inline-block w-3 h-3 ml-1 text-amber-700"
                strokeWidth={2}
              />
            )}
          </span>
        )}
      </button>
      {open && <div className="px-5 pb-4">{children(open)}</div>}
    </div>
  );
}

function useLazy<T>(fn: () => Promise<T>, open: boolean): [T | null, string | null] {
  const [data, setData] = useState<T | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (!open || data !== null || err !== null) return;
    let cancelled = false;
    fn()
      .then((v) => !cancelled && setData(v))
      .catch((e) => !cancelled && setErr(String(e)));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
  return [data, err];
}


// ---------------------------------------------------------------------------
// Sections — one per extraction layer
// ---------------------------------------------------------------------------

function ParsedTextSection({ fileId, totalPages }: { fileId: string; totalPages: number }) {
  return (
    <Accordion
      icon={FileText}
      title="Parsed text (L1, per page)"
      count={`${totalPages} page${totalPages === 1 ? "" : "s"}`}
      testId="doc-detail-source"
    >
      {(open) => <SourcePager fileId={fileId} totalPages={totalPages} open={open} />}
    </Accordion>
  );
}

function SourcePager({
  fileId,
  totalPages,
  open,
}: {
  fileId: string;
  totalPages: number;
  open: boolean;
}) {
  const [page, setPage] = useState(0);
  const [pageData, setPageData] = useState<RawPage | null>(null);
  const [pageErr, setPageErr] = useState<string | null>(null);
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setPageData(null);
    getDocPages(fileId, { limit: 1, offset: page })
      .then((r) => !cancelled && setPageData(r.items[0] ?? null))
      .catch((e) => !cancelled && setPageErr(String(e)));
    return () => {
      cancelled = true;
    };
  }, [fileId, page, open]);

  if (pageErr) return <ErrorRow message={pageErr} />;
  if (!pageData) return <LoadingRow />;
  const p = pageData;
  const elements = p.layout_json?.elements ?? [];

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-[11px] mono text-zinc-500">
        <button
          type="button"
          className="px-1.5 py-0.5 rounded hover:bg-zinc-100 disabled:opacity-30"
          disabled={page === 0}
          onClick={() => setPage((n) => Math.max(0, n - 1))}
        >
          ◀
        </button>
        <span>
          page {p.page_number} of {totalPages}
        </span>
        <button
          type="button"
          className="px-1.5 py-0.5 rounded hover:bg-zinc-100 disabled:opacity-30"
          disabled={page + 1 >= totalPages}
          onClick={() => setPage((n) => n + 1)}
        >
          ▶
        </button>
      </div>
      {elements.length > 0 && <LayoutStrip elements={elements} pageSize={p.layout_json?.size ?? null} />}
      <pre className="text-[12px] text-zinc-700 whitespace-pre-wrap bg-zinc-50 rounded p-3 max-h-[400px] overflow-y-auto font-sans leading-relaxed">
        {p.text || <span className="text-zinc-400">(empty page text)</span>}
      </pre>
    </div>
  );
}


/** R5 — per-page layout summary surfaced from the Docling parser.
 *
 *  Shows a count breakdown by element label (section_header / text /
 *  table / picture / list_item / ...) plus a miniature SVG sketch of
 *  the page with each element drawn as a coloured rectangle. PDF
 *  coordinates have a bottom-left origin so we flip Y when drawing.
 *
 *  Pre-R5 PDFs (parsed before the parser captured per-element
 *  provenance) will simply not have `elements` set, and this whole
 *  block is omitted. */
function LayoutStrip({
  elements,
  pageSize,
}: {
  elements: NonNullable<RawPage["layout_json"]>["elements"];
  pageSize: { width: number | null; height: number | null } | null;
}) {
  const list = elements ?? [];
  if (list.length === 0) return null;

  const counts = new Map<string, number>();
  for (const e of list) {
    const k = e.label ?? "other";
    counts.set(k, (counts.get(k) ?? 0) + 1);
  }
  const sortedCounts = Array.from(counts.entries()).sort((a, b) => b[1] - a[1]);

  return (
    <div
      className="text-[11px] mono text-zinc-600 bg-amber-50/60 border border-amber-100 rounded px-2 py-2 space-y-2"
      data-testid="layout-strip"
    >
      <div className="flex items-center flex-wrap gap-2">
        <span className="text-zinc-500">layout:</span>
        <span className="font-medium text-zinc-700">
          {list.length} element{list.length === 1 ? "" : "s"}
        </span>
        {sortedCounts.map(([label, n]) => (
          <span
            key={label}
            className="px-1.5 py-0.5 rounded bg-white border border-zinc-200"
            title={`${n} ${label} element(s)`}
          >
            {label} {n}
          </span>
        ))}
      </div>
      {pageSize?.width && pageSize?.height && (
        <LayoutMiniMap
          elements={list}
          pageWidth={pageSize.width}
          pageHeight={pageSize.height}
        />
      )}
    </div>
  );
}


function LayoutMiniMap({
  elements,
  pageWidth,
  pageHeight,
}: {
  elements: NonNullable<RawPage["layout_json"]>["elements"];
  pageWidth: number;
  pageHeight: number;
}) {
  const list = elements ?? [];
  // Width-cap the mini-map so a letter-page (612x792) renders ~160px wide.
  const renderW = 160;
  const renderH = (pageHeight / pageWidth) * renderW;
  const sx = renderW / pageWidth;
  const sy = renderH / pageHeight;

  return (
    <svg
      viewBox={`0 0 ${renderW} ${renderH}`}
      width={renderW}
      height={renderH}
      className="bg-white border border-zinc-200 rounded"
      role="img"
      aria-label={`Layout sketch — ${list.length} elements`}
    >
      <rect x={0} y={0} width={renderW} height={renderH} fill="#fff" />
      {list.map((e, i) => {
        const { l, t, r, b, coord_origin } = e.bbox;
        // PDF native origin is bottom-left; flip Y for SVG which is
        // top-left. If the parser passed TOPLEFT we use directly.
        const flipY = (coord_origin ?? "").toUpperCase().includes("BOTTOM");
        const x = l * sx;
        const w = Math.max(1, (r - l) * sx);
        const yTop = flipY ? (pageHeight - t) : t;
        const yBot = flipY ? (pageHeight - b) : b;
        const y = Math.min(yTop, yBot) * sy;
        const h = Math.max(1, Math.abs(yBot - yTop) * sy);
        const color = colorForLabel(e.label);
        return (
          <rect
            key={i}
            x={x}
            y={y}
            width={w}
            height={h}
            fill={color}
            fillOpacity={0.25}
            stroke={color}
            strokeWidth={0.5}
          >
            <title>{`${e.label ?? "element"}${e.text ? ` — ${e.text.slice(0, 80)}` : ""}`}</title>
          </rect>
        );
      })}
    </svg>
  );
}


function colorForLabel(label: string | null | undefined): string {
  switch ((label ?? "").toLowerCase()) {
    case "section_header":
    case "title":
      return "#3b82f6"; // blue
    case "text":
    case "paragraph":
      return "#10b981"; // emerald
    case "table":
      return "#f59e0b"; // amber
    case "picture":
    case "figure":
      return "#a855f7"; // purple
    case "list_item":
      return "#0ea5e9"; // sky
    case "page_header":
    case "page_footer":
      return "#9ca3af"; // zinc
    default:
      return "#64748b"; // slate
  }
}

function ProposedFieldsSection({ fileId }: { fileId: string }) {
  return (
    <Accordion
      icon={FileText}
      title="Inferred fields (L3 open-world)"
      testId="doc-detail-fields"
    >
      {(open) => <ProposedFieldsBody fileId={fileId} open={open} />}
    </Accordion>
  );
}

function ProposedFieldsBody({ fileId, open }: { fileId: string; open: boolean }) {
  const [data, err] = useLazy(
    useCallback(() => getProposedFields(fileId), [fileId]),
    open,
  );
  const { cite, citation } = useCitation();
  if (err) return <ErrorRow message={err} />;
  if (!data) return <LoadingRow />;
  if (data.length === 0)
    return <EmptyRow message="No fields inferred for this doc." />;

  return (
    <div className="rounded border border-zinc-200 divide-y divide-zinc-100 overflow-hidden">
      {data.map((f) => {
        const v = (f.value_text ?? "").trim();
        const hasExact =
          f.source_chunk_id != null &&
          f.source_char_start != null &&
          f.source_char_end != null;
        const clickable = hasExact || v.length > 1;
        const c: Citation | null = hasExact
          ? {
              kind: "exact",
              chunkId: f.source_chunk_id!,
              start: f.source_char_start!,
              end: f.source_char_end!,
              pages: f.source_page_numbers ?? undefined,
            }
          : clickable
            ? { kind: "text", text: v }
            : null;
        const active =
          !!c &&
          ((citation?.kind === "exact" &&
            c.kind === "exact" &&
            citation.chunkId === c.chunkId &&
            citation.start === c.start) ||
            (citation?.kind === "text" &&
              c.kind === "text" &&
              citation.text === c.text));
        return (
          <button
            key={f.id}
            type="button"
            onClick={() => c && cite(c)}
            disabled={!clickable}
            className={`w-full grid grid-cols-[minmax(140px,200px)_1fr_70px] gap-3 px-3 py-2 items-start text-xs text-left transition-colors ${
              active
                ? "bg-amber-50"
                : clickable
                  ? "hover:bg-zinc-50"
                  : ""
            }`}
            title={clickable ? "Click to highlight in source" : undefined}
          >
            <div className="mono text-zinc-900 min-w-0 break-all">
              {f.field_name}
            </div>
            <div className="text-zinc-800 min-w-0 break-words">
              <div>{f.value_text ?? <span className="text-zinc-400">—</span>}</div>
              {f.field_description && (
                <div className="text-[11px] text-zinc-500 mt-0.5">
                  {f.field_description}
                </div>
              )}
            </div>
            <div className="text-[11px] mono text-zinc-500 text-right min-w-0">
              {f.value_type ?? "—"}
              {f.is_pii && (
                <span className="ml-1 px-1 py-0.5 rounded bg-rose-50 text-rose-700">
                  PII
                </span>
              )}
            </div>
          </button>
        );
      })}
    </div>
  );
}

function AtomicUnitsSection({ fileId, total }: { fileId: string; total: number }) {
  return (
    <Accordion
      icon={FileText}
      title="Sub-entities (transactions · clauses · line items · …)"
      count={total}
      testId="doc-detail-units"
    >
      {(open) => <PaginatedAccordion fileId={fileId} open={open} total={total} renderItems={renderUnits} fetcher={getSubEntities} pageSize={25} />}
    </Accordion>
  );
}

function renderUnits(items: AtomicUnit[]) {
  return <UnitList items={items} />;
}

function UnitList({ items }: { items: AtomicUnit[] }) {
  const { cite, citation } = useCitation();

  // Nested-entities refactor: group by unit_type so the user sees
  // "Transaction (21)", "Clause (12)" instead of one mixed list.
  // Preserves rarity ordering inside each group (server already
  // returned items sorted by rarity DESC).
  const grouped: Record<string, AtomicUnit[]> = {};
  for (const u of items) {
    const key = u.unit_type || "(other)";
    (grouped[key] = grouped[key] || []).push(u);
  }
  // Sort groups by their highest-rarity item (so the most-anomalous
  // category surfaces first).
  const groupKeys = Object.keys(grouped).sort((a, b) => {
    const ra = grouped[a][0]?.rarity_score ?? 0;
    const rb = grouped[b][0]?.rarity_score ?? 0;
    return rb - ra;
  });

  return (
    <div className="space-y-4">
      {groupKeys.map((groupKey) => {
        const groupItems = grouped[groupKey];
        return (
          <div key={groupKey}>
            <div className="text-[10px] uppercase tracking-wider text-zinc-400 mb-1.5 flex items-center gap-1.5">
              <span>{groupKey}</span>
              <span className="mono text-zinc-400">{groupItems.length}</span>
            </div>
            <div className="space-y-2">
              {groupItems.map((u) => {
                const c = citationForUnit(u);
                const active =
                  c && citation && JSON.stringify(c) === JSON.stringify(citation);
                return (
                  <button
                    key={u.id}
                    type="button"
                    onClick={() => cite(c)}
                    className={`w-full text-left rounded border p-2.5 transition-colors ${
                      active
                        ? "border-amber-300 bg-amber-50"
                        : "border-zinc-200 hover:bg-zinc-50"
                    }`}
                    title={c ? "Click to highlight in source" : undefined}
                  >
                    <div className="flex items-center gap-2 text-[10px] mono text-zinc-500 mb-1">
                      <span className="px-1 py-0.5 rounded bg-zinc-100 uppercase">
                        {u.unit_type}
                      </span>
                      <span className="ml-auto">
                        rarity {u.rarity_score?.toFixed(2) ?? "—"}
                      </span>
                    </div>
                    <pre className="text-[12px] text-zinc-800 whitespace-pre-wrap font-sans leading-snug">
                      {prettyParameters(u.parameters)}
                    </pre>
                  </button>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** Pick the best citation shape for an atomic_unit. Priority:
 *  1. xlsx row coordinates (sheet + row_index) — pinpoint precision
 *  2. worker-resolved source offsets (clause summary located in source)
 *  3. fallback text search */
function citationForUnit(u: AtomicUnit): Citation | null {
  const p = u.parameters as { sheet_name?: string; row_index?: number; text?: string; summary?: string };
  if (typeof p.row_index === "number") {
    return { kind: "xlsx-row", sheet: p.sheet_name, rowIndex: p.row_index };
  }
  if (
    u.source_chunk_id != null &&
    u.source_char_start != null &&
    u.source_char_end != null
  ) {
    return {
      kind: "exact",
      chunkId: u.source_chunk_id,
      start: u.source_char_start,
      end: u.source_char_end,
      pages: u.source_page_numbers ?? undefined,
    };
  }
  if (typeof p.summary === "string" && p.summary.length > 4) {
    return { kind: "text", text: p.summary.slice(0, 200) };
  }
  if (typeof p.text === "string" && p.text.length > 4) {
    return { kind: "text", text: p.text.slice(0, 200) };
  }
  return null;
}

function ExtractedEntitiesSection({ fileId }: { fileId: string }) {
  return (
    <Accordion
      icon={FileText}
      title="Schema entity instances (L4 closed-world)"
      testId="doc-detail-schema-entities"
    >
      {(open) => <ExtractedEntitiesBody fileId={fileId} open={open} />}
    </Accordion>
  );
}

function ExtractedEntitiesBody({ fileId, open }: { fileId: string; open: boolean }) {
  const [data, err] = useLazy(
    useCallback(() => getExtractedEntities(fileId), [fileId]),
    open,
  );
  if (err) return <ErrorRow message={err} />;
  if (!data) return <LoadingRow />;
  if (data.length === 0)
    return (
      <EmptyRow message="No schema entity instances extracted from this doc — either there's no matching schema_entity for this doc_type yet, or the closed-world extractor produced nothing." />
    );

  return (
    <div className="space-y-2">
      {data.map((e) => (
        <div key={e.id} className="rounded border border-zinc-200 p-3">
          <div className="text-[11px] mono text-zinc-500 mb-1.5">
            {e.schema_entity_name ?? e.schema_entity_id}
          </div>
          <pre className="text-[12px] text-zinc-800 whitespace-pre-wrap font-sans">
            {JSON.stringify(e.fields, null, 2)}
          </pre>
        </div>
      ))}
    </div>
  );
}

function MentionsSection({ fileId, total }: { fileId: string; total: number }) {
  return (
    <Accordion
      icon={FileText}
      title="Mentions (L2 surface forms)"
      count={total}
      warn={total === 0}
      testId="doc-detail-mentions"
    >
      {(open) => (
        <PaginatedAccordion
          fileId={fileId}
          open={open}
          total={total}
          renderItems={renderMentions}
          fetcher={getDocMentions}
          pageSize={50}
        />
      )}
    </Accordion>
  );
}

function renderMentions(items: Mention[]) {
  return <MentionList items={items} />;
}

function MentionList({ items }: { items: Mention[] }) {
  const { cite, citation } = useCitation();
  return (
    <div className="space-y-1">
      {items.map((m) => {
        // Prefer worker-resolved exact offsets (migration 0032). Fall
        // back to text-search for rows the resolver couldn't repair.
        const c: Citation =
          m.source_chunk_id != null &&
          m.source_char_start != null &&
          m.source_char_end != null
            ? {
                kind: "exact",
                chunkId: m.source_chunk_id,
                start: m.source_char_start,
                end: m.source_char_end,
                pages: m.source_page_numbers ?? undefined,
              }
            : {
                kind: "text",
                text: m.mention_text,
                page: m.source_page_numbers ?? undefined,
                chunkId: m.chunk_id,
              };
        const active =
          (citation?.kind === "exact" &&
            c.kind === "exact" &&
            citation.chunkId === c.chunkId &&
            citation.start === c.start) ||
          (citation?.kind === "text" &&
            c.kind === "text" &&
            citation.text === c.text);
        return (
          <button
            key={m.id}
            type="button"
            onClick={() => cite(c)}
            className={`w-full grid grid-cols-[80px_1fr_120px_80px] gap-2 px-2 py-1.5 rounded text-xs items-center text-left transition-colors ${
              active ? "bg-amber-50" : "hover:bg-zinc-50"
            }`}
            title="Click to highlight in source"
          >
            <span className="text-[10px] mono px-1 py-0.5 rounded bg-zinc-100 text-zinc-700 w-fit">
              {m.mention_type}
            </span>
            <span className="text-zinc-900 truncate min-w-0">{m.mention_text}</span>
            <span className="text-zinc-500 mono text-[11px] truncate min-w-0">
              {m.canonical_name ?? "— unlinked"}
            </span>
            <span className="text-zinc-400 mono text-[11px] text-right">
              {m.confidence !== null ? m.confidence.toFixed(2) : "—"}
            </span>
          </button>
        );
      })}
    </div>
  );
}

function EntitiesMentionedSection({
  fileId,
  total,
}: {
  fileId: string;
  total: number;
}) {
  return (
    <Accordion
      icon={FileText}
      title="Entities mentioned (L4 canonical)"
      count={total}
      warn={total === 0}
      testId="doc-detail-entities"
    >
      {(open) => (
        <PaginatedAccordion
          fileId={fileId}
          open={open}
          total={total}
          renderItems={renderEntities}
          fetcher={getEntitiesMentioned}
          pageSize={50}
        />
      )}
    </Accordion>
  );
}

function renderEntities(items: EntityMentioned[]) {
  return (
    <div className="space-y-1">
      {items.map((e) => (
        <div
          key={e.entity_id}
          className="grid grid-cols-[80px_1fr_80px_100px] gap-2 px-2 py-1.5 rounded hover:bg-zinc-50 text-xs items-center"
        >
          <span className="text-[10px] mono px-1 py-0.5 rounded bg-zinc-100 text-zinc-700 w-fit">
            {e.entity_type}
          </span>
          <span className="text-zinc-900 truncate min-w-0">{e.canonical_name}</span>
          <span className="text-zinc-500 mono text-[11px] text-right">
            {e.mentions_in_doc} in doc
          </span>
          <span className="text-zinc-400 mono text-[11px] text-right">
            {e.total_mentions} corpus
          </span>
        </div>
      ))}
    </div>
  );
}

function TriplesSection({ fileId, total }: { fileId: string; total: number }) {
  return (
    <Accordion
      icon={FileText}
      title="Relationships (L4 triples)"
      count={total}
      testId="doc-detail-triples"
    >
      {(open) => (
        <PaginatedAccordion
          fileId={fileId}
          open={open}
          total={total}
          renderItems={renderTriples}
          fetcher={getDocTriples}
          pageSize={50}
        />
      )}
    </Accordion>
  );
}

function renderTriples(items: TripleInDoc[]) {
  return <TripleList items={items} />;
}

function TripleList({ items }: { items: TripleInDoc[] }) {
  const { cite, citation } = useCitation();
  return (
    <div className="rounded border border-zinc-200 divide-y divide-zinc-100 overflow-hidden">
      {items.map((t) => {
        // Prefer worker-resolved offsets on the SUBJECT (the most
        // meaningful "where did this fact start" anchor). Object
        // offsets are also stored on the row; we expose them on hover
        // later.
        const c: Citation =
          t.chunk_id != null &&
          t.subject_char_start != null &&
          t.subject_char_end != null
            ? {
                kind: "exact",
                chunkId: t.chunk_id,
                start: t.subject_char_start,
                end: t.subject_char_end,
                pages: t.source_page_numbers ?? undefined,
              }
            : t.chunk_id != null &&
                t.object_char_start != null &&
                t.object_char_end != null
              ? {
                  kind: "exact",
                  chunkId: t.chunk_id,
                  start: t.object_char_start,
                  end: t.object_char_end,
                  pages: t.source_page_numbers ?? undefined,
                }
              : {
                  kind: "text",
                  text: t.subject_text,
                  page: t.source_page_numbers ?? undefined,
                  chunkId: t.chunk_id,
                };
        const active =
          (citation?.kind === "exact" &&
            c.kind === "exact" &&
            citation.chunkId === c.chunkId &&
            citation.start === c.start) ||
          (citation?.kind === "text" &&
            c.kind === "text" &&
            citation.text === c.text);
        return (
          <button
            key={t.id}
            type="button"
            onClick={() => cite(c)}
            className={`w-full grid grid-cols-[1fr_auto_1fr_50px] gap-2 px-3 py-2 items-center text-xs text-left transition-colors ${
              active ? "bg-amber-50" : "hover:bg-zinc-50"
            }`}
            title="Click to highlight in source"
          >
            <span className="text-zinc-900 truncate min-w-0">{t.subject_text}</span>
            <span className="text-zinc-400 mono text-[11px] whitespace-nowrap">
              — {t.predicate_text} →
            </span>
            <span className="text-zinc-900 truncate min-w-0">{t.object_text}</span>
            <span className="text-zinc-400 mono text-[11px] text-right">
              {t.confidence !== null ? t.confidence.toFixed(2) : "—"}
            </span>
          </button>
        );
      })}
    </div>
  );
}

function ChainSection({ details }: { details: FileDetails }) {
  if (!details.chain_id) {
    return (
      <Accordion icon={FileText} title="Revision history" testId="doc-detail-chain">
        {() => (
          <EmptyRow message="Not part of a detected doc chain." />
        )}
      </Accordion>
    );
  }
  return (
    <Accordion
      icon={FileText}
      title="Revision history"
      count={`v${details.chain_version_index ?? "?"}${
        details.is_current_version ? " · current" : ""
      }`}
      testId="doc-detail-chain"
    >
      {() => (
        <div className="text-[12px] text-zinc-700">
          <div>
            chain {" "}
            <span className="mono text-zinc-900">
              {details.chain_id?.slice(0, 8)}…
            </span>{" "}
            · role {" "}
            <span className="mono text-zinc-900">{details.chain_role}</span>
          </div>
          <div className="text-[11px] text-zinc-500 mt-1">
            Sibling versions in this chain aren&apos;t fetched yet — only this
            doc&apos;s membership is shown. (Other members will list when the
            /doc-chains/&#123;id&#125; endpoint is wired into this section.)
          </div>
        </div>
      )}
    </Accordion>
  );
}

function ProcessingLogSection({ details }: { details: FileDetails }) {
  return (
    <Accordion
      icon={FileText}
      title="Processing log"
      count={`${details.lifecycle.length} event${details.lifecycle.length === 1 ? "" : "s"}`}
      testId="doc-detail-processing"
    >
      {() => <ProcessingLog events={details.lifecycle} />}
    </Accordion>
  );
}

function ProcessingLog({ events }: { events: LifecycleEventDetail[] }) {
  if (events.length === 0)
    return <EmptyRow message="No lifecycle events recorded." />;
  const t0 = new Date(events[0].created_at).getTime();
  return (
    <div className="rounded border border-zinc-200 divide-y divide-zinc-100 max-h-[300px] overflow-y-auto">
      {events.map((ev, i) => {
        const t = new Date(ev.created_at).getTime();
        const dt = ((t - t0) / 1000).toFixed(1);
        return (
          <div
            key={i}
            className="grid grid-cols-[60px_1fr_140px] gap-2 px-3 py-1.5 text-[11px] mono"
          >
            <span className="text-zinc-500 text-right">+{dt}s</span>
            <span className="text-zinc-900">{ev.event}</span>
            <span className="text-zinc-500 truncate">
              {ev.from_state ?? "·"} → {ev.to_state}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function CitationsSection({ fileId }: { fileId: string }) {
  return (
    <Accordion
      icon={FileText}
      title="Cited in chat answers"
      testId="doc-detail-citations"
    >
      {(open) => <CitationsBody fileId={fileId} open={open} />}
    </Accordion>
  );
}

function CitationsBody({ fileId, open }: { fileId: string; open: boolean }) {
  const [data, err] = useLazy(
    useCallback(() => getDocCitations(fileId, { limit: 20 }), [fileId]),
    open,
  );
  if (err) return <ErrorRow message={err} />;
  if (!data) return <LoadingRow />;
  if (data.total === 0)
    return <EmptyRow message="Not cited in any chat answer yet." />;

  return (
    <div className="space-y-2">
      <div className="text-[11px] text-zinc-500 mono">
        Showing {data.items.length} of {data.total}
      </div>
      {data.items.map((c) => (
        <div
          key={c.query_id}
          className="rounded border border-zinc-200 p-2.5 text-xs"
        >
          <div className="flex items-center gap-2 text-[11px] mono text-zinc-500 mb-1">
            <span className="px-1 py-0.5 rounded bg-zinc-100 text-zinc-700">
              {c.endpoint}
            </span>
            <span className="ml-auto">{c.created_at}</span>
          </div>
          <div className="text-zinc-700 line-clamp-1">Q: {c.query}</div>
          {c.answer && (
            <div className="text-zinc-500 line-clamp-2 mt-0.5">A: {c.answer}</div>
          )}
        </div>
      ))}
    </div>
  );
}


// ---------------------------------------------------------------------------
// Pagination + utility rows
// ---------------------------------------------------------------------------

function PaginatedAccordion<T>({
  fileId,
  open,
  total,
  renderItems,
  fetcher,
  pageSize,
}: {
  fileId: string;
  open: boolean;
  total: number;
  renderItems: (items: T[]) => ReactNode;
  fetcher: (
    id: string,
    opts?: { limit?: number; offset?: number },
  ) => Promise<Paginated<T>>;
  pageSize: number;
}) {
  const [items, setItems] = useState<T[]>([]);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!open || items.length > 0) return;
    setLoading(true);
    fetcher(fileId, { limit: pageSize, offset: 0 })
      .then((r) => {
        setItems(r.items);
        setOffset(r.items.length);
      })
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  if (err) return <ErrorRow message={err} />;
  if (loading && items.length === 0) return <LoadingRow />;
  if (total === 0)
    return <EmptyRow message="Nothing here for this doc." />;

  const hasMore = offset < total;
  return (
    <div className="space-y-2">
      {renderItems(items)}
      <div className="flex items-center justify-between text-[11px] text-zinc-500 mono pt-1">
        <span>
          showing {items.length} of {total}
        </span>
        {hasMore && (
          <button
            type="button"
            disabled={loading}
            onClick={() => {
              setLoading(true);
              fetcher(fileId, { limit: pageSize, offset })
                .then((r) => {
                  setItems((prev) => [...prev, ...r.items]);
                  setOffset((o) => o + r.items.length);
                })
                .catch((e) => setErr(String(e)))
                .finally(() => setLoading(false));
            }}
            className="px-2 py-1 rounded border border-zinc-200 text-zinc-700 hover:bg-zinc-50 disabled:opacity-50"
          >
            {loading ? "loading…" : "load more"}
          </button>
        )}
      </div>
    </div>
  );
}

function LoadingRow() {
  return <div className="text-xs text-zinc-500 py-2">Loading…</div>;
}

function EmptyRow({ message }: { message: string }) {
  return <div className="text-xs text-zinc-500 py-2 italic">{message}</div>;
}

function ErrorRow({ message }: { message: string }) {
  return (
    <div className="text-xs text-red-700 py-2 mono break-all">{message}</div>
  );
}
