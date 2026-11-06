"""Accounts MCP server: identity verification, escalation, and (M8) account changes."""

from __future__ import annotations

from typing import Annotated, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from griddesk.mcp_servers.common import INTERNAL, ConversationId
from griddesk.tools import (
    accounts,
    approvals,
    registry,  # noqa: F401 (registers approval executors)
    service,
)
from griddesk.tools.common import ToolContext, run_tool

AccountId = Annotated[str, Field(description="The customer's account number.")]
Street = Annotated[str, Field(description="Street address with house number.")]
Zip = Annotated[str | None, Field(description="5-digit ZIP code, if known.")]
Unit = Annotated[str | None, Field(description="Apartment or unit, e.g. 'Apt 3', if the building has units.")]
Day = Annotated[str, Field(description="Date as YYYY-MM-DD.")]
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

    @server.tool(description="Contact details on file, masked, plus paperless and autopay settings.")
    def get_contact_info(conversation_id: ConversationId, account_id: AccountId) -> service.ContactOut:
        return run_tool(service.get_contact_info, service.ContactOut, ctx, conversation_id, account_id)

    @server.tool(description="Change the email, phone or mailing address on file. Pass only what changes.")
    def update_contact_info(
        conversation_id: ConversationId,
        account_id: AccountId,
        email: Annotated[str | None, Field(description="New email.")] = None,
        phone: Annotated[str | None, Field(description="New 10-digit phone.")] = None,
        mailing_address: Annotated[str | None, Field(description="New one-line mailing address.")] = None,
    ) -> service.ContactOut:
        return run_tool(
            service.update_contact_info,
            service.ContactOut,
            ctx,
            conversation_id,
            account_id,
            email,
            phone,
            mailing_address,
        )

    @server.tool(description="Turn paperless billing on or off.")
    def set_paperless(
        conversation_id: ConversationId, account_id: AccountId, enabled: bool
    ) -> service.FlagOut:
        return run_tool(service.set_paperless, service.FlagOut, ctx, conversation_id, account_id, enabled)

    @server.tool(description="Turn autopay off (enrolling happens only in the customer portal).")
    def set_autopay(conversation_id: ConversationId, account_id: AccountId, enabled: bool) -> service.FlagOut:
        return run_tool(service.set_autopay, service.FlagOut, ctx, conversation_id, account_id, enabled)

    @server.tool(
        description="For a verified customer starting or moving service: find a service address id and "
        "whether it has active service (never who lives there)."
    )
    def lookup_address(
        conversation_id: ConversationId, street: Street, zip_code: Zip = None, unit: Unit = None
    ) -> service.AddressOut:
        return run_tool(
            service.lookup_address, service.AddressOut, ctx, conversation_id, street, zip_code, unit
        )

    @server.tool(description="Start service for the verified customer at a vacant address (1-60 days ahead).")
    def start_service(
        conversation_id: ConversationId,
        service_address_id: Annotated[str, Field(description="From lookup_address, e.g. SA-00612.")],
        start_date: Day,
    ) -> service.ServiceOrderOut:
        return run_tool(
            service.start_service,
            service.ServiceOrderOut,
            ctx,
            conversation_id,
            service_address_id,
            start_date,
        )

    @server.tool(description="Stop service on an account (1-60 days ahead). Needs supervisor approval.")
    def stop_service(
        conversation_id: ConversationId, account_id: AccountId, stop_date: Day
    ) -> service.ServiceOrderOut:
        return run_tool(
            service.stop_service, service.ServiceOrderOut, ctx, conversation_id, account_id, stop_date
        )

    @server.tool(
        description="Move service to a new address on one date: stops the old account and starts the new "
        "address. Needs supervisor approval."
    )
    def transfer_service(
        conversation_id: ConversationId,
        account_id: AccountId,
        new_service_address_id: Annotated[str, Field(description="From lookup_address.")],
        move_date: Day,
    ) -> service.ServiceOrderOut:
        return run_tool(
            service.transfer_service,
            service.ServiceOrderOut,
            ctx,
            conversation_id,
            account_id,
            new_service_address_id,
            move_date,
        )

    @server.tool(description="Runtime only: execute an action a human approved.", meta=INTERNAL)
    def execute_approved_action(
        approval_request_id: Annotated[str, Field(description="Approval request id.")],
    ) -> approvals.ExecutionOut:
        return approvals.execute(ctx, approval_request_id)

    return server
