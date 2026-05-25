"use client";

import { useRef, useState } from "react";
import { UploadCloud } from "lucide-react";
import { uploadFile } from "@/lib/api";
import { useUploadStore } from "@/lib/state";

/**
 * Drag-drop + click-to-browse dropzone. Each accepted file POSTs to /files
 * with a fresh Idempotency-Key, then the page-level effect subscribes to
 * the lifecycle SSE per file.
 */
export function DropZone() {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { dispatch } = useUploadStore();

  async function handleFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    setError(null);
    for (let i = 0; i < files.length; i += 1) {
      const file = files[i];
      try {
        const resource = await uploadFile(file);
        dispatch({ type: "upserted", file: resource });
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        setError(`Upload failed: ${msg}`);
      }
    }
  }

  return (
    <div className="mb-6">
      <div
        className={`dropzone border border-dashed rounded-xl bg-white px-8 py-10 cursor-pointer ${
          dragging ? "dragging border-zinc-900 bg-zinc-50" : "border-zinc-300"
        }`}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          void handleFiles(e.dataTransfer.files);
        }}
        role="button"
        tabIndex={0}
        data-testid="dropzone"
      >
        <div className="flex items-center justify-center flex-col text-center">
          <div className="w-10 h-10 rounded-lg bg-zinc-100 flex items-center justify-center mb-3 text-zinc-500">
            <UploadCloud className="w-5 h-5" strokeWidth={1.75} />
          </div>
          <div className="text-sm font-medium text-zinc-900">
            Drop files here
          </div>
          <div className="text-xs text-zinc-500 mt-1">
            or{" "}
            <span className="text-zinc-900 underline underline-offset-2">
              click to browse
            </span>
          </div>
          <div className="text-[11px] text-zinc-400 mt-3 mono">
            PDF · xlsx · csv · jpg · png · eml
          </div>
        </div>
        <input
          ref={inputRef}
          type="file"
          multiple
          className="hidden"
          onChange={(e) => {
            void handleFiles(e.target.files);
            e.target.value = "";
          }}
          aria-label="Upload files"
        />
      </div>

      {error && (
        <div className="mt-3 text-xs text-red-600 mono" role="alert">
          {error}
        </div>
      )}
    </div>
  );
}
