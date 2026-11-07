// Typed client for the GridDesk API. Every network call in the app goes through here.
//   normal build: talks to the FastAPI backend at VITE_API_BASE (default /api)
//   mock mode:    `npm run dev:mock`, all data served in the browser (see mock.ts)
//   static build: eval endpoints read ./eval-data.json; other endpoints are unavailable

import { API_BASE, MOCK_MODE, STATIC_MODE } from "../lib/env";
import { operatorToken } from "../lib/operatorToken";
import { readSse } from "../lib/sse";
import { mockApi } from "./mock";
import type {
  ApprovalRequest,
  AuditEntry,
  ConversationDetail,
  ConversationSummary,
  Decision,
  DecisionResult,
  EvalRunDetail,
  EvalRunSummary,
  NewConversation,
  StaticEvalData,
  StreamEvent,
} from "./types";

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export const CONVERSATION_TOKEN_HEADER = "X-Conversation-Token";

export interface Api {
  createConversation(signal?: AbortSignal): Promise<NewConversation>;
  sendMessage(conversation: NewConversation, text: string, signal?: AbortSignal): AsyncGenerator<StreamEvent>;
  listConversations(): Promise<ConversationSummary[]>;
  /** With the conversation's token as its customer; without one as the operator. */
  getConversation(conversationId: string, token?: string): Promise<ConversationDetail>;
  listApprovals(): Promise<ApprovalRequest[]>;
  decide(approvalId: string, decision: Decision, operator: string): Promise<DecisionResult>;
  listAudit(outcome?: string, limit?: number): Promise<AuditEntry[]>;
  listRuns(): Promise<EvalRunSummary[]>;
  getRun(name: string): Promise<EvalRunDetail>;
  /** "sample" when eval data is the bundled sample, not real results. */
  evalDataSource(): Promise<EvalDataSource>;
}

export type EvalDataSource = "live" | "sample";

async function errorMessage(res: Response): Promise<string> {
  try {
    const body: unknown = await res.json();
    if (body && typeof body === "object" && "detail" in body) {
      const detail = (body as { detail: unknown }).detail;
      if (typeof detail === "string") return detail;
    }
  } catch {
    // body was not JSON
  }
  return `${res.status} ${res.statusText || "request failed"}`;
}

/** Headers for operator endpoints: the bearer token entered in the console, if any. */
function operatorHeaders(): Record<string, string> {
  const token = operatorToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

const conversationHeaders = (token: string): Record<string, string> => ({ [CONVERSATION_TOKEN_HEADER]: token });

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { Accept: "application/json", ...(init?.body ? { "Content-Type": "application/json" } : {}), ...init?.headers },
  });
  if (!res.ok) throw new ApiError(res.status, await errorMessage(res));
  return (await res.json()) as T;
}

function parseStreamEvent(event: string, data: string): StreamEvent | null {
  let payload: Record<string, unknown>;
  try {
    payload = data ? (JSON.parse(data) as Record<string, unknown>) : {};
  } catch {
    return { type: "error", message: "The server sent a malformed event." };
  }
  switch (event) {
    case "agent":
      return { type: "agent", text: String(payload.text ?? ""), agent: payload.agent == null ? null : String(payload.agent) };
    case "approval":
      return {
        type: "approval",
        approval_request_id: String(payload.approval_request_id ?? ""),
        action: String(payload.action ?? ""),
      };
    case "error":
      return { type: "error", message: String(payload.message ?? "Unknown error") };
    case "done":
      return { type: "done" };
    default:
      return null;
  }
}

let staticData: Promise<{ data: StaticEvalData; source: EvalDataSource }> | null = null;

/**
 * Static build: ./eval-data.json, falling back to the sample for previews.
 * The fallback is reported through `evalDataSource()` so the UI can label it.
 */
