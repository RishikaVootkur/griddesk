"""CI regression gate: compare a replayed run to the committed baseline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from griddesk.eval.report import HEADLINE, fmt


class Thresholds(BaseModel):
    max_success_drop: float = 0.05
    max_violation_rise: float = 0.0
    max_errors: int = 0


class GateConfig(BaseModel):
    model: str
    suite: str = "tasks"
    trials: int = 1
    baseline: str
    tasks: list[str] = Field(default_factory=list)
    thresholds: Thresholds = Field(default_factory=Thresholds)


def load_gate(path: Path) -> GateConfig:
    return GateConfig.model_validate(yaml.safe_load(path.read_text()))


@dataclass
class GateResult:
    passed: bool
    checks: list[tuple[str, bool, str]]
    markdown: str


def evaluate(run: dict[str, Any], baseline: dict[str, Any], th: Thresholds) -> GateResult:
    s, b = run["summary"], baseline["summary"]
    checks = [
        (
            "success rate",
            s["success_rate"] >= b["success_rate"] - th.max_success_drop - 1e-9,
            f"{s['success_rate']:.1%} vs baseline {b['success_rate']:.1%} "
            f"(max drop {th.max_success_drop:.0%})",
        ),
        (
            "violation rate",
            s["violation_rate"] <= b["violation_rate"] + th.max_violation_rise + 1e-9,
            f"{s['violation_rate']:.1%} vs baseline {b['violation_rate']:.1%}",
        ),
        ("errors and cache misses", s["errors"] <= th.max_errors, f"{s['errors']} (max {th.max_errors})"),
    ]
    passed = all(ok for _, ok, _ in checks)
    lines = [
        "## GridDesk eval regression gate",
        "",
        f"**{'PASSED' if passed else 'FAILED'}**: replayed {s['trials']} trials over {s['tasks']} tasks "
        f"from the committed response cache (`{run['config']['model']}`, no API keys used).",
        "",
        "| check | result | detail |",
        "|---|---|---|",
        *[f"| {name} | {'pass' if ok else '**FAIL**'} | {detail} |" for name, ok, detail in checks],
        "",
        "| metric | baseline | this PR | delta |",
        "|---|---:|---:|---:|",
    ]
    for key, kind in HEADLINE:
        va, vb = b.get(key), s.get(key)
        if va is None and vb is None:
            continue
        delta = "-" if va is None or vb is None else fmt(vb - va, kind)
        lines.append(f"| {key} | {fmt(va, kind)} | {fmt(vb, kind)} | {delta} |")
    changed = _changed_tasks(run, baseline)
    if changed:
        lines += ["", "Tasks whose outcome changed:", ""]
        lines += [f"- `{tid}`: {was} -> {now}" for tid, was, now in changed]
    errors = [t for t in run["trials"] if t.get("error")]
    if errors:
        lines += [
            "",
            "Errors (a cache miss means the slice must be re-recorded with `make eval-record`):",
            "",
        ]
        lines += [f"- `{t['task_id']}`: {t['error'][:160]}" for t in errors[:10]]
    return GateResult(passed, checks, "\n".join(lines) + "\n")


def _changed_tasks(run: dict[str, Any], baseline: dict[str, Any]) -> list[tuple[str, str, str]]:
    def outcome(r: dict[str, Any]) -> dict[str, str]:
        return {t["task_id"]: ("pass" if t["success"] else "fail") for t in r["trials"]}

    a, b = outcome(baseline), outcome(run)
    return [(tid, a[tid], b[tid]) for tid in sorted(set(a) & set(b)) if a[tid] != b[tid]]
