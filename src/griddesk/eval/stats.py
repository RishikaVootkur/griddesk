"""Uncertainty and significance for eval results.

Trials of the same task are correlated, so both the bootstrap and the paired test resample whole
tasks (a cluster bootstrap), not individual trials.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

DEFAULT_ITERS = 10_000


@dataclass(frozen=True)
class Interval:
    mean: float
    low: float
    high: float

    def fmt(self, pct: bool = True) -> str:
        if pct:
            return f"{self.mean:.1%} [{self.low:.1%}, {self.high:.1%}]"
        return f"{self.mean:.3f} [{self.low:.3f}, {self.high:.3f}]"


def task_means(per_task: Mapping[str, Sequence[float]]) -> np.ndarray:
    return np.array([float(np.mean(v)) for _, v in sorted(per_task.items())])


def bootstrap_ci(
    per_task: Mapping[str, Sequence[float]], iters: int = DEFAULT_ITERS, alpha: float = 0.05, seed: int = 0
) -> Interval:
    """Percentile bootstrap CI of the mean over tasks, resampling tasks with replacement."""
    x = task_means(per_task)
    if len(x) == 0:
        return Interval(0.0, 0.0, 0.0)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(x), size=(iters, len(x)))
    means = x[idx].mean(axis=1)
    return Interval(
        float(x.mean()), float(np.quantile(means, alpha / 2)), float(np.quantile(means, 1 - alpha / 2))
    )


@dataclass(frozen=True)
class PairedResult:
    n_tasks: int
    mean_a: float
    mean_b: float
    diff: Interval  # b - a, with a bootstrap CI
    p_value: float  # two-sided paired sign-flip permutation test
    wins_b: int  # tasks where b did better
    wins_a: int


def paired_test(
    a: Mapping[str, Sequence[float]],
    b: Mapping[str, Sequence[float]],
    iters: int = DEFAULT_ITERS,
    seed: int = 0,
    alpha: float = 0.05,
) -> PairedResult:
    """Compare two runs on the tasks they share.

    The test statistic is the mean per-task difference (b - a). Under the null hypothesis that the two
    systems are exchangeable, each task's difference is equally likely to have either sign, so the
    p-value is the share of random sign flips whose |mean| is at least the observed |mean|.
    """
    shared = sorted(set(a) & set(b))
    if not shared:
        raise ValueError("the two runs share no tasks")
    xa = np.array([float(np.mean(a[t])) for t in shared])
    xb = np.array([float(np.mean(b[t])) for t in shared])
    d = xb - xa
    rng = np.random.default_rng(seed)
    observed = abs(d.mean())
    if np.allclose(d, 0):
        p = 1.0
    else:
        signs = rng.choice([-1.0, 1.0], size=(iters, len(d)))
        flipped = np.abs((signs * d).mean(axis=1))
        p = float((np.sum(flipped >= observed - 1e-12) + 1) / (iters + 1))
    idx = rng.integers(0, len(d), size=(iters, len(d)))
    boot = d[idx].mean(axis=1)
    ci = Interval(
        float(d.mean()), float(np.quantile(boot, alpha / 2)), float(np.quantile(boot, 1 - alpha / 2))
    )
    return PairedResult(
        len(shared), float(xa.mean()), float(xb.mean()), ci, p, int(np.sum(d > 0)), int(np.sum(d < 0))
    )
