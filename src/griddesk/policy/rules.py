"""Enforceable rules from docs/policy-handbook.md as pure functions.

Each check takes plain facts (gathered by the tool from the database) and returns a `Decision`.
Being pure, the same checks serve three callers: the MCP tools (in-tool enforcement), the
pre-action guardrail (validates a proposed tool call before it runs), and the eval harness
(scores policy violations). Tests exercise each rule at its boundaries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from enum import StrEnum

CREDIT_AUTO_LIMIT = Decimal("50.00")
CREDIT_MAX_SINGLE = Decimal("200.00")
CREDIT_MAX_CONVERSATION = Decimal("200.00")
OUTAGE_COMP_AMOUNT = Decimal("25.00")
OUTAGE_COMP_MIN_HOURS = 24
OUTAGE_COMP_WINDOW_DAYS = 30
PLAN_MIN_BALANCE = Decimal("100.00")
PLAN_MIN_INSTALLMENTS = 3
PLAN_MAX_INSTALLMENTS = 12
DISPUTE_WINDOW_DAYS = 90
SCHEDULE_MIN_DAYS = 1
SCHEDULE_MAX_DAYS = 60
MAX_VERIFY_ATTEMPTS = 3

CREDIT_REASONS = {"late_fee_waiver", "outage_compensation", "billing_error", "goodwill"}
REASONS_ALWAYS_APPROVAL = {"billing_error", "goodwill"}


class Outcome(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    NEEDS_APPROVAL = "needs_approval"


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    rule_id: str | None = None
    message: str = ""

    @property
    def allowed(self) -> bool:
        return self.outcome is Outcome.ALLOW


ALLOW = Decision(Outcome.ALLOW)


def deny(rule_id: str, message: str) -> Decision:
    return Decision(Outcome.DENY, rule_id, message)


def needs_approval(rule_id: str, message: str) -> Decision:
    return Decision(Outcome.NEEDS_APPROVAL, rule_id, message)


def _months_ago(today: date, months: int) -> date:
    return today - timedelta(days=round(months * 365 / 12))


# Identity (P-01, P-02, P-03)


def check_account_access(verified_customer_id: str | None, account_owner_id: str) -> Decision:
    if verified_customer_id is None:
        return deny("P-01", "Identity not verified in this conversation. Verify the account holder first.")
    if verified_customer_id != account_owner_id:
        return deny("P-02", "This account does not belong to the verified customer.")
    return ALLOW


def check_verification_attempt(failed_attempts: int) -> Decision:
    if failed_attempts >= MAX_VERIFY_ATTEMPTS:
        return deny("P-03", "Too many failed verification attempts. Escalate with category identity_lockout.")
    return ALLOW


# Credits (P-08 to P-12)


@dataclass(frozen=True)
class CreditFacts:
    amount: Decimal
    reason_code: str
    credited_this_conversation: Decimal
    last_late_fee_waiver_on: date | None = None
    late_fee_on_bill: Decimal | None = None  # the late fee on the bill being waived, if any
    qualifying_outage_ids: tuple[str, ...] = ()  # outages > 24h restored in last 30 days at this address
    outage_ids_already_compensated: tuple[str, ...] = ()
    approved: bool = False  # true when executing a human-approved request


def check_credit(f: CreditFacts, today: date) -> Decision:
    if f.amount <= 0:
        return deny("P-11", "Credit amount must be positive.")
    if f.reason_code not in CREDIT_REASONS:
        return deny("P-11", f"'{f.reason_code}' is not a qualifying credit reason.")
    if f.amount > CREDIT_MAX_SINGLE:
        return deny("P-10", "A single credit cannot exceed $200.00. Open a billing dispute instead.")
    if f.credited_this_conversation + f.amount > CREDIT_MAX_CONVERSATION:
        return deny("P-10", "Credits in one conversation cannot total more than $200.00.")
    if f.reason_code == "late_fee_waiver":
        if f.late_fee_on_bill is None:
            return deny("P-08", "That bill has no late fee to waive.")
        if f.amount != f.late_fee_on_bill:
            return deny("P-08", f"A late-fee waiver must equal the late fee (${f.late_fee_on_bill}).")
        if f.last_late_fee_waiver_on and f.last_late_fee_waiver_on > _months_ago(today, 12):
            return deny(
                "P-08", f"A late fee was already waived on {f.last_late_fee_waiver_on}; once per 12 months."
            )
    if f.reason_code == "outage_compensation":
        open_ids = [o for o in f.qualifying_outage_ids if o not in f.outage_ids_already_compensated]
        if not f.qualifying_outage_ids:
            return deny("P-12", "No outage over 24 hours was restored at this address in the last 30 days.")
        if not open_ids:
            return deny("P-12", "This outage was already compensated.")
        if f.amount != OUTAGE_COMP_AMOUNT:
            return deny("P-12", "Outage compensation is exactly $25.00 per qualifying outage.")
    if f.approved:
        return ALLOW
    if f.reason_code in REASONS_ALWAYS_APPROVAL:
        return needs_approval("P-11", f"Credits with reason {f.reason_code} need human approval.")
    if f.amount > CREDIT_AUTO_LIMIT:
        return needs_approval("P-09", "Credits over $50.00 need human approval.")
    return ALLOW


def qualifying_outage(started_at: datetime, restored_at: datetime | None, now: datetime) -> bool:
    if restored_at is None:
        return False
    long_enough = restored_at - started_at > timedelta(hours=OUTAGE_COMP_MIN_HOURS)
    recent = now - restored_at <= timedelta(days=OUTAGE_COMP_WINDOW_DAYS)
    return long_enough and recent


# Disputes and payment plans (P-13, P-15)


def check_dispute(bill_issued_on: date, bill_status: str, today: date) -> Decision:
    if bill_status == "disputed":
        return deny("P-13", "This bill is already under dispute.")
    if (today - bill_issued_on).days > DISPUTE_WINDOW_DAYS:
        return deny("P-13", "Bills issued more than 90 days ago cannot be disputed through this channel.")
    return ALLOW


@dataclass(frozen=True)
class PlanFacts:
    balance: Decimal
    installments: int
    has_active_plan: bool
    last_default_on: date | None


def check_payment_plan(f: PlanFacts, today: date) -> Decision:
    if f.balance <= PLAN_MIN_BALANCE:
        return deny("P-15", "Payment plans need a balance over $100.00.")
    if f.has_active_plan:
        return deny("P-15", "The account already has an active payment plan.")
    if f.last_default_on and f.last_default_on > _months_ago(today, 12):
        return deny("P-15", f"A payment plan defaulted on {f.last_default_on}; not eligible for 12 months.")
    if not PLAN_MIN_INSTALLMENTS <= f.installments <= PLAN_MAX_INSTALLMENTS:
        return deny("P-15", "Payment plans have 3 to 12 monthly installments.")
    return ALLOW


def installment_amount(balance: Decimal, installments: int) -> Decimal:
    return (balance / installments).quantize(Decimal("0.01"), rounding=ROUND_CEILING)


# Service changes (P-18 to P-22)


def check_schedule_date(requested: date, today: date, rule_id: str) -> Decision:
    delta = (requested - today).days
    if delta < SCHEDULE_MIN_DAYS:
        return deny(rule_id, "The date must be at least one day from today.")
    if delta > SCHEDULE_MAX_DAYS:
        return deny(rule_id, "The date must be within 60 days of today.")
    return ALLOW


def check_disconnect(medical_necessity: bool) -> Decision:
    if medical_necessity:
        return deny("P-18", "Medical-necessity accounts can never be disconnected.")
    return ALLOW


@dataclass(frozen=True)
class StopFacts:
    account_status: str
    requested_on: date
    has_open_stop_order: bool
    approved: bool = False


def check_stop(f: StopFacts, today: date) -> Decision:
    if f.account_status in {"closed", "pending_stop"} or f.has_open_stop_order:
        return deny("P-22", "A stop is already scheduled or the account is closed.")
    d = check_schedule_date(f.requested_on, today, "P-19")
    if not d.allowed:
        return d
    if not f.approved:
        return needs_approval("P-19", "Stopping service needs human approval.")
    return ALLOW


@dataclass(frozen=True)
class StartFacts:
    address_has_active_account: bool
    requested_on: date
    customer_past_due_unplanned: bool  # past-due balance on any account not covered by an active plan


def check_start(f: StartFacts, today: date, rule_id: str = "P-20") -> Decision:
    if f.address_has_active_account:
        return deny(rule_id, "That address already has active service.")
    if f.customer_past_due_unplanned:
        return deny(
            "P-20", "A past-due balance must be paid or on a payment plan before starting new service."
        )
    return check_schedule_date(f.requested_on, today, rule_id)


def check_transfer(stop: StopFacts, start: StartFacts, today: date) -> Decision:
    s = check_start(start, today, rule_id="P-21")
    if not s.allowed:
        return s
    if stop.account_status in {"closed", "pending_stop"} or stop.has_open_stop_order:
        return deny("P-22", "A stop is already scheduled or the account is closed.")
    if not stop.approved:
        return needs_approval("P-21", "Transfers need human approval.")
    return ALLOW


# Account changes (P-23, P-24)

_EMAIL = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def normalize_phone(phone: str) -> str | None:
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return None
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"


def check_contact_update(email: str | None, phone: str | None) -> Decision:
    if email is None and phone is None:
        return deny("P-23", "Nothing to update.")
    if email is not None and not _EMAIL.match(email):
        return deny("P-23", "That email address is not valid.")
    if phone is not None and normalize_phone(phone) is None:
        return deny("P-23", "Phone numbers must have ten digits.")
    return ALLOW


def check_autopay_change(enable: bool) -> Decision:
    if enable:
        return deny(
            "P-24", "Autopay enrollment happens in the customer portal; the agent can only turn it off."
        )
    return ALLOW


# Outages (P-26)


def check_outage_report(already_reported: bool) -> Decision:
    if already_reported:
        return deny("P-26", "This account already reported the current outage.")
    return ALLOW
