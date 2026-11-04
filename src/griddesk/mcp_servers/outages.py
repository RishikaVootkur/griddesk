"""Outages MCP server: outage status by address, reports and restoration estimates."""

from __future__ import annotations

from typing import Annotated

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from griddesk.mcp_servers.common import ConversationId
from griddesk.tools import outages
from griddesk.tools.common import ToolContext, run_tool

Street = Annotated[str, Field(description="Street address with house number, e.g. '412 Maple Ave'.")]
Zip = Annotated[str | None, Field(description="5-digit ZIP code, if known.")]


def build_outages_server(ctx: ToolContext) -> MCPServer:
    server = MCPServer(
        "griddesk-outages",
        instructions="Outage status for an address (no verification needed), outage reports and estimates.",
    )

    @server.tool(description="Is there an outage at this address? Returns the active outage and recent ones.")
    def lookup_outage(
        conversation_id: ConversationId, street: Street, zip_code: Zip = None
    ) -> outages.AddressOutageStatus:
        return run_tool(
            outages.lookup_outage, outages.AddressOutageStatus, ctx, conversation_id, street, zip_code
        )

    @server.tool(
        description="Report an outage or hazard at an address. Set hazard=true for downed lines, sparking, "
        "fire or smoke. Links to the active outage when there is one."
    )
    def report_outage(
        conversation_id: ConversationId,
        street: Street,
        description: Annotated[str, Field(max_length=1000, description="What the customer sees.")],
        zip_code: Zip = None,
        hazard: Annotated[
            bool, Field(description="Downed line, sparking, fire, smoke or burning smell.")
        ] = False,
        account_id: Annotated[str | None, Field(description="The verified caller's account, if any.")] = None,
    ) -> outages.ReportOut:
        return run_tool(
            outages.report_outage,
            outages.ReportOut,
            ctx,
            conversation_id,
            street,
            zip_code,
            description,
            hazard,
            account_id,
        )

    @server.tool(description="Current status and estimated restoration time for an outage id.")
    def get_restoration_estimate(
        conversation_id: ConversationId, outage_id: Annotated[str, Field(description="e.g. OUT-2610-01")]
    ) -> outages.EstimateOut:
        return run_tool(
            outages.get_restoration_estimate, outages.EstimateOut, ctx, conversation_id, outage_id
        )

    return server
