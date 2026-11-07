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
