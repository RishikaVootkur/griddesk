from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

import pytest

from griddesk.policy import rules as r
from griddesk.policy.rules import Outcome

TODAY = date(2026, 10, 15)
NOW = datetime(2026, 10, 15, 15, tzinfo=UTC)


def credit(**kw):
    base = dict(amount=D("10"), reason_code="outage_compensation", credited_this_conversation=D("0"))
    base.update(kw)
    return r.CreditFacts(**base)


def test_access_requires_verification_and_ownership():
    assert r.check_account_access(None, "C-1").rule_id == "P-01"
    assert r.check_account_access("C-2", "C-1").rule_id == "P-02"
    assert r.check_account_access("C-1", "C-1").allowed


@pytest.mark.parametrize(("fails", "allowed"), [(0, True), (2, True), (3, False), (4, False)])
def test_lockout_after_three_failures(fails, allowed):
    assert r.check_verification_attempt(fails).allowed is allowed


def test_late_fee_waiver_rules():
    ok = credit(amount=D("15.00"), reason_code="late_fee_waiver", late_fee_on_bill=D("15.00"))
    assert r.check_credit(ok, TODAY).allowed
    assert r.check_credit(credit(amount=D("15"), reason_code="late_fee_waiver"), TODAY).rule_id == "P-08"
    wrong_amount = credit(amount=D("20"), reason_code="late_fee_waiver", late_fee_on_bill=D("15"))
    assert r.check_credit(wrong_amount, TODAY).rule_id == "P-08"
    recent = credit(
        amount=D("15"),
        reason_code="late_fee_waiver",
        late_fee_on_bill=D("15"),
        last_late_fee_waiver_on=TODAY - timedelta(days=300),
    )
    assert r.check_credit(recent, TODAY).rule_id == "P-08"
    old = credit(
        amount=D("15"),
        reason_code="late_fee_waiver",
        late_fee_on_bill=D("15"),
        last_late_fee_waiver_on=TODAY - timedelta(days=400),
    )
    assert r.check_credit(old, TODAY).allowed


def test_p09_threshold_is_strictly_over_50():
    # P-09 is a backstop: no auto-approvable reason exceeds $25 at current rates, so use a $50 late fee.
    at = credit(amount=D("50.00"), reason_code="late_fee_waiver", late_fee_on_bill=D("50.00"))
    assert r.check_credit(at, TODAY).allowed
    over = credit(amount=D("50.01"), reason_code="late_fee_waiver", late_fee_on_bill=D("50.01"))
    d = r.check_credit(over, TODAY)
    assert d.outcome is Outcome.NEEDS_APPROVAL and d.rule_id == "P-09"
    approved = credit(
        amount=D("50.01"), reason_code="late_fee_waiver", late_fee_on_bill=D("50.01"), approved=True
    )
    assert r.check_credit(approved, TODAY).allowed


def test_billing_error_and_goodwill_always_need_approval():
    assert (
        r.check_credit(credit(amount=D("5"), reason_code="goodwill"), TODAY).outcome is Outcome.NEEDS_APPROVAL
    )
    assert r.check_credit(credit(amount=D("5"), reason_code="billing_error"), TODAY).rule_id == "P-11"
    assert r.check_credit(credit(amount=D("5"), reason_code="goodwill", approved=True), TODAY).allowed


def test_no_credit_on_disputed_bill():
    f = credit(
        amount=D("15"), reason_code="late_fee_waiver", late_fee_on_bill=D("15"), bill_status="disputed"
    )
    assert r.check_credit(f, TODAY).rule_id == "P-13"
    f2 = credit(amount=D("20"), reason_code="billing_error", bill_status="disputed", approved=True)
    assert r.check_credit(f2, TODAY).rule_id == "P-13"


@pytest.mark.parametrize("amount", [D("NaN"), D("Infinity"), D("49.999"), D("-5")])
def test_malformed_amounts_are_denied(amount):
    assert r.check_credit(credit(amount=amount, reason_code="goodwill"), TODAY).outcome is Outcome.DENY


