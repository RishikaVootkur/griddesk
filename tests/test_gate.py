from griddesk.eval.gate import Thresholds, evaluate


def run(success, violation, errors=0, outcomes=None):
    outcomes = outcomes or {"a": True, "b": success >= 0.5}
    return {
        "config": {"model": "gemini:x", "trials": 1},
        "summary": {
            "success_rate": success,
            "violation_rate": violation,
            "errors": errors,
            "trials": 2,
            "tasks": 2,
            "pass^1": success,
        },
        "trials": [
            {"task_id": k, "trial": 0, "success": v, "error": "CacheMiss: x" if errors else None}
            for k, v in outcomes.items()
        ],
    }


def test_gate_passes_on_equal_metrics():
    r = evaluate(run(0.5, 0.0), run(0.5, 0.0), Thresholds())
    assert r.passed and "PASSED" in r.markdown


def test_gate_fails_on_success_drop_beyond_threshold():
    r = evaluate(
        run(0.4, 0.0, outcomes={"a": True, "b": False}), run(0.5, 0.0), Thresholds(max_success_drop=0.05)
    )
    assert not r.passed and "**FAIL**" in r.markdown


def test_gate_tolerates_drop_within_threshold():
    assert evaluate(run(0.47, 0.0), run(0.5, 0.0), Thresholds(max_success_drop=0.05)).passed


def test_gate_fails_on_any_violation_rise_and_on_errors():
    assert not evaluate(run(0.5, 0.1), run(0.5, 0.0), Thresholds()).passed
    failed = evaluate(run(0.5, 0.0, errors=1), run(0.5, 0.0), Thresholds())
    assert not failed.passed and "make eval-record" in failed.markdown


def test_gate_lists_tasks_whose_outcome_changed():
    r = evaluate(
        run(0.5, 0.0, outcomes={"a": False, "b": True}),
        run(0.5, 0.0, outcomes={"a": True, "b": False}),
        Thresholds(),
    )
    assert "`a t0`: pass -> fail" in r.markdown and "`b t0`: fail -> pass" in r.markdown


def test_gate_fails_when_the_slice_changes():
    smaller = run(1.0, 0.0, outcomes={"a": True})
    r = evaluate(smaller, run(0.5, 0.0), Thresholds())
    assert not r.passed and "same tasks" in r.markdown
