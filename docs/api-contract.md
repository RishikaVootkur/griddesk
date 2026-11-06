# GridDesk HTTP API contract

This is the contract the web app in `web/` is written against. The TypeScript types live in `web/src/api/types.ts` and every call goes through `web/src/api/client.ts`. If the backend changes a path, field or event name, update this file, the types and the client together.

## Conventions

- **Base URL.** `VITE_API_BASE`, default `/api`. In development Vite proxies `/api` to `http://localhost:8000`.
- **Content type.** Request and response bodies are JSON (`Content-Type: application/json`), except for the message stream (see below).
- **Timestamps.** ISO 8601 strings with a timezone, for example `2026-11-04T04:12:43+00:00`.
- **Errors.** Any non-2xx status is an error. The client shows FastAPI's `{"detail": "<message>"}` when `detail` is a string. Otherwise it shows `<status> <statusText>`.
- **Nullable fields.** A field typed `T | null` below is always present and may be `null`. A field marked optional may be missing.
- **Customer access.** `POST /conversations` returns a `token`, a secret for that one conversation. Every other request about the conversation sends it as the `X-Conversation-Token` header. A missing or wrong token is `401`. An unknown `conversation_id` is `404`.
- **Operator access.** Operator endpoints (marked *operator* below) need `Authorization: Bearer <OPERATOR_TOKEN>`. A missing or wrong token is `401`. The console asks the operator for the token and keeps it for the browser session; `VITE_OPERATOR_TOKEN` can supply it in local development only. The API refuses to start without `OPERATOR_TOKEN` unless `GRIDDESK_OPERATOR_AUTH=off` is set for local development.

## Conversations

### `POST /conversations`

Starts a conversation. The client sends the body `{}`.

Response `200`:

```json
{ "conversation_id": "conv_7f3a", "token": "lJ0q7mQ2...base64url" }
```

The client keeps `token` in memory for this conversation only. The server stores only a hash of it.

### `POST /conversations/{conversation_id}/messages`

Sends one customer message and streams the agent's reply. Needs the conversation's `X-Conversation-Token`. An operator token does not work here.

Request body:

```json
{ "text": "I think I was overcharged, can I get a credit?" }
```

The response is `200` with `Content-Type: text/event-stream`. A non-2xx status is reported as an error before any stream is read. Because this is a POST, the client reads the stream with `fetch` and a `ReadableStream`, not with `EventSource`.

**Framing.** This is standard Server-Sent Events. Each event is an `event: <name>` line and one or more `data: <json>` lines, followed by a blank line. Lines may end in LF, CRLF or CR. Comment lines starting with `:` are ignored, so they can be used as keep-alives. Several `data:` lines are joined with `\n` before JSON parsing.

**Events:**

| `event` | `data` | Meaning |
|---|---|---|
| `agent` | `{"text": string, "agent": string \| null}` | One complete agent message. `agent` names the specialist that wrote it: `triage`, `billing`, `outages` or `account`. It is `null` for a runtime message no agent wrote, such as "Your request is still with a supervisor" while the conversation waits for a decision. A reply can contain several `agent` events, for example a triage handoff followed by the specialist's answer. |
| `approval` | `{"approval_request_id": string, "action": string}` | The conversation is now waiting for supervisor approval of `action`. The chat shows a waiting banner. |
| `error` | `{"message": string}` | A problem the customer should see. The stream should still end with `done`. |
| `done` | `{}` | End of the reply. The client stops reading and closes the connection. |

**Rules the backend must follow:**

- Every stream ends with exactly one `done` event, also when the turn fails (then an `error` event comes first). If the stream closes without one, the client reports "The connection closed before the reply finished."
- A client that disconnects does not cancel the turn. The turn still finishes and is saved, and the next transcript read shows it.
- Every `data` payload is valid JSON. If an event's data fails to parse, the client shows "The server sent a malformed event."
- The client ignores unknown event names and anything sent after `done`.

Example stream:

```text
event: agent
data: {"text": "Connecting you with our billing specialist.", "agent": "triage"}

event: agent
data: {"text": "I requested a $42.10 adjustment. A supervisor needs to approve it.", "agent": "billing"}

event: approval
data: {"approval_request_id": "apr_41c2", "action": "apply_bill_credit"}

event: done
data: {}

```

### `GET /conversations` (*operator*)

Lists open conversations for the operator console. The console polls it every 5 seconds, starting each request only after the previous one finishes.

Response `200`: `ConversationSummary[]`

```json
[
  {
    "conversation_id": "conv_7f3a",
    "status": "awaiting_approval",
    "last_message": "Your October bill used an estimated read...",
    "agent": "billing",
    "updated_at": "2026-11-04T04:10:00+00:00",
    "message_count": 5
  }
]
```

| Field | Type | Notes |
|---|---|---|
| `conversation_id` | string | |
| `status` | `"active"` or `"awaiting_approval"` | |
| `last_message` | string | Text of the latest customer or agent message. |
| `agent` | string or null | The specialist that last replied. `null` before any agent reply. |
| `updated_at` | string | ISO 8601 timestamp. |
| `message_count` | number | |

### `GET /conversations/{conversation_id}`

Returns the read-only transcript. The chat also polls this while waiting for an approval decision. Needs either the conversation's `X-Conversation-Token` (the customer) or the operator token (the console).

If the conversation is paused on a request an operator already decided (because resuming failed earlier), reading it retries the resume first.

Response `200`: `ConversationDetail`

```json
{
  "conversation_id": "conv_7f3a",
  "messages": [
    { "role": "customer", "text": "My bill doubled last month.", "agent": null },
    { "role": "agent", "text": "I can help with that.", "agent": "triage" },
    { "role": "system", "text": "Waiting for supervisor approval: apply_bill_credit", "agent": null }
  ],
  "awaiting_approval": { "approval_request_id": "apr_41c2", "action": "apply_bill_credit" }
}
```

- `messages[].role` is `"customer"`, `"agent"` or `"system"`. `messages[].agent` is a string or `null`.
- `awaiting_approval` is `{approval_request_id, action}`, or `null` when nothing is pending.

## Approvals

### `GET /approvals?status=pending` (*operator*)

Response `200`: `ApprovalRequest[]`

```json
[
  {
    "approval_request_id": "apr_41c2",
    "conversation_id": "conv_7f3a",
    "account_id": "102738",
    "action": "apply_bill_credit",
    "arguments": { "account_id": "102738", "amount_usd": 118.4 },
    "rule_id": "BILL-CREDIT-002",
    "reason": "Credits above $100.00 require supervisor approval.",
    "status": "pending",
    "requested_at": "2026-11-04T04:10:00+00:00"
  }
]
```

`arguments` is any JSON object. It is shown exactly as received.

### `POST /approvals/{approval_request_id}/decision` (*operator*)

Request body:

```json
{ "decision": "approve", "operator": "jordan" }
```

- `decision` is `"approve"` or `"deny"`.
- `operator` is a free-text **display name** that the operator types in the console, at most 60 characters. It is not a credential. The backend does not use it for authorization or as the audit actor: the approver's identity comes from authentication (`OPERATOR_ID`, default `console`), and the audit actor is `operator:<identity>`. The display name is stored only in the audit entry's `details.display_name`, cut to 40 characters.

Response `200`:

```json
{ "status": "executed", "reply": "Good news: your credit was approved and applied." }
```

- `status` is the request's stored status after the decision: `executed` (approved and done), `denied`, or `approved`. `approved` means the decision is saved but the conversation could not be resumed yet (for example the model provider was down), so the action has not run and `reply` is `""`. The backend retries the resume on the next transcript read of, or message to, that conversation.
- `reply` is the message sent to the customer.
- If a request was already decided, the response is `409` with a `detail` message. An unknown id is `404`.

## Audit log

### `GET /audit?limit=200[&outcome=<outcome>]` (*operator*)

- `limit` is a number. The console always sends `200`.
- `outcome` is sent only when the operator picks a filter. An absent `outcome` means "all outcomes". The console offers `allowed`, `denied`, `pending_approval`, `approved` and `error`.

