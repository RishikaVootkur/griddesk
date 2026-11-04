import { useState } from "react";
import { api, describeError } from "../../api/client";
import type { ApprovalRequest, Decision } from "../../api/types";
import { Button, Card, Empty, ErrorNote, Pill, Spinner } from "../../components/ui";
import { timeOnly } from "../../lib/format";
import type { AsyncState } from "../../lib/useAsync";

const OPERATOR_KEY = "griddesk.operator";

function readOperator(): string {
  try {
    return localStorage.getItem(OPERATOR_KEY) ?? "supervisor";
  } catch {
    return "supervisor";
  }
}

function saveOperator(name: string) {
  try {
    localStorage.setItem(OPERATOR_KEY, name);
  } catch {
    // storage unavailable; the name just will not be remembered
  }
}

interface Props {
  state: AsyncState<ApprovalRequest[]>;
  onDecided: () => void;
}

export function ApprovalQueue({ state, onDecided }: Props) {
  const { data, error, loading, reload } = state;
  const [operator, setOperator] = useState(readOperator);
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<{ tone: "good" | "bad"; text: string } | null>(null);

  async function decide(req: ApprovalRequest, decision: Decision) {
    setBusy(req.approval_request_id);
    setResult(null);
    try {
      const r = await api.decide(req.approval_request_id, decision, operator.trim() || "supervisor");
      setResult({ tone: "good", text: `${decision === "approve" ? "Approved" : "Denied"} ${req.action} for account ${req.account_id}. Reply sent: "${r.reply}"` });
      onDecided();
    } catch (e) {
      setResult({ tone: "bad", text: describeError(e) });
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card
      title={
        <span className="flex items-center gap-2">
          Approval queue {data && data.length > 0 && <Pill tone="warn">{data.length} pending</Pill>}
        </span>
      }
      actions={
        // A display name for the reply and audit note only. It is not a login: the backend
        // must take the approver's identity from its own authentication, never from this field.
        <label className="flex items-center gap-2 text-xs text-slate-500" title="Shown in the audit note. Not used for sign-in or authorization.">
          Display name
          <input
            value={operator}
            autoComplete="off"
            onChange={(e) => {
              setOperator(e.target.value);
              saveOperator(e.target.value);
            }}
            className="w-32 rounded-md border border-slate-300 px-2 py-1 text-sm text-slate-800 focus:border-brand-500 focus:outline-none"
          />
        </label>
      }
    >
      {result && (
        <p role="status" className={`mx-4 mt-4 rounded-lg px-3 py-2 text-sm ${result.tone === "good" ? "bg-emerald-50 text-emerald-800" : "bg-red-50 text-red-800"}`}>
          {result.text}
        </p>
      )}
      {error && <ErrorNote message={error} onRetry={reload} />}
      {!data && loading && <Spinner label="Loading approvals" />}
      {data && data.length === 0 && <Empty>No requests waiting for approval.</Empty>}
      {data && data.length > 0 && (
        <ul className="grid gap-4 p-4 md:grid-cols-2">
          {data.map((req) => (
            <li key={req.approval_request_id} className="flex min-w-0 flex-col gap-3 rounded-lg border border-amber-200 bg-amber-soft/40 p-4">
              <div className="flex flex-wrap items-center gap-2">
                <code className="rounded bg-white px-1.5 py-0.5 font-mono text-sm font-semibold text-slate-900 ring-1 ring-slate-200">{req.action}</code>
                <Pill tone="info">Rule {req.rule_id}</Pill>
                <span className="ml-auto text-xs text-slate-500">{timeOnly(req.requested_at)}</span>
              </div>
              <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm">
                <dt className="text-slate-500">Account</dt>
                <dd className="font-mono">{req.account_id}</dd>
                <dt className="text-slate-500">Conversation</dt>
                <dd className="truncate font-mono">{req.conversation_id}</dd>
                <dt className="text-slate-500">Reason</dt>
                <dd>{req.reason}</dd>
              </dl>
              <pre className="overflow-x-auto rounded-md bg-white p-2 font-mono text-xs text-slate-700 ring-1 ring-slate-200">{JSON.stringify(req.arguments, null, 2)}</pre>
              <div className="flex gap-2">
                <Button onClick={() => void decide(req, "approve")} disabled={busy !== null} className="flex-1">
                  {busy === req.approval_request_id ? "Working" : "Approve"}
                </Button>
                <Button variant="danger" onClick={() => void decide(req, "deny")} disabled={busy !== null} className="flex-1">
                  Deny
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