def test_credit_ceilings():
    assert r.check_credit(credit(amount=D("200.00"), reason_code="goodwill", approved=True), TODAY).allowed
    assert (
        r.check_credit(credit(amount=D("200.01"), reason_code="goodwill", approved=True), TODAY).rule_id
        == "P-10"
    )
    exactly = credit(
        amount=D("20"), reason_code="goodwill", credited_this_conversation=D("180"), approved=True
    )
    assert r.check_credit(exactly, TODAY).allowed
    over_total = credit(amount=D("25"), reason_code="goodwill", credited_this_conversation=D("180"))
    assert r.check_credit(over_total, TODAY).rule_id == "P-10"
    assert r.check_credit(credit(amount=D("0")), TODAY).rule_id == "P-11"
    assert r.check_credit(credit(reason_code="because"), TODAY).rule_id == "P-11"


def test_outage_compensation_is_tied_to_one_outage():
    q = ("O1", "O2")
    assert r.check_credit(credit(amount=D("25"), outage_id="O1", qualifying_outage_ids=q), TODAY).allowed
    assert r.check_credit(credit(amount=D("25"), qualifying_outage_ids=q), TODAY).rule_id == "P-12"
    assert (
        r.check_credit(credit(amount=D("25"), outage_id="O9", qualifying_outage_ids=q), TODAY).rule_id
        == "P-12"
    )
    done = credit(
        amount=D("25"), outage_id="O1", qualifying_outage_ids=q, outage_ids_already_compensated=("O1",)
    )
    assert r.check_credit(done, TODAY).rule_id == "P-12"
    other = credit(
        amount=D("25"), outage_id="O2", qualifying_outage_ids=q, outage_ids_already_compensated=("O1",)
    )
    assert r.check_credit(other, TODAY).allowed
    assert (
        r.check_credit(credit(amount=D("30"), outage_id="O1", qualifying_outage_ids=q), TODAY).rule_id
        == "P-12"
    )


def test_twelve_month_window_uses_calendar_months():
    assert r.months_before(date(2028, 2, 29), 12) == date(2027, 2, 28)
    assert r.months_before(date(2026, 3, 31), 1) == date(2026, 2, 28)
    # A waiver on 2027-03-01 still blocks on 2028-02-29 (a 365-day window would not).
    f = credit(
        amount=D("15"),
        reason_code="late_fee_waiver",
        late_fee_on_bill=D("15"),
        last_late_fee_waiver_on=date(2027, 3, 1),
    )
    assert r.check_credit(f, date(2028, 2, 29)).rule_id == "P-08"
    # Exactly 12 months ago is outside the window.
    g = credit(
        amount=D("15"),
        reason_code="late_fee_waiver",
        late_fee_on_bill=D("15"),
        last_late_fee_waiver_on=date(2025, 10, 15),
    )
    assert r.check_credit(g, TODAY).allowed


def test_qualifying_outage_window():
    start = NOW - timedelta(days=3)
    assert r.qualifying_outage(start, start + timedelta(hours=25), NOW)
    assert not r.qualifying_outage(start, start + timedelta(hours=24), NOW)  # must exceed 24h
    assert not r.qualifying_outage(start, None, NOW)
    old = NOW - timedelta(days=40)
    assert not r.qualifying_outage(old, old + timedelta(hours=30), NOW)


def test_dispute_window():
    assert r.check_dispute(TODAY - timedelta(days=90), "open", TODAY).allowed
    assert r.check_dispute(TODAY - timedelta(days=91), "open", TODAY).rule_id == "P-13"
    assert r.check_dispute(TODAY, "disputed", TODAY).rule_id == "P-13"


@pytest.mark.parametrize(
    ("balance", "n", "active", "default", "allowed"),
    [
        (D("100.00"), 4, False, None, False),  # must be over 100
        (D("100.01"), 4, False, None, True),
        (D("300"), 2, False, None, False),
        (D("300"), 3, False, None, True),
        (D("300"), 12, False, None, True),
        (D("300"), 13, False, None, False),
        (D("300"), 4, True, None, False),
        (D("300"), 4, False, TODAY - timedelta(days=200), False),
        (D("300"), 4, False, TODAY - timedelta(days=400), True),
    ],
)
def test_payment_plan_eligibility(balance, n, active, default, allowed):
    assert r.check_payment_plan(r.PlanFacts(balance, n, active, default), TODAY).allowed is allowed


def test_installment_rounds_up():
    assert r.installment_amount(D("100.01"), 3) == D("33.34")
    assert r.installment_amount(D("300.00"), 4) == D("75.00")


