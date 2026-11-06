import type { EvalSummary } from "../api/types";
import { compact, deltaPp, deltaRel, ms, pct, usd } from "./format";

export interface MetricDef {
  key: string;
  label: string;
  value: (s: EvalSummary) => number | null | undefined;
  format: (v: number | null | undefined) => string;
  /** "pp" deltas are percentage points (rates); "rel" deltas are relative change. */
  delta: "pp" | "rel";
  higherIsBetter: boolean;
}

const num = (key: keyof EvalSummary) => (s: EvalSummary) => {
  const v = s[key];
  return typeof v === "number" ? v : null;
};

export const PASS_KS = [1, 2, 3, 4] as const;

export const METRICS: MetricDef[] = [
  { key: "success_rate", label: "Success rate", value: num("success_rate"), format: pct, delta: "pp", higherIsBetter: true },
  ...PASS_KS.map<MetricDef>((k) => ({
    key: `pass^${k}`,
    label: `pass^${k}`,
    value: num(`pass^${k}`),
    format: pct,
    delta: "pp",
    higherIsBetter: true,
  })),
  { key: "violation_rate", label: "Policy violation rate", value: num("violation_rate"), format: pct, delta: "pp", higherIsBetter: false },
  { key: "tool_call_validity", label: "Tool call validity", value: num("tool_call_validity"), format: pct, delta: "pp", higherIsBetter: true },
  { key: "approval_accuracy", label: "Approval accuracy", value: num("approval_accuracy"), format: pct, delta: "pp", higherIsBetter: true },
  { key: "escalation_accuracy", label: "Escalation accuracy", value: num("escalation_accuracy"), format: pct, delta: "pp", higherIsBetter: true },
  { key: "cost_per_resolved_usd", label: "Cost per resolved task", value: num("cost_per_resolved_usd"), format: usd, delta: "rel", higherIsBetter: false },
  { key: "latency_p50_ms", label: "Latency p50", value: num("latency_p50_ms"), format: ms, delta: "rel", higherIsBetter: false },
  { key: "latency_p95_ms", label: "Latency p95", value: num("latency_p95_ms"), format: ms, delta: "rel", higherIsBetter: false },
  { key: "turns_per_resolved", label: "Turns per resolved task", value: num("turns_per_resolved"), format: compact, delta: "rel", higherIsBetter: false },
  { key: "tokens_per_resolved", label: "Tokens per resolved task", value: num("tokens_per_resolved"), format: compact, delta: "rel", higherIsBetter: false },
  { key: "total_cost_usd", label: "Total cost", value: num("total_cost_usd"), format: usd, delta: "rel", higherIsBetter: false },
];

export function formatDelta(m: MetricDef, a: number | null | undefined, b: number | null | undefined): string {
  return m.delta === "pp" ? deltaPp(a, b) : deltaRel(a, b);
}

/** +1 when B is better than A, -1 when worse, 0 when equal or unknown. */
export function deltaDirection(m: MetricDef, a: number | null | undefined, b: number | null | undefined): -1 | 0 | 1 {
  if (a == null || b == null || Number.isNaN(a) || Number.isNaN(b) || a === b) return 0;
  const better = m.higherIsBetter ? b > a : b < a;
  return better ? 1 : -1;
}

export function passKSeries(s: EvalSummary): { k: string; value: number | null }[] {
  return PASS_KS.map((k) => {
    const v = s[`pass^${k}`];
    return { k: `k=${k}`, value: typeof v === "number" ? v : null };
  });
}

const ORDER: Record<string, number> = { easy: 0, medium: 1, hard: 2, billing: 0, outages: 1, accounts: 2, mixed: 3 };

/** {hard: 1, easy: 0.6} -> [{name: "easy", value: 0.6}, {name: "hard", value: 1}] in a stable order. */
export function breakdown(rec: Record<string, number> | undefined): { name: string; value: number }[] {
  return Object.entries(rec ?? {})
    .map(([name, value]) => ({ name, value }))
    .sort((a, b) => (ORDER[a.name] ?? 99) - (ORDER[b.name] ?? 99) || a.name.localeCompare(b.name));
}
