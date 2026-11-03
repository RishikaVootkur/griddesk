from collections import Counter
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from griddesk.db import models as m
from griddesk.db.seed import CUSTOMER_CHARGE, bill_lines, generate


@pytest.fixture(scope="module")
def data():
    return generate()


def test_generation_is_deterministic(data):
    again = generate()
    assert again["customers"] == data["customers"]
    assert again["bills"][:50] == data["bills"][:50]


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


@pytest.mark.db
def test_loaded_counts_and_trial_rollback(engine, sessions):
    with sessions() as s:
        assert s.scalar(select(func.count()).select_from(m.Customer)) >= 500
        acct = s.scalars(select(m.Account).limit(1)).one()
        acct.paperless = not acct.paperless
        s.commit()
        flipped = acct.paperless
    with sessions() as s:
        assert s.get(m.Account, acct.id).paperless == flipped
    # After the trial scope (fixture teardown) the change is gone; check with a fresh connection.
    with engine.connect() as c:
        val = c.execute(select(m.Account.paperless).where(m.Account.id == acct.id)).scalar_one()
    assert val != flipped
