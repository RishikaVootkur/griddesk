"""Billing tools: balances, bills, charge explanations, credits, payment plans, disputes."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from griddesk.db import models as m
from griddesk.db.seed import CUSTOMER_CHARGE, DELIVERY_RATE, ENERGY_RATE, TAX_RATE
from griddesk.logging import redact_text
from griddesk.policy import rules
from griddesk.tools.common import (
    PolicyStop,
    ToolContext,
    ToolError,
    ToolOutput,
    audit,
    enforce,
    lock_approval,
    money,
    require_account,
)

# Output models


class PlanOut(BaseModel):
    plan_id: str
    total_amount: str
    installments: int
    installment_amount: str
    status: str
    start_on: str


class AccountSummary(ToolOutput):
    account_id: str | None = None
    status: str | None = None
    rate_plan: str | None = None
    balance: str | None = None
    past_due_amount: str | None = None
    autopay: bool | None = None
    paperless: bool | None = None
    service_address: str | None = None
    medical_necessity_on_file: bool | None = None
    active_payment_plan: PlanOut | None = None
    open_orders: list[str] = Field(default_factory=list)


class LineItemOut(BaseModel):
    kind: str
    description: str
    amount: str


class BillOut(BaseModel):
    bill_id: str
    period: str
    issued_on: str
    due_on: str
    kwh: int
    amount: str
    status: str
    estimated_reading: bool
    line_items: list[LineItemOut] = Field(default_factory=list)


class BillList(ToolOutput):
    bills: list[BillOut] = Field(default_factory=list)


class BillExplanation(ToolOutput):
    bill: BillOut | None = None
    rates: dict[str, str] = Field(default_factory=dict)
    previous_kwh: int | None = None
    usage_change_pct: float | None = None
    notes: list[str] = Field(default_factory=list)


class CreditOut(ToolOutput):
    credit_id: str | None = None
    amount: str | None = None
    new_balance: str | None = None


class PlanResult(ToolOutput):
    plan: PlanOut | None = None
    disconnect_cancelled: bool = False


class DisputeOut(ToolOutput):
    bill_id: str | None = None
    escalation_id: str | None = None


class OrderOut(ToolOutput):
    order_id: str | None = None
    kind: str | None = None
    scheduled_for: str | None = None


# Helpers


def _bill_out(b: m.Bill, with_lines: bool = True) -> BillOut:
    return BillOut(
        bill_id=b.id,
        period=f"{b.period_start} to {b.period_end}",
        issued_on=str(b.issued_on),
        due_on=str(b.due_on),
        kwh=b.kwh,
        amount=money(b.amount),
        status=b.status,
        estimated_reading=b.estimated,
        line_items=[
            LineItemOut(kind=li.kind, description=li.description, amount=money(li.amount))
            for li in b.line_items
        ]
        if with_lines
        else [],
    )


def _plan_out(p: m.PaymentPlan) -> PlanOut:
    return PlanOut(
        plan_id=p.id,
        total_amount=money(p.total_amount),
        installments=p.installments,
        installment_amount=money(p.installment_amount),
        status=p.status,
        start_on=str(p.start_on),
    )


def _active_plan(s: Session, account_id: str) -> m.PaymentPlan | None:
    return s.scalars(
        select(m.PaymentPlan).where(m.PaymentPlan.account_id == account_id, m.PaymentPlan.status == "active")
    ).first()


def open_order_kinds(s: Session, account_id: str) -> tuple[str, ...]:
    rows = s.scalars(
        select(m.ServiceOrder.kind)
        .where(m.ServiceOrder.account_id == account_id, m.ServiceOrder.status == "scheduled")
        .order_by(m.ServiceOrder.requested_on, m.ServiceOrder.id)
    )
    return tuple(rows)


def _load_bill(
    s: Session, ctx: ToolContext, conversation_id: str, bill_id: str, action: str, for_update: bool = False
) -> m.Bill:
    bill = s.get(m.Bill, bill_id.strip())
    # An unknown bill id goes through the same account check, so ids cannot be probed (P-05).
    require_account(s, ctx, conversation_id, bill.account_id if bill else "", action, for_update=for_update)
    assert bill is not None
    return bill


def parse_amount(raw: str | float) -> Decimal:
    try:
        return Decimal(str(raw).replace("$", "").strip())
    except InvalidOperation:
        raise PolicyStop(ToolError(code="invalid_request", message=f"'{raw}' is not an amount.")) from None


# Tools


def get_account_summary(ctx: ToolContext, conversation_id: str, account_id: str) -> AccountSummary:
    with ctx.sessions() as s:
        acct = require_account(s, ctx, conversation_id, account_id, "get_account_summary")
        past_due = s.scalar(
            select(func.coalesce(func.sum(m.Bill.amount), 0)).where(
                m.Bill.account_id == acct.id, m.Bill.status == "past_due"
            )
        )
        addr = acct.service_address
        plan = _active_plan(s, acct.id)
        orders = s.scalars(
            select(m.ServiceOrder)
            .where(m.ServiceOrder.account_id == acct.id, m.ServiceOrder.status == "scheduled")
            .order_by(m.ServiceOrder.requested_on, m.ServiceOrder.id)
        )
        audit(s, ctx, conversation_id, "get_account_summary", "ok", acct.id)
        s.commit()
        return AccountSummary(
            account_id=acct.id,
            status=acct.status,
            rate_plan=acct.rate_plan,
            balance=money(acct.balance),
            past_due_amount=money(Decimal(past_due or 0)),
            autopay=acct.autopay,
            paperless=acct.paperless,
            service_address=f"{addr.street}{', ' + addr.unit if addr.unit else ''}, {addr.city} {addr.zip}",
            medical_necessity_on_file=acct.customer.medical_necessity,
            active_payment_plan=_plan_out(plan) if plan else None,
            open_orders=[f"{o.kind} on {o.requested_on} ({o.id})" for o in orders],
        )


def list_bills(ctx: ToolContext, conversation_id: str, account_id: str, limit: int = 3) -> BillList:
    with ctx.sessions() as s:
        acct = require_account(s, ctx, conversation_id, account_id, "list_bills")
        bills = s.scalars(
            select(m.Bill)
            .where(m.Bill.account_id == acct.id)
            .order_by(m.Bill.period_end.desc())
            .limit(max(1, min(limit, 12)))
        )
        out = [_bill_out(b) for b in bills]
        audit(s, ctx, conversation_id, "list_bills", "ok", acct.id)
        s.commit()
        return BillList(bills=out)


def explain_bill(ctx: ToolContext, conversation_id: str, bill_id: str) -> BillExplanation:
    with ctx.sessions() as s:
        bill = _load_bill(s, ctx, conversation_id, bill_id, "explain_bill")
        acct = s.get(m.Account, bill.account_id)
        assert acct is not None
        prev = s.scalars(
            select(m.Bill)
            .where(m.Bill.account_id == bill.account_id, m.Bill.period_end < bill.period_end)
            .order_by(m.Bill.period_end.desc())
            .limit(1)
        ).first()
        notes = []
        if bill.estimated:
            notes.append("This bill used an ESTIMATED meter reading. Policy P-14: offer a free meter check.")
        if any(li.kind == "late_fee" for li in bill.line_items):
            notes.append(
                "Includes a $15.00 late fee because the previous bill was unpaid after its due date."
            )
        change = None
        if prev and prev.kwh:
            change = round(100 * (bill.kwh - prev.kwh) / prev.kwh, 1)
        audit(s, ctx, conversation_id, "explain_bill", "ok", bill.account_id)
        s.commit()
        return BillExplanation(
            bill=_bill_out(bill),
            rates={
                "energy_per_kwh": str(ENERGY_RATE[acct.rate_plan]),
                "delivery_per_kwh": str(DELIVERY_RATE),
                "customer_charge": money(CUSTOMER_CHARGE),
                "tax_rate": f"{TAX_RATE * 100:.0f}%",
                "rate_plan": acct.rate_plan,
            },
            previous_kwh=prev.kwh if prev else None,
            usage_change_pct=change,
            notes=notes,
        )


def apply_credit(
    ctx: ToolContext,
    conversation_id: str,
    account_id: str,
    amount: str | float,
    reason_code: str,
    bill_id: str | None = None,
    outage_id: str | None = None,
    note: str = "",
    approval_request_id: str | None = None,
) -> CreditOut:
    """Apply a credit. `approval_request_id` is accepted only from the approval executor."""
    amt = parse_amount(amount)
    bill_id = bill_id.strip() if bill_id else None
    outage_id = outage_id.strip() if outage_id else None
    note = redact_text(note)[:200]
    args = {
        "account_id": account_id,
        "amount": str(amt),
        "reason_code": reason_code,
        "bill_id": bill_id,
        "outage_id": outage_id,
        "note": note,
    }
    with ctx.sessions() as s:
        acct = require_account(s, ctx, conversation_id, account_id, "apply_credit", for_update=True)
        today = ctx.today()
        if reason_code in rules.REASONS_TIED_TO_BILL and not bill_id:
            raise PolicyStop(
                ToolError(
                    code="policy_denied",
                    rule_id="P-11",
                    message=f"A {reason_code} credit must name the bill it applies to.",
                )
            )
        bill = s.get(m.Bill, bill_id) if bill_id else None
        if bill_id and (bill is None or bill.account_id != acct.id):
            raise PolicyStop(ToolError(code="not_found", message=f"No bill {bill_id} on this account."))
        late_fee = None
        if bill is not None:
            late_fee = next((li.amount for li in bill.line_items if li.kind == "late_fee"), None)
        last_waiver = s.scalar(
            select(func.max(m.Credit.applied_on)).where(
                m.Credit.account_id == acct.id, m.Credit.reason_code == "late_fee_waiver"
            )
        )
        credited = s.scalar(
            select(func.coalesce(func.sum(m.Credit.amount), 0)).where(
                m.Credit.conversation_id == conversation_id
            )
        )
        qualifying, compensated = _outage_facts(s, acct, ctx)
        approval = (
            lock_approval(
                s,
                approval_request_id,
                action="apply_credit",
                conversation_id=conversation_id,
                account_id=acct.id,
                args=args,
            )
            if approval_request_id
            else None
        )
        approved = approval is not None
        facts = rules.CreditFacts(
            amount=amt,
            reason_code=reason_code,
            credited_this_conversation=Decimal(credited or 0),
            bill_status=bill.status if bill else None,
            last_late_fee_waiver_on=last_waiver,
            late_fee_on_bill=late_fee,
            outage_id=outage_id,
            qualifying_outage_ids=qualifying,
            outage_ids_already_compensated=compensated,
            approved=approved,
        )
        enforce(
            s,
            ctx,
            rules.check_credit(facts, today),
            conversation_id=conversation_id,
            action="apply_credit",
            account_id=acct.id,
            arguments=args,
        )
        credit = m.Credit(
            id=ctx.ids.new("CR"),
            account_id=acct.id,
            amount=amt,
            reason_code=reason_code,
            note=note[:240] or None,
            applied_on=today,
            bill_id=bill_id,
            outage_id=outage_id,
            conversation_id=conversation_id,
            approval_request_id=approval_request_id if approved else None,
        )
        acct.balance = acct.balance - amt
        if approval is not None:
            approval.status = "executed"
        s.add(credit)
        audit(
            s,
            ctx,
            conversation_id,
            "apply_credit",
            "ok",
            acct.id,
            details={"credit": credit.id, "amount": str(amt), "reason": reason_code},
        )
        s.commit()
        return CreditOut(credit_id=credit.id, amount=money(amt), new_balance=money(acct.balance))


def _outage_facts(s: Session, acct: m.Account, ctx: ToolContext) -> tuple[tuple[str, ...], tuple[str, ...]]:
    feeder = acct.service_address.feeder
    now = ctx.clock.now()
    outages = s.scalars(
        select(m.Outage).where(m.Outage.feeder == feeder, m.Outage.status == "restored").order_by(m.Outage.id)
    )
    # Only outages the account was open for (P-12 compensates this account's lost service).
    qualifying = tuple(
        o.id
        for o in outages
        if o.started_at.date() >= acct.opened_on and rules.qualifying_outage(o.started_at, o.restored_at, now)
    )
    paid = set(
        s.scalars(
            select(m.Credit.outage_id).where(
                m.Credit.account_id == acct.id, m.Credit.reason_code == "outage_compensation"
            )
        )
    )
    compensated = tuple(o for o in qualifying if o in paid)
    return qualifying, compensated


def create_payment_plan(
    ctx: ToolContext, conversation_id: str, account_id: str, installments: int
) -> PlanResult:
    with ctx.sessions() as s:
        acct = require_account(s, ctx, conversation_id, account_id, "create_payment_plan", for_update=True)
        today = ctx.today()
        last_default = s.scalar(
            select(func.max(m.PaymentPlan.defaulted_on)).where(
                m.PaymentPlan.account_id == acct.id, m.PaymentPlan.status == "defaulted"
            )
        )
        facts = rules.PlanFacts(
            balance=acct.balance,
            installments=installments,
            has_active_plan=_active_plan(s, acct.id) is not None,
            last_default_on=last_default,
        )
        enforce(
            s,
            ctx,
            rules.check_payment_plan(facts, today),
            conversation_id=conversation_id,
            action="create_payment_plan",
            account_id=acct.id,
            arguments={"account_id": account_id, "installments": installments},
        )
        n = max(1, installments)
        plan = m.PaymentPlan(
            id=ctx.ids.new("PP"),
            account_id=acct.id,
            total_amount=acct.balance,
            installments=n,
            installment_amount=rules.installment_amount(acct.balance, n),
            start_on=today + timedelta(days=1),
            status="active",
            created_on=today,
            defaulted_on=None,
        )
        s.add(plan)
        cancelled = False
        if acct.status == "disconnect_scheduled":  # P-16
            for o in s.scalars(
                select(m.ServiceOrder).where(
                    m.ServiceOrder.account_id == acct.id,
                    m.ServiceOrder.kind == "disconnect",
                    m.ServiceOrder.status == "scheduled",
                )
            ):
                o.status = "cancelled"
            acct.status = "active"
            cancelled = True
        audit(
            s,
            ctx,
            conversation_id,
            "create_payment_plan",
            "ok",
            acct.id,
            details={"plan": plan.id, "installments": n, "disconnect_cancelled": cancelled},
        )
        s.commit()
        return PlanResult(plan=_plan_out(plan), disconnect_cancelled=cancelled)


def dispute_bill(ctx: ToolContext, conversation_id: str, bill_id: str, reason: str) -> DisputeOut:
    with ctx.sessions() as s:
        bill = _load_bill(s, ctx, conversation_id, bill_id, "dispute_bill", for_update=True)
        enforce(
            s,
            ctx,
            rules.check_dispute(bill.issued_on, bill.status, ctx.today()),
            conversation_id=conversation_id,
            action="dispute_bill",
            account_id=bill.account_id,
            arguments={"bill_id": bill_id},
        )
        bill.status = "disputed"
        esc = m.Escalation(
            id=ctx.ids.new("ESC"),
            conversation_id=conversation_id,
            account_id=bill.account_id,
            category="billing_dispute",
            summary=f"Dispute of {bill.id}: {redact_text(reason)[:500]}",
            created_at=ctx.clock.now(),
            status="open",
        )
        s.add(esc)
        audit(s, ctx, conversation_id, "dispute_bill", "ok", bill.account_id, details={"bill": bill.id})
        s.commit()
        return DisputeOut(bill_id=bill.id, escalation_id=esc.id)


def request_meter_check(ctx: ToolContext, conversation_id: str, account_id: str) -> OrderOut:
    with ctx.sessions() as s:
        acct = require_account(s, ctx, conversation_id, account_id, "request_meter_check", for_update=True)
        enforce(
            s,
            ctx,
            rules.check_no_open_order("meter_check", open_order_kinds(s, acct.id)),
            conversation_id=conversation_id,
            action="request_meter_check",
            account_id=acct.id,
            arguments={"account_id": account_id},
        )
        when = ctx.today() + timedelta(days=3)
        order = m.ServiceOrder(
            id=ctx.ids.new("SO"),
            account_id=acct.id,
            kind="meter_check",
            requested_on=when,
            status="scheduled",
            new_service_address_id=None,
            created_at=ctx.clock.now(),
        )
        s.add(order)
        audit(s, ctx, conversation_id, "request_meter_check", "ok", acct.id, details={"order": order.id})
        s.commit()
        return OrderOut(order_id=order.id, kind="meter_check", scheduled_for=str(when))
