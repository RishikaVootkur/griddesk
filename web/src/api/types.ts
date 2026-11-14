// Types for the GridDesk FastAPI backend, as documented in docs/api-contract.md. Change both together.

/** `POST /conversations`. `token` is the conversation's secret: send it as `X-Conversation-Token`. */
export interface NewConversation {
  conversation_id: string;
  token: string;
}

export type ConversationStatus = "active" | "awaiting_approval";

export interface ConversationSummary {
  conversation_id: string;
  status: ConversationStatus;
  last_message: string;
  agent: string | null;
  updated_at: string;
  message_count: number;
}

export interface ChatMessage {
  role: "customer" | "agent" | "system";
  text: string;
  agent: string | null;
}

export interface PendingApproval {
  approval_request_id: string;
  action: string;
}

export interface ConversationDetail {
  conversation_id: string;
  messages: ChatMessage[];
  awaiting_approval: PendingApproval | null;
}

export type StreamEvent =
  // `agent` is null for runtime messages no agent wrote (for example "still with a supervisor").
  | { type: "agent"; text: string; agent: string | null }
  | { type: "approval"; approval_request_id: string; action: string }
  | { type: "error"; message: string }
  | { type: "done" };

export interface ApprovalRequest {
  approval_request_id: string;
  conversation_id: string;
  account_id: string;
  action: string;
  arguments: Record<string, unknown>;
  rule_id: string;
  reason: string;
  status: string;
  requested_at: string;
}

export type Decision = "approve" | "deny";

/**
 * `executed` or `denied` once the conversation resumed. `approved` means the decision is stored but
 * the action has not run yet (the resume failed); the backend retries it, and `reply` is empty.
 */
export interface DecisionResult {
  status: "executed" | "denied" | "approved" | (string & {});
  reply: string;
}

export interface AuditEntry {
  id: number | string;
  ts: string;
  conversation_id: string | null;
  actor: string;
  action: string;
  account_id: string | null;
  outcome: string;
  rule_id: string | null;
  details: Record<string, unknown>;
}

// ---- Evals ----

export interface EvalSummary {
  success_rate?: number;
  success_ci95?: [number, number];
  violation_rate?: number;
  violation_ci95?: [number, number];
  "pass^1"?: number | null;
  "pass^2"?: number | null;
  "pass^3"?: number | null;
  "pass^4"?: number | null;
  tool_call_validity?: number;
  approval_accuracy?: number;
  escalation_accuracy?: number;
  turns_per_resolved?: number;
  tokens_per_resolved?: number;
  latency_p50_ms?: number;
  latency_p95_ms?: number;
  cost_per_resolved_usd?: number;
  total_cost_usd?: number;
  errors?: number;
  tasks?: number;
  trials?: number;
  [key: string]: unknown;
}

export interface EvalRunSummary {
  name: string;
  label: string;
  model: string;
  design: string;
  enforce_policy: boolean;
  trials: number;
  created_at: string;
  summary: EvalSummary;
}

export interface EvalToolCall {
  tool: string;
  args: Record<string, unknown>;
  ok: boolean;
  rule_id: string | null;
}

export interface EvalTrial {
  task_id: string;
  trial: number;
  success: boolean;
  violation: boolean;
  violations: string[];
  turns: number;
  tool_calls: EvalToolCall[];
  transcript: { speaker: "customer" | "agent"; text: string }[];
  error: string | null;
  agent_cost_usd: number;
}

export interface EvalTaskInfo {
  domain: string;
  difficulty: string;
  kind: string;
  title: string;
}

export interface EvalRunDetail {
  config: {
    model: string;
    sim_model: string;
    design: string;
    enforce_policy: boolean;
    defenses?: Record<string, boolean>;
    trials: number;
    label: string;
    suite: string;
  };
  created_at: string;
  summary: EvalSummary;
  by_difficulty: Record<string, number>;
  by_domain: Record<string, number>;
  tasks: Record<string, EvalTaskInfo>;
  trials: EvalTrial[];
}

export interface StaticEvalData {
  runs: EvalRunSummary[];
  details: Record<string, EvalRunDetail>;
}
