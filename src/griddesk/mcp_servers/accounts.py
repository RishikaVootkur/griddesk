"""Accounts MCP server: identity verification, escalation, and (M8) account changes."""

from __future__ import annotations

from typing import Annotated, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from griddesk.mcp_servers.common import ConversationId
from griddesk.tools import accounts
from griddesk.tools.common import ToolContext, run_tool

Category = Literal["billing_dispute", "hazard", "identity_lockout", "medical", "complaint", "other"]


def build_accounts_server(ctx: ToolContext) -> MCPServer:
    server = MCPServer(
        "griddesk-accounts",
        instructions="Identity verification, escalation to a human, and account changes.",
    )

    @server.tool(
        description="Verify the caller owns an account: account number, last name, and either date of birth "
        "or the last 4 digits of their SSN. Three failures lock verification for this conversation."
    )
    def verify_identity(
        conversation_id: ConversationId,
        account_id: Annotated[str, Field(description="Account number the caller gave.")],
        last_name: Annotated[str, Field(description="Account holder's last name as the caller gave it.")],
        date_of_birth: Annotated[str | None, Field(description="YYYY-MM-DD, if the caller gave it.")] = None,
        ssn_last4: Annotated[
            str | None, Field(pattern=r"^\d{4}$", description="Last 4 SSN digits, if given.")
        ] = None,
    ) -> accounts.VerificationOut:
        return run_tool(
            accounts.verify_identity,
            accounts.VerificationOut,
            ctx,
            conversation_id,
            account_id,
            last_name,
            date_of_birth,
            ssn_last4,
        )

    @server.tool(description="Hand the case to a human specialist with a short summary.")
    def escalate_to_human(
        conversation_id: ConversationId,
        category: Category,
        summary: Annotated[str, Field(max_length=1000, description="What the human needs to know.")],
        account_id: Annotated[str | None, Field(description="Related account, if known.")] = None,
    ) -> accounts.EscalationOut:
        return run_tool(
            accounts.escalate_to_human,
            accounts.EscalationOut,
            ctx,
            conversation_id,
            category,
            summary,
            account_id,
        )

    return server
