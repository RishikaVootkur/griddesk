import { useEffect, useRef } from "react";
import type { EvalTaskInfo, EvalTrial } from "../../api/types";
import { Transcript } from "../../components/Transcript";
import { Button, Pill } from "../../components/ui";
import { usd } from "../../lib/format";

export function TrialDialog({ trial, task, onClose }: { trial: EvalTrial; task: EvalTaskInfo | undefined; onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-slate-900/40 sm:items-center sm:p-6" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="trial-title"
        onClick={(e) => e.stopPropagation()}
        className="flex max-h-[92dvh] w-full max-w-3xl flex-col overflow-hidden rounded-t-2xl bg-white shadow-xl sm:rounded-2xl"
      >
        <header className="flex items-start justify-between gap-3 border-b border-slate-100 px-4 py-3">
          <div className="min-w-0">
            <h2 id="trial-title" className="truncate font-mono text-sm font-semibold text-slate-900">
              {trial.task_id} · trial {trial.trial}
            </h2>
            {task && <p className="text-xs text-slate-500">{task.title} · {task.domain} · {task.difficulty} · {task.kind}</p>}
            <div className="mt-2 flex flex-wrap gap-1.5">
              {trial.success ? <Pill tone="good">pass</Pill> : <Pill tone="bad">fail</Pill>}
              <Pill>{trial.turns} turns</Pill>
              <Pill>{trial.tool_calls.length} tool calls</Pill>
              <Pill>{usd(trial.agent_cost_usd)}</Pill>
              {trial.violations.map((v) => (
                <Pill key={v} tone="bad">violation: {v}</Pill>
              ))}
            </div>
          </div>
          <Button ref={closeRef} variant="ghost" onClick={onClose} aria-label="Close transcript">
            Close
          </Button>
        </header>

        <div className="flex-1 overflow-y-auto">
          {trial.error && <p className="mx-4 mt-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-800">Error: {trial.error}</p>}
          <div className="bg-slate-50/60 px-3 py-4 sm:px-4">
            {trial.transcript.length === 0 ? (
              <p className="text-center text-sm text-slate-500">No transcript recorded.</p>
            ) : (
              <Transcript lines={trial.transcript.map((l) => ({ role: l.speaker, text: l.text }))} customerLabel="Simulated customer" />
            )}
          </div>
          {trial.tool_calls.length > 0 && (
            <section className="border-t border-slate-100 px-4 py-3">
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Tool calls</h3>
              <ol className="flex flex-col gap-2">
                {trial.tool_calls.map((c, i) => (
                  <li key={i} className="rounded-md border border-slate-200 p-2 text-xs">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono font-semibold text-slate-800">{c.tool}</span>
                      {c.ok ? <Pill tone="good">ok</Pill> : <Pill tone="bad">blocked</Pill>}
                      {c.rule_id && <Pill tone="info">{c.rule_id}</Pill>}
                    </div>
                    <pre className="mt-1 overflow-x-auto font-mono text-slate-600">{JSON.stringify(c.args)}</pre>
                  </li>
                ))}
              </ol>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
