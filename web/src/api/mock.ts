// In-browser mock backend for `npm run dev:mock`: lets the UI be explored and
// screenshotted with no FastAPI server. Eval data comes from eval-data.sample.json,
// which is built from real (synthetic-customer) eval runs.

import type { Api } from "./client";
import type {
  ApprovalRequest,
  AuditEntry,
  ChatMessage,
  ConversationDetail,
  ConversationSummary,
  StaticEvalData,
  StreamEvent,
} from "./types";

interface MockConversation {
  id: string;
  messages: ChatMessage[];
  awaiting: { approval_request_id: string; action: string } | null;
  updatedAt: string;
  accountId: string;
}

const sleep = (ms: number, signal?: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    const t = setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => {
      clearTimeout(t);
      reject(new DOMException("Aborted", "AbortError"));
    });
  });

const minutesAgo = (m: number) => new Date(Date.now() - m * 60_000).toISOString();
let seq = 100;
const nextId = (prefix: string) => `${prefix}_${(seq++).toString(36)}${Math.random().toString(36).slice(2, 6)}`;

function seed() {
  const conversations = new Map<string, MockConversation>();
  const approvals: ApprovalRequest[] = [];
  const audit: AuditEntry[] = [];

  const add = (c: MockConversation) => conversations.set(c.id, c);

  add({
    id: "conv_7f3a",
    accountId: "102738",
    updatedAt: minutesAgo(2),
    awaiting: { approval_request_id: "apr_41c2", action: "apply_bill_credit" },
    messages: [
      { role: "customer", text: "My bill doubled last month and nothing changed at home. Can you take a look?", agent: null },
      { role: "agent", text: "I can help with that. Please confirm your account number and last name.", agent: "triage" },
      { role: "customer", text: "Account 102738, last name Andrews. Date of birth 1947-04-02.", agent: null },
      {
        role: "agent",
        text: "Thank you, you are verified. Your October bill used an estimated read of 1,840 kWh; the actual read was 912 kWh. I have requested a $118.40 correction credit, which needs supervisor approval because it is over $100.",
        agent: "billing",
      },
      { role: "system", text: "Waiting for supervisor approval: apply_bill_credit", agent: null },
    ],
  });
  approvals.push({
    approval_request_id: "apr_41c2",
    conversation_id: "conv_7f3a",
    account_id: "102738",
    action: "apply_bill_credit",
    arguments: { account_id: "102738", amount_usd: 118.4, reason_code: "estimated_read_correction" },
    rule_id: "BILL-CREDIT-002",
    reason: "Credits above $100.00 require supervisor approval.",
    status: "pending",
    requested_at: minutesAgo(2),
  });

  add({
    id: "conv_2b91",
    accountId: "220415",
    updatedAt: minutesAgo(6),
    awaiting: null,
    messages: [
      { role: "customer", text: "Power is out on Larch Street since this morning. Any update?", agent: null },
      {
        role: "agent",
        text: "There is a known outage affecting 214 customers near Larch Street, caused by a damaged transformer. Crews are on site; estimated restoration is 4:30 PM today.",
        agent: "outages",
      },
    ],
  });

  add({
    id: "conv_c044",
    accountId: "305522",
    updatedAt: minutesAgo(11),
    awaiting: { approval_request_id: "apr_77e0", action: "waive_late_fee" },
    messages: [
      { role: "customer", text: "I was in hospital and paid late. Can the late fee be waived?", agent: null },
      { role: "agent", text: "I am sorry to hear that. I have requested a waiver of the $15.00 late fee, pending supervisor review.", agent: "billing" },
      { role: "system", text: "Waiting for supervisor approval: waive_late_fee", agent: null },
    ],
  });
  approvals.push({
    approval_request_id: "apr_77e0",
    conversation_id: "conv_c044",
    account_id: "305522",
    action: "waive_late_fee",
    arguments: { account_id: "305522", fee_id: "fee_2026_10", amount_usd: 15 },
    rule_id: "BILL-WAIVE-001",
    reason: "Second late-fee waiver in 12 months requires approval.",
    status: "pending",
    requested_at: minutesAgo(11),
  });

  const rows: Omit<AuditEntry, "id">[] = [
    { ts: minutesAgo(2), conversation_id: "conv_7f3a", actor: "billing", action: "apply_bill_credit", account_id: "102738", outcome: "pending_approval", rule_id: "BILL-CREDIT-002", details: { amount_usd: 118.4 } },
    { ts: minutesAgo(3), conversation_id: "conv_7f3a", actor: "billing", action: "get_bill_history", account_id: "102738", outcome: "allowed", rule_id: null, details: {} },
    { ts: minutesAgo(3), conversation_id: "conv_7f3a", actor: "triage", action: "verify_identity", account_id: "102738", outcome: "allowed", rule_id: null, details: { method: "dob" } },
    { ts: minutesAgo(6), conversation_id: "conv_2b91", actor: "outages", action: "get_outage_status", account_id: "220415", outcome: "allowed", rule_id: null, details: { outage_id: "out_5531" } },
    { ts: minutesAgo(8), conversation_id: "conv_9d10", actor: "accounts", action: "update_mailing_address", account_id: "118204", outcome: "denied", rule_id: "ACCT-ID-001", details: { reason: "identity not verified" } },
    { ts: minutesAgo(11), conversation_id: "conv_c044", actor: "billing", action: "waive_late_fee", account_id: "305522", outcome: "pending_approval", rule_id: "BILL-WAIVE-001", details: { amount_usd: 15 } },
    { ts: minutesAgo(19), conversation_id: "conv_5e7c", actor: "supervisor:jordan", action: "approve", account_id: "410077", outcome: "approved", rule_id: "BILL-PLAN-003", details: { approval_request_id: "apr_19aa" } },
    { ts: minutesAgo(24), conversation_id: "conv_5e7c", actor: "billing", action: "create_payment_plan", account_id: "410077", outcome: "pending_approval", rule_id: "BILL-PLAN-003", details: { installments: 6 } },
    { ts: minutesAgo(31), conversation_id: "conv_88b2", actor: "billing", action: "apply_bill_credit", account_id: "509911", outcome: "denied", rule_id: "BILL-CREDIT-004", details: { reason: "exceeds credit ceiling" } },
  ];
  rows.forEach((r, i) => audit.push({ id: 9000 - i, ...r }));

  return { conversations, approvals, audit };
}

