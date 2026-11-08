"""Billing MCP server (official MCP Python SDK, protocol 2026-07-28)."""

from __future__ import annotations

from typing import Annotated, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from griddesk.mcp_servers.common import INTERNAL, ConversationId, GridDeskServer
from griddesk.tools import approvals, billing, registry  # noqa: F401 (registry registers executors)
from griddesk.tools.common import ToolContext, run_tool

AccountId = Annotated[str, Field(description="The customer's account number, e.g. 100482.")]
BillId = Annotated[str, Field(description="Bill id, e.g. B-100482-2609.")]
ReasonCode = Literal["late_fee_waiver", "outage_compensation", "billing_error", "goodwill"]


def build_billing_server(ctx: ToolContext) -> MCPServer:
    server = GridDeskServer(
        "griddesk-billing",
        instructions="Bills, balances, charge explanations, credits, payment plans and disputes. "
        "Every account-specific tool requires identity verification in this conversation.",
    )

    @server.tool(description="Balance, status, past-due amount, plan and open orders for a verified account.")
    def get_account_summary(conversation_id: ConversationId, account_id: AccountId) -> billing.AccountSummary:
        return run_tool(billing.get_account_summary, billing.AccountSummary, ctx, conversation_id, account_id)

    @server.tool(description="Most recent bills with line items, newest first.")
    def list_bills(
        conversation_id: ConversationId,
        account_id: AccountId,
        limit: Annotated[int, Field(ge=1, le=12, description="How many bills (1-12).")] = 3,
    ) -> billing.BillList:
        return run_tool(billing.list_bills, billing.BillList, ctx, conversation_id, account_id, limit)

    @server.tool(
        description="Explain one bill: line items, the rates used, usage change vs the previous bill."
    )
    def explain_bill(conversation_id: ConversationId, bill_id: BillId) -> billing.BillExplanation:
        return run_tool(billing.explain_bill, billing.BillExplanation, ctx, conversation_id, bill_id)

    @server.tool(
        description="Apply a credit to an account. late_fee_waiver needs bill_id (the bill with the fee) and "
        "amount equal to the fee; outage_compensation needs outage_id and is $25. Credits over $50, "
        "billing_error and goodwill go to a human for approval."
    )
    def apply_credit(
        conversation_id: ConversationId,
        account_id: AccountId,
        amount: Annotated[str, Field(description="Dollar amount, e.g. '15.00'.")],
        reason_code: ReasonCode,
        bill_id: Annotated[str | None, Field(description="Bill the credit applies to.")] = None,
        outage_id: Annotated[str | None, Field(description="Outage being compensated.")] = None,
        note: Annotated[str, Field(max_length=200, description="Short internal note.")] = "",
    ) -> billing.CreditOut:
        return run_tool(
            billing.apply_credit,
            billing.CreditOut,
            ctx,
            conversation_id,
            account_id,
            amount,
            reason_code,
            bill_id,
            outage_id,
            note,
        )

    @server.tool(description="Set up a monthly payment plan for the account's full balance.")
    def create_payment_plan(
        conversation_id: ConversationId,
        account_id: AccountId,
        installments: Annotated[int, Field(description="Number of monthly installments (3-12).")],
    ) -> billing.PlanResult:
        return run_tool(
            billing.create_payment_plan, billing.PlanResult, ctx, conversation_id, account_id, installments
        )

    @server.tool(
        description="Open a dispute on a bill. Marks it disputed and creates a billing_dispute case."
    )
    def dispute_bill(
        conversation_id: ConversationId,
        bill_id: BillId,
        reason: Annotated[str, Field(max_length=500, description="What the customer disputes.")],
    ) -> billing.DisputeOut:
        return run_tool(billing.dispute_bill, billing.DisputeOut, ctx, conversation_id, bill_id, reason)

    @server.tool(description="Schedule a free meter check (for questioned estimated readings).")
    def request_meter_check(conversation_id: ConversationId, account_id: AccountId) -> billing.OrderOut:
        return run_tool(billing.request_meter_check, billing.OrderOut, ctx, conversation_id, account_id)

    @server.tool(description="Runtime only: execute an action a human approved.", meta=INTERNAL)
    def execute_approved_action(
        approval_request_id: Annotated[str, Field(description="Approval request id.")],
    ) -> approvals.ExecutionOut:
        return approvals.execute(ctx, approval_request_id)

    return server
