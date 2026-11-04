import { useState } from "react";
import type { EvalRunDetail, EvalTrial } from "../../api/types";
import { Card, Empty, Pill } from "../../components/ui";

export function TrialsTable({ run, onOpen }: { run: EvalRunDetail; onOpen: (t: EvalTrial) => void }) {
  const [failedOnly, setFailedOnly] = useState(false);
  const rows = run.trials.filter((t) => !failedOnly || !t.success || t.violation);
  const failed = run.trials.filter((t) => !t.success || t.violation).length;

  return (
    <Card
      title={`Trials (${run.trials.length})`}
      actions={
        <label className="flex items-center gap-2 text-xs text-slate-600">
          <input type="checkbox" checked={failedOnly} onChange={(e) => setFailedOnly(e.target.checked)} className="size-4 accent-brand-700" />
          Failures only ({failed})
        </label>
      }
    >
      {rows.length === 0 ? (
        <Empty>{failedOnly ? "No failed trials in this run." : "This run has no trials."}</Empty>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full sm:min-w-[40rem] text-left text-sm">
            <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th scope="col" className="px-4 py-2 font-medium">Task</th>
                <th scope="col" className="hidden px-3 py-2 font-medium sm:table-cell">Trial</th>
                <th scope="col" className="px-3 py-2 font-medium">Result</th>
                <th scope="col" className="hidden px-3 py-2 text-right font-medium sm:table-cell">Turns</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">
                  <span className="sm:hidden">Tools</span>
                  <span className="hidden sm:inline">Tool calls</span>
                </th>
                <th scope="col" className="px-4 py-2 text-right font-medium">
                  <span className="sm:hidden">Viol.</span>
                  <span className="hidden sm:inline">Violations</span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {rows.map((t) => {
                const task = run.tasks[t.task_id];
                return (
                  // The row is not interactive itself: a real button opens the transcript, and its
                  // stretched ::after overlay lets a click anywhere on the row reach that button.
                  <tr key={`${t.task_id}-${t.trial}`} className="relative hover:bg-brand-50/50 has-[button:focus-visible]:bg-brand-50">
                    <td className="px-4 py-2">
                      <button
                        type="button"
                        onClick={() => onOpen(t)}
                        className="text-left font-mono text-xs break-all text-brand-700 underline decoration-brand-200 underline-offset-2 after:absolute after:inset-0 hover:decoration-brand-500"
                      >
                        {t.task_id}
                        <span className="sr-only">, trial {t.trial}: open transcript</span>
                      </button>
                      {task && (
                        <div className="text-xs text-slate-500">
                          {task.title} · {task.difficulty}
                        </div>
                      )}
                    </td>
                    <td className="hidden px-3 py-2 tabular-nums sm:table-cell">{t.trial}</td>
                    <td className="px-3 py-2">
                      {t.error ? <Pill tone="bad">error</Pill> : t.success ? <Pill tone="good">pass</Pill> : <Pill tone="bad">fail</Pill>}
                    </td>
                    <td className="hidden px-3 py-2 text-right tabular-nums sm:table-cell">{t.turns}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{t.tool_calls.length}</td>
                    <td className="px-4 py-2 text-right tabular-nums">
                      {t.violations.length > 0 ? <Pill tone="bad">{t.violations.length}</Pill> : <span className="text-slate-400">0</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
