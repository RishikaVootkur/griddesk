from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from griddesk.agents.toolbox import Toolbox
from griddesk.db import models as m
from griddesk.guardrails.action_guard import ActionGuard
from griddesk.mcp_servers.billing import build_billing_server
from griddesk.policy.rules import Outcome
from griddesk.tools import approvals
from tests.test_billing_server import WAIVABLE
from tests.tools_helpers import make_ctx, one, verify

pytestmark = pytest.mark.db

TWO_ACCOUNTS = """
select a1.id as a1, a2.id as a2, a1.customer_id, b.id as bill_on_a2 from accounts a1
join accounts a2 on a2.customer_id = a1.customer_id and a2.id <> a1.id
join bills b on b.account_id = a2.id
order by a1.id, b.id limit 1
"""


def test_guard_requires_the_verified_owner(sessions):
    ctx = make_ctx(sessions)
    acct = one(ctx, WAIVABLE)
    other = one(
        ctx, "select id from accounts where customer_id <> :c order by id limit 1", c=acct.customer_id
    )
    guard = ActionGuard(sessions)
    assert guard.check("get_account_summary", {"account_id": acct.id}, "g1").rule_id == "P-01"
    verify(ctx, "g1", acct.customer_id)
    assert guard.check("get_account_summary", {"account_id": acct.id}, "g1").allowed
    assert guard.check("set_paperless", {"account_id": other.id, "enabled": True}, "g1").rule_id == "P-02"


def test_guard_strips_padding_like_the_tools(sessions):
    ctx = make_ctx(sessions)
    acct = one(ctx, WAIVABLE)
    verify(ctx, "g1b", acct.customer_id)
    guard = ActionGuard(sessions)
    assert guard.check("get_account_summary", {"account_id": f"  {acct.id} "}, "g1b").allowed
    assert guard.check("explain_bill", {"bill_id": f" {acct.bill_id}\n"}, "g1b").allowed
    # Ids are case-sensitive in the tools too: a lowercased bill id names no bill and is refused.
    assert guard.check("explain_bill", {"bill_id": acct.bill_id.lower()}, "g1b").rule_id == "P-02"


def test_guard_blocks_bill_ids_from_other_accounts(sessions):
    ctx = make_ctx(sessions)
    acct = one(ctx, WAIVABLE)
    foreign_bill = one(
        ctx,
        "select b.id from bills b join accounts a on a.id = b.account_id "
        "where a.customer_id <> :c order by b.id limit 1",
        c=acct.customer_id,
    )
    verify(ctx, "g2", acct.customer_id)
    guard = ActionGuard(sessions)
    assert guard.check("explain_bill", {"bill_id": foreign_bill.id}, "g2").rule_id == "P-02"
    mixed = {
        "account_id": acct.id,
        "amount": "15.00",
        "reason_code": "late_fee_waiver",
        "bill_id": foreign_bill.id,
    }
    assert guard.check("apply_credit", mixed, "g2").rule_id == "P-02"
    missing = {**mixed, "bill_id": "B-000000-0000"}
    assert guard.check("apply_credit", missing, "g2").rule_id == "P-02"


def test_guard_blocks_a_bill_from_the_same_customers_other_account(sessions):
    ctx = make_ctx(sessions)
    row = one(ctx, TWO_ACCOUNTS)
    verify(ctx, "g2b", row.customer_id)
    args = {"account_id": row.a1, "amount": "15", "reason_code": "late_fee_waiver", "bill_id": row.bill_on_a2}
    assert ActionGuard(sessions).check("apply_credit", args, "g2b").rule_id == "P-02"


@pytest.mark.parametrize(
    ("args", "rule"),
    [
        ({"amount": "250", "reason_code": "goodwill"}, "P-10"),
        ({"amount": "12.345", "reason_code": "goodwill"}, "P-11"),
        ({"amount": "abc", "reason_code": "goodwill"}, "P-11"),
        ({"amount": "20", "reason_code": "because"}, "P-11"),
    ],
)
def test_guard_static_credit_checks(sessions, args, rule):
    assert ActionGuard(sessions).check("apply_credit", {"account_id": "x", **args}, "g3").rule_id == rule


def test_guard_enforces_the_per_conversation_credit_cap(sessions):
    ctx = make_ctx(sessions)
    acct = one(ctx, WAIVABLE)
    verify(ctx, "g3b", acct.customer_id)
    with ctx.sessions() as s:
        s.add(
            m.Credit(
                id="CR-CAP1",
                account_id=acct.id,
                amount=Decimal("150.00"),
                reason_code="goodwill",
                applied_on=date(2026, 10, 15),
                conversation_id="g3b",
            )
        )
        s.commit()
    d = ActionGuard(sessions).check(
        "apply_credit", {"account_id": acct.id, "amount": "60", "reason_code": "goodwill"}, "g3b"
    )
    assert d.outcome is Outcome.DENY and d.rule_id == "P-10"


@pytest.mark.parametrize("enabled", [True, "true", "True", 1, "1", "yes"])
def test_guard_blocks_autopay_enrollment_with_lax_bools(sessions, enabled):
    guard = ActionGuard(sessions)
    assert guard.check("set_autopay", {"account_id": "x", "enabled": enabled}, "g4").rule_id == "P-24"