function loadStaticData() {
  staticData ??= (async () => {
    for (const [file, source] of [
      ["eval-data.json", "live"],
      ["eval-data.sample.json", "sample"],
    ] as const) {
      try {
        // BASE_URL is "./" in the static build (hash routing keeps the page at index.html) and "/" otherwise.
        const res = await fetch(`${import.meta.env.BASE_URL}${file}`);
        if (!res.ok) continue;
        const data = (await res.json()) as StaticEvalData;
        if (Array.isArray(data.runs)) return { data, source };
      } catch {
        // missing or not JSON (some hosts answer 404s with HTML): try the next file
      }
    }
    throw new ApiError(404, "No eval data found. Generate eval-data.json next to index.html.");
  })();
  return staticData;
}

const staticUnavailable = (): never => {
  throw new ApiError(501, "This view needs the GridDesk backend. Run the app locally.");
};

export const httpApi: Api = {
  createConversation: (signal) => request<NewConversation>("/conversations", { method: "POST", body: "{}", signal }),

  async *sendMessage(conversation, text, signal) {
    const res = await fetch(`${API_BASE}/conversations/${encodeURIComponent(conversation.conversation_id)}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream", ...conversationHeaders(conversation.token) },
      body: JSON.stringify({ text }),
      signal,
    });
    if (!res.ok) throw new ApiError(res.status, await errorMessage(res));
    if (!res.body) throw new ApiError(0, "The server returned an empty stream.");
    for await (const raw of readSse(res.body)) {
      const ev = parseStreamEvent(raw.event, raw.data);
      if (!ev) continue;
      yield ev;
      if (ev.type === "done") return;
    }
    // A stream that closes without `done` means the backend or a proxy dropped the reply.
    throw new ApiError(0, "The connection closed before the reply finished.");
  },

  listConversations: () => request("/conversations", { headers: operatorHeaders() }),
  getConversation: (id, token) =>
    request(`/conversations/${encodeURIComponent(id)}`, { headers: token ? conversationHeaders(token) : operatorHeaders() }),
  listApprovals: () => request("/approvals?status=pending", { headers: operatorHeaders() }),
  decide: (id, decision, operator) =>
    request(`/approvals/${encodeURIComponent(id)}/decision`, {
      method: "POST",
      body: JSON.stringify({ decision, operator }),
      headers: operatorHeaders(),
    }),
  listAudit(outcome = "", limit = 200) {
    // Only send `outcome` when a filter is chosen: an empty value would match nothing.
    const q = new URLSearchParams({ limit: String(limit) });
    if (outcome) q.set("outcome", outcome);
    return request(`/audit?${q.toString()}`, { headers: operatorHeaders() });
  },
  listRuns: () => request("/eval/runs"),
  getRun: (name) => request(`/eval/runs/${encodeURIComponent(name)}`),
  evalDataSource: async () => "live",
};

const staticApi: Api = {
  createConversation: staticUnavailable,
  // eslint-disable-next-line require-yield
  async *sendMessage() {
    staticUnavailable();
  },
  listConversations: staticUnavailable,
  getConversation: staticUnavailable,
  listApprovals: staticUnavailable,
  decide: staticUnavailable,
  listAudit: staticUnavailable,
  listRuns: async () => (await loadStaticData()).data.runs,
  async getRun(name) {
    const run = (await loadStaticData()).data.details[name];
    if (!run) throw new ApiError(404, `Run "${name}" is not in eval-data.json.`);
    return run;
  },
  evalDataSource: async () => (await loadStaticData()).source,
};

/** Mock mode only: the sample is bundled as its own chunk, never part of the normal build. */
// The MODE check is written inline so the bundler can drop the import in other builds.
const loadMockData = (): Promise<StaticEvalData> =>
  import.meta.env.MODE === "mock"
    ? import("../../sample/eval-data.sample.json").then((m) => m.default as unknown as StaticEvalData)
    : Promise.reject(new ApiError(500, "Mock data is only available in mock mode."));

export const api: Api = STATIC_MODE ? staticApi : MOCK_MODE ? mockApi(loadMockData) : httpApi;

export function describeError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof TypeError) return "Could not reach the GridDesk API. Is the backend running on port 8000?";
  if (err instanceof Error) return err.message;
  return "Something went wrong.";
}
