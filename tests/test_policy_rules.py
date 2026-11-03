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


def test_credit_approval_threshold_is_strictly_over_50():
    q = ("OUT-1",)
    fifty = credit(amount=D("50.00"), reason_code="billing_error", approved=True)
    assert r.check_credit(fifty, TODAY).allowed
    # billing_error and goodwill always need approval, whatever the amount
    assert (
        r.check_credit(credit(amount=D("5"), reason_code="goodwill"), TODAY).outcome is Outcome.NEEDS_APPROVAL
    )
    assert r.check_credit(credit(amount=D("25"), qualifying_outage_ids=q), TODAY).allowed


def test_credit_over_50_needs_approval_then_allowed_when_approved():
    # Use a hypothetical reason path that only hits P-09: late fee of $60 (not in tariff, but tests the gate)
    f = credit(amount=D("60"), reason_code="late_fee_waiver", late_fee_on_bill=D("60"))
    d = r.check_credit(f, TODAY)
    assert d.outcome is Outcome.NEEDS_APPROVAL and d.rule_id == "P-09"
    f2 = credit(amount=D("60"), reason_code="late_fee_waiver", late_fee_on_bill=D("60"), approved=True)
    assert r.check_credit(f2, TODAY).allowed


def test_credit_ceilings():
    assert r.check_credit(credit(amount=D("200.01"), reason_code="goodwill"), TODAY).rule_id == "P-10"
    over_total = credit(amount=D("25"), reason_code="goodwill", credited_this_conversation=D("180"))
    assert r.check_credit(over_total, TODAY).rule_id == "P-10"
    assert r.check_credit(credit(amount=D("0")), TODAY).rule_id == "P-11"
    assert r.check_credit(credit(reason_code="because"), TODAY).rule_id == "P-11"


def test_outage_compensation():
    assert r.check_credit(credit(amount=D("25")), TODAY).rule_id == "P-12"  # no qualifying outage
    done = credit(amount=D("25"), qualifying_outage_ids=("O1",), outage_ids_already_compensated=("O1",))
    assert r.check_credit(done, TODAY).rule_id == "P-12"
    assert r.check_credit(credit(amount=D("30"), qualifying_outage_ids=("O1",)), TODAY).rule_id == "P-12"


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
    f = r.StopFacts("active", TODAY + timedelta(days=5), False)
    assert r.check_stop(f, TODAY).outcome is Outcome.NEEDS_APPROVAL
    assert r.check_stop(r.StopFacts("active", TODAY + timedelta(days=5), False, approved=True), TODAY).allowed
    assert (
        r.check_stop(r.StopFacts("pending_stop", TODAY + timedelta(days=5), False), TODAY).rule_id == "P-22"
    )


def test_start_and_transfer():
    good = r.StartFacts(False, TODAY + timedelta(days=3), False)
    assert r.check_start(good, TODAY).allowed
    assert r.check_start(r.StartFacts(True, TODAY + timedelta(days=3), False), TODAY).rule_id == "P-20"
    assert r.check_start(r.StartFacts(False, TODAY + timedelta(days=3), True), TODAY).rule_id == "P-20"
    stop = r.StopFacts("active", TODAY + timedelta(days=3), False)
    assert r.check_transfer(stop, good, TODAY).outcome is Outcome.NEEDS_APPROVAL
    occupied = r.StartFacts(True, TODAY + timedelta(days=3), False)
    assert r.check_transfer(stop, occupied, TODAY).rule_id == "P-21"


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
    ],
)
def test_contact_update_validation(email, phone, allowed):
    assert r.check_contact_update(email, phone).allowed is allowed


def test_autopay_only_off_and_outage_dedup():
    assert r.check_autopay_change(True).rule_id == "P-24"
    assert r.check_autopay_change(False).allowed
    assert r.check_outage_report(True).rule_id == "P-26"
