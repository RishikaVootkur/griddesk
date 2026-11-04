import type { ReactNode } from "react";
import type { EvalSummary } from "../../api/types";
import { ci, ms, NA, pct, usd } from "../../lib/format";
import { PASS_KS } from "../../lib/metrics";

function Stat({ label, value, sub, accent = false }: { label: string; value: ReactNode; sub?: ReactNode; accent?: boolean }) {
  return (
    <div className={`min-w-0 rounded-xl border bg-white p-4 shadow-sm ${accent ? "border-brand-200 ring-1 ring-brand-100" : "border-slate-200"}`}>
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums text-slate-900">{value}</div>
      {sub && <div className="mt-1 text-xs text-slate-500">{sub}</div>}
    </div>
  );
}

export function MetricCards({ summary: s }: { summary: EvalSummary }) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5">
      <Stat
        accent
        label="Success rate"
        value={pct(s.success_rate)}
        sub={<>95% CI {ci(s.success_ci95)}</>}
      />
      <Stat
        label="pass^k"
        value={pct(s["pass^1"] ?? null, 0)}
        sub={
          <span className="tabular-nums">
            {PASS_KS.slice(1).map((k) => (
              <span key={k} className="mr-2 inline-block">
                ^{k} {pct(s[`pass^${k}`] ?? null, 0)}
              </span>
            ))}
          </span>
        }
      />
      <Stat
        label="Violation rate"
        value={pct(s.violation_rate)}
        sub={s.violation_ci95 ? <>95% CI {ci(s.violation_ci95)}</> : undefined}
      />
      <Stat label="Cost per resolved" value={usd(s.cost_per_resolved_usd)} sub={<>total {usd(s.total_cost_usd)}</>} />
      <Stat
        label="Latency p50"
        value={ms(s.latency_p50_ms)}
        sub={<>p95 {ms(s.latency_p95_ms)}</>}
      />
      {typeof s.errors === "number" && s.errors > 0 && <Stat label="Errored trials" value={s.errors ?? NA} />}
    </div>
  );
}
