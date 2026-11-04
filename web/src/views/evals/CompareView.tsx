import { Link, useSearchParams } from "react-router";
import { api } from "../../api/client";
import type { EvalRunDetail, EvalRunSummary } from "../../api/types";
import { Card, Empty, ErrorNote, Spinner } from "../../components/ui";
import { breakdown, deltaDirection, formatDelta, METRICS } from "../../lib/metrics";
import { useAsync } from "../../lib/useAsync";
import { CompareBars, SERIES_A, SERIES_B } from "./charts";
import { SampleDataNote } from "./SampleDataNote";

function RunPicker({ id, label, color, runs, value, onChange }: { id: string; label: string; color: string; runs: EvalRunSummary[]; value: string; onChange: (v: string) => void }) {
  return (
    <label htmlFor={id} className="flex min-w-0 flex-1 flex-col gap-1 text-xs font-medium text-slate-600">
      <span className="flex items-center gap-1.5">
        <span className="size-2.5 rounded-full" style={{ background: color }} aria-hidden />
        {label}
      </span>
      <select
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full min-w-0 rounded-lg border border-slate-300 bg-white px-2 py-2 text-sm text-slate-800 focus:border-brand-500 focus:outline-none"
      >
        {runs.map((r) => (
          <option key={r.name} value={r.name}>
            {r.label} ({r.model})
          </option>
        ))}
      </select>
    </label>
  );
}

function mergeBreakdown(a: Record<string, number>, b: Record<string, number>) {
  const names = breakdown({ ...a, ...b }).map((d) => d.name);
  return names.map((name) => ({ name, a: a[name] ?? null, b: b[name] ?? null }));
}

function Comparison({ a, b }: { a: EvalRunDetail; b: EvalRunDetail }) {
  return (
    <div className="flex flex-col gap-6">
      <Card title="Headline metrics">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[34rem] text-left text-sm">
            <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th scope="col" className="px-4 py-2 font-medium">Metric</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">A</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">B</th>
                <th scope="col" className="px-4 py-2 text-right font-medium">B vs A</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {METRICS.map((m) => {
                const va = m.value(a.summary);
                const vb = m.value(b.summary);
                if (va == null && vb == null) return null;
                const dir = deltaDirection(m, va, vb);
                return (
                  <tr key={m.key}>
                    <th scope="row" className="px-4 py-2 font-medium text-slate-700">{m.label}</th>
                    <td className="px-3 py-2 text-right tabular-nums">{m.format(va)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{m.format(vb)}</td>
                    <td className={`px-4 py-2 text-right font-medium tabular-nums ${dir > 0 ? "text-emerald-700" : dir < 0 ? "text-red-700" : "text-slate-500"}`}>
                      {dir > 0 ? "▲ " : dir < 0 ? "▼ " : ""}
                      {formatDelta(m, va, vb)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="border-t border-slate-100 px-4 py-2 text-xs text-slate-500">
          Rates differ in percentage points (pp); cost, latency and effort differ in relative terms. Green means B is better.
        </p>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Success by difficulty">
          <CompareBars data={mergeBreakdown(a.by_difficulty, b.by_difficulty)} labelA={a.config.label} labelB={b.config.label} />
        </Card>
        <Card title="Success by domain">
          <CompareBars data={mergeBreakdown(a.by_domain, b.by_domain)} labelA={a.config.label} labelB={b.config.label} />
        </Card>
      </div>
    </div>
  );
}

export function CompareView() {
  const [params, setParams] = useSearchParams();
  const runs = useAsync(() => api.listRuns(), []);
  const list = runs.data ?? [];
  const nameA = params.get("a") ?? list[1]?.name ?? list[0]?.name ?? "";
  const nameB = params.get("b") ?? list[0]?.name ?? "";

  const pair = useAsync(
    () => (nameA && nameB ? Promise.all([api.getRun(nameA), api.getRun(nameB)]) : Promise.resolve(null)),
    [nameA, nameB],
  );

  const set = (key: "a" | "b", v: string) => setParams({ a: nameA, b: nameB, [key]: v });

  return (
    <div className="flex flex-col gap-6">
      <div>
        <Link to="/evals" className="text-sm text-brand-700 hover:underline">
          &larr; All runs
        </Link>
        <h1 className="mt-1 text-xl font-semibold text-slate-900">Compare runs</h1>
        <p className="text-sm text-slate-500">Pick a baseline (A) and a candidate (B).</p>
      </div>

      <SampleDataNote />

      {runs.error && <Card><ErrorNote message={runs.error} onRetry={runs.reload} /></Card>}
      {!runs.data && runs.loading && <Card><Spinner label="Loading runs" /></Card>}
      {runs.data && list.length < 2 && <Card><Empty>At least two eval runs are needed to compare.</Empty></Card>}

      {list.length >= 2 && (
        <>
          <Card>
            <div className="flex flex-col gap-3 p-4 sm:flex-row">
              <RunPicker id="run-a" label="Run A (baseline)" color={SERIES_A} runs={list} value={nameA} onChange={(v) => set("a", v)} />
              <RunPicker id="run-b" label="Run B (candidate)" color={SERIES_B} runs={list} value={nameB} onChange={(v) => set("b", v)} />
            </div>
          </Card>
          {pair.error && <Card><ErrorNote message={pair.error} onRetry={pair.reload} /></Card>}
          {pair.loading && !pair.data && <Card><Spinner label="Loading runs" /></Card>}
          {pair.data && <Comparison a={pair.data[0]} b={pair.data[1]} />}
        </>
      )}
    </div>
  );
}
