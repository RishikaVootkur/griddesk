from collections import Counter

import pytest

from griddesk.eval.runner import outputs_present
from griddesk.eval.state import COMPARE, diff, diffs_equal, mismatch
from griddesk.eval.tasks import Task, load_tasks


def snap(**tables):
    base = {t: Counter() for t in COMPARE}
    for t, rows in tables.items():
        base[t] = Counter(rows)
    return base


def test_diff_detects_added_and_removed_rows():
    before = snap(accounts=[("1", "C", "S", "active", "100.00", "False", "False", "")])
    after = snap(
        accounts=[("1", "C", "S", "active", "85.00", "False", "False", "")],
        credits=[("1", "15.00", "late_fee_waiver", "B-1", "")],
    )
    d = diff(before, after)
    assert d["credits"].added == (("1", "15.00", "late_fee_waiver", "B-1", ""),)
    assert len(d["accounts"].added) == 1 and len(d["accounts"].removed) == 1
    assert d["bills"].empty


def test_equal_diffs_and_mismatch_report():
    before = snap()
    good = diff(before, snap(credits=[("1", "15.00", "late_fee_waiver", "B-1", "")]))
    twice = diff(before, snap(credits=[("1", "15.00", "late_fee_waiver", "B-1", "")] * 2))
    assert diffs_equal(good, good)
    assert not diffs_equal(good, twice)  # a duplicate credit is a different end state
    report = mismatch(twice, good)
    assert report["credits"]["unexpected"] == [
        {
            "account_id": "1",
            "amount": "15.00",
            "reason_code": "late_fee_waiver",
            "bill_id": "B-1",
            "outage_id": "",
        }
    ]


def test_outputs_present_ignores_formatting():
    assert outputs_present(["Your balance is $1,486.32 today."], ["1486.32"])
    assert not outputs_present(["Your balance is $486.31."], ["486.32"])


def test_committed_tasks_load_and_are_unique():
    from griddesk.config import REPO_ROOT

    tasks = load_tasks(REPO_ROOT / "evals" / "tasks")
    assert len(tasks) >= 10
    assert all(isinstance(t, Task) for t in tasks)


def test_duplicate_task_ids_are_rejected(tmp_path):
    (tmp_path / "a.yaml").write_text(
        "- {id: x, domain: billing, difficulty: easy, title: t, persona: p, goal: g}\n"
        "- {id: x, domain: billing, difficulty: easy, title: t, persona: p, goal: g}\n"
    )
    with pytest.raises(ValueError):
        load_tasks(tmp_path)


@pytest.mark.db
def test_every_task_ground_truth_passes_policy(engine):
    from griddesk.config import REPO_ROOT
    from griddesk.eval.expected import expected_diff

    for task in load_tasks(REPO_ROOT / "evals" / "tasks"):
        expected_diff(engine, task)  # raises ExpectedActionError if the ground truth is refused
