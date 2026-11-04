from decimal import Decimal

import pytest
from mcp import Client
from sqlalchemy import func, select

from griddesk.db import models as m
from griddesk.mcp_servers.billing import build_billing_server
from griddesk.tools import approvals
from tests.tools_helpers import call, make_ctx, one, verify

pytestmark = pytest.mark.db
CONV = "conv-test-1"

# A past-due account whose newest bill carries a late fee, with no waiver, plan or default history.
WAIVABLE = """
select a.id, a.customer_id, b.id as bill_id from accounts a
join bills b on b.account_id = a.id
join bill_line_items li on li.bill_id = b.id and li.kind = 'late_fee'
where a.status = 'active' and b.status in ('past_due', 'open')
  and not exists (select 1 from credits c where c.account_id = a.id)
  and not exists (select 1 from payment_plans p where p.account_id = a.id)
  and a.balance > 100
order by a.id limit 1
"""


@pytest.fixture
def ctx(sessions):
    return make_ctx(sessions)


async def test_tools_list_hides_nothing_but_marks_internal(ctx):
    async with Client(build_billing_server(ctx)) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    assert {"get_account_summary", "apply_credit", "create_payment_plan", "execute_approved_action"} <= set(
        tools
    )
    assert tools["execute_approved_action"].meta == {"griddesk/internal": True}
    assert tools["apply_credit"].output_schema is not None
    assert "approval_request_id" not in tools["apply_credit"].input_schema["properties"]


async def test_unverified_access_is_refused_with_rule_id(ctx):
    acct = one(ctx, WAIVABLE)
    async with Client(build_billing_server(ctx)) as c:
        out = await call(c, "get_account_summary", conversation_id=CONV, account_id=acct.id)
        missing = await call(c, "get_account_summary", conversation_id=CONV, account_id="9999999999")
    assert out["ok"] is False and out["error"]["rule_id"] == "P-01"
    # A missing account looks the same as an unverified one, so the tool is not an account oracle.
    assert missing["error"]["rule_id"] == "P-01"


async def test_other_customers_account_is_refused(ctx):
    acct = one(ctx, WAIVABLE)
    other = one(ctx, "select id from customers where id <> :c limit 1", c=acct.customer_id)
    verify(ctx, CONV, other.id)
    async with Client(build_billing_server(ctx)) as c:
        out = await call(c, "list_bills", conversation_id=CONV, account_id=acct.id)
    assert out["error"]["rule_id"] == "P-02"


async def test_summary_and_explain_for_verified_customer(ctx):
    acct = one(ctx, WAIVABLE)
    verify(ctx, CONV, acct.customer_id)
    async with Client(build_billing_server(ctx)) as c:
        summary = await call(c, "get_account_summary", conversation_id=CONV, account_id=acct.id)
        expl = await call(c, "explain_bill", conversation_id=CONV, bill_id=acct.bill_id)
    assert summary["ok"] and Decimal(summary["balance"]) > 100
    kinds = [li["kind"] for li in expl["bill"]["line_items"]]
    assert "late_fee" in kinds and expl["rates"]["delivery_per_kwh"] == "0.0980"


async def test_late_fee_waiver_once_then_refused(ctx):
    acct = one(ctx, WAIVABLE)
    verify(ctx, CONV, acct.customer_id)
    async with Client(build_billing_server(ctx)) as c:
        first = await call(
            c,
            "apply_credit",
            conversation_id=CONV,
            account_id=acct.id,
            amount="15.00",
            reason_code="late_fee_waiver",
            bill_id=acct.bill_id,
        )
        second = await call(
            c,
            "apply_credit",
            conversation_id=CONV,
            account_id=acct.id,
            amount="15.00",
            reason_code="late_fee_waiver",
            bill_id=acct.bill_id,
        )
    assert first["ok"] and first["credit_id"].startswith("CR-")
    assert second["ok"] is False and second["error"]["rule_id"] == "P-08"
    with ctx.sessions() as s:
        assert s.scalar(select(func.count()).select_from(m.Credit).where(m.Credit.account_id == acct.id)) == 1
        refused = s.scalars(select(m.AuditLog).where(m.AuditLog.outcome == "refused")).all()
        assert refused and refused[-1].rule_id == "P-08"


async def test_goodwill_credit_parks_for_approval_and_executes_after_approval(ctx):
    acct = one(ctx, WAIVABLE)
    verify(ctx, CONV, acct.customer_id)
    server = build_billing_server(ctx)
    async with Client(server) as c:
        out = await call(
            c,
            "apply_credit",
            conversation_id=CONV,
            account_id=acct.id,
            amount="30.00",
            reason_code="goodwill",
            note="long hold times",
        )
        apr = out["approval_request_id"]
        assert out["ok"] is False and out["error"]["code"] == "needs_approval" and apr
        # Executing before a human decides does nothing.
        early = await call(c, "execute_approved_action", approval_request_id=apr)
        assert early["ok"] is False and early["status"] == "pending"
        approvals.decide(ctx, apr, "approve", operator="op-1")
        done = await call(c, "execute_approved_action", approval_request_id=apr)
        again = await call(c, "execute_approved_action", approval_request_id=apr)
    assert done["ok"] and done["status"] == "executed"
    assert again["ok"] is False  # an executed approval cannot be replayed
    with ctx.sessions() as s:
        credits = s.scalars(select(m.Credit).where(m.Credit.account_id == acct.id)).all()
        assert [c.amount for c in credits] == [Decimal("30.00")]
        assert credits[0].approval_request_id == apr


