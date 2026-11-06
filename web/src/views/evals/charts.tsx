import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { pct } from "../../lib/format";

// Validated pair (light surface): blue for run A / single series, amber for run B.
export const SERIES_A = "#2f68b0";
export const SERIES_B = "#e0941a";
const GRID = "#e2e8f0";
const AXIS = "#64748b";

const axisProps = { stroke: AXIS, tick: { fill: AXIS, fontSize: 12 }, tickLine: false, axisLine: { stroke: GRID } };
const pctAxis = { ...axisProps, domain: [0, 1] as [number, number], ticks: [0, 0.25, 0.5, 0.75, 1], tickFormatter: (v: number) => pct(v, 0), width: 44 };
const tooltipStyle = { borderRadius: 8, border: "1px solid #e2e8f0", fontSize: 12 };
const fmt = (v: unknown) => (typeof v === "number" ? pct(v) : "n/a");

export function PassKChart({ data, trials }: { data: { k: string; value: number | null }[]; trials: number }) {
  const points = data.filter((d) => d.value !== null).length;
  return (
    <div className="px-2 pt-4 pb-2">
      <div className="h-56">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
            <CartesianGrid stroke={GRID} vertical={false} />
            <XAxis dataKey="k" {...axisProps} padding={{ left: 20, right: 20 }} />
            <YAxis {...pctAxis} />
            <Tooltip formatter={(v) => [fmt(v), "pass^k"]} contentStyle={tooltipStyle} />
            <Line type="monotone" dataKey="value" stroke={SERIES_A} strokeWidth={2} dot={{ r: 4, fill: SERIES_A, stroke: "#fff", strokeWidth: 2 }} activeDot={{ r: 6 }} connectNulls={false} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <p className="px-2 text-xs text-slate-500">
        Share of tasks solved in all of k trials.
        {points <= 1 && ` This run has ${trials} trial${trials === 1 ? "" : "s"} per task, so only pass^${Math.max(1, trials)} is defined.`}
      </p>
    </div>
  );
}

export function BreakdownChart({ data }: { data: { name: string; value: number }[] }) {
  if (data.length === 0) return <p className="px-4 py-8 text-center text-sm text-slate-500">No data.</p>;
  return (
    <div className="h-56 px-2 pt-4 pb-2">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={GRID} vertical={false} />
          <XAxis dataKey="name" {...axisProps} />
          <YAxis {...pctAxis} />
          <Tooltip formatter={(v) => [fmt(v), "success"]} cursor={{ fill: "#f1f5f9" }} contentStyle={tooltipStyle} />
          <Bar dataKey="value" fill={SERIES_A} radius={[4, 4, 0, 0]} maxBarSize={48} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function CompareBars({ data, labelA, labelB }: { data: { name: string; a: number | null; b: number | null }[]; labelA: string; labelB: string }) {
  if (data.length === 0) return <p className="px-4 py-8 text-center text-sm text-slate-500">No data.</p>;
  return (
    <div className="h-60 px-2 pt-4 pb-2">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }} barGap={2}>
          <CartesianGrid stroke={GRID} vertical={false} />
          <XAxis dataKey="name" {...axisProps} />
          <YAxis {...pctAxis} />
          <Tooltip formatter={(v, n) => [fmt(v), n]} cursor={{ fill: "#f1f5f9" }} contentStyle={tooltipStyle} />
          <Legend iconType="circle" wrapperStyle={{ fontSize: 12 }} formatter={(value) => <span style={{ color: AXIS }}>{value}</span>} />
          <Bar dataKey="a" name={`A: ${labelA}`} fill={SERIES_A} radius={[4, 4, 0, 0]} maxBarSize={36} isAnimationActive={false} />
          <Bar dataKey="b" name={`B: ${labelB}`} fill={SERIES_B} radius={[4, 4, 0, 0]} maxBarSize={36} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
