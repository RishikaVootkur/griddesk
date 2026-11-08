"""Concurrent eval trials that block each other are retried, never scored or hung."""

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from griddesk.agents.toolbox import Toolbox
from griddesk.db.session import TrialConflict, is_lock_timeout, trial_scope
from griddesk.eval.runner import retry_conflicts
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


async def test_a_lock_timeout_inside_a_tool_becomes_a_trial_conflict():
    server = GridDeskServer("t")

    @server.tool()
    def boom(conversation_id: str) -> dict[str, bool]:
        raise OperationalError(
            "insert", {}, Exception("LockNotAvailable: canceling statement due to lock timeout")
        )

    tb = await Toolbox.open({"t": server})
    with pytest.raises(TrialConflict):
        await tb.call("boom", {}, conversation_id="c1", agent="a", allowed=["boom"])
    await tb.close()


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