function route(text: string): { agent: string; reply: string; approval?: string } {
  const t = text.toLowerCase();
  if (/(outage|power is out|no power|blackout|restor)/.test(t))
    return { agent: "outages", reply: "I checked your service address. There is no active outage on record there; I have logged a trouble report (TR-48211) and a crew will be dispatched within 4 hours." };
  if (/(credit|refund|waive|overcharg)/.test(t))
    return { agent: "billing", reply: "I reviewed your recent bills and requested a $42.10 adjustment. Adjustments of this kind need a supervisor to approve them, which usually takes a few minutes.", approval: "apply_bill_credit" };
  if (/(bill|balance|pay|due|late fee|plan)/.test(t))
    return { agent: "billing", reply: "Your current balance is $186.52, due November 18. Your last payment of $140.00 posted on October 17. Would you like to set up a payment plan?" };
  if (/(address|move|name|account|password|email)/.test(t))
    return { agent: "accounts", reply: "I can update that for you once your identity is verified. Please confirm your account number, last name, and date of birth." };
  return { agent: "triage", reply: "Thanks for contacting Northbrook Power & Light. I can help with bills and payments, outages, and account changes. What can I do for you today?" };
}

export function mockApi(loadEvalData: () => Promise<StaticEvalData>): Api {
  const db = seed();

  const toSummary = (c: MockConversation): ConversationSummary => {
    const last = [...c.messages].reverse().find((m) => m.role !== "system");
    const lastAgent = [...c.messages].reverse().find((m) => m.role === "agent");
    return {
      conversation_id: c.id,
      status: c.awaiting ? "awaiting_approval" : "active",
      last_message: last?.text ?? "",
      agent: lastAgent?.agent ?? null,
      updated_at: c.updatedAt,
      message_count: c.messages.length,
    };
  };

  const get = (id: string) => {
    const c = db.conversations.get(id);
    if (!c) throw new Error(`Conversation ${id} not found`);
    return c;
  };

  return {
    async createConversation(signal) {
      await sleep(150, signal);
      const id = nextId("conv");
      db.conversations.set(id, { id, messages: [], awaiting: null, updatedAt: new Date().toISOString(), accountId: "102738" });
      return id;
    },

    async *sendMessage(id, text, signal): AsyncGenerator<StreamEvent> {
      const c = get(id);
      c.messages.push({ role: "customer", text, agent: null });
      c.updatedAt = new Date().toISOString();
      await sleep(700, signal);
      if (c.awaiting) {
        const reply = "Your request is still with a supervisor. I will let you know as soon as it is decided.";
        c.messages.push({ role: "agent", text: reply, agent: "billing" });
        yield { type: "agent", text: reply, agent: "billing" };
        yield { type: "done" };
        return;
      }
      const r = route(text);
      if (r.agent !== "triage") {
        const hand = `Connecting you with our ${r.agent} specialist.`;
        c.messages.push({ role: "agent", text: hand, agent: "triage" });
        yield { type: "agent", text: hand, agent: "triage" };
        await sleep(900, signal);
      }
      c.messages.push({ role: "agent", text: r.reply, agent: r.agent });
      yield { type: "agent", text: r.reply, agent: r.agent };
      if (r.approval) {
        const apr = nextId("apr");
        c.awaiting = { approval_request_id: apr, action: r.approval };
        db.approvals.unshift({
          approval_request_id: apr,
          conversation_id: id,
          account_id: c.accountId,
          action: r.approval,
          arguments: { account_id: c.accountId, amount_usd: 42.1, reason_code: "billing_error" },
          rule_id: "BILL-CREDIT-001",
          reason: "Bill credits always require supervisor approval in this demo.",
          status: "pending",
          requested_at: new Date().toISOString(),
        });
        db.audit.unshift({ id: nextId("aud"), ts: new Date().toISOString(), conversation_id: id, actor: r.agent, action: r.approval, account_id: c.accountId, outcome: "pending_approval", rule_id: "BILL-CREDIT-001", details: { amount_usd: 42.1 } });
        yield { type: "approval", approval_request_id: apr, action: r.approval };
      }
      yield { type: "done" };
    },

    async listConversations() {
      await sleep(120);
      return [...db.conversations.values()]
        .filter((c) => c.messages.length > 0)
        .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
        .map(toSummary);
    },

    async getConversation(id): Promise<ConversationDetail> {
      await sleep(120);
      const c = get(id);
      return { conversation_id: c.id, messages: [...c.messages], awaiting_approval: c.awaiting };
    },

    async listApprovals() {
      await sleep(120);
      return db.approvals.filter((a) => a.status === "pending");
    },

    async decide(approvalId, decision, operator) {
      await sleep(400);
      const a = db.approvals.find((x) => x.approval_request_id === approvalId);
      if (!a || a.status !== "pending") throw new Error("This request was already decided.");
      a.status = decision === "approve" ? "approved" : "denied";
      const c = db.conversations.get(a.conversation_id);
      const reply =
        decision === "approve"
          ? `Good news: your ${a.action.replaceAll("_", " ")} was approved and has been applied to your account.`
          : `A supervisor reviewed your ${a.action.replaceAll("_", " ")} request and could not approve it. I can explain other options.`;
      if (c) {
        c.awaiting = null;
        c.messages = c.messages.filter((m) => m.role !== "system");
        c.messages.push({ role: "agent", text: reply, agent: "billing" });
        c.updatedAt = new Date().toISOString();
      }
      db.audit.unshift({ id: nextId("aud"), ts: new Date().toISOString(), conversation_id: a.conversation_id, actor: `supervisor:${operator}`, action: decision, account_id: a.account_id, outcome: a.status, rule_id: a.rule_id, details: { approval_request_id: approvalId } });
      return { status: a.status, reply };
    },

    async listAudit(outcome = "", limit = 200) {
      await sleep(120);
      return db.audit.filter((e) => !outcome || e.outcome === outcome).slice(0, limit);
    },

    listRuns: async () => (await loadEvalData()).runs,
    async getRun(name) {
      const run = (await loadEvalData()).details[name];
      if (!run) throw new Error(`Run ${name} not found`);
      return run;
    },
  };
}
