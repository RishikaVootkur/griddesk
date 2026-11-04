"""Account changes: contact details, paperless and autopay, and starting, stopping or moving service."""

from __future__ import annotations

import hashlib
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from griddesk.db import models as m
from griddesk.policy import rules
from griddesk.tools.billing import open_order_kinds
from griddesk.tools.common import (
    PolicyStop,
    ToolContext,
    ToolError,
    ToolOutput,
    audit,
    enforce,
    lock_approval,
    require_account,
    verified_customer_id,
)
from griddesk.tools.outages import find_address


class ContactOut(ToolOutput):
    email: str | None = None  # masked
    phone: str | None = None  # masked
    mailing_address: str | None = None
    preferred_contact: str | None = None
    paperless: bool | None = None
    autopay: bool | None = None


class AddressOut(ToolOutput):
    service_address_id: str | None = None
    address: str | None = None
    has_active_service: bool | None = None


class ServiceOrderOut(ToolOutput):
    order_id: str | None = None
    kind: str | None = None
    scheduled_for: str | None = None
    new_account_id: str | None = None
    account_status: str | None = None


class FlagOut(ToolOutput):
    account_id: str | None = None
    paperless: bool | None = None
    autopay: bool | None = None


def mask_email(e: str | None) -> str | None:
    if not e or "@" not in e:
        return None
    user, domain = e.split("@", 1)
    return f"{user[0]}***@{domain}"


def mask_phone(p: str | None) -> str | None:
    return f"***-{p[-4:]}" if p else None


def _parse_date(raw: str, field: str) -> date:
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        raise PolicyStop(ToolError(code="invalid_request", message=f"{field} must be YYYY-MM-DD.")) from None


def get_contact_info(ctx: ToolContext, conversation_id: str, account_id: str) -> ContactOut:
    with ctx.sessions() as s:
        acct = require_account(s, ctx, conversation_id, account_id, "get_contact_info")
        c = acct.customer
        audit(s, ctx, conversation_id, "get_contact_info", "ok", acct.id)
        s.commit()
        return ContactOut(
            email=mask_email(c.email),
            phone=mask_phone(c.phone),
            mailing_address=c.mailing_address,
            preferred_contact=c.preferred_contact,
            paperless=acct.paperless,
            autopay=acct.autopay,
        )


def update_contact_info(
    ctx: ToolContext,
    conversation_id: str,
    account_id: str,
    email: str | None = None,
    phone: str | None = None,
    mailing_address: str | None = None,
) -> ContactOut:
    with ctx.sessions() as s:
        acct = require_account(s, ctx, conversation_id, account_id, "update_contact_info", for_update=True)
        email = email.strip() if email else None
        decision = rules.check_contact_update(email, phone, mailing_address)
        enforce(
            s,
            ctx,
            decision,
            conversation_id=conversation_id,
            action="update_contact_info",
            account_id=acct.id,
            arguments={
                "fields": [
                    k
                    for k, v in {"email": email, "phone": phone, "mailing_address": mailing_address}.items()
                    if v
                ]
            },
        )
        c = acct.customer
        if email:
            c.email = email
        if phone:
            c.phone = rules.normalize_phone(phone) or phone
        if mailing_address:
            c.mailing_address = mailing_address.strip()
        audit(
            s,
            ctx,
            conversation_id,
            "update_contact_info",
            "ok",
            acct.id,
            details={
                "changed": [
                    k
                    for k, v in {"email": email, "phone": phone, "mailing_address": mailing_address}.items()
                    if v
                ]
            },
        )
        s.commit()
        return ContactOut(
            email=mask_email(c.email),
            phone=mask_phone(c.phone),
            mailing_address=c.mailing_address,
            preferred_contact=c.preferred_contact,
            paperless=acct.paperless,
            autopay=acct.autopay,
        )


def set_paperless(ctx: ToolContext, conversation_id: str, account_id: str, enabled: bool) -> FlagOut:
    with ctx.sessions() as s:
        acct = require_account(s, ctx, conversation_id, account_id, "set_paperless", for_update=True)
        acct.paperless = enabled
        audit(s, ctx, conversation_id, "set_paperless", "ok", acct.id, details={"enabled": enabled})
        s.commit()
        return FlagOut(account_id=acct.id, paperless=acct.paperless, autopay=acct.autopay)


