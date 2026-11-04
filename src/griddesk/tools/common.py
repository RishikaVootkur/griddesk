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

ErrorCode = Literal["policy_denied", "needs_approval", "not_found", "invalid_request", "budget"]


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
    # Needs approval: park it.
    assert account_id is not None
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


def require_account(
    s: Session, ctx: ToolContext, conversation_id: str, account_id: str, action: str
) -> m.Account:
    """Load an account and check the caller may act on it (P-01, P-02)."""
    acct = s.get(m.Account, account_id.strip())
    if acct is None:
        # Same wording whether or not the account exists, so the tool is not an account oracle.
        if verified_customer_id(s, conversation_id) is None and ctx.enforce_policy:
            raise PolicyStop(
                ToolError(
                    code="policy_denied",
                    rule_id="P-01",
                    message="Identity not verified in this conversation.",
                )
            )
        raise PolicyStop(ToolError(code="not_found", message=f"No account {account_id}."))
    decision = check_account_access(verified_customer_id(s, conversation_id), acct.customer_id)
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
