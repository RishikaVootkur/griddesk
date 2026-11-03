from collections import Counter
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from griddesk.db import models as m
from griddesk.db.seed import CUSTOMER_CHARGE, TODAY, bill_lines, generate


@pytest.fixture(scope="module")
def data():
    return generate()


def test_generation_is_deterministic(data):
    assert generate() == data


def test_volume(data):
    assert len(data["customers"]) >= 500
    assert len(data["accounts"]) > len(data["customers"])  # some customers hold two accounts


def test_edge_cases_are_present(data):
    acct_status = Counter(a["status"] for a in data["accounts"])
    bill_status = Counter(b["status"] for b in data["bills"])
    assert bill_status["past_due"] >= 30
    assert bill_status["disputed"] >= 5
    assert sum(r["reading_type"] == "estimated" for r in data["meter_readings"]) >= 20
    assert sum(c["medical_necessity"] for c in data["customers"]) >= 15
    assert any(p["status"] == "defaulted" for p in data["payment_plans"])
    assert any(c["reason_code"] == "late_fee_waiver" for c in data["credits"])
    assert acct_status["closed"] > 0 and acct_status["disconnect_scheduled"] > 0
    assert {o["status"] for o in data["outages"]} == {"active", "restored"}
    used = {a["service_address_id"] for a in data["accounts"]}
    assert sum(sa["id"] not in used for sa in data["service_addresses"]) >= 30  # vacant addresses


def test_contact_details_are_fictional(data):
    for c in data["customers"]:
        assert c["email"].split("@")[1] in {"example.com", "example.net", "example.org"}
        assert "555-01" in c["phone"]


def test_bill_amount_equals_line_items(data):
    totals: dict[str, Decimal] = {}
    for li in data["bill_line_items"]:
        totals[li["bill_id"]] = totals.get(li["bill_id"], Decimal(0)) + li["amount"]
    for b in data["bills"]:
        assert totals[b["id"]] == b["amount"]


def test_balance_equals_unpaid_bills(data):
    unpaid: dict[str, Decimal] = {}
    for b in data["bills"]:
        if b["status"] != "paid":
            unpaid[b["account_id"]] = unpaid.get(b["account_id"], Decimal(0)) + b["amount"]
    for a in data["accounts"]:
        assert a["balance"] == unpaid.get(a["id"], Decimal(0))


def test_medical_accounts_are_never_scheduled_for_disconnect(data):
    medical = {c["id"] for c in data["customers"] if c["medical_necessity"]}
    for a in data["accounts"]:
        if a["customer_id"] in medical:
            assert a["status"] != "disconnect_scheduled"


def test_bill_lines_hand_computed():
    # 500 kWh standard: energy 71.00, delivery 49.00, charge 11.50, tax 5% of 131.50 = 6.58
    lines = {k: v for k, _, v in bill_lines(500, "residential_standard")}
    assert lines == {
        "energy": Decimal("71.00"),
        "delivery": Decimal("49.00"),
        "customer_charge": CUSTOMER_CHARGE,
        "tax": Decimal("6.58"),
    }


def test_bills_are_issued_before_today_and_paid_after_issue(data):
    by_id = {b["id"]: b for b in data["bills"]}
    for b in data["bills"]:
        assert b["issued_on"] <= TODAY
    for p in data["payments"]:
        bill = by_id["B-" + p["id"][3:]]
        assert bill["issued_on"] <= p["paid_on"] <= TODAY


def test_late_fees_only_follow_an_unpaid_prior_bill_or_a_waiver(data):
    late = {li["bill_id"] for li in data["bill_line_items"] if li["kind"] == "late_fee"}
    waived = {c["note"].rsplit(" ", 1)[1] for c in data["credits"] if c["reason_code"] == "late_fee_waiver"}
    bills_by_acct: dict[str, list] = {}
    for b in data["bills"]:
        bills_by_acct.setdefault(b["account_id"], []).append(b)
    for bid in late - waived:
        acct_bills = bills_by_acct[by_acct_id(bid)]
        idx = [b["id"] for b in acct_bills].index(bid)
        assert acct_bills[idx - 1]["status"] == "past_due"
    assert waived <= late  # every waiver credit points at a bill that carried a late fee


def by_acct_id(bill_id: str) -> str:
    return bill_id.split("-")[1]


def test_returned_payments_reopen_their_bill(data):
    by_id = {b["id"]: b for b in data["bills"]}
    returned = [p for p in data["payments"] if p["status"] == "returned"]
    assert returned
    for p in returned:
        assert by_id["B-" + p["id"][3:]]["status"] != "paid"


def test_plan_installments_cover_the_total(data):
    for p in data["payment_plans"]:
        n, inst, total = p["installments"], p["installment_amount"], p["total_amount"]
        assert inst * (n - 1) < total <= inst * n


def test_no_account_has_both_active_plan_and_scheduled_disconnect(data):
    planned = {p["account_id"] for p in data["payment_plans"] if p["status"] == "active"}
    disconnects = {o["account_id"] for o in data["service_orders"] if o["kind"] == "disconnect"}
    assert not planned & disconnects
    status = {a["id"]: a["status"] for a in data["accounts"]}
    assert all(status[a] == "disconnect_scheduled" for a in disconnects)


def test_load_refuses_remote_hosts():
    from sqlalchemy import create_engine

    from griddesk.db.seed import load

    remote = create_engine("postgresql+psycopg://u:p@db.example.com/x")
    with pytest.raises(RuntimeError, match="non-local"):
        load(remote, {})


@pytest.mark.db
def test_trial_scope_rolls_back(engine):
    from griddesk.db.session import trial_scope

    with engine.connect() as c:
        acct_id, before = c.execute(select(m.Account.id, m.Account.paperless).limit(1)).one()
    with trial_scope(engine) as sessions:
        with sessions() as s:
            assert s.scalar(select(func.count()).select_from(m.Customer)) >= 500
            s.get(m.Account, acct_id).paperless = not before
            s.commit()
        with sessions() as s:
            assert s.get(m.Account, acct_id).paperless is (not before)
    with engine.connect() as c:
        after = c.execute(select(m.Account.paperless).where(m.Account.id == acct_id)).scalar_one()
    assert after is before
