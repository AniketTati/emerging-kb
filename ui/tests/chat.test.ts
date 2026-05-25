/**
 * Vitest unit tests for the chat helpers — segmentAnswer (inline-citation
 * tokenizer) and the chat-state reducer.
 */

import { describe, expect, it } from "vitest";

import {
  segmentAnswer,
  type Citation,
  type ChatResponse,
} from "@/lib/api";
import { initialState, reducer } from "@/lib/chat-state";

const fakeCitations = (n: number): Citation[] =>
  Array.from({ length: n }, (_, i) => ({
    hit_id: `${"abcdef12".slice(0, 8)}-aaaa-bbbb-cccc-dddddddddddd`.replace(
      "abcdef12",
      `cafe000${i}`,
    ),
    kind: "chunk",
    file_id: "f1",
    snippet_preview: `snippet ${i}`,
    score: 0.9 - i * 0.05,
  }));

describe("segmentAnswer", () => {
  it("returns a single text segment when there are no citations", () => {
    const segs = segmentAnswer("plain answer with no refs.", []);
    expect(segs).toEqual([{ kind: "text", value: "plain answer with no refs." }]);
  });

  it("emits cite segments for inline [hit_id] markers", () => {
    const cites = fakeCitations(2);
    const text = `First fact [${cites[0].hit_id.slice(0, 8)}] and another [${cites[1].hit_id.slice(0, 8)}].`;
    const segs = segmentAnswer(text, cites);
    const kinds = segs.map((s) => s.kind);
    expect(kinds).toContain("cite");
    expect(kinds.filter((k) => k === "cite").length).toBe(2);
  });

  it("falls back to index -1 for unknown hit ids", () => {
    const segs = segmentAnswer("Mystery [deadbeef] one.", []);
    const cite = segs.find((s) => s.kind === "cite");
    expect(cite).toBeDefined();
    if (cite && cite.kind === "cite") {
      expect(cite.index).toBe(-1);
    }
  });

  it("preserves text between cite markers", () => {
    const cites = fakeCitations(1);
    const text = `Hello [${cites[0].hit_id.slice(0, 8)}] world.`;
    const segs = segmentAnswer(text, cites);
    const reassembled = segs
      .map((s) => (s.kind === "text" ? s.value : "[X]"))
      .join("");
    expect(reassembled).toBe("Hello [X] world.");
  });
});

describe("reducer", () => {
  it("user_sent appends a user turn + a pending assistant turn", () => {
    const s = reducer(initialState, {
      type: "user_sent",
      userId: "u1",
      assistantId: "a1",
      content: "hello",
    });
    expect(s.turns.length).toBe(2);
    expect(s.turns[0]).toMatchObject({ id: "u1", role: "user", content: "hello" });
    expect(s.turns[1]).toMatchObject({
      id: "a1",
      role: "assistant",
      pending: true,
    });
  });

  it("assistant_answered fills the response and clears pending", () => {
    const response: ChatResponse = {
      query_id: "q1",
      query: "hello",
      rewrites: { original: "hello" },
      generation: {
        answer: "world",
        citations: [],
        refused: false,
        refusal_reason: null,
        model_id: "fake",
      },
      hits: [],
      crag_score: 0.7,
      latency_ms: 100,
    };
    let s = reducer(initialState, {
      type: "user_sent",
      userId: "u1",
      assistantId: "a1",
      content: "hello",
    });
    s = reducer(s, { type: "assistant_answered", assistantId: "a1", response });
    expect(s.turns[1].pending).toBe(false);
    expect(s.turns[1].content).toBe("world");
    expect(s.turns[1].response?.crag_score).toBe(0.7);
  });

  it("assistant_errored stores an error on the assistant turn", () => {
    let s = reducer(initialState, {
      type: "user_sent",
      userId: "u1",
      assistantId: "a1",
      content: "hello",
    });
    s = reducer(s, {
      type: "assistant_errored",
      assistantId: "a1",
      error: "boom",
    });
    expect(s.turns[1].pending).toBe(false);
    expect(s.turns[1].error).toBe("boom");
  });
});
