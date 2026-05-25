"use client";

import { stageIndexFor, stageLabelFor, type LifecycleState } from "@/lib/api";

const STAGE_NAMES = ["parse", "embed", "raptor", "extract", "ready"] as const;

export function StageBadge({ state }: { state: LifecycleState }) {
  const idx = stageIndexFor(state);

  if (state === "failed") {
    return (
      <div className="flex items-center gap-2">
        <span
          className="inline-block w-1.5 h-1.5 rounded-full bg-red-500"
          aria-hidden="true"
        />
        <span className="text-xs text-red-600">failed</span>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2" title={STAGE_NAMES.join(" → ")}>
      <div className="flex items-center gap-0.5">
        {STAGE_NAMES.map((name, i) => {
          const filled = i < idx;
          const active = i === idx && state !== "ready";
          const done = state === "ready";
          let cls = "bg-zinc-200";
          if (done || filled) cls = "bg-zinc-900";
          if (active) cls = "bg-zinc-900 pip-active";
          return (
            <span
              key={name}
              className={`w-1.5 h-1.5 rounded-full ${cls}`}
              aria-label={`stage ${name} ${active ? "(active)" : filled || done ? "(done)" : "(pending)"}`}
            />
          );
        })}
      </div>
      <span className="text-xs text-zinc-600">{stageLabelFor(state)}</span>
    </div>
  );
}
