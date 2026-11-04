import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, httpApi } from "./client";
import type { StreamEvent } from "./types";

const enc = new TextEncoder();

/** A Response whose body streams `chunks` one at a time and records whether it was cancelled. */
function sseResponse(chunks: string[], init: ResponseInit = {}) {
  const state = { cancelled: false };
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      for (const ch of chunks) c.enqueue(enc.encode(ch));
      c.close();
    },
    cancel() {
      state.cancelled = true;
    },
  });
  return { res: new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" }, ...init }), state };
}

function stubFetch(res: Response) {
  const fn = vi.fn<typeof fetch>(async () => res);
  vi.stubGlobal("fetch", fn);
  return fn;
}

async function collect(gen: AsyncGenerator<StreamEvent>): Promise<StreamEvent[]> {
  const out: StreamEvent[] = [];
  for await (const ev of gen) out.push(ev);
  return out;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("httpApi.sendMessage", () => {
  it("posts the text and yields agent and approval events until done", async () => {
    const { res, state } = sseResponse([
      'event: agent\ndata: {"text":"Connecting you","agent":"triage"}\n\n',
      'event: agent\ndata: {"text":"Credit requested","agent":"billing"}\n\nevent: approval\ndata: {"approval_request_id":"apr_1","action":"apply_bill_credit"}\n\n',
      "event: done\ndata: {}\n\n",
      'event: agent\ndata: {"text":"after done","agent":"x"}\n\n',
    ]);
    const fetchMock = stubFetch(res);

    const events = await collect(httpApi.sendMessage("conv 1", "I was overcharged"));

    expect(events).toEqual([
      { type: "agent", text: "Connecting you", agent: "triage" },
      { type: "agent", text: "Credit requested", agent: "billing" },
      { type: "approval", approval_request_id: "apr_1", action: "apply_bill_credit" },
      { type: "done" },
    ]);
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("/api/conversations/conv%201/messages");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ text: "I was overcharged" });
    // Stopping at `done` releases the connection.
    expect(state.cancelled).toBe(true);
  });

  it("passes error events through and reports malformed JSON as an error", async () => {
    stubFetch(
      sseResponse([
        'event: error\ndata: {"message":"Model unavailable"}\n\n',
        "event: agent\ndata: {not json\n\n",
        "event: ping\ndata: {}\n\n",
        "event: done\ndata: {}\n\n",
      ]).res,
    );

    const events = await collect(httpApi.sendMessage("c", "hi"));

    expect(events).toEqual([
      { type: "error", message: "Model unavailable" },
      { type: "error", message: "The server sent a malformed event." },
      { type: "done" },
    ]);
  });

  it("throws when the stream closes before done", async () => {
    stubFetch(sseResponse(['event: agent\ndata: {"text":"Partial","agent":"billing"}\n\n']).res);

    const seen: StreamEvent[] = [];
    let error: unknown;
    try {
      for await (const ev of httpApi.sendMessage("c", "hi")) seen.push(ev);
    } catch (e) {
      error = e;
    }

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).message).toBe("The connection closed before the reply finished.");
    expect(seen).toEqual([{ type: "agent", text: "Partial", agent: "billing" }]);
  });

  it("throws an ApiError with the FastAPI detail on a non-OK response", async () => {
    stubFetch(new Response(JSON.stringify({ detail: "Conversation not found" }), { status: 404 }));

    await expect(collect(httpApi.sendMessage("missing", "hi"))).rejects.toMatchObject({
      name: "ApiError",
      status: 404,
      message: "Conversation not found",
    });
  });
});

describe("httpApi requests", () => {
  it("omits the outcome filter when none is chosen", async () => {
    const fetchMock = stubFetch(new Response("[]"));
    await httpApi.listAudit("", 200);
    expect(fetchMock.mock.calls[0]![0]).toBe("/api/audit?limit=200");
  });

  it("sends the outcome filter when one is chosen", async () => {
    const fetchMock = stubFetch(new Response("[]"));
    await httpApi.listAudit("pending_approval", 50);
    expect(fetchMock.mock.calls[0]![0]).toBe("/api/audit?limit=50&outcome=pending_approval");
  });

  it("passes the abort signal to conversation creation", async () => {
    const fetchMock = stubFetch(new Response(JSON.stringify({ conversation_id: "conv_1" })));
    const controller = new AbortController();
    await expect(httpApi.createConversation(controller.signal)).resolves.toBe("conv_1");
    expect(fetchMock.mock.calls[0]![1]?.signal).toBe(controller.signal);
  });
});
