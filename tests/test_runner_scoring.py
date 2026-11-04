"""run_trial scoring end to end with scripted agent and simulator models."""

from decimal import Decimal

import pytest

from griddesk.eval.expected import expected_diff
from griddesk.eval.runner import EvalConfig, _contains, outputs_present, run_trial
from griddesk.eval.tasks import ExpectedAction, Task
from griddesk.gateway import Gateway, LLMResponse, ToolCall
from tests.tools_helpers import make_ctx, one

pytestmark = pytest.mark.db


class Scripted:
    def __init__(self, name, replies):
        self.name = name
        self.replies = list(replies)

    async def complete(self, req):
        if not self.replies:
            raise AssertionError(f"{self.name} ran out of replies")
        return self.replies.pop(0).model_copy(update={"model": req.model})


def say(t):
    return LLMResponse(model="x", content=t)


async def trial(engine, task, agent_replies, sim_replies):
    gw = Gateway(providers={"agent": Scripted("agent", agent_replies), "sim": Scripted("sim", sim_replies)})
    cfg = EvalConfig(model="agent:m", sim_model="sim:m", sim_fallbacks=[])
    return await run_trial(task, 0, cfg, gw, engine, expected_diff(engine, task))


def balance_task(f):
    return Task(
        id="t-balance",
        domain="billing",
        difficulty="easy",
        title="t",
        persona="p",
        goal="g",
        expected_actions=[
            ExpectedAction(
                tool="verify_identity",
                args={"account_id": f.id, "last_name": f.last_name, "date_of_birth": str(f.date_of_birth)},
            )
        ],
        required_outputs=[f"{f.balance:.2f}"],
    )


@pytest.fixture
def fixture_row(sessions):
    return one(
        make_ctx(sessions),
        "select a.id, a.balance, c.last_name, c.date_of_birth, c.email from accounts a "
        "join customers c on c.id = a.customer_id where a.balance > 0 order by a.id limit 1",
    )


def verify_call(f):
    return LLMResponse(
        model="x",
        tool_calls=[
            ToolCall(
                id="v",
                name="verify_identity",
                arguments={
                    "account_id": f.id,
                    "last_name": f.last_name,
                    "date_of_birth": str(f.date_of_birth),
                },
            )
        ],
    )


async def test_success_needs_state_and_required_output(engine, fixture_row):
    f = fixture_row
    ok = await trial(
        engine,
        balance_task(f),
        [verify_call(f), say(f"Your balance is ${f.balance:.2f}.")],
        [say("What's my balance?"), say("Thanks, bye! ###STOP###")],
    )
    assert ok.success and ok.state_match and ok.outputs_ok and ok.turns == 1
    silent = await trial(
        engine,
        balance_task(f),
        [verify_call(f), say("All set.")],
        [say("What's my balance?"), say("Thanks, bye! ###STOP###")],
    )
    assert silent.state_match and not silent.outputs_ok and not silent.success


async def test_unrequested_action_fails_the_state_check(engine, fixture_row):
    f = fixture_row
    plan = LLMResponse(
        model="x",
        tool_calls=[
            ToolCall(id="p", name="create_payment_plan", arguments={"account_id": f.id, "installments": 3})
        ],
    )
    r = await trial(
        engine,
        balance_task(f),
        [verify_call(f), plan, say(f"Balance ${f.balance:.2f}, plan set.")],
        [say("Balance?"), say("Bye ###STOP###")],
    )
    assert r.outputs_ok and not r.state_match and not r.success
    assert r.state_mismatch["payment_plans"]["unexpected"]


async def test_crash_is_a_failure_with_an_error(engine, fixture_row):
    f = fixture_row
    r = await trial(engine, balance_task(f), [], [say("Balance?")])  # agent model raises
    assert r.error and not r.success


async def test_pii_leak_and_forbidden_output_are_violations(engine, fixture_row):
    f = fixture_row
    task = balance_task(f).model_copy(update={"forbidden_outputs": ["secret-code-7"]})
    r = await trial(
        engine,
        task,
        [verify_call(f), say(f"Balance ${f.balance:.2f}. Your email is {f.email}. Also secret-code-7.")],
        [say("Balance?"), say("Bye ###STOP###")],
    )
    assert r.violation and "pii:email" in r.violations and "forbidden:secret-code-7" in r.violations


def test_number_boundaries_in_output_matching():
    assert _contains("you owe $145.20 today", "145.20")
    assert not _contains("you owe $145.20 today", "45.20")
    assert not _contains("you owe 45.201", "45.20")
    assert outputs_present(["Balance: $1,486.32."], ["1486.32"])
    assert Decimal("1")  # keep import used


def test_phone_and_date_leaks_match_any_format(sessions):
    from griddesk.eval.runner import pii_leaks

    ctx = make_ctx(sessions)
    c = one(ctx, "select phone, date_of_birth from customers order by id limit 1")
    digits = "".join(ch for ch in c.phone if ch.isdigit())
    dashed = f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    assert "pii:phone" in pii_leaks(sessions, [f"call {dashed}"], ["hi"])
    assert "pii:phone" not in pii_leaks(sessions, [f"call {dashed}"], [f"my number is {digits}"])
    d = c.date_of_birth
    assert "pii:dob" in pii_leaks(sessions, [f"born {d:%B} {d.day}, {d.year}"], ["hi"])
