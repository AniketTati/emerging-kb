/**
 * Vitest unit tests for the upload reducer — focused on the pagination
 * actions added when /upload moved from "fetch all" to "load more".
 */

import { describe, expect, it } from "vitest";

import type { FileResource, LifecycleEvent } from "@/lib/api";
import { initialState, reducer } from "@/lib/state";

function file(id: string, name = `${id}.pdf`): FileResource {
  return {
    id,
    workspace_id: "ws",
    name,
    content_sha: "abc",
    mime_type: "application/pdf",
    size_bytes: 1024,
    lifecycle_state: "ready",
    created_at: "2026-01-01T00:00:00Z",
  } as FileResource;
}

describe("upload reducer — pagination", () => {
  it("seed sets total and replaces order", () => {
    const next = reducer(initialState, {
      type: "seed",
      files: [file("a"), file("b")],
      total: 17,
    });
    expect(next.order).toEqual(["a", "b"]);
    expect(next.total).toBe(17);
    expect(Object.keys(next.rows).sort()).toEqual(["a", "b"]);
  });

  it("appendPage extends order at the end, never shadowing seed", () => {
    const s1 = reducer(initialState, {
      type: "seed",
      files: [file("a"), file("b")],
      total: 10,
    });
    const s2 = reducer(s1, {
      type: "appendPage",
      files: [file("c"), file("d")],
      total: 10,
    });
    expect(s2.order).toEqual(["a", "b", "c", "d"]);
    expect(s2.total).toBe(10);
  });

  it("appendPage skips IDs already loaded (idempotent on overlap)", () => {
    // Simulates the race where the user uploads file "x" (dispatched via
    // `upserted`, lands at offset 0) and then immediately hits Load more,
    // which returns the SAME "x" at the top of page 2 from the server.
    // Without dedup we'd see two rows for the same file_id.
    const s1 = reducer(initialState, {
      type: "seed",
      files: [file("x"), file("a")],
      total: 50,
    });
    const s2 = reducer(s1, {
      type: "appendPage",
      files: [file("a"), file("b"), file("c")],
      total: 50,
    });
    expect(s2.order).toEqual(["x", "a", "b", "c"]);
  });

  it("upserted bumps total when the file is new", () => {
    const s1 = reducer(initialState, {
      type: "seed",
      files: [file("a")],
      total: 1,
    });
    const s2 = reducer(s1, { type: "upserted", file: file("b") });
    expect(s2.order).toEqual(["b", "a"]); // new ones land at the top
    expect(s2.total).toBe(2);
  });

  it("upserted leaves total alone when the file already exists", () => {
    const s1 = reducer(initialState, {
      type: "seed",
      files: [file("a")],
      total: 5,
    });
    const s2 = reducer(s1, {
      type: "upserted",
      file: { ...file("a"), lifecycle_state: "ready" },
    });
    expect(s2.total).toBe(5);
    expect(s2.order).toEqual(["a"]);
  });

  it("lifecycle events do not change order or total", () => {
    const s1 = reducer(initialState, {
      type: "seed",
      files: [file("a")],
      total: 9,
    });
    const ev: LifecycleEvent = {
      file_id: "a",
      from_state: "queued",
      to_state: "parsing",
    } as LifecycleEvent;
    const s2 = reducer(s1, { type: "lifecycle", event: ev });
    expect(s2.order).toEqual(["a"]);
    expect(s2.total).toBe(9);
    expect(s2.rows["a"].lifecycle_state).toBe("parsing");
  });
});
