// Number and date formatting shared by the views. All return "n/a" for missing values.

export const NA = "n/a";

function missing(v: number | null | undefined): v is null | undefined {
  return v === null || v === undefined || Number.isNaN(v);
}

/** 0.7692 -> "76.9%". */
export function pct(v: number | null | undefined, digits = 1): string {
  if (missing(v)) return NA;
  return `${(v * 100).toFixed(digits)}%`;
}

/** [0.538, 1] -> "53.8 to 100.0%". */
export function ci(range: readonly [number, number] | null | undefined, digits = 1): string {
  if (!range) return NA;
  const [lo, hi] = range;
  if (missing(lo) || missing(hi)) return NA;
  return `${(lo * 100).toFixed(digits)} to ${(hi * 100).toFixed(digits)}%`;
}

/** Small dollar amounts keep enough precision to be meaningful: 0.0096 -> "$0.0096". */
export function usd(v: number | null | undefined): string {
  if (missing(v)) return NA;
  if (v === 0) return "$0";
  const abs = Math.abs(v);
  const digits = abs >= 1 ? 2 : abs >= 0.01 ? 3 : 4;
  return `${v < 0 ? "-" : ""}$${abs.toFixed(digits)}`;
}

/** 1492.1 -> "1.49 s"; 820 -> "820 ms". */
export function ms(v: number | null | undefined): string {
  if (missing(v)) return NA;
  if (Math.abs(v) >= 1000) return `${(v / 1000).toFixed(2)} s`;
  return `${Math.round(v)} ms`;
}

/** 20676.1 -> "20.7k". */
export function compact(v: number | null | undefined): string {
  if (missing(v)) return NA;
  const abs = Math.abs(v);
  if (abs >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${(v / 1_000).toFixed(1)}k`;
  return Number.isInteger(v) ? String(v) : v.toFixed(1);
}

/** Signed difference in percentage points: 0.05 -> "+5.0 pp". */
export function deltaPp(a: number | null | undefined, b: number | null | undefined): string {
  if (missing(a) || missing(b)) return NA;
  const d = (b - a) * 100;
  return `${d >= 0 ? "+" : ""}${d.toFixed(1)} pp`;
}

/** Signed relative change: (1, 1.2) -> "+20%". */
export function deltaRel(a: number | null | undefined, b: number | null | undefined): string {
  if (missing(a) || missing(b)) return NA;
  if (a === 0) return b === 0 ? "0%" : NA;
  const d = ((b - a) / Math.abs(a)) * 100;
  return `${d >= 0 ? "+" : ""}${d.toFixed(0)}%`;
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return NA;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function timeOnly(iso: string | null | undefined): string {
  if (!iso) return NA;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
