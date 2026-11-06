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
from datetime import date, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from typing import Any

from faker import Faker
from sqlalchemy import Engine, text

from griddesk.config import get_settings
from griddesk.db import models as m

SEED = 20261015
N_CUSTOMERS = 520
SIM_NOW = get_settings().simulated_now  # one clock for the seed, the tools and the agent
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

# Tariff (matches section 6 "Rates" of docs/policy-handbook.md)
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
            if prof.disconnect_scheduled and not (prof.plan_active and prof.past_due):
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
            _bills_for_account(rng, out, account, addr, prof)

    for _ in range(40):  # vacant addresses, used by start-service and transfer tasks
        new_address()

    _outages(rng, out)
    from griddesk.tools.knowledge import handbook_chunks

    out["kb_chunks"] = handbook_chunks()
    return dict(out)


def _bills_for_account(
    rng: random.Random, out: dict[str, list[Row]], account: Row, addr: Row, prof: Profile
) -> None:
    aid = account["id"]
    meter_id = "M-" + addr["id"].split("-")[1]
    cycle_day = rng.randint(1, 28)
    base_kwh = rng.randint(350, 1100)
    last_end = account["closed_on"] or TODAY
    # The most recent period end on the cycle day whose bill (issued 2 days later) is already out.
    end = last_end.replace(day=cycle_day) if last_end.day >= cycle_day else _prev_month(last_end, cycle_day)
    while end + timedelta(days=2) > last_end:
        end = _prev_month(end, cycle_day)
    periods: list[tuple[date, date]] = []
    for _ in range(12):
        start = _prev_month(end, cycle_day)
        periods.append((start, end))
        end = start
    periods.reverse()

    n = len(periods)
    # A recent late-fee waiver cancelled the late fee on one of the older bills.
    waiver_index = rng.randint(n - 11, n - 3) if prof.recent_waiver else None
    bills: list[Row] = []
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
        # Past-due accounts left the previous bill unpaid, so the newest bill carries a late fee for it.
        unpaid = prof.past_due and i >= n - 2
        if (prof.past_due and latest) or i == waiver_index:
            lines.append(("late_fee", "Late payment fee (previous bill unpaid after due date)", LATE_FEE))
        amount = sum((ln[2] for ln in lines), Decimal("0"))
        if account["status"] == "closed":
            status = "paid"
        elif unpaid:
            status = "past_due" if due < TODAY else "open"
        elif latest and prof.disputed:
            status = "disputed"
        elif latest and due >= TODAY and not account["autopay"]:
            status = "open"
        else:
            status = "paid"
        bill = {
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
        bills.append(bill)
        out["bills"].append(bill)
        for kind, desc, amt in lines:
            out["bill_line_items"].append({"bill_id": bid, "kind": kind, "description": desc, "amount": amt})
        if i == waiver_index:
            out["credits"].append(
                {
                    "id": f"CR-{aid}-W",
                    "account_id": aid,
                    "amount": LATE_FEE,
                    "reason_code": "late_fee_waiver",
                    "note": f"Courtesy waiver of late fee on {bid}",
                    "applied_on": issued + timedelta(days=3),
                    "approval_request_id": None,
                    "bill_id": bid,
                    "outage_id": None,
                    "conversation_id": None,
                }
            )
        if status == "paid":
            paid_on = min(due - timedelta(days=rng.randint(0, 15)), last_end)
            paid_on = max(paid_on, issued)
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

    # A returned payment reopens the bill it paid.
    if prof.returned_payment and account["status"] != "closed":
        paid = [b for b in bills if b["status"] == "paid"]
        if paid:
            b = paid[-1]
            b["status"] = "past_due" if b["due_on"] < TODAY else "open"
            for pm in reversed(out["payments"]):
                if pm["id"] == f"PM-{aid}-{b['period_end']:%y%m}":
                    pm["status"] = "returned"
                    break

    balance = sum((b["amount"] for b in bills if b["status"] != "paid"), Decimal("0"))

    if prof.plan_defaulted:
        created = TODAY - timedelta(days=rng.randint(120, 330))
        total = money(rng.uniform(150, 600))
        out["payment_plans"].append(
            {
                "id": f"PP-{aid}-D",
                "account_id": aid,
                "total_amount": total,
                "installments": 6,
                "installment_amount": installment(total, 6),
                "start_on": created,
                "status": "defaulted",
                "created_on": created,
                "defaulted_on": created + timedelta(days=rng.randint(40, 90)),
            }
        )
    plan_active = prof.plan_active and balance > 0
    if plan_active:
        created = TODAY - timedelta(days=rng.randint(10, 60))
        out["payment_plans"].append(
            {
                "id": f"PP-{aid}-A",
                "account_id": aid,
                "total_amount": balance,
                "installments": 4,
                "installment_amount": installment(balance, 4),
                "start_on": created,
                "status": "active",
                "created_on": created,
                "defaulted_on": None,
            }
        )
    if prof.disconnect_scheduled and plan_active:
        account["status"] = "active"  # an active plan cancels a scheduled disconnect (P-16)
    elif prof.disconnect_scheduled:
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


def installment(total: Decimal, n: int) -> Decimal:
    """Monthly installment, rounded up to the cent; the last installment absorbs the remainder."""
    return (total / n).quantize(Decimal("0.01"), rounding=ROUND_CEILING)


def _prev_month(d: date, day: int) -> date:
    y, mo = (d.year, d.month - 1) if d.month > 1 else (d.year - 1, 12)
    return date(y, mo, day)


# Outage ids are stable; feeders are the busiest ones in this seed so every outage reaches customers.
OUTAGE_SPECS = [
    # id, status, cause, start (hours from now), restoration (hours from now)
    ("OUT-2610-01", "active", "Storm damage: tree on line", -3, 4),
    ("OUT-2610-02", "active", "Equipment failure: substation transformer", -1, None),
    ("OUT-2610-03", "active", "Planned maintenance", -2, 2),
    ("OUT-2610-04", "restored", "Vehicle hit utility pole", -26, -20),  # 6 h: no compensation
    ("OUT-2610-05", "restored", "Storm damage: multiple lines down", -80, -48),  # 32 h: qualifies (P-12)
]


def _outages(rng: random.Random, out: dict[str, list[Row]]) -> None:
    active_addr = {a["service_address_id"] for a in out["accounts"] if a["status"] != "closed"}
    feeder_counts: dict[str, int] = defaultdict(int)
    for a in out["service_addresses"]:
        if a["id"] in active_addr:
            feeder_counts[a["feeder"]] += 1
    busiest = sorted(feeder_counts, key=lambda f: (-feeder_counts[f], f))
    assert len(busiest) >= len(OUTAGE_SPECS), "need a distinct populated feeder per seeded outage"
    for (oid, status, cause, start_h, etr_h), feeder in zip(
        OUTAGE_SPECS, busiest[: len(OUTAGE_SPECS)], strict=True
    ):
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
    m.KbChunk,
]


LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "postgres"}


def load(engine: Engine, data: dict[str, list[Row]], force: bool = False) -> dict[str, int]:
    """Replace all rows with `data`. Refuses non-local databases unless `force` is set."""
    host = engine.url.host or "localhost"
    if host not in LOCAL_HOSTS and not force:
        raise RuntimeError(
            f"Refusing to reseed non-local database host {host!r}; pass force=True to override."
        )
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
