"""Compare two run files: metric deltas plus a paired significance test on shared tasks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from griddesk.eval.stats import PairedResult, paired_test

HEADLINE = [
    ("success_rate", "pct"),
    ("pass^1", "pct"),
    ("pass^2", "pct"),
    ("pass^3", "pct"),
    ("pass^4", "pct"),
    ("violation_rate", "pct"),
    ("tool_call_validity", "pct"),
    ("approval_accuracy", "pct"),
    ("escalation_accuracy", "pct"),
    ("turns_per_resolved", "num"),
    ("tokens_per_resolved", "int"),
    ("latency_p50_ms", "int"),
    ("latency_p95_ms", "int"),
    ("cost_per_resolved_usd", "usd"),
    ("errors", "int"),
]


def load_run(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text())
    return data


def per_task(run: dict[str, Any], field: str = "success") -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for t in run["trials"]:
        out.setdefault(t["task_id"], []).append(float(t[field]))
    return out


def fmt(v: Any, kind: str) -> str:
    if v is None:
        return "-"
    if kind == "pct":
        return f"{v:.1%}"
    if kind == "usd":
        return f"${v:.4f}"
    if kind == "int":
        return f"{v:,.0f}"
    return f"{v:.2f}"


def compare(a: dict[str, Any], b: dict[str, Any]) -> tuple[str, PairedResult, PairedResult]:
    sa, sb = a["summary"], b["summary"]
    name_a = a["config"].get("label") or a["config"]["model"]
    name_b = b["config"].get("label") or b["config"]["model"]
    lines = [f"{'metric':26s} {name_a[:22]:>22s} {name_b[:22]:>22s} {'delta':>10s}"]
    for key, kind in HEADLINE:
        va, vb = sa.get(key), sb.get(key)
        delta = "-" if va is None or vb is None else fmt(vb - va, kind if kind != "pct" else "pct")
        if kind == "pct" and delta != "-" and not delta.startswith("-"):
            delta = "+" + delta
        lines.append(f"{key:26s} {fmt(va, kind):>22s} {fmt(vb, kind):>22s} {delta:>10s}")
    succ = paired_test(per_task(a), per_task(b))
    viol = paired_test(per_task(a, "violation"), per_task(b, "violation"))
    lines.append("")
    lines.append(
        f"success (paired over {succ.n_tasks} shared tasks): diff {succ.diff.fmt()}  p={succ.p_value:.4f}  "
        f"tasks better in B: {succ.wins_b}, in A: {succ.wins_a}"
    )
    lines.append(f"violations: diff {viol.diff.fmt()}  p={viol.p_value:.4f}")
    return "\n".join(lines), succ, viol
