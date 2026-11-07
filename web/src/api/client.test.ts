import { afterEach, describe, expect, it, vi } from "vitest";
import { setOperatorToken } from "../lib/operatorToken";
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

const conv = (id: string) => ({ conversation_id: id, token: "tok-1" });

afterEach(() => {
  vi.unstubAllGlobals();
  setOperatorToken("");
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

    const events = await collect(httpApi.sendMessage(conv("conv 1"), "I was overcharged"));

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
    expect((init?.headers as Record<string, string>)["X-Conversation-Token"]).toBe("tok-1");
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

    const events = await collect(httpApi.sendMessage(conv("c"), "hi"));

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
      for await (const ev of httpApi.sendMessage(conv("c"), "hi")) seen.push(ev);
    } catch (e) {
      error = e;
    }

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).message).toBe("The connection closed before the reply finished.");
    expect(seen).toEqual([{ type: "agent", text: "Partial", agent: "billing" }]);
  });

  it("throws an ApiError with the FastAPI detail on a non-OK response", async () => {
    stubFetch(new Response(JSON.stringify({ detail: "Conversation not found" }), { status: 404 }));

    await expect(collect(httpApi.sendMessage(conv("missing"), "hi"))).rejects.toMatchObject({
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
    const fetchMock = stubFetch(new Response(JSON.stringify({ conversation_id: "conv_1", token: "t" })));
    const controller = new AbortController();
    await expect(httpApi.createConversation(controller.signal)).resolves.toEqual({ conversation_id: "conv_1", token: "t" });
    expect(fetchMock.mock.calls[0]![1]?.signal).toBe(controller.signal);
  });

  it("reads a transcript with the conversation token, or as the operator without one", async () => {
    setOperatorToken("op-secret");
    const fetchMock = vi.fn<typeof fetch>(async () => new Response(JSON.stringify({ conversation_id: "c", messages: [], awaiting_approval: null })));
    vi.stubGlobal("fetch", fetchMock);
    await httpApi.getConversation("c", "tok-1");
    await httpApi.getConversation("c");
    const [first, second] = fetchMock.mock.calls.map((c) => c[1]?.headers as Record<string, string>);
    expect(first).toMatchObject({ "X-Conversation-Token": "tok-1" });
    expect(first).not.toHaveProperty("Authorization");
    expect(second).toMatchObject({ Authorization: "Bearer op-secret" });
  });

  it("sends the operator token on operator endpoints", async () => {
    setOperatorToken("op-secret");
    const fetchMock = vi.fn<typeof fetch>(async () => new Response("[]"));
    vi.stubGlobal("fetch", fetchMock);
    await httpApi.listConversations();
    await httpApi.listApprovals();
    await httpApi.listAudit();
    for (const call of fetchMock.mock.calls) {
      expect(call[1]?.headers).toMatchObject({ Authorization: "Bearer op-secret" });
    }
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ status: "executed", reply: "ok" })));
    await httpApi.decide("apr 1", "approve", "Dana");
    const last = fetchMock.mock.calls.at(-1)!;
    expect(last[0]).toBe("/api/approvals/apr%201/decision");
    expect(last[1]?.headers).toMatchObject({ Authorization: "Bearer op-secret", "Content-Type": "application/json" });
  });

  it("sends no Authorization header when no operator token is set", async () => {
    const fetchMock = stubFetch(new Response("[]"));
    await httpApi.listApprovals();
    expect(fetchMock.mock.calls[0]![1]?.headers).not.toHaveProperty("Authorization");
  });
});

describe("agent events", () => {
  it("keeps a null agent as null", async () => {
    stubFetch(sseResponse(['event: agent\ndata: {"text":"Still with a supervisor","agent":null}\n\n', "event: done\ndata: {}\n\n"]).res);
    const events = await collect(httpApi.sendMessage(conv("c"), "news?"));
    expect(events[0]).toEqual({ type: "agent", text: "Still with a supervisor", agent: null });
  });
});
