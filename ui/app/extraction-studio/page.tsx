"use client";

/**
 * /extraction-studio — Wave C surface.
 *
 * Intentional roadmap page so nav doesn't 404. The real surface ships
 * a per-doc PDF + extracted-fields side-by-side editor (approve / edit
 * / reject), a prompt editor with version diffing, and a test-mode
 * sandbox that re-runs extraction without persisting. None of that is
 * built; this page is honest about it.
 *
 * Today: link to the existing extraction-related surfaces that already
 * ship: /upload (re-extract per file), /schema-studio (inferred field
 * promotion), /files/[id] (per-doc inspection).
 */

import Link from "next/link";
import { FlaskConical, ArrowRight, ScrollText } from "lucide-react";
import { Sidebar } from "@/components/Sidebar";


const SHIPPED_NOW: { href: string; label: string; desc: string }[] = [
  {
    href: "/upload",
    label: "Re-extract on a single file",
    desc: "Expanded row → Re-extract / Re-parse from scratch. New extracted rows overwrite the old ones via per-file idempotency.",
  },
  {
    href: "/schema-studio?tab=inferred",
    label: "Promote an inferred field",
    desc: "Threshold bars + Promote / Rename / Discard. Once promoted, all docs of that type re-project the new field on next extraction.",
  },
  {
    href: "/files",
    label: "Inspect a doc's full extraction stack",
    desc: "L0 source → L1 chunks → L2 mentions → L3 fields + units → L4 entities → relationships. One row, every layer.",
  },
];


const PLANNED: { label: string; sub: string }[] = [
  {
    label: "Per-doc PDF + extracted fields side-by-side",
    sub: "Click a field, jump to the citation. Edit the value inline; persists as a correction (Design 4).",
  },
  {
    label: "Approve / Edit / Reject per field",
    sub: "Bulk-approve high-confidence rows; queue low-confidence ones for the human-in-the-loop.",
  },
  {
    label: "Prompt editor with version diffing",
    sub: "Edit the extraction prompt for a doc-type, see which fields change, A/B against the live prompt before promoting.",
  },
  {
    label: "Test mode — re-run without persisting",
    sub: "Run extraction against a doc with a candidate prompt; results render side-by-side with the persisted extraction. Nothing is saved unless you click Promote.",
  },
];


export default function ExtractionStudioPage() {
  return (
    <div className="flex h-full">
      <Sidebar current="extraction" />
      <main className="flex-1 flex flex-col min-w-0 bg-zinc-50/40">
        <header className="h-12 flex-shrink-0 border-b border-zinc-200 flex items-center px-5 gap-3 bg-white">
          <FlaskConical className="w-4 h-4 text-zinc-500" strokeWidth={1.75} />
          <span className="text-sm text-zinc-900">Extraction Studio</span>
          <span className="text-[11px] text-zinc-400 mono">
            Wave C surface · roadmap
          </span>
        </header>

        <div className="flex-1 overflow-y-auto">
          <div className="max-w-4xl mx-auto px-8 py-10">
            <div className="mb-8">
              <div className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-amber-50 border border-amber-200 text-[11px] mono text-amber-900 mb-3">
                roadmap · Wave C
              </div>
              <h1 className="text-2xl font-semibold text-zinc-900">
                Extraction Studio
              </h1>
              <p className="text-sm text-zinc-600 mt-2 leading-relaxed max-w-2xl">
                A dedicated workbench for editing extracted fields, tuning
                doc-type prompts, and approving / rejecting low-confidence
                extractions before they land in the schema. The full
                surface is a Wave C deliverable — the prototype design
                lives in{" "}
                <a
                  href="https://github.com/AniketTati/emerging-kb/blob/main/prototype/extraction-studio.html"
                  className="mono text-zinc-700 hover:text-zinc-900 underline"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  prototype/extraction-studio.html
                </a>
                .
              </p>
            </div>

            {/* What you can do today */}
            <section className="mb-8">
              <h2 className="text-sm font-semibold text-zinc-900 mb-3 flex items-center gap-2">
                <ScrollText className="w-4 h-4 text-zinc-500" strokeWidth={1.75} />
                What you can do today
              </h2>
              <div className="space-y-2">
                {SHIPPED_NOW.map((item) => (
                  <Link
                    key={item.href}
                    href={item.href}
                    className="block rounded-lg border border-zinc-200 bg-white p-4 hover:border-zinc-400 hover:bg-zinc-50/40 transition-colors"
                  >
                    <div className="flex items-center gap-3">
                      <div className="flex-1 min-w-0">
                        <div className="text-sm font-medium text-zinc-900">
                          {item.label}
                        </div>
                        <div className="text-xs text-zinc-500 mt-0.5 leading-relaxed">
                          {item.desc}
                        </div>
                      </div>
                      <ArrowRight className="w-4 h-4 text-zinc-400 flex-shrink-0" strokeWidth={1.75} />
                    </div>
                  </Link>
                ))}
              </div>
            </section>

            {/* What's planned */}
            <section>
              <h2 className="text-sm font-semibold text-zinc-900 mb-3">
                What this page will do when it ships
              </h2>
              <div className="rounded-lg border border-zinc-200 bg-white divide-y divide-zinc-100">
                {PLANNED.map((p) => (
                  <div key={p.label} className="px-4 py-3">
                    <div className="text-sm text-zinc-900">{p.label}</div>
                    <div className="text-xs text-zinc-500 mt-0.5 leading-relaxed">
                      {p.sub}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          </div>
        </div>
      </main>
    </div>
  );
}
