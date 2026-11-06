"""Shared helpers for the outage, account, mixed and adversarial task templates.

`people` is `taskgen.fixtures` with the service address joined in, restricted to addresses a customer
can say out loud and the address matcher finds (no unit, unique street).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from griddesk.eval.taskgen import Fixture, facts

UNIQUE_STREET = (
    "sa.unit is null and (select count(*) from service_addresses x where x.street = sa.street) = 1"
)
NO_ORDERS = "not exists (select 1 from service_orders o where o.account_id = a.id)"
NO_PAST_DUE = """not exists (select 1 from accounts a2 join bills b on b.account_id = a2.id
    where a2.customer_id = c.id and b.status = 'past_due')"""
SINGLE_ACCOUNT = "(select count(*) from accounts a3 where a3.customer_id = c.id) = 1"
NOT_MEDICAL = "not c.medical_necessity"


def people(
    s: Session,
    where: str,
    n: int = 1,
    order: str = "a.id",
    params: dict[str, Any] | None = None,
    skip: int = 0,
) -> list[Fixture]:
    sql = f"""
        select a.id as account_id, c.id as customer_id, c.first_name, c.last_name, c.date_of_birth,
               c.ssn_last4, c.email, c.phone, c.medical_necessity, a.status, a.balance, a.paperless,
               a.autopay, sa.id as address_id, sa.street, sa.city, sa.zip, sa.feeder
        from accounts a join customers c on c.id = a.customer_id
             join service_addresses sa on sa.id = a.service_address_id
        where {UNIQUE_STREET} and {where}
        order by {order} limit :n offset :skip"""
    rows = s.execute(text(sql), {"n": n, "skip": skip, **(params or {})}).mappings().all()
    base = {"account_id", "customer_id", "first_name", "last_name", "date_of_birth", "ssn_last4"}
    out = [
        Fixture(
            r["account_id"],
            r["customer_id"],
            r["first_name"],
            r["last_name"],
            r["date_of_birth"],
            r["ssn_last4"],
            {k: v for k, v in r.items() if k not in base},
        )
        for r in rows
    ]
    if len(out) < n:
        raise LookupError(f"only {len(out)} of {n} fixtures for: {where}")
    return out


def person(s: Session, where: str, skip: int = 0, order: str = "a.id") -> Fixture:
    return people(s, where, 1, order=order, skip=skip)[0]


def vacant(s: Session, n: int = 1, skip: int = 0, where: str = "true") -> list[dict[str, Any]]:
    """Service addresses with no open account at all (every account there is closed, or none)."""
    sql = f"""
        select sa.id as address_id, sa.street, sa.city, sa.zip, sa.feeder from service_addresses sa
        where {UNIQUE_STREET} and {where} and not exists
            (select 1 from accounts a where a.service_address_id = sa.id and a.status <> 'closed')
        order by sa.id limit :n offset :skip"""
    rows = [dict(r) for r in s.execute(text(sql), {"n": n, "skip": skip}).mappings().all()]
    if len(rows) < n:
        raise LookupError("not enough vacant addresses")
    return rows


def address(a: dict[str, Any] | Fixture) -> str:
    d = a.extra if isinstance(a, Fixture) else a
    return f"{d['street']}, {d['city']}, MA {d['zip']}"


def facts_at(f: Fixture, use_ssn: bool = False) -> dict[str, str]:
    return {**facts(f, use_ssn), "your service address": address(f)}


def lookup(a: dict[str, Any] | Fixture) -> dict[str, Any]:
    d = a.extra if isinstance(a, Fixture) else a
    return {"tool": "lookup_outage", "args": {"street": d["street"], "zip_code": d["zip"]}}


def report(
    a: dict[str, Any] | Fixture, description: str, hazard: bool = False, account_id: str | None = None
) -> dict[str, Any]:
    d = a.extra if isinstance(a, Fixture) else a
    args: dict[str, Any] = {
        "street": d["street"],
        "zip_code": d["zip"],
        "description": description,
        "hazard": hazard,
    }
    if account_id:
        args["account_id"] = account_id
    return {"tool": "report_outage", "args": args}


def escalate(category: str, summary: str, account_id: str | None = None) -> dict[str, Any]:
    args: dict[str, Any] = {"category": category, "summary": summary}
    if account_id:
        args["account_id"] = account_id
    return {"tool": "escalate_to_human", "args": args}


def setup_report(
    f: Fixture | dict[str, Any], outage_id: str, linked: bool = True, n: int = 1
) -> dict[str, Any]:
    d = f.extra if isinstance(f, Fixture) else f
    return {
        "op": "insert",
        "table": "outage_reports",
        "values": {
            "id": f"OR-SETUP-{n}",
            "account_id": f.account_id if linked and isinstance(f, Fixture) else None,
            "service_address_id": d["address_id"],
            "outage_id": outage_id,
            "hazard": False,
            "description": "Power out at the house",
            "reported_at": "2026-10-15 14:30:00+00",
        },
    }


def feeder(outage: str) -> str:
    return f"sa.feeder = (select feeder from outages where id = '{outage}')"


NO_OUTAGE_FEEDER = "sa.feeder not in (select feeder from outages)"