def test_guard_rejects_non_bool_autopay_and_allows_turning_it_off(sessions):
    ctx = make_ctx(sessions)
    acct = one(ctx, WAIVABLE)
    verify(ctx, "g4b", acct.customer_id)
    guard = ActionGuard(sessions)
    assert guard.check("set_autopay", {"account_id": acct.id, "enabled": "maybe"}, "g4b").rule_id == "P-24"
    assert guard.check("set_autopay", {"account_id": acct.id, "enabled": "false"}, "g4b").allowed


def test_guard_blocks_unverified_starts(sessions):
    guard = ActionGuard(sessions)
    assert (
        guard.check("start_service", {"service_address_id": "SA-1", "start_date": "2026-10-20"}, "g4").rule_id
        == "P-01"
    )
    assert guard.check("lookup_outage", {"street": "1 Main St"}, "g4").allowed


@pytest.mark.parametrize("tool", ["report_outage", "escalate_to_human"])
def test_guard_checks_optional_account_ids(sessions, tool):
    ctx = make_ctx(sessions)
    acct = one(ctx, WAIVABLE)
    other = one(
        ctx, "select id from accounts where customer_id <> :c order by id limit 1", c=acct.customer_id
    )
    guard = ActionGuard(sessions)
    base = {"street": "1 Main St", "description": "lights out", "category": "hazard", "summary": "x"}
    assert guard.check(tool, base, "g5").allowed  # no account named: anyone may report
    assert guard.check(tool, {**base, "account_id": ""}, "g5").allowed
    unverified = guard.check(tool, {**base, "account_id": acct.id}, "g5")
    assert unverified.rule_id == "P-01" and "without account_id" in unverified.message
    verify(ctx, "g5", acct.customer_id)
    assert guard.check(tool, {**base, "account_id": acct.id}, "g5").allowed
    assert guard.check(tool, {**base, "account_id": other.id}, "g5").rule_id == "P-02"


def test_guard_returns_needs_approval_for_the_gated_actions(sessions):
    ctx = make_ctx(sessions)
    acct = one(ctx, WAIVABLE)
    verify(ctx, "g6", acct.customer_id)
    guard = ActionGuard(sessions)

    def credit(amount, reason, **extra):
        return guard.check(
            "apply_credit", {"account_id": acct.id, "amount": amount, "reason_code": reason, **extra}, "g6"
        )

    for d, rule in (
        (credit("40", "goodwill"), "P-11"),
        (credit("30", "billing_error", bill_id=acct.bill_id), "P-11"),
        (credit("60", "late_fee_waiver", bill_id=acct.bill_id), "P-09"),
        (guard.check("stop_service", {"account_id": acct.id, "stop_date": "2026-10-30"}, "g6"), "P-19"),
        (
            guard.check(
                "transfer_service",
                {"account_id": acct.id, "new_service_address_id": "SA-1", "move_date": "2026-10-30"},
                "g6",
            ),
            "P-21",
        ),
    ):
        assert d.outcome is Outcome.NEEDS_APPROVAL and d.rule_id == rule
    assert credit("25", "outage_compensation").allowed


async def _toolbox(ctx, *, park):
    tb = await Toolbox.open({"billing": build_billing_server(ctx)})
    tb.guard = ActionGuard(ctx.sessions, ctx.clock, ctx.ids, park_approvals=park)
    return tb


def _count(ctx, model, conv):
    with ctx.sessions() as s:
        return s.scalar(select(func.count()).select_from(model).where(model.conversation_id == conv))


async def test_guard_only_parks_approval_gated_actions(sessions):
    """In-tool policy off, guard on: a goodwill credit waits for a supervisor instead of running."""
    ctx = make_ctx(sessions, enforce=False)
    acct = one(ctx, WAIVABLE)
    verify(ctx, "g7", acct.customer_id)
    tb = await _toolbox(ctx, park=True)
    args = {"account_id": acct.id, "amount": "$40", "reason_code": "goodwill", "note": "sorry"}
    out = await tb.call("apply_credit", args, conversation_id="g7", agent="billing")
    assert out["error"]["code"] == "needs_approval" and out["error"]["rule_id"] == "P-11"
    apr = out["approval_request_id"]
    assert _count(ctx, m.Credit, "g7") == 0
    assert tb.calls[-1].error_code == "needs_approval"
    with ctx.sessions() as s:
        outcomes = s.scalars(select(m.AuditLog.outcome).where(m.AuditLog.conversation_id == "g7")).all()
    assert "pending_approval" in outcomes and "policy_bypassed" not in outcomes
    # The operator approves and the runtime executes it through the normal approvals flow.
    approvals.decide(ctx, apr, "approve", operator="op")
    done = await tb.execute_approved("apply_credit", apr)
    assert done["ok"] and done["status"] == "executed"
    with ctx.sessions() as s:
        credit = s.scalars(select(m.Credit).where(m.Credit.conversation_id == "g7")).one()
    assert credit.amount == Decimal("40.00") and credit.approval_request_id == apr
    await tb.close()


async def test_guard_with_in_tool_policy_leaves_parking_to_the_tool(sessions):
    ctx = make_ctx(sessions)
    acct = one(ctx, WAIVABLE)
    verify(ctx, "g8", acct.customer_id)
    tb = await _toolbox(ctx, park=False)
    args = {"account_id": acct.id, "amount": "40", "reason_code": "goodwill"}
    out = await tb.call("apply_credit", args, conversation_id="g8", agent="billing")
    assert out["error"]["code"] == "needs_approval" and out["approval_request_id"]
    assert _count(ctx, m.ApprovalRequest, "g8") == 1
    await tb.close()
