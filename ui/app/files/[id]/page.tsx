"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { Sidebar } from "@/components/Sidebar";
import { DocDetail } from "@/components/DocDetail";

/**
 * /files/[id] — doc-detail audit view.
 *
 * The user opens this from the Upload table to verify the full
 * extraction stack (L0 source → L1 chunks → L2 mentions → L3 fields +
 * units → L4 schema entities + canonical entity links → relationships
 * → citations). Each layer is its own lazy-loaded accordion; lists
 * paginate so a 500-page doc stays workable.
 */
export default function DocDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  return (
    <div className="flex h-full">
      <Sidebar current="upload" />
      <main className="flex-1 flex flex-col min-w-0">
        <header className="h-12 flex-shrink-0 border-b border-zinc-200 flex items-center px-5 gap-3">
          <Link
            href="/upload"
            className="flex items-center gap-1.5 text-zinc-500 hover:text-zinc-900 text-sm"
          >
            <ArrowLeft className="w-3.5 h-3.5" strokeWidth={1.75} />
            Upload
          </Link>
          <span className="text-zinc-300">·</span>
          <span className="text-sm text-zinc-900">Doc detail</span>
        </header>
        <div className="flex-1 min-h-0 flex flex-col">
          {mounted ? <DocDetail fileId={id} /> : null}
        </div>
      </main>
    </div>
  );
}
