import { describe, expect, it } from "vitest";
import { ci, compact, deltaPp, deltaRel, ms, NA, pct, usd } from "./format";

describe("format", () => {
  it("formats percentages and missing values", () => {
    expect(pct(0.7692307692307693)).toBe("76.9%");
    expect(pct(1)).toBe("100.0%");
    expect(pct(0, 0)).toBe("0%");
    expect(pct(null)).toBe(NA);
    expect(pct(undefined)).toBe(NA);
    expect(pct(Number.NaN)).toBe(NA);
  });

  it("formats confidence intervals", () => {
    expect(ci([0.5384615384615384, 1])).toBe("53.8 to 100.0%");
    expect(ci(null)).toBe(NA);
  });

  it("keeps precision on small dollar amounts", () => {
    expect(usd(0.0096248)).toBe("$0.0096");
    expect(usd(0.096248)).toBe("$0.096");
    expect(usd(12.5)).toBe("$12.50");
    expect(usd(0)).toBe("$0");
    expect(usd(null)).toBe(NA);
  });

  it("formats latency and large counts", () => {
    expect(ms(1492.1)).toBe("1.49 s");
    expect(ms(820.4)).toBe("820 ms");
    expect(compact(20676.1)).toBe("20.7k");
    expect(compact(4)).toBe("4");
    expect(compact(2_500_000)).toBe("2.5M");
  });

  it("formats deltas", () => {
    expect(deltaPp(0.7, 0.75)).toBe("+5.0 pp");
    expect(deltaPp(0.8, 0.75)).toBe("-5.0 pp");
    expect(deltaRel(1000, 1200)).toBe("+20%");
    expect(deltaRel(0, 1)).toBe(NA);
    expect(deltaPp(null, 0.5)).toBe(NA);
  });
});
