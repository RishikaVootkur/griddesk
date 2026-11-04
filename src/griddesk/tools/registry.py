"""Name -> (tool function, output model) for every model-facing tool.

The eval uses this to replay a task's expected actions directly against the tool layer, with the
same policy checks the MCP servers apply.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from griddesk.tools import accounts, approvals, billing, knowledge, outages, service
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
    "get_contact_info": (service.get_contact_info, service.ContactOut),
    "update_contact_info": (service.update_contact_info, service.ContactOut),
    "set_paperless": (service.set_paperless, service.FlagOut),
    "set_autopay": (service.set_autopay, service.FlagOut),
    "lookup_address": (service.lookup_address, service.AddressOut),
    "start_service": (service.start_service, service.ServiceOrderOut),
    "stop_service": (service.stop_service, service.ServiceOrderOut),
    "transfer_service": (service.transfer_service, service.ServiceOrderOut),
    "lookup_outage": (outages.lookup_outage, outages.AddressOutageStatus),
    "report_outage": (outages.report_outage, outages.ReportOut),
    "get_restoration_estimate": (outages.get_restoration_estimate, outages.EstimateOut),
    "search_policy": (knowledge.search_policy, knowledge.SearchOut),
}


def _credit_executor(ctx: ToolContext, conversation_id: str, **kwargs: Any) -> ToolOutput:
    return run_tool(billing.apply_credit, billing.CreditOut, ctx, conversation_id, **kwargs)


def _executor(name: str) -> Callable[..., ToolOutput]:
    fn, out_type = TOOLS[name]

    def run(ctx: ToolContext, conversation_id: str, **kwargs: Any) -> ToolOutput:
        return run_tool(fn, out_type, ctx, conversation_id, **kwargs)

    return run


# Actions that can wait for approval, and how to run them once approved.
approvals.register_executor("apply_credit", _credit_executor)
approvals.register_executor("stop_service", _executor("stop_service"))
approvals.register_executor("transfer_service", _executor("transfer_service"))
