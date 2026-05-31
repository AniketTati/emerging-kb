/**
 * Vitest unit tests for failureReasonFrom (P6a) — the pure extractor that
 * pulls error_class / message / traceback_head off the failed lifecycle event.
 */

import { describe, expect, it } from "vitest";

import { failureReasonFrom } from "@/components/FilesTable";
import type { FileDetails } from "@/lib/api";

type Lifecycle = FileDetails["lifecycle"];

function ev(to: string, event: string, payload: Record<string, unknown> = {}): Lifecycle[number] {
  return { from_state: null, to_state: to as never, event, payload, created_at: "2026-01-01T00:00:00Z" };
}

describe("failureReasonFrom", () => {
  it("returns null when the file never failed", () => {
    const lc: Lifecycle = [ev("parsed", "parse_done"), ev("ready", "ready")];
    expect(failureReasonFrom(lc)).toBeNull();
  });

  it("extracts error_class + message + traceback from a failed event", () => {
    const lc: Lifecycle = [
      ev("parsing", "parse_started"),
      ev("failed", "parse_failed", {
        error_class: "OCRConfigError",
        message: "GEMINI_API_KEY not set",
        traceback_head: "Traceback…",
      }),
    ];
    expect(failureReasonFrom(lc)).toEqual({
      event: "parse_failed",
      errorClass: "OCRConfigError",
      message: "GEMINI_API_KEY not set",
      traceback: "Traceback…",
    });
  });

  it("tolerates a failed event with no payload fields", () => {
    const lc: Lifecycle = [ev("failed", "chunk_failed")];
    expect(failureReasonFrom(lc)).toEqual({
      event: "chunk_failed",
      errorClass: null,
      message: null,
      traceback: null,
    });
  });

  it("picks the most-recent failed transition on re-runs", () => {
    const lc: Lifecycle = [
      ev("failed", "parse_failed", { error_class: "First", message: "old" }),
      ev("parsing", "reparse"),
      ev("failed", "extract_failed", { error_class: "Second", message: "new" }),
    ];
    expect(failureReasonFrom(lc)?.errorClass).toBe("Second");
  });
});