def set_autopay(ctx: ToolContext, conversation_id: str, account_id: str, enabled: bool) -> FlagOut:
    with ctx.sessions() as s:
        acct = require_account(s, ctx, conversation_id, account_id, "set_autopay", for_update=True)
        enforce(
            s,
            ctx,
            rules.check_autopay_change(enabled),
            conversation_id=conversation_id,
            action="set_autopay",
            account_id=acct.id,
            arguments={"enabled": enabled},
        )
        acct.autopay = enabled
        audit(s, ctx, conversation_id, "set_autopay", "ok", acct.id, details={"enabled": enabled})
        s.commit()
        return FlagOut(account_id=acct.id, paperless=acct.paperless, autopay=acct.autopay)


def lookup_address(
    ctx: ToolContext, conversation_id: str, street: str, zip_code: str | None = None
) -> AddressOut:
    """Whether an address exists and has active service. Never says who lives there (P-05)."""
    with ctx.sessions() as s:
        addr = find_address(s, street, zip_code)
        audit(s, ctx, conversation_id, "lookup_address", "ok", None, details={"address": addr.id})
        s.commit()
        return AddressOut(
            service_address_id=addr.id,
            has_active_service=_address_active(s, addr.id),
            address=f"{addr.street}{', ' + addr.unit if addr.unit else ''}, {addr.city} {addr.zip}",
        )


def _address_active(s: Session, address_id: str) -> bool:
    return (
        s.scalars(
            select(m.Account.id).where(
                m.Account.service_address_id == address_id,
                m.Account.status.in_(("active", "pending_start", "disconnect_scheduled")),
            )
        ).first()
        is not None
    )


def _past_due_unplanned(s: Session, customer_id: str) -> bool:
    for a in s.scalars(select(m.Account).where(m.Account.customer_id == customer_id)):
        past_due = s.scalars(
            select(m.Bill.id).where(m.Bill.account_id == a.id, m.Bill.status == "past_due")
        ).first()
        planned = s.scalars(
            select(m.PaymentPlan.id).where(m.PaymentPlan.account_id == a.id, m.PaymentPlan.status == "active")
        ).first()
        if past_due and not planned:
            return True
    return False


def start_service(
    ctx: ToolContext, conversation_id: str, service_address_id: str, start_date: str
) -> ServiceOrderOut:
    when = _parse_date(start_date, "start_date")
    with ctx.sessions() as s:
        cust_id = verified_customer_id(s, conversation_id)
        if cust_id is None:
            enforce(
                s,
                ctx,
                rules.check_account_access(None, ""),
                conversation_id=conversation_id,
                action="start_service",
                account_id=None,
                arguments={"address": service_address_id},
            )
        addr = s.get(m.ServiceAddress, service_address_id.strip())
        if addr is None:
            raise PolicyStop(ToolError(code="not_found", message="No such service address."))
        facts = rules.StartFacts(
            address_has_active_account=_address_active(s, addr.id),
            requested_on=when,
            customer_past_due_unplanned=bool(cust_id) and _past_due_unplanned(s, cust_id or ""),
        )
        enforce(
            s,
            ctx,
            rules.check_start(facts, ctx.today()),
            conversation_id=conversation_id,
            action="start_service",
            account_id=None,
            arguments={"address": addr.id, "date": when.isoformat()},
        )
        new_acct = _open_account(s, ctx, cust_id or "", addr.id, when)
        order = _order(s, ctx, new_acct.id, "start", when)
        audit(s, ctx, conversation_id, "start_service", "ok", new_acct.id, details={"order": order.id})
        s.commit()
        return ServiceOrderOut(
            order_id=order.id,
            kind="start",
            scheduled_for=str(when),
            new_account_id=new_acct.id,
            account_status=new_acct.status,
        )


def new_account_id(customer_id: str, address_id: str) -> str:
    """Account numbers for new service are derived from customer and address, so an eval trial and its
    expected-state replay create the same id (D9)."""
    digest = hashlib.sha256(f"{customer_id}:{address_id}".encode()).hexdigest()
    return "9" + str(int(digest[:12], 16))[:5]


