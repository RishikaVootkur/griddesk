"""Human-in-the-loop approvals.

A tool that needs approval stores an `approval_requests` row and returns `needs_approval`. An
operator approves or denies it (`decide`). The runtime, not the model, then calls `execute`,
which re-runs the parked action with exactly the stored arguments. Each tool re-checks the
request itself (status approved, same action, same arguments) before acting, so a forged or
replayed approval id does nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel

from griddesk.db import models as m
from griddesk.tools.common import ToolContext, ToolError, ToolOutput, audit

Executor = Callable[..., ToolOutput]
_EXECUTORS: dict[str, Executor] = {}


def register_executor(action: str, fn: Executor) -> None:
    _EXECUTORS[action] = fn


class ApprovalOut(BaseModel):
    approval_request_id: str
    conversation_id: str
    account_id: str
    action: str
    arguments: dict[str, Any]
    rule_id: str
    reason: str
    status: str


class ExecutionOut(ToolOutput):
    approval_request_id: str | None = None
    status: str | None = None
    result: dict[str, Any] | None = None


def _out(r: m.ApprovalRequest) -> ApprovalOut:
    return ApprovalOut(
        approval_request_id=r.id,
        conversation_id=r.conversation_id,
        account_id=r.account_id,
        action=r.action,
        arguments=r.arguments,
        rule_id=r.rule_id,
        reason=r.reason,
        status=r.status,
    )


def get(ctx: ToolContext, approval_request_id: str) -> ApprovalOut | None:
    with ctx.sessions() as s:
        r = s.get(m.ApprovalRequest, approval_request_id)
        return _out(r) if r else None


IDENTITY_MAX = 31  # decided_by and audit actor ("operator:" + identity) are String(40)
DISPLAY_NAME_MAX = 40


def decide(
    ctx: ToolContext,
    approval_request_id: str,
    decision: Literal["approve", "deny"],
    operator: str,
    display_name: str | None = None,
) -> ApprovalOut:
    """Record an operator decision.

    `operator` is the trusted identity (from authentication) and is stored as `decided_by` and as the
    audit actor `operator:<identity>`. `display_name` is a free-text label from the console; it only
    goes into the audit details, truncated.
    """
    identity = operator.removeprefix("operator:")
    if not identity or len(identity) > IDENTITY_MAX:
        raise ValueError(f"operator identity must be 1 to {IDENTITY_MAX} characters")
    details: dict[str, Any] = {}
    if display_name and display_name.strip():
        details["display_name"] = display_name.strip()[:DISPLAY_NAME_MAX]
    with ctx.sessions() as s:
        r = s.get(m.ApprovalRequest, approval_request_id)
        if r is None:
            raise KeyError(approval_request_id)
        if r.status != "pending":
            raise ValueError(f"approval {r.id} is already {r.status}")
        r.status = "approved" if decision == "approve" else "denied"
        r.decided_at = ctx.clock.now()
        r.decided_by = identity
        audit(
            s,
            ctx,
            r.conversation_id,
            f"approval_{r.status}",
            "ok",
            r.account_id,
            r.rule_id,
            {"approval": r.id, "action": r.action, **details},
            actor=f"operator:{identity}",
        )
        s.commit()
        return _out(r)


def execute(ctx: ToolContext, approval_request_id: str) -> ExecutionOut:
    with ctx.sessions() as s:
        r = s.get(m.ApprovalRequest, approval_request_id)
        if r is None:
            return ExecutionOut(
                ok=False, error=ToolError(code="not_found", message="No such approval request.")
            )
        if r.status != "approved":
            return ExecutionOut(
                ok=False,
                approval_request_id=r.id,
                status=r.status,
                error=ToolError(code="policy_denied", message=f"Request is {r.status}, not approved."),
            )
        action, args, conv = r.action, dict(r.arguments), r.conversation_id
    fn = _EXECUTORS.get(action)
    if fn is None:
        return ExecutionOut(
            ok=False, error=ToolError(code="invalid_request", message=f"No executor for {action}.")
        )
    # The tool locks the request, re-checks it, and marks it executed together with the action.
    result = fn(ctx, conv, approval_request_id=approval_request_id, **args)
    with ctx.sessions() as s:
        r = s.get(m.ApprovalRequest, approval_request_id)
        assert r is not None
        ok = result.ok and r.status == "executed"
        return ExecutionOut(
            ok=ok,
            error=result.error,
            approval_request_id=r.id,
            status=r.status,
            result=result.model_dump(exclude_none=True),
        )
