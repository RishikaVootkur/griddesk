"""Shared plumbing for the domain tools behind the MCP servers.

A tool function takes a `ToolContext` and typed arguments and returns a Pydantic output model
that extends `ToolOutput`. Policy refusals are not exceptions: they come back as `ok=False` with
a structured `ToolError` naming the rule, so the agent can explain the refusal and the eval can
count it.

`ToolContext.enforce_policy=False` is the ablation used to measure what in-tool enforcement buys.
The tool then performs the action anyway, but the audit log still records which rule it broke
(`outcome="policy_bypassed"`), so violations stay measurable in both conditions.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from griddesk.db import models as m
from griddesk.db.session import SessionFactory
from griddesk.logging import redact_value
from griddesk.policy.rules import Decision, Outcome, check_account_access
from griddesk.runtime import Clock, IdGenerator

ErrorCode = Literal["policy_denied", "needs_approval", "not_found", "invalid_request", "ambiguous", "budget"]


class ToolError(BaseModel):
    code: ErrorCode
    message: str
    rule_id: str | None = None


class ToolOutput(BaseModel):
    ok: bool = True
    error: ToolError | None = None
    approval_request_id: str | None = None  # set when the action is waiting for a human


@dataclass
class ToolContext:
    sessions: SessionFactory
    clock: Clock = field(default_factory=Clock)
    ids: IdGenerator = field(default_factory=IdGenerator)
    enforce_policy: bool = True

    def today(self) -> date:
        return self.clock.now().date()


class PolicyStop(Exception):
    """Raised inside a tool to return a refusal; converted to a `ToolOutput` by `run_tool`."""

    def __init__(self, error: ToolError, approval_request_id: str | None = None) -> None:
        super().__init__(error.message)
        self.error = error
        self.approval_request_id = approval_request_id


def money(x: Decimal) -> str:
    return f"{x:.2f}"


def audit(
    s: Session,
    ctx: ToolContext,
    conversation_id: str | None,
    action: str,
    outcome: str,
    account_id: str | None = None,
    rule_id: str | None = None,
    details: dict[str, Any] | None = None,
    actor: str = "agent",
) -> None:
    s.add(
        m.AuditLog(
            ts=ctx.clock.now(),
            conversation_id=conversation_id,
            actor=actor,
            action=action,
            account_id=account_id,
            outcome=outcome,
            rule_id=rule_id,
            details=redact_value(details or {}),
        )
    )


def verified_customer_id(s: Session, conversation_id: str) -> str | None:
    """The customer verified in this conversation, if any (P-01, P-02)."""
    row = s.scalars(
        select(m.IdentityVerification)
        .where(m.IdentityVerification.conversation_id == conversation_id, m.IdentityVerification.success)
        .order_by(m.IdentityVerification.id.desc())
        .limit(1)
    ).first()
    return row.customer_id if row else None


def enforce(
    s: Session,
    ctx: ToolContext,
    decision: Decision,
    *,
    conversation_id: str,
    action: str,
    account_id: str | None,
    arguments: dict[str, Any],
) -> None:
    """Apply a policy decision. Raises `PolicyStop` to refuse or to park the action for approval."""
    if decision.outcome is Outcome.ALLOW:
        return
    if not ctx.enforce_policy:
        audit(
            s,
            ctx,
            conversation_id,
            action,
            "policy_bypassed",
            account_id,
            decision.rule_id,
            {"would_be": decision.outcome.value, "arguments": arguments},
        )
        return
    if decision.outcome is Outcome.DENY:
        audit(
            s, ctx, conversation_id, action, "refused", account_id, decision.rule_id, {"arguments": arguments}
        )
        s.commit()
        raise PolicyStop(ToolError(code="policy_denied", message=decision.message, rule_id=decision.rule_id))
    # Needs approval: park it, unless the same request is already waiting (a retry must not queue a
    # duplicate for the supervisor).
    assert account_id is not None
    existing = pending_approval(s, conversation_id, account_id, action, arguments)
    if existing is not None:
        raise PolicyStop(
            ToolError(
                code="needs_approval",
                message=f"{decision.message} Already sent to a supervisor; still waiting for a decision.",
                rule_id=decision.rule_id,
            ),
            approval_request_id=existing.id,
        )
    req = m.ApprovalRequest(
        id=ctx.ids.new("APR"),
        conversation_id=conversation_id,
        account_id=account_id,
        action=action,
        arguments=_jsonable(arguments),
        rule_id=decision.rule_id or "",
        reason=decision.message,
        status="pending",
        requested_at=ctx.clock.now(),
    )
    s.add(req)
    audit(
        s,
        ctx,
        conversation_id,
        action,
        "pending_approval",
        account_id,
        decision.rule_id,
        {"approval": req.id},
    )
    s.commit()
    raise PolicyStop(
        ToolError(
            code="needs_approval",
            message=f"{decision.message} Sent to a supervisor for approval.",
            rule_id=decision.rule_id,
        ),
        approval_request_id=req.id,
    )


def pending_approval(
    s: Session, conversation_id: str, account_id: str, action: str, arguments: Mapping[str, Any]
) -> m.ApprovalRequest | None:
    """The pending request in this conversation for exactly this account, action and arguments, if any."""
    want = _jsonable(dict(arguments))
    rows = s.scalars(
        select(m.ApprovalRequest)
        .where(
            m.ApprovalRequest.conversation_id == conversation_id,
            m.ApprovalRequest.account_id == account_id,
            m.ApprovalRequest.action == action,
            m.ApprovalRequest.status == "pending",
        )
        .order_by(m.ApprovalRequest.requested_at, m.ApprovalRequest.id)
    )
    return next((r for r in rows if r.arguments == want), None)


def require_account(
    s: Session, ctx: ToolContext, conversation_id: str, account_id: str, action: str, for_update: bool = False
) -> m.Account:
    """Load an account and check the caller may act on it (P-01, P-02).

    A missing account gets the same refusal as someone else's account, so the tool never confirms
    whether an account number exists (P-05). Write tools pass `for_update=True` to lock the row,
    which serializes concurrent writes so read-then-insert limits (P-08, P-10, P-12, P-15, P-22) hold.
    """
    stmt = select(m.Account).where(m.Account.id == account_id.strip())
    if for_update:
        stmt = stmt.with_for_update()
    acct = s.scalars(stmt).first()
    verified = verified_customer_id(s, conversation_id)
    if acct is None:
        if not ctx.enforce_policy:
            raise PolicyStop(ToolError(code="not_found", message=f"No account {account_id}."))
        decision = check_account_access(verified, "<no such account>")
        raise PolicyStop(ToolError(code="policy_denied", rule_id=decision.rule_id, message=decision.message))
    decision = check_account_access(verified, acct.customer_id)
    enforce(
        s,
        ctx,
        decision,
        conversation_id=conversation_id,
        action=action,
        account_id=acct.id,
        arguments={"account_id": account_id},
    )
    return acct


def lock_approval(
    s: Session,
    approval_id: str,
    *,
    action: str,
    conversation_id: str,
    account_id: str,
    args: Mapping[str, object],
) -> m.ApprovalRequest | None:
    """Lock and return an approval that is approved for exactly this conversation, account, action and
    arguments; otherwise None. The caller marks it `executed` in the same transaction as the action,
    so the two commit together and the approval can never run twice.
    """
    req = s.scalars(
        select(m.ApprovalRequest).where(m.ApprovalRequest.id == approval_id).with_for_update()
    ).first()
    if req is None or req.status != "approved" or req.action != action:
        return None
    if req.conversation_id != conversation_id or req.account_id != account_id:
        return None
    if any(str(req.arguments.get(k)) != str(v) for k, v in args.items()):
        return None
    return req


def _jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [_jsonable(x) for x in v]
    return v


def run_tool[T: ToolOutput](fn: Any, out_type: type[T], *args: Any, **kwargs: Any) -> T:
    """Call a tool body, turning `PolicyStop` into a structured refusal of the tool's output type."""
    try:
        result: T = fn(*args, **kwargs)
        return result
    except PolicyStop as stop:
        return out_type(ok=False, error=stop.error, approval_request_id=stop.approval_request_id)
