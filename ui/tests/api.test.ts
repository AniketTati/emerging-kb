/**
 * Vitest unit tests for ui/lib/api.ts pure helpers.
 * No real HTTP — pure function checks for stage projections + label formatting.
 */

import { describe, expect, it } from "vitest";

import {
  isTerminal,
  stageIndexFor,
  stageLabelFor,
  type LifecycleState,
} from "@/lib/api";

describe("stageIndexFor", () => {
  it("maps parsing → 0", () => {
    expect(stageIndexFor("parsing")).toBe(0);
    expect(stageIndexFor("parsed")).toBe(0);
  });

  it("maps embed-cluster → 1", () => {
    for (const s of ["chunked", "contextualized", "embedded"] as LifecycleState[]) {
      expect(stageIndexFor(s)).toBe(1);
    }
  });

  it("maps raptor → 2", () => {
    expect(stageIndexFor("raptor_building")).toBe(2);
  });

  it("maps all *_extracting + identity → 3", () => {
    for (const s of [
      "mentions_extracting",
      "fields_extracting",
      "units_extracting",
      "entities_extracting",
      "identity_resolving",
    ] as LifecycleState[]) {
      expect(stageIndexFor(s)).toBe(3);
    }
  });

  it("maps ready → 4 (terminal)", () => {
    expect(stageIndexFor("ready")).toBe(4);
  });

  it("maps queued + failed + deleted → -1 (not on the 5-pip line)", () => {
    expect(stageIndexFor("queued")).toBe(-1);
    expect(stageIndexFor("failed")).toBe(-1);
    expect(stageIndexFor("deleted")).toBe(-1);
  });
});

describe("isTerminal", () => {
  it("ready / failed / deleted are terminal", () => {
    expect(isTerminal("ready")).toBe(true);
    expect(isTerminal("failed")).toBe(true);
    expect(isTerminal("deleted")).toBe(true);
  });

  it("intermediate states are not terminal", () => {
    expect(isTerminal("queued")).toBe(false);
    expect(isTerminal("parsing")).toBe(false);
    expect(isTerminal("raptor_building")).toBe(false);
  });
});

describe("stageLabelFor", () => {
  it("turns underscores into spaces", () => {
    expect(stageLabelFor("raptor_building")).toBe("raptor building");
    expect(stageLabelFor("mentions_extracting")).toBe("mentions extracting");
  });

  it("passes through simple labels", () => {
    expect(stageLabelFor("ready")).toBe("ready");
    expect(stageLabelFor("failed")).toBe("failed");
  });
});
