"""Metric and statistics functions, checked against values computed by hand."""

import math

import pytest

from griddesk.eval.metrics import TrialMetrics, pass_hat_k, pass_hat_k_suite, percentile, summarize
from griddesk.eval.stats import bootstrap_ci, paired_test


def test_pass_hat_k_matches_combinatorics():
    # 4 trials, 3 successes: pass^1 = 3/4, pass^2 = C(3,2)/C(4,2) = 3/6, pass^3 = 1/4, pass^4 = 0
    assert pass_hat_k(4, 3, 1) == 0.75
    assert pass_hat_k(4, 3, 2) == 0.5
    assert pass_hat_k(4, 3, 3) == 0.25
    assert pass_hat_k(4, 3, 4) == 0.0
    assert pass_hat_k(4, 4, 4) == 1.0
    with pytest.raises(ValueError):
        pass_hat_k(4, 3, 5)


def test_pass_hat_k_suite_averages_tasks():
    # task A: 4/4, task B: 2/4, task C: 0/4
    per = {"A": [True] * 4, "B": [True, False, True, False], "C": [False] * 4}
    assert pass_hat_k_suite(per, 1) == pytest.approx((1 + 0.5 + 0) / 3)
    # pass^2: A=1, B=C(2,2)/C(4,2)=1/6, C=0
    assert pass_hat_k_suite(per, 2) == pytest.approx((1 + 1 / 6) / 3)


def test_pass_hat_k_never_increases_with_k():
    per = {"A": [True, True, False, True], "B": [False, True, True, True]}
    vals = [pass_hat_k_suite(per, k) for k in range(1, 5)]
    assert vals == sorted(vals, reverse=True)


def test_percentile_linear_interpolation():
    xs = [100.0, 200.0, 300.0, 400.0]
    assert percentile(xs, 50) == 250.0  # between 200 and 300
    assert percentile(xs, 95) == pytest.approx(385.0)  # 300 + 0.85 * 100
    assert percentile([], 50) == 0.0


def tm(
    task, trial, success, cost=0.01, tokens=1000, turns=3, lat=(100.0,), violation=False, calls=2, valid=2
):
    return TrialMetrics(
        task, trial, success, violation, 0, calls, valid, 1.0, True, True, turns, tokens, cost, lat
    )


def test_summary_hand_computed():
    trials = [
        tm("A", 0, True, cost=0.02, tokens=1000, turns=2, lat=(100.0, 300.0)),
        tm("A", 1, False, cost=0.04, tokens=3000, turns=5, lat=(200.0,), violation=True, calls=4, valid=3),
        tm("B", 0, True, cost=0.01, tokens=2000, turns=4, lat=(400.0,)),
        tm("B", 1, True, cost=0.01, tokens=2000, turns=4, lat=(500.0,)),
    ]
    s = summarize(trials, max_k=2)
    assert s["success_rate"] == 0.75
    assert s["violation_rate"] == 0.25
    assert s["tool_call_validity"] == pytest.approx(9 / 10)
    # resolved trials: A0, B0, B1 -> turns (2+4+4)/3, tokens (1000+2000+2000)/3
    assert s["turns_per_resolved"] == pytest.approx(10 / 3)
    assert s["tokens_per_resolved"] == pytest.approx(5000 / 3)
    # cost per resolved = all spend / resolved = 0.08 / 3
    assert s["cost_per_resolved_usd"] == pytest.approx(0.08 / 3)
    # latencies 100, 300, 200, 400, 500 -> median 300
    assert s["latency_p50_ms"] == 300.0
    assert s["pass^1"] == pytest.approx((0.5 + 1.0) / 2)
    assert s["pass^2"] == pytest.approx((0.0 + 1.0) / 2)


def test_summary_leaves_pass_k_empty_when_too_few_trials():
    s = summarize([tm("A", 0, True)], max_k=3)
    assert s["pass^1"] == 1.0 and s["pass^2"] is None and s["pass^3"] is None


def test_bootstrap_ci_brackets_the_mean_and_is_deterministic():
    per = {f"t{i}": [1.0 if i % 4 else 0.0] for i in range(40)}  # 30 of 40 tasks succeed
    ci = bootstrap_ci(per, iters=4000, seed=1)
    assert ci.mean == 0.75
    assert ci.low < 0.75 < ci.high
    # normal approximation: SE = sqrt(.75*.25/40) = 0.0685, so the 95% CI is about +-0.134
    assert ci.high - ci.low == pytest.approx(2 * 1.96 * math.sqrt(0.75 * 0.25 / 40), abs=0.04)
    assert bootstrap_ci(per, iters=4000, seed=1) == ci


def test_bootstrap_of_constant_is_a_point():
    ci = bootstrap_ci({"a": [1.0], "b": [1.0]})
    assert (ci.low, ci.mean, ci.high) == (1.0, 1.0, 1.0)


def test_paired_test_identical_runs():
    a = {"x": [1.0, 0.0], "y": [1.0, 1.0]}
    r = paired_test(a, a)
    assert r.p_value == 1.0 and r.diff.mean == 0.0 and r.wins_a == r.wins_b == 0


def test_paired_test_detects_a_consistent_improvement():
    a = {f"t{i}": [0.0] for i in range(20)}
    b = {f"t{i}": [1.0] for i in range(20)}
    r = paired_test(a, b, iters=5000)
    assert r.diff.mean == 1.0 and r.wins_b == 20
    # every sign flip of twenty +1s gives |mean| < 1 except the all-positive and all-negative ones
    assert r.p_value < 0.001


def test_paired_test_uses_only_shared_tasks():
    a = {"x": [1.0], "y": [0.0], "only_a": [1.0]}
    b = {"x": [1.0], "y": [1.0], "only_b": [0.0]}
    r = paired_test(a, b, iters=2000)
    assert r.n_tasks == 2 and r.mean_a == 0.5 and r.mean_b == 1.0
    with pytest.raises(ValueError):
        paired_test({"a": [1.0]}, {"b": [1.0]})


def test_paired_test_small_sample_p_value():
    # one task better in B, one equal: diffs (1, 0). Sign flips give |mean| = 0.5 every time, so p is 1.
    r = paired_test({"a": [0.0], "b": [1.0]}, {"a": [1.0], "b": [1.0]}, iters=2000)
    assert r.p_value == pytest.approx(1.0)
