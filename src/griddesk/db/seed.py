"""Deterministic synthetic data for Northbrook Power & Light (a fictional utility).

`generate(seed)` is pure: it returns rows per table and never touches a database, so tests can
check coverage of edge cases cheaply. `load()` writes the rows. Every person, address, email and
phone number here is generated. Emails use reserved example domains and phones use the 555-01xx
fictional range, so no record can reach a real person.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from faker import Faker
from sqlalchemy import Engine, text

from griddesk.db import models as m

SEED = 20261015
N_CUSTOMERS = 520
SIM_NOW = datetime(2026, 10, 15, 15, 0, tzinfo=UTC)
TODAY = SIM_NOW.date()

CITIES = [
    ("Northbrook", "01901"),
    ("Alder Falls", "01902"),
    ("Cedar Junction", "01903"),
    ("Millbrook", "01904"),
    ("Riverton", "01905"),
    ("Fairhaven Heights", "01906"),
]
FEEDERS = [f"F{n:02d}" for n in range(1, 25)]

# Tariff (matches docs/policy-handbook.md section "Rates")
ENERGY_RATE = {
    "residential_standard": Decimal("0.1420"),
    "time_of_use": Decimal("0.1310"),
    "income_assist": Decimal("0.1065"),
}
DELIVERY_RATE = Decimal("0.0980")
CUSTOMER_CHARGE = Decimal("11.50")
TAX_RATE = Decimal("0.05")
LATE_FEE = Decimal("15.00")

Row = dict[str, Any]


def money(x: Decimal | float) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def bill_lines(kwh: int, rate_plan: str) -> list[tuple[str, str, Decimal]]:
    """Line items for a bill before any late fee. Shared with the billing tools' explanations."""
    energy = money(kwh * ENERGY_RATE[rate_plan])
    delivery = money(kwh * DELIVERY_RATE)
    subtotal = energy + delivery + CUSTOMER_CHARGE
    tax = money(subtotal * TAX_RATE)
    return [
        ("energy", f"Energy {kwh} kWh at ${ENERGY_RATE[rate_plan]}/kWh", energy),
        ("delivery", f"Delivery {kwh} kWh at ${DELIVERY_RATE}/kWh", delivery),
        ("customer_charge", "Monthly customer charge", CUSTOMER_CHARGE),
        ("tax", "State utility tax 5%", tax),
    ]


@dataclass
class Profile:
    """Edge-case flags drawn for one account."""

    past_due: bool = False
    disputed: bool = False
    estimated: bool = False
    plan_defaulted: bool = False
    plan_active: bool = False
    recent_waiver: bool = False
    disconnect_scheduled: bool = False
    returned_payment: bool = False


def _email(rng: random.Random, first: str, last: str) -> str:
    domain = rng.choice(["com", "net", "org"])
    return f"{first.lower()}.{last.lower()}{rng.randint(1, 99)}@example.{domain}"


def _phone(rng: random.Random) -> str:
    return f"({rng.choice(['617', '508', '781', '978'])}) 555-01{rng.randint(0, 99):02d}"