def _open_account(s: Session, ctx: ToolContext, customer_id: str, address_id: str, when: date) -> m.Account:
    acct = m.Account(
        id=new_account_id(customer_id, address_id),
        customer_id=customer_id,
        service_address_id=address_id,
        status="pending_start",
        rate_plan="residential_standard",
        opened_on=when,
    )
    s.add(acct)
    s.flush()
    return acct


def _order(
    s: Session, ctx: ToolContext, account_id: str, kind: str, when: date, new_address: str | None = None
) -> m.ServiceOrder:
    order = m.ServiceOrder(
        id=ctx.ids.new("SO"),
        account_id=account_id,
        kind=kind,
        requested_on=when,
        status="scheduled",
        new_service_address_id=new_address,
        created_at=ctx.clock.now(),
    )
    s.add(order)
    return order


def stop_service(
    ctx: ToolContext,
    conversation_id: str,
    account_id: str,
    stop_date: str,
    approval_request_id: str | None = None,
) -> ServiceOrderOut:
    when = _parse_date(stop_date, "stop_date")
    args = {"account_id": account_id, "stop_date": when.isoformat()}
    with ctx.sessions() as s:
        acct = require_account(s, ctx, conversation_id, account_id, "stop_service", for_update=True)
        approval = (
            lock_approval(
                s,
                approval_request_id,
                action="stop_service",
                conversation_id=conversation_id,
                account_id=acct.id,
                args=args,
            )
            if approval_request_id
            else None
        )
        facts = rules.StopFacts(
            account_status=acct.status,
            requested_on=when,
            open_order_kinds=open_order_kinds(s, acct.id),
            approved=approval is not None,
        )
        enforce(
            s,
            ctx,
            rules.check_stop(facts, ctx.today()),
            conversation_id=conversation_id,
            action="stop_service",
            account_id=acct.id,
            arguments=args,
        )
        order = _order(s, ctx, acct.id, "stop", when)
        acct.status = "pending_stop"
        if approval is not None:
            approval.status = "executed"
        audit(s, ctx, conversation_id, "stop_service", "ok", acct.id, details={"order": order.id})
        s.commit()
        return ServiceOrderOut(
            order_id=order.id, kind="stop", scheduled_for=str(when), account_status=acct.status
        )


def transfer_service(
    ctx: ToolContext,
    conversation_id: str,
    account_id: str,
    new_service_address_id: str,
    move_date: str,
    approval_request_id: str | None = None,
) -> ServiceOrderOut:
    when = _parse_date(move_date, "move_date")
    args = {
        "account_id": account_id,
        "new_service_address_id": new_service_address_id,
        "move_date": when.isoformat(),
    }
    with ctx.sessions() as s:
        acct = require_account(s, ctx, conversation_id, account_id, "transfer_service", for_update=True)
        addr = s.get(m.ServiceAddress, new_service_address_id.strip())
        if addr is None:
            raise PolicyStop(ToolError(code="not_found", message="No such service address."))
        approval = (
            lock_approval(
                s,
                approval_request_id,
                action="transfer_service",
                conversation_id=conversation_id,
                account_id=acct.id,
                args=args,
            )
            if approval_request_id
            else None
        )
        stop = rules.StopFacts(
            account_status=acct.status,
            requested_on=when,
            open_order_kinds=open_order_kinds(s, acct.id),
            approved=approval is not None,
        )
        start = rules.StartFacts(
            address_has_active_account=_address_active(s, addr.id),
            requested_on=when,
            customer_past_due_unplanned=_past_due_unplanned(s, acct.customer_id),
        )
        enforce(
            s,
            ctx,
            rules.check_transfer(stop, start, ctx.today()),
            conversation_id=conversation_id,
            action="transfer_service",
            account_id=acct.id,
            arguments=args,
        )
        new_acct = _open_account(s, ctx, acct.customer_id, addr.id, when)
        order = _order(s, ctx, acct.id, "transfer", when, new_address=addr.id)
        acct.status = "pending_stop"
        if approval is not None:
            approval.status = "executed"
        audit(
            s,
            ctx,
            conversation_id,
            "transfer_service",
            "ok",
            acct.id,
            details={"order": order.id, "new_account": new_acct.id},
        )
        s.commit()
        return ServiceOrderOut(
            order_id=order.id,
            kind="transfer",
            scheduled_for=str(when),
            new_account_id=new_acct.id,
            account_status=acct.status,
        )
