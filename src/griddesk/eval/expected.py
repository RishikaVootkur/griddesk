"""Compute a task's expected end state by replaying its ground-truth actions."""

from __future__ import annotations

from sqlalchemy import Engine

from griddesk.db.session import trial_scope
from griddesk.eval.state import Diff, apply_setup, diff, snapshot
from griddesk.eval.tasks import Task
from griddesk.runtime import Clock, IdGenerator
from griddesk.tools import approvals
from griddesk.tools.common import ToolContext, run_tool
from griddesk.tools.registry import TOOLS

EXPECTED_CONV = "expected"


class ExpectedActionError(Exception):
    """A task's own ground truth failed against the policy: the task is wrong, not the agent."""


def expected_diff(engine: Engine, task: Task) -> Diff:
    with trial_scope(engine) as sessions:
        apply_setup(sessions, task.setup)
        before = snapshot(sessions)
        ctx = ToolContext(
            sessions=sessions, clock=Clock(deterministic=True), ids=IdGenerator(f"expected:{task.id}")
        )
        for i, action in enumerate(task.expected_actions):
            if action.tool not in TOOLS:
                raise ExpectedActionError(f"{task.id}: unknown tool {action.tool}")
            fn, out_type = TOOLS[action.tool]
            out = run_tool(fn, out_type, ctx, EXPECTED_CONV, **action.args)
            if out.error and out.error.code == "needs_approval" and out.approval_request_id:
                approvals.decide(ctx, out.approval_request_id, task.operator_decision, operator="eval")
                if task.operator_decision == "approve":
                    res = approvals.execute(ctx, out.approval_request_id)
                    if not res.ok:
                        raise ExpectedActionError(f"{task.id}: approved action {i} failed: {res.error}")
                continue
            if not out.ok and action.tool != "verify_identity":
                raise ExpectedActionError(
                    f"{task.id}: expected action {i} ({action.tool}) refused: {out.error}"
                )
        return diff(before, snapshot(sessions))
