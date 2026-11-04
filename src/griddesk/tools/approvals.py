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


def decide(
    ctx: ToolContext, approval_request_id: str, decision: Literal["approve", "deny"], operator: str
) -> ApprovalOut:
    with ctx.sessions() as s:
        r = s.get(m.ApprovalRequest, approval_request_id)
        if r is None:
            raise KeyError(approval_request_id)
        if r.status != "pending":
            raise ValueError(f"approval {r.id} is already {r.status}")
        r.status = "approved" if decision == "approve" else "denied"
        r.decided_at = ctx.clock.now()
        r.decided_by = operator
        audit(
            s,
            ctx,
            r.conversation_id,
            f"approval_{r.status}",
            "ok",
            r.account_id,
            r.rule_id,
            {"approval": r.id, "action": r.action},
            actor=f"operator:{operator}",
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
    result = fn(ctx, conv, approval_request_id=approval_request_id, **args)
    with ctx.sessions() as s:
        r = s.get(m.ApprovalRequest, approval_request_id)
        assert r is not None
        if result.ok:
            r.status = "executed"
        s.commit()
        return ExecutionOut(
            ok=result.ok,
            error=result.error,
            approval_request_id=r.id,
            status=r.status,
            result=result.model_dump(exclude_none=True),
        )
