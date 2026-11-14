"""Fill the README's results blocks from committed run and analysis files.

`griddesk eval results` rewrites the text between `<!-- RESULTS:NAME -->` and `<!-- /RESULTS:NAME -->`,
and `--check` fails when the README no longer matches the files, so no number is typed by hand.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from griddesk.eval.report import load_run, per_task
from griddesk.eval.results import (
    attack_counts,
    attack_table,
    attacked,
    by_family,
    link,
    model_table,
    paired_table,
)
from griddesk.eval.stats import bootstrap_ci, paired_test

RUNS = Path("evals/runs")
ANALYSIS = Path("evals/analysis")

GEMINI = RUNS / "core30-gemini-flashlite-multi-k1.json"
GROQ = RUNS / "core30-groq-qwen38-multi-k1.json"
QWEN = RUNS / "core30-qwen3-8b-multi-k4.json"
QWEN_SINGLE = RUNS / "core30-qwen3-8b-single-k1.json"
GEMINI_AFTER = RUNS / "after-core30-gemini-flashlite-multi-k1.json"
QWEN_AFTER = RUNS / "after-core30-qwen3-8b-multi-k4.json"
ATTACKS_ALL = RUNS / "attacks-qwen3-8b-defenses-all.json"
ATTACKS_NONE = RUNS / "attacks-qwen3-8b-defenses-none.json"
SIM_VALIDATION = ANALYSIS / "simulator-validation.json"
# The tasks the two fixes were aimed at (D14 handoff nudge, D15 outage report rule).
TARGETED = ["accounts-paperless-on-01", "mixed-hazard-balance-01", "outages-report-linked-02"]
TAXONOMY_RUNS = [GEMINI, GROQ, QWEN, QWEN_SINGLE, GEMINI_AFTER, QWEN_AFTER]


def _rate(run: dict[str, Any]) -> float:
    return float(bootstrap_ci(per_task(run)).mean)


def _ci(run: dict[str, Any]) -> str:
    ci = bootstrap_ci(per_task(run))
    return f"{ci.mean:.1%} (95% CI {ci.low:.1%} to {ci.high:.1%})"


def _no_handoff(run: dict[str, Any]) -> tuple[int, int]:
    trials = run["trials"]
    return sum(1 for t in trials if not t.get("handoffs")), len(trials)


def headline(root: Path) -> str:
    gem, groq, qwen = (load_run(root / p) for p in (GEMINI, GROQ, QWEN))
    single = load_run(root / QWEN_SINGLE)
    gem_after = load_run(root / GEMINI_AFTER)
    att_all, att_none = load_run(root / ATTACKS_ALL), load_run(root / ATTACKS_NONE)
    n_all, changed_all, said_all, blocked_all = attack_counts(att_all)
    n_none, changed_none, _, _ = attack_counts(att_none)
    fix = paired_test(per_task(gem), per_task(gem_after))
    design = paired_test(per_task(qwen), per_task(single))
    sim = json.loads((root / SIM_VALIDATION).read_text())
    worst_violations = max(load_run(root / p)["summary"]["violation_rate"] for p in TAXONOMY_RUNS)
    return "\n".join(
        [
            "| | Result |",
            "|---|---|",
            f"| Task success, Gemini 3.5 Flash-Lite (30 tasks) | {_ci(gem)}; "
            f"{_pct(_rate(gem_after))} after the two fixes |",
            f"| Task success, Groq qwen3.8-27b | {_ci(groq)} |",
            f"| Task success, local qwen3:8b, multi-agent / single agent | {_pct(_rate(qwen))} / "
            f"{_pct(_rate(single))} (paired p = {design.p_value:.3f}) |",
            f"| Reliability, qwen3:8b pass^1 to pass^4 | {_pct(qwen['summary']['pass^1'])} to "
            f"{_pct(qwen['summary']['pass^4'])} |",
            f"| Policy violations on ordinary tasks, worst run | {_pct(worst_violations)} |",
            f"| Attacks that changed data, defenses on / off | {changed_all} of {n_all} / "
            f"{changed_none} of {n_none} |",
            f"| Other attacks that worked, defenses on | {said_all} of {n_all} got a violating reply "
            f"(a false confirmation or leaked text); {blocked_all} stopped a required action |",
            f"| Fix effect on Gemini (paired, same 30 tasks) | {fix.diff.mean * 100:+.1f} points, "
            f"p = {fix.p_value:.2f} (not significant) |",
            f"| Simulator broke the task (judged, failure-weighted sample of {sim['judged']}) | "
            f"{_pct(sim['broke_task_rate'])} |",
        ]
    )


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x:.1%}"


def passk_rows(paths: list[Path]) -> str:
    out = ["| Run | pass^1 | pass^2 | pass^3 | pass^4 |", "|---|---|---|---|---|"]
    for p in paths:
        s = load_run(p)["summary"]
        out.append(f"| {link(p)} | " + " | ".join(_pct(s.get(f"pass^{k}")) for k in range(1, 5)) + " |")
    return "\n".join(out)


def targeted_table(root: Path) -> str:
    pairs = [("Gemini", root / GEMINI, root / GEMINI_AFTER), ("qwen3:8b", root / QWEN, root / QWEN_AFTER)]
    out = ["| Task | Model | Passed before | Passed after |", "|---|---|---|---|"]
    for task in TARGETED:
        for name, a, b in pairs:
            ra, rb = per_task(load_run(a)).get(task, []), per_task(load_run(b)).get(task, [])
            out.append(f"| `{task}` | {name} | {int(sum(ra))} of {len(ra)} | {int(sum(rb))} of {len(rb)} |")
    return "\n".join(out)


def handoff_table(root: Path) -> str:
    out = ["| Run | Trials where the front desk never handed off |", "|---|---|"]
    for p in (GEMINI, GEMINI_AFTER, GROQ, QWEN, QWEN_AFTER):
        n, total = _no_handoff(load_run(root / p))
        out.append(f"| {link(p)} | {n} of {total} |")
    return "\n".join(out)


def taxonomy_table(root: Path) -> str:
    data = {p: json.loads((root / ANALYSIS / p.name).read_text()) for p in TAXONOMY_RUNS}
    cats = [c for c in next(iter(data.values()))["counts"] if any(d["counts"][c] for d in data.values())]
    head = "| Run | Failed | " + " | ".join(c.replace("_", " ") for c in cats) + " |"
    out = [head, "|---|---|" + "---|" * len(cats)]
    for p, d in data.items():
        out.append(
            f"| {link(p)} | {d['failed']} of {d['trials']} | "
            + " | ".join(str(d["counts"][c]) for c in cats)
            + " |"
        )
    return "\n".join(out)


def family_table(root: Path) -> str:
    on, off = by_family(root / ATTACKS_ALL, root), by_family(root / ATTACKS_NONE, root)
    out = ["| Attack family | Success, defenses on | Success, defenses off |", "|---|---|---|"]
    for fam in sorted(set(on) | set(off)):
        out.append(f"| {fam.replace('_', ' ')} | {_pct(on.get(fam))} | {_pct(off.get(fam))} |")
    return "\n".join(out)


def tables(root: Path) -> str:
    sim = json.loads((root / SIM_VALIDATION).read_text())
    att_all = load_run(root / ATTACKS_ALL)
    leaks = sorted({t["task_id"] for t in att_all["trials"] if attacked(t)})
    return "\n\n".join(
        [
            "Task runs use the same 30-task core subset (`evals/subsets/core-30.txt`) and the same simulated "
            "customer (`gpt-oss-120b`); the attack runs use all 47 attacks and `gpt-oss-20b` (same family, "
            "to split the free-tier token budget). Errored trials were rerun until none remained, so every "
            "number below covers every trial.",
            "### Models (multi-agent design)",
            model_table([root / GEMINI, root / GROQ, root / QWEN]),
            "### Reliability (pass^k, 4 trials per task)",
            passk_rows([root / QWEN, root / QWEN_AFTER]),
            "### Design, fixes and model comparisons (paired over shared tasks)",
            paired_table(
                [
                    ("Multi-agent -> single agent, qwen3:8b", root / QWEN, root / QWEN_SINGLE, "success"),
                    ("Before -> after fixes, Gemini", root / GEMINI, root / GEMINI_AFTER, "success"),
                    ("Before -> after fixes, qwen3:8b", root / QWEN, root / QWEN_AFTER, "success"),
                    ("Gemini -> Groq qwen3.8-27b", root / GEMINI, root / GROQ, "success"),
                ]
            ),
            "### The two targeted fixes, on the tasks they were aimed at",
            targeted_table(root),
            handoff_table(root),
            "### Attacks (47 attacks, qwen3:8b, multi-agent)",
            attack_table([("all on", root / ATTACKS_ALL), ("all off", root / ATTACKS_NONE)]),
            family_table(root),
            "Attacks that still succeed with defenses on: " + ", ".join(f"`{t}`" for t in leaks) + ".",
            "### Error taxonomy (failed trials)",
            taxonomy_table(root),
            f"A judge from another model family (`{sim['judge_model']}`) read {sim['judged']} transcripts, "
            "half of them failed trials, so the sample is weighted toward failures: the simulated customer "
            f"followed its goal in {_pct(sim['followed_goal_rate'])} and broke the task in "
            f"{_pct(sim['broke_task_rate'])} "
            f"({_pct(sim['broke_task_rate_on_failed'])} of the failed trials). "
            "Judged breaks, plus empty or garbled customer messages, are labelled simulator errors above; "
            "the rest are not judged, so simulator-error counts are a lower bound.",
        ]
    )


BLOCKS = {"HEADLINE": headline, "TABLES": tables}


def render(readme: str, root: Path) -> str:
    for name, build in BLOCKS.items():
        block = f"<!-- RESULTS:{name} -->\n{build(root)}\n<!-- /RESULTS:{name} -->"
        pattern = re.compile(rf"<!-- RESULTS:{name} -->(?:.*?<!-- /RESULTS:{name} -->)?", re.DOTALL)
        if not pattern.search(readme):
            raise ValueError(f"README has no RESULTS:{name} marker")
        readme = pattern.sub(lambda _m: block, readme, count=1)  # noqa: B023 (used at once)
    return readme
