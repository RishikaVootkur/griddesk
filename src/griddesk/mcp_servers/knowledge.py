"""Knowledge MCP server: hybrid search over the policy handbook."""

from __future__ import annotations

from typing import Annotated

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from griddesk.mcp_servers.common import ConversationId
from griddesk.tools import knowledge
from griddesk.tools.common import ToolContext, run_tool


def build_knowledge_server(ctx: ToolContext) -> MCPServer:
    server = MCPServer("griddesk-knowledge", instructions="Search the customer-operations policy handbook.")

    @server.tool(
        description="Search the policy handbook (rules, rates, procedures). Returns the best passages."
    )
    def search_policy(
        conversation_id: ConversationId,
        query: Annotated[str, Field(max_length=300, description="What you need to know, in plain words.")],
        k: Annotated[int, Field(ge=1, le=5, description="How many passages.")] = 3,
    ) -> knowledge.SearchOut:
        return run_tool(knowledge.search_policy, knowledge.SearchOut, ctx, conversation_id, query, k)

    return server