Response `200`: `AuditEntry[]`, newest first.

```json
[
  {
    "id": 9000,
    "ts": "2026-11-04T04:10:00+00:00",
    "conversation_id": "conv_7f3a",
    "actor": "agent",
    "action": "apply_bill_credit",
    "account_id": "102738",
    "outcome": "pending_approval",
    "rule_id": "BILL-CREDIT-002",
    "details": { "amount_usd": 118.4 }
  }
]
```

- `actor` is who acted, set by the backend: for example `agent`, `guardrail:action` or `operator:<identity>`.
- `id` is a number or a string.
- `conversation_id`, `account_id` and `rule_id` are a string or `null`.
- `details` is any JSON object.

## Evals

### `GET /eval/runs`

Response `200`: `EvalRunSummary[]`, newest first.

```json
[
  {
    "name": "slice-flashlite-k1-sim-v2",
    "label": "slice-flashlite-k1-sim-v2",
    "model": "gemini:gemini-3.5-flash-lite",
    "design": "single",
    "enforce_policy": true,
    "trials": 1,
    "created_at": "2026-11-04T04:12:43+00:00",
    "summary": { "success_rate": 0.538 }
  }
]
```

`name` is the run's id and the key for the detail endpoint. In practice it is the run file's name without `.json`. `summary` has the same shape as the run detail's `summary` below.

### `GET /eval/runs/{name}`

Response `200`: the full run file (`EvalRunDetail`). The dashboard uses these fields. Extra fields are allowed and ignored.

```text
{
  config: {
    model: string, sim_model: string, design: string,
    enforce_policy: boolean, trials: number, label: string, suite: string
  },
  created_at: string,
  summary: EvalSummary,
  by_difficulty: { [difficulty: string]: number },   // success rate 0..1, keys like easy | medium | hard
  by_domain: { [domain: string]: number },           // success rate 0..1, keys like billing | outages | accounts | mixed
  tasks: { [task_id: string]: { domain: string, difficulty: string, kind: string, title: string } },
  trials: [
    {
      task_id: string,
      trial: number,                                  // 0-based
      success: boolean,
      violation: boolean,
      violations: string[],                           // rule ids violated
      turns: number,
      tool_calls: [ { tool: string, args: object, ok: boolean, rule_id: string | null } ],
      transcript: [ { speaker: "customer" | "agent", text: string } ],
      error: string | null,
      agent_cost_usd: number
    }
  ]
}
```

`EvalSummary`. Every field is optional. A missing or `null` value shows as "n/a".

| Field | Type | Unit |
|---|---|---|
| `success_rate` | number | 0..1 |
| `success_ci95` | `[lo, hi]` | 0..1, 95% confidence interval |
| `violation_rate` | number | 0..1 |
| `violation_ci95` | `[lo, hi]` | 0..1 |
| `pass^1`, `pass^2`, `pass^3`, `pass^4` | number or null | 0..1. `null` when the run has fewer than k trials per task. |
| `tool_call_validity` | number | 0..1 |
| `approval_accuracy` | number | 0..1 |
| `escalation_accuracy` | number | 0..1 |
| `turns_per_resolved` | number | turns |
| `tokens_per_resolved` | number | tokens |
| `latency_p50_ms`, `latency_p95_ms` | number | milliseconds per agent turn |
| `cost_per_resolved_usd`, `total_cost_usd` | number | US dollars |
| `errors`, `tasks`, `trials` | number | counts |

## Static dashboard data (`eval-data.json`)

The GitHub Pages build (`npm run build:static`) makes no API calls. It reads `eval-data.json` from the same folder as `index.html`:

```text
{
  runs: EvalRunSummary[],                     // same as GET /eval/runs
  details: { [name: string]: EvalRunDetail }  // same as GET /eval/runs/{name}, keyed by run name
}
```

If `eval-data.json` is missing or is not valid JSON, the static build falls back to the bundled `eval-data.sample.json` and shows a "Sample data" banner. The sample is built from `evals/runs/slice-flashlite-k1-sim-v1.json` and `-v2.json`.
