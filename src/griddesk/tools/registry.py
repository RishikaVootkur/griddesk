"""Name -> (tool function, output model) for every model-facing tool.

The eval uses this to replay a task's expected actions directly against the tool layer, with the
same policy checks the MCP servers apply.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from griddesk.tools import accounts, approvals, billing
from griddesk.tools.common import ToolContext, ToolOutput, run_tool

TOOLS: dict[str, tuple[Callable[..., Any], type[ToolOutput]]] = {
    "get_account_summary": (billing.get_account_summary, billing.AccountSummary),
    "list_bills": (billing.list_bills, billing.BillList),
    "explain_bill": (billing.explain_bill, billing.BillExplanation),
    "apply_credit": (billing.apply_credit, billing.CreditOut),
    "create_payment_plan": (billing.create_payment_plan, billing.PlanResult),
    "dispute_bill": (billing.dispute_bill, billing.DisputeOut),
    "request_meter_check": (billing.request_meter_check, billing.OrderOut),
    "verify_identity": (accounts.verify_identity, accounts.VerificationOut),
    "escalate_to_human": (accounts.escalate_to_human, accounts.EscalationOut),
}


def _credit_executor(ctx: ToolContext, conversation_id: str, **kwargs: Any) -> ToolOutput:
    return run_tool(billing.apply_credit, billing.CreditOut, ctx, conversation_id, **kwargs)


# Actions that can wait for approval, and how to run them once approved.
approvals.register_executor("apply_credit", _credit_executor)
