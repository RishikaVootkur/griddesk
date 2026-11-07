from griddesk.eval.analysis import classify
from griddesk.eval.tasks import ExpectedAction, Task


def task(**kw):
    base = dict(
        id="t",
        domain="billing",
        difficulty="easy",
        title="t",
        persona="p",
        goal="g",
        expected_actions=[ExpectedAction(tool="verify_identity"), ExpectedAction(tool="apply_credit")],
    )
    base.update(kw)
    return Task(**base)


def trial(**kw):
    base = dict(
        success=False,
        error=None,
        violation=False,
        tool_calls=[],
        state_mismatch={},
        escalations=[],
        outputs_ok=True,
        transcript=[{"speaker": "customer", "text": "hi"}],
    )
    base.update(kw)
    return base


def call(tool, ok=True, rule_id=None):
    return {"tool": tool, "ok": ok, "rule_id": rule_id}


def test_success_is_not_classified():
    assert classify(trial(success=True), task()) is None


def test_bucket_order():
    assert classify(trial(error="SimulatorError: empty"), task()) == "simulator_error"
    assert classify(trial(), task(), {"broke_task": True}) == "simulator_error"
    assert classify(trial(error="ProviderError: 503"), task()) == "infra_error"
    assert classify(trial(violation=True), task()) == "policy_breach"
    assert (
        classify(trial(tool_calls=[call("apply_credit", ok=False, rule_id="P-01")]), task())
        == "skipped_verification"
    )
    assert classify(trial(tool_calls=[call("verify_identity")]), task()) == "wrong_tool"
    wrong_amount = trial(
        tool_calls=[call("verify_identity"), call("apply_credit")],
        state_mismatch={"credits": {"unexpected": [{"amount": "30"}], "missing": [{"amount": "15"}]}},
    )
    assert classify(wrong_amount, task()) == "wrong_arguments"
    gave_up = trial(
        tool_calls=[call("verify_identity"), call("escalate_to_human")], escalations=["complaint"]
    )
    assert classify(gave_up, task()) == "gave_up_early"
    assert (
        classify(trial(tool_calls=[call("verify_identity"), call("apply_credit")], outputs_ok=False), task())
        == "missing_output"
    )


def test_garbled_customer_text_is_a_simulator_error():
    t = trial(transcript=[{"speaker": "customer", "text": "Okay, what is this? " * 4}])
    assert classify(t, task()) == "simulator_error"


def test_correct_state_and_outputs_fail_on_approval_or_escalation():
    ok = trial(tool_calls=[call("verify_identity"), call("apply_credit")])
    assert classify(ok, task()) == "approval_or_escalation"


def test_refused_write_counts_as_not_done():
    refused = trial(
        tool_calls=[call("verify_identity"), call("apply_credit", ok=False, rule_id="P-09")],
        state_mismatch={"credits": {"unexpected": [], "missing": [{"amount": "60"}]}},
    )
    assert classify(refused, task()) == "wrong_tool"


def test_extra_write_beside_a_correct_one_is_wrong_tool():
    extra = trial(
        tool_calls=[call("verify_identity"), call("apply_credit"), call("create_payment_plan")],
        state_mismatch={"payment_plans": {"unexpected": [{"installments": "3"}], "missing": []}},
    )
    assert classify(extra, task()) == "wrong_tool"


def test_verified_late_is_not_skipped_verification():
    late = trial(
        tool_calls=[
            call("apply_credit", ok=False, rule_id="P-01"),
            call("verify_identity"),
            call("apply_credit"),
        ],
        state_mismatch={"credits": {"unexpected": [{"a": 1}], "missing": [{"a": 2}]}},
    )
    assert classify(late, task()) == "wrong_arguments"


def test_stratified_sample_mixes_failures_and_runs():
    from griddesk.eval.analysis import stratified_sample

    def run(label, n_fail, n_pass):
        trials = [dict(trial(success=False), task_id=f"f{i}", trial=0) for i in range(n_fail)]
        trials += [dict(trial(success=True), task_id=f"p{i}", trial=0) for i in range(n_pass)]
        return {"config": {"label": label}, "trials": trials}

    sample = stratified_sample([run("big", 40, 100), run("small", 3, 5)], n=10, seed=1)
    assert len(sample) == 10
    assert sum(not t["success"] for _, t in sample) == 5
    assert {r["config"]["label"] for r, _ in sample} == {"big", "small"}
