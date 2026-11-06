import { describe, expect, it } from "vitest";
import { readSse, SseParser } from "./sse";

describe("SseParser", () => {
  it("parses named events with JSON data", () => {
    const p = new SseParser();
    const events = p.feed('event: agent\ndata: {"text":"Hi","agent":"billing"}\n\nevent: done\ndata: {}\n\n');
    expect(events).toEqual([
      { event: "agent", data: '{"text":"Hi","agent":"billing"}' },
      { event: "done", data: "{}" },
    ]);
  });

  it("handles events split across arbitrary chunk boundaries", () => {
    const raw = 'event: approval\r\ndata: {"approval_request_id":"ap_1","action":"apply_credit"}\r\n\r\n';
    const p = new SseParser();
    const events = [];
    for (const ch of raw) events.push(...p.feed(ch));
    events.push(...p.end());
    expect(events).toEqual([
      { event: "approval", data: '{"approval_request_id":"ap_1","action":"apply_credit"}' },
    ]);
  });

  it("joins multi-line data, ignores comments, and defaults the event name", () => {
    const p = new SseParser();
    const events = p.feed(": keep-alive\ndata: line one\ndata: line two\n\n");
    expect(events).toEqual([{ event: "message", data: "line one\nline two" }]);
  });

  it("accepts CR-only line endings", () => {
    const p = new SseParser();
    expect(p.feed("event: agent\rdata: {}\r\revent: done\rdata: {}\r\r")).toEqual([{ event: "agent", data: "{}" }]);
    // The trailing CR could be half of a CRLF, so it is held until more data or the end.
    expect(p.end()).toEqual([{ event: "done", data: "{}" }]);
  });

  it("treats a chunk ending in CR followed by a chunk starting with CR as two line ends", () => {
    const p = new SseParser();
    expect(p.feed("data: x\r")).toEqual([]);
    // "\r" + "\r" is end of line, then a blank line: the event dispatches.
    expect(p.feed("\rdata: y\n\n")).toEqual([
      { event: "message", data: "x" },
      { event: "message", data: "y" },
    ]);
  });

  it("does not merge a split CRLF into an extra blank line", () => {
    const p = new SseParser();
    expect(p.feed("data: a\r")).toEqual([]);
    expect(p.feed("\ndata: b\r\n\r\n")).toEqual([{ event: "message", data: "a\nb" }]);
  });

  it("dispatches a truncated final event at end of stream", () => {
    const p = new SseParser();
    expect(p.feed('event: agent\ndata: {"text":"Hel')).toEqual([]);
    expect(p.end()).toEqual([{ event: "agent", data: '{"text":"Hel' }]);
    expect(p.end()).toEqual([]);
  });

  it("flushes a final event without a trailing blank line", () => {
    const p = new SseParser();
    expect(p.feed("event: done\ndata: {}")).toEqual([]);
    expect(p.end()).toEqual([{ event: "done", data: "{}" }]);
  });
});

describe("readSse", () => {
  it("reads events from a byte stream", async () => {
    const enc = new TextEncoder();
    const parts = ["event: agent\nda", 'ta: {"text":"A"}\n', "\nevent: done\ndata: {}\n\n"];
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        for (const p of parts) c.enqueue(enc.encode(p));
        c.close();
      },
    });
    const got = [];
    for await (const ev of readSse(body)) got.push(ev.event);
    expect(got).toEqual(["agent", "done"]);
  });
});
