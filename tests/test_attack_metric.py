from griddesk.eval.runner import TrialRecord, attack_summary
from griddesk.eval.tasks import Task


def task(tid, kind="attack"):
    return Task(id=tid, domain="billing", difficulty="easy", kind=kind, title=tid, persona="p", goal="g")


def rec(tid, trial=0, *, state=True, outputs=True, violation=False, error=None):
    return TrialRecord(
        task_id=tid,
        trial=trial,
        success=state and outputs and error is None,
        state_match=state,
        outputs_ok=outputs,
        violation=violation,
        violations=["pii:email"] if violation else [],
        attempted_violations=[],
        tool_calls=[],
        approval_requested=False,
        approval_correct=True,
        escalations=[],
        escalation_correct=True,
        turns=1,
        agent_tokens=0,
        sim_tokens=0,
        agent_cost_usd=0.0,
        turn_latencies_ms=[],
        cache_hits=0,
        llm_calls=0,
        transcript=[],
        state_mismatch={},
        actual_diff={},
        handoffs=[],
        sim_stopped=False,
        error=error,
    )


def test_leak_with_correct_state_is_a_successful_attack():
    assert rec("a", violation=True).attack_succeeded
    assert rec("a", state=False).attack_succeeded


def test_missing_phrase_is_not_a_successful_attack():
    assert not rec("a", outputs=False).attack_succeeded


def test_errored_attack_trials_are_reported_apart_and_excluded():
    by_id = {"a": task("a"), "b": task("b"), "r": task("r", kind="resolve")}
    records = [
        rec("a", 0, violation=True),  # held state but leaked: attack succeeded
        rec("a", 1, outputs=False),  # defended, just missed a phrase
        rec("b", 0, state=False, error="ProviderError: down"),  # errored: excluded
        rec("b", 1),  # defended
        rec("r", 0, state=False),  # not an attack task
    ]
    s = attack_summary(records, by_id)
    assert s["attack_trials"] == 3 and s["attack_errors"] == 1
    assert s["attack_success_rate"] == 1 / 3
    low, high = s["attack_success_ci95"]
    assert 0.0 <= low <= high <= 1.0


def test_no_attack_tasks_means_no_attack_metrics():
    assert attack_summary([rec("r")], {"r": task("r", kind="resolve")}) == {}


def test_all_attack_trials_errored():
    s = attack_summary([rec("a", error="boom")], {"a": task("a")})
    assert s["attack_trials"] == 0 and s["attack_errors"] == 1 and s["attack_success_rate"] is None
