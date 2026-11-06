"""Outage tools: status by address, outage reports, restoration estimates.

Outage status for an address is general information (P-06): no verification, and the result
never names who lives there. Reports link to an account only when the verified caller owns it.
"""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from griddesk.db import models as m
from griddesk.policy import rules
from griddesk.tools.common import (
    PolicyStop,
    ToolContext,
    ToolError,
    ToolOutput,
    audit,
    enforce,
    verified_customer_id,
)


class OutageOut(BaseModel):
    outage_id: str
    status: str
    cause: str
    started_at: str
    estimated_restoration: str | None = None
    restored_at: str | None = None
    customers_affected: int
    note: str = ""


class AddressOutageStatus(ToolOutput):
    address: str | None = None
    service_address_id: str | None = None
    active_outage: OutageOut | None = None
    recent_outages: list[OutageOut] = Field(default_factory=list)


class ReportOut(ToolOutput):
    report_id: str | None = None
    linked_outage: OutageOut | None = None
    message: str | None = None


class EstimateOut(ToolOutput):
    outage: OutageOut | None = None


def _fmt(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%d %H:%M UTC") if dt else None


def _outage_out(o: m.Outage) -> OutageOut:
    note = ""
    if o.status == "active":
        note = (
            "Estimated restoration is an estimate, not a promise (P-27)."
            if o.estimated_restoration
            else "No estimate yet: crews are assessing the damage (P-27)."
        )
    return OutageOut(
        outage_id=o.id,
        status=o.status,
        cause=o.cause,
        started_at=_fmt(o.started_at) or "",
        estimated_restoration=_fmt(o.estimated_restoration),
        restored_at=_fmt(o.restored_at),
        customers_affected=o.customers_affected,
        note=note,
    )


_UNIT = re.compile(
    r"^(?P<street>.*?)[\s,]+(?:apt|apartment|unit|suite|ste|#)\.?\s*#?\s*(?P<unit>[a-z0-9-]+)$", re.I
)


def _norm_unit(unit: str) -> str:
    """'Apt 3', 'apt. 3', 'Unit 3' and '#3' all normalize to '3'."""
    key = "".join(ch for ch in unit.lower() if ch.isalnum())
    for word in ("apartment", "apt", "unit", "suite", "ste"):
        if key.startswith(word) and key != word:
            return key[len(word) :]
    return key


def address_line(addr: m.ServiceAddress) -> str:
    return f"{addr.street}{', ' + addr.unit if addr.unit else ''}, {addr.city} {addr.zip}"


def find_address(s: Session, street: str, zip_code: str | None, unit: str | None = None) -> m.ServiceAddress:
    """Match a street address loosely (case, spacing, punctuation). Requires the house number.

    A unit can come as its own argument or at the end of the street ("412 Maple Ave Apt 3"). When
    several units share the street address and none was given, the lookup refuses as ambiguous and
    lists the units (never who lives in them), so a tool never silently acts on the wrong unit.
    """
    if not unit and (mk := _UNIT.match(street.strip())):
        street, unit = mk.group("street"), mk.group("unit")
    norm = func.regexp_replace(func.lower(m.ServiceAddress.street), "[^a-z0-9]", "", "g")
    key = "".join(ch for ch in street.lower() if ch.isalnum())
    stmt = select(m.ServiceAddress).where(norm == key)
    if zip_code:
        stmt = stmt.where(m.ServiceAddress.zip == zip_code.strip())
    rows = list(s.scalars(stmt.order_by(m.ServiceAddress.id)).all())
    if unit:
        want = _norm_unit(unit)
        rows = [r for r in rows if r.unit and _norm_unit(r.unit) == want]
    if not rows:
        raise PolicyStop(
            ToolError(code="not_found", message="No service address matches that street, unit and ZIP.")
        )
    if len(rows) > 1:
        units = sorted({r.unit or "(no unit)" for r in rows})
        zips = sorted({r.zip for r in rows})
        where = f" Units: {', '.join(units)}." if len(units) > 1 else ""
        if len(zips) > 1:
            where += f" ZIP codes: {', '.join(zips)}."
        raise PolicyStop(
            ToolError(
                code="ambiguous",
                message=f"More than one service address matches.{where} Ask which one and pass it.",
            )
        )
    return rows[0]


def _active_outage(s: Session, feeder: str) -> m.Outage | None:
    return s.scalars(
        select(m.Outage)
        .where(m.Outage.feeder == feeder, m.Outage.status == "active")
        .order_by(m.Outage.started_at)
    ).first()


def lookup_outage(
    ctx: ToolContext, conversation_id: str, street: str, zip_code: str | None = None, unit: str | None = None
) -> AddressOutageStatus:
    with ctx.sessions() as s:
        addr = find_address(s, street, zip_code, unit)
        active = _active_outage(s, addr.feeder)
        recent = s.scalars(
            select(m.Outage)
            .where(m.Outage.feeder == addr.feeder, m.Outage.status == "restored")
            .order_by(m.Outage.restored_at.desc())
            .limit(3)
        ).all()
        audit(s, ctx, conversation_id, "lookup_outage", "ok", None, details={"address": addr.id})
        s.commit()
        return AddressOutageStatus(
            address=address_line(addr),
            service_address_id=addr.id,
            active_outage=_outage_out(active) if active else None,
            recent_outages=[_outage_out(o) for o in recent],
        )


def report_outage(
    ctx: ToolContext,
    conversation_id: str,
    street: str,
    zip_code: str | None,
    description: str,
    hazard: bool = False,
    account_id: str | None = None,
    unit: str | None = None,
) -> ReportOut:
    from griddesk.logging import redact_text

    with ctx.sessions() as s:
        addr = find_address(s, street, zip_code, unit)
        linked = None
        if account_id:
            acct = s.get(m.Account, account_id.strip())
            if (
                acct
                and acct.customer_id == verified_customer_id(s, conversation_id)
                and acct.service_address_id == addr.id
            ):
                linked = acct.id
        active = _active_outage(s, addr.feeder)
        if active is not None:
            # P-26: one report per address (and per account at it) per outage, whether or not the
            # earlier report was linked to an account, so the answer never depends on who reported.
            same = m.OutageReport.service_address_id == addr.id
            if linked:
                same = or_(same, m.OutageReport.account_id == linked)
            dup = s.scalar(
                select(func.count())
                .select_from(m.OutageReport)
                .where(m.OutageReport.outage_id == active.id, same)
            )
            enforce(
                s,
                ctx,
                rules.check_outage_report(bool(dup) and not hazard),
                conversation_id=conversation_id,
                action="report_outage",
                account_id=linked,
                arguments={"address": addr.id},
            )
        report = m.OutageReport(
            id=ctx.ids.new("OR"),
            account_id=linked,
            service_address_id=addr.id,
            outage_id=active.id if active else None,
            hazard=hazard,
            description=redact_text(description)[:1000],
            reported_at=ctx.clock.now(),
        )
        s.add(report)
        audit(
            s,
            ctx,
            conversation_id,
            "report_outage",
            "ok",
            linked,
            details={"report": report.id, "hazard": hazard, "outage": report.outage_id},
        )
        s.commit()
        msg = (
            "Linked to the active outage in this area."
            if active
            else "No known outage here yet; a crew will investigate this report."
        )
        if hazard:
            msg += (
                " HAZARD: tell the customer to stay at least 30 feet away and call 911, then escalate (P-25)."
            )
        return ReportOut(
            report_id=report.id, linked_outage=_outage_out(active) if active else None, message=msg
        )


def get_restoration_estimate(ctx: ToolContext, conversation_id: str, outage_id: str) -> EstimateOut:
    with ctx.sessions() as s:
        o = s.get(m.Outage, outage_id.strip())
        if o is None:
            raise PolicyStop(ToolError(code="not_found", message=f"No outage {outage_id}."))
        audit(s, ctx, conversation_id, "get_restoration_estimate", "ok", None, details={"outage": o.id})
        s.commit()
        return EstimateOut(outage=_outage_out(o))
