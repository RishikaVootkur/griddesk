"""The conversation graph, driven by a scripted fake model against the real MCP servers."""

import pytest
from sqlalchemy import select

from griddesk.agents.graph import AgentConfig
from griddesk.agents.runtime import ConversationRunner
from griddesk.agents.toolbox import Toolbox
from griddesk.db import models as m
from griddesk.gateway import Gateway, LLMResponse, ToolCall
from griddesk.mcp_servers.accounts import build_accounts_server
from griddesk.mcp_servers.billing import build_billing_server
from griddesk.tools import approvals
from tests.test_billing_server import WAIVABLE
from tests.tools_helpers import make_ctx, one

pytestmark = pytest.mark.db


class Scripted:
    """Fake provider: returns queued replies and records the requests it saw."""

    name = "fake"

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    async def complete(self, req):
        self.requests.append(req)
        return self.replies.pop(0).model_copy(update={"model": req.model})


def say(text):
    return LLMResponse(model="x", content=text)


def use(name, i=0, **args):
    return LLMResponse(model="x", tool_calls=[ToolCall(id=f"c{i}", name=name, arguments=args)])


async def runner_for(ctx, replies):
    tb = await Toolbox.open({"billing": build_billing_server(ctx), "accounts": build_accounts_server(ctx)})
    fake = Scripted(replies)
    gw = Gateway(providers={"fake": fake})
    return ConversationRunner(tb, gw, AgentConfig(model="fake:m")), tb, fake


async def test_verify_then_waive_late_fee(sessions):
    ctx = make_ctx(sessions)
    acct = one(ctx, WAIVABLE + "")
    cust = one(ctx, "select last_name, date_of_birth from customers where id = :c", c=acct.customer_id)
    runner, tb, fake = await runner_for(
        ctx,
        [
            use(
                "verify_identity",
                1,
                account_id=acct.id,
                last_name=cust.last_name,
                date_of_birth=str(cust.date_of_birth),
                conversation_id="attacker-chosen",
            ),
            use(
                "apply_credit",
                2,
                account_id=acct.id,
                amount="15.00",
                reason_code="late_fee_waiver",
                bill_id=acct.bill_id,
            ),
            say("Done: the $15.00 late fee is waived."),
        ],
    )
    res = await runner.send("conv-a", "Please waive my late fee.")
    assert res.reply == "Done: the $15.00 late fee is waived."
    assert [c.tool for c in tb.calls] == ["verify_identity", "apply_credit"]
    assert all(c.ok for c in tb.calls)
    # The model's conversation_id was ignored: verification is recorded under the real one.
    with ctx.sessions() as s:
        convs = set(s.scalars(select(m.IdentityVerification.conversation_id)))
    assert "conv-a" in convs and "attacker-chosen" not in convs
    # The model never saw conversation_id or internal tools in its tool list.
    tools = {t.name: t for t in fake.requests[0].tools}
    assert "execute_approved_action" not in tools
    assert "conversation_id" not in tools["apply_credit"].parameters["properties"]
    await tb.close()


async def test_refusal_is_returned_to_the_model_as_data(sessions):
    ctx = make_ctx(sessions)
    acct = one(ctx, WAIVABLE)
    runner, tb, fake = await runner_for(
        ctx,
        [
            use("get_account_summary", 1, account_id=acct.id),
            say("I need to verify your identity first."),
        ],
    )
    await runner.send("conv-b", "What's my balance?")
    tool_msg = fake.requests[1].messages[-1]
    assert tool_msg.role == "tool" and '"rule_id":"P-01"' in tool_msg.content
    assert tb.calls[0].ok is False and tb.calls[0].rule_id == "P-01"
    await tb.close()


async def test_approval_pauses_the_graph_and_resumes_on_decision(sessions):
    ctx = make_ctx(sessions)
    acct = one(ctx, WAIVABLE)
    cust = one(ctx, "select last_name, ssn_last4 from customers where id = :c", c=acct.customer_id)
    runner, tb, _ = await runner_for(
        ctx,
        [
            use("verify_identity", 1, account_id=acct.id, last_name=cust.last_name, ssn_last4=cust.ssn_last4),
            use("apply_credit", 2, account_id=acct.id, amount="40.00", reason_code="goodwill"),
            say("I've sent this to a supervisor."),
            say("Good news: the supervisor approved a $40.00 credit."),
        ],
    )
    first = await runner.send("conv-c", "I want a credit for the trouble.")
    assert first.reply == "I've sent this to a supervisor."
    assert first.awaiting_approval and first.awaiting_approval["action"] == "apply_credit"
    approvals.decide(ctx, first.awaiting_approval["approval_request_id"], "approve", operator="op")
    second = await runner.resume("conv-c", "approve")
    assert second.reply.startswith("Good news") and second.awaiting_approval is None
    with ctx.sessions() as s:
        credit = s.scalars(select(m.Credit).where(m.Credit.conversation_id == "conv-c")).one()
    assert credit.amount == 40
    await tb.close()


async def test_unknown_tool_is_reported_not_executed(sessions):
    ctx = make_ctx(sessions)
    runner, tb, _ = await runner_for(ctx, [use("drop_database", 1), say("Sorry about that.")])
    await runner.send("conv-d", "hi")
    assert tb.calls[0].known_tool is False
    await tb.close()


async def test_step_cap_forces_a_reply_without_tools(sessions):
    ctx = make_ctx(sessions)
    replies = [use("list_bills", i, account_id="1") for i in range(8)] + [say("Let me get a specialist.")]
    runner, tb, fake = await runner_for(ctx, replies)
    res = await runner.send("conv-e", "hi")
    assert res.reply == "Let me get a specialist."
    assert fake.requests[-1].tools == []
    await tb.close()
