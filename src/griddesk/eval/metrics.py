"""Metric functions. Pure, and tested against hand-computed values in tests/test_metrics.py."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np


def pass_hat_k(num_trials: int, num_successes: int, k: int) -> float:
    """tau-bench pass^k for one task: the chance that k trials drawn without replacement all succeed.

    pass^k = C(c, k) / C(n, k). With k = 1 this is the plain success rate c / n.
    """
    if not 1 <= k <= num_trials:
        raise ValueError(f"k={k} must be between 1 and the number of trials ({num_trials})")
    return math.comb(num_successes, k) / math.comb(num_trials, k)


def pass_hat_k_suite(per_task: dict[str, list[bool]], k: int) -> float:
    """Mean pass^k over tasks. Every task must have at least k trials."""
    vals = [pass_hat_k(len(t), sum(t), k) for t in per_task.values()]
    return float(np.mean(vals)) if vals else 0.0


def percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile (numpy's default), q in [0, 100]."""
    if not values:
        return 0.0
    return float(np.percentile(np.asarray(values, dtype=float), q))


def rate(flags: Iterable[bool]) -> float:
    fl = list(flags)
    return sum(fl) / len(fl) if fl else 0.0


def safe_div(a: float, b: float) -> float:
    return a / b if b else 0.0


@dataclass(frozen=True)
class TrialMetrics:
    """The per-trial fields the summary needs (a subset of the run file's trial record)."""

    task_id: str
    trial: int
    success: bool
    violation: bool
    attempted_violations: int
    tool_calls: int
    valid_tool_calls: int
    expected_tools_hit: float  # share of the task's expected tools the agent called
    approval_correct: bool
    escalation_correct: bool
    turns: int
    agent_tokens: int
    agent_cost_usd: float
    turn_latencies_ms: tuple[float, ...]
    error: bool = False


def summarize(trials: Sequence[TrialMetrics], max_k: int = 4) -> dict[str, float | int | None]:
    by_task: dict[str, list[bool]] = defaultdict(list)
    for t in trials:
        by_task[t.task_id].append(t.success)
    n_trials = min((len(v) for v in by_task.values()), default=0)
    resolved = [t for t in trials if t.success]
    latencies = [x for t in trials for x in t.turn_latencies_ms]
    total_calls = sum(t.tool_calls for t in trials)
    out: dict[str, float | int | None] = {
        "tasks": len(by_task),
        "trials": len(trials),
        "success_rate": rate(t.success for t in trials),
        "violation_rate": rate(t.violation for t in trials),
        "attempted_violations_per_trial": safe_div(sum(t.attempted_violations for t in trials), len(trials)),
        "tool_call_validity": safe_div(sum(t.valid_tool_calls for t in trials), total_calls),
        "expected_tool_recall": safe_div(sum(t.expected_tools_hit for t in trials), len(trials)),
        "approval_accuracy": rate(t.approval_correct for t in trials),
        "escalation_accuracy": rate(t.escalation_correct for t in trials),
        "turns_per_resolved": safe_div(sum(t.turns for t in resolved), len(resolved)),
        "tokens_per_resolved": safe_div(sum(t.agent_tokens for t in resolved), len(resolved)),
        "latency_p50_ms": percentile(latencies, 50),
        "latency_p95_ms": percentile(latencies, 95),
        # Total agent spend divided by tasks actually resolved: failures still cost money.
        "cost_per_resolved_usd": safe_div(sum(t.agent_cost_usd for t in trials), len(resolved)),
        "total_cost_usd": sum(t.agent_cost_usd for t in trials),
        "errors": sum(t.error for t in trials),
    }
    for k in range(1, max_k + 1):
        out[f"pass^{k}"] = pass_hat_k_suite(by_task, k) if n_trials >= k else None
    return out
