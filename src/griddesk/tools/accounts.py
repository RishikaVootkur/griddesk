"""Account tools: identity verification and escalation (more account changes arrive in M8)."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field
from sqlalchemy import func, select

from griddesk.db import models as m
from griddesk.policy import rules
from griddesk.tools.common import PolicyStop, ToolContext, ToolError, ToolOutput, audit, verified_customer_id

ESCALATION_CATEGORIES = ("billing_dispute", "hazard", "identity_lockout", "medical", "complaint", "other")


class AccountRef(BaseModel):
    account_id: str
    status: str
    service_address: str


class VerificationOut(ToolOutput):
    verified: bool = False
    customer_first_name: str | None = None
    accounts: list[AccountRef] = Field(default_factory=list)
    attempts_remaining: int | None = None


class EscalationOut(ToolOutput):
    escalation_id: str | None = None
    category: str | None = None


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        raise PolicyStop(
            ToolError(code="invalid_request", message="Date of birth must be YYYY-MM-DD.")
        ) from None


def verify_identity(
    ctx: ToolContext,
    conversation_id: str,
    account_id: str,
    last_name: str,
    date_of_birth: str | None = None,
    ssn_last4: str | None = None,
) -> VerificationOut:
    """P-01: account number + last name + (date of birth or last 4 of SSN). P-03: 3 failures lock out."""
    dob = _parse_date(date_of_birth)
    with ctx.sessions() as s:
        failed = (
            s.scalar(
                select(func.count())
                .select_from(m.IdentityVerification)
                .where(
                    m.IdentityVerification.conversation_id == conversation_id, ~m.IdentityVerification.success
                )
            )
            or 0
        )
        lock = rules.check_verification_attempt(failed)
        if not lock.allowed and ctx.enforce_policy:
            audit(s, ctx, conversation_id, "verify_identity", "refused", None, lock.rule_id)
            s.commit()
            raise PolicyStop(ToolError(code="policy_denied", message=lock.message, rule_id=lock.rule_id))
        acct = s.get(m.Account, account_id.strip())
        cust = acct.customer if acct else None
        ok = cust is not None and rules.identity_matches(
            cust.last_name, cust.date_of_birth, cust.ssn_last4, last_name, dob, ssn_last4
        )
        s.add(
            m.IdentityVerification(
                conversation_id=conversation_id,
                customer_id=cust.id if ok and cust else None,
                account_id=account_id[:12],
                success=ok,
                method="dob" if dob else ("ssn_last4" if ssn_last4 else "none"),
                attempted_at=ctx.clock.now(),
            )
        )
        audit(
            s,
            ctx,
            conversation_id,
            "verify_identity",
            "ok" if ok else "failed",
            acct.id if ok and acct else None,
        )
        s.commit()
        if not ok or cust is None:
            # Never say which detail was wrong (P-03).
            return VerificationOut(
                ok=True, verified=False, attempts_remaining=max(0, rules.MAX_VERIFY_ATTEMPTS - failed - 1)
            )
        accounts = [
            AccountRef(
                account_id=a.id,
                status=a.status,
                service_address=f"{a.service_address.street}, {a.service_address.city}",
            )
            for a in sorted(cust.accounts, key=lambda a: a.id)
        ]
        return VerificationOut(verified=True, customer_first_name=cust.first_name, accounts=accounts)


def escalate_to_human(
    ctx: ToolContext, conversation_id: str, category: str, summary: str, account_id: str | None = None
) -> EscalationOut:
    if category not in ESCALATION_CATEGORIES:
        raise PolicyStop(ToolError(code="invalid_request", message=f"Unknown category {category}."))
    with ctx.sessions() as s:
        # Link the account only when it belongs to the verified caller; hazards may come from anyone.
        linked = None
        if account_id:
            acct = s.get(m.Account, account_id.strip())
            if acct and acct.customer_id == verified_customer_id(s, conversation_id):
                linked = acct.id
        esc = m.Escalation(
            id=ctx.ids.new("ESC"),
            conversation_id=conversation_id,
            account_id=linked,
            category=category,
            summary=summary[:1000],
            created_at=ctx.clock.now(),
            status="open",
        )
        s.add(esc)
        audit(s, ctx, conversation_id, "escalate_to_human", "ok", linked, details={"category": category})
        s.commit()
        return EscalationOut(escalation_id=esc.id, category=category)