def generate(seed: int = SEED, n_customers: int = N_CUSTOMERS) -> dict[str, list[Row]]:
    rng = random.Random(seed)
    fake = Faker("en_US")
    fake.seed_instance(seed)
    out: dict[str, list[Row]] = defaultdict(list)

    street_names = [fake.unique.street_name() for _ in range(90)]
    street_feeder = {s: rng.choice(FEEDERS) for s in street_names}
    street_city = {s: rng.choice(CITIES) for s in street_names}

    addr_n = 0

    def new_address() -> Row:
        nonlocal addr_n
        addr_n += 1
        street = rng.choice(street_names)
        city, zip_code = street_city[street]
        row: Row = {
            "id": f"SA-{addr_n:05d}",
            "street": f"{rng.randint(2, 980)} {street}",
            "unit": f"Apt {rng.randint(1, 12)}" if rng.random() < 0.25 else None,
            "city": city,
            "state": "MA",
            "zip": zip_code,
            "feeder": street_feeder[street],
        }
        out["service_addresses"].append(row)
        out["meters"].append(
            {
                "id": f"M-{addr_n:05d}",
                "service_address_id": row["id"],
                "kind": "smart" if rng.random() < 0.85 else "analog",
                "installed_on": date(2015, 1, 1) + timedelta(days=rng.randint(0, 3000)),
            }
        )
        return row

    acct_n = 100_000
    for ci in range(1, n_customers + 1):
        first, last = fake.first_name(), fake.last_name()
        cid = f"C-{ci:05d}"
        dob = date(1940, 1, 1) + timedelta(days=rng.randint(0, 22_000))
        medical = rng.random() < 0.06
        out["customers"].append(
            {
                "id": cid,
                "first_name": first,
                "last_name": last,
                "email": _email(rng, first, last),
                "phone": _phone(rng),
                "date_of_birth": dob,
                "ssn_last4": f"{rng.randint(0, 9999):04d}",
                "medical_necessity": medical,
                "preferred_contact": rng.choice(["email", "email", "sms", "mail"]),
                "mailing_address": None,
                "created_at": SIM_NOW - timedelta(days=rng.randint(400, 6000)),
            }
        )

        n_accounts = 2 if rng.random() < 0.06 else 1
        for ai in range(n_accounts):
            acct_n += rng.randint(1, 9)
            aid = f"{acct_n}"
            addr = new_address()
            closed = (ai == 1 and rng.random() < 0.5) or rng.random() < 0.04
            status = "closed" if closed else ("pending_stop" if rng.random() < 0.02 else "active")
            rate_plan = rng.choices(list(ENERGY_RATE), weights=[75, 15, 10])[0]
            opened = TODAY - timedelta(days=rng.randint(420, 4000))
            prof = Profile()
            if status != "closed":
                r = rng.random()
                prof.past_due = r < 0.14
                prof.disputed = 0.14 <= r < 0.18
                prof.estimated = rng.random() < 0.09
                prof.plan_defaulted = rng.random() < 0.05
                prof.plan_active = not prof.plan_defaulted and prof.past_due and rng.random() < 0.3
                prof.recent_waiver = rng.random() < 0.06
                prof.disconnect_scheduled = prof.past_due and not medical and rng.random() < 0.15
                prof.returned_payment = rng.random() < 0.03
            if prof.disconnect_scheduled:
                status = "disconnect_scheduled"
            account: Row = {
                "id": aid,
                "customer_id": cid,
                "service_address_id": addr["id"],
                "status": status,
                "rate_plan": rate_plan,
                "balance": Decimal("0.00"),
                "autopay": (not prof.past_due) and rng.random() < 0.35,
                "paperless": rng.random() < 0.5,
                "opened_on": opened,
                "closed_on": TODAY - timedelta(days=rng.randint(30, 300)) if status == "closed" else None,
            }
            out["accounts"].append(account)
            _bills_for_account(rng, out, account, addr, prof, medical)

    for _ in range(40):  # vacant addresses, used by start-service and transfer tasks
        new_address()

    _outages(rng, out)
    return dict(out)


def _bills_for_account(
    rng: random.Random, out: dict[str, list[Row]], account: Row, addr: Row, prof: Profile, medical: bool
) -> None:
    aid = account["id"]
    meter_id = "M-" + addr["id"].split("-")[1]
    cycle_day = rng.randint(1, 28)
    base_kwh = rng.randint(350, 1100)
    last_end = account["closed_on"] or TODAY
    # Find the most recent period end on the cycle day.
    end = last_end.replace(day=cycle_day) if last_end.day >= cycle_day else _prev_month(last_end, cycle_day)
    periods: list[tuple[date, date]] = []
    for _ in range(12):
        start = _prev_month(end, cycle_day)
        periods.append((start, end))
        end = start
    periods.reverse()

    balance = Decimal("0")
    n = len(periods)
    for i, (start, end) in enumerate(periods):
        month_factor = 1.0 + 0.35 * abs(6 - end.month) / 6  # winter and summer peaks
        kwh = int(base_kwh * month_factor * rng.uniform(0.85, 1.15))
        latest = i == n - 1
        estimated = latest and prof.estimated
        out["meter_readings"].append(
            {
                "meter_id": meter_id,
                "read_on": end,
                "kwh": kwh,
                "reading_type": "estimated" if estimated else "actual",
            }
        )
        issued = end + timedelta(days=2)
        due = issued + timedelta(days=21)
        bid = f"B-{aid}-{end:%y%m}"
        lines = bill_lines(kwh, account["rate_plan"])
        # Past-due accounts carry the last two bills unpaid, with a late fee on the newest.
        unpaid_past_due = prof.past_due and i >= n - 2
        if unpaid_past_due and latest:
            lines.append(("late_fee", "Late payment fee", LATE_FEE))
        amount = sum((ln[2] for ln in lines), Decimal("0"))
        if account["status"] == "closed":
            status = "paid"
        elif unpaid_past_due:
            status = "past_due" if due < TODAY or not latest else "open"
            if latest:
                status = "past_due"
        elif latest and prof.disputed:
            status = "disputed"
        elif latest and due >= TODAY and not account["autopay"]:
            status = "open"
        else:
            status = "paid"
        out["bills"].append(
            {
                "id": bid,
                "account_id": aid,
                "period_start": start,
                "period_end": end,
                "issued_on": issued,
                "due_on": due,
                "kwh": kwh,
                "amount": amount,
                "estimated": estimated,
                "status": status,
            }
        )
        for kind, desc, amt in lines:
            out["bill_line_items"].append({"bill_id": bid, "kind": kind, "description": desc, "amount": amt})
        if status == "paid":
            paid_on = min(due - timedelta(days=rng.randint(0, 15)), TODAY)
            method = "autopay" if account["autopay"] else rng.choice(["card", "bank", "bank", "cash"])
            out["payments"].append(
                {
                    "id": f"PM-{aid}-{end:%y%m}",
                    "account_id": aid,
                    "amount": amount,
                    "paid_on": paid_on,
                    "method": method,
                    "status": "posted",
                }
            )
        else:
            balance += amount

    if prof.returned_payment and out["payments"] and out["payments"][-1]["account_id"] == aid:
        out["payments"][-1]["status"] = "returned"

    if prof.plan_defaulted:
        created = TODAY - timedelta(days=rng.randint(120, 330))
        out["payment_plans"].append(
            {
                "id": f"PP-{aid}-D",
                "account_id": aid,
                "total_amount": money(rng.uniform(150, 600)),
                "installments": 6,
                "installment_amount": money(rng.uniform(25, 100)),
                "start_on": created,
                "status": "defaulted",
                "created_on": created,
                "defaulted_on": created + timedelta(days=rng.randint(40, 90)),
            }
        )
    if prof.plan_active and balance > 0:
        created = TODAY - timedelta(days=rng.randint(10, 60))
        out["payment_plans"].append(
            {
                "id": f"PP-{aid}-A",
                "account_id": aid,
                "total_amount": balance,
                "installments": 4,
                "installment_amount": money(balance / 4),
                "start_on": created,
                "status": "active",
                "created_on": created,
                "defaulted_on": None,
            }
        )
    if prof.recent_waiver:
        out["credits"].append(
            {
                "id": f"CR-{aid}-W",
                "account_id": aid,
                "amount": LATE_FEE,
                "reason_code": "late_fee_waiver",
                "note": "Courtesy waiver",
                "applied_on": TODAY - timedelta(days=rng.randint(20, 340)),
                "approval_request_id": None,
            }
        )
    if prof.disconnect_scheduled:
        out["service_orders"].append(
            {
                "id": f"SO-{aid}-X",
                "account_id": aid,
                "kind": "disconnect",
                "requested_on": TODAY + timedelta(days=rng.randint(3, 10)),
                "status": "scheduled",
                "new_service_address_id": None,
                "created_at": SIM_NOW - timedelta(days=5),
            }
        )
    if account["status"] == "pending_stop":
        out["service_orders"].append(
            {
                "id": f"SO-{aid}-S",
                "account_id": aid,
                "kind": "stop",
                "requested_on": TODAY + timedelta(days=rng.randint(2, 20)),
                "status": "scheduled",
                "new_service_address_id": None,
                "created_at": SIM_NOW - timedelta(days=3),
            }
        )
    account["balance"] = balance
    _ = medical


