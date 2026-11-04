import { useState } from "react";
import { api } from "../../api/client";
import { Card, Empty, ErrorNote, Pill, Spinner } from "../../components/ui";
import { dateTime } from "../../lib/format";
import { useAsync } from "../../lib/useAsync";

const OUTCOMES = ["", "allowed", "denied", "pending_approval", "approved", "error"];

function outcomeTone(outcome: string) {
  if (outcome === "allowed" || outcome === "approved") return "good" as const;
  if (outcome === "denied" || outcome === "error") return "bad" as const;
  if (outcome.startsWith("pending")) return "warn" as const;
  return "neutral" as const;
}

export function AuditLog() {
  const [outcome, setOutcome] = useState("");
  const { data, error, loading, reload } = useAsync(() => api.listAudit(outcome, 200), [outcome], 5000);

  return (
    <Card
      title="Audit log"
      actions={
        <label className="flex items-center gap-2 text-xs text-slate-500">
          Outcome
          <select
            value={outcome}
            onChange={(e) => setOutcome(e.target.value)}
            className="rounded-md border border-slate-300 bg-white px-2 py-1 text-sm text-slate-800 focus:border-brand-500 focus:outline-none"
          >
            {OUTCOMES.map((o) => (
              <option key={o} value={o}>
                {o ? o.replaceAll("_", " ") : "All outcomes"}
              </option>
            ))}
          </select>
        </label>
      }
    >
      {error && <ErrorNote message={error} onRetry={reload} />}
      {!data && loading && <Spinner label="Loading audit log" />}
      {data && data.length === 0 && <Empty>No audit entries{outcome && ` with outcome "${outcome.replaceAll("_", " ")}"`}.</Empty>}
      {data && data.length > 0 && (
        <div className="max-h-[28rem] overflow-auto">
          <table className="w-full min-w-[44rem] text-left text-sm">
            <thead className="sticky top-0 bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
              <tr>
                {["Time", "Conversation", "Actor", "Action", "Account", "Outcome", "Rule"].map((h) => (
                  <th key={h} scope="col" className="px-4 py-2 font-medium">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {data.map((e) => (
                <tr key={e.id} className="hover:bg-slate-50">
                  <td className="whitespace-nowrap px-4 py-2 text-slate-600">{dateTime(e.ts)}</td>
                  <td className="px-4 py-2 font-mono text-xs">{e.conversation_id ?? "n/a"}</td>
                  <td className="px-4 py-2">{e.actor}</td>
                  <td className="px-4 py-2 font-mono text-xs">{e.action}</td>
                  <td className="px-4 py-2 font-mono text-xs">{e.account_id ?? "n/a"}</td>
                  <td className="px-4 py-2">
                    <Pill tone={outcomeTone(e.outcome)}>{e.outcome.replaceAll("_", " ")}</Pill>
                  </td>
                  <td className="px-4 py-2 font-mono text-xs text-slate-600">{e.rule_id ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
