"""Build the README results tables from run files, so every number in the docs traces to a run."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from griddesk.eval.report import load_run, per_task
from griddesk.eval.stats import bootstrap_ci, paired_test


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x:.1%}"


def _ci(run: dict[str, Any], key: str = "success") -> str:
    ci = bootstrap_ci(per_task(run, key))
    return f"{ci.mean:.1%} [{ci.low:.1%}, {ci.high:.1%}]"


def link(path: Path) -> str:
    return f"[`{path.stem}`](evals/runs/{path.name})"


def model_table(paths: list[Path]) -> str:
    rows = [
        "| Model | Run | Success (95% CI) | Violations | Tool-call validity | Latency p50 / p95 (s) | "
        "Tokens per resolved | List-price cost per resolved |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for p in paths:
        r = load_run(p)
        s = r["summary"]
        rows.append(
            f"| `{r['config']['model']}` | {link(p)} | {_ci(r)} | {_pct(s['violation_rate'])} | "
            f"{_pct(s['tool_call_validity'])} | "
            f"{s['latency_p50_ms'] / 1000:.1f} / {s['latency_p95_ms'] / 1000:.1f} | "
            f"{s['tokens_per_resolved']:,.0f} | ${s['cost_per_resolved_usd']:.4f} |"
        )
    return "\n".join(rows)


def passk_table(path: Path) -> str:
    r = load_run(path)
    s = r["summary"]
    ks = [k for k in range(1, 5) if s.get(f"pass^{k}") is not None]
    head = "| Run | " + " | ".join(f"pass^{k}" for k in ks) + " |"
    sep = "|---|" + "---|" * len(ks)
    row = f"| {link(path)} | " + " | ".join(_pct(s[f"pass^{k}"]) for k in ks) + " |"
    return "\n".join([head, sep, row])


def paired_row(name: str, a_path: Path, b_path: Path, field: str = "success") -> str:
    a, b = load_run(a_path), load_run(b_path)
    res = paired_test(per_task(a, field), per_task(b, field))
    return (
        f"| {name} | {link(a_path)} vs {link(b_path)} | {res.n_tasks} | {_pct(res.mean_a)} -> "
        f"{_pct(res.mean_b)} | {res.diff.mean:+.1%} [{res.diff.low:+.1%}, {res.diff.high:+.1%}] | "
        f"{res.p_value:.3f} |"
    )


def paired_table(rows: list[tuple[str, Path, Path, str]]) -> str:
    out = [
        "| Comparison | Runs | Shared tasks | A -> B | Difference (95% CI) | Paired p |",
        "|---|---|---|---|---|---|",
    ]
    out += [paired_row(n, a, b, f) for n, a, b, f in rows]
    return "\n".join(out)


def attacked(trial: dict[str, Any]) -> bool:
    """Same definition as `TrialRecord.attack_succeeded`: wrong end state or a violation."""
    return (not trial["state_match"]) or bool(trial["violation"])


def attack_per_task(run: dict[str, Any]) -> dict[str, list[float]]:
    """Per-task attack outcomes, leaving errored trials out (a provider error says nothing about defenses)."""
    out: dict[str, list[float]] = {}
    for t in run["trials"]:
        if t.get("error"):
            continue
        out.setdefault(t["task_id"], []).append(float(attacked(t)))
    return out


def attack_table(paths: list[tuple[str, Path]]) -> str:
    rows = [
        "| Defenses on | Run | Attack success rate (95% CI) | Executed violations "
        "| Blocked attempts per trial |",
        "|---|---|---|---|---|",
    ]
    for label, p in paths:
        r = load_run(p)
        s = r["summary"]
        att = bootstrap_ci(attack_per_task(r))
        rows.append(
            f"| {label} | {link(p)} | {att.mean:.1%} [{att.low:.1%}, {att.high:.1%}] | "
            f"{_pct(s['violation_rate'])} | {s['attempted_violations_per_trial']:.2f} |"
        )
    return "\n".join(rows)


def by_family(path: Path) -> dict[str, float]:
    from griddesk.config import REPO_ROOT
    from griddesk.eval.tasks import load_tasks

    fam = {t.id: t.attack or "other" for t in load_tasks(REPO_ROOT / "evals" / "adversarial")}
    r = load_run(path)
    groups: dict[str, list[bool]] = {}
    for t in r["trials"]:
        if t.get("error"):
            continue
        groups.setdefault(fam.get(t["task_id"], "other"), []).append(attacked(t))
    return {k: sum(v) / len(v) for k, v in sorted(groups.items())}
