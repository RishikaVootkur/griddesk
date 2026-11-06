// Minimal incremental parser for `text/event-stream` bodies.
// Used with fetch + ReadableStream because the chat endpoint is a POST.

export interface SseEvent {
  event: string;
  data: string;
}

export class SseParser {
  private buffer = "";
  private eventName = "";
  private dataLines: string[] = [];

  /** Feed a decoded text chunk; returns any events completed by it. */
  feed(chunk: string): SseEvent[] {
    let text = this.buffer + chunk;
    // A trailing "\r" may be the first half of a "\r\n" split across chunks: hold it back.
    const heldCr = text.endsWith("\r");
    if (heldCr) text = text.slice(0, -1);
    const out: SseEvent[] = [];
    // Split on any line ending, keeping the trailing partial line in the buffer.
    const lines = text.split(/\r\n|\r|\n/);
    this.buffer = (lines.pop() ?? "") + (heldCr ? "\r" : "");
    for (const line of lines) {
      const ev = this.processLine(line);
      if (ev) out.push(ev);
    }
    return out;
  }

  /** Flush at end of stream; dispatches a final event with no blank line after it. */
  end(): SseEvent[] {
    const out: SseEvent[] = [];
    const rest = this.buffer.replace(/\r$/, "");
    this.buffer = "";
    if (rest) {
      const ev = this.processLine(rest);
      if (ev) out.push(ev);
    }
    const ev = this.dispatch();
    if (ev) out.push(ev);
    return out;
  }

  private processLine(line: string): SseEvent | null {
    if (line === "") return this.dispatch();
    if (line.startsWith(":")) return null; // comment / keep-alive
    const idx = line.indexOf(":");
    const field = idx === -1 ? line : line.slice(0, idx);
    let value = idx === -1 ? "" : line.slice(idx + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") this.eventName = value;
    else if (field === "data") this.dataLines.push(value);
    return null;
  }

  private dispatch(): SseEvent | null {
    if (this.dataLines.length === 0 && !this.eventName) return null;
    const ev = { event: this.eventName || "message", data: this.dataLines.join("\n") };
    this.eventName = "";
    this.dataLines = [];
    return ev;
  }
}

/** Read a fetch Response body as SSE, yielding events as they arrive. */
export async function* readSse(body: ReadableStream<Uint8Array>): AsyncGenerator<SseEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  const parser = new SseParser();
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      yield* parser.feed(decoder.decode(value, { stream: true }));
    }
    yield* parser.feed(decoder.decode());
    yield* parser.end();
  } finally {
    // Close the connection if the consumer stopped early (for example after `done`).
    void reader.cancel().catch(() => undefined);
  }
}
