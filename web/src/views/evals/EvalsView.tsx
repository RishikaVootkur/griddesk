import { Link, useSearchParams } from "react-router";
import { api } from "../../api/client";
import { Card, Empty, ErrorNote, Pill, Spinner } from "../../components/ui";
import { dateTime, pct } from "../../lib/format";
import { useAsync } from "../../lib/useAsync";
import { RunDetail } from "./RunDetail";
import { SampleDataNote } from "./SampleDataNote";

export function EvalsView() {
  const [params, setParams] = useSearchParams();
  const runs = useAsync(() => api.listRuns(), []);
  const selected = params.get("run") ?? runs.data?.[0]?.name ?? null;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">Eval dashboard</h1>
          <p className="text-sm text-slate-500">Simulated-customer evaluation runs: task success, reliability across trials, policy safety, and cost.</p>
        </div>
        {runs.data && runs.data.length >= 2 && (
          <Link to="/evals/compare" className="rounded-lg bg-brand-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-800">
            Compare runs
          </Link>
        )}
      </div>

      <SampleDataNote />

      <div className="grid gap-6 lg:grid-cols-[18rem_minmax(0,1fr)]">
        <Card title="Runs" className="self-start">
          {runs.error && <ErrorNote message={runs.error} onRetry={runs.reload} />}
          {!runs.data && runs.loading && <Spinner label="Loading runs" />}
          {runs.data && runs.data.length === 0 && <Empty>No eval runs yet. Run the eval harness to create one.</Empty>}
          {runs.data && runs.data.length > 0 && (
            <ul className="max-h-[22rem] divide-y divide-slate-100 overflow-y-auto lg:max-h-[40rem]">
              {runs.data.map((r) => (
                <li key={r.name}>
                  <button
                    type="button"
                    onClick={() => setParams({ run: r.name })}
                    aria-current={selected === r.name}
                    className={`flex w-full flex-col gap-1 px-4 py-3 text-left transition-colors hover:bg-slate-50 ${
                      selected === r.name ? "bg-brand-50/70 shadow-[inset_3px_0_0_var(--color-brand-600)]" : ""
                    }`}
                  >
                    <span className="flex items-center justify-between gap-2">
                      <span className="truncate text-sm font-medium text-slate-900">{r.label}</span>
                      <span className="text-sm font-semibold tabular-nums text-brand-700">{pct(r.summary.success_rate, 0)}</span>
                    </span>
                    <span className="truncate font-mono text-xs text-slate-500">{r.model}</span>
                    <span className="flex flex-wrap items-center gap-1.5">
                      <Pill>{r.design}</Pill>
                      <Pill tone={r.enforce_policy ? "good" : "warn"}>{r.enforce_policy ? "policy on" : "policy off"}</Pill>
                      <span className="text-xs text-slate-400">{dateTime(r.created_at)}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <div className="min-w-0">
          {selected ? <RunDetail name={selected} /> : !runs.loading && !runs.error && <Card><Empty>Select a run.</Empty></Card>}
        </div>
      </div>
    </div>
  );
}