def _prev_month(d: date, day: int) -> date:
    y, mo = (d.year, d.month - 1) if d.month > 1 else (d.year - 1, 12)
    return date(y, mo, day)


def _outages(rng: random.Random, out: dict[str, list[Row]]) -> None:
    feeder_counts: dict[str, int] = defaultdict(int)
    for a in out["service_addresses"]:
        feeder_counts[a["feeder"]] += 1
    specs = [
        ("OUT-2610-07", "F07", "active", "Storm damage: tree on line", -3, 4),
        ("OUT-2610-15", "F15", "active", "Equipment failure: substation transformer", -1, None),
        ("OUT-2610-19", "F19", "active", "Planned maintenance", -2, 2),
        ("OUT-2610-03", "F03", "restored", "Vehicle hit utility pole", -26, -20),
        ("OUT-2610-11", "F11", "restored", "Storm damage: multiple lines down", -80, -48),
    ]
    for oid, feeder, status, cause, start_h, etr_h in specs:
        started = SIM_NOW + timedelta(hours=start_h)
        etr = SIM_NOW + timedelta(hours=etr_h) if etr_h is not None else None
        out["outages"].append(
            {
                "id": oid,
                "feeder": feeder,
                "status": status,
                "cause": cause,
                "started_at": started,
                "estimated_restoration": etr if status == "active" else None,
                "restored_at": etr if status == "restored" else None,
                "customers_affected": feeder_counts[feeder] * 37 + rng.randint(0, 30),
            }
        )


TABLE_ORDER = [
    m.Customer,
    m.ServiceAddress,
    m.Account,
    m.Meter,
    m.MeterReading,
    m.Bill,
    m.BillLineItem,
    m.Payment,
    m.PaymentPlan,
    m.Credit,
    m.Outage,
    m.OutageReport,
    m.ServiceOrder,
    m.IdentityVerification,
    m.ApprovalRequest,
    m.Escalation,
    m.AuditLog,
]


def load(engine: Engine, data: dict[str, list[Row]]) -> dict[str, int]:
    """Replace all rows with `data`. Only for the local dev and eval database."""
    names = ", ".join(t.__tablename__ for t in TABLE_ORDER)
    counts: dict[str, int] = {}
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
        for model in TABLE_ORDER:
            rows = data.get(model.__tablename__, [])
            if rows:
                conn.execute(model.__table__.insert(), rows)  # type: ignore[attr-defined]
            counts[model.__tablename__] = len(rows)
    return counts