def test_schedule_window():
    assert r.check_schedule_date(TODAY, TODAY, "P-19").rule_id == "P-19"
    assert r.check_schedule_date(TODAY + timedelta(days=1), TODAY, "P-19").allowed
    assert r.check_schedule_date(TODAY + timedelta(days=60), TODAY, "P-19").allowed
    assert not r.check_schedule_date(TODAY + timedelta(days=61), TODAY, "P-19").allowed


def test_stop_needs_approval_and_no_duplicates():
    day5 = TODAY + timedelta(days=5)
    assert r.check_stop(r.StopFacts("active", day5), TODAY).outcome is Outcome.NEEDS_APPROVAL
    assert r.check_stop(r.StopFacts("active", day5, approved=True), TODAY).allowed
    assert r.check_stop(r.StopFacts("pending_stop", day5), TODAY).rule_id == "P-22"
    assert r.check_stop(r.StopFacts("active", day5, open_order_kinds=("transfer",)), TODAY).rule_id == "P-22"
    assert (
        r.check_stop(r.StopFacts("active", TODAY + timedelta(days=61), approved=True), TODAY).rule_id
        == "P-19"
    )


def test_one_open_order_per_kind():
    assert r.check_no_open_order("meter_check", ("meter_check",)).rule_id == "P-22"
    assert r.check_no_open_order("meter_check", ("stop",)).allowed


def test_start_and_transfer():
    good = r.StartFacts(False, TODAY + timedelta(days=3), False)
    assert r.check_start(good, TODAY).allowed
    assert r.check_start(r.StartFacts(True, TODAY + timedelta(days=3), False), TODAY).rule_id == "P-20"
    assert r.check_start(r.StartFacts(False, TODAY + timedelta(days=3), True), TODAY).rule_id == "P-20"
    stop = r.StopFacts("active", TODAY + timedelta(days=3))
    assert r.check_transfer(stop, good, TODAY).outcome is Outcome.NEEDS_APPROVAL
    occupied = r.StartFacts(True, TODAY + timedelta(days=3), False)
    assert r.check_transfer(stop, occupied, TODAY).rule_id == "P-21"
    approved_mismatch = r.StopFacts("active", TODAY + timedelta(days=78), approved=True)
    assert r.check_transfer(approved_mismatch, good, TODAY).rule_id == "P-21"
    far = TODAY + timedelta(days=78)
    assert (
        r.check_transfer(
            r.StopFacts("active", far, approved=True), r.StartFacts(False, far, False), TODAY
        ).outcome
        is Outcome.DENY
    )


def test_identity_matches():
    dob = date(1980, 5, 1)
    assert r.identity_matches("Nguyen", dob, "1234", " nguyen ", dob, None)
    assert r.identity_matches("Nguyen", dob, "1234", "Nguyen", None, "1234")
    assert not r.identity_matches("Nguyen", dob, "1234", "Nguyen", None, None)
    assert not r.identity_matches("Nguyen", dob, "1234", "Ngyuen", dob, "1234")
    assert not r.identity_matches("Nguyen", dob, "1234", "Nguyen", date(1980, 5, 2), "9999")


def test_medical_necessity_blocks_disconnect():
    assert r.check_disconnect(True).rule_id == "P-18"
    assert r.check_disconnect(False).allowed


@pytest.mark.parametrize(
    ("email", "phone", "allowed"),
    [
        ("a.b@example.com", None, True),
        ("not-an-email", None, False),
        (None, "617-555-0100", True),
        (None, "+1 (617) 555 0100", True),
        (None, "555-0100", False),
        (None, None, False),
        ("a@b.com\n", None, False),
        ("a@x..com", None, False),
        ("a@-x.com", None, False),
        (None, "\u0666\u0661\u0667\u0665\u0665\u0665\u0660\u0661\u0660\u0660", False),
    ],
)
def test_contact_update_validation(email, phone, allowed):
    assert r.check_contact_update(email, phone).allowed is allowed


@pytest.mark.parametrize(
    ("addr", "allowed"), [("12 Elm St, Northbrook MA 01901", True), ("x", False), ("12 Elm\nSt", False)]
)
def test_mailing_address_validation(addr, allowed):
    assert r.check_contact_update(None, None, addr).allowed is allowed


def test_autopay_only_off_and_outage_dedup():
    assert r.check_autopay_change(True).rule_id == "P-24"
    assert r.check_autopay_change(False).allowed
    assert r.check_outage_report(True).rule_id == "P-26"
