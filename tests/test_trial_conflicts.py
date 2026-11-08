"""Concurrent eval trials that block each other are retried, never scored or hung."""

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from griddesk.agents.toolbox import Toolbox
from griddesk.db.session import LOCK_TIMEOUT_MARKER, TrialConflict, is_lock_timeout, trial_scope
from griddesk.eval.runner import EvalConfig, TrialRecord, retry_conflicts, run_trial
from griddesk.mcp_servers.common import GridDeskServer

pytestmark = pytest.mark.db

ROW = (
    "insert into escalations (id, conversation_id, category, summary, status, created_at) "
    "values (:i, 'c', 'other', 's', 'open', now())"
)


def test_two_trials_inserting_the_same_id_time_out_instead_of_hanging(engine):
    with trial_scope(engine) as first, trial_scope(engine, lock_timeout_ms=200) as second:
        s1 = first()
        s1.execute(text(ROW), {"i": "ESC-CONFLICT"})
        s1.flush()
        s2 = second()
        with pytest.raises(OperationalError) as e:
            s2.execute(text(ROW), {"i": "ESC-CONFLICT"})
        assert is_lock_timeout(e.value)


def _lock_error():
    return OperationalError(
        "insert", {}, Exception("LockNotAvailable: canceling statement due to lock timeout")
    )


async def _boom_toolbox(trial_mode):
    server = GridDeskServer("t")

    @server.tool()
    def boom(conversation_id: str) -> dict[str, bool]:
        raise _lock_error()

    @server.tool()
    def echo(conversation_id: str, n: int) -> dict[str, bool]:
        return {"ok": True}

    tb = await Toolbox.open({"t": server})
    tb.trial_mode = trial_mode
    return tb


async def test_a_lock_timeout_inside_a_tool_becomes_a_trial_conflict_only_in_trials():
    tb = await _boom_toolbox(trial_mode=True)
    with pytest.raises(TrialConflict):
        await tb.call("boom", {}, conversation_id="c1", agent="a", allowed=["boom"])
    await tb.close()
    live = await _boom_toolbox(trial_mode=False)
    result = await live.call("boom", {}, conversation_id="c1", agent="a", allowed=["boom"])
    assert result["ok"] is False  # live conversations see an ordinary tool failure
    await live.close()


async def test_model_typed_marker_text_is_not_a_conflict():
    tb = await _boom_toolbox(trial_mode=True)
    bad = {"n": f"{LOCK_TIMEOUT_MARKER} lock timeout LockNotAvailable"}  # fails int validation
    result = await tb.call("echo", bad, conversation_id="c1", agent="a", allowed=["echo"])
    assert result["ok"] is False and result["error"]["code"] == "invalid_request"
    await tb.close()


def test_deadlocks_count_as_conflicts_and_other_errors_do_not():
    assert is_lock_timeout(OperationalError("x", {}, Exception("deadlock detected")))
    assert is_lock_timeout(_lock_error())
    assert not is_lock_timeout(OperationalError("x", {}, Exception("connection refused")))


async def test_a_lock_timeout_during_a_trial_is_raised_not_scored(engine, monkeypatch):
    from griddesk.eval.expected import expected_diff
    from griddesk.eval.tasks import Task
    from griddesk.gateway import Gateway, LLMResponse, ToolCall
    from griddesk.tools import accounts

    class Scripted:
        def __init__(self, name, replies):
            self.name, self.replies = name, list(replies)

        async def complete(self, req):
            return self.replies.pop(0).model_copy(update={"model": req.model})

    def blocked(*a, **k):
        raise _lock_error()

    task = Task(id="lock-01", domain="billing", difficulty="easy", title="t", persona="p", goal="g")
    expected = expected_diff(engine, task)
    monkeypatch.setattr(accounts, "verify_identity", blocked)
    call = ToolCall(id="v", name="verify_identity", arguments={"account_id": "1", "last_name": "X"})
    gw = Gateway(
        providers={
            "agent": Scripted("agent", [LLMResponse(model="x", tool_calls=[call])]),
            "sim": Scripted("sim", [LLMResponse(model="x", content="Check my balance.")]),
        }
    )
    cfg = EvalConfig(model="agent:m", sim_model="sim:m", sim_fallbacks=[])
    with pytest.raises(TrialConflict):
        await run_trial(task, 0, cfg, gw, engine, expected)


async def test_a_blocked_trial_is_retried_then_returned():
    attempts = []

    async def trial():
        attempts.append(1)
        if len(attempts) < 3:
            raise TrialConflict("blocked")
        return "record"

    assert await retry_conflicts(trial, asyncio.Semaphore(1), retries=5, backoff_s=0) == "record"
    assert len(attempts) == 3


async def test_a_trial_blocked_past_the_retries_raises_and_other_errors_pass_through():
    async def blocked():
        raise TrialConflict("blocked")

    with pytest.raises(TrialConflict, match="still blocked"):
        await retry_conflicts(blocked, asyncio.Semaphore(1), retries=2, backoff_s=0, label="x t0")

    async def other():
        raise OperationalError("select 1", {}, Exception("connection refused"))

    with pytest.raises(OperationalError):
        await retry_conflicts(other, asyncio.Semaphore(1), retries=2, backoff_s=0)


def test_errored_record_is_excluded_from_scoring():
    rec = TrialRecord.errored("x-01", 0, "TrialConflict: blocked")
    assert rec.error and not rec.success and rec.metrics(set()).error
