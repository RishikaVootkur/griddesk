from collections import Counter

from griddesk.config import REPO_ROOT
from griddesk.eval.subsets import render, stratified
from griddesk.eval.tasks import load_tasks


def test_committed_subsets_are_current():
    tasks = load_tasks(REPO_ROOT / "evals" / "tasks")
    for name, n in (("core-30.txt", 30), ("live-8.txt", 8)):
        assert (REPO_ROOT / "evals" / "subsets" / name).read_text() == render(stratified(tasks, n))


def test_live_subset_covers_every_domain():
    tasks = {t.id: t for t in load_tasks(REPO_ROOT / "evals" / "tasks")}
    ids = stratified(list(tasks.values()), 8)
    assert set(Counter(tasks[i].domain for i in ids)) == {"billing", "outages", "accounts", "mixed"}
