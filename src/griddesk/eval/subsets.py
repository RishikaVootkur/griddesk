"""Deterministic stratified task subsets (domain x difficulty, proportional, largest remainder)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from griddesk.eval.tasks import Task

HEADER = (
    "# A stratified sample of the task suite (domain x difficulty, proportional).\n"
    "# Rebuild with `griddesk eval build-subset`; tests/test_subsets.py checks it is current.\n"
)


def stratified(tasks: Sequence[Task], n: int) -> list[str]:
    groups: dict[tuple[str, str], list[Task]] = defaultdict(list)
    for t in tasks:
        groups[(t.domain, t.difficulty)].append(t)
    total = len(tasks)
    alloc = {k: max(1, round(len(v) * n / total)) for k, v in groups.items()}
    while sum(alloc.values()) > n:
        k = max(alloc, key=lambda k: (alloc[k] - len(groups[k]) * n / total, alloc[k]))
        alloc[k] -= 1
    while sum(alloc.values()) < n:
        k = min(alloc, key=lambda k: alloc[k] - len(groups[k]) * n / total)
        alloc[k] += 1
    picked: list[str] = []
    for k, members in sorted(groups.items()):
        ordered = sorted(members, key=lambda t: t.id)
        step = len(ordered) / alloc[k] if alloc[k] else 0
        picked += [ordered[int(i * step)].id for i in range(alloc[k])]
    return sorted(picked)


def render(ids: Sequence[str]) -> str:
    return HEADER + "\n".join(ids) + "\n"
