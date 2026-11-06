import { useCallback, useState } from "react";
import { api } from "../../api/client";
import type { EvalTrial } from "../../api/types";
import { Card, ErrorNote, Pill, Spinner } from "../../components/ui";
import { dateTime } from "../../lib/format";
import { breakdown, passKSeries } from "../../lib/metrics";
import { useAsync } from "../../lib/useAsync";
import { BreakdownChart, PassKChart } from "./charts";
import { MetricCards } from "./MetricCards";
import { TrialDialog } from "./TrialDialog";
import { TrialsTable } from "./TrialsTable";

export function RunDetail({ name }: { name: string }) {
  const { data: run, error, loading, reload } = useAsync(() => api.getRun(name), [name]);
  const [openTrial, setOpenTrial] = useState<EvalTrial | null>(null);
  const closeTrial = useCallback(() => setOpenTrial(null), []);

  if (error) return <Card><ErrorNote message={error} onRetry={reload} /></Card>;
  if (!run || loading) return <Card><Spinner label="Loading run" /></Card>;

  const c = run.config;
  return (
    <div className="flex flex-col gap-6">
      <Card>
        <div className="flex flex-col gap-2 px-4 py-4">
          <h2 className="text-lg font-semibold break-words text-slate-900">{c.label}</h2>
          <div className="flex flex-wrap items-center gap-2 text-xs text-slate-600">
            <Pill tone="info">agent: {c.model}</Pill>
            <Pill>simulator: {c.sim_model}</Pill>
            <Pill>design: {c.design}</Pill>
            <Pill tone={c.enforce_policy ? "good" : "warn"}>{c.enforce_policy ? "in-tool policy on" : "in-tool policy off"}</Pill>
            <Pill>
              {run.summary.tasks ?? Object.keys(run.tasks).length} tasks x {c.trials} trial{c.trials === 1 ? "" : "s"}
            </Pill>
            <span className="text-slate-400">{dateTime(run.created_at)}</span>
          </div>
        </div>
      </Card>

      <MetricCards summary={run.summary} />

      <div className="grid gap-6 xl:grid-cols-3">
        <Card title="Reliability: pass^k">
          <PassKChart data={passKSeries(run.summary)} trials={c.trials} />
        </Card>
        <Card title="Success by difficulty">
          <BreakdownChart data={breakdown(run.by_difficulty)} />
        </Card>
        <Card title="Success by domain">
          <BreakdownChart data={breakdown(run.by_domain)} />
        </Card>
      </div>

      <TrialsTable run={run} onOpen={setOpenTrial} />
      {openTrial && <TrialDialog trial={openTrial} task={run.tasks[openTrial.task_id]} onClose={closeTrial} />}
    </div>
  );
}
