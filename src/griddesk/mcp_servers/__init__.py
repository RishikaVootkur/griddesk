"""MCP servers over the utility database: billing, outages, accounts, knowledge."""

from __future__ import annotations

from collections.abc import Callable

from mcp.server.mcpserver import MCPServer

from griddesk.tools.common import ToolContext

ServerFactory = Callable[[ToolContext], MCPServer]


def server_factories() -> dict[str, ServerFactory]:
    from griddesk.mcp_servers.billing import build_billing_server

    return {"billing": build_billing_server}