async def test_denied_approval_never_executes(ctx):
    acct = one(ctx, WAIVABLE)
    verify(ctx, CONV, acct.customer_id)
    async with Client(build_billing_server(ctx)) as c:
        out = await call(
            c,
            "apply_credit",
            conversation_id=CONV,
            account_id=acct.id,
            amount="20.00",
            reason_code="goodwill",
        )
        approvals.decide(ctx, out["approval_request_id"], "deny", operator="op-1")
        res = await call(c, "execute_approved_action", approval_request_id=out["approval_request_id"])
    assert res["ok"] is False and res["status"] == "denied"


async def test_outage_compensation_for_long_restored_outage(ctx):
    acct = one(
        ctx,
        """select a.id, a.customer_id from accounts a join service_addresses sa
                       on sa.id = a.service_address_id join outages o on o.feeder = sa.feeder
                       where o.id = 'OUT-2610-05' and a.status = 'active' order by a.id limit 1""",
    )
    verify(ctx, CONV, acct.customer_id)
    async with Client(build_billing_server(ctx)) as c:
        ok = await call(
            c,
            "apply_credit",
            conversation_id=CONV,
            account_id=acct.id,
            amount="25.00",
            reason_code="outage_compensation",
            outage_id="OUT-2610-05",
        )
        dup = await call(
            c,
            "apply_credit",
            conversation_id=CONV,
            account_id=acct.id,
            amount="25.00",
            reason_code="outage_compensation",
            outage_id="OUT-2610-05",
        )
        short = await call(
            c,
            "apply_credit",
            conversation_id=CONV,
            account_id=acct.id,
            amount="25.00",
            reason_code="outage_compensation",
            outage_id="OUT-2610-04",
        )
    assert ok["ok"] and dup["error"]["rule_id"] == "P-12" and short["error"]["rule_id"] == "P-12"


async def test_payment_plan_cancels_scheduled_disconnect(ctx):
    acct = one(
        ctx,
        """select a.id, a.customer_id, a.balance from accounts a
                       where a.status = 'disconnect_scheduled' and a.balance > 100
                       and not exists (select 1 from payment_plans p where p.account_id = a.id) limit 1""",
    )
    verify(ctx, CONV, acct.customer_id)
    async with Client(build_billing_server(ctx)) as c:
        bad = await call(c, "create_payment_plan", conversation_id=CONV, account_id=acct.id, installments=24)
        out = await call(c, "create_payment_plan", conversation_id=CONV, account_id=acct.id, installments=4)
        dup = await call(c, "create_payment_plan", conversation_id=CONV, account_id=acct.id, installments=4)
    assert bad["error"]["rule_id"] == "P-15"
    assert out["ok"] and out["disconnect_cancelled"] is True
    assert Decimal(out["plan"]["installment_amount"]) * 4 >= acct.balance
    assert dup["error"]["rule_id"] == "P-15"
    with ctx.sessions() as s:
        assert s.get(m.Account, acct.id).status == "active"


async def test_dispute_then_no_credit_on_disputed_bill(ctx):
    acct = one(ctx, WAIVABLE)
    verify(ctx, CONV, acct.customer_id)
    async with Client(build_billing_server(ctx)) as c:
        d = await call(c, "dispute_bill", conversation_id=CONV, bill_id=acct.bill_id, reason="usage too high")
        again = await call(c, "dispute_bill", conversation_id=CONV, bill_id=acct.bill_id, reason="again")
        credit = await call(
            c,
            "apply_credit",
            conversation_id=CONV,
            account_id=acct.id,
            amount="15.00",
            reason_code="late_fee_waiver",
            bill_id=acct.bill_id,
        )
    assert d["ok"] and d["escalation_id"].startswith("ESC-")
    assert again["error"]["rule_id"] == "P-13" and credit["error"]["rule_id"] == "P-13"


async def test_meter_check_once(ctx):
    acct = one(ctx, WAIVABLE)
    verify(ctx, CONV, acct.customer_id)
    async with Client(build_billing_server(ctx)) as c:
        first = await call(c, "request_meter_check", conversation_id=CONV, account_id=acct.id)
        second = await call(c, "request_meter_check", conversation_id=CONV, account_id=acct.id)
    assert first["ok"] and second["error"]["rule_id"] == "P-22"


async def test_without_enforcement_the_action_runs_but_is_audited(sessions):
    ctx = make_ctx(sessions, enforce=False)
    acct = one(ctx, WAIVABLE)
    async with Client(build_billing_server(ctx)) as c:  # not even verified
        out = await call(
            c,
            "apply_credit",
            conversation_id=CONV,
            account_id=acct.id,
            amount="150.00",
            reason_code="goodwill",
        )
    assert out["ok"]
    with ctx.sessions() as s:
        bypassed = s.scalars(select(m.AuditLog.rule_id).where(m.AuditLog.outcome == "policy_bypassed")).all()
    assert {"P-01", "P-11"} <= set(bypassed)


async def test_bad_arguments_are_rejected_by_the_schema(ctx):
    async with Client(build_billing_server(ctx)) as c:
        r = await c.call_tool(
            "apply_credit",
            {"conversation_id": CONV, "account_id": "1", "amount": "5", "reason_code": "because"},
        )
    assert r.is_error
